import { useEffect, useMemo, useRef, useState } from 'react'
import type { ArrangementEvent, EnergyChange, Section, SongSummary } from '../types'
import { macroSections, type MacroSection } from '../lib/sections'
import { fmtTime } from '../lib/format'
import { api } from '../api'
import { comparisonSignals, energyDescription, energyHeadline, energySignals, names, openingDescription, participation, sectionChanges, sectionValue, sourceName } from '../lib/study'

interface Props {
  song: SongSummary; time: number; selected: Section | null; sourceId: string | null
  event: ArrangementEvent | null; change: EnergyChange | null
  onSection: (s: Section | null) => void; onSource: (id: string | null) => void
  onSeek: (t: number) => void; onListen: (t: number) => void; onZoom: (a: number, b: number) => void
  onChange: (c: EnergyChange) => void; onEvidence: () => void; onClear: () => void
  onSolo: (id: string) => void; solo: Set<string>
}
export function SongStudy(p: Props) {
  const { song } = p
  const sections = useMemo(() => macroSections(song), [song])
  const [compareId, setCompareId] = useState('')
  const [energyEvidenceOpen, setEnergyEvidenceOpen] = useState(false)
  const [comparisonDetail, setComparisonDetail] = useState<string[] | null>(null)
  const [comparisonLoading, setComparisonLoading] = useState(false)
  const energyEvidenceRef = useRef<HTMLDivElement>(null)
  useEffect(() => { setEnergyEvidenceOpen(false) }, [p.change?.t])
  useEffect(() => { if (energyEvidenceOpen) energyEvidenceRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }) }, [energyEvidenceOpen])
  const current = sections.find(m => p.time >= m.start && p.time < m.end) ?? sections[sections.length - 1]
  const selected = sections.find(m => m.parts.some(s => s.id === p.selected?.id))
  const section = selected ?? current
  const source = song.sources.find(s => s.id === p.sourceId)
  const changeReading = p.change ? energyDescription(p.change, song) : null
  const previous = section && sections[sections.indexOf(section) - 1]
  const other = sections.find(m => m.id === compareId && m.id !== section?.id)
    ?? (selected && previous?.role === section?.role ? previous : undefined)
  const reference = other ?? previous
  useEffect(() => {
    if (!reference || !section) { setComparisonDetail(null); setComparisonLoading(false); return }
    const keys = Object.keys(song.energy.weights).filter(key => song.energy.components[key])
    const names = keys.map(key => `energy/${key}`)
    let active = true
    setComparisonLoading(true)
    setComparisonDetail(null)
    Promise.all([
      api.curves(song.id, names, reference.start, reference.end, 128),
      api.curves(song.id, names, section.start, section.end, 128),
    ]).then(([a, b]) => {
      if (!active) return
      const deltas: Record<string, number> = {}
      for (const key of keys) {
        const before = a.series[`energy/${key}`]
        const after = b.series[`energy/${key}`]
        if (!before?.error && !after?.error && Number.isFinite(before?.mean) && Number.isFinite(after?.mean)) {
          deltas[key] = after.mean - before.mean
        }
      }
      // Section comparison already names a clear width change above; repeating
      // the same observation under a second heading adds no understanding.
      if (Math.abs(sectionValue(reference, 'stereo_width') - sectionValue(section, 'stereo_width')) > .04) {
        delete deltas.stereo_width
      }
      setComparisonDetail(comparisonSignals(deltas, song.energy.weights))
    }).catch(() => { if (active) setComparisonDetail([]) })
      .finally(() => { if (active) setComparisonLoading(false) })
    return () => { active = false }
  }, [song.id, song.energy, reference?.id, reference?.start, reference?.end, section?.id, section?.start, section?.end])
  const participationLabel = (fraction: number) => fraction > .85 ? 'Throughout' : fraction > .1 ? 'In places' : fraction > 0 ? 'Briefly' : 'Absent'
  const comparisonParts = other && section ? song.sources.filter(s => !s.is_silent && s.id !== 'other' && participationLabel(participation(s, other)) !== participationLabel(participation(s, section))) : []
  const active = song.sources.filter(s => !s.is_silent && s.segments.some(r => p.time >= r.start && p.time < r.end))
  const highlights = useMemo(() => {
    const ranked = [...song.energy_changes].sort((a,b) => b.magnitude - a.magnitude)
    const endBuffer = Math.max(12, song.duration * .08)
    return [ranked.find(c => c.direction === 'rise' && c.t > 8 && c.t < song.duration - endBuffer), ranked.find(c => c.direction === 'fall' && c.t > 8 && c.t < song.duration - endBuffer)].filter((c): c is EnergyChange => !!c)
  }, [song])
  const changeBoundary = p.change && sections.findIndex((m, i) => i > 0 && Math.abs(m.start - p.change!.t) < 2)
  const changeLocation = changeBoundary && changeBoundary > 0
    ? `${sections[changeBoundary - 1].label} → ${sections[changeBoundary].label}`
    : sections.find(m => p.change && p.change.t >= m.start && p.change.t < m.end)?.label
  const pick = (m: MacroSection) => { p.onSection(m.parts[0]); p.onSeek(m.start); setCompareId('') }
  const repeats = section ? sections.filter(m => m.id !== section.id && (m.role === section.role || m.parts.some(a => section.parts.some(b => a.repeat_group != null && a.repeat_group >= 0 && a.repeat_group === b.repeat_group)))) : []
  return <>
    <div className="song-location">
      <span className="location-name">{current?.label ?? 'Song'}</span>
      <span className="location-time">{fmtTime(p.time, false)}</span>
      <span className="location-playing">{active.length ? 'Detected here' : 'In this passage'}</span>
      <div className="playing-parts">{(active.length ? active : song.sources.filter(s => current?.sources.includes(s.id) && !s.is_silent)).map(s => <button key={s.id} onClick={() => p.onSource(s.id)}><i style={{background:s.color}} />{sourceName(s)}</button>)}</div>
    </div>
    <div className={`study-panel${other && !comparisonParts.length ? ' study-panel-wide' : ''}`}>
      <div className="study-main">
        <div className="study-eyebrow">{source ? 'Instrument' : p.change ? 'Energy change' : p.event ? 'What changed' : selected ? 'Selected passage' : 'At the playhead'}</div>
        <div className="study-heading"><h2>{source ? sourceName(source) : p.change ? energyHeadline(p.change) : p.event ? p.event.label : other && section ? `${section.label} vs ${other.label}` : section?.label}</h2>
          {(source || p.change || p.event || selected) && <button className="ghost" onClick={() => { p.onClear(); setCompareId('') }}>Back to song</button>}</div>
        {source ? <>
          <p>{source.is_silent ? 'No clear part was detected.' : source.segments.length ? <>First heard at <button className="text-link" onClick={() => p.onSeek(source.segments[0].start)}>{fmtTime(source.segments[0].start)}</button>. {source.segments.length === 1 ? source.total_active_seconds < 8 ? 'One brief passage in the arrangement.' : 'One passage in the arrangement.' : source.duty_cycle > .85 ? 'A foundation through most of the song.' : 'Comes and goes through the arrangement.'}</> : 'No activity was detected.'}</p>
          <div className="study-actions"><button onClick={() => p.onSolo(source.id)}>{p.solo.has(source.id) ? 'Stop solo' : 'Listen in isolation'}</button><button onClick={p.onEvidence}>Instrument details</button></div>
          <div className="participation" aria-label={`${sourceName(source)} by section`}>{sections.map(m => <button key={m.id} onClick={() => p.onSeek(m.start)} title={`${m.label}: active for ${Math.round(participation(source,m)*100)}% of the passage`}><span>{m.label}</span><i style={{background:source.color, width:`${participation(source,m) > 0 ? Math.max(2,participation(source,m)*100) : 0}%`}} /></button>)}</div>
        </> : p.change ? <><p>{changeReading?.measured}</p>{changeReading?.suggested && <p className="dim">{changeReading.suggested}</p>}<p className="dim">{fmtTime(p.change.t)} · {changeLocation}</p><div className="study-actions"><button onClick={() => p.onListen(Math.max(0,p.change!.t-3))}>Listen to the lead-in</button><button onClick={() => setEnergyEvidenceOpen(open => !open)}>{energyEvidenceOpen ? 'Hide evidence' : 'Show evidence'}</button></div>
          {energyEvidenceOpen && <div className="energy-evidence" ref={energyEvidenceRef}>
            <h3>{p.change.direction === 'rise' ? 'What rose in the mix' : 'What fell in the mix'}</h3>
            <p className="dim">The energy score changes by {Math.round(Math.abs(p.change.delta) * 100)} points on this song’s scale. These signals move around the change:</p>
            <ul>{energySignals(p.change).map(signal => <li key={signal.label}><span>{signal.label}</span><strong>{signal.movement}</strong>{signal.inferred && <small>estimated from separated parts</small>}</li>)}</ul>
            <p className="small dim">These are measured changes in the recording. Instrument entrances and exits are estimates from separated audio; listen to confirm them.</p>
            <button className="text-link" onClick={p.onEvidence}>Explore all energy changes</button>
          </div>}
        </>
        : p.event ? <><p>{p.event.why || `${names(song,p.event.source_ids) || 'The mix'} changes at ${fmtTime(p.event.t)}.`}</p><div className="study-actions"><button onClick={() => p.onListen(Math.max(0,p.event!.t-3))}>Listen to the lead-in</button><button onClick={p.onEvidence}>Show evidence</button></div></>
        : section ? <>
          <p className="section-time">{fmtTime(section.start,false)}–{fmtTime(section.end,false)} · {names(song,section.sources) || 'No main parts detected'}</p>
          {reference && <p className="comparison-lede">In <strong>{section.label} ({fmtTime(section.start,false)})</strong>, compared with <strong>{reference.label} ({fmtTime(reference.start,false)})</strong>: {comparisonLoading ? 'comparing the sound…' : comparisonDetail?.length ? `${comparisonDetail.join('; ')}.` : 'no strong difference in the measured sound.'}</p>}
          <div className="comparison-heading"><h3>{reference ? `In ${section.label}, compared with ${reference.label}` : 'The opening'}</h3>
            <label>Compare with <select aria-label="Compare section with" value={other?.id ?? ''} onChange={e => setCompareId(e.target.value)}><option value="">Previous section</option>{sections.filter(m=>m.id!==section.id).map(m=><option key={m.id} value={m.id}>{m.label} · {fmtTime(m.start,false)}</option>)}</select></label></div>
          <ul className="musical-changes">{(other ? sectionChanges(song,other,section) : previous ? sectionChanges(song,previous,section) : [openingDescription(song,section)]).map(line=><li key={line}>{line}</li>)}</ul>
          {other && <div className="compare-listen"><button onClick={()=>p.onListen(other.start)}>Listen: {other.label}</button><button onClick={()=>p.onListen(section.start)}>Listen: {section.label}</button></div>}
          <div className="study-actions"><button onClick={() => p.onSeek(section.start)}>Go to start</button><button onClick={() => p.onZoom(section.start,section.end)}>Zoom into passage</button><button className="ghost" onClick={p.onEvidence}>Details & evidence</button></div>
        </> : null}
      </div>
      {(!other || comparisonParts.length > 0) && <div className="study-next"><h3>{other && section ? 'Instrument presence' : 'Places to explore'}</h3>
        {other && section ? comparisonParts.length ? <div className="compare-instruments"><div className="compare-columns"><span>Part</span><span>{other.label}</span><span>{section.label}</span></div>{comparisonParts.map(s=><div className="compare-columns" key={s.id}><button className="text-link" onClick={()=>p.onSource(s.id)}>{sourceName(s)}</button>{[other,section].map(m=><span key={m.id}>{participationLabel(participation(s,m))}</span>)}</div>)}</div>
          : <p className="small dim">No clear entrance or exit separates these passages in the detected parts.</p>
        : <>{highlights.map(c=><button className="explore-row" key={`${c.t}-${c.direction}`} onClick={()=>p.onChange(c)}><span>{c.direction==='rise'?'Biggest energy lift':'Biggest energy drop within the song'}</span><time>{fmtTime(c.t,false)} →</time></button>)}
          {repeats.slice(0,2).map(m=><button className="explore-row" key={m.id} onClick={()=>{ if (!selected) p.onSection(section!.parts[0]); setCompareId(m.id) }}><span>Compare with {m.label}</span><time>{fmtTime(m.start,false)} →</time></button>)}
          <details className="section-directory"><summary>All {sections.length} sections</summary>{sections.map(m=><button className="explore-row" key={m.id} onClick={()=>pick(m)}><span>{m.label}</span><time>{fmtTime(m.start,false)}</time></button>)}</details>
        </>}
      </div>}
    </div>
  </>
}
