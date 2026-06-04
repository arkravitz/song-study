/**
 * The timeline: canvas for the dense layers, DOM overlay for the interactive
 * parts (section bands, event pins, selections, labels).
 *
 * Interactions, all of them deliberate:
 *   click / drag      seek + scrub
 *   shift+click       seek to the nearest grid line
 *   shift+drag        zoom to a range
 *   shift+alt+drag    select range A
 *   alt+drag          select range B (for A/B compare)
 *   wheel             zoom about the cursor
 *   wheel             zoom around pointer
 *   double-click      zoom to fit
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ArrangementEvent, EnergyChange, Section, SongSummary } from '../types'
import { api } from '../api'
import {
  EVENT_COLORS, eventKindLabel, fmtBar, fmtBarOnly, fmtTime,
  groundingColor, titleCase,
} from '../lib/format'
import { drawableEvents } from './eventRanges'
import { LABEL_PADDING_PX, LABEL_PX_PER_CHAR, macroSections } from '../lib/sections'
import { EditableSectionName } from './EditableSectionName'
import { boundaryTime, gridStepSeconds, MIN_SECTION_SECONDS } from '../lib/sectionEditing'
import {
  GUTTER, LAYOUT, computeStretch, lanesHeight, laneRects, rowRects,
  setCurveRowError, useTimelineCanvas, type TimelineGeom, type WidthBand,
} from './TimelineCanvas'
import { useCurve, useCurves, usePeaks, useStemWaves, type CurveSeries } from '../hooks/useData'

export interface TimelineProps {
  song: SongSummary
  /**
   * The playhead as a live ref, not a number.
   *
   * A number here is a snapshot of the audio clock taken when React last
   * rendered, and that render is gated at 20 Hz. The canvas drew that
   * snapshot, so the playhead trailed the sound by a median of 32 ms and up
   * to 59 ms — measured in the browser over 80 animation frames. Passing the
   * ref lets the canvas read the clock inside its own draw.
   */
  playheadRef: { current: number }
  playbackTime: number
  onMergeSections: (rightMacroId: string) => Promise<void>
  onSplitAtPlayhead: () => Promise<void>
  onMoveSectionBoundary: (id: string, at: number) => Promise<void>
  onRenameSection: (section: Section, name: string | null) => void
  onUndo: () => void; onRedo: () => void
  undoLabel?: string; redoLabel?: string
  sectionEditBusy: boolean
  sectionEditError: string | null
  onSeek: (t: number) => void
  onScrub?: (t: number) => void
  onSelectEvent: (e: ArrangementEvent | null) => void
  onSelectSection: (s: Section | null) => void
  selectedEventId: string | null
  selectedSectionId: string | null
  view: { t0: number; t1: number }
  setView: (v: { t0: number; t1: number }) => void
  zoomAt: (factor: number, anchorT?: number) => void
  panTo: (t: number) => void
  showAll: () => void
  showRange: (a: number, b: number) => void
  rangeA: { start: number; end: number } | null
  rangeB: { start: number; end: number } | null
  onRangeChange: (a: { start: number; end: number } | null,
                   b: { start: number; end: number } | null) => void
  hiddenSources: Set<string>
  collapsedFamilies: Set<string>
  focusSource: string | null
  onFocusSource: (id: string | null) => void
  /** Mixer, so the lane labels can offer solo/mute where a producer looks. */
  mix?: {
    solo: Set<string>
    loading: Set<string>
  }
  /** Adds or removes a stem from the solo set. */
  onSolo?: (id: string) => void
  /** Selecting an energy-change marker opens its explanation. */
  onEnergyChange?: (c: EnergyChange) => void
  options: {
    showWaveform: boolean
    showEnergyRaw: boolean
    showWidth: boolean
    gridSnap: boolean
    showEvents: boolean
  }
  onOptions: (o: Partial<TimelineProps['options']>) => void
}

interface TooltipState {
  x: number
  y: number
  kind: 'event' | 'section' | 'lane' | 'time' | 'energy'
  event?: ArrangementEvent
  section?: Section
  lane?: { id: string; label: string; color: string }
  t?: number
}

/** Stable identity: a fresh [] on every render would retrigger the draw. */
const EMPTY_CHANGES: EnergyChange[] = []


export function Timeline(props: TimelineProps) {
  const {
    song, playheadRef, onSeek, onSelectEvent, onSelectSection, selectedEventId,
    selectedSectionId, view, setView, zoomAt, showAll, showRange, rangeA, rangeB,
    onRangeChange, hiddenSources, collapsedFamilies,
    focusSource, onFocusSource, mix, onSolo, onEnergyChange,
    options, onOptions,
  } = props

  const [boundaryPreview, setBoundaryPreview] = useState<{id: string; at: number} | null>(null)
  const boundaryDrag = useRef<{id: string; original: number; x: number; min: number; max: number; at: number} | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const playheadOverlayRef = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(1200)
  const [availH, setAvailH] = useState(360)
  const [tooltip, setTooltip] = useState<TooltipState | null>(null)
  // The hover guide is a function of where the pointer *is*, so the pixel is
  // what gets stored and the time is derived on every render.
  //
  // It used to store the time. That is correct until the view moves without the
  // pointer: navigating the timeline then drew the guide at
  // `tToX(the time from before the pan)` — a different pixel, marking a
  // different instant than the one the pointer was last actually over. The line
  // appeared to be stuck on a position that no longer meant anything, which is
  // what made it look like a stale selection rather than a cursor.
  const [hoverX, setHoverX] = useState<number | null>(null)
  const [zoomPreview, setZoomPreview] = useState<{ start: number; end: number } | null>(null)
  const dragRef = useRef<{ mode: 'none' | 'scrub' | 'pan' | 'zoom' | 'a' | 'b';
                           startX: number; startT: number; startView: { t0: number; t1: number } }>(
    { mode: 'none', startX: 0, startT: 0, startView: { t0: 0, t1: 1 } })

  // `plotW` and `stretch` are part of the geom, not derived inside the draw
  // loop, so every frame computes them from the same numbers. Leaving them off
  // meant the very first frame divided by `undefined` and drew NaN coordinates,
  // which canvas silently discards — a blank timeline until something forced a
  // redraw.
  // The geometry is a *value* computed once per render, not a mutable ref that
  // two consumers read at different moments.
  //
  // It was a ref, assigned during render, and read by the canvas draw loop and by
  // the DOM overlay. The overlay's memo did not depend on the fields the
  // container resize changes — `availH`, `stretch`, `totalH` — so a window resize
  // moved the lanes and left the labels where they were. The screenshot showed
  // the six lane names stacked in the corner over the energy curve, 400px above
  // the lanes they name. Two consumers reading a mutable ref cannot be made to
  // agree, only lucky; one value computed once and passed to both makes
  // disagreement impossible.
  const EMPTY_SET: Set<string> = useMemo(() => new Set<string>(), [])
  const geom: TimelineGeom = {
    width: 1200, height: 400, plotW: 1200 - GUTTER, stretch: 0,
    t0: 0, t1: 30, playheadRef, hover: null,
    selectionA: null, selectionB: null,
    hiddenSources: EMPTY_SET, collapsedFamilies: EMPTY_SET,
    // Matches the App defaults so the first frame before any option is
    // applied is the same as the settled one — a default that only exists on
    // the hook side means the first paint is a different layout than the
    // second, which is its own small class of bug.
    showEnergyRaw: false, showEnergyComponents: false,
    showWidth: false, showWaveform: true, showEvents: false, gridSnap: true,
    focusSource: null,
  }
  const geomRef = useRef<TimelineGeom>(geom)

  // observe the container's size: the timeline stretches to fill it
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setWidth(Math.max(320, el.clientWidth))
      setAvailH(Math.max(160, el.clientHeight))
    })
    ro.observe(el)
    setWidth(Math.max(320, el.clientWidth))
    setAvailH(Math.max(160, el.clientHeight))
    return () => ro.disconnect()
  }, [])

  const span = view.t1 - view.t0
  const { series: energy, error: energyError } = useCurve(
    song.id, 'energy/perceived', view.t0, view.t1, 1400)
  const { series: energyRaw } = useCurve(
    song.id, options.showEnergyRaw ? 'energy/raw' : null, view.t0, view.t1, 900)
  // Three bands, one request. The row used to fetch the whole-spectrum average,
  // which is the number the analysis itself finds least informative: it reported
  // "DNA." as a mean width of 0.05 and separately observed that the high band is
  // much wider than the low one, and the row showed the average of the two facts
  // it was making a point of.
  const widthBands: WidthBand[] = song.spatial_summary?.bands ?? []
  const widthCurveNames = useMemo(
    () => (options.showWidth ? widthBands.map((b) => b.curve) : []),
    [options.showWidth, widthBands],
  )
  const { series: widthSeries, errors: widthBandErrors } = useCurves(
    song.id, widthCurveNames, view.t0, view.t1, 900, options.showWidth)
  // One reason for the row, taken from the bands: if none of them loaded, the row
  // is empty and the gutter must say why. A band that is individually missing
  // says so in its own lane instead.
  const widthError = useMemo(() => {
    if (!options.showWidth) return null
    // No bands at all is its own failure, not an empty row. The server builds
    // the list from the same constant the measurement used, so it is always
    // present for a song this server has served — but "always" is a claim about
    // a payload crossing a network, and a row that is blank with nothing saying
    // why is the failure this project has already paid for twice (B-005, B-069).
    if (!widthBands.length) return 'no band information for this track'
    if (Object.keys(widthSeries).length) return null
    const first = widthBandErrors[widthBands[0].curve]
    return first ?? 'no width curve for this track'
  }, [options.showWidth, widthBands, widthSeries, widthBandErrors])
  const peaks = usePeaks(song.id, view.t0, view.t1, Math.min(4000, width), options.showWaveform)
  // One envelope per separated stem, for the lanes to draw under their
  // highlights. Gated on the same switch as the mix waveform row: it is the same
  // kind of thing — audio in the timeline — and a second switch with nearly the
  // same meaning is one more decision for a reader who only wants a clean
  // diagram. Sources that report no audible content still ask for their file, so
  // a bleed-only lane shows an empty waveform and reads as empty rather than as
  // unchecked.
  const stemWaves = useStemWaves(
    song.id, song.sources.map((s) => s.id),
    view.t0, view.t1, Math.min(2000, width), options.showWaveform,
  )

  const naturalH = lanesHeight(song, {
    hiddenSources, collapsedFamilies, showWidth: options.showWidth,
    showWaveform: options.showWaveform, showEvents: options.showEvents,
    width, stretch: 0,
  } as Partial<TimelineGeom>)
  const visibleLaneCount = song.sources.filter(
    (s) => !hiddenSources.has(s.id) && !collapsedFamilies.has(s.family)).length
  const stretch = computeStretch(availH, naturalH, visibleLaneCount)
  const totalH = Math.max(naturalH, availH)

  // The one authoritative geometry for this render. The ref exists only so the
  // canvas's rAF loop can read the latest value without re-subscribing; it is
  // written from the same value the overlay reads, so the two cannot diverge.
  const liveGeom: TimelineGeom = {
    ...geom,
    width, plotW: Math.max(40, width - GUTTER), stretch,
    height: totalH, t0: view.t0, t1: view.t1, playheadRef, gridSnap: options.gridSnap,
    hover: geomRef.current.hover,
    selectionA: rangeA, selectionB: rangeB, hiddenSources, collapsedFamilies,
    showEnergyRaw: options.showEnergyRaw, showWidth: options.showWidth,
    showWaveform: options.showWaveform, showEvents: options.showEvents,
    focusSource,
  }
  geomRef.current = liveGeom

  // Hand the reasons to the canvas before it draws, so the gutter can print them.
  setCurveRowError('energy', energyError)
  setCurveRowError('width', widthError)

  useTimelineCanvas(canvasRef, song, geomRef, peaks, energy as CurveSeries | null,
    energyRaw as CurveSeries | null, widthBands, widthSeries, widthBandErrors,
    song?.energy_changes ?? EMPTY_CHANGES,
    onEnergyChange ?? null, stemWaves, playheadOverlayRef)

  // ------------------------------------------------------------ geometry
  const plotW = Math.max(40, width - GUTTER)
  const tToX = useCallback((t: number) => GUTTER + ((t - view.t0) / Math.max(1e-9, span)) * plotW,
    [view.t0, span, plotW])
  const xToT = useCallback((x: number) => view.t0 + ((x - GUTTER) / Math.max(1, plotW)) * span,
    [view.t0, span, plotW])

  // Derived from the pointer's pixel, so it tracks the view: a pan that moves
  // the content under a still pointer moves the guide with it. See `hoverX`.
  const hoverT = hoverX === null ? null : xToT(hoverX)
  const hoverValid = hoverT !== null && hoverT >= 0 && hoverT <= song.duration

  // ------------------------------------------------------------- pointer
  //
  // A press that lands in the label gutter, or on a control that handles its own
  // clicks, must not scrub.
  //
  // The scrub handler is on `.timeline-scroll`, which spans the whole timeline
  // including the 116px gutter where the solo and mute buttons live. It mapped
  // `clientX` straight to a time, so a press at x = 40 became a time before the
  // start of the view and the `Math.max(0, …)` clamp turned it into `onSeek(0)`.
  // Clicking the left-hand side of the screen restarted the song from the top —
  // on the most-clicked part of the interface.
  //
  // The solo button made it worse by stopping propagation on `click`, which fires
  // *after* `pointerdown` has already reached the scrub handler. `stopPropagation`
  // in the wrong handler is the same as none at all. The section bands and event
  // pins already stopped it on `pointerdown`; this makes the lane labels
  // consistent with them instead of inventing a second convention.
  const isOwnControl = (target: EventTarget | null): boolean => {
    const el = target as HTMLElement | null
    if (!el || !el.closest) return false
    return !!el.closest('button, .tl-label-row, .lane-label, a, input, select, [role="button"]')
  }

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const modifiedTimelineItem = e.shiftKey && !!(e.target as HTMLElement).closest?.('.section-band, .event-span')
    if (x < GUTTER || (isOwnControl(e.target) && !modifiedTimelineItem)) return
    const t = xToT(x)
    ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
    if (e.shiftKey && e.altKey) {
      dragRef.current = { mode: 'a', startX: x, startT: t, startView: view }
      onRangeChange({ start: t, end: t }, rangeB)
    } else if (e.shiftKey) {
      dragRef.current = { mode: 'zoom', startX: x, startT: t, startView: view }
      setZoomPreview({ start: t, end: t })
    } else if (e.altKey) {
      dragRef.current = { mode: 'b', startX: x, startT: t, startView: view }
      onRangeChange(rangeA, { start: t, end: t })
    } else if (e.button === 1 || (e.button === 0 && e.metaKey)) {
      dragRef.current = { mode: 'pan', startX: x, startT: t, startView: view }
    } else {
      dragRef.current = { mode: 'scrub', startX: x, startT: t, startView: view }
      onSeek(Math.max(0, Math.min(song.duration, t)))
      onSelectEvent(null)
      onSelectSection(null)
    }
  }, [xToT, view, onRangeChange, onSeek, song.duration, onSelectEvent, onSelectSection, rangeA, rangeB])

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (dragRef.current.mode === 'none' && (e.target as HTMLElement).closest('input, .section-rename, .section-name-save, .section-name-reset')) {
      setHoverX(null); return
    }
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const t = xToT(x)
    setHoverX(x)
    const d = dragRef.current
    if (d.mode === 'none') {
      geomRef.current.hover = { t, x, y: e.clientY - rect.top }
      return
    }
    if (d.mode === 'scrub') {
      onSeek(Math.max(0, Math.min(song.duration, t)))
    } else if (d.mode === 'pan') {
      const dt = ((d.startX - x) / Math.max(1, width)) * (d.startView.t1 - d.startView.t0)
      setView({ t0: d.startView.t0 + dt, t1: d.startView.t1 + dt })
    } else if (d.mode === 'a') {
      const r = { start: Math.min(d.startT, t), end: Math.max(d.startT, t) }
      onRangeChange(r, rangeB)
    } else if (d.mode === 'b') {
      const r = { start: Math.min(d.startT, t), end: Math.max(d.startT, t) }
      onRangeChange(rangeA, r)
    } else if (d.mode === 'zoom') {
      setZoomPreview({ start: Math.min(d.startT, t), end: Math.max(d.startT, t) })
    }
  }, [xToT, onSeek, song.duration, width, setView, onRangeChange, rangeA, rangeB])

  const endDrag = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current
    const rect = e.currentTarget.getBoundingClientRect()
    const t = Math.max(0, Math.min(song.duration, xToT(e.clientX - rect.left)))
    const r = { start: Math.max(0, Math.min(d.startT, t)), end: Math.min(song.duration, Math.max(d.startT, t)) }
    if (d.mode === 'zoom') {
      setZoomPreview(null)
      if (Math.abs(e.clientX - rect.left - d.startX) < 3) {
        onSeek(boundaryTime(t, song.grid, plotW / span, 0, song.duration, true, false))
        onSelectSection(null)
        onSelectEvent(null)
      } else if (r.end - r.start >= 0.25) {
        showRange(r.start, r.end)
        onSelectSection(null)
        onSelectEvent(null)
      }
    } else if (d.mode === 'a') {
      onRangeChange(r.end - r.start >= 0.25 ? r : null, rangeB)
    } else if (d.mode === 'b') {
      onRangeChange(rangeA, r.end - r.start >= 0.25 ? r : null)
    }
    dragRef.current = { mode: 'none', startX: 0, startT: 0, startView: view }
    ;(e.target as HTMLElement).releasePointerCapture?.(e.pointerId)
  }, [rangeA, rangeB, onRangeChange, showRange, view, song.duration, song.grid, plotW, span, xToT, onSeek, onSelectSection, onSelectEvent])

  const onWheel = useCallback((e: React.WheelEvent) => {
    if (boundaryDrag.current) { e.preventDefault(); return }
    e.preventDefault()
    // A wheel means navigating, not pointing, so the hover guide goes away.
    //
    // It used to survive the gesture, which is where it became noise: you rest
    // the pointer somewhere, scroll, and the content moves under a stationary
    // cursor. The line and its readout then describe a moment you did not choose
    // and cannot act on — and since the pointer has not moved, nothing will bring
    // it back or take it away except moving the mouse, which defeats the point of
    // using the wheel.
    //
    // Reported twice as "the highlight stays after shift zooming… it feels
    // useless". The first response was to make it *more* prominent, which was the
    // wrong answer to the right observation: the earlier fix was correct but it
    // was fixing a different bug (the guide marking the wrong instant after a
    // pan) and this is the one that was actually reported.
    //
    // It is still there when you are pointing — which is when reading the time and
    // bar under the cursor is worth having — and the next real pointer move brings
    // it straight back.
    setHoverX(null)
    setTooltip(null)
    // Shift-wheel is reported as horizontal delta by some browsers and trackpads.
    // A zero deltaY used to turn every such gesture into "zoom in" regardless
    // of its direction.
    const delta = Math.abs(e.deltaY) >= Math.abs(e.deltaX) ? e.deltaY : e.deltaX
    if (delta === 0) return
    const rect = e.currentTarget.getBoundingClientRect()
    const t = xToT(e.clientX - rect.left)
    zoomAt(delta > 0 ? 1.18 : 1 / 1.18, t)
    if (e.shiftKey) onSelectSection(null)
  }, [zoomAt, xToT, setTooltip, onSelectSection])

  // --------------------------------------------------------- hit testing
  const hitTest = useCallback((x: number, y: number) => {
    const t = xToT(x)
    // lanes
    if (song) {
      const rects = laneRects(song, liveGeom)
      for (const lane of rects) {
        if (y >= lane.y && y < lane.y + lane.h) {
          return { kind: 'lane' as const, lane, t }
        }
      }
      // events track
      const evR = rowRects(song, liveGeom).find((r) => r.key === 'events')
      const evTop = evR ? evR.y : LAYOUT.ruler + 40
      if (y >= evTop && y < evTop + (evR?.h ?? LAYOUT.events) && options.showEvents) {
        const tol = (14 / width) * span
        let best: ArrangementEvent | null = null
        let bd = tol
        for (const e of song.events) {
          if (e.is_continuous) continue
          const d = Math.abs(e.t - t)
          if (d < bd) { best = e; bd = d }
        }
        if (best) return { kind: 'event' as const, event: best, t }
      }
    }
    return { kind: 'time' as const, t }
  }, [xToT, width, span, song, options.showEvents, options.showWaveform])

  // ------------------------------------------------------- DOM overlay
  //
  // Every detected event gets a drawn range, including the continuous ones with
  // no measured end, which used to be discarded outright. A section of the song
  // changing and staying changed is a finding, not an absence of one.
  const drawnEvents = useMemo(
    () => (song ? drawableEvents(song.events, song.sections, song.duration) : []),
    [song],
  )
  const inView = useMemo(
    () => drawnEvents.filter(
      (d) => d.end !== null
        ? (d.end >= view.t0 - 1 && d.start <= view.t1 + 1)
        : (d.start >= view.t0 - 1 && d.start <= view.t1 + 1),
    ),
    [drawnEvents, view.t0, view.t1],
  )

  /**
   * Only the significant events get a pin, and the row says how many it kept.
   *
   * A picket fence is not a visualisation. 160 events across 1100 pixels is a pin
   * every seven, which carries no information and looks like a deliberate
   * encoding of something. Zoomed to a phrase, the same events are readable and
   * all of them are shown.
   *
   * Ranked by how strong the change is, which is the same ranking the inspector
   * uses for "biggest changes", so the two agree about what big means. The
   * budget scales with the row so a long track is not penalised, and it never
   * drops below a handful — a quiet track should still show its few events
   * rather than an empty row with a disclaimer.
   */
   const PIN_BUDGET_PER_1000PX = 44
  const { visibleEvents, hiddenEvents } = useMemo(() => {
    const budget = Math.max(
      8,
      Math.min(220, Math.round((width / 1000) * PIN_BUDGET_PER_1000PX)),
    )
    if (inView.length <= budget) return { visibleEvents: inView, hiddenEvents: 0 }
    const strength = (d: (typeof inView)[number]) =>
      d.event.strength ?? d.event.confidence ?? 0
    const ranked = [...inView].sort((a, b) => strength(b) - strength(a))
    const keep = ranked.slice(0, budget)
    const keepIds = new Set(keep.map((d) => d.event.id))
    // Back to time order, so the row still reads left to right.
    return {
      visibleEvents: inView.filter((d) => keepIds.has(d.event.id)),
      hiddenEvents: inView.length - keep.length,
    }
  }, [inView, width])

  // Derive the event row from the shared layout table so the pins, the canvas
  // row and the hit-testing can never drift apart.
  const evRow = rowRects(song, liveGeom).find((r) => r.key === 'events')
  const evY = evRow ? evRow.y + evRow.h / 2 : LAYOUT.ruler + 60

  // The form. Shared with the inspector so the ribbon and the section list can
  // never disagree about what the sections of this record are.
  const macroRibbon = useMemo(() => (song ? macroSections(song) : []), [song])

  const displayRibbon = macroRibbon.map((m, i) => ({ ...m,
    start: boundaryPreview?.id === m.id ? boundaryPreview.at : m.start,
    end: boundaryPreview && boundaryPreview.id === macroRibbon[i + 1]?.id ? boundaryPreview.at : m.end,
  }))
  const canSplit = macroRibbon.some(m => props.playbackTime >= m.start + MIN_SECTION_SECONDS
    && props.playbackTime <= m.end - MIN_SECTION_SECONDS)
  const saveBoundary = async (id: string, at: number) => {
    try { await props.onMoveSectionBoundary(id, at) }
    catch { /* The toolbar shows the save error. */ }
    finally { setBoundaryPreview(null) }
  }

  // `stretch`, `totalH` and `availH` are in the dependency list because they
  // are what a container resize changes. They were missing, and that omission is
  // the whole bug: the labels kept the geometry of the window they were drawn in
  // while the canvas took the new one.
  const laneList = useMemo(() => {
    if (!song) return []
    return laneRects(song, liveGeom)
  }, [song, liveGeom.height, liveGeom.stretch, liveGeom.collapsedFamilies,
      liveGeom.hiddenSources, liveGeom.showWidth,
      liveGeom.showWaveform, width])

  return (
    <div className="timeline-wrap">
      <TimelineToolbar {...props} canSplit={canSplit} />
      {props.sectionEditError && <div className="section-edit-feedback" role="alert">{props.sectionEditError}</div>}
      <div
        className="timeline-scroll"
        ref={scrollRef}
        style={{ overflowX: 'hidden', overflowY: 'auto' }}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={() => { dragRef.current.mode = 'none'; setZoomPreview(null) }}
        onPointerLeave={() => { setHoverX(null); setTooltip(null) }}
        onDoubleClick={() => showAll()}
      >
        <div className="timeline-inner" style={{ height: totalH, width: '100%' }}>
          <canvas ref={canvasRef} className="tl-canvas" style={{ height: totalH }} />
          <div ref={playheadOverlayRef} className="timeline-playhead" aria-hidden="true" />

          {/*
            Macro sections, not detected segments. A chorus that thickens for
            its last four bars is one chorus; drawing the boundary as a second
            top-level section made the form unreadable, and the macro structure
            that fixes it was sitting in the data unused. The ribbon now shows
            what a producer would say out loud, and the internal boundary is
            still marked underneath as a thin tick so the change is not hidden.
          */}
          {displayRibbon.map((m, index) => {
            const left = Math.max(GUTTER, tToX(m.start))
            const right = Math.min(width, tToX(m.end))
            if (right <= left) return null
            const nameWidth = m.label.length * LABEL_PX_PER_CHAR + LABEL_PADDING_PX + 28
            const compact = right - left < nameWidth
            const revealWidth = Math.min(width - GUTTER, Math.max(190, nameWidth))
            return <div key={m.id}
              className={`section-band ${m.role} ${compact ? 'compact' : ''} ${selectedSectionId === m.parts[0].id ? 'sel' : ''}`}
              style={{left, top: LAYOUT.ruler, width: right - left,
                '--section-reveal-width': `${revealWidth}px`,
                '--section-reveal-shift': `${Math.min(0, width - left - revealWidth)}px`,
              } as React.CSSProperties}
              title={`${m.label} · ${fmtTime(m.start)}–${fmtTime(m.end)}`}
              onPointerDown={e => { if (!e.shiftKey) e.stopPropagation() }}
              onClick={e => { if (!e.shiftKey) { e.stopPropagation(); onSelectSection(m.parts[0]) } }}
              onDoubleClick={e => e.stopPropagation()}>
              {compact && <span className="compact-section-index" aria-hidden="true">{index + 1}</span>}
              <EditableSectionName section={m}
                onSelect={() => onSelectSection(m.parts[0])}
                onRename={name => props.onRenameSection(m.parts[0], name)} />
            </div>
          })}
          {displayRibbon.map((m, i) => {
            if (!i || m.start < view.t0 || m.start > view.t1) return null
            const min = macroRibbon[i - 1].start + MIN_SECTION_SECONDS
            const max = macroRibbon[i].end - MIN_SECTION_SECONDS
            return <button key={`boundary-${m.id}`} className="section-boundary"
              style={{left: tToX(m.start), top: LAYOUT.ruler}}
              role="slider" aria-label={`Boundary between ${macroRibbon[i - 1].label} and ${m.label}`}
              aria-valuemin={min} aria-valuemax={max} aria-valuenow={m.start}
              aria-valuetext={fmtTime(m.start)} disabled={props.sectionEditBusy}
              title={`Drag to resize · Shift-drag to move freely · Double-click to merge, keeping “${macroRibbon[i - 1].label}” · Delete also merges`}
              onClick={e => e.stopPropagation()} onDoubleClick={e => {
                e.preventDefault(); e.stopPropagation()
                if (!props.sectionEditBusy) void props.onMergeSections(m.id).catch(() => {})
              }}
              onPointerDown={e => {
                e.preventDefault(); e.stopPropagation(); if (props.sectionEditBusy) return
                e.currentTarget.setPointerCapture(e.pointerId)
                boundaryDrag.current = {id: m.id, original: m.start, x: e.clientX, min, max, at: m.start}
                setTooltip(null); setHoverX(null)
              }}
              onPointerMove={e => {
                e.stopPropagation(); const d = boundaryDrag.current
                if (!d || d.id !== m.id || Math.abs(e.clientX - d.x) < 2) return
                d.at = boundaryTime(d.original + (e.clientX - d.x) * span / plotW,
                  song.grid, plotW / span, d.min, d.max, options.gridSnap, e.shiftKey)
                setBoundaryPreview({id: d.id, at: d.at})
              }}
              onPointerUp={e => {
                e.stopPropagation(); const d = boundaryDrag.current; boundaryDrag.current = null
                e.currentTarget.releasePointerCapture(e.pointerId)
                if (d && Math.abs(d.at - d.original) > .000001) void saveBoundary(d.id, d.at)
                else setBoundaryPreview(null)
              }}
              onPointerCancel={e => { e.stopPropagation(); boundaryDrag.current = null; setBoundaryPreview(null) }}
              onKeyDown={e => {
                if (e.key === 'Delete' || e.key === 'Backspace') {
                  e.preventDefault(); e.stopPropagation()
                  if (!e.repeat && !props.sectionEditBusy) void props.onMergeSections(m.id).catch(() => {})
                  return
                }
                if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
                e.preventDefault(); e.stopPropagation()
                const step = e.shiftKey || !options.gridSnap ? .05 : gridStepSeconds(song.grid, plotW / span)
                const at = boundaryTime(m.start + (e.key === 'ArrowLeft' ? -step : step),
                  song.grid, plotW / span, min, max, options.gridSnap, e.shiftKey)
                setBoundaryPreview({id: m.id, at}); void saveBoundary(m.id, at)
              }} />
          })}
          {boundaryPreview && <div className="boundary-guide" style={{left: tToX(boundaryPreview.at)}}>
            <span>{fmtTime(boundaryPreview.at)}</span>
          </div>}

          {/* event pins */}
          {options.showEvents && visibleEvents.map((d) => {
            const e = d.event
            if (e.is_continuous) {
              // A sustained change, not a point, so it is drawn as a bar — and
              // the bar has to behave like the pins do. It used to be
              // `pointer-events: none` with no tooltip and no handler, which made
              // every continuous event in the analysis unreachable: the one on
              // the test track was an unidentifiable amber line across the row.
              const open = d.openEnded
              const x0 = tToX(d.start)
              const x1 = Math.max(x0 + (open ? 18 : 4), tToX(d.end ?? d.start))
              const left = Math.max(GUTTER, x0)
              const right = Math.min(width, x1)
              if (right <= left) return null
              return (
                <div
                  key={e.id}
                  className={`event-span ${open ? 'open' : ''} ${selectedEventId === e.id ? 'sel' : ''}`}
                  role="button"
                  tabIndex={0}
                  aria-label={
                    `${e.label}, ${eventKindLabel(e.kind)}, from ${fmtTime(e.t, false)}`
                    + (open ? ', still going at this point'
                            : ` to ${fmtTime(e.end ?? e.t, false)}`)
                  }
                  style={{
                    left,
                    width: right - left,
                    top: evY - 3,
                    background: EVENT_COLORS[e.kind] ?? '#888',
                    opacity: 0.45 + 0.55 * e.confidence,
                  }}
                  onPointerDown={(ev) => { if (ev.shiftKey) return; ev.stopPropagation(); onSelectEvent(e); onSeek(Math.max(0, e.t)) }}
                  onKeyDown={(ev) => {
                    if (ev.key === 'Enter' || ev.key === ' ') {
                      ev.preventDefault()
                      onSelectEvent(e)
                      onSeek(Math.max(0, e.t))
                    }
                  }}
                  onMouseEnter={(ev) => setTooltip({ x: ev.clientX, y: ev.clientY, kind: 'event', event: e })}
                  onMouseLeave={() => setTooltip(null)}
                  /* Why the bar is short, stated where it can be found. The end
                     of an open-ended change is wherever the next same-kind event
                     landed, not a measured boundary, so painting the full run
                     would present a display limit as a span. */
                  title={open
                    ? `${e.label} — from ${fmtTime(e.t, false)}, and the new state `
                      + `held for the next ${fmtTime((d.end ?? d.start) - d.start, false)}. The bar `
                      + `is drawn short because the end is where the next change of `
                      + `this kind landed, not a measured boundary.`
                    : undefined}
                >
                  {/* Caps, so a span reads as a span rather than a rule. An
                      open-ended one keeps only the start cap and gets a fade at
                      the right, because its end is a display limit and not a
                      measurement — showing a firm cap there would be a claim the
                      analysis never made. */}
                  {x0 >= GUTTER && <i className="cap" style={{ left: 0 }} />}
                  {!open && x1 <= width && <i className="cap" style={{ right: 0 }} />}
                  {open && <i className="open-fade" />}
                  <span className="cap-label">
                    {e.label}
                  </span>
                </div>
              )
            }
            const x = tToX(e.t)
            if (x < GUTTER || x > width) return null
            const col = EVENT_COLORS[e.kind] ?? '#888'
            return (
              <div
                key={e.id}
                className={`event-pin ${selectedEventId === e.id ? 'sel' : ''}`}
                style={{ left: x, top: evY, background: col, opacity: 0.35 + 0.65 * e.confidence }}
                onPointerDown={(ev) => { if (ev.shiftKey) return; ev.stopPropagation(); onSelectEvent(e); onSeek(Math.max(0, e.t)) }}
                onMouseEnter={(ev) => setTooltip({ x: ev.clientX, y: ev.clientY, kind: 'event', event: e })}
                onMouseLeave={() => setTooltip(null)}
              />
            )
          })}

          {options.showEvents && hiddenEvents > 0 && (
            <div
              className="ev-more"
              style={{ left: GUTTER + 6, top: evY - 1 }}
              title={`${hiddenEvents} smaller arrangement changes are not drawn at `
                + `this zoom. Zoom in to see them, or open the events tab for all `
                + `${drawnEvents.length}.`}
            >
              +{hiddenEvents} smaller changes — zoom in
            </div>
          )}

          {/*
            The A and B selections.

            These drew as full-height tinted bands with no handles, which is the
            exact appearance of a loop or time selection in a DAW — the one thing
            in this interface that suggests you can drag its edges, drag it
            around, and click it away. None of that worked, and nothing said so,
            so a selection made once sat there looking like a region that had
            stopped responding.

            The band itself stays `pointer-events: none`, because it spans every
            row: making it clickable would mean a lane or an event pin inside a
            selection could no longer be clicked, which is a worse bug than the
            one being fixed. So the way out is a small × sitting in the ruler
            strip at the top of the band — the one part of the band that overlays
            nothing you would want to click.

            The footer's A and B readouts clear their own range, which is the
            discoverable route and the one that does not need aiming.
          */}
          {rangeA && (
            <>
              <div className="range-sel" style={{
                left: tToX(rangeA.start),
                width: Math.max(1, tToX(rangeA.end) - tToX(rangeA.start)),
              }} />
              <button className="range-x" title="Clear selection A"
                onClick={() => onRangeChange(null, rangeB)}
                style={{ left: tToX((rangeA.start + rangeA.end) / 2) }}>×</button>
            </>
          )}
          {rangeB && (
            <>
              <div className="range-sel b" style={{
                left: tToX(rangeB.start),
                width: Math.max(1, tToX(rangeB.end) - tToX(rangeB.start)),
              }} />
              <button className="range-x b" title="Clear selection B"
                onClick={() => onRangeChange(rangeA, null)}
                style={{ left: tToX((rangeB.start + rangeB.end) / 2) }}>×</button>
            </>
          )}
          {zoomPreview && zoomPreview.end > zoomPreview.start && (
            <div className="range-sel zoom" style={{
              left: Math.max(GUTTER, tToX(zoomPreview.start)),
              width: Math.max(1, Math.min(width, tToX(zoomPreview.end)) - Math.max(GUTTER, tToX(zoomPreview.start))),
            }} />
          )}

          {/*
            Hover guide: where the pointer is, and what time that is.

            It was a bare 1px line with nothing attached. A vertical rule whose
            only job is to say "here", saying nothing about *when* here is, reads
            as a selection — which is the one thing it is not, and clicking it
            does nothing. So it now carries the time and the bar, the two things
            you actually want from a cursor, and the position is derived from the
            pointer's pixel so a pan cannot leave it pointing at a moment the
            pointer is no longer over.
          */}
          {hoverValid && hoverT !== null && (
            <>
              <div style={{
                position: 'absolute', left: tToX(hoverT), top: LAYOUT.ruler, bottom: 0,
                width: 1, background: 'rgba(255,255,255,0.22)', pointerEvents: 'none',
              }} />
              <div style={{
                // Below the ruler, not inside it. In the ruler it covered the bar
                // numbers — the one row whose whole job is to say where you are —
                // and left a gap in the tick sequence. Here it floats over the
                // section ribbon, and only while the pointer is over a section
                // name, which is exactly when the name is not what you need.
                position: 'absolute', left: tToX(hoverT), top: LAYOUT.ruler + 2,
                pointerEvents: 'none',
                transform: tToX(hoverT) > width - 110 ? 'translateX(-100%)' : 'translateX(2px)',
                background: 'rgba(12,16,22,0.94)', border: '1px solid rgba(255,255,255,0.14)',
                borderRadius: 3, padding: '0 4px', font: '9px ui-monospace, monospace',
                color: '#dfe5ee', whiteSpace: 'nowrap', lineHeight: '13px',
              }}>
                {fmtTime(hoverT)}
                <span style={{ color: '#8b96a8' }}>
                  {'  '}bar {fmtBarOnly(hoverT, song.grid.bpm, song.grid.meter, song.grid.offset)}
                </span>
              </div>
            </>
          )}

          {/* lane labels */}
          {laneList.map((lane) => {
            const src = song.sources.find((s) => s.id === lane.id)
            return (
              <div
                key={lane.id}
                className={`tl-label-row ${focusSource && focusSource !== lane.id ? 'dim' : ''}`}
                style={{ top: lane.y - 1, width: GUTTER - 4 }}
                onClick={() => onFocusSource(focusSource === lane.id ? null : lane.id)}
                /* Double-click to solo, which is the convention in every DAW and
                   was simply absent: the row's only handler was a single click
                   that toggles focus, so a double click toggled it twice and
                   landed back where it started. The row reads as a track header,
                   so it should behave like one. Single click still isolates. */
                onDoubleClick={(e) => {
                  e.stopPropagation()
                  if (onSolo) onSolo(lane.id)
                }}
                title={`${lane.label}${src ? ` — ${src.n_entries} ${src.n_entries === 1 ? 'passage' : 'passages'}, heard through about ${Math.round(src.duty_cycle * 100)}% of the song` : ''}`}
              >
                <span className="swatch" style={{ background: lane.color }} />
                <button className="name lane-name" onClick={(e) => { e.stopPropagation(); onFocusSource(focusSource === lane.id ? null : lane.id) }}>{lane.id === 'other' ? 'Other sounds' : lane.label}</button>
                {/*
                  Solo and mute live on the lane itself, not only in the stems
                  panel. The lane is where you are already looking when you
                  decide a part is doing something, and the control has to be
                  the height of the thing it affects.
                */}
                {onSolo && (
                  <button
                    className={`lane-btn s ${mix?.solo.has(lane.id) ? 'on' : ''}`}
                    aria-pressed={mix?.solo.has(lane.id) ?? false}
                    onPointerDown={(e) => { e.stopPropagation() }}
                    onClick={(e) => { e.stopPropagation(); onSolo(lane.id) }}
                    /*
                      The tooltip states the new state, not the current one, and
                      says that a click adds rather than switches — which is the thing
                      that was never true before. Soloing two parts together is one
                      click each, and the header names what is currently soloed.
                    */
                    title={mix?.loading.has(lane.id) ? `loading ${lane.label}…`
                      : mix?.solo.has(lane.id)
                        ? `stop soloing ${lane.label} · ${mix.solo.size} in the set`
                        : mix?.solo.size
                          ? `also solo ${lane.label} — play both together`
                          : `solo ${lane.label} — hear it alone, in sync`}
                  >
                    {mix?.loading.has(lane.id) ? '·' : 'S'}
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {tooltip && <TimelineTooltip tip={tooltip} song={song} />}
    </div>
  )
}

// ---------------------------------------------------------------------------


function TimelineTooltip({ tip, song }: { tip: TooltipState; song: SongSummary }) {
  const style: React.CSSProperties = {
    left: Math.min(window.innerWidth - 350, tip.x + 12),
    top: Math.min(window.innerHeight - 200, tip.y + 12),
  }
  if (tip.kind === 'event' && tip.event) {
    const e = tip.event
    return (
      <div className="tl-tooltip" style={style}>
        <div className="t-title">
          <span style={{ color: EVENT_COLORS[e.kind] }}>●</span> {e.label}
        </div>
        <div className="tiny dim" style={{ marginTop: 1 }}>
          {e.is_continuous ? 'continuous · ' : ''}{eventKindLabel(e.kind)}
        </div>
        <div className="mono tiny dim">
          {fmtTime(e.t)}{e.end ? ` – ${fmtTime(e.end)}` : ''} · {fmtBar(e.t, song.grid.bpm, song.grid.meter, song.grid.offset)}
        </div>
        <div className="small dim" style={{ marginTop: 3 }}>
          {e.confidence < 0.6 ? 'Possible arrangement change' : 'Detected arrangement change'}
        </div>
        {e.evidence[0] && (
          <div className="t-ev">
            <div className="mono tiny" style={{ color: 'var(--accent)' }}>
              {e.evidence[0].method}{e.evidence[0].metric ? ` / ${e.evidence[0].metric}` : ''}
            </div>
            <div className="tiny dim">{e.evidence[0].note}</div>
          </div>
        )}
        <div className="tiny faint" style={{ marginTop: 4 }}>click to select &amp; inspect</div>
      </div>
    )
  }
  if (tip.kind === 'section' && tip.section) {
    const s = tip.section
    return (
      <div className="tl-tooltip" style={style}>
        <div className="t-title">{macroSections(song).find(m => m.parts.some(p => p.id === s.id))?.label ?? titleCase(s.label)}</div>
        <div className="mono tiny dim">
          {fmtTime(s.start)} – {fmtTime(s.end)} ({(s.end - s.start).toFixed(1)}s)
        </div>
        <div className="small dim" style={{ marginTop: 3 }}>
          {s.source_ids.map(id => song.sources.find(src => src.id === id)?.label).filter(Boolean).join(', ')}
        </div>
        {s.deltas_from_previous.length > 0 && (
          <div className="t-ev tiny dim">{s.deltas_from_previous.join('; ')}</div>
        )}
      </div>
    )
  }
  if (tip.kind === 'lane' && tip.lane) {
    const src = song.sources.find((s) => s.id === tip.lane!.id)
    if (!src) return null
    return (
      <div className="tl-tooltip" style={style}>
        <div className="t-title">{src.label}</div>
        <div className="small dim">
          {src.segments.length} spans · plays {src.total_active_seconds.toFixed(1)}s
          {' '}({Math.round(src.duty_cycle * 100)}% of the song)
        </div>
        <div className="small dim">
          mean {src.mean_level_db.toFixed(1)} dB · pan {src.mean_pan >= 0 ? 'R' : 'L'}
          {Math.abs(src.mean_pan * 100).toFixed(0)}% · width {src.mean_width.toFixed(2)}
        </div>
        <div className="tiny faint">from {src.separator_model} · confidence {src.confidence.toFixed(2)}</div>
      </div>
    )
  }
  return (
    <div className="tl-tooltip" style={style}>
      <div className="t-title mono">{fmtTime(tip.t ?? 0)}</div>
      <div className="tiny dim">
        {fmtBar(tip.t ?? 0, song.grid.bpm, song.grid.meter, song.grid.offset)}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------


function TimelineToolbar(props: TimelineProps & {canSplit: boolean}) {
  const { song, options, onOptions, view, zoomAt, showAll } = props
  const [showLegend, setShowLegend] = useState(false)
  const [showDisplay, setShowDisplay] = useState(false)

  // Only the kinds actually present, in descending order of how often they
  // occur, so the legend stays short and the common ones lead.
  const legendKinds = useMemo(() => {
    const m = new Map<string, number>()
    for (const e of song.events) m.set(e.kind, (m.get(e.kind) ?? 0) + 1)
    return [...m.entries()]
      .map(([kind, count]) => ({ kind, count }))
      .sort((a, b) => b.count - a.count)
  }, [song.events])

  return (
    <div className="transport" style={{ flexWrap: 'wrap', gap: 8 }}>
      {/*
        The strip of truncated section chips used to live here. It duplicated the
        section band directly below it, cost 95px — a fifth of the vertical space
        the music had — and rendered the form as "intr vers chor", which is the
        one row a producer most needs to read and the one row that was
        unreadable. The section ribbon now carries the labels at full size, and a
        section is selected by clicking the band.
      */}
      <strong className="timeline-title">The arrangement</strong>
      <button onClick={() => void props.onSplitAtPlayhead()} disabled={!props.canSplit || props.sectionEditBusy}
        title="Split the section at the playhead (⌘E / Ctrl+E)">Split at playhead</button>
      <button className={options.gridSnap ? 'on' : 'ghost'} aria-pressed={options.gridSnap}
        onClick={() => onOptions({gridSnap: !options.gridSnap})}
        title="Snap section boundaries to the musical grid · Shift-drag boundaries to move freely · Shift-click the song to snap the playhead">Snap to grid</button>
      <button className="ghost" onClick={props.onUndo} disabled={!props.undoLabel || props.sectionEditBusy}
        title={props.undoLabel ? `Undo ${props.undoLabel.toLowerCase()} (⌘Z / Ctrl+Z)` : 'Undo (⌘Z / Ctrl+Z)'}>Undo</button>
      <button className="ghost" onClick={props.onRedo} disabled={!props.redoLabel || props.sectionEditBusy}
        title={props.redoLabel ? `Redo ${props.redoLabel.toLowerCase()} (⇧⌘Z / Ctrl+Shift+Z)` : 'Redo (⇧⌘Z / Ctrl+Shift+Z)'}>Redo</button>
      <div className="spacer grow" />
      <button className="tiny ghost" onClick={() => zoomAt(1 / 1.5)} title="Zoom in (+)">＋</button>
      <button className="tiny ghost" onClick={() => zoomAt(1.5)} title="Zoom out (−)">－</button>
      <button className="tiny ghost" onClick={showAll} title="Fit whole song (0)">Whole song</button>
      {/*
        The visible window, stated. Without it there is no way to tell that
        "fit" left you looking at eight seconds of a three-minute song except by
        squinting at the ruler — and the bar numbers alone do not make the span
        obvious. Showing it also makes the wheel's effect legible.
      */}
      <span
        className="view-range mono"
        data-testid="view-range"
        title={`Viewing ${fmtTime(view.t0, false)}\u2013${fmtTime(view.t1, false)} of ${fmtTime(song.duration, false)}`}
      >
        {fmtTime(view.t0, false)}–{fmtTime(view.t1, false)}
        {/* The share of the song in view was removed from here. `0:00-0:02 of
            3:06` already answers "how much am I looking at", and a bare "1 %"
            next to it read as a proportion of something unspecified — and as an
            alarming number when you are looking at the whole record. */}
        <span className="faint">{' '}of {fmtTime(song.duration, false)}</span>
      </span>
      {/*
        Words, not three-letter abbreviations. "dens" and "ev" and "snap" mean
        nothing to anyone who has not read the source, and this row is the only
        place the optional layers are reachable.
      */}
      <button className="ghost" aria-expanded={showDisplay} onClick={() => setShowDisplay(!showDisplay)}>Display</button>
      {showDisplay && <div className="seg-ctl" role="group" aria-label="Timeline layers">
        <button className={options.showWaveform ? 'on' : ''}
          onClick={() => onOptions({ showWaveform: !options.showWaveform })}
          title="Waveform">waveform</button>
        <button className={options.showEnergyRaw ? 'on' : ''}
          onClick={() => onOptions({ showEnergyRaw: !options.showEnergyRaw })}
          title="Measured level alongside the perceived energy curve">measured</button>
        <button className={options.showWidth ? 'on' : ''}
          onClick={() => onOptions({ showWidth: !options.showWidth })}
          title="Stereo width over time">stereo width</button>
        <button className={options.showEvents ? 'on' : ''}
          onClick={() => onOptions({ showEvents: !options.showEvents })}
          title="Arrangement changes">changes</button>
      </div>}
      {/*
        Event kinds are told apart by colour and nothing else, so the mapping has
        to be discoverable — but it was a permanent 33px row of seven chips above
        the music, answering a question asked once. It is now behind a control
        that says how many kinds are present, so the information is one click away
        and the song gets the space.
      */}
      {showDisplay && options.showEvents && legendKinds.length > 0 && (
        <>
          <button
            className="tiny ghost nowrap"
            onClick={() => setShowLegend((v) => !v)}
            aria-expanded={showLegend}
            title="What each change colour means"
          >
            {showLegend ? 'hide key' : `${legendKinds.length} kinds of change`}
          </button>
          {showLegend && (
            <div className="ev-legend" role="list" aria-label="Event kinds">
              {legendKinds.map((k) => (
                <span
                  key={k.kind}
                  className="ev-legend-item"
                  role="listitem"
                  title={`${k.count} ${eventKindLabel(k.kind)}${k.count === 1 ? '' : 's'}`}
                >
                  <i style={{ background: EVENT_COLORS[k.kind] ?? '#888' }} />
                  {eventKindLabel(k.kind)}
                  <span className="faint mono">{k.count}</span>
                </span>
              ))}
            </div>
          )}
        </>
      )}

    </div>
  )
}

export { api, groundingColor }
