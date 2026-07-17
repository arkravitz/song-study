/** Per-song, session-local history. Move the cursor only after a restore succeeds. */
export interface Edit<T> { label: string; before: T; after: T }
export class EditHistory<T> {
  private past: Edit<T>[] = []
  private future: Edit<T>[] = []
  private limit: number
  constructor(limit = 50) { this.limit = limit }
  get undo() { return this.past.at(-1) }
  get redo() { return this.future.at(-1) }
  record(label: string, before: T, after: T) {
    this.past.push({ label, before, after })
    if (this.past.length > this.limit) this.past.shift()
    this.future = []
  }
  didUndo() { const edit = this.past.pop(); if (edit) this.future.push(edit) }
  didRedo() { const edit = this.future.pop(); if (edit) this.past.push(edit) }
}
