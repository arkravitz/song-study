import assert from 'node:assert/strict'
import { test } from 'node:test'
import { gridStepSeconds, boundaryTime } from '../src/lib/sectionEditing.ts'

const grid = { bpm: 120, offset: .2, meter: '4/4' }

test('whole-song edits snap to bars and close edits snap to subdivisions', () => {
  assert.equal(gridStepSeconds(grid, 10), 2)
  assert.equal(gridStepSeconds(grid, 160), .125)
  assert.equal(boundaryTime(6.05, grid, 10, 0, 40, true, false), 6.2)
  assert.equal(boundaryTime(1.31, grid, 160, 0, 40, true, false), 1.325)
})

test('Shift and the grid toggle allow exact free movement', () => {
  assert.equal(boundaryTime(1.31, grid, 10, 0, 40, true, true), 1.31)
  assert.equal(boundaryTime(1.31, grid, 10, 0, 40, false, false), 1.31)
})

test('snapping stays on a grid point within neighboring section bounds', () => {
  assert.equal(boundaryTime(40, grid, 10, .1, 39.9, true, false), 38.2)
  assert.equal(boundaryTime(-1, grid, 10, .1, 39.9, false, false), .1)
  assert.equal(boundaryTime(40, grid, 10, .1, 39.9, false, true), 39.9)
})

test('a section narrower than the grid can still be adjusted safely', () => {
  assert.equal(boundaryTime(1.5, grid, 10, 1, 1.8, true, false), 1.5)
})
