import assert from 'node:assert/strict'
import { test } from 'node:test'
import { sectionChanges, comparisonSignals } from '../src/lib/study.ts'

test('comparison changes describe the inspected section relative to the reference', () => {
  const reference = { label: 'Verse 1', start: 0, end: 10, sources: [], parts: [{start:0,end:10,energy_perceived:.3,stereo_width:.3}] }
  const inspected = { label: 'Chorus 2', start: 10, end: 20, sources: [], parts: [{start:10,end:20,energy_perceived:.5,stereo_width:.3}] }
  const song = { sources: [] }
  assert.match(sectionChanges(song, reference, inspected)[0], /more energetic in Chorus 2/)
  assert.match(sectionChanges(song, inspected, reference)[0], /less energetic in Verse 1/)
  assert.deepEqual(comparisonSignals({bass_activity:.3,onset_density:-.3}, {bass_activity:.1,onset_density:.1}), ['the low end is more active', 'rhythmic attacks are less frequent'])
})
