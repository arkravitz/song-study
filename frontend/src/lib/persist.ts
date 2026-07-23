/**
 * Where you were, kept across a refresh.
 *
 * Why this exists
 * ---------------
 * Reloading threw away everything except the song. Measured, by reloading a
 * document that had a sentinel on it and reading the state back:
 *
 *   before   Taste, view 0:12-0:17 of 2:37 (4%), measurements tab, one lane hidden
 *   after    DNA,   view 0:00-0:30 of 3:06 (16%), overview tab, nothing hidden
 *
 * So a refresh did not just lose the zoom, it lost the *song* — it fell back to
 * the most recently analysed one. On a library of more than one track that is
 * simply wrong: the app cannot tell which song you were looking at, and lands
 * you somewhere else without saying so. This is the same class as B-060, where
 * the app opened on one second of the song because a ref written inside a
 * `setState` updater was discarded; state that should survive a transition and
 * does not.
 *
 * What is kept, and why
 * ---------------------
 * *Which song*, and *where in it* — the view window. These are the two that make
 * you lose your place, and the view is the one that is genuinely unrecoverable:
 * there is no way to get back to 0:12 without remembering it was 0:12.
 *
 * *Which rows are on.* "stereo width" was on, and came back off. A row you
 * turned on to look at something is a row you turned on for a reason.
 *
 * *Per song, not globally.* Zooming into 1:30 of one track must not mean the next
 * track opens at 1:30. So the per-song part is keyed by song id and the
 * application part is not.
 *
 * Not kept: the playhead, the volume, the A/B ranges, and which event or section
 * was selected. None of those survive a reload meaningfully — audio does not
 * resume, and an event id from before a re-analysis may not exist after it. The
 * stored object is also pruned to the song's actual sources on write, so it
 * cannot grow without bound across re-analyses.
 *
 * The rule this file exists to enforce
 * ------------------------------------
 * **A stored state must never be able to break the app.** Every field is checked
 * on the way in, a field of the wrong type discards that field rather than the
 * whole thing, and anything unparseable is dropped silently and the app starts
 * as if nothing had been stored. Persisting state that can crash the page on load
 * is strictly worse than not persisting it, because it turns a convenience into
 * a failure mode the user cannot get out of.
 */

const KEY = 'arrangement.ui.v1'
const VERSION = 1

export interface SongView {
  /** The visible window, in seconds. Clamped to the song on the way in. */
  view: { t0: number; t1: number } | null
  hiddenSources: string[]
  collapsedFamilies: string[]
  focusSource: string | null
}

export interface PersistedState {
  v: number
  /** Which song was open. */
  selectedId: string | null
  /** Inspector tab. */
  tab: string
  /**
   * Whether the library is open — `undefined` when the width has not been
   * overruled.
   *
   * The absence is the point. Whether the library is open is a function of how
   * much room there is: at 1240px and below it is in the way, above it there is
   * space for it. That is a default derived from a measurement, and it changes
   * when the measurement changes.
   *
   * It used to be persisted unconditionally, with `true` as the fallback for a
   * missing value, so the width rule's own output was stored as though the user
   * had chosen it. The media-query listener that re-applies the rule only fires
   * on a *change*, so the sequence was: open the app once in a narrow window,
   * have the sidebar close for that reason, and then never see the library again
   * — including on a wide monitor, where there is room for it. A default that
   * gets written to storage stops being a default.
   *
   * So a value is stored only once the reader has actually pressed the button,
   * and an absent value means "ask the width".
   */
  showSidebar?: boolean
  showInspector: boolean
  /** Row toggles. */
  options: Record<string, boolean>
  /** Per-song timeline state, keyed by song id. */
  songs: Record<string, SongView>
}

/* ------------------------------------------------------------------ checks */

const isBool = (x: unknown): x is boolean => typeof x === 'boolean'
const isStr = (x: unknown): x is string => typeof x === 'string' && x.length < 512

function num(x: unknown): number | null {
  return typeof x === 'number' && Number.isFinite(x) ? x : null
}

function strList(x: unknown, max = 64): string[] {
  if (!Array.isArray(x)) return []
  // Bounded: this comes from storage a user can edit, and an array of a million
  // entries would be a slow loop on every load.
  return x.slice(0, max).filter(isStr)
}

function songView(x: unknown): SongView | null {
  if (!x || typeof x !== 'object') return null
  const o = x as Record<string, unknown>
  const v = o.view
  let view: SongView['view'] = null
  if (v && typeof v === 'object') {
    const t0 = num((v as Record<string, unknown>).t0)
    const t1 = num((v as Record<string, unknown>).t1)
    // A window that is not a window is not a window. t1 <= t0 would make every
    // downstream span negative, and `clampView` is not reached because the
    // arithmetic happens before it.
    if (t0 !== null && t1 !== null && t1 > t0) view = { t0, t1 }
  }
  return {
    view,
    hiddenSources: strList(o.hiddenSources),
    collapsedFamilies: strList(o.collapsedFamilies),
    focusSource: isStr(o.focusSource) ? o.focusSource : null,
  }
}

/**
 * Read the stored state, or null if there is nothing usable.
 *
 * Never throws. Storage can be disabled, full, or hold something another script
 * wrote; none of those should stop the app from opening.
 */
export function loadState(): PersistedState | null {
  let raw: string | null = null
  try {
    raw = localStorage.getItem(KEY)
  } catch {
    return null // private mode, disabled storage, or a sandboxed frame
  }
  if (!raw) return null
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    // A half-written entry, or hand-edited. Start clean rather than complain:
    // the user cannot act on "your saved view is corrupt".
    try { localStorage.removeItem(KEY) } catch { /* nothing to do */ }
    return null
  }
  if (!parsed || typeof parsed !== 'object') return null
  const o = parsed as Record<string, unknown>
  // A version we do not know is a shape we do not know. Discard whole: guessing
  // at a field layout is how a stored t0 ends up read as a string.
  if (o.v !== VERSION) return null

  const songs: Record<string, SongView> = {}
  if (o.songs && typeof o.songs === 'object') {
    for (const [k, v] of Object.entries(o.songs as Record<string, unknown>).slice(0, 200)) {
      if (!isStr(k)) continue
      const sv = songView(v)
      if (sv) songs[k] = sv
    }
  }

  const options: Record<string, boolean> = {}
  if (o.options && typeof o.options === 'object') {
    for (const [k, v] of Object.entries(o.options as Record<string, unknown>).slice(0, 40)) {
      if (isStr(k) && isBool(v)) options[k] = v
    }
  }

  return {
    v: VERSION,
    selectedId: isStr(o.selectedId) ? o.selectedId : null,
    tab: isStr(o.tab) ? o.tab : 'overview',
    showSidebar: isBool(o.showSidebar) ? o.showSidebar : undefined,
    showInspector: isBool(o.showInspector) ? o.showInspector : true,
    options,
    songs,
  }
}

export function saveState(state: Omit<PersistedState, 'v'>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...state, v: VERSION }))
  } catch {
    // Quota, or storage disabled. Losing the saved view is a smaller problem
    // than an exception on every keystroke of a zoom.
  }
}

/** Drop everything. Used by the "start fresh" affordance and by tests. */
export function clearState(): void {
  try { localStorage.removeItem(KEY) } catch { /* nothing to do */ }
}

/**
 * The per-song record for a song, defaulted.
 *
 * `view` is deliberately left null when nothing was stored: `useZoomView` has
 * its own cold-load default, and passing `null` lets it apply that rather than
 * duplicating the number here in two places.
 */
export function songViewFor(
  state: PersistedState | null, songId: string | null,
): SongView {
  const empty: SongView = {
    view: null, hiddenSources: [], collapsedFamilies: [], focusSource: null,
  }
  if (!state || !songId) return empty
  return state.songs[songId] ?? empty
}
