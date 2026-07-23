import type { Grid } from '../types'

type MusicalGrid = Pick<Grid, 'bpm' | 'offset' | 'meter'>
export const MIN_SECTION_SECONDS = .1

/** The grid gets finer as the musician zooms into a phrase. */
export function gridStepSeconds(grid: MusicalGrid, pixelsPerSecond: number): number {
  if (grid.bpm <= 0) return 0
  const beat = 60 / grid.bpm
  const beatsPerBar = Math.max(1, Number.parseInt(grid.meter, 10) || 4)
  const bar = beat * beatsPerBar
  const steps = [beat / 4, beat / 2, beat, bar, bar * 2, bar * 4, bar * 8, bar * 16, bar * 32]
  return steps.find(step => step * pixelsPerSecond >= 12) ?? steps[steps.length - 1]
}

export function boundaryTime(t: number, grid: MusicalGrid, pixelsPerSecond: number,
                             min: number, max: number, snap: boolean, free: boolean): number {
  const clamp = (value: number) => Math.max(min, Math.min(max, value))
  const step = gridStepSeconds(grid, pixelsPerSecond)
  if (!snap || free || step <= 0) return clamp(t)
  const first = Math.ceil((min - grid.offset) / step - 1e-9)
  const last = Math.floor((max - grid.offset) / step + 1e-9)
  if (first > last) return clamp(t)
  const tick = Math.max(first, Math.min(last, Math.round((t - grid.offset) / step)))
  return Number((grid.offset + tick * step).toFixed(9))
}
