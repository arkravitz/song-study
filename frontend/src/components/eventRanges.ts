/** Render the continuous events the analysis could not bound, instead of
 * dropping them.
 *
 * `if (e.end === null) return null` in the event overlay silently discarded
 * every continuous change the analysis detected but could not give an end time
 * to — "Stereo image narrows", "Arrangement thickens". That is 37% of the
 * events on one test track, 25% on another and 12% on a third. The tool found
 * them, and the interface did not show them.
 *
 * The class is real, not an edge case: a continuous event means "this changed at
 * t", and the state it changed into may simply run to the end of the section.
 * So an unbounded one is drawn as an open-ended span — it runs to the next
 * change of the same kind, or to the end of the enclosing section, or to the
 * end of the record — and is marked with a dashed right edge and an arrow cap so
 * the absence of a measured end is visible rather than implied.
 *
 * A producer reading the row should be able to see "something changed here and
 * it is still going" as a distinct state from "this happened".
 */
import type { ArrangementEvent, Section } from '../types'

export interface DrawnEvent {
  event: ArrangementEvent
  start: number
  /** null means "open-ended": no end was measured. */
  end: number | null
  openEnded: boolean
}

export function drawableEvents(
  events: ArrangementEvent[], sections: Section[], duration: number,
): DrawnEvent[] {
  const out: DrawnEvent[] = []

  // Next change of the same kind, so an unbounded change runs until the state
  // it established is next disturbed.
  const nextOfKind = new Map<string, number>()
  const sorted = [...events].sort((a, b) => a.t - b.t)
  for (let i = sorted.length - 1; i >= 0; i--) {
    const e = sorted[i]
    const existing = nextOfKind.get(e.kind)
    if (existing !== undefined) e._next = existing
    nextOfKind.set(e.kind, e.t)
  }

  const sectionEndAt = (t: number): number => {
    for (const s of sections) {
      if (t >= s.start - 0.05 && t < s.end) return s.end
    }
    return duration
  }

  for (const e of sorted) {
    if (e.end != null) {
      out.push({ event: e, start: e.t, end: e.end, openEnded: false })
      continue
    }
    if (!e.is_continuous) {
      // A discrete event with no end is really an instant; draw it as one.
      out.push({ event: e, start: e.t, end: null, openEnded: false })
      continue
    }
    const nxt = e._next
    const end = nxt !== undefined && nxt > e.t
      ? nxt
      : sectionEndAt(e.t)
    out.push({ event: e, start: e.t, end: Math.min(end, duration), openEnded: true })
  }
  return out
}

declare module '../types' {
  interface ArrangementEvent {
    /** Next change of the same kind, seconds. Internal; not serialised. */
    _next?: number
  }
}
