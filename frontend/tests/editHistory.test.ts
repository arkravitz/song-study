import assert from 'node:assert/strict'
import { test } from 'node:test'
import { EditHistory } from '../src/lib/editHistory.ts'

test('undo and redo preserve entire edit states and do not move until saved', () => {
  const history = new EditHistory<object>()
  const before = { sections: ['Verse'], names: {} }
  const after = { sections: ['Verse', 'Chorus'], names: { chorus: 'Final chorus' } }
  history.record('Split section', before, after)
  assert.deepEqual(history.undo?.before, before)
  assert.equal(history.redo, undefined)
  // A failed restore leaves the pending undo available.
  assert.equal(history.undo?.label, 'Split section')
  history.didUndo()
  assert.deepEqual(history.redo?.after, after)
  history.didRedo()
  assert.deepEqual(history.undo?.before, before)
})

test('a new edit after undo replaces the redo branch and history is bounded', () => {
  const history = new EditHistory<number>(2)
  history.record('Rename', 0, 1)
  history.record('Split', 1, 2)
  history.didUndo()
  history.record('Move boundary', 1, 3)
  assert.equal(history.redo, undefined)
  history.record('Merge', 3, 4)
  history.didUndo(); history.didUndo()
  assert.equal(history.undo, undefined)
  assert.equal(history.redo?.before, 1)
})
