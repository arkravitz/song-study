/**
 * The timeline canvas.
 *
 * Everything high-density is drawn here — waveform, energy, per-source
 * activity lanes, stereo width, the bar ruler. Section bands and event pins are
 * DOM (see Timeline.tsx) because they need hover tooltips and hit targets and
 * there are only tens of them.
 *
 * The canvas is redrawn on a rAF loop that only runs while the view or the data
 * actually changes, so a static timeline costs nothing — plus one redraw per
 * animation frame while the transport is rolling, because the playhead is
 * moving and it is read from the audio clock rather than from a React snapshot.
 */
import { useEffect, useRef } from 'react'
import type { SongSummary } from '../types'
import { gridStepSeconds } from '../lib/sectionEditing'
import { beatsPerBar, EVENT_COLORS } from '../lib/format'
import type { PeaksData, StemWave } from '../lib/peaks'
import type { CurveSeries } from '../hooks/useData'
import type { EnergyChange } from '../types'
import { componentLabel } from '../lib/format'

/**
 * Coordinate convention — read this before touching any drawing code.
 *
 * There are two x spaces in this file, and mixing them is the most damaging
 * mistake available here:
 *
 *   plot x   0 .. g.plotW     relative to the left edge of the plot area. Every
 *                             row-drawing loop iterates in this space, because
 *                             the loop bound is the plot width.
 *   canvas x 0 .. g.width     absolute CSS pixels. The plot starts at GUTTER,
 *                             so canvas x = GUTTER + plot x.
 *
 * The bugs that motivated this note were one mistake repeated: iterating
 * `for (p = 0; p < PW; p++)` and then calling `ctx.lineTo(p, ...)` without
 * adding the gutter. The waveform, the energy curves and the onset-density row
 * all drew from the very left edge of the canvas, ran underneath the label
 * gutter, and stopped 116px short of the right edge — so they disagreed with
 * the section bands, the ruler and the playhead, which are all gutter-aware.
 *
 * Rules:
 *   - iterate plot x, draw at `plotX(g, p)`
 *   - convert time with `plotToT(p, g)`, never `xToT` (which takes canvas x)
 *   - full-width chrome spans `0 .. g.width`, or `GUTTER .. GUTTER + plotW`;
 *     it never ends at `plotW` on its own.
 */

/** Width of the left gutter that holds lane labels. */
export const GUTTER = 116

export const LAYOUT = {
  ruler: 22,
  /**
   * The section ribbon is the first thing a producer looks for and the last
   * thing this layout used to give them. It was 15px — a sliver under three rows
   * of toolbar — while the waveform took 230. It is now tall enough to hold a
   * readable label, because a row you cannot read is not a summary.
   */
  sections: 48,
  waveform: 44,
  energy: 34,
  events: 16,
  /**
   * Three stacked band lanes, 22px each.
   *
   * It was 22px for a single averaged trace, which is not enough for three: at
   * 7px apiece the traces have no room for a label and no room for a mean rule,
   * so the row costs the same vertical space as before while saying a third as
   * much. The row is off by default, so this costs nothing until it is asked for.
   */
  spatial: 66,
  lane: 19,
  laneGap: 1,
  /** Lanes grow with spare space but stop here, or a tall window turns the
   *  activity lanes into featureless slabs. */
  laneMax: 56,
}

export interface TimelineGeom {
  /** Full canvas width including the label gutter. */
  width: number
  height: number
  /** Width available for drawing time-based content. */
  plotW: number
  /** Extra pixels distributed into the stretchable rows. */
  stretch: number
  t0: number
  t1: number
  /**
   * The playhead, as a *live* value read at draw time — not a number.
   *
   * Every other field here is a snapshot the render captured, which is correct:
   * a change to any of them is a change to what the timeline *is*. The playhead
   * is different, because it is not a property of the timeline, it is a reading
   * of the audio clock, and it moves whether or not React re-renders. Passing it
   * as a number meant the canvas drew whatever the last render happened to carry,
   * and that snapshot is published at 20 Hz: measured against the audio clock in
   * the browser, the drawn playhead trailed it by 32 ms at the median, 48 ms at
   * p90 and 59 ms at worst, over 80 animation frames. On a transient — an 808
   * landing, a snare hit — that reads as the sound arriving before the playhead
   * gets to it, which is exactly what it was reported as doing, on every
   * waveform, because it is one clock and not one lane.
   *
   * The 60 Hz rAF in `usePlayer` already maintains this ref; it existed for
   * precisely this purpose and nothing was reading it.
   */
  playheadRef: { current: number }
  hover: { t: number; x: number; y: number } | null
  selectionA: { start: number; end: number } | null
  selectionB: { start: number; end: number } | null
  hiddenSources: Set<string>
  collapsedFamilies: Set<string>
  showEnergyRaw: boolean
  showEnergyComponents: boolean
  showWidth: boolean
  showWaveform: boolean
  showEvents: boolean
  gridSnap: boolean
  focusSource: string | null
}

/** Smoothing window applied to the energy row, seconds. Read by the label. */
let ENERGY_WINDOW_S = 0
/** Whole-song display treatment for a near-silent ending. */
let ENERGY_ENDING_COMPRESSED = false
const ENERGY_KNEES = new WeakMap<CurveSeries, number | false>()

function energyOverviewKnee(series: CurveSeries): number | false {
  const cached = ENERGY_KNEES.get(series)
  if (cached !== undefined) return cached
  const points = series.points.filter(Number.isFinite)
  if (points.length < 100) { ENERGY_KNEES.set(series, false); return false }
  const body = points.slice(0, Math.floor(points.length * .90)).sort((a, b) => a - b)
  const ending = points.slice(Math.floor(points.length * .96))
  const bodyLow = body[Math.floor(body.length * .10)]
  const endingMean = ending.reduce((sum, value) => sum + value, 0) / ending.length
  // Keep quiet passages on the ordinary scale. Only an unusually quiet tail
  // on an otherwise active recording gets this whole-song display treatment.
  const knee = bodyLow > .55 && endingMean < .30 && points[points.length - 1] < .12
    ? Math.min(.72, Math.max(.45, bodyLow - .04)) : false
  ENERGY_KNEES.set(series, knee)
  return knee
}

function energyDisplayValue(value: number, knee: number | false, strength: number): number {
  if (knee === false || strength <= 0) return value
  const v = Math.max(0, Math.min(1, value))
  const focused = v <= knee ? .25 * v / knee : .25 + .75 * (v - knee) / (1 - knee)
  return value + (focused - value) * strength
}

/**
 * Why a row is empty, when it is empty for a reason.
 *
 * An empty row is three different things — still loading, deliberately off, or
 * the curve does not exist — and they look identical, which is how the
 * stereo-width row sat blank for a long time with nothing anywhere saying the
 * name was wrong. The row label now prints the reason, so a failure looks like a
 * failure instead of a design choice.
 */
export const CURVE_ERRORS: Record<string, string> = {}
/** Top of the stereo-width row's current scale. Read by the label. */
let WIDTH_SCALE_S = 0
/** Averaging applied before that scale was set. Read by the label. */
let WIDTH_WINDOW_S = 0

/**
 * One colour per width band, in frequency order.
 *
 * Warm to cool, low to high, because that is the convention every producer
 * already has for frequency and because it means the three traces are
 * recognisable by hue alone before reading a label. `high` keeps the sky blue
 * the row was already drawn in, so the band that carried the existing visual
 * weight does not move.
 *
 * Contrast against the lane background is at least 3:1 for all three, which is
 * what WCAG 1.4.11 asks of a chart mark; a band colour below that reads as a
 * tint rather than as data.
 */
export const WIDTH_BAND_COLORS = ['#f4795b', '#e8b53c', '#38bdf8'] as const

/**
 * A band as the analysis reports it.
 *
 * The edges come from the server, from the same `SPATIAL_BANDS` the measurement
 * was filtered with, rather than being written down here. A hard-coded "20-250"
 * beside a curve the server filtered at 20-250 is one edit away from labelling
 * the wrong band.
 */
export interface WidthBand {
  key: string
  label: string
  lo_hz: number
  hi_hz: number
  curve: string
}

/** `250-2000` reads as a range; `2.5k` does not, and the range is the point. */
export function bandHzLabel(b: WidthBand): string {
  return `${b.lo_hz}-${b.hi_hz >= 1000 ? `${b.hi_hz / 1000}k` : b.hi_hz}`
}

const C = {
  bg: '#0b0e13',
  laneBg: '#11151d',
  laneAlt: '#0e1219',
  line: '#252d3a',
  line2: '#2f3948',
  fg: '#dfe5ee',
  dim: '#98a3b4',
  faint: '#6b7688',
  accent: '#4cc2ff',
  // Waveform. Two tones so a loud passage reads brighter than a quiet one
  // without leaving the palette: the older single tone sat at roughly 1.6:1
  // against the background, which is below the 3:1 that WCAG 1.4.11 asks of
  // chart marks and is why the row looked like a haze.
  wave: '#6b8bb3',
  waveBright: '#9dbbdd',
  waveQuiet: '#3d5170',
}

/** Time at plot-relative x. See the coordinate convention above. */
function plotToT(p: number, g: TimelineGeom) {
  return g.t0 + (p / Math.max(1, g.plotW)) * (g.t1 - g.t0)
}

/** Plot-relative x → canvas x. Every draw call for row content goes via this. */
function plotX(g: TimelineGeom, x: number) {
  return GUTTER + x
}

function tToX(t: number, g: TimelineGeom) {
  const span = g.t1 - g.t0 || 1
  return GUTTER + ((t - g.t0) / span) * g.plotW
}

function xToT(x: number, g: TimelineGeom) {
  const span = g.t1 - g.t0 || 1
  return g.t0 + ((x - GUTTER) / Math.max(1, g.plotW)) * span
}

/** Height of a row after stretching. */
function rowH(base: number, g: TimelineGeom, factor: number): number {
  return base + g.stretch * factor
}

function visibleLanes(song: SongSummary, g: TimelineGeom) {
  const out: typeof song.sources = []
  for (const s of song.sources) {
    if (g.hiddenSources.has(s.id)) continue
    if (g.collapsedFamilies.has(s.family)) continue
    out.push(s)
  }
  return out
}

/** Natural height of the timeline at its base row sizes. */
export function lanesHeight(song: SongSummary | null, g: Partial<TimelineGeom>) {
  if (!song) return LAYOUT.ruler + LAYOUT.sections + LAYOUT.waveform
  const geo = g as TimelineGeom
  const lanes = visibleLanes(song, geo)
  let h = LAYOUT.ruler + LAYOUT.sections
  h += g.showWaveform === false ? 0 : LAYOUT.waveform
  h += LAYOUT.energy + (g.showEvents ? LAYOUT.events : 0)
  if (g.showWidth !== false) h += LAYOUT.spatial
  h += lanes.length * (laneHeight(geo) + LAYOUT.laneGap)
  return h
}

/**
 * Distribute leftover vertical space across the stretchable rows so the
 * timeline fills its container instead of hugging the top. Waveform and energy
 * take most of it because that is what a producer reads at a glance; lanes grow
 * just enough to stay clickable.
 */
/** Lane height after stretch, with the same cap the drawer uses. */
export function laneHeight(g: Partial<TimelineGeom>): number {
  return Math.min(LAYOUT.laneMax, LAYOUT.lane + (g.stretch ?? 0) * 0.9)
}

export function computeStretch(available: number, natural: number,
                               laneCount: number): number {
  if (available <= natural || available < 1) return 0
  // The waveform no longer takes 2.2x the slack. It was taking enough that the
  // row was a grey mass with the section ribbon a sliver beside it — the exact
  // inverse of the reading order. Waveform and energy now grow comparably and
  // lanes take the rest.
  const flexibleWeight = 5.4 + Math.max(0, laneCount) * 0.9
  return Math.min(120, (available - natural) / flexibleWeight)
}

export interface RowRect {
  key: string
  label: string
  y: number
  h: number
  color: string
}

/** All fixed rows, in draw order, for the gutter labels. */
export function rowRects(song: SongSummary | null, g: TimelineGeom): RowRect[] {
  const out: RowRect[] = []
  let y = LAYOUT.ruler + LAYOUT.sections
  if (g.showWaveform !== false) {
    const h = rowH(LAYOUT.waveform, g, 1.4)
    out.push({ key: 'wave', label: 'waveform', y, h, color: '#5c7799' })
    y += h
  }
  const eh = rowH(LAYOUT.energy, g, 1.4)
  out.push({ key: 'energy', label: 'energy', y, h: eh, color: '#a78bfa' })
  y += eh
  if (g.showEvents) {
    const evh = rowH(LAYOUT.events, g, 0.15)
    out.push({ key: 'events', label: 'changes', y, h: evh, color: '#4ade80' })
    y += evh
  }
  if (g.showWidth !== false) {
    const h = rowH(LAYOUT.spatial, g, 0.9)
    out.push({ key: 'width', label: 'stereo width', y, h, color: '#38bdf8' })
    y += h
  }
  void song
  return out
}

export function laneRects(song: SongSummary, g: TimelineGeom) {
  const rects: { id: string; label: string; color: string; y: number; h: number }[] = []
  let y = LAYOUT.ruler + LAYOUT.sections
  if (g.showWaveform !== false) y += rowH(LAYOUT.waveform, g, 1.4)
  y += rowH(LAYOUT.energy, g, 1.4)
  if (g.showEvents) y += rowH(LAYOUT.events, g, 0.15)
  if (g.showWidth !== false) y += rowH(LAYOUT.spatial, g, 0.9)
  const lh = laneHeight(g)
  for (const s of visibleLanes(song, g)) {
    rects.push({ id: s.id, label: s.label, color: s.color, y, h: lh })
    y += lh + LAYOUT.laneGap
  }
  return rects
}

export function useTimelineCanvas(
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  song: SongSummary | null,
  geomRef: React.MutableRefObject<TimelineGeom>,
  peaks: PeaksData | null,
  energy: CurveSeries | null,
  energyRaw: CurveSeries | null,
  /** One lane per band, keyed by the curve name the server named. */
  widthBands: WidthBand[],
  widthSeries: Record<string, CurveSeries>,
  widthBandErrors: Record<string, string>,
  energyChanges: EnergyChange[],
  onEnergyChange: ((c: EnergyChange) => void) | null,
  stemWaves: Record<string, StemWave>,
  playheadOverlayRef: React.RefObject<HTMLDivElement | null>,
) {
  const drawRef = useRef<() => void>(() => {})
  // Read through a ref, like the geometry: the draw closure is installed once
  // and must see the newest envelopes without being reinstalled per fetch, which
  // would tear down and rebuild the whole draw on every zoom step.
  const wavesRef = useRef(stemWaves)
  wavesRef.current = stemWaves

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d', { alpha: false })
    if (!ctx) return

    const draw = () => {
      const g = geomRef.current
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      const w = Math.max(1, Math.floor(canvas.clientWidth))
      const h = Math.max(1, Math.floor(canvas.clientHeight))
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr
        canvas.height = h * dpr
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      ctx.fillStyle = C.bg
      ctx.fillRect(0, 0, w, h)
      if (!song) return

      g.width = w
      g.plotW = Math.max(40, w - GUTTER)
      const PW = g.plotW
      const t0 = Math.max(0, g.t0)
      const t1 = g.t1

      // lane-label gutter
      ctx.fillStyle = '#0d1117'
      ctx.fillRect(0, 0, GUTTER, h)
      ctx.strokeStyle = C.line
      ctx.beginPath()
      ctx.moveTo(GUTTER + 0.5, 0); ctx.lineTo(GUTTER + 0.5, h)
      ctx.stroke()

      // ---- section tint bands (behind everything) -----------------
      // Section boundaries as faint vertical rules. A full-height alternating
      // wash reads as stray boxes behind the rows and fights with the data.
      for (const sec of song.sections) {
        if (sec.end < t0 || sec.start > t1) continue
        const x0 = Math.round(tToX(sec.start, g)) + 0.5
        ctx.strokeStyle = 'rgba(255,255,255,0.055)'
        ctx.beginPath()
        ctx.moveTo(x0, LAYOUT.ruler)
        ctx.lineTo(x0, h)
        ctx.stroke()
      }

      // ---- lanes ---------------------------------------------------
      const lanes = laneRects(song, g)
      lanes.forEach((lane, i) => {
        ctx.fillStyle = i % 2 ? C.laneBg : C.laneAlt
        ctx.fillRect(GUTTER, lane.y, PW, lane.h)
      })

      // ---- waveform -------------------------------------------------
      let y = LAYOUT.ruler + LAYOUT.sections
      if (g.showWaveform !== false) {
        const wh = rowH(LAYOUT.waveform, g, 1.4)
        // Deliberately no energy fill behind the waveform. The perceived-energy
        // curve is drawn in the row directly below, at its own scale with its
        // own baseline; repeating it here gave one measurement two different
        // heights, and the translucent wash sat on top of the waveform peaks and
        // flattened exactly the contrast that makes a waveform readable.
        drawWaveform(ctx, g, y, wh, peaks)
        ctx.strokeStyle = 'rgba(255,255,255,0.07)'
        ctx.beginPath()
        ctx.moveTo(GUTTER, Math.round(y + wh) + 0.5)
        ctx.lineTo(plotX(g, PW), Math.round(y + wh) + 0.5)
        ctx.stroke()
        y += wh
      }

      // ---- energy ---------------------------------------------------
      const eh = rowH(LAYOUT.energy, g, 1.4)
      y = drawEnergy(ctx, g, PW, y, energy, energyRaw, eh, song.duration)
      drawEnergyChanges(ctx, g, PW, y - eh, eh, energyChanges)
      if (g.showEvents) y += rowH(LAYOUT.events, g, 0.15)

      // ---- spatial --------------------------------------------------
      if (g.showWidth !== false) {
        y = drawSpatial(ctx, g, PW, y, widthBands, widthSeries, widthBandErrors,
          song, rowH(LAYOUT.spatial, g, 0))
      }

      // ---- source activity lanes -------------------------------------
      for (const lane of lanes) {
        const src = song.sources.find((s) => s.id === lane.id)
        if (!src) continue
        drawLane(ctx, g, PW, lane, src, t0, t1, wavesRef.current[lane.id] ?? null)
      }

      // ---- gutter labels ----------------------------------------------
      ctx.textBaseline = 'middle'
      ctx.font = '9px ui-monospace, monospace'
      // No reset here. The reasons are published during render, by
      // `setCurveRowError`, which deletes a key when there is no error — so the
      // slot is already correct for this frame. Clearing it at the top of the
      // draw wiped the reason immediately before the label pass read it, which
      // is why the row still said only the generic "unavailable".
      for (const row of rowRects(song, g)) {
        ctx.fillStyle = row.color
        ctx.globalAlpha = 0.75
        ctx.fillRect(6, row.y + 4, 5, 5)
        ctx.globalAlpha = 1
        ctx.fillStyle = C.faint
        // The stereo width row's gutter is a legend — swatch, band, range, mean —
        // so its label heads that. Everywhere else the label is vertically
        // centred in its row. A row taller than a single line of text would
        // otherwise centre its label in the middle of the plot area, which for
        // the width row is a legend entry.
        const mid = row.key === 'width' || row.h > 44
          ? row.y + 10 : row.y + Math.min(11, row.h / 2)
        ctx.fillText(row.label, 16, mid)
        // Why this row is empty, when that is why. An empty row is three
        // different things — still loading, deliberately off, or the curve does
        // not exist — and they look identical, which is how the stereo-width row
        // sat blank for a long time with nothing anywhere saying the name was
        // wrong.
        const why = CURVE_ERRORS[row.key]
        // One secondary line per row. A row that failed says why; a row that
        // loaded says its number. Drawing both put the reason on top of the
        // number, which is unreadable and says two things at once.
        if (!why && row.key === 'width' && WIDTH_SCALE_S > 0) {
          // Only the top of the scale. The per-band means moved into the lanes
          // themselves, next to the curve they belong to, because a number in
          // the gutter is separated from the trace it describes by the width of
          // the plot.
          ctx.fillStyle = '#38bdf8'
          ctx.fillText(`0-${WIDTH_SCALE_S.toFixed(2)}`, 16, mid + 10)
        }
        if (!why && row.key === 'energy' && ENERGY_ENDING_COMPRESSED) {
          ctx.fillStyle = '#a78bfa'
          ctx.fillText('quiet range scaled', 16, mid + 10, GUTTER - 22)
        } else if (!why && row.key === 'energy' && ENERGY_WINDOW_S > 0.01 && g.t1 - g.t0 < 45) {
          // Say the curve is smoothed, and by how much. A smoothed line that
          // does not declare itself is a line the user will over-read.
          ctx.fillStyle = '#a78bfa'
          ctx.fillText(ENERGY_WINDOW_S >= 0.05
            ? `${ENERGY_WINDOW_S.toFixed(1)}s avg`
            : `${Math.round(ENERGY_WINDOW_S * 1000)}ms avg`, 16, mid + 10)
        }
        if (why) {
          ctx.fillStyle = '#f59e0b'
          const msg = why.length > 20 ? `${why.slice(0, 19)}…` : why
          ctx.fillText(msg, 16, mid + 10)
        }
      }

      // ---- ruler (drawn last so it sits on top) ----------------------
      drawRuler(ctx, g, PW, song, t0, t1)

      // ---- playhead --------------------------------------------------
      // Read live, inside draw(), from the audio clock's own ref. Not from the
      // geometry: see the note on `playheadRef`.
      const headX = tToX(g.playheadRef.current, g)
      // The ribbon is a DOM layer above the canvas. Update its foreground
      // playhead in this same draw, using the same live clock and geometry.
      const overlay = playheadOverlayRef.current
      if (overlay) {
        overlay.style.display = headX >= GUTTER && headX <= w ? 'block' : 'none'
        overlay.style.transform = `translateX(${Math.round(headX)}px)`
      }
    }

    drawRef.current = draw
    let raf = 0
    let lastKey = ''
    // The playhead position the canvas last drew. The redraw decision compares
    // the live value against it, rather than consulting a "playing" flag.
    //
    // That is deliberately self-synchronising. The first version of this fix
    // gated the per-frame redraw on `state.playing` from React, and calling the
    // Player directly — which is how the player is driven while investigating a
    // bug report — left that flag false while the audio advanced, so the
    // playhead stopped moving. A gate that has to agree with the transport is
    // the same bug as a gate that reads a stale snapshot: it can disagree, and
    // when it does the symptom is a frozen playhead, which is worse than the
    // 50 ms of lag it replaced. Comparing against what was last drawn cannot
    // disagree with anything.
    let lastDrawnPlayhead = Number.NaN
    // Canvas size is part of the render key, fed by a ResizeObserver rather
    // than read inline. Reading clientWidth every frame would force a
    // synchronous layout 60 times a second; the observer fires only when the
    // size actually changes. Without this, the first frame could be drawn
    // before the row set had settled and the canvas would keep its default
    // 300x150 backing store forever — the drawing would be present but
    // stretched and blurry, with nothing on screen to suggest it was wrong.
    const sizeRef = { current: '' }
    const ro = new ResizeObserver(() => {
      sizeRef.current = `${Math.floor(canvas.clientWidth)}x${Math.floor(canvas.clientHeight)}`
    })
    ro.observe(canvas)
    sizeRef.current = `${Math.floor(canvas.clientWidth)}x${Math.floor(canvas.clientHeight)}`

    const loop = () => {
      const g = geomRef.current
      const key = [
        song?.id, g.t0.toFixed(3), g.t1.toFixed(3), g.width,
        [...g.hiddenSources].join(), [...g.collapsedFamilies].join(),
        g.showEnergyRaw, g.showWidth, g.showWaveform, g.focusSource,
        g.stretch.toFixed(2), sizeRef.current,
        peaks?.t0, peaks?.t1, energy?.points.length, energyRaw?.points.length,
        energyChanges.length,
        // Three band curves, one request. Keyed on the window each was read
        // over, so a new window's arrival is a redraw even though the set of
        // names never changes.
        Object.values(widthSeries).map((s) => `${s.name}@${s.t0 ?? 0}`).join(),
        Object.keys(widthSeries).length,
        // The stem envelopes arrive after the lanes are already drawn, and a new
        // window refetches the same six ids. Keying on which stems are present
        // would miss the second; the window start is what changes on every fetch.
        Object.entries(wavesRef.current)
          .map(([k, v]) => `${k}@${v.t0.toFixed(2)}`).join(),
      ].join('|')
      // The playhead is deliberately *not* in the key. It used to be, keyed on a
      // 20 Hz snapshot, which is what made the drawn playhead trail the sound.
      // Now it is read from the audio clock inside draw(), and the redraw is
      // driven by whether that live value has moved since the last frame — so
      // the transport rolling means a redraw per animation frame, and a paused
      // playhead means none. The key alone still decides everything else, so a
      // static timeline costs nothing.
      const head = g.playheadRef.current
      if (key !== lastKey || head !== lastDrawnPlayhead) {
        lastKey = key
        lastDrawnPlayhead = head
        try {
          draw()
        } catch (err) {
          // A throw inside draw() must not end the render loop: the canvas
          // would freeze on a half-drawn frame and never recover.
          // eslint-disable-next-line no-console
          console.error('timeline draw failed', err)
        }
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    const onResize = () => draw()
    window.addEventListener('resize', onResize)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      window.removeEventListener('resize', onResize)
    }
  }, [canvasRef, song, peaks, energy, energyRaw, widthBands, widthSeries,
       widthBandErrors, energyChanges, onEnergyChange,
       geomRef, stemWaves, playheadOverlayRef])

  return drawRef
}

/**
 * Value of a windowed curve at time `t`.
 *
 * The server resamples each window to the requested number of points, so the
 * effective rate is `n / (t1 - t0)` — *not* the curve's native `frame_hz`.
 * Indexing by frame_hz therefore drifts badly in a zoomed view and the curve
 * reads as a flat block. We map through the returned `times` array instead,
 * which is always correct whatever resampling the server applied.
 */
/**
 * Resample a curve to one value per plot column, averaging over a window that
 * follows the zoom.
 *
 * The window is chosen so the row always carries roughly `TARGET_DETAIL`
 * wiggles, whatever the zoom. A fixed window is wrong at both ends: too wide and
 * a 4-second zoom loses the transients you zoomed in to hear, too narrow and a
 * 3-minute zoom is a band of noise. Averaging in the *time* domain (via the
 * series' own `times` array) rather than by index is what makes this correct at
 * any server-side resampling.
 *
 * Returns the window in seconds so the caller can label the row honestly.
 */
const TARGET_DETAIL = 220

function resampleAdaptive(
  s: CurveSeries | null, g: TimelineGeom, PW: number,
): { col: Float32Array; windowS: number } {
  const n = PW + 1
  const empty = new Float32Array(n)
  if (!s || !s.points.length) return { col: empty, windowS: 0 }
  const m = s.points.length
  const t0 = s.times[0]
  const t1 = s.times[m - 1]
  if (!(t1 > t0)) {
    empty.fill(s.points[0])
    return { col: empty, windowS: 0 }
  }
  const span = g.t1 - g.t0
  // One smoothing window per `span / TARGET_DETAIL` seconds, in samples.
  const windowS = Math.max(0, span / TARGET_DETAIL)
  const perSample = (t1 - t0) / Math.max(1, m - 1)
  const half = Math.max(0, windowS / 2 / perSample)
  const at = (t: number) => {
    if (t <= t0) return s.points[0]
    if (t >= t1) return s.points[m - 1]
    const f = ((t - t0) / (t1 - t0)) * (m - 1)
    const i = Math.floor(f)
    const frac = f - i
    return s.points[i] * (1 - frac) + s.points[Math.min(m - 1, i + 1)] * frac
  }
  const col = new Float32Array(n)
  for (let p = 0; p < n; p++) {
    const t = plotToT(p, g)
    if (half < 0.5) { col[p] = at(t); continue }
    let acc = 0
    let wsum = 0
    for (let k = -Math.ceil(half); k <= Math.ceil(half); k++) {
      const w = 1 - Math.abs(k) / (half + 1)
      if (w <= 0) continue
      acc += at(t + k * perSample) * w
      wsum += w
    }
    col[p] = wsum > 0 ? acc / wsum : at(t)
  }
  return { col, windowS }
}

function sampleSeries(s: CurveSeries | null, t: number): number {
  if (!s || !s.points.length) return 0
  const n = s.points.length
  if (n === 1) return s.points[0]
  const t0 = s.times[0]
  const t1 = s.times[n - 1]
  if (!(t1 > t0)) return s.points[0]
  if (t <= t0) return s.points[0]
  if (t >= t1) return s.points[n - 1]
  const f = ((t - t0) / (t1 - t0)) * (n - 1)
  const i = Math.floor(f)
  const frac = f - i
  return s.points[i] * (1 - frac) + s.points[Math.min(n - 1, i + 1)] * frac
}

/**
 * Min/max waveform, one column per device pixel of the plot area.
 *
 * The peak array is indexed by *time*, not by canvas x: the server resamples it
 * to whatever window is visible, so `frac` is the fraction of the covered time
 * range, and the column drawn at plot x is the peak at that time. The earlier
 * version indexed by `p / w * peaks.width` and then computed the time anyway,
 * using only the second result — and drew it at plot x with no gutter, so the
 * waveform sat under the labels and stopped short of the right edge.
 */
function drawWaveform(
  ctx: CanvasRenderingContext2D, g: TimelineGeom, y: number, h: number,
  peaks: PeaksData | null,
) {
  if (!peaks || !peaks.peaks.length) return
  const span = peaks.t1 - peaks.t0
  if (!(span > 0)) return

  const mid = y + h / 2
  const amp = (h / 2) * 0.97
  const last = peaks.peaks.length - 1
  const n = peaks.peaks.length

  const at = (p: number): [number, number, number?] | null => {
    const t = plotToT(p, g)
    // Outside the covered range there is no peak to draw. Smearing the edge
    // value across the gap would draw a flat wall that looks like audio.
    if (t < peaks.t0 || t > peaks.t1) return null
    const frac = (t - peaks.t0) / span
    let idx = Math.floor(frac * n)
    if (idx < 0) idx = 0
    if (idx > last) idx = last
    return peaks.peaks[idx]
  }

  // Two layers, and which one carries the value depends on the zoom.
  //
  // The envelope (the mean of the samples in the column) is drawn as a filled
  // body: that is the loudness shape, and it is what makes a quiet passage look
  // quiet. The min/max bound is drawn over it as thin texture, so transients
  // are visible without becoming the thing you read.
  //
  // The bound used to be the only layer, and at whole-song zoom it saturated:
  // the maximum of a quarter second of a mastered track is near full scale
  // almost everywhere, so the row was one solid rectangle — measured as a single
  // colour down all 148 rows of it. There is no way to fix that by scaling; a
  // bound is the wrong statistic for "how loud is this moment".
  const hasMean = peaks.peaks.length > 0 && peaks.peaks[0].length >= 3

  if (hasMean) {
    // Scale the envelope to the row.
    //
    // The third value is an rms, and a mastered track's rms tops out near 0.45 of
    // full scale — drawn against 1.0 the loudest passage reached barely 40% of
    // the row and the rest was dead space. Scaling by the visible window's own
    // 98th percentile puts the loudest moment at full height and keeps a quiet
    // one proportionally quiet.
    //
    // Recomputed per redraw rather than cached, which is what makes it stable:
    // the scale follows the *window*, not the audio, so it does not breathe while
    // the track plays. A percentile rather than the max, so one transient does
    // not flatten everything else.
    // A cheap 98th percentile: collect, sort, index. One entry per column, a
    // few thousand at most, which is nothing next to the path work. A percentile
    // rather than the max, so one transient does not flatten everything else.
    const envs: number[] = []
    for (let p = 0; p < g.plotW; p++) {
      const pk = at(p)
      if (pk) envs.push(Math.abs(pk[2] ?? 0))
    }
    envs.sort((a, b) => a - b)
    const p98v = envs.length ? Math.max(1e-4, envs[Math.min(envs.length - 1,
      Math.floor(envs.length * 0.98))]) : 1
    // Smooth the envelope to the zoom, the way the energy row already does.
    //
    // At 30 s across 800 pixels a column is 37 ms — about 1600 samples — and the
    // rms of a dense modern mix still swings wildly over that, so the row came out
    // as vertical grass rather than a shape. Widening the window with the zoom
    // fixes it at the level it is wrong: the loudness of a *passage* is what the
    // eye reads, and a passage is longer than a pixel. Three columns is the floor
    // so a fully zoomed-in waveform keeps its transient detail.
    const perCol = (peaks.t1 - peaks.t0) / Math.max(1, g.plotW)
    const smoothW = Math.max(1, Math.min(9, Math.round(perCol / 0.012)))
    const envAt = (p: number): number => {
      const pk = at(p)
      let v = pk ? Math.abs(pk[2] ?? 0) : 0
      if (smoothW <= 1) return v
      let acc = 0
      let n = 0
      const half = smoothW >> 1
      for (let k = -half; k <= half; k++) {
        const q = at(p + k)
        if (!q) continue
        acc += Math.abs(q[2] ?? 0)
        n++
      }
      return n ? acc / n : v
    }
    const envScale = amp / p98v

    for (let p = 0; p < g.plotW; p++) {
      if (!at(p)) continue
      const env = envAt(p)
      if (env < 0.002) continue
      const bodyH = Math.max(1, Math.min(amp * 2, env * envScale))
      ctx.fillStyle = env > p98v * 0.55
        ? 'rgba(122,162,206,0.62)' : 'rgba(92,124,158,0.40)'
      ctx.fillRect(plotX(g, p), mid - bodyH / 2, 1, bodyH)
    }
    // The bound on top, thin and low-contrast. When the column is narrower than
    // a bucket it *is* the envelope, so drawing it again would only thicken the
    // body; hence the width test.
    const perPx = (peaks.t1 - peaks.t0) / Math.max(1, g.plotW)
    if (perPx < 0.02) {
      ctx.fillStyle = 'rgba(157,187,221,0.45)'
      for (let p = 0; p < g.plotW; p++) {
        const pk = at(p)
        if (!pk) continue
        const lo = pk[0] ?? 0
        const hi = pk[1] ?? 0
        const top = mid - hi * amp
        const bot = mid - lo * amp
        if (bot - top < 1) continue
        ctx.fillRect(plotX(g, p), top, 1, Math.max(1, bot - top))
      }
    }
  } else {
    // No envelope available (older payload): the bound is all there is, so draw
    // it as a body rather than as invisible texture.
    for (let p = 0; p < g.plotW; p++) {
      const pk = at(p)
      if (!pk) continue
      const lo = pk[0] ?? 0
      const hi = pk[1] ?? 0
      const peak = Math.max(Math.abs(lo), Math.abs(hi))
      if (peak < 0.004) continue
      ctx.fillStyle = peak > 0.4 ? C.wave : C.waveQuiet
      const top = mid - hi * amp
      const bot = mid - lo * amp
      ctx.fillRect(plotX(g, p), top, 1, Math.max(1, bot - top))
    }
  }

  // Centre line, so the mirror axis is a deliberate mark and the row does not
  // read as a solid rectangle with a hole down the middle.
  ctx.fillStyle = 'rgba(255,255,255,0.10)'
  ctx.fillRect(GUTTER, Math.round(mid), g.plotW, 1)
}

