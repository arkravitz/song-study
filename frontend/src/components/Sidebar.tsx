/** Library sidebar: song list, upload, local-folder scan, and the job queue. */
import { useRef, useState } from 'react'
import { api, uploadFile } from '../api'
import type { HealthInfo, JobInfo, SongListItem } from '../types'
import { fmtTime } from '../lib/format'

export interface SidebarProps {
  songs: SongListItem[]
  selectedId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
  health: HealthInfo | null
  jobs: JobInfo[]
  onQueuePath: (path: string) => void
  onCancel: (jobId: string) => void
  refresh: () => void
  /** Set when the list could not be fetched, so "empty" and "unreachable" differ. */
  libraryError: string | null
}

/** "standard" is a choice someone made; say it as a choice. */
function qualityWord(q: string): string {
  return q === 'standard' ? 'standard quality' : `${q} quality`
}

/** "multi" is the separator's internal name. It is more stems, which is the point. */
function separatorWord(s: string): string {
  if (s === 'multi') return 'six stems'
  if (s === 'single') return 'two stems'
  return s
}

export function Sidebar(props: SidebarProps) {
  const {
    songs, selectedId, onSelect, onDelete, health, jobs, onQueuePath, onCancel, refresh,
    libraryError,
  } = props
  const [over, setOver] = useState(false)
  // Which models ran is a troubleshooting question, not a first-glance one.
  const [showSystem, setShowSystem] = useState(false)
  const [uploading, setUploading] = useState<string | null>(null)
  const [scanRoot, setScanRoot] = useState('')
  const [scanning, setScanning] = useState(false)
  const [scanFiles, setScanFiles] = useState<{ path: string; name: string; size: number }[] | null>(null)
  const [filter, setFilter] = useState('')
  const fileRef = useRef<HTMLInputElement | null>(null)

  const visible = songs.filter((s) => {
    if (!filter.trim()) return true
    const q = filter.toLowerCase()
    return (s.title || '').toLowerCase().includes(q)
      || (s.artist || '').toLowerCase().includes(q)
      || (s.album || '').toLowerCase().includes(q)
  })

  const activeJobs = jobs.filter((j) => j.status === 'running' || j.status === 'queued')
  const recentJobs = jobs.filter((j) => j.status !== 'running' && j.status !== 'queued').slice(0, 4)

  const doUpload = async (files: FileList | null) => {
    if (!files || !files.length) return
    for (const f of Array.from(files)) {
      setUploading(f.name)
      try {
        const { promise } = uploadFile(f, () => {})
        const r = await promise
        onQueuePath(r.path)
      } catch (e) {
        alert(`Upload failed: ${(e as Error).message}`)
      } finally {
        setUploading(null)
      }
    }
  }

  const doScan = async () => {
    const root = scanRoot.trim() || '~'
    setScanning(true)
    try {
      const r = await api.library(root.replace(/^~(?=$|\/)/, '~'), 400)
      setScanFiles(r.files)
    } catch (e) {
      alert(`Scan failed: ${(e as Error).message}`)
    } finally {
      setScanning(false)
    }
  }

  return (
    <div className="sidebar">
      <div className="card">
        <h3>Add a song</h3>
        <div
          className={`drop-zone ${over ? 'over' : ''}`}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setOver(true) }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); void doUpload(e.dataTransfer.files) }}
        >
          {uploading ? `uploading ${uploading}…` : 'drop audio here, or click to choose'}
        </div>
        <input ref={fileRef} type="file" accept="audio/*,.flac,.mp3,.m4a,.wav"
          multiple style={{ display: 'none' }}
          onChange={(e) => void doUpload(e.target.files)} />

        <div className="tiny faint" style={{ marginTop: 10, marginBottom: 3 }}>
          …or a folder on this computer
        </div>
        <div className="row">
          <input
            type="text"
            value={scanRoot}
            placeholder="~/Music"
            title="A folder to look in. Its subfolders are searched too."
            onChange={(e) => setScanRoot(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void doScan() }}
          />
          <button onClick={doScan} disabled={scanning}
            title="List the audio files in that folder, so you can pick one">{scanning ? '…' : 'find files'}</button>
        </div>
        {scanFiles && (
          <div style={{ marginTop: 6, maxHeight: 180, overflowY: 'auto' }} className="scroll">
            <div className="row tiny faint" style={{ marginBottom: 3 }}>
              {scanFiles.length} files found
              <button className="tiny ghost" onClick={() => { setScanFiles(null); onQueuePath('') }}
                style={{ marginLeft: 'auto' }}>clear</button>
            </div>
            {scanFiles.map((f) => (
              <div key={f.path} className="song-item" onClick={() => onQueuePath(f.path)}>
                <div className="t nowrap">{f.name}</div>
                <div className="a">{(f.size / 1e6).toFixed(1)} MB</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {activeJobs.length > 0 && (
        <div className="card">
          <h3>Analysing ({activeJobs.length})</h3>
          {activeJobs.map((j) => (
            <div key={j.id} style={{ marginBottom: 8 }}>
              <div className="row tiny">
                <span className="nowrap grow">{j.title ?? j.song_id}</span>
                {/*
                  A stop is not instant, and the reason is worth stating rather
                  than leaving the user to conclude the button is broken.

                  The analysis only checks for cancellation at a stage boundary,
                  and the stages are not equal: separation runs a neural net over
                  the whole track and can be a minute or more, while the ones
                  after it take seconds. So after a click there is a real wait,
                  and during it the row used to look exactly as it had before --
                  same stage, same percentage, same enabled `stop` button.
                  Clicking again did nothing, which reads as a broken control
                  rather than a pending one.

                  So: the button becomes the state, and the line below says which
                  stage has to finish first. Both are true without knowing how
                  long that will take, which is the honest limit -- the server
                  does not know either.
                */}
                {j.stopping ? (
                  <span className="tiny" style={{ color: 'var(--warn)' }}>stopping…</span>
                ) : (
                  <button className="tiny ghost" onClick={() => onCancel(j.id)}>stop</button>
                )}
              </div>
              <div className="progress-track" style={{ margin: '3px 0' }}>
                <div style={{ width: `${Math.round(j.progress * 100)}%` }} />
              </div>
              <div className="tiny faint nowrap">
                {j.stopping
                  ? `stopping after ${j.stage ?? 'the current stage'}`
                  : `${(j.stage ?? 'starting')} · ${Math.round(j.progress * 100)}% · ${j.elapsed.toFixed(0)}s`}
              </div>
              {j.message && !j.stopping && <div className="tiny faint nowrap">{j.message}</div>}
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <h3>
          Library ({songs.length})
          <button className="tiny ghost" style={{ float: 'right', marginTop: -2 }}
            onClick={refresh}
            title="Look on disk again — for files added outside the app, or after a scan">refresh</button>
        </h3>
        <input type="search" value={filter} placeholder="filter…"
          onChange={(e) => setFilter(e.target.value)} style={{ marginBottom: 6 }} />
        {/* Two different facts, and they must not look the same. "Nothing
            analysed yet" is an invitation to add a song; "could not reach the
            server" is a problem with the connection, and answering it by
            re-adding songs you already have is the expensive mistake. */}
        {libraryError && (
          <div className="warn-box" style={{ marginBottom: 6 }}>
            Could not reach the server, so this list may be out of date.
            <span className="faint"> ({libraryError})</span>
          </div>
        )}
        {songs.length === 0 && !libraryError && (
          <div className="faint small">Nothing analysed yet.</div>
        )}
        {songs.length === 0 && libraryError && (
          <div className="faint small">
            The list could not be loaded. Songs already analysed are not lost —
            start the server and press refresh.
          </div>
        )}
        {visible.map((s) => (
          <div key={s.id}
            className={`song-item ${selectedId === s.id ? 'sel' : ''}`}
            onClick={() => onSelect(s.id)}>
            <div className="row">
              <div className="grow">
                <div className="t nowrap">{s.title}</div>
                <div className="a nowrap">{s.artist ?? '—'}</div>
                {/*
                  Abbreviations the reader has to decode, on a line that wraps
                  mid-item at a narrow sidebar width — "3:06.0 · 136 BPM 4/4 · 5
                  src · 12 sec · 160 ev" broke after the tempo and left a stray
                  "ev" on the next line. Two things fixed at once: words instead
                  of a three-letter code, and the one number a person scanning a
                  library actually wants, which is how long it is.
                */}
                <div className="m nowrap">
                  {fmtTime(s.duration)} · {s.bpm_confidence < 0.4 ? '≈' : ''}{s.bpm.toFixed(0)} BPM
                </div>
                <div className="m2 faint">
                  {s.sources} detected {s.sources === 1 ? 'part' : 'parts'} ·{' '}
                  {s.sections} sections
                </div>
              </div>
              <button className="tiny ghost"
                onClick={(e) => { e.stopPropagation(); if (confirm(`Delete analysis of "${s.title}"?`)) onDelete(s.id) }}
                title="delete">×</button>
            </div>
          </div>
        ))}
      </div>

      {recentJobs.length > 0 && (
        <div className="card">
          <h3>Recent</h3>
          {recentJobs.map((j) => (
            <div key={j.id} className="tiny faint" style={{ marginBottom: 2 }}>
              <span className="mono">{j.status}</span> {j.title ?? j.song_id}
              {j.error && <div style={{ color: 'var(--bad)' }}>{j.error.slice(0, 120)}</div>}
            </div>
          ))}
        </div>
      )}

      {/*
        Which separator ran, on what device, with which model. A producer opening
        this to look at a song does not need it, and a permanent card of it in the
        sidebar spends the same attention as the library. It is here for when
        someone asks "why does this track look different" — which is a real
        question, and the answer is usually the separator or the model.
      */}
      {health && (
        <div className="card">
          <button
            className="disclosure"
            onClick={() => setShowSystem((v) => !v)}
            aria-expanded={showSystem}
          >
            <span>Setup</span>
            {/* In words, and allowed to wrap.

                This read "standard · multi · deepseek-chat" and was clipped by
                the sidebar to "Setup standard · multi · deepseek…". Three
                problems in one line: two of the three are configuration
                identifiers rather than anything a producer chose or would
                recognise, and the third is cut off mid-word, so the summary of
                the app's own configuration is the one piece of text on screen
                that cannot be read.

                The full values are still there on expand, in mono, because that
                is where a troubleshooting question wants them. */}
            <span className="faint tiny setup-summary">
              {qualityWord(health.quality)} · {separatorWord(health.separator)} ·{' '}
              {health.llm_enabled ? 'commentary on' : 'no commentary'}
            </span>
          </button>
          {showSystem && (
            <div className="tiny faint mono" style={{ lineHeight: 1.6, marginTop: 6 }}>
              <div>quality: {health.quality}</div>
              <div>separator: {health.separator}</div>
              <div>llm model: {health.llm_enabled ? health.llm_model : 'disabled'}</div>
              <div>device: {String(health.hardware?.torch_device ?? '?')}</div>
              <div>llm: {health.llm_enabled ? health.llm_model : 'disabled'}</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
