import type { SongSummary } from '../types'

export type UserSectionNames = Record<string, Record<string, string>>
const key = 'arrangement-user-section-names-v1'

export function readUserSectionNames(): UserSectionNames {
  try {
    const value = JSON.parse(window.localStorage.getItem(key) || '{}')
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    return value as UserSectionNames
  } catch { return {} }
}

export function writeUserSectionNames(names: UserSectionNames) {
  try { window.localStorage.setItem(key, JSON.stringify(names)) } catch { /* editing still works for this session */ }
}

export function withUserSectionNames(song: SongSummary | null, names: UserSectionNames): SongSummary | null {
  if (!song) return null
  const forSong = names[song.id] ?? {}
  if (!Object.keys(forSong).length) return song
  return { ...song, sections: song.sections.map(s => ({
    ...s, user_label: forSong[s.macro_id || s.id] || undefined,
  })) }
}
