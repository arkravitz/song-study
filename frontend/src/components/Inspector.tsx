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

