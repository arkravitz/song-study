/**
 * Data fetching: songs, the selected song, and windowed curve/peak loads.
 *
 * Every fetch here follows the same rule, because getting it wrong is a
 * correctness bug rather than a cosmetic one: **a response may only be applied
 * if it is still the response we asked for.**
 *
 * The failure this prevents was real. `useSong` fired a request for song A,
 * the user switched to song B, and if A's larger response happened to land
 * after B's smaller one then A's analysis was rendered under B's title — a
 * whole song's measurements, sections and commentary attributed to the wrong
 * track. `usePeaks` and `useCurve` had the mirror-image bug: they never cleared
 * on a song change, so the previous song's waveform stayed on screen under the
 * new song's name until the new fetch resolved.
 *
 * The guard is a monotonically increasing generation counter, compared before
 * any state is written. An `AbortController` is not enough on its own: the
 * server may have already produced the response, and aborting only covers the
 * in-flight request, not the callback that is already scheduled.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { HealthInfo, JobInfo, SongListItem, SongSummary } from '../types'

// The monotonic token lives in `useWindowed`, beside the bookkeeping it belongs
// to. It used to be defined here and used by `useSong`, which keeps its own
// `alive` flag: that one guards a *mount*, where a token cannot help, because
// the question is "is this component still here" rather than "is this the newest
// request".
import { useWindowed } from './useWindowed'

export function useHealth() {
  const [health, setHealth] = useState<HealthInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refresh = useCallback(() => {
    api.health().then(setHealth).catch((e) => setError(String(e.message ?? e)))
  }, [])
  useEffect(() => { refresh() }, [refresh])
  return { health, error, refresh }
}

/**
 * The song list, and whether we could reach the server.
 *
 * This used to answer a failed request with `setSongs([])`, which made "the
 * server is not running" look exactly like "you have no songs". Observed by
 * stopping the backend and pressing refresh: the sidebar said
 *
 *     Library (0)
 *     Nothing analysed yet.
 *
 * while the topbar still read "DNA — Kendrick Lamar · 5 instruments" and the
 * timeline was still on screen. Four songs had become none, on screen, in
 * response to a server that was merely down — which reads as data loss and
 * invites the reader to add songs they already have.
 *
 * So a failure keeps the list it had and says it could not be refreshed. The
 * empty state and the unreachable state are different facts and are now
 * different states; the interface can tell them apart because one of them is
 * recorded.
 */
export function useSongList(refreshKey: number) {
  const [songs, setSongs] = useState<SongListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    setLoading(true)
    api.songs()
      .then((r) => {
        if (!alive) return
        setSongs(r.songs)
        setError(null)
      })
      .catch((e: Error) => {
        // The previous list stands. It may be stale, and saying so is the
        // interface's job, not this hook's.
        if (alive) setError(e.message || 'could not reach the server')
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [refreshKey])
  return { songs, loading, error }
}

export function useSong(songId: string | null) {
  // The same mechanism as the windowed rows, because it is the same shape of
  // problem and this file's own header describes the failure it exists to
  // prevent: a response from song A rendered under song B's title.
  //
  // The generation token stopped the *stale response* from being applied, but the
  // previously applied song A was still in state while B loaded, so the screen
  // briefly showed one song's sections, events and measurements under another's
  // name. `useWindowed` clears on a scope change, before the new fetch resolves,
  // which closes that window rather than only the response race.
  const { data: song, error, loading, reload } = useWindowed<SongSummary | null>({
    scope: songId,
    key: songId,
    fallback: null,
    fetch: (id) => api.song(id),
  })
  return { song, loading, error, reload }
}

export function useJob(jobId: string | null) {
  const [job, setJob] = useState<JobInfo | null>(null)
  const [log, setLog] = useState<{ stage: string; pct: number; msg: string }[]>([])

  useEffect(() => {
    if (!jobId) { setJob(null); setLog([]); return }
    let alive = true
    let es: EventSource | null = null
    let poll: number | null = null

    const apply = (j: JobInfo) => {
      if (!alive) return
      setJob(j)
      setLog((prev) => {
        const next = j.stages.map((s) => ({ stage: s.name, pct: s.progress, msg: s.message }))
        return next.length ? next : prev
      })
    }

    const startPolling = () => {
      if (poll !== null) return
      poll = window.setInterval(() => {
        api.job(jobId).then(apply).catch(() => {})
      }, 900)
    }

    try {
      es = new EventSource(`/api/jobs/${jobId}/stream`)
      es.onmessage = (e) => {
        try { apply(JSON.parse(e.data)) } catch { /* ignore */ }
      }
      es.onerror = () => { es?.close(); es = null; startPolling() }
    } catch {
      startPolling()
    }
    // belt and braces: poll anyway, slowly, in case SSE stalls
    startPolling()

    return () => {
      alive = false
      es?.close()
      if (poll !== null) window.clearInterval(poll)
    }
  }, [jobId])

  return { job, log }
}

/** Latest jobs, for the sidebar queue. */
export function useJobs(pollMs = 2500) {
  const [jobs, setJobs] = useState<JobInfo[]>([])
  useEffect(() => {
    let alive = true
    const tick = () => {
      api.jobs().then((r) => { if (alive) setJobs(r.jobs) }).catch(() => {})
    }
    tick()
    const id = window.setInterval(tick, pollMs)
    return () => { alive = false; window.clearInterval(id) }
  }, [pollMs])
  return jobs
}

// PeaksData is declared once, in lib/peaks, and re-exported here for the
// hook's callers. A second, near-identical declaration had drifted: it was
// missing `duration`, which the server does send, so the canvas could not
// accept the hook's type without a cast.
export type { PeaksData } from '../lib/peaks'
import type { PeaksData, StemWave } from '../lib/peaks'
import { buildEnvelope, scaleEnvelopes } from '../lib/peaks'

/**
 * Quantise a view window before using it as a fetch key.
 *
 * The playhead-follow effect nudges the view continuously while audio plays. If
 * the fetch key were exact, every one of those nudges would cancel and restart
 * four HTTP requests, and the server would serve a fresh request every frame
 * forever. Snapping the window to a small fraction of the span makes the fetch
 * happen only when the user has actually moved the view meaningfully; the
 * server resamples to the requested width anyway, so a slightly wider window is
 * indistinguishable.
 */
/**
 * Window steps, on a fixed ladder.
 *
 * The gaps are roughly 1.5-3x, which is the point: it is the ratio between
 * neighbouring rungs that decides how many zoom steps a rung survives.
 */
const WINDOW_STEPS = [
  0.5, 1, 2, 5, 10, 15, 30, 60, 120, 240, 600, 1800, 7200,
]

/**
 * The window to *fetch* for a view, which is not the view.
 *
 * This is the fix for zooming being slow, and the diagnosis is one line:
 *
 *     const step = Math.max(0.1, span / 40)
 *
 * The quantisation grid was derived from the span, so the grid *moved* every
 * time the span changed. Two views one zoom notch apart therefore landed on
 * different boundaries and always produced a different key, so every notch
 * refetched everything. Measured on "DNA." from the network log: 20 consecutive
 * wheel events produced 20 sets of 6 peaks requests and 20 curve requests --
 * about 200 requests to go from the whole song to four seconds, with the
 * waveform emptying and refilling at every step, which is what "really funky"
 * describes.
 *
 * So the window is chosen on a **fixed** ladder, and it is always *at least* as
 * wide as the view:
 *
 *   - the step is the smallest rung that is >= the view's span, so a window one
 *     step long is *wide enough* for the view. The drawing code returns nothing
 *     outside `[t0, t1]`, so covering the view is what stops the waveform
 *     blanking while loading.
 *   - the start is rounded down to a multiple of that step, so a small pan keeps
 *     the same window.
 *
 * Consequence: zooming from the whole 3:06 song down to half a second costs
 * about six fetches instead of twenty, and the nineteen steps in between cost
 * nothing and blank nothing. The extra data is bounded by the ladder: the worst
 * case is a view of 121 s fetching the 240 s rung, which for the peaks endpoint
 * is about 7 ms and a few hundred KB.
 *
 * **Wide enough is not the same as covering.** The first version of this rounded
 * the *centre* to a multiple of the step, and that was wrong in a way the
 * reasoning above did not anticipate: a 186 s view centres at 93 s, which rounds
 * to 0 on a 240 s grid, so the fetched window was **-120..120** and the entire
 * second half of the song was not requested at all. The waveform stopped at 64%
 * of the way across, and the stem lanes ran the full width beside it, so it read
 * as "the waveform doesn't load when you zoom out" rather than as an arithmetic
 * slip. The comment on that version said the window covered the view "by
 * construction". It did not, and it was checked by reading the comment.
 *
 * So coverage is now enforced rather than argued: the window is a full step long,
 * and if it does not contain the view it slides right until it does. Verified
 * against a grid of views rather than a worked example, because the failure only
 * appears when the span and the centre disagree about which rung to use.
 */
function quantiseWindow(t0: number, t1: number) {
  const span = Math.max(0.25, t1 - t0)
  const step = WINDOW_STEPS.find((r) => r >= span) ?? WINDOW_STEPS[WINDOW_STEPS.length - 1]
  let q0 = Math.floor(t0 / step) * step
  let q1 = q0 + step
  if (q1 < t1) {
    // Not covering. Slide rather than grow: growing would make the window depend
    // on the exact view, and every pixel of panning would then be a new key --
    // which is the thing this function exists to stop.
    q1 = t1
    q0 = q1 - step
  }
  return { q0, q1, step }
}

/**
 * One envelope per separated stem, for the lanes to draw under their highlights.
 *
 * This exists so a lane can show the audio it is making a claim about. A lane
 * that draws only detected spans asks to be believed: "no vocals here" and "the
 * detector missed the vocals" look identical, and on "DNA." the regions reported
 * as absent were measured *louder* than the regions reported as playing. Putting
 * the stem's own waveform under the highlight makes the claim checkable against
 * the audio without a second window or a second opinion.
 *
 * The stems are fetched in one parallel batch rather than one request per lane as
 * each lane scrolls into view, because a scroll of the source list would then
 * trigger a staggered re-fetch of the same six files.
 */
export function useStemWaves(
  songId: string | null, sources: string[], t0: number, t1: number, width: number,
  enabled: boolean,
) {
  const w = Math.min(4000, Math.max(200, Math.round(width / 50) * 50))
  const { q0, q1 } = quantiseWindow(t0, t1)
  // The key has to include the stem list: the same song, a different window and
  // the same ids must refetch, while the same ids in a different order must not.
  const sourcesKey = useMemo(() => [...sources].sort().join(','), [sources])
  const live = enabled && !!songId && !!sourcesKey && q1 > q0

  const { data } = useWindowed<Record<string, StemWave>>({
    scope: songId,
    key: live ? `${q0.toFixed(2)}|${q1.toFixed(2)}|${w}|${sourcesKey}` : null,
    fallback: EMPTY_WAVES,
    // A zoom gesture is a stream of keys, one per wheel event. Wait for it
    // to settle so the fetch is for the window that is actually on screen.
    settleMs: 120,
    fetch: (key) => {
      const [a, b, widthPx, names] = key.split('|')
      // One request for every lane. This was six, and the per-window cost of a
      // zoom was six round trips to move a few hundred kilobytes.
      return api
        .stemPeaks(songId!, names.split(','), Number(a), Number(b), Number(widthPx))
        // A stem with no file on disk, or one the server cannot read, must not
        // take the other five down with it. The lane simply keeps its
        // highlight, which is what it showed before this existed.
        .catch(() => ({
          series: {} as Record<string, PeaksData>,
          errors: {} as Record<string, string>,
        }))
        .then(({ series }) => {
        // Built first, scaled second: the scale has to be shared by every lane,
        // so it cannot be decided while each one is still arriving on its own.
        const built: Record<string, { env: Float32Array; p98: number }> = {}
        const windows: Record<string, { t0: number; t1: number }> = {}
        for (const r in series) {
          built[r] = buildEnvelope(series[r])
          windows[r] = { t0: series[r].t0, t1: series[r].t1 }
        }
        const scaled = scaleEnvelopes(built)
        const out: Record<string, StemWave> = {}
        for (const k in scaled.env) out[k] = { ...windows[k], env: scaled.env[k] }
        return out
      })
    },
  })
  return data
}

/** A stable empty object, so the fallback identity never changes. */
const EMPTY_WAVES: Record<string, StemWave> = {}

export function usePeaks(
  songId: string | null, t0: number, t1: number, width: number, enabled: boolean,
) {
  const w = Math.min(8000, Math.max(200, Math.round(width / 50) * 50))
  const { q0, q1 } = quantiseWindow(t0, t1)
  const { data } = useWindowed<PeaksData | null>({
    scope: songId,
    key: enabled && songId && q1 > q0 ? `${q0.toFixed(2)}|${q1.toFixed(2)}|${w}` : null,
    fallback: null,
    // A zoom gesture is a stream of keys, one per wheel event. Wait for it
    // to settle so the fetch is for the window that is actually on screen.
    settleMs: 120,
    fetch: (key) => {
      const [a, b, widthPx] = key.split('|')
      return api.peaks(songId!, Number(a), Number(b), Number(widthPx))
    },
  })
  return data
}

export interface CurveSeries {
  name: string
  frame_hz: number
  points: number[]
  times: number[]
  min: number
  max: number
  mean: number
  /** Window actually read, after the server clamped it to the song. */
  t0?: number
  t1?: number
}

export function useCurve(
  songId: string | null, name: string | null, t0: number, t1: number,
  points = 1200, enabled = true,
) {
  const pts = Math.round(points / 100) * 100
  const { q0, q1 } = quantiseWindow(t0, t1)
  const live = enabled && !!name && !!songId && q1 > q0
  const { data, error, loading } = useWindowed<CurveSeries | null>({
    scope: songId,
    key: live ? `${name}|${q0.toFixed(2)}|${q1.toFixed(2)}|${pts}` : null,
    fallback: null,
    // A zoom gesture is a stream of keys, one per wheel event. Wait for it
    // to settle so the fetch is for the window that is actually on screen.
    settleMs: 120,
    fetch: (key) => {
      const [curveName, a, b, n] = key.split('|')
      return api.curve(songId!, curveName, Number(a), Number(b), Number(n))
        .then((r) => {
          // The server already answers a bad name with `{error, available}` and a
          // 200. Throwing that away is what made the stereo-width row come up
          // permanently empty with no explanation (B-005), and it is why "no
          // data", "still loading" and "this does not exist" were the same
          // picture. The reason is carried to the row, which prints it.
          if (r.error) throw new Error(r.error)
          return r as unknown as CurveSeries
        })
    },
  })
  return { series: data, loading, error }
}

/**
 * Several curves that are always wanted together, in one request.
 *
 * The stereo width row needs three. Fetched separately they would be three
 * chances for one to be a frame behind the others — the window changes on every
 * zoom, and a width row showing a low band from the previous zoom next to a high
 * band from this one is a measurement of no moment at all. One request cannot
 * interleave.
 *
 * A failure is per curve, not per batch: one missing band must not empty the
 * other two, because a partially-drawn row that names the missing band is
 * informative and a blank row is not.
 */
export function useCurves(
  songId: string | null, names: string[], t0: number, t1: number,
  points = 1200, enabled = true,
) {
  const pts = Math.round(points / 100) * 100
  const { q0, q1 } = quantiseWindow(t0, t1)
  const namesKey = useMemo(() => [...names].sort().join(','), [names])
  const live = enabled && !!songId && !!namesKey && q1 > q0

  const { data, error, loading } = useWindowed<CurveBatch>({
    scope: songId,
    key: live ? `${namesKey}|${q0.toFixed(2)}|${q1.toFixed(2)}|${pts}` : null,
    fallback: EMPTY_BATCH,
    // A zoom gesture is a stream of keys, one per wheel event. Wait for it
    // to settle so the fetch is for the window that is actually on screen.
    settleMs: 120,
    fetch: (key) => {
      const [want, a, b, n] = key.split('|')
      const list = want.split(',')
      return api.curves(songId!, list, Number(a), Number(b), Number(n))
        .then((payload): CurveBatch => {
          const got = payload.series ?? {}
          const ok: Record<string, CurveSeries> = {}
          const bad: Record<string, string> = {}
          for (const nm of list) {
            const r = got[nm]
            if (!r) { bad[nm] = 'not returned'; continue }
            if (r.error) { bad[nm] = r.error; continue }
            ok[nm] = r as unknown as CurveSeries
          }
          return { series: ok, errors: bad }
        })
    },
    // A batch that partly succeeded reports the part, not the whole: three band
    // curves where one is missing must not blank the two that arrived.
    splitError: (b) => (Object.keys(b.series).length ? null : Object.values(b.errors)[0] ?? null),
  })
  return { series: data.series, errors: data.errors, error, loading }
}

interface CurveBatch {
  series: Record<string, CurveSeries>
  errors: Record<string, string>
}

const EMPTY_BATCH: CurveBatch = { series: {}, errors: {} }
