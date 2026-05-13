/**
 * The inspector: everything about *what* the system believes and why.
 *
 * Progressive disclosure is the organising principle. Nothing here is a wall
 * of charts — each panel answers one question, and the technical evidence is
 * one click away from any statement the system makes.
 */
import { useState } from 'react'
import type {
  ArrangementEvent, Claim, Evidence, Insight, PatternFinding, Section,
  SongSummary, Source,
} from '../types'
import {
  EVENT_COLORS, FAMILY_LABEL, componentLabel, eventKindLabel, fmtBar, fmtDb, fmtHz, fmtTime,
  groundingColor, groundingLabel, num, panLabel, pct, titleCase,
} from '../lib/format'
import { macroSections } from '../lib/sections'

export function EvidenceList({ items, compact }: { items: Evidence[]; compact?: boolean }) {
  if (!items.length) return null
  return (
    <>
      {items.slice(0, compact ? 1 : 8).map((e, i) => (
        <div className="evidence" key={i}>
          <div className="em">
            {e.method}{e.metric ? ` · ${e.metric}` : ''}
            {' '}· <span style={{ color: e.strength === 'direct' ? 'var(--ok)' : 'var(--fg-faint)' }}>
              {e.strength}
            </span>
          </div>
          {e.note && <div className="en">{e.note}</div>}
          <div className="en mono tiny faint">
            {e.value !== undefined && e.value !== null && (
              <>value {typeof e.value === 'number' ? Number(e.value).toPrecision(4) : String(e.value)}{e.unit ? ` ${e.unit}` : ''} · </>
            )}
            {e.t0 !== null && e.t0 !== undefined && (
              <>{e.t1 ? `${fmtTime(e.t0)}–${fmtTime(e.t1)}` : fmtTime(e.t0)} · </>
            )}
            {e.unit ?? ''}
          </div>
        </div>
      ))}
    </>
  )
}

export function ClaimList({ claims, onJump }: { claims: Claim[]; onJump?: (t: number) => void }) {
  if (!claims.length) return <div className="faint small">No claims returned.</div>
  return (
    <div>
      {claims.map((c, i) => (
        <div className={`claim ${c.grounding}`} key={i}>
          <div className="ctext">
            {c.time_refs.length > 0 && onJump ? (
              <button
                className="ghost tiny mono"
                style={{ color: 'var(--accent)', padding: 0 }}
                onClick={() => onJump(c.time_refs[0])}
                title="jump to this time"
              >
                {fmtTime(c.time_refs[0])}
              </button>
            ) : null}{' '}
            {c.text}
          </div>
          <div className="cmeta">
            <span className="tag" style={{ color: groundingColor(c.grounding), borderColor: groundingColor(c.grounding) }}>
              {groundingLabel(c.grounding)}
            </span>
            <span className="tag">{c.kind}</span>
            <span className="tag">{c.confidence}</span>
            {c.source_refs.map((s) => <span className="tag" key={s}>{s}</span>)}
            {c.event_refs.map((e) => (
              <span className="tag" key={e}>@{e}</span>
            ))}
          </div>
          {c.grounding_note && (
            <div className="cev" style={{ color: groundingColor(c.grounding) }}>
              ⚠ {c.grounding_note}
            </div>
          )}
          {c.evidence.length > 0 && <EvidenceList items={c.evidence} compact />}
        </div>
      ))}
    </div>
  )
}

export function InsightPanel({ insight, onJump }: { insight: Insight; onJump: (t: number) => void }) {
  return (
    <div className="card">
      <h3>{insight.title}</h3>
      <div className="row tiny faint" style={{ marginBottom: 6 }}>
        <span>{insight.model}</span>
        <span>·</span>
        <span>{num(insight.duration_seconds, 1)}s</span>
        {insight.token_usage && (
          <>
            <span>·</span>
            <span>{insight.token_usage.prompt}→{insight.token_usage.completion} tok</span>
          </>
        )}
      </div>
      <ClaimList claims={insight.claims} onJump={onJump} />
    </div>
  )
}

// ---------------------------------------------------------------------------


/**
 * A single labelled number, for the two or three figures worth showing inline
 * before the user has clicked anything.
 */
export function EventDetail({
  event, song, onExplain, busy, onSelectSource,
}: {
  event: ArrangementEvent
  song: SongSummary
  onExplain: () => void
  busy: boolean
  onSelectSource: (id: string) => void
}) {
  const srcs = event.source_ids.map((id) => song.sources.find((s) => s.id === id)).filter(Boolean) as Source[]
  return (
    <div className="card">
      <h3 style={{ color: EVENT_COLORS[event.kind], textTransform: 'none', fontSize: 13 }}>
        {event.label}
      </h3>
      <div className="row wrap tiny faint" style={{ marginBottom: 6 }}>
        <span className="mono">{fmtTime(event.t)}</span>
        {event.end != null && <span className="mono">→ {fmtTime(event.end)}</span>}
        <span className="mono">{fmtBar(event.t, song.grid.bpm, song.grid.meter, song.grid.offset)}</span>
        <span className="tag">{eventKindLabel(event.kind)}</span>
        {event.is_continuous && <span className="tag">continuous</span>}
      </div>

      <details className="technical-evidence"><summary>Detector scores</summary>
        <p className="tiny faint">These help rank events from the same detector. They are not probabilities that the event label is correct.</p>
        <dl className="kv"><dt>strength</dt><dd>{num(event.strength)}</dd><dt>detector score</dt><dd>{num(event.confidence)}</dd></dl>
      </details>

      {srcs.length > 0 && (
        <>
          <h4>Elements involved</h4>
          <div className="row wrap" style={{ gap: 4 }}>
            {srcs.map((s) => (
              <span key={s.id} className="chip" onClick={() => onSelectSource(s.id)}
                style={{ background: s.color, color: '#08121a' }}>{s.label}</span>
            ))}
          </div>
        </>
      )}

      <h4>Why the system believes this</h4>
      <EvidenceList items={event.evidence} />
      <button className="primary" style={{ width: '100%', marginTop: 8 }} onClick={onExplain} disabled={busy}>
        {busy ? 'thinking…' : 'Explain this event (DeepSeek)'}
      </button>
    </div>
  )
}

export function SectionDetail({
  section, song, onCompare, busy, onJump,
}: {
  section: Section
  song: SongSummary
  onCompare: (withSection: Section) => void
  busy: boolean
  onJump: (t: number) => void
}) {
  const group = song.sections.filter((s) => s.repeat_group === section.repeat_group
    && s.repeat_group !== null && s.repeat_group >= 0)
  return (
    <div className="card">
      <h3 style={{ textTransform: 'none', fontSize: 13 }}>
        {titleCase(section.label)} <span className="faint mono small">{section.id}</span>
      </h3>
      <div className="row wrap tiny faint" style={{ marginBottom: 6 }}>
        <button className="ghost tiny mono" style={{ color: 'var(--accent)', padding: 0 }}
          onClick={() => onJump(section.start)}>
          {fmtTime(section.start)} – {fmtTime(section.end)}
        </button>
        <span className="mono">{num(section.end - section.start, 1)}s</span>
        {section.bar_start !== null && (
          <span className="mono">bar {section.bar_start + 1}–{(section.bar_end ?? 0) + 1}</span>
        )}
      </div>

      <dl className="kv">
        {/* Every 0-1 score is stated as the share of full scale it is, because
            a bare decimal with no unit is a number nobody can reason about.
            Correlation is the exception: it is conventionally signed and
            unbounded-looking, so it stays as it is. */}
        <dt>energy score</dt><dd>{Math.round(section.energy_perceived * 100)} / 100</dd>
        <dt>raw energy score</dt><dd>{Math.round(section.energy_raw * 100)} / 100</dd>
        <dt>sources playing</dt><dd>{section.source_count}</dd>
        <dt>stereo width</dt><dd>{pct(section.stereo_width, 1)}</dd>
        <dt>correlation</dt><dd>{num(section.stereo_correlation, 2)}</dd>
      </dl>
      <details className="technical-evidence"><summary>How the name was suggested</summary>
        {section.label_source === 'model' ? (
          <p className="tiny faint">All-In-One MLX suggested this name from the audio. It does not provide a reliability score for individual names.</p>
        ) : (
          <>
            <p className="tiny faint">The score is a ranking, not the chance that this section name is right.</p>
            <dl className="kv"><dt>source</dt><dd>{section.label_source}</dd><dt>ranking score</dt><dd>{num(section.label_confidence)}</dd></dl>
          </>
        )}
      </details>

      {section.deltas_from_previous.length > 0 && (
        <>
          <h4>Changes from the previous section</h4>
          <ul className="small dim" style={{ margin: 0, paddingLeft: 16 }}>
            {section.deltas_from_previous.map((d, i) => <li key={i}>{d}</li>)}
          </ul>
        </>
      )}

      <h4>Sources present</h4>
      <div className="row wrap" style={{ gap: 4 }}>
        {section.source_ids.length === 0 && <span className="faint small">none detected</span>}
        {section.source_ids.map((id) => {
          const s = song.sources.find((x) => x.id === id)
          return (
            <span key={id} className="chip" style={{ background: s?.color ?? '#555', color: '#08121a' }}>
              {s?.label ?? id}
            </span>
          )
        })}
      </div>

      {(section.label_alternatives?.length ?? 0) > 0 && (
        <>
          <h4>Also considered</h4>
          <div className="row wrap" style={{ gap: 4, marginBottom: 2 }}>
            {section.label_alternatives.map((alt) => (
              <span key={alt.label} className="chip outline">
                {alt.label}
              </span>
            ))}
          </div>
          <div className="tiny faint" style={{ marginTop: 3 }}>
            {section.label_source === 'llm'
              ? 'Weighted by how far this passage actually sits from the other sections.'
              : 'Suggestions based on measured section characteristics.'}
          </div>
        </>
      )}

      {group.length > 1 && (
        <>
          <h4>Repeats of this passage ({group.length})</h4>
          <div className="row wrap" style={{ gap: 4 }}>
            {group.map((s) => (
              <span key={s.id} className="chip" onClick={() => onJump(s.start)}>
                #{s.repeat_rank + 1} · {fmtTime(s.start)}
              </span>
            ))}
          </div>
          {group.filter((s) => s.id !== section.id).map((s) => (
            <button key={s.id} className="tiny" style={{ marginTop: 6, width: '100%' }}
              onClick={() => onCompare(s)} disabled={busy}>
              {busy ? 'comparing…' : `Compare with ${fmtTime(s.start)}`}
            </button>
          ))}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------


/**
 * What is this record, and where does it change.
 *
 * The four tabs are the three depths a producer works at — the song, the
 * instruments in it, and the notes on it — plus the measurements, which are one
 * click away because they are evidence rather than headline.
 */
/**
 * The form of the record, as a producer would describe it.
 *
 * Every section with its bar number, its length, and the instruments actually in
 * it. That list is the thing someone reverse-engineering an arrangement wants to
 * write down, and it was previously only available as coloured bands on the
 * timeline and a paragraph of prose about how naming works.
 *
 * Underneath: the largest energy changes and what caused them. The attribution
 * already existed — the analysis records which components moved and by how much
 * — and it answers "where does this get bigger" better than a curve does,
 * because it does not ask the reader to correlate four traces by eye.
 */
function SongShape({
  song, selectedSectionId, onPickSection,
}: {
  song: SongSummary
  selectedSectionId?: string | null
  onPickSection?: (s: Section) => void
}) {
  const macros = macroSections(song)
  const { bpm, meter, offset } = song.grid
  const colour = new Map(song.sources.map((s) => [s.id, s.color]))

  // The changes worth a line of their own. Everything else is in the energy tab.
  const changes = [...(song.energy_changes ?? [])]
    .filter(c => c.t > 8 && c.t < song.duration - Math.max(12, song.duration * .08))
    .sort((a, b) => (b.magnitude ?? Math.abs(b.delta)) - (a.magnitude ?? Math.abs(a.delta)))
    .slice(0, 6)

  return (
    <>
      <h4>Form</h4>
      <ol className="form-list">
        {macros.map((m, i) => {
          const prev = i > 0 ? macros[i - 1] : null
          const step = prev ? m.energy - prev.energy : 0
          return (
            <li
              key={m.id}
              className={`form-row ${m.role}`
                + (selectedSectionId === m.parts[0].id ? ' sel' : '')}
              /* A list of sections is a navigation control. This one had
                 `cursor: default` and no handler, so clicking "Breakdown" did
                 nothing while clicking the same section on the timeline selected
                 it. A macro row covers several detected segments, so it selects
                 the first and says so rather than pretending otherwise. */
              onClick={() => onPickSection?.(m.parts[0])}
              tabIndex={onPickSection ? 0 : -1}
              role={onPickSection ? 'button' : undefined}
              onKeyDown={(e) => {
                if (onPickSection && (e.key === 'Enter' || e.key === ' ')) {
                  e.preventDefault()
                  onPickSection(m.parts[0])
                }
              }}
              title={m.parts.length > 1
                ? `${m.label} — ${m.parts.length} detected segments; jumps to the first`
                : `${m.label} — ${fmtTime(m.start)}`}
            >
              <span className="frole" aria-hidden />
              <span className="fname">{m.label}</span>
              <span className="fbar faint mono">{fmtBar(m.start, bpm, meter, offset)}</span>
              <span className="fdur faint mono">{fmtTime(m.end - m.start, false)}</span>
              {prev && Math.abs(step) > 0.04 && (
                <span
                  className="fstep mono"
                  style={{ color: step > 0 ? 'var(--ok)' : 'var(--fg-faint)' }}
                  title={`Song-relative energy score ${step > 0 ? 'up' : 'down'} ${Math.round(Math.abs(step) * 100)} points from the previous section`}
                >
                  {step > 0 ? '▲' : '▼'} {Math.round(Math.abs(step) * 100)} pts
                </span>
              )}
              <span className="fsrc">
                {m.sources.map((id) => (
                  <i
                    key={id}
                    className="dot"
                    style={{ background: colour.get(id) ?? '#888' }}
                    title={id}
                  />
                ))}
              </span>
            </li>
          )
        })}
      </ol>
      <div className="tiny faint" style={{ marginTop: 4 }}>
        Bars from the detected downbeat. A section that changes texture inside
        itself is one row here; the internal change is on the timeline.
      </div>

      {changes.length > 0 && (
        <>
          <h4 style={{ marginTop: 14 }}>Biggest changes</h4>
          <div className="tiny faint" style={{ marginBottom: 4 }}>
            How much the arrangement energy moved, and which measurements moved
            with it.
          </div>
          <ul className="change-list">
            {changes.map((c, i) => {
              // The contributor values are z-scores — standard deviations of each
              // component's own distribution — not fractions. `bass_activity: 2.43`
              // means bass moved two and a half standard deviations, and rendering
              // that as "bass +243%" stated a number that is simply wrong: it read
              // as a tripling when it is an ordinary large move. There is no
              // honest percentage to show here, so none is shown. The names are
              // ordered by how far each moved and the top one is named, which is
              // the part a producer acts on: *what* drove this, not by how much
              // in units they have never been told about.
              const top = (c.contributors ?? []).slice(0, 2)
              return (
                <li key={i} className={c.direction}>
                  <span className="ct">{fmtBar(c.t, bpm, meter, offset)}</span>
                  <span className="cd">
                    {c.direction === 'rise' ? '▲' : '▼'} {Math.round(Math.abs(c.delta) * 100)} points
                  </span>
                  <span className="cc">
                    {top.length
                      ? top.map(([name]) => componentLabel(name)).join(' and ')
                      : 'level moved'}
                  </span>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </>
  )
}

/**
 * The range the local pulse covers, in words.
 *
 * This was `tempo drift: 89.6 BPM` sitting directly under `tempo: 136.36 BPM`,
 * which reads as a number contradicting the one above it. It is not a drift: it
 * is the span of the local pulse — the same 90 BPM the caveat describes as a
 * 5-95% range. On "DNA." the pulse curve really does run 130 to 250 BPM, because
 * the metre changes at 1:50, so the claim is true and the old label made it sound
 * like an error in the tempo estimate.
 *
 * Naming the two ends says what varies. A curve too short to range falls back to
 * the width, or to "steady", rather than inventing endpoints from a single value.
 */
/** A number with its sign, always: -0.13, +0.10, 0.00. */
function fmtSigned(v: number): string {
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}`
}

function pulseRange(
  curve: [number, number][] | null | undefined,
  bpm: number,
  drift: number | null,
): string {
  const d = drift ?? 0
  let lo = Infinity
  let hi = -Infinity
  for (const [, v] of curve ?? []) {
    if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v) }
  }
  if (Number.isFinite(lo) && (curve?.length ?? 0) >= 4 && hi - lo >= 1) {
    return `${num(lo, 0)}-${num(hi, 0)} BPM across the record`
  }
  if (d > 1) return `varies by about ${num(d, 0)} BPM`
  return `steady at ${num(bpm, 1)} BPM`
}

export function SongOverview({
  song, selectedSectionId, onPickSection,
}: {
  song: SongSummary
  selectedSectionId?: string | null
  onPickSection?: (s: Section) => void
}) {
  const [tab, setTab] = useState<'song' | 'instruments' | 'measurements'>('song')
  const fam = new Map<string, Source[]>()
  for (const s of song.sources) {
    const arr = fam.get(s.family) ?? []
    arr.push(s)
    fam.set(s.family, arr)
  }
  return (
    <div className="card">
      <div className="seg-ctl" style={{ marginBottom: 10 }}>
        {([
          ['song', 'the song'],
          ['instruments', 'instruments'],
          ['measurements', 'measurements'],
        ] as const).map(([k, label]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === 'song' && (
        <SongShape
          song={song}
          selectedSectionId={selectedSectionId}
          onPickSection={onPickSection}
        />
      )}

      {tab === 'measurements' && (
        <>
          <h4>Track</h4>
          <dl className="kv">
            <dt>duration</dt><dd>{fmtTime(song.duration)}</dd>
            <dt>source</dt><dd>{String(song.source_quality?.container ?? '?')}/{String(song.source_quality?.codec ?? '?')}</dd>
            <dt>native rate</dt><dd>{String(song.source_quality?.native_sample_rate ?? '?')} Hz</dd>
            <dt>bit depth</dt><dd>{String(song.source_quality?.bits_per_sample ?? '?')}</dd>
            <dt>lossy</dt><dd>{song.source_quality?.lossy ? 'yes' : 'no'}</dd>
          </dl>
          <h4>Tempo &amp; metre</h4>
          <dl className="kv">
            <dt>tempo</dt><dd>{num(song.grid.bpm, 2)} BPM</dd>
            <dt>confidence</dt><dd>{pct(song.grid.bpm_confidence)}</dd>
            <dt>metre</dt><dd>{song.grid.meter} ({pct(song.grid.meter_confidence)})</dd>
            <dt>first downbeat</dt><dd>{fmtTime(song.grid.offset)}</dd>
            <dt>tempo drift</dt><dd>{num(song.grid.tempo_drift, 1)} BPM</dd>
            <dt>beats found</dt><dd>{song.beats.onset_times.length ? song.grid.beats.length : 0}</dd>
          </dl>
          {song.grid.is_tangent && (
            <div className="warn-box" style={{ marginTop: 6 }}>
              Irregular pulse detected — bar numbers are approximate. Bar-referenced
              claims from the reasoning model should be read with that in mind.
            </div>
          )}
          <h4>Loudness</h4>
          <dl className="kv">
            <dt>integrated</dt><dd>{fmtDb(song.loudness.integrated_lufs)} LUFS</dd>
            <dt>range (LRA)</dt><dd>{fmtDb(song.loudness.lra)} LU</dd>
            <dt>true peak</dt><dd>{fmtDb(song.loudness.true_peak_dbfs)}</dd>
            <dt>crest</dt><dd>{fmtDb(song.loudness.crest_db)}</dd>
            <dt>method</dt><dd style={{ fontSize: 10 }}>{song.loudness.method}</dd>
          </dl>
          <h4>Stereo image</h4>
          {song.spatial_summary.is_mono ? (
            <div className="warn-box">
              This file is mono. Spatial values are undefined rather than measured on
              duplicated channels.
            </div>
          ) : (
            <dl className="kv">
              <dt>mean pan</dt><dd>{panLabel(song.spatial_summary.mean_pan ?? 0)}</dd>
              {/* The bands, not just the broadband mean.

                  This panel said "width (side/mid) 0.051" while the timeline's
                  stereo-width row drew three bands reading 0.047 / 0.137 /
                  0.109 on the same track. Two numbers for one idea, no
                  explanation, and the mid band nearly three times the headline —
                  so a reader comparing them concludes one of them is broken.

                  The broadband mean is still here and still labelled as a mean,
                  because it is a real number; the bands come first because they
                  are what the timeline shows and what the "high band is wider
                  than the low" caveat above is talking about. Reporting the same
                  thing twice with the same words is the only way they can be
                  compared. */}
              {song.spatial_summary.bands.map((b) => (
                <span key={b.key} style={{ display: 'contents' }}>
                  <dt>width, {b.label} ({fmtHz(b.lo_hz)}-{fmtHz(b.hi_hz)})</dt>
                  <dd>{num(song.spatial_summary[b.key] ?? 0, 3)}</dd>
                </span>
              ))}
              <dt>width, whole spectrum</dt>
              <dd>{num(song.spatial_summary.mean_width ?? 0, 3)}</dd>
              <dt>correlation</dt><dd>{num(song.spatial_summary.mean_correlation ?? 0, 3)}</dd>
            </dl>
          )}
        </>
      )}

      {tab === 'instruments' && (
        <>
          {[...fam.entries()].map(([f, srcs]) => (
            <div key={f} style={{ marginBottom: 10 }}>
              <h4>{FAMILY_LABEL[f] ?? titleCase(f)}</h4>
              {srcs.map((s) => (
                <SourceRow key={s.id} s={s} song={song} />
              ))}
            </div>
          ))}
        </>
      )}

      {tab === 'measurements' && (
        <>
          <h4>Models used</h4>
          <dl className="kv">
            {Object.entries(song.models_used).map(([k, v]) => (
              <span key={k} style={{ display: 'contents' }}>
                <dt>{k}</dt><dd style={{ fontSize: 10 }}>{v}</dd>
              </span>
            ))}
          </dl>
          <h4>Energy model</h4>
          <div className="tiny faint" style={{ lineHeight: 1.5 }}>{song.energy.model_notes}</div>
          <div className="tiny faint" style={{ margin: '8px 0' }}>
            This is a score relative to this song, not a measure of loudness or a percentage of physical energy.
          </div>
          <dl className="kv">
            {Object.entries(song.energy.weights)
              .sort((a, b) => b[1] - a[1])
              .map(([name, weight]) => {
                const total = Object.values(song.energy.weights).reduce((sum, value) => sum + value, 0)
                const label: Record<string, string> = {
                  loudness: 'Loudness', active_layers: 'Active parts',
                  onset_density: 'Rhythmic activity', bass_activity: 'Low-end activity',
                  high_activity: 'High-frequency activity', percussive_energy: 'Attacks and transients',
                  hp_ratio: 'Percussive balance', band_spread: 'Frequency coverage',
                  stereo_width: 'Stereo width',
                }
                return <span key={name} style={{ display: 'contents' }}>
                  <dt title={song.energy.components[name]?.description ?? ''}>{label[name] ?? componentLabel(name)}</dt>
                  <dd>{Math.round(weight / Math.max(total, 1e-9) * 100)}%</dd>
                </span>
              })}
          </dl>
          <div className="tiny faint">Loudness, spectrum, transients and width come from the recording. “Active parts” comes from separated stems and can be less reliable.</div>
          <h4>Fingerprint</h4>
          <dl className="kv">
            {Object.entries(song.fingerprint).map(([k, v]) => (
              <span key={k} style={{ display: 'contents' }}>
                <dt>{k}</dt>
                <dd style={{ fontSize: 10 }}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
              </span>
            ))}
          </dl>
          <h4>Pipeline</h4>
          {song.stages.map((st) => (
            <div className="stage-row" key={st.name}>
              <span className="sdot" />
              <span className="sname">{st.name}</span>
              <span className="sstat">
                {st.status}{st.duration ? ` ${num(st.duration, 1)}s` : ''}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  )
}

function SourceRow({ s, song }: { s: Source; song: SongSummary }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <div className="src-item" onClick={() => setOpen(!open)}>
        <span className="swatch" style={{ background: s.color }} />
        <span className="lbl">{s.label}</span>
        <span className="cnt">{s.segments.length} spans</span>
        <span className="cnt">{fmtDb(s.mean_level_db, 0)}</span>
      </div>
      {open && (
        <div style={{ padding: '2px 0 8px 18px' }}>
          <div className="tiny dim">
            {s.detection_method} · activity contrast score {num(s.confidence, 2)} (not an identification probability)
          </div>
          <dl className="kv tiny" style={{ marginTop: 4 }}>
            <dt>descriptor</dt><dd>{s.descriptor.role ?? '—'}</dd>
            <dt>timbre</dt><dd>{s.descriptor.descriptors.join(', ') || '—'}</dd>
            <dt>centroid</dt><dd>{fmtHz(s.descriptor.spectral_centroid_hz)} Hz</dd>
            <dt>flatness</dt><dd>{num(s.descriptor.spectral_flatness, 3)}</dd>
            <dt>pan</dt><dd>{panLabel(s.mean_pan)}</dd>
            <dt>width</dt><dd>{num(s.mean_width, 2)}</dd>
            <dt>duty cycle</dt><dd>{pct(s.duty_cycle)}</dd>
            <dt>entries / exits</dt><dd>{s.n_entries} / {s.n_exits}</dd>
          </dl>
          <div className="tiny faint" style={{ marginTop: 4 }}>
            First appearance: {s.segments[0] ? fmtTime(s.segments[0].start) : '—'}
            {s.segments[0] && song.grid.bpm > 0 && (
              <> ({fmtBar(s.segments[0].start, song.grid.bpm, song.grid.meter, song.grid.offset)})</>
            )}
          </div>
          <table className="tbl tiny" style={{ marginTop: 4 }}>
            <thead><tr><th>#</th><th>start</th><th>end</th><th>dur</th><th>level</th></tr></thead>
            <tbody>
              {s.segments.slice(0, 12).map((g, i) => (
                <tr key={i}>
                  <td>{i + 1}</td>
                  <td className="num mono">{fmtTime(g.start)}</td>
                  <td className="num mono">{fmtTime(g.end)}</td>
                  <td className="num mono">{num(g.end - g.start, 1)}s</td>
                  <td className="num mono">{num(g.level_db, 0)} dB</td>
                </tr>
              ))}
              {s.segments.length > 12 && (
                <tr><td colSpan={5} className="faint">+{s.segments.length - 12} more</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------


export function PatternsList({
  patterns, onJump, selected, onSelect,
}: {
  patterns: PatternFinding[]
  onJump: (t: number) => void
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  if (!patterns.length) {
    return <div className="faint small">No structural patterns were detected.</div>
  }

  /*
    Two things were wrong with this list, both visible on "DNA.".

    **The internal kind was printed on every card.** `change_grid`,
    `width_energy`, `repeat_delta` — the schema's names, next to titles that were
    already in plain English. The instruction this product is built on is not to
    expose those; they are what the detection is called, not what was found. The
    tag is gone and nothing replaces it, because the title and the detail already
    say everything the kind was saying.

    **One finding was printed four times.** Every repeat of a passage produced its
    own "Later repeat at 27s / 57s / 84s / 173s differs from its first pass" card,
    all four at strength 0.3, with deltas of -0.11, -0.13, -0.11 and +0.08. Four
    cards of near-noise reads as four findings and buries the two on the list that
    are actually about the record. Repeat deltas are now one card: what the
    repeats do, how many there are, the range, and every timestamp as a chip. The
    information is identical; the impression is not.
  */
  const repeatDeltas = patterns.filter((p) => p.kind === 'repeat_delta')
  const others = patterns.filter((p) => p.kind !== 'repeat_delta')
  // `[+-][\d.]+` swallowed the sentence-ending period, so "energy -0.11."
  // parsed as -0.11. -> NaN and the summary read "from NaN to NaN". The number
  // has to be a number, and a period is not a decimal point at the end of a
  // sentence.
  const deltaValues = repeatDeltas
    .map((p) => {
      const m = /energy\s+([+-]?\d+(?:\.\d+)?)/.exec(p.detail)
      if (!m) return null
      const v = Number(m[1])
      return Number.isFinite(v) ? v : null
    })
    .filter((v): v is number => v !== null)
  const deltaAll = deltaValues.length ? deltaValues : repeatDeltas.map(() => 0.3)

  const grouped = [
    ...others,
    ...(repeatDeltas.length > 1 ? [{
      id: '__repeat_delta_group__',
      kind: 'repeat_delta',
      title: repeatDeltas.length > 1
        ? 'Later repeats differ from the first pass' : repeatDeltas[0].title,
      // The sign belongs to the number being printed. It used to be read off
      // the *last* delta and then applied to the *minimum*, so a set of deltas
      // whose last one was positive printed "energy from +-0.13 to +0.10" — a
      // double sign, from two different numbers.
      detail: deltaValues.length
        ? `${repeatDeltas.length} returns, energy from `
          + `${fmtSigned(Math.min(...deltaAll))} to ${fmtSigned(Math.max(...deltaAll))} `
          + 'against their first time through'
        : `${repeatDeltas.length} returns differ measurably from their first pass`,
      strength: Math.max(...repeatDeltas.map((p) => p.strength)),
      evidence: repeatDeltas.flatMap((p) => p.evidence ?? []),
      times: repeatDeltas.flatMap((p) => p.times ?? []),
    }] : []),
  ]

  return (
    <div>
      {/* The heading lives here, not in the caller.

          The caller wrote `song.patterns.length` into the title while the
          grouping that decides how many rows are drawn lived here, so on
          "DNA." the panel said **6** and showed **3**. Two numbers for one
          list, one of them describing rows the reader cannot see. The count is
          now `grouped.length` — the same array that is mapped below — so it
          cannot drift from the list without the compiler noticing. */}
      <h3>Arrangement patterns ({grouped.length})</h3>
      <div className="tiny faint" style={{ marginBottom: 6 }}>
        Computed from the measurements — these are facts, not opinions.
      </div>
      {grouped.map((p) => (
        <div key={p.id}
          className={`finding ${selected === p.id ? 'sel' : ''}`}
          onClick={() => onSelect(selected === p.id ? null : p.id)}
        >
          <div className="ft">{p.title}</div>
          <div className="fd">{p.detail}</div>
          <div className="row" style={{ marginTop: 5, gap: 6 }}>
            <div className="meter grow">
              <div style={{
                width: `${Math.round(p.strength * 100)}%`,
                background: p.strength > 0.6 ? 'var(--ok)' : p.strength > 0.35 ? 'var(--warn)' : 'var(--fg-faint)',
              }} />
            </div>
          </div>
          {selected === p.id && (
            <>
              <EvidenceList items={p.evidence} compact />
              {p.times.length > 0 && (
                <div className="row wrap" style={{ gap: 4, marginTop: 5 }}>
                  {p.times.slice(0, 12).map((t, i) => (
                    <span key={i} className="chip" onClick={(e) => { e.stopPropagation(); onJump(t) }}>
                      {fmtTime(t)}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      ))}
    </div>
  )
}
