import type { EnergyChange, SongSummary, Source } from '../types'
import type { MacroSection } from './sections'

export const sourceName = (s: Source) => s.id === 'other' ? 'Other sounds' : s.label
const pluralPart = (id: string) => id === 'drums' || id === 'vocals' || id === 'other'
export const names = (song: SongSummary, ids: string[]) => ids
  .map(id => song.sources.find(s => s.id === id)).filter((s): s is Source => !!s && !s.is_silent)
  .map(sourceName).join(', ')
const sentenceNames = (value: string) => value.toLowerCase().replace(/, ([^,]+)$/, ' and $1')

export function openingDescription(song: SongSummary, section: MacroSection) {
  const parts = names(song, section.sources)
  if (!parts) return 'The opening has no clearly detected instrument yet.'
  return `${parts.replace(/, ([^,]+)$/, ' and $1')} ${section.sources.length === 1 && !pluralPart(section.sources[0]) ? 'carries' : 'carry'} the opening passage.`
}

export function sectionValue(m: MacroSection, key: 'energy_perceived' | 'stereo_width' | 'density') {
  return m.parts.reduce((sum, p) => sum + p[key] * (p.end - p.start), 0) / Math.max(.01, m.end - m.start)
}

export function sectionChanges(song: SongSummary, from: MacroSection, to: MacroSection): string[] {
  const added = to.sources.filter(s => s !== 'other' && !from.sources.includes(s))
  const removed = from.sources.filter(s => s !== 'other' && !to.sources.includes(s))
  const lines: string[] = []
  if (names(song, added)) lines.push(`Separated audio suggests ${sentenceNames(names(song, added))} ${added.length === 1 && !pluralPart(added[0]) ? 'joins' : 'join'} the arrangement.`)
  if (names(song, removed)) lines.push(`Separated audio suggests ${sentenceNames(names(song, removed))} ${removed.length === 1 && !pluralPart(removed[0]) ? 'drops' : 'drop'} out of the main arrangement.`)
  const shifts = song.sources.filter(s => !s.is_silent && s.id !== 'other' && !added.includes(s.id) && !removed.includes(s.id))
    .map(s => ({s, difference: participation(s,to) - participation(s,from)}))
  const fuller = shifts.filter(v => v.difference > .3).sort((a,b) => b.difference - a.difference).slice(0,2)
  const thinner = shifts.filter(v => v.difference < -.3).sort((a,b) => a.difference - b.difference).slice(0,2)
  if (fuller.length) lines.push(`Separated audio suggests ${fuller.map(v => sourceName(v.s).toLowerCase()).join(' and ')} ${fuller.length === 1 && !pluralPart(fuller[0].s.id) ? 'plays' : 'play'} through more of the passage.`)
  if (thinner.length) lines.push(`Separated audio suggests ${thinner.map(v => sourceName(v.s).toLowerCase()).join(' and ')} ${thinner.length === 1 && !pluralPart(thinner[0].s.id) ? 'appears' : 'appear'} less often.`)
  if (!added.length && !removed.length && !fuller.length && !thinner.length) lines.push('The same main parts are detected in both passages.')
  const energy = sectionValue(to, 'energy_perceived') - sectionValue(from, 'energy_perceived')
  const width = sectionValue(to, 'stereo_width') - sectionValue(from, 'stereo_width')
  const measured = [Math.abs(energy) > .04
    ? `The recording feels ${energy > 0 ? 'more' : 'less'} energetic in ${to.label} (${Math.round(Math.abs(energy) * 100)} points on this song’s scale).`
    : 'The overall energy stays similar.']
  if (Math.abs(width) > .04) measured.push(`The mix ${width > 0 ? 'spreads wider' : 'draws inward'}.`)
  return [...measured, ...lines]
}

const contributorWords: Record<string, [string, string]> = {
  loudness: ['the recording gets louder', 'the recording gets quieter'],
  onset_density: ['attacks become more frequent', 'attacks become less frequent'],
  percussive_energy: ['transients get stronger', 'transients get softer'],
  bass_activity: ['low-frequency activity rises', 'low-frequency activity falls'],
  high_activity: ['the high end becomes more active', 'the high end becomes less active'],
  band_spread: ['more of the frequency range is active', 'less of the frequency range is active'],
  hp_ratio: ['percussive sounds become more prominent', 'sustained sounds become more prominent'],
  stereo_width: ['the stereo image widens', 'the stereo image narrows'],
}
const signalNames: Record<string, string> = {
  loudness: 'Recording level', onset_density: 'Frequency of attacks',
  percussive_energy: 'Transient strength', bass_activity: 'Low-frequency activity',
  high_activity: 'High-frequency activity', band_spread: 'Active frequency range',
  hp_ratio: 'Percussive sound relative to sustained sound', stereo_width: 'Stereo width',
  active_layers: 'Estimated number of active parts',
}
export function energySignals(c: EnergyChange) {
  return c.contributors.slice(0, 5).filter(([key]) => key in signalNames).map(([key, value]) => ({
    label: signalNames[key],
    movement: value >= 0 ? 'increases' : 'decreases',
    inferred: key === 'active_layers',
  }))
}
export function energyDescription(c: EnergyChange, song: SongSummary) {
  const changePartName = (s: Source) => s.id === 'other' ? 'another sound layer' : sourceName(s).toLowerCase()
  const changePartIsPlural = (s: Source) => s.id !== 'other' && pluralPart(s.id)
  const share = (s: Source, a: number, b: number) => s.segments.reduce((sum, r) => sum + Math.max(0, Math.min(r.end,b) - Math.max(r.start,a)), 0) / Math.max(.01,b-a)
  const before = Math.max(0, c.t - 4)
  const after = Math.min(song.duration, c.t + 4)
  // The catch-all stem is useful for listening but cannot identify an
  // instrument. Calling its entrance a new layer implies knowledge we lack.
  const exits = song.sources.filter(s => !s.is_silent && s.id !== 'other' && share(s,before,c.t) > .6 && share(s,c.t,after) < .2)
  const entrances = song.sources.filter(s => !s.is_silent && s.id !== 'other' && share(s,before,c.t) < .2 && share(s,c.t,after) > .6)
  const measured = c.contributors
    .filter(([key]) => key !== 'active_layers')
    .slice(0, 3)
    .map(([key, value]) => contributorWords[key]?.[value >= 0 ? 0 : 1])
    .filter((line): line is string => Boolean(line))
  const acoustic = measured.length
    ? `${measured.map((line, i) => i ? line : line[0].toUpperCase() + line.slice(1)).join('; ')}.`
    : `This is a noticeable ${c.direction === 'rise' ? 'increase' : 'drop'} in the song's energy shape.`
  const parts = [
    exits.length ? `${exits.map(changePartName).join(' and ')} ${exits.length === 1 && !changePartIsPlural(exits[0]) ? 'drops' : 'drop'} out` : '',
    entrances.length ? `${entrances.map(changePartName).join(' and ')} ${entrances.length === 1 && !changePartIsPlural(entrances[0]) ? 'enters' : 'enter'}` : '',
  ].filter(Boolean)
  return { measured: acoustic, suggested: parts.length ? `The separated audio suggests ${parts.join(' while ')} around here.` : '' }
}

export function energyHeadline(c: EnergyChange) {
  const sign = c.direction === 'rise' ? 1 : -1
  const strongest = c.contributors.find(([key, value]) => key !== 'active_layers' && value * sign > 0)?.[0]
  if (strongest === 'onset_density') return c.direction === 'rise' ? 'The rhythm picks up' : 'The rhythm thins out'
  if (strongest === 'loudness') return c.direction === 'rise' ? 'The recording surges' : 'The recording pulls back'
  if (strongest === 'stereo_width') return c.direction === 'rise' ? 'The mix opens up' : 'The mix narrows'
  if (strongest === 'bass_activity') return c.direction === 'rise' ? 'The low end grows' : 'The low end recedes'
  return c.direction === 'rise' ? 'Energy rises' : 'Energy falls'
}

export function participation(source: Source, m: MacroSection) {
  const seconds = source.segments.reduce((sum, s) => sum + Math.max(0, Math.min(s.end, m.end) - Math.max(s.start, m.start)), 0)
  return Math.min(1, seconds / Math.max(.01, m.end - m.start))
}

const comparisonWords: Record<string, [string, string]> = {
  loudness: ['the recording is louder', 'the recording is quieter'],
  onset_density: ['rhythmic attacks are more frequent', 'rhythmic attacks are less frequent'],
  percussive_energy: ['the hits have sharper attacks', 'the hits have softer attacks'],
  bass_activity: ['the low end is more active', 'the low end is less active'],
  high_activity: ['the high end is more active', 'the high end is less active'],
  band_spread: ['more of the frequency range is active', 'less of the frequency range is active'],
  hp_ratio: ['percussive sounds are more prominent', 'sustained sounds are more prominent'],
  stereo_width: ['the mix is wider', 'the mix is narrower'],
}

export function comparisonSignals(
  deltas: Record<string, number>, weights: Record<string, number>,
): string[] {
  return Object.entries(deltas)
    .filter(([key, delta]) => comparisonWords[key] && Number.isFinite(delta))
    .map(([key, delta]) => ({ key, delta, impact: Math.abs(delta * (weights[key] ?? 0)) }))
    .filter(({ delta, impact }) => Math.abs(delta) >= .1 && impact >= .015)
    .sort((a, b) => b.impact - a.impact)
    .slice(0, 3)
    .map(({ key, delta }) => comparisonWords[key][delta > 0 ? 0 : 1])
}
