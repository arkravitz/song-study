/**
 * The form of a record: macro sections built from detected segments.
 *
 * One implementation, used by the timeline ribbon and the inspector. It was
 * tempting to write this twice, and that is exactly the failure mode this
 * project keeps hitting — two components computing the same grouping from the
 * same data, drifting apart, and one of them quietly showing something the other
 * does not. A boundary is not a section: when adjacent segments end up with the
 * same name they are one formal section with an internal change, and the section
 * list is what a producer reads as form.
 *
 * Every read tolerates a cached analysis written before the macro fields
 * existed, in which case each segment is its own macro — a worse answer than the
 * current one, but a song rather than a blank panel.
 */
import type { Section, SongSummary } from '../types'
import { titleCase } from './format'

export interface MacroSection {
  id: string
  /** The numbered name a producer would use: "Chorus 2". */
  label: string
  userNamed: boolean
  /** The bare name, for styling: "chorus". */
  role: string
  start: number
  end: number
  /** The detected segments inside it, in time order. */
  parts: Section[]
  /** Union of the parts playing across the whole macro, loudest first. */
  sources: string[]
  /** Largest perceived-energy step between this macro and the one before. */
  energy: number
}

/**
 * Section roles get a semantic colour, not a categorical one.
 *
 * Six arbitrary hues would compete with the six instrument colours for
 * attention and neither would mean anything. These read as a scale — quiet
 * through to full — so the shape of a chorus is visible before the word is read,
 * and they are the only saturated thing in the ribbon.
 */
export const SECTION_ROLE_CLASS: Record<string, string> = {
  intro: 'role-intro',
  verse: 'role-verse',
  prechorus: 'role-prechorus',
  postchorus: 'role-postchorus',
  chorus: 'role-chorus',
  bridge: 'role-bridge',
  breakdown: 'role-breakdown',
  instrumental: 'role-instrumental',
  solo: 'role-solo',
  interlude: 'role-interlude',
  drop: 'role-drop',
  tag: 'role-tag',
  outro: 'role-outro',
  fade: 'role-outro',
  unknown: 'role-unknown',
}

export function macroSections(song: Pick<SongSummary, 'sections'>): MacroSection[] {
  const byMacro = new Map<string, Section[]>()
  for (const s of song.sections ?? []) {
    const key = s.macro_id || s.id
    const arr = byMacro.get(key)
    if (arr) arr.push(s)
    else byMacro.set(key, [s])
  }

  const out: MacroSection[] = []
  for (const [id, parts] of byMacro) {
    parts.sort((a, b) => a.start - b.start)
    const first = parts[0]
    const last = parts[parts.length - 1]
    const listenerName = first.user_label || (first.label_source === 'user' ? first.macro_label : null)
    const raw = (listenerName || first.macro_label || first.label || 'unknown').toLowerCase()
    const role = raw.split(' ')[0].replace(/-/g, '')

    // A source counts as "in this section" if it plays for a meaningful part of
    // it. Union-with-anything would put a one-bar fill on the same line as the
    // bass, and the whole point of the list is to show what a section is made of.
    const span = Math.max(0.01, last.end - first.start)
    const weight = new Map<string, number>()
    for (const p of parts) {
      for (const sid of p.source_ids ?? []) {
        weight.set(sid, (weight.get(sid) ?? 0) + (p.end - p.start) / span)
      }
    }
    const sources = [...weight.entries()]
      .filter(([, w]) => w >= 0.25)
      .sort((a, b) => b[1] - a[1])
      .map(([sid]) => sid)

    out.push({
      id,
      label: listenerName || titleCase(first.macro_label || first.label || 'unknown'),
      userNamed: Boolean(listenerName),
      role: SECTION_ROLE_CLASS[role] ? role : 'unknown',
      start: first.start,
      end: last.end,
      parts,
      sources,
      energy: first.energy_perceived ?? 0,
    })
  }
  out.sort((a, b) => a.start - b.start)
  // An unlabeled edge of the record still has a human place in the form.
  // "Unknown" is a classifier result, not a useful section name.
  if (out[0]?.role === 'unknown' && !out[0].userNamed) out[0].label = 'Opening'
  if (out.length > 1 && out[out.length - 1].role === 'unknown' && !out[out.length - 1].userNamed) out[out.length - 1].label = 'Ending'
  for (const m of out) if (m.role === 'unknown' && m.label === 'Unknown') m.label = 'Passage'
  // Cached macro labels may repeat their ordinal (for example "Chorus 1"
  // appears three times in Taste). Number occurrences in the song's order,
  // including listener-named occurrences in the count. Otherwise splitting
  // Verse 1 into a listener-named Verse 1 and another passage would silently
  // strip the number from the later generated Verse 2.
  const totals = new Map<string, number>()
  for (const m of out) {
    const key = m.role === 'unknown' ? m.label : m.role
    totals.set(key, (totals.get(key) ?? 0) + 1)
  }
  const seen = new Map<string, number>()
  for (const m of out) {
    const key = m.role === 'unknown' ? m.label : m.role
    const count = (seen.get(key) ?? 0) + 1
    seen.set(key, count)
    if (m.userNamed) continue
    const base = m.label.replace(/\s+\d+$/, '')
    m.label = (totals.get(key) ?? 0) > 1 ? `${base} ${count}` : base
  }
  return out
}

/**
 * How wide a section band has to be before its name is readable, in pixels per
 * character at the band's 11px label.
 */
export const LABEL_PX_PER_CHAR = 7.2

/**
 * The band's own horizontal padding, which is not available for the label.
 *
 * This is the whole of B-097. The band is styled `padding: 0 7px` and the band
 * sets `overflow: hidden`, so the width the band is given is 14px wider than the
 * space the text has. `labelForWidth` was handed the band's width and so
 * believed a 35px band could hold a 26px word, when it could hold 21px — the
 * browser then clipped the word mid-glyph and the intro of "DNA." rendered as
 *
 *     Intrc
 *
 * which reads as a typo rather than as an abbreviation. A *sliced* word at least
 * looks deliberate; a clipped one does not.
 */
export const LABEL_PADDING_PX = 16

/**
 * The name to draw in a band `px` wide, or `''` when the whole name will not fit.
 *
 * There is deliberately no partial answer any more. The tight branch used to
 * return `label.slice(0, ...)`; the comment above it claimed that was an
 * abbreviation, and the same comment records that an *earlier* unconditional
 * `slice(0, 4)` produced "intr vers chor" across the one row that carries the
 * form. Truncating a section name is not a label, it is a different word, and a
 * band that cannot hold its name should show none — the name is in the tooltip
 * and in the inspector, and an empty band is honest where "Intrc" is not.
 */
export function labelForWidth(label: string, px: number): string {
  const available = Math.max(0, px - LABEL_PADDING_PX)
  return available >= label.length * LABEL_PX_PER_CHAR ? label : ''
}
