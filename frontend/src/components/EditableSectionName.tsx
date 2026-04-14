import { useRef, useState } from 'react'
import type { MacroSection } from '../lib/sections'

interface Props {
  section: MacroSection
  text?: string
  prefix?: string
  onSelect: () => void
  onRename: (name: string | null) => void
}

/** The name stays in its section band while being edited. */
export function EditableSectionName({ section, text, prefix, onSelect, onRename }: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const editingRef = useRef(false)
  const begin = () => {
    editingRef.current = true
    setDraft(section.label)
    setEditing(true)
  }
  const finish = (save: boolean) => {
    if (!editingRef.current) return
    editingRef.current = false
    if (save && draft.trim() && draft.trim() !== section.label) onRename(draft.trim())
    setEditing(false)
  }
  return <div className={`editable-section-name${editing ? ' editing' : ''}`}>
    {editing ? <>
      <input type="text" autoFocus maxLength={36} aria-label={`Section name: ${section.label}`}
        value={draft} onChange={e => setDraft(e.target.value)}
        onFocus={e => e.currentTarget.select()}
        onPointerDown={e => e.stopPropagation()} onClick={e => e.stopPropagation()}
        onDoubleClick={e => e.stopPropagation()}
        onBlur={() => finish(true)}
        onKeyDown={e => {
          e.stopPropagation()
          if (e.key === 'Enter' || e.key === 'Escape') {
            e.preventDefault(); finish(e.key === 'Enter')
          }
        }} />
      <button className="section-name-save" title="Save name (Enter)"
        aria-label={`Save name for ${section.label}`}
        onPointerDown={e => { e.preventDefault(); e.stopPropagation() }}
        onClick={e => { e.stopPropagation(); finish(true) }}>✓</button>
      {section.userNamed && section.parts[0].label_source !== 'user' && <button
        className="section-name-reset" title="Use suggested name" aria-label="Use suggested name"
        onPointerDown={e => { e.preventDefault(); e.stopPropagation() }}
        onClick={e => { e.stopPropagation(); editingRef.current = false; onRename(null); setEditing(false) }}>↶</button>}
    </> : <>
      <button className="section-select" aria-label={`${section.label}, ${section.start.toFixed(1)} to ${section.end.toFixed(1)} seconds`}
        onPointerDown={e => { if (!e.shiftKey) e.stopPropagation() }}
        onClick={e => { e.stopPropagation(); if (!e.shiftKey) onSelect() }}
        onDoubleClick={e => { e.preventDefault(); e.stopPropagation(); begin() }}
        onKeyDown={e => { if (e.key === 'F2') { e.preventDefault(); e.stopPropagation(); begin() } }}>
        {prefix && <small>{prefix}</small>}<span>{text ?? section.label}</span>
      </button>
      <button className="section-rename" title={`Rename ${section.label} (double-click its name or press F2)`}
        aria-label={`Rename ${section.label}`}
        onPointerDown={e => e.stopPropagation()}
        onClick={e => { e.stopPropagation(); begin() }}
        onDoubleClick={e => e.stopPropagation()}>
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="m10.5 2.5 3 3-7.7 7.7-3.5.5.5-3.5 7.7-7.7Z M9 4l3 3" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
        </svg>
      </button>
    </>}
  </div>
}
