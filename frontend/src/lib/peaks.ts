/**
 * Shape of the peak payload returned by /api/songs/{id}/peaks.
 *
 * Each column is `[min, max, mean]`. The mean is the loudness envelope and the
 * min/max pair is the bound; at whole-song zoom the bound saturates — the
 * maximum of a quarter second of a mastered track is near full scale almost
 * everywhere — so the mean is the only one of the three that still describes
 * the music. The third element is optional so a payload from an older server
 * still renders, with the bound standing in for the envelope.
 */
export interface PeaksData {
  t0: number
  t1: number
  width: number
  duration: number
  source?: string
  peaks: [number, number, number?][]
}

/**
 * One separated stem's loudness shape over a window, ready to draw.
 *
 * The canvas draws a stem envelope in every visible lane on every frame, so the
 * smoothing and scaling are done here once per fetch rather than per frame per
 * lane. Six lanes at 60 fps put a nine-wide window on every column of every
 * lane sixty times a second, which is the whole cost of the row for no change in
 * the picture.
 */
export interface StemWave {
  t0: number
  t1: number
  /** One value per peak column, 0..1, where 1 is the window's 98th percentile. */
  env: Float32Array
}

/**
 * Reduce a peaks payload to a 0..1 envelope, one value per column.
 *
 * Two decisions worth stating, both of them measured on real data:
 *
 * The scale is the window's own 98th percentile, not the maximum and not 1.0. A
 * separated stem tops out well below full scale — "DNA."'s vocals sit at an rms
 * of 0.06 where the mix reaches 0.61 — so drawn against 1.0 the lane is a flat
 * smear along the centre line and a reader concludes the stem is empty. A
 * percentile rather than a maximum, so one transient does not flatten the rest.
 *
 * The smoothing window widens with the zoom, to a floor of one column. At whole
 * song a column is a sixth of a second and the rms still swings hard inside it,
 * which draws vertical grass rather than a shape; when zoomed in, a column is a
 * few milliseconds and smoothing it would erase the transients that are the only
 * thing visible at that scale.
 *
 * **The filter is a median, not a mean.** This is the fix for a reported
 * misalignment, and the reason is that a mean is the wrong tool for the job it
 * was given. Grass *is* spikes: isolated columns far above their neighbours. A
 * mean does not remove a spike, it spreads it — every column within half the
 * window inherits part of it, so a single loud column paints a 9-column-wide
 * blob. Worse, a mean is symmetric, so a step is blurred symmetrically about its
 * true position: the envelope starts before the sound does and keeps going after
 * it stops.
 *
 * At the default whole-song zoom that is 9 columns of 216 ms, so **±864 ms**:
 * the lane's waveform began up to 0.86 s before the audio and continued up to
 * 0.86 s after it, which is what "the boxes end at the right place and the
 * waveform goes past them" was. A median removes the spikes, which is the
 * original requirement, *and* follows the majority of its neighbourhood, so an
 * edge is approached from the loud side and abandoned at the quiet side instead
 * of being smeared across both.
 *
 * A median cannot be done with a prefix sum, so this is O(n log w). At 900
 * columns with a window of at most 9 that is not a cost worth measuring; the
 * previous prefix-sum path is kept for the common case where no smoothing is
 * wanted at all.
 *
 * Returns the envelope *unnormalised*, with the scale it would have used. The
 * caller normalises, because the scale has to be shared across lanes: see
 * `scaleEnvelopes`.
 */
export function buildEnvelope(d: PeaksData): { env: Float32Array; p98: number } {
  const n = d.peaks.length
  const out = new Float32Array(n)
  if (!n) return { env: out, p98: 0 }

  const raw = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    const c = d.peaks[i]
    raw[i] = c.length >= 3
      ? Math.abs(c[2] ?? 0)
      : Math.max(Math.abs(c[0] ?? 0), Math.abs(c[1] ?? 0))
  }

  const perCol = (d.t1 - d.t0) / n
  const win = Math.max(1, Math.min(9, Math.round(perCol / 0.012)))
  if (win > 1) {
    const half = win >> 1
    const scratch = new Float32Array(2 * half + 1)
    for (let i = 0; i < n; i++) {
      const a = Math.max(0, i - half)
      const b = Math.min(n, i + half + 1)
      const m = b - a
      if (m === win) {
        for (let k = 0; k < m; k++) scratch[k] = raw[a + k]
      } else {
        // At the ends the window is short. Left alone, a short mean at the very
        // start of a track would be taken over fewer samples and read louder than
        // its neighbours, so the edge is held rather than computed.
        for (let k = 0; k < m; k++) scratch[k] = raw[b - 1 - k]
      }
      const slice = scratch.subarray(0, m)
      slice.sort()
      out[i] = m % 2 ? slice[(m - 1) >> 1] : (slice[m / 2 - 1] + slice[m / 2]) / 2
    }
  } else {
    out.set(raw)
  }

  const sorted = Float32Array.from(out).sort()
  const p98 = Math.max(1e-4, sorted[Math.min(n - 1, Math.floor(n * 0.98))])
  return { env: out, p98 }
}

/**
 * Put every lane on one scale, so lane heights can be compared.
 *
 * This is the correction to a mistake in the first version of the stem
 * waveforms, which normalised each lane by its own 98th percentile. Every lane
 * then filled its row whatever its actual level, which is the one thing a
 * waveform must not do: on "Taste" the piano stem peaks at -73 dB and is pure
 * separator bleed, and drawn against its own loudest moment it looked exactly as
 * substantial as the drums. The reader was invited to compare lanes that had been
 * scaled to be identical.
 *
 * The shared reference is the loudest stem on screen, so the *ratios* between
 * lanes are real even though the absolute reference is arbitrary. A lane that is
 * 20 dB down now draws a fifth as tall, which is the fact worth being able to
 * see. It also means a lane that genuinely is silent draws as a flat line, which
 * is the honest reading rather than a full-height row of nothing.
 */
export function scaleEnvelopes(rows: Record<string, { env: Float32Array; p98: number }>) {
  let ref = 0
  for (const k in rows) ref = Math.max(ref, rows[k].p98)
  // Every stem silent, or no stems at all. Dividing by this would be a division
  // by zero; a full-scale divisor leaves every envelope at zero, which is what
  // silence should look like.
  if (!(ref > 0)) ref = 1
  const out: Record<string, Float32Array> = {}
  for (const k in rows) {
    const src = rows[k].env
    const env = new Float32Array(src.length)
    for (let i = 0; i < src.length; i++) env[i] = Math.min(1, src[i] / ref)
    out[k] = env
  }
  return { ref, env: out }
}
