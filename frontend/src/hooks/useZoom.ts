/** Zoom / scroll / selection state for the timeline. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

export interface ViewRange {
  t0: number
  t1: number
}

export interface Range {
  start: number
  end: number
}

const MIN_SPAN = 0.5

/**
 * The default view is **the whole song**, not a fixed span.
 *
 * It used to be `min(30, max(8, duration / 4))` -- up to 30 seconds -- and that
 * was chosen when the timeline was a waveform and a playhead. It is wrong now
 * that the timeline carries a section ribbon, a per-instrument lane and an
 * events row, because a fragment shows none of them: "DNA." opened at 0:00-0:30,
 * which is 16% of the record and **2 of its 9 sections**, while the form list
 * beside it listed all nine. The two halves of the screen disagreed about what
 * the song was, and there was no way to see the shape of the arrangement without
 * first finding the fit button.
 *
 * Nothing is lost by opening wide: the view follows the playhead during
 * playback, and zooming in is one click.
 *
 * `initialSpan` still exists for a caller that wants something else, and is
 * undefined here so the default is "the whole song" rather than a number written
 * down in two places.
 */
export function useZoomView(duration: number, initialSpan?: number) {
  const firstSpan = initialSpan ?? duration
  const [view, setView] = useState<ViewRange>(() => ({
    t0: 0, t1: Math.min(duration, firstSpan),
  }))
  const [follow, setFollow] = useState(true)
  // The *pending* view, not the rendered one.
  //
  // This was `viewRef.current = view` on every render, which meant the ref only
  // caught up when React committed. `zoomAt` reads the ref, so every zoom event
  // that arrived before the next render computed from the same starting window
  // and produced the same result: N events in one frame zoomed by one step, not
  // N. A trackpad flick delivers several wheel events per frame, and the ＋
  // button can be double-clicked, so the zoom step per gesture was whatever the
  // frame rate happened to allow — which is the "erratic zoom" the wheel
  // handler's own comment was written to avoid.
  //
  // So the ref is the source of truth for *what has been asked for*, written
  // synchronously by `setViewSafe`, and `view` is only its rendered form. Every
  // reader below (zoomAt, panTo, follow, restore) now composes correctly no
  // matter how many calls land in a frame.
  const viewRef = useRef(view)

  const clampView = useCallback((v: ViewRange): ViewRange => {
    let span = Math.max(MIN_SPAN, Math.min(duration, v.t1 - v.t0))
    if (duration <= 0) return v.t1 > v.t0 ? v : { t0: 0, t1: 1 }
    let t0 = v.t0
    if (t0 < 0) t0 = 0
    if (t0 + span > duration) t0 = duration - span
    if (t0 < 0) t0 = 0
    const out = { t0, t1: t0 + span }
    // Return the same object when nothing changed so setState is a no-op
    // instead of scheduling a render on every call.
    return Math.abs(out.t0 - v.t0) < 1e-9 && Math.abs(out.t1 - v.t1) < 1e-9 ? v : out
  }, [duration])

  // The single writer for the view. Everything that moves the window goes
  // through here, so `viewRef` cannot fall behind `view` -- which it did, at six
  // call sites, before this existed.
  const setViewSafe = useCallback((v: ViewRange): ViewRange => {
    const next = clampView(v)
    // Written before the render, not during one, so a second call in the same
    // frame sees the first one's result.
    viewRef.current = next
    setView(next)
    return next
  }, [clampView])

  // The view must be re-clamped when the song (and therefore the duration)
  // changes.  Without this the initial state — computed while duration was
  // still the placeholder — survives the load and the timeline shows a
  // one-second window of a three-minute song.
  //
  // The span is re-read from the argument on every duration change, not from a
  // ref captured on mount. Capturing it meant the placeholder duration won: with
  // duration = 1 the default span was 1, so a 167 s song opened showing its
  // first second and the `fit` button looked broken when it was the initial span
  // that was wrong. The same mistake is why `duration` must arrive as 0 rather
  // than a placeholder -- see the note in App.tsx.
  //
  // The bookkeeping happens in the effect body, *not* inside the `setView`
  // updater. An updater must be pure, and writing a ref from one is a side
  // effect that React is entitled to run twice: under StrictMode the first pass
  // set the ref and the second pass then read it, decided there was nothing to
  // do, and discarded the reset. The app opened showing one second of a
  // 2:47 record, with `fit` and every readout looking correct.
  // Zero means "no duration yet". The caller must pass 0 rather than a
  // placeholder like 1 while the analysis is loading, because this hook decides
  // what counts as the first real duration by exactly that test, and a
  // placeholder is indistinguishable from a real one-second song.
  const appliedDurationRef = useRef(0)
  // A window to restore instead of the default, consumed exactly once. Held in a
  // ref rather than read from a prop so that it is applied on the first *real*
  // duration and not on the placeholder, and not re-applied if the caller
  // re-renders with the same window still in hand. Filled by `restore()`.
  const restoreRef = useRef<ViewRange | null>(null)
  useEffect(() => {
    if (duration <= 0) return
    const want = Math.max(MIN_SPAN, Math.min(duration, firstSpan))
    const firstReal = appliedDurationRef.current === 0
    const songChanged = Math.abs(appliedDurationRef.current - duration) > 0.01
    appliedDurationRef.current = duration
    if (firstReal || songChanged) {
      const restore = restoreRef.current
      restoreRef.current = null
      // `clampView` runs on the way in, so a stored window from a different
      // render of this song — or from before the song was re-analysed shorter —
      // is corrected rather than trusted. A stored window is a convenience; a
      // view of somewhere past the end of the song is a bug.
      setViewSafe(restore ? restore : { t0: 0, t1: want })
    } else setViewSafe(viewRef.current)
  }, [duration, firstSpan, setViewSafe])

  /**
   * Come back to a stored window.
   *
   * Applied immediately when the duration is already known, and held for the
   * first real duration when it is not. The caller drives this explicitly rather
   * than passing a window in as a prop, because *when* it applies is the whole
   * problem: a prop is read once at mount, and mount is before the analysis
   * arrives, so the window was being computed against a placeholder duration,
   * clamped against a song one second long, and discarded when the real duration
   * arrived with nothing left to restore.
   */
  const restore = useCallback((v: ViewRange | null) => {
    restoreRef.current = v
    if (v && duration > 0) setViewSafe(v)
  }, [duration, setViewSafe])

  const zoomAt = useCallback((factor: number, anchorT?: number) => {
    const v = viewRef.current
    const span = v.t1 - v.t0
    const anchor = anchorT ?? (v.t0 + v.t1) / 2
    const frac = span > 0 ? (anchor - v.t0) / span : 0.5
    const newSpan = Math.max(MIN_SPAN, Math.min(duration, span * factor))
    const t0 = anchor - frac * newSpan
    setViewSafe({ t0, t1: t0 + newSpan })
  }, [setViewSafe, duration])

  const panTo = useCallback((t: number, keepSpan = true) => {
    const v = viewRef.current
    const span = keepSpan ? v.t1 - v.t0 : v.t1 - v.t0
    const t0 = t - span / 2
    setViewSafe({ t0, t1: t0 + span })
  }, [setViewSafe])

  const showAll = useCallback(() => {
    setViewSafe({ t0: 0, t1: duration })
  }, [setViewSafe, duration])

  /** Back to the opening view, or to an explicit span. */
  const reset = useCallback((span?: number) => {
    setViewSafe({ t0: 0, t1: Math.max(MIN_SPAN, span ?? firstSpan) })
  }, [setViewSafe, firstSpan])

  const showRange = useCallback((start: number, end: number, pad = 0.08) => {
    const span = Math.max(MIN_SPAN, end - start)
    const p = span * pad
    setViewSafe({ t0: start - p, t1: end + p })
  }, [setViewSafe])

  const followPlayhead = useCallback((t: number, playing: boolean) => {
    if (!follow) return
    const v = viewRef.current
    const span = v.t1 - v.t0
    if (t < v.t0 + span * 0.08 || t > v.t1 - span * 0.12) {
      setViewSafe({ t0: t - span * 0.4, t1: t + span * 0.6 })
    }
    void playing
  }, [follow, clampView])

  const timeToX = useCallback((t: number, width: number) => {
    const v = viewRef.current
    const span = v.t1 - v.t0 || 1
    return ((t - v.t0) / span) * width
  }, [])

  const xToTime = useCallback((x: number, width: number) => {
    const v = viewRef.current
    const span = v.t1 - v.t0 || 1
    return v.t0 + (x / Math.max(1, width)) * span
  }, [])

  return useMemo(() => ({
    view, viewRef, setView: setViewSafe, restore, zoomAt, panTo, showAll, showRange, reset,
    follow, setFollow, followPlayhead, timeToX, xToTime, clampView,
    pixelsPerSecond: 1000 / Math.max(0.001, view.t1 - view.t0),
  }), [view, setViewSafe, restore, zoomAt, panTo, showAll, showRange, follow,
       followPlayhead, timeToX, xToTime, clampView])
}

/** Drag-to-select on the timeline, supporting a second (B) range for A/B compare. */
export function useRangeSelect(
  getTime: (clientX: number) => number | null,
  onCommit: (a: Range | null, b: Range | null) => void,
) {
  const [dragging, setDragging] = useState<'a' | 'b' | null>(null)
  const [rangeA, setRangeA] = useState<Range | null>(null)
  const [rangeB, setRangeB] = useState<Range | null>(null)
  const startRef = useRef(0)

  const begin = useCallback((which: 'a' | 'b', clientX: number) => {
    const t = getTime(clientX)
    if (t === null) return
    startRef.current = t
    setDragging(which)
    if (which === 'a') setRangeA({ start: t, end: t })
    else setRangeB({ start: t, end: t })
  }, [getTime])

  const move = useCallback((clientX: number) => {
    if (!dragging) return
    const t = getTime(clientX)
    if (t === null) return
    const r = { start: Math.min(startRef.current, t), end: Math.max(startRef.current, t) }
    if (dragging === 'a') setRangeA(r)
    else setRangeB(r)
  }, [dragging, getTime])

  const end = useCallback(() => {
    if (!dragging) return
    setDragging(null)
    // Use the committed values via a ref-free approach: the state setter is
    // batched, so we re-derive from the last ref values.
    onCommit(rangeA, rangeB)
  }, [dragging, rangeA, rangeB, onCommit])

  const clear = useCallback(() => {
    setRangeA(null); setRangeB(null)
    onCommit(null, null)
  }, [onCommit])

  return { rangeA, rangeB, dragging, begin, move, end, clear }
}
