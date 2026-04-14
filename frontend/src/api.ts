/** Typed API client. */

import type {
  ComparisonInsight, GroundingSummary, HealthInfo, Insight, JobInfo,
  SongListItem, SongSummary,
} from './types'
import type { PeaksData } from './lib/peaks'

export interface SectionEditSnapshot {
  sections: SongSummary['sections']; transitions: unknown[]; warnings: string[]; revision: string
}

const BASE = ''

/**
 * The shape of one windowed curve read.
 *
 * It admits `error` because the endpoint returns one with a 200 when the name
 * does not exist, alongside the list of names that do. It used to be typed as
 * always-a-series, so the frontend could not tell a bad name from an empty
 * window — which is how the stereo-width row sat blank for a long time with
 * nothing anywhere saying the name was wrong.
 */
type CurveResponse = {
  name: string; frame_hz: number; count: number; min: number; max: number
  mean: number; points: number[]; times: number[]
} & ({ error?: undefined } | { error: string; available?: string[] })

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* keep the status line */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => json<HealthInfo>('/api/health'),
  models: () => json<{ models: Record<string, unknown>[] }>('/api/models'),
  songs: () => json<{ songs: SongListItem[] }>('/api/songs'),
  song: (id: string) => json<SongSummary>(`/api/songs/${id}`),
  sectionState: (id: string) => json<SectionEditSnapshot>(`/api/songs/${id}/sections/state`),
  restoreSections: (id: string, state: SectionEditSnapshot, expectedRevision: string) =>
    json<SectionEditSnapshot>(`/api/songs/${id}/sections/state`, {
      method: 'PUT', body: JSON.stringify({ state, expected_revision: expectedRevision }),
    }),
  splitSection: (id: string, macroId: string, at: number,
                 beforeName?: string, afterName?: string) =>
    json<{ section_id: string; at: number; macro_sections: number }>(
      `/api/songs/${id}/sections/split`, {
        method: 'POST', body: JSON.stringify({ macro_id: macroId, at,
                                             before_name: beforeName,
                                             after_name: afterName }),
      }),
  moveSectionBoundary: (id: string, rightMacroId: string, at: number) =>
    json<{ at: number }>(`/api/songs/${id}/sections/boundary`, {
      method: 'POST', body: JSON.stringify({ right_macro_id: rightMacroId, at }),
    }),
  mergeSections: (id: string, rightMacroId: string) =>
    json<{ macro_id: string }>(`/api/songs/${id}/sections/merge`, {
      method: 'POST', body: JSON.stringify({ right_macro_id: rightMacroId }),
    }),
  deleteSong: (id: string) => json<{ deleted: boolean }>(`/api/songs/${id}`, { method: 'DELETE' }),

  peaks: (id: string, t0: number, t1: number, width: number, source?: string) =>
    json<PeaksData>(
      `/api/songs/${id}/peaks?t0=${t0}&t1=${t1}&width=${width}`
      + (source ? `&source=${encodeURIComponent(source)}` : '')),

  /**
   * Every stem's envelope in one request.
   *
   * The lanes need all of them at once, and asking one at a time cost six round
   * trips per window — which is most of the latency of a zoom, because the
   * payloads are small and the per-request overhead is not. Batched the same way
   * the curve endpoint already batches its names, and for the same reason: one
   * window, one trip.
   *
   * A stem that is missing is reported in `errors` rather than failing the batch,
   * so one absent file still leaves the other five lanes drawn. That was the
   * behaviour of the six-request version and it is kept deliberately.
   */
  stemPeaks: (id: string, sources: string[], t0: number, t1: number, width: number) =>
    json<{ series: Record<string, PeaksData>; errors: Record<string, string> }>(
      `/api/songs/${id}/peaks?t0=${t0}&t1=${t1}&width=${width}`
      + `&sources=${encodeURIComponent(sources.join(','))}`),
  /**
   * A windowed curve read.
   *
   * The type admits `error` because the endpoint returns one with a 200 when the
   * name does not exist, alongside the list of names that do. It used to be typed
   * as always-a-series, so the frontend could not tell a bad name from an empty
   * window — which is how the stereo-width row sat blank for a long time with
   * nothing anywhere saying the name was wrong.
   */
  curve: (id: string, name: string, t0: number, t1: number, points = 1400) =>
    json<CurveResponse>(
      `/api/songs/${id}/curve?name=${encodeURIComponent(name)}&t0=${t0}&t1=${t1}&points=${points}`),

  /**
   * Several curves in one request, for the ones always wanted together.
   *
   * The stereo width row is three curves across a window that changes on every
   * zoom. Fetched one at a time they can land on different windows, and a row
   * showing a low band from the previous zoom beside a high band from this one
   * describes no moment at all.
   */
  curves: (id: string, names: string[], t0: number, t1: number, points = 1400) =>
    json<{ series: Record<string, CurveResponse> }>(
      `/api/songs/${id}/curve?names=${encodeURIComponent(names.join(','))}`
      + `&t0=${t0}&t1=${t1}&points=${points}`),

  library: (root: string, limit = 300) =>
    json<{ root: string; files: { path: string; name: string; size: number }[] }>(
      `/api/library?root=${encodeURIComponent(root)}&limit=${limit}`),

  analyse: (path: string, quality?: string) =>
    json<{ job: JobInfo; song_id: string; resumed: boolean }>('/api/analyse', {
      method: 'POST', body: JSON.stringify({ path, quality }),
    }),
  jobs: () => json<{ jobs: JobInfo[] }>('/api/jobs'),
  job: (id: string) => json<JobInfo>(`/api/jobs/${id}`),
  cancelJob: (id: string) => json<{ cancelled: boolean }>(`/api/jobs/${id}/cancel`, { method: 'POST' }),

  insight: (id: string, deep = false) =>
    json<{ insight: Insight; grounding: GroundingSummary }>(
      `/api/songs/${id}/insight?deep=${deep}`, { method: 'POST' }),
  insightWindow: (id: string, t0: number, t1: number, deep = false) =>
    json<{ insight: Insight; grounding: GroundingSummary }>(
      `/api/songs/${id}/insight/window`, {
        method: 'POST', body: JSON.stringify({ t0, t1, deep }),
      }),
  insightEvent: (id: string, eventId: string, deep = false) =>
    json<{ insight: Insight; grounding: GroundingSummary }>(
      `/api/songs/${id}/insight/event`, {
        method: 'POST', body: JSON.stringify({ event_id: eventId, deep }),
      }),
  insightEnergy: (id: string, index: number, deep = false) =>
    json<{ insight: Insight; grounding: GroundingSummary }>(
      `/api/songs/${id}/insight/energy`, {
        method: 'POST', body: JSON.stringify({ index, deep }),
      }),
  lessons: (id: string, deep = true) =>
    json<{ insight: Insight; grounding: GroundingSummary }>(
      `/api/songs/${id}/lessons?deep=${deep}`, { method: 'POST' }),
  ask: (id: string, question: string, deep = false) =>
    json<{ insight: Insight; grounding: GroundingSummary }>(
      `/api/songs/${id}/ask`, { method: 'POST', body: JSON.stringify({ question, deep }) }),
  compare: (id: string, a0: number, a1: number, b0: number, b1: number,
            labelA = '', labelB = '', deep = false) =>
    json<{ comparison: ComparisonInsight; grounding: GroundingSummary }>(
      `/api/songs/${id}/compare`, {
        method: 'POST',
        body: JSON.stringify({ a0, a1, b0, b1, label_a: labelA, label_b: labelB, deep }),
      }),

  /** Ask the model to name the sections; returns the updated section list. */
  labelSections: async (id: string, deep = false) => {
    const r = await fetch(`${BASE}/api/songs/${id}/sections/label?deep=${deep}`,
      { method: 'POST' })
    if (!r.ok) {
      let detail = `${r.status}`
      try { detail = (await r.json())?.detail ?? detail } catch { /* keep */ }
      throw new Error(detail)
    }
    return r.json() as Promise<{
      insight: import('./types').Insight
      grounding: import('./types').GroundingSummary
      sections: {
        id: string; start: number; end: number; label: string
        confidence: number; source: string
        repeat_group: number | null; repeat_rank: number
        description: string | null
      }[]
    }>
  },

  audioUrl: (id: string) => `${BASE}/api/songs/${id}/audio`,
  originalUrl: (id: string) => `${BASE}/api/songs/${id}/audio?original=true`,
}

/** Upload a file with real progress, which fetch() cannot report. */
export function uploadFile(
  file: File,
  onProgress: (frac: number) => void,
): { promise: Promise<{ path: string; filename: string; size: number }>; abort: () => void } {
  const xhr = new XMLHttpRequest()
  const promise = new Promise<{ path: string; filename: string; size: number }>(
    (resolve, reject) => {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total)
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText))
        else reject(new Error(`upload failed: ${xhr.status} ${xhr.responseText.slice(0, 200)}`))
      }
      xhr.onerror = () => reject(new Error('upload failed'))
      xhr.onabort = () => reject(new Error('upload cancelled'))
    },
  )
  const form = new FormData()
  form.append('file', file)
  xhr.open('POST', `${BASE}/api/upload`)
  xhr.send(form)
  return { promise, abort: () => xhr.abort() }
}
