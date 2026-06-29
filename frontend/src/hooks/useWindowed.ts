/**
 * Fetch bookkeeping for anything that is read over a window and can be switched
 * off.
 *
 * Why this exists
 * ---------------
 * Three hooks each kept their own "have I already fetched this?" key. Every one
 * of them cleared the data when the row was switched off and kept the key, so
 * switching a row back on found the key matching, skipped the fetch, and left
 * the row drawing its empty state — permanently, until the window moved.
 *
 * Measured on "DNA.", which has 130 events and onsets throughout: switching the
 * onset row off and on again produced **0 requests**; nudging the zoom produced
 * **1**. The row then printed *"no onsets detected in view"*, which is a flat
 * false statement about a track that is nothing but onsets. The mix waveform and
 * the three stereo width bands failed the same way, and the width band copy of
 * this bug was written an hour before it was found, by copying the pattern.
 *
 * So the invariant is stated once, here, where it cannot be half-remembered:
 *
 *   **The record of having the data and the data itself are discarded together.**
 *
 * A key that survives its data is a claim the code cannot back up, and every
 * consumer of it then draws an empty state it believes to be a measurement.
 *
 * `scope` versus `key`
 * --------------------
 * `scope` is the song. A change clears the data *immediately*, before the new
 * fetch resolves, so one song's curve is never shown under another song's title
 * — the failure the per-hook version guarded against with a hand-written song
 * ref, and which is the reason a scope exists separately from the key rather
 * than just being part of it.
 *
 * `key` is the rest of the request: the quantised window, the curve names, the
 * point count. A change refetches but *keeps* the old data on screen, which is
 * what stops the row flashing empty on every zoom step.
 *
 * `key === null` disables: data cleared, key forgotten.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export function useGeneration() {
  const ref = useRef(0)
  return {
    next: () => ++ref.current,
    isCurrent: (n: number) => n === ref.current,
    invalidate: () => { ref.current++ },
  }
}

export interface WindowedResult<T> {
  data: T
  error: string | null
  loading: boolean
  /** True until the first successful load for the current scope. */
  everLoaded: boolean
  /** Fetch again even though the key has not changed. */
  reload: () => void
}

export function useWindowed<T>(opts: {
  /** Song id, or null when there is no song. A change clears the data at once. */
  scope: string | null
  /** The rest of the request. `null` disables the fetch and clears the data. */
  key: string | null
  fetch: (key: string) => Promise<T>
  /** What to hold when disabled, before the first load, or after a failure. */
  fallback: T
  /**
   * Read a per-item failure out of an otherwise-successful response.
   *
   * A batch can partly succeed: three band curves where one is missing. That is
   * not a failure of the batch, and blanking the two that arrived because the
   * third did not would be worse than useless.
   */
  splitError?: (data: T) => string | null
  /**
   * Wait this long for the key to stop changing before fetching, ms.
   *
   * `0` — the default — fetches on the key change, which is right for a song
   * payload: there is exactly one key per song and nothing to coalesce.
   *
   * It exists for the media rows, whose key is the visible window. A zoom
   * gesture is a *stream* of keys — one per wheel event, and a trackpad flick
   * delivers several per frame — so fetching on each one means fetching a window
   * the user has already zoomed past. Measured on "hit it and quit it" after the
   * window ladder was added: 60 wheel events across 20 steps still produced
   * **51 requests in 8 windows**, because the gesture crossed 8 rungs and each
   * rung is a real fetch. Seven of those eight were for windows nobody ever
   * looked at.
   *
   * So the fetch waits for the gesture to settle and then asks for the window
   * that is actually on screen. The old data stays on screen meanwhile, which is
   * safe precisely because of the ladder: the fetched window is always at least
   * as wide as the view, so there is nothing to blank while waiting.
   */
  settleMs?: number
}): WindowedResult<T> {
  const { scope, key, fetch: run, fallback, splitError, settleMs = 0 } = opts
  const [data, setData] = useState<T>(fallback)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [everLoaded, setEverLoaded] = useState(false)

  const lastKey = useRef<string | null>(null)
  const lastScope = useRef<string | null>(null)
  const gen = useRef(0)
  // Bumped by `reload()`. Folded into the key rather than exposed as a ref so
  // that forcing a refetch goes through the same path as any other key change
  // and cannot leave the bookkeeping out of step.
  const [nonce, setNonce] = useState(0)
  const reload = useCallback(() => setNonce((n) => n + 1), [])
  // The fetcher closes over the current window and names, which change every
  // render. Held in a ref so the effect can depend on the key alone; an inline
  // arrow would be a new function each render and the effect would re-run every
  // render, which is the original bug in a different costume.
  const runRef = useRef(run)
  runRef.current = run
  const fallbackRef = useRef(fallback)
  fallbackRef.current = fallback
  const splitRef = useRef(splitError)
  splitRef.current = splitError

  const effectiveKey = key === null ? null : `${key}#${nonce}`

  useEffect(() => {
    const clear = () => {
      // Data and the record of having it go together. This is the whole point.
      setData(fallbackRef.current)
      setError(null)
      setEverLoaded(false)
    }

    if (scope !== lastScope.current) {
      lastScope.current = scope
      lastKey.current = null
      clear()
    }

    if (effectiveKey === null) {
      clear()
      lastKey.current = null
      gen.current++
      setLoading(false)
      return
    }

    if (lastKey.current === effectiveKey) return
    lastKey.current = effectiveKey

    // Claim the generation now, so a key change during the settle discards the
    // work rather than letting it land late. The fetch itself waits.
    const token = ++gen.current
    const mine = (n: number) => n === gen.current
    setLoading(true)

    let timer: number | null = null
    const go = () => {
      timer = null
      if (!mine(token)) return
      runRef.current(key!)
        .then((d) => {
          if (!mine(token)) return
          setData(d)
          // A batch that partly succeeded reports the part, not the whole.
          setError(splitRef.current ? splitRef.current(d) : null)
          setEverLoaded(true)
        })
        .catch((e) => {
          if (!mine(token)) return
          setData(fallbackRef.current)
          setError(String(e?.message ?? e))
          setEverLoaded(false)
        })
        .finally(() => { if (mine(token)) setLoading(false) })
    }
    if (settleMs > 0) timer = window.setTimeout(go, settleMs)
    else go()

    return () => { if (timer !== null) window.clearTimeout(timer) }
  }, [scope, effectiveKey, key, settleMs])

  return { data, error, loading, everLoaded, reload }
}
