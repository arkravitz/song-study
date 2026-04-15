/** App shell: library + timeline + inspector. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type SectionEditSnapshot } from './api'
import { EditHistory } from './lib/editHistory'
import { Sidebar } from './components/Sidebar'
import { Transport } from './components/Transport'
import { SongStudy } from './components/SongStudy'
import { readUserSectionNames, withUserSectionNames, writeUserSectionNames } from './lib/userSectionNames'
import { macroSections } from './lib/sections'
import { Timeline } from './components/Timeline'
import { EditableSectionName } from './components/EditableSectionName'
import { MIN_SECTION_SECONDS } from './lib/sectionEditing'
import { PanelBoundary } from './components/ErrorBoundary'
import {
  EventDetail, PatternsList, SectionDetail, SongOverview,
} from './components/Inspector'
import { EnergyChangeList, ReasoningPanel } from './components/ReasoningPanel'
import { useHealth, useJob, useJobs, useSong, useSongList } from './hooks/useData'
import { usePlayer, useTransportKeys } from './hooks/useTransport'
import type { StemInfo } from './audio/Player'
import { useZoomView } from './hooks/useZoom'
import {
  loadState, saveState, songViewFor, type PersistedState, type SongView,
} from './lib/persist'
import { EVENT_COLORS, eventKindLabel, eventKindQualifier, fmtTime } from './lib/format'
import type { ArrangementEvent, Section } from './types'

type SectionEditState = { snapshot: SectionEditSnapshot; names: Record<string, string> }

type Tab = 'overview' | 'events' | 'energy' | 'patterns' | 'reasoning'

/** The tabs that exist, for validating a stored value against. */
const TAB_NAMES: readonly Tab[] = ['overview', 'events', 'energy', 'patterns', 'reasoning']

const DEFAULT_OPTIONS = {
  showWaveform: true,
  // Two overlaid energy traces is an analysis view. The perceived curve is the
  // one that answers "where does this build and release".
  showEnergyRaw: false,
  // Off by default. Both answer "why does this feel bigger", which is a real
  // question and the wrong first question. On, they were the second and third
  // loudest things on screen and took a third of the plot height.
  showWidth: false,
  // A flat-topped block that says nothing about where the rhythm is. Off with
  // the other "why" traces; the events row already shows entrances.
  gridSnap: true,
  showEvents: false,
}

export function App() {
  // Read once, at mount. Held in a ref so that a re-render cannot re-read
  // storage and so that every effect below closes over one snapshot rather than
  // whatever happens to be in localStorage when it runs.
  //
  // The point of this is that a refresh used to lose the *song*, not just the
  // zoom: measured, reloading a document that had been left on Taste at 0:12
  // came back on DNA at 0:00, because the app fell back to the most recently
  // analysed track. On a library of more than one song that is simply wrong.
  const savedRef = useRef<PersistedState | null>(loadState())
  const saved = savedRef.current

  const [selectedId, setSelectedId] = useState<string | null>(saved?.selectedId ?? null)
  const [listKey, setListKey] = useState(0)
  const [jobId, setJobId] = useState<string | null>(null)
  const { health } = useHealth()
  const { songs, error: libraryError } = useSongList(listKey)
  const { song, loading, reload } = useSong(selectedId)
  const [userSectionNames, setUserSectionNames] = useState(readUserSectionNames)
  const studySong = useMemo(() => withUserSectionNames(song, userSectionNames), [song, userSectionNames])
  const songSections = useMemo(() => studySong ? macroSections(studySong) : [], [studySong])
  const jobs = useJobs()
  const { job } = useJob(jobId)

  const [tab, setTab] = useState<Tab>(
    // Narrowed to the tabs that exist, because storage is editable and a stale
    // value would otherwise render a panel with nothing in it.
    (TAB_NAMES as readonly string[]).includes(saved?.tab ?? '')
      ? (saved!.tab as Tab) : 'overview')
  const [selectedEvent, setSelectedEvent] = useState<ArrangementEvent | null>(null)
  const [selectedSection, setSelectedSection] = useState<Section | null>(null)
  const [selectedPattern, setSelectedPattern] = useState<string | null>(null)
  const [selectedEnergyIdx, setSelectedEnergyIdx] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [sectionEditBusy, setSectionEditBusy] = useState(false)
  const [sectionEditError, setSectionEditError] = useState('')
  const sectionEditBusyRef = useRef(false)
  const sectionNamesRef = useRef(userSectionNames)
  const editHistories = useRef(new Map<string, EditHistory<SectionEditState>>())
  const [, renderHistory] = useState(0)
  const editHistory = song ? editHistories.current.get(song.id) : undefined
  useEffect(() => { setSectionEditError('') }, [selectedId])
  // The library starts closed on a narrow window and open on a wide one, and
  // only follows the user afterwards. A panel that is open by default on a
  // 1280px laptop covers a third of the song, and the timeline is the surface
  // that cannot be read some other way — so the library goes first. Once the
  // user has chosen, their choice wins and the automatic behaviour stops, or
  // closing the library would be undone by the next song change.
  const [showSidebar, setShowSidebar] = useState(false)
  const sidebarTouched = useRef(false)
  const [showInspector, setShowInspector] = useState(false)
  // Row toggles, defaults with the stored choices layered over them. A stored
  // value for a toggle that no longer exists is simply not read: the merge is
  // over the *defaults*, so a removed option disappears instead of lingering.
  const [options, setOptions] = useState(() => ({ ...DEFAULT_OPTIONS }))
  const [hiddenSources, setHiddenSources] = useState<Set<string>>(new Set())
  const [collapsedFamilies, setCollapsedFamilies] = useState<Set<string>>(new Set())
  const [focusSource, setFocusSource] = useState<string | null>(null)
  const [rangeA, setRangeA] = useState<{ start: number; end: number } | null>(null)
  const [rangeB, setRangeB] = useState<{ start: number; end: number } | null>(null)

  const audioUrl = selectedId ? api.audioUrl(selectedId) : null
  // Stems available for soloing, rebuilt only when the song changes.
  const stemList = useMemo<StemInfo[]>(
    () => (song?.stem_audio ?? []).map((s) => ({ id: s.stem, url: s.url })),
    [song?.stem_audio])
  // `timeRef` is the 60 Hz reading of the audio clock, and it is what the
  // timeline draws its playhead from. `state.time` is the 20 Hz React snapshot
  // and belongs to the readouts, where a frame of lag is invisible.
  const { state, ready, toggle, seek, nudge, setVolume, timeRef,
          mix, toggleSolo, clearMix } = usePlayer(audioUrl, stemList)


  // Both counts come from the server, which computes them the same way it does
  // for the library list. They were recomputed here from `song.sources` and
  // `song.sections`, which meant the sidebar said "6 instruments · 10 sections"
  // and this header, one screen away, said "5 instruments · 8 sections" for the
  // same record. Two computations of one fact is how they come to disagree.
  const audibleSourceCount = song?.audible_sources ?? 0
  const macroCount = song?.macro_sections ?? 0

  const duration = song?.duration ?? 1
  // The zoom hook needs to be able to tell "no song loaded yet" from "a song
  // that is one second long", and `duration` above cannot: it is 1 while the
  // analysis is still in flight, so the hook's `duration <= 0` guard never fired
  // and the placeholder was treated as a real duration. That is the same mistake
  // as B-060, where a placeholder won a comparison against a real value, and
  // the visible effect was the same too — a restored window being computed
  // against a 1-second song, clamped to 0.0-1.0, and then thrown away when the
  // real duration arrived with nothing left to restore.
  const zoomDuration = song?.duration ?? 0
  // No second argument: the default is the whole song. It used to pass
  // `min(30, max(8, duration / 4))` here, which opened "DNA." on 16% of the
  // record and 2 of its 9 sections while the form list beside it showed all
  // nine. See the note on `useZoomView`.
  const zoom = useZoomView(zoomDuration)

  // Dev-only: the current view window, so a screenshot can be turned back into
  // times. `useZoom` keeps the view in a ref and never re-renders on a pan or
  // a zoom — the right design for the timeline, and the wrong one for
  // investigating a report: without the window a pixel in a screenshot cannot
  // be named, and an alignment complaint becomes an argument. Paired with `__aa`.
  if (import.meta.env.DEV) {
    ;(window as any).__aaView = zoom.view
  }

  // auto-select the newest song when the library first loads
  useEffect(() => {
    if (!songs.length) return
    if (selectedId && songs.some((s) => s.id === selectedId)) return
    // Either nothing was stored, or what was stored names a song that is no
    // longer in the library — deleted, or on a different data directory. Fall
    // back rather than opening a song that will 404.
    setSelectedId(songs[0].id)
  }, [songs, selectedId])

  // Come back to where each song was left, the first time and every time after.
  //
  // `zoom` is a fresh object on every render (its memo depends on the view
  // state), so putting it in the dependency array made this effect fire on
  // every render, reset the view, refetch four curves and re-render — a
  // feedback loop that hammered the server thousands of times. The functions are
  // held in refs instead and the effect depends on the song id alone.
  //
  // The first song is *not* special-cased. It used to be, so the app would not
  // reset the view on mount — which is also why a stored window was never
  // applied on load, because applying it and not-resetting are the same action.
  // One path for both means there is no mount case left to get wrong.
  const resetZoom = useRef(zoom.reset)
  resetZoom.current = zoom.reset
  const restoreZoom = useRef(zoom.restore)
  restoreZoom.current = zoom.restore
  // Which song's state has been put into the UI, and whether the record effect
  // has been told to stand down. Without this the two effects race: the record
  // effect fires on mount, before the restore has landed, and writes this
  // song's *default* window over the stored one — so the second reload found
  // nothing to restore and the third found a zero-width window.
  const appliedSong = useRef<string | null>(null)
  const suppressSave = useRef(false)
  useEffect(() => {
    if (!selectedId) return
    if (appliedSong.current === selectedId) return
    appliedSong.current = selectedId
    // Per song, not global: zooming into 1:30 of one track must not mean the
    // next track opens at 1:30.
    const sv = songViewFor(saved, selectedId)
    if (sv.view) restoreZoom.current(sv.view)
    else resetZoom.current()
    setHiddenSources(new Set(sv.hiddenSources))
    // The removed parts picker was the only family-collapse control.
    setCollapsedFamilies(new Set())
    setFocusSource(sv.focusSource)
    setSelectedEvent(null)
    setSelectedSection(null)
    setSelectedEnergyIdx(null)
    setRangeA(null)
    setRangeB(null)
    suppressSave.current = true
  }, [selectedId])

  // Record where you are. Debounced, because panning and dragging fire this on
  // every frame and a `localStorage` write per frame is a synchronous
  // round-trip through the disk on the main thread.
  const saveTimer = useRef<number | null>(null)
  useEffect(() => {
    if (suppressSave.current) { suppressSave.current = false; return }
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = null
      if (!selectedId || !song) return
      if (appliedSong.current !== selectedId) return
      const songsOut: Record<string, SongView> = { ...(saved?.songs ?? {}) }
      songsOut[selectedId] = {
        view: zoom.view,
        // Pruned to the sources this song actually has, so the store cannot
        // grow without bound across re-analyses that rename or drop a stem.
        hiddenSources: [...hiddenSources].filter((id) =>
          song.sources.some((s) => s.id === id)),
        collapsedFamilies: [...collapsedFamilies],
        focusSource,
      }
      saveState({
        selectedId,
        tab,
        // Only once the reader has pressed the button. See `PersistedState`.
        showSidebar: sidebarTouched.current ? showSidebar : undefined,
        showInspector,
        options,
        songs: songsOut,
      })
    }, 500)
    return () => { if (saveTimer.current !== null) window.clearTimeout(saveTimer.current) }
  }, [selectedId, song, tab, showSidebar, showInspector, options,
      hiddenSources, collapsedFamilies, focusSource, zoom.view, saved])

  // when a job finishes, open the result
  useEffect(() => {
    if (job?.status === 'done' && job.song_id) {
      setListKey((k) => k + 1)
      setSelectedId(job.song_id)
      setJobId(null)
    } else if (job && (job.status === 'failed' || job.status === 'cancelled')) {
      setJobId(null)
    }
  }, [job?.status, job?.song_id])

  // keep the view following playback
  useEffect(() => {
    if (state.playing) zoom.followPlayhead(state.time, true)
  }, [state.time, state.playing, zoom])

  // Same hazard as above: these callbacks are read through a ref so the
  // keyboard handler is not re-bound on every render.
  const zoomRef = useRef(zoom)
  zoomRef.current = zoom
  const songRef = useRef(song)
  songRef.current = song
  const seekRef = useRef(seek)
  seekRef.current = seek
  const clearRanges = useRef(() => { setRangeA(null); setRangeB(null) })
  clearRanges.current = () => { setRangeA(null); setRangeB(null) }

  const handlers = useMemo(() => ({
    toggle,
    nudge,
    onLeft: () => zoomRef.current.showAll(),
    onRight: () => songRef.current && seekRef.current(0),
    onEscape: () => clearRanges.current(),
    // Escape also drops a solo. A solo is a transient mode the user entered to
    // answer one question, and the way back out should not require the mouse.
    onClearMix: () => clearMixRef.current(),
  }), [toggle, nudge])
  const clearMixRef = useRef(clearMix)
  clearMixRef.current = clearMix
  useTransportKeys(handlers)

  const queuePath = useCallback((path: string) => {
    if (!path) return
    api.analyse(path)
      .then((r) => { setJobId(r.job.id); setSelectedId(r.song_id) })
      .catch((e) => alert(`Could not queue: ${e.message}`))
  }, [])

  const onDelete = useCallback((id: string) => {
    api.deleteSong(id).then(() => {
      setListKey((k) => k + 1)
      if (selectedId === id) setSelectedId(null)
    })
  }, [selectedId])

  const onJump = useCallback((t: number) => {
    seek(t)
    zoom.showRange(Math.max(0, t - 1.5), Math.min(duration, t + 1.5))
  }, [seek, zoom, duration])

  const toggleSetValue = useCallback(<T,>(set: Set<T>, v: T): Set<T> => {
    const next = new Set(set)
    if (next.has(v)) next.delete(v)
    else next.add(v)
    return next
  }, [])

  const onOptions = useCallback((o: Partial<typeof DEFAULT_OPTIONS>) => {
    setOptions((s) => ({ ...s, ...o }))
  }, [])

  const setSongSectionNames = useCallback((id: string, names: Record<string, string>) => {
    const next = { ...sectionNamesRef.current, [id]: names }
    sectionNamesRef.current = next
    writeUserSectionNames(next)
    setUserSectionNames(next)
  }, [])

  const applySectionEdit = useCallback(async (label: string, action: () => Promise<unknown>) => {
    if (!song || sectionEditBusyRef.current) throw new Error('Another section edit is being saved.')
    const id = song.id
    sectionEditBusyRef.current = true
    setSectionEditBusy(true); setSectionEditError('')
    try {
      const before = { snapshot: await api.sectionState(id), names: { ...(sectionNamesRef.current[id] ?? {}) } }
      await action()
      const after = { snapshot: await api.sectionState(id), names: { ...(sectionNamesRef.current[id] ?? {}) } }
      if (before.snapshot.revision !== after.snapshot.revision || JSON.stringify(before.names) !== JSON.stringify(after.names)) {
        let history = editHistories.current.get(id)
        if (!history) { history = new EditHistory(); editHistories.current.set(id, history) }
        history.record(label, before, after)
        renderHistory(n => n + 1)
      }
      setSelectedSection(null)
      reload(); setListKey(k => k + 1)
    } catch (error) {
      setSectionEditError((error as Error).message)
      throw error
    } finally { sectionEditBusyRef.current = false; setSectionEditBusy(false) }
  }, [song?.id, reload])

  const onRenameSection = useCallback((section: Section, name: string | null) => {
    if (!song) return
    const id = song.id
    void applySectionEdit('Rename section', async () => {
      const names = { ...(sectionNamesRef.current[id] ?? {}) }
      const key = section.macro_id || section.id
      if (name) names[key] = name
      else delete names[key]
      setSongSectionNames(id, names)
    }).catch(() => {})
  }, [song?.id, applySectionEdit, setSongSectionNames])

  const splitAtPlayhead = useCallback(async () => {
    if (!song || sectionEditBusyRef.current) return
    const at = timeRef.current
    const section = songSections.find(s => at >= s.start + MIN_SECTION_SECONDS && at <= s.end - MIN_SECTION_SECONDS)
    if (!section) return
    await applySectionEdit('Split section', () => api.splitSection(song.id, section.id, at,
      section.userNamed ? section.label : undefined,
      section.userNamed ? section.label : undefined)).catch(() => {})
  }, [song, songSections, timeRef, applySectionEdit])

  const mergeSections = useCallback(async (rightMacroId: string) => {
    if (song) await applySectionEdit('Merge sections', () => api.mergeSections(song.id, rightMacroId))
  }, [song?.id, applySectionEdit])

  const moveSectionBoundary = useCallback(async (rightMacroId: string, at: number) => {
    if (song) await applySectionEdit('Move boundary', () => api.moveSectionBoundary(song.id, rightMacroId, at))
  }, [song?.id, applySectionEdit])

  const restoreEdit = useCallback(async (redo: boolean) => {
    if (!song || sectionEditBusyRef.current) return
    const id = song.id
    const history = editHistories.current.get(id)
    const edit = redo ? history?.redo : history?.undo
    if (!history || !edit) return
    const target = redo ? edit.after : edit.before
    const expected = redo ? edit.before : edit.after
    sectionEditBusyRef.current = true
    setSectionEditBusy(true); setSectionEditError('')
    try {
      await api.restoreSections(id, target.snapshot, expected.snapshot.revision)
      setSongSectionNames(id, { ...target.names })
      if (redo) history.didRedo(); else history.didUndo()
      renderHistory(n => n + 1)
      setSelectedSection(null)
      reload(); setListKey(k => k + 1)
    } catch (error) { setSectionEditError((error as Error).message) }
    finally { sectionEditBusyRef.current = false; setSectionEditBusy(false) }
  }, [song?.id, reload, setSongSectionNames])

  useEffect(() => {
    const onEditKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.repeat) return
      const target = e.target as HTMLElement | null
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return
      if (e.key.toLowerCase() === 'e') { e.preventDefault(); void splitAtPlayhead() }
      if (e.key.toLowerCase() === 'z') { e.preventDefault(); void restoreEdit(e.shiftKey) }
    }
    window.addEventListener('keydown', onEditKey)
    return () => window.removeEventListener('keydown', onEditKey)
  }, [splitAtPlayhead, restoreEdit])

  const doCompareWith = useCallback(async (other: Section) => {
    if (!song || !selectedSection) return
    setBusy(true)
    try {
      await api.compare(song.id, other.start, other.end,
        selectedSection.start, selectedSection.end, 'other', 'this')
      reload()
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }, [song, selectedSection, reload])

  const showSidebarToggle = (
    <button
      className={`ghost ${showSidebar ? 'on' : ''}`}
      onClick={() => { sidebarTouched.current = true; setShowSidebar((s) => !s) }}
      title="Toggle library"
      aria-pressed={showSidebar}
    >Library</button>
  )
  const showInspectorToggle = (
    <button
      className={`ghost ${showInspector ? 'on' : ''}`}
      onClick={() => setShowInspector((s) => !s)}
      title="Toggle inspector"
      aria-pressed={showInspector}
    >Explore analysis</button>
  )

  return (
    <div className="app">
      <div className="topbar">
        {showSidebarToggle}
        <div className="brand">Song study</div>
        {song && (
          <span className="song-title nowrap">
            {song.title}{song.artist ? ` — ${song.artist}` : ''}
          </span>
        )}
        <div className="spacer" />
        {job && job.status === 'running' && (
          <span className="meta">
            {job.stage} {Math.round(job.progress * 100)}% · {job.elapsed.toFixed(0)}s
          </span>
        )}
        {song && (
          <span className="meta">
            {audibleSourceCount} detected {audibleSourceCount === 1 ? 'part' : 'parts'} · {macroCount} sections
          </span>
        )}
        {showInspectorToggle}
      </div>

      <div className="main">
        {showSidebar && (
          <Sidebar
            songs={songs}
            libraryError={libraryError}
            selectedId={selectedId}
            onSelect={(id) => { setSelectedId(id); setShowSidebar(false) }}
            onDelete={onDelete}
            health={health}
            jobs={jobs}
            onQueuePath={queuePath}
            onCancel={(id) => api.cancelJob(id)}
            refresh={() => setListKey((k) => k + 1)}
          />
        )}

        <div className="center">
          {loading && !song && <div className="empty"><div className="inner">loading…</div></div>}
          {!loading && !song && (
            <div className="empty">
              <div className="inner">
                {/*
                  The heading and the instruction have to match the situation.

                  This said "Nothing loaded — Add a song from the library panel"
                  while the library panel beside it held four songs, which is what
                  a reader sees after the server has been stopped and restarted:
                  the list comes back, the selection does not, and the one piece
                  of guidance on screen sends them to add music they already have.

                  So the two states are separated. "No songs at all" is a
                  first-run state and the paragraph about what the app does
                  belongs to it. "Songs exist, none is open" is one click in the
                  library, and telling someone to add a file is not a small
                  misdirection — it is the expensive kind.
                */}
                <h2>{songs.length ? 'No song open' : 'Nothing loaded'}</h2>
                {songs.length ? (
                  <p>
                    Pick one from the library to open it.
                    {showSidebar
                      ? ''
                      : ' The library is hidden — the ☰ button at the top left brings it back.'}
                  </p>
                ) : (
                  <>
                    <p>
                      Add a song from the library panel. Everything runs locally:
                      source separation, beat and tempo tracking, structure
                      analysis, per-element activity detection, stereo measurement,
                      and an energy model.
                    </p>
                    <p className="faint small">
                      Expect roughly 1–2 minutes per song for standard quality. The
                      deep profile adds a much slower, cleaner vocal separation.
                    </p>
                  </>
                )}
              </div>
            </div>
          )}

          {song && (
            <>
              <Transport
                song={song}
                time={state.time}
                playing={state.playing}
                ready={ready}
                volume={state.volume}
                onToggle={toggle}
                onSeek={seek}
                onNudge={nudge}
                onVolume={setVolume}
                mix={mix}
                onSoloStem={(id) => void toggleSolo(id)}
                onClearMix={clearMix}
              />
              <nav className="section-map" aria-label="Song sections">
                <span>Sections</span>
                {songSections.map((section, index) => (
                  <div key={section.id}
                    className={selectedSection && section.parts.some(part => part.id === selectedSection.id) ? 'selected' : ''}
                    title={`${fmtTime(section.start, false)}–${fmtTime(section.end, false)}`}>
                    <EditableSectionName section={section} prefix={String(index + 1).padStart(2, '0')}
                      onRename={name => onRenameSection(section.parts[0], name)} onSelect={() => {
                      setSelectedSection(section.parts[0])
                      setSelectedEvent(null)
                      setSelectedEnergyIdx(null)
                      setFocusSource(null)
                      seek(section.start)
                      if (!state.playing) toggle()
                    }} />
                  </div>
                ))}
              </nav>
              <Timeline
                song={studySong ?? song}
                playheadRef={timeRef}
                playbackTime={state.time}
                onSplitAtPlayhead={splitAtPlayhead}
                onMoveSectionBoundary={moveSectionBoundary}
                onMergeSections={mergeSections}
                onRenameSection={onRenameSection}
                onUndo={() => void restoreEdit(false)} onRedo={() => void restoreEdit(true)}
                undoLabel={editHistory?.undo?.label} redoLabel={editHistory?.redo?.label}
                sectionEditBusy={sectionEditBusy}
                sectionEditError={sectionEditError}
                onSeek={seek}
                mix={mix}
                onSolo={(id) => void toggleSolo(id)}
                // Clicking a change marker on the energy row opens its
                // explanation, the same as clicking it in the list.
                onEnergyChange={(c) => {
                  setSelectedEnergyIdx(song.energy_changes.indexOf(c))
                  setSelectedSection(null); setSelectedEvent(null); setFocusSource(null)
                }}
                onSelectEvent={(e) => { setSelectedEvent(e); if (e) { setSelectedSection(null); setSelectedEnergyIdx(null); setFocusSource(null) } }}
                onSelectSection={(s) => { setSelectedSection(s); setSelectedEvent(null); setSelectedEnergyIdx(null); setFocusSource(null) }}
                selectedEventId={selectedEvent?.id ?? null}
                selectedSectionId={selectedSection?.id ?? null}
                view={zoom.view}
                setView={zoom.setView}
                zoomAt={zoom.zoomAt}
                panTo={zoom.panTo}
                showAll={zoom.showAll}
                showRange={zoom.showRange}
                rangeA={rangeA}
                rangeB={rangeB}
                onRangeChange={(a, b) => { setRangeA(a); setRangeB(b) }}
                hiddenSources={hiddenSources}
                collapsedFamilies={collapsedFamilies}
                focusSource={focusSource}
                onFocusSource={(id) => { setFocusSource(id); setSelectedEvent(null); setSelectedEnergyIdx(null) }}
                options={options}
                onOptions={onOptions}
              />
              <SongStudy key={song.id} song={studySong ?? song} time={state.time} selected={selectedSection}
                sourceId={focusSource} event={selectedEvent}
                change={selectedEnergyIdx === null ? null : song.energy_changes[selectedEnergyIdx] ?? null}
                onSection={(s) => { setSelectedSection(s); setSelectedEvent(null); setSelectedEnergyIdx(null); setFocusSource(null) }}
                onSource={(id) => { setFocusSource(id); setSelectedEvent(null); setSelectedEnergyIdx(null) }}
                onSeek={seek} onZoom={zoom.showRange} solo={mix.solo} onSolo={(id) => void toggleSolo(id)}
                onListen={(t) => { seek(t); if (!state.playing) toggle() }}
                onChange={(c) => { setSelectedEnergyIdx(song.energy_changes.indexOf(c)); setSelectedEvent(null); setFocusSource(null); seek(c.t) }}
                onClear={() => { setSelectedSection(null); setSelectedEvent(null); setSelectedEnergyIdx(null); setFocusSource(null) }}
                onEvidence={() => { setShowInspector(true); setTab(selectedEnergyIdx !== null ? 'energy' : 'overview') }}
              />
              {(rangeA || rangeB) && <div className="range-footer"><span>Selected for comparison</span>
                <button onClick={() => { setShowInspector(true); setTab('reasoning') }}>Compare selected ranges</button>
                <button onClick={() => {setRangeA(null);setRangeB(null)}}>Clear ranges</button>
              </div>}
            </>
          )}
        </div>

        {showInspector && song && (
          <div className="inspector"><div className="drawer-heading"><strong>Explore analysis</strong><button className="ghost" onClick={() => setShowInspector(false)}>Close</button></div>
            <div className="seg-ctl" style={{ marginBottom: 10, width: '100%' }}>
              {(['overview', 'events', 'energy', 'patterns', 'reasoning'] as Tab[]).map((t) => (
                <button key={t} className={tab === t ? 'on' : ''}
                  onClick={() => setTab(t)} style={{ flex: 1 }}>{({overview:'Details',events:'Changes',energy:'Energy',patterns:'Patterns',reasoning:'Ask'})[t]}</button>
              ))}
            </div>

            {tab === 'overview' && (
              <>
                {selectedEvent && (
                  <PanelBoundary label="Event inspector">
                    <EventDetail
                      event={selectedEvent}
                      song={song}
                      busy={busy}
                      onSelectSource={(id) => {
                        setFocusSource(id)
                        setHiddenSources(new Set(song.sources.filter((s) => s.id !== id).map((s) => s.id)))
                      }}
                      onExplain={async () => {
                        setBusy(true)
                        try {
                          await api.insightEvent(song.id, selectedEvent.id)
                          reload()
                        } catch (e) { alert((e as Error).message) } finally { setBusy(false) }
                      }}
                    />
                  </PanelBoundary>
                )}
                {selectedSection && (
                  <PanelBoundary label="Section inspector">
                    <SectionDetail
                      section={selectedSection}
                      song={song}
                      busy={busy}
                      onJump={onJump}
                      onCompare={doCompareWith}
                    />
                  </PanelBoundary>
                )}
                <PanelBoundary label="Song overview">
                  <SongOverview
                    song={song}
                    selectedSectionId={selectedSection?.id ?? null}
                    onPickSection={(s) => {
                      setSelectedSection(s)
                      seek(s.start)
                    }}
                  />
                </PanelBoundary>
                <div className="card">
                  <h3>Source lanes</h3>
                  <div className="row wrap" style={{ gap: 4 }}>
                    {song.sources.map((s) => (
                      <span key={s.id}
                        className="chip"
                        style={{
                          background: hiddenSources.has(s.id) ? 'var(--bg)' : s.color,
                          color: hiddenSources.has(s.id) ? 'var(--fg-faint)' : '#08121a',
                        }}
                        onClick={() => setHiddenSources((hs) => toggleSetValue(hs, s.id))}
                        onDoubleClick={() => setFocusSource(focusSource === s.id ? null : s.id)}
                        title={s.descriptor.descriptors.join(', ')}
                      >
                        {s.label}
                      </span>
                    ))}
                  </div>
                  <div className="tiny faint" style={{ marginTop: 5 }}>
                    click to hide a lane · double-click to solo it
                  </div>
                </div>
                {/* Caveats live here, in the default view, because they qualify
                    the numbers directly above them. There used to be a `notes`
                    tab as well, holding the identical list: the same five
                    paragraphs printed twice on one screen. One place, no tab, no
                    button that switched the wrong tab. */}
                {song.warnings.length > 0 && (
                  <div className="card">
                    <h3>Worth knowing ({song.warnings.length})</h3>
                    <ul className="small dim" style={{ paddingLeft: 16, margin: 0 }}>
                      {song.warnings.map((w, i) => (
                        <li key={i} style={{ marginBottom: 4 }}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}

            {tab === 'events' && (
              <div className="card">
                <h3>Arrangement events ({song.events.length})</h3>
                <div className="tiny faint" style={{ marginBottom: 6 }}>
                  Everything the analysis noticed change, in time order. Click a row
                  to hear it.
                </div>
                {/*
                  The `kind` column was showing the internal enum, truncated to fit:
                  `e.kind.slice(0, 9)` rendered "width_cha" and "density_c" beside a
                  `what` column that already said "Stereo image narrows" in English.
                  Two vocabularies for one row, one of them the schema's.

                  It is replaced by the swatch the timeline draws that event in,
                  which is the thing the column was actually for — matching a row
                  to a pin — and by the plain-language kind name. Confidence is a
                  percentage with a bar rather than a bare decimal.
                */}
                <table className="tbl">
                  <thead>
                    <tr><th>time</th><th>bar</th><th></th><th>what changed</th></tr>
                  </thead>
                  <tbody>
                    {song.events.map((e) => (
                      <tr key={e.id}
                        style={{
                          cursor: 'pointer',
                          background: selectedEvent?.id === e.id ? 'var(--bg-3)' : undefined,
                        }}
                        onClick={() => { setSelectedEvent(e); setSelectedSection(null); setTab('overview'); seek(Math.max(0, e.t)) }}>
                        <td className="num mono">{fmtTime(e.t)}</td>
                        <td className="num mono tiny faint">
                          {e.bar !== null ? e.bar + 1 : '—'}
                        </td>
                        <td style={{ width: 18 }}>
                          <i
                            className="dot"
                            title={eventKindLabel(e.kind)}
                            style={{
                              background: EVENT_COLORS[e.kind] ?? '#888',
                              opacity: 0.4 + 0.6 * e.confidence,
                            }}
                          />
                        </td>
                        <td className="tiny">
                          {e.label}
                          {(() => {
                            const extra = eventKindQualifier(e.label, e.kind)
                            return extra ? <span className="faint"> · {extra}</span> : null
                          })()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {tab === 'energy' && (
              <>
                <div className="card">
                  <h3>Energy changes ({song.energy_changes.length})</h3>
                  <div className="tiny faint" style={{ marginBottom: 6 }}>
                    Where the arrangement's energy moved, and which measurements
                    moved with it. Click a change to hear it.
                  </div>
                  <EnergyChangeList
                    changes={song.energy_changes}
                    song={song}
                    selectedIndex={selectedEnergyIdx}
                    onJump={(t) => onJump(t)}
                    onExplain={async (i) => {
                      setBusy(true)
                      try {
                        await api.insightEnergy(song.id, i)
                        reload()
                      } catch (e) { alert((e as Error).message) } finally { setBusy(false) }
                    }}
                  />
                </div>
                <div className="card">
                  <h3>Section energy</h3>
                  <table className="tbl">
                    <thead>
                      <tr><th>section</th><th>time</th><th>E</th><th>srcs</th><th>width</th></tr>
                    </thead>
                    <tbody>
                      {song.sections.map((s) => (
                        <tr key={s.id} style={{ cursor: 'pointer' }}
                          onClick={() => { setSelectedSection(s); setTab('overview'); onJump(s.start) }}>
                          <td className="tiny">{s.label}</td>
                          <td className="num mono">{fmtTime(s.start)}</td>
                          <td className="num">{s.energy_perceived.toFixed(2)}</td>
                          <td className="num">{s.source_count}</td>
                          <td className="num">{s.stereo_width.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {tab === 'patterns' && (
              <div className="card">
                <PatternsList
                  patterns={song.patterns}
                  selected={selectedPattern}
                  onSelect={setSelectedPattern}
                  onJump={onJump}
                />
              </div>
            )}

            {tab === 'reasoning' && (
              <ReasoningPanel
                song={song}
                onSongChanged={reload}
                onJump={onJump}
                rangeA={rangeA}
                rangeB={rangeB}
                onExplainWindow={(t0, t1) => { setRangeA({ start: t0, end: t1 }) }}
                onCompare={() => setTab('reasoning')}
                onExplainEnergy={(i) => setSelectedEnergyIdx(i)}
                onExplainEvent={(id) => {
                  const e = song.events.find((x) => x.id === id)
                  if (e) setSelectedEvent(e)
                }}
                llmEnabled={health?.llm_enabled ?? false}
              />
            )}
          </div>
        )}
      </div>
    </div>
  )
}
