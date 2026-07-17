/** Formatting and musical helpers. */

export function fmtTime(t: number, ms = true): string {
  if (!isFinite(t) || t < 0) t = 0
  const m = Math.floor(t / 60)
  const s = t - m * 60
  return ms
    ? `${m}:${s < 10 ? '0' : ''}${s.toFixed(1)}`
    : `${m}:${String(Math.floor(s)).padStart(2, '0')}`
}

export function fmtClock(t: number): string {
  const m = Math.floor(Math.max(0, t) / 60)
  const s = Math.floor(Math.max(0, t) % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

export function fmtDb(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  return `${v.toFixed(digits)} dB`
}

export function fmtHz(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  return v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : `${Math.round(v)}`
}

/** "bar 12.3" for a time, given tempo and meter. */
export function fmtBar(t: number, bpm: number, meter = '4/4', offset = 0): string {
  if (!bpm || bpm <= 0) return '—'
  const bpb = beatsPerBar(meter)
  const beats = (t - offset) / (60 / bpm)
  // Derive the beat from the bar rather than a second modulo, so there is no
  // second ternary that can disagree with the first.
  //
  // Bars are counted from 1, and the result is clamped to that. It used to be
  // `floor(beats / bpb) + 1` with no floor on the value, so any time before the
  // first downbeat produced "bar 0.x" — a bar that does not exist. On a clean
  // load of "Taste" the transport read **bar 0.4** at 0:00.0, which is the
  // first thing a producer sees and means nothing to them.
  //
  // A track whose first downbeat is not at 0.0 is normal; it is a pickup, or the
  // first bar simply starts slightly before the audio does. Either way the
  // honest reading at t=0 is the top of bar 1, and the clamp is what the
  // arithmetic was always trying to say.
  const bar = Math.max(1, Math.floor(beats / bpb) + 1)
  // Clamped at both ends, because both ends are 1-indexed. Clamping only the bar
  // traded "bar 0.4" for "bar 1.0", which is the same mistake one field over.
  const beat = Math.max(1, Math.min(bpb, Math.floor(beats - (bar - 1) * bpb) + 1))
  return `bar ${bar}.${beat}`
}

export function beatsPerBar(meter = '4/4'): number {
  try {
    const [n, d] = meter.split('/')
    return Math.max(1, (Number(n) * 4) / Number(d))
  } catch {
    return 4
  }
}

/** Graceful: mixes like 7/8 must not produce a non-integer bar length. */
export function fmtBarOnly(t: number, bpm: number, meter = '4/4', offset = 0): number {
  if (!bpm || bpm <= 0) return 0
  const bpb = beatsPerBar(meter)
  return Math.floor((t - offset) / ((60 / bpm) * bpb)) + 1
}

export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

export function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t
}

/** Smooth 0..1 -> ramp, for confidence display. */
export function confColor(c: number): string {
  if (c >= 0.75) return 'var(--ok)'
  if (c >= 0.45) return 'var(--warn)'
  return 'var(--bad)'
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !isFinite(v)) return '—'
  return `${(v * 100).toFixed(digits)}%`
}

/**
 * `verse 2` -> `Verse 2`, `pre_chorus` -> `Pre Chorus`.
 *
 * The backend stores lowercase enum values because that is what compares and
 * serialises cleanly. The interface has to speak the other dialect: a section
 * ribbon reading "pre_chorus" is showing the schema rather than the music.
 */
export function titleCase(s: string): string {
  if (!s) return s
  return s.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Family ordering for the source lanes: drums first reads like a session. */
export const FAMILY_ORDER: Record<string, number> = {
  drums: 0, bass: 1, vocals: 2, harmony: 3, lead: 4, pad: 5,
  orchestra: 6, fx: 7, ambience: 8, unknown: 9,
}

export const FAMILY_LABEL: Record<string, string> = {
  drums: 'Drums', bass: 'Bass', vocals: 'Vocals', harmony: 'Harmony',
  lead: 'Lead', pad: 'Pads', orchestra: 'Orchestra', fx: 'FX',
  ambience: 'Ambience', unknown: 'Other',
}

export function panLabel(pan: number): string {
  if (Math.abs(pan) < 0.06) return 'centre'
  const side = pan < 0 ? 'L' : 'R'
  const amt = Math.round(Math.abs(pan) * 100)
  return `${amt}% ${side}`
}

/** Colour for an event kind, used consistently across the UI. */
export const EVENT_COLORS: Record<string, string> = {
  enter: '#4ade80',
  restart: '#86efac',
  exit: '#f87171',
  level_change: '#fbbf24',
  filter_change: '#c084fc',
  width_change: '#38bdf8',
  density_change: '#fb923c',
  texture_change: '#a78bfa',
  harmony_change: '#f0abfc',
  rhythm_change: '#facc15',
  fill: '#fbbf24',
  riser: '#f472b6',
  impact: '#fb7185',
  downlifter: '#a3e635',
  silence: '#94a3b8',
  resume: '#4ade80',
  pattern_shift: '#2dd4bf',
  vocal_doubling: '#f9a8d4',
  space_change: '#67e8f9',
  unknown: '#94a3b8',
}

/**
 * Plain-language names for the event kinds.
 *
 * The kinds are identified by colour on the timeline and nowhere else, which
 * makes the colour the sole carrier of the meaning — and leaves "what is the
 * yellow bar" unanswerable. Anything rendering a kind should name it.
 */
export const EVENT_KIND_LABEL: Record<string, string> = {
  enter: 'enters',
  exit: 'leaves',
  restart: 'restart',
  level_change: 'level change',
  width_change: 'stereo width change',
  density_change: 'rhythm density change',
  space_change: 'stereo space change',
  downlifter: 'downlifter',
  vocal_doubling: 'vocal doubling',
  fill: 'fill',
  riser: 'riser',
  impact: 'impact',
  silence: 'silence',
  resume: 'resume',
  pattern_shift: 'pattern shift',
  unknown: 'change',
}

export function eventKindLabel(kind: string): string {
  return EVENT_KIND_LABEL[kind] ?? kind.replace(/_/g, ' ')
}

/**
 * Kinds that describe a thing happening, where the label already says what.
 * Not a measurement, so there is no second question for them to answer.
 */
const OCCURRENCE_KINDS = new Set(['enter', 'exit', 'restart'])

/**
 * The kind name, but only when it says something the label does not.
 *
 * The events list printed `label · kind`, and for the commonest events the two
 * halves are the same words: "Vocals enters · enters", "Bass drops out · leaves",
 * "Guitar returns · restart". On 115 rows that is 115 restatements, and it
 * teaches the reader that the second column is noise — which then hides the
 * cases where it genuinely helps.
 *
 * The kind earns its place by naming the *measurement* that moved, which the
 * label never does: "Stereo image narrows · stereo width change" and
 * "Arrangement thickens · rhythm density change" both say something extra, and
 * the timeline's pins are coloured by exactly that kind.
 *
 * So: if every word of the kind already appears in the label, there is nothing
 * left to say and the half is dropped. Compared on words, not on substrings, so
 * "enter" inside "enters" counts and "leaves" is recognised inside
 * "drops out · leaves".
 */
export function eventKindQualifier(label: string, kind: string): string | null {
  // These three name the occurrence, and the label already names it in the
  // reader's words: "Bass drops out" is `exit`, "Guitar returns" is `restart`.
  // No word-overlap test catches that — "drops out" and "leaves" share no words
  // — so it is stated here rather than left to a heuristic that would pass
  // "Vocals enters" and miss "Bass drops out", which is worse than not trying.
  if (OCCURRENCE_KINDS.has(kind)) return null
  const k = eventKindLabel(kind)
  const labelWords = new Set(
    label.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean).map((w) => singular(w)),
  )
  const kindWords = k.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean)
  if (!kindWords.length) return null
  const allPresent = kindWords.every((w) => labelWords.has(singular(w)))
  return allPresent ? null : k
}

/** Crude but sufficient: strip a trailing plural so "enters" == "enter". */
function singular(w: string): string {
  if (w.length > 3 && w.endsWith('ies')) return w.slice(0, -3) + 'y'
  if (w.length > 3 && w.endsWith('es')) return w.slice(0, -2)
  if (w.length > 3 && w.endsWith('s')) return w.slice(0, -1)
  return w
}

/**
 * Plain-language names for the energy-model components.
 *
 * `active_layers` is an accurate description of a measurement and useless as a
 * label on a marker the size of a triangle. These name what the component does
 * to the mix, which is the question a producer is actually asking.
 */
export const COMPONENT_LABEL: Record<string, string> = {
  loudness: 'level',
  onset_density: 'rhythm',
  active_layers: 'layers',
  percussive_energy: 'percussion',
  bass_activity: 'bass',
  high_activity: 'high end',
  band_spread: 'spread',
  hp_ratio: 'dark/bright',
  stereo_width: 'width',
  vibrato: 'vibrato',
  chord_change_rate: 'harmony',
  register: 'register',
}

export function componentLabel(name: string): string {
  return COMPONENT_LABEL[name] ?? name.replace(/_/g, ' ')
}

export function groundingColor(g: string): string {
  if (g === 'verified') return 'var(--ok)'
  if (g === 'partially_grounded') return 'var(--warn)'
  return 'var(--bad)'
}

export function groundingLabel(g: string): string {
  if (g === 'verified') return 'grounded'
  if (g === 'partially_grounded') return 'partly grounded'
  return 'ungrounded'
}

/**
 * Format a number for display without ever throwing.
 *
 * A render-time throw unmounts the whole React tree, not just the offending
 * panel, so the user gets a blank page and no clue what happened. That is a far
 * worse failure than showing an em dash, and every field crossing the API
 * boundary is a potential null. One missing `strength` on an event took the
 * entire app down on the first click of an event pin.
 */
export function num(v: unknown, digits = 2): string {
  if (v === null || v === undefined) return '—'
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isFinite(n)) return '—'
  return n.toFixed(digits)
}

/** Format a number with a unit suffix, tolerating null. */
export function numUnit(v: unknown, unit: string, digits = 1): string {
  const s = num(v, digits)
  return s === '—' ? s : `${s}${unit}`
}
