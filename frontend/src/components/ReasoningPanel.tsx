/**
 * The reasoning panel: DeepSeek commentary with every claim's grounding shown.
 *
 * The design rule here is that uncertainty is *visible*. A claim the system
 * could not tie to a measurement is displayed as ungrounded, in red, with the
 * reason — rather than being quietly dropped or quietly trusted.
 */
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { SongSummary } from '../types'
import { componentLabel, fmtBar, fmtTime, groundingColor, groundingLabel, pct } from '../lib/format'
import { ClaimList } from './Inspector'

export interface ReasoningProps {
  song: SongSummary
  onSongChanged: () => void
  onJump: (t: number) => void
  rangeA: { start: number; end: number } | null
  rangeB: { start: number; end: number } | null
  onExplainWindow: (t0: number, t1: number) => void
  onCompare: (a: [number, number], b: [number, number]) => void
  onExplainEnergy: (index: number) => void
  onExplainEvent: (eventId: string) => void
  llmEnabled: boolean
}

const SUGGESTED = [
  'What is the overall plan of this record?',
  'How is the second half different from the first?',
  'Which elements are used only once, and why does that work?',
  'How does the producer create tension?',
  'What would I need to hear to make the chorus feel bigger?',
  'Is anything in the arrangement unaccounted for?',
]

export function ReasoningPanel(props: ReasoningProps) {
  const {
    song, onSongChanged, onJump, rangeA, rangeB, llmEnabled,
  } = props
  const [busy, setBusy] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  // Keyed by action, not a single string. A shared error rendered in the wrong
  // card is worse than no error: the user reads it as belonging to the control
  // nearest to it, which is the one that just worked.
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [deep, setDeep] = useState(false)
  // Elapsed seconds for the in-flight call, so a 30 s model round-trip shows
  // something that moves. A button reading "…" for thirty seconds is
  // indistinguishable from a dead button, which is how this got reported as
  // "completely broken" in the first place.
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!busy) { setElapsed(0); return }
    const t0 = Date.now()
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 500)
    return () => clearInterval(id)
  }, [busy])

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setErrors((e) => { const n = { ...e }; delete n[key]; return n })
    try {
      await fn()
      onSongChanged()
    } catch (e) {
      const msg = String((e as Error).message ?? e)
      setErrors((prev) => ({ ...prev, [key]: msg }))
    } finally {
      setBusy(null)
    }
  }

  /** What the panel shows while `busy === key`: a spinner, elapsed time, and a
   *  note that this is a model call and not a local computation. */
  const waitFor = (key: string, label: string) =>
    busy === key
      ? <><span className="spin" aria-hidden="true" /> {label} · {elapsed}s</>
      : null

  const songInsight = song.insights.find((i) => i.scope === 'song')
  const questionInsight = song.insights.find((i) => i.scope === 'question')
  const lessonsInsight = song.insights.find((i) => i.scope === 'lessons')
  const windowInsights = song.insights.filter((i) => i.scope.startsWith('window:'))
  const eventInsights = song.insights.filter((i) => i.scope.startsWith('event:'))
  const energyInsights = song.insights.filter((i) => i.scope.startsWith('energy:'))
  const comparisons = song.comparisons

  const allClaims = [
    ...songInsight?.claims ?? [], ...questionInsight?.claims ?? [],
    ...lessonsInsight?.claims ?? [],
    ...windowInsights.flatMap((i) => i.claims),
    ...eventInsights.flatMap((i) => i.claims),
    ...energyInsights.flatMap((i) => i.claims),
  ]
  const verified = allClaims.filter((c) => c.grounding === 'verified').length

  if (!llmEnabled) {
    return (
      <div className="card">
        <h3>Reasoning</h3>
        <div className="warn-box">
          <b>DEEPSEEK_API_KEY is not set.</b> All measurement-based analysis works and
          every panel is populated, but the producer-level commentary is disabled.
          Copy <code>.env.example</code> to <code>.env</code>, add your key, and restart.
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="card">
        <h3>Reasoning</h3>
        <div className="row" style={{ marginBottom: 6 }}>
          <span className="tag">
            {song.llm_enabled ? 'DeepSeek' : 'disabled'}
          </span>
          <label className="row tiny faint" style={{ gap: 4, cursor: 'pointer' }}>
            <input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)}
              style={{ width: 'auto' }} />
            deeper model (slower, more thorough)
          </label>
        </div>
        {allClaims.length > 0 && (
          <div className="tiny dim" style={{ marginBottom: 6 }}>
            {verified} of {allClaims.length} claims are tied to a specific measurement
            ({pct(verified / allClaims.length)}). Ungrounded claims are shown in red
            with the reason.
          </div>
        )}

        <div className="col">
          <button className="primary" disabled={!!busy}
            onClick={() => run('song', () => api.insight(song.id, deep))}>
            {busy === 'song' ? 'analysing…' : songInsight ? 'Update arrangement explanation' : 'Explain the arrangement'}
          </button>
          <button disabled={!!busy || !rangeA}
            onClick={() => rangeA && run('win',
              () => api.insightWindow(song.id, rangeA.start, rangeA.end, deep))}>
            {busy === 'win' ? 'thinking…'
              : rangeA ? `Explain selection A (${fmtTime(rangeA.start)}–${fmtTime(rangeA.end)})`
              : 'Shift-Option-drag the timeline to select A'}
          </button>
          <button disabled={!!busy || !rangeA || !rangeB}
            onClick={() => rangeA && rangeB && run('cmp',
              () => api.compare(song.id, rangeA.start, rangeA.end, rangeB.start, rangeB.end, 'A', 'B', deep))}>
            {busy === 'cmp' ? 'comparing…'
              : rangeA && rangeB ? `Compare A vs B`
              : 'Option-drag for a second range to compare'}
          </button>
          <button disabled={!!busy}
            onClick={() => run('lessons', () => api.lessons(song.id, true))}>
            {busy === 'lessons' ? 'thinking…' : 'What can I learn from this record?'}
          </button>
        </div>
        {errors.song && <div className="warn-box bad" role="alert">{errors.song}</div>}
        {errors.win && <div className="warn-box bad" role="alert">{errors.win}</div>}
        {errors.cmp && <div className="warn-box bad" role="alert">{errors.cmp}</div>}
        {errors.lessons && <div className="warn-box bad" role="alert">{errors.lessons}</div>}
      </div>

      <div className="card">
        <h3>Ask about this song</h3>
        <div className="row" style={{ marginBottom: 5 }}>
          <input
            type="text"
            value={question}
            placeholder="Why does the second chorus feel bigger?"
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && question.trim() && !busy) {
                // The question is cleared by the promise, not before it. Clearing
                // it up front meant a failed request destroyed what the user
                // typed and left an empty box with no way to retry.
                const q = question.trim()
                run('ask', () => api.ask(song.id, q, deep)).then(() => setQuestion(''))
              }
            }}
          />
          <button disabled={!!busy || !question.trim()}
            onClick={() => {
              const q = question.trim()
              run('ask', () => api.ask(song.id, q, deep)).then(() => setQuestion(''))
            }}>
            {busy === 'ask' ? 'asking…' : 'Ask'}
          </button>
        </div>
        {busy === 'ask' && (
          <div className="tiny faint row" style={{ gap: 6, marginBottom: 4 }}>
            <span className="spin" aria-hidden="true" />
            Reading the analysis and drafting an answer — {elapsed}s.
            This is a model call over the measured data, not a lookup.
          </div>
        )}
        {errors.ask && <div className="warn-box bad" role="alert">{errors.ask}</div>}
        {/*
          Full text, no truncation. These used to be cut at 38 characters with no
          tooltip, so the chip read "How is the second half different from …" and
          there was no way to tell what clicking it would actually ask.
        */}
        <div className="row wrap suggest" style={{ gap: 4 }}>
          {SUGGESTED.map((q) => (
            <span key={q} className="chip suggest-chip" role="button" tabIndex={0}
              title={q}
              onClick={() => { setQuestion(q) }}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault(); setQuestion(q) } }}>
              {q}
            </span>
          ))}
        </div>
      </div>

      {songInsight && (
        <InsightCard title="Song overview" model={songInsight.model}
          claims={songInsight.claims} onJump={onJump} />
      )}
      {lessonsInsight && (
        <InsightCard title="Producer lessons" model={lessonsInsight.model}
          claims={lessonsInsight.claims} onJump={onJump} accent />
      )}
      {questionInsight && (
        <InsightCard title={questionInsight.title} model={questionInsight.model}
          claims={questionInsight.claims} onJump={onJump} />
      )}

      {comparisons.length > 0 && comparisons.map((c) => (
        <div className="card" key={c.id}>
          <h3>{c.title}</h3>
          {c.added.length > 0 && (
            <div className="small" style={{ marginBottom: 4 }}>
              <span className="tag ok">only in B</span> {c.added.join(', ')}
            </div>
          )}
          {c.removed.length > 0 && (
            <div className="small" style={{ marginBottom: 4 }}>
              <span className="tag bad">only in A</span> {c.removed.join(', ')}
            </div>
          )}
          {c.level_changes.length > 0 && (
            <table className="tbl">
              <thead><tr><th>metric</th><th>A</th><th>B</th><th>Δ</th></tr></thead>
              <tbody>
                {c.level_changes.map((m, i) => (
                  <tr key={i}>
                    <td>{String(m.metric ?? '').replace(/_/g, ' ')}</td>
                    <td className="num">{String(m.a)}</td>
                    <td className="num">{String(m.b)}</td>
                    <td className="num" style={{
                      color: Number(m.delta) > 0 ? 'var(--ok)' : 'var(--bad)',
                    }}>{Number(m.delta) > 0 ? '+' : ''}{String(m.delta)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div style={{ marginTop: 6 }}>
            <ClaimList claims={c.claims} onJump={onJump} />
          </div>
        </div>
      ))}

      {windowInsights.map((i) => (
        <InsightCard key={i.id} title={i.title} model={i.model} claims={i.claims} onJump={onJump} />
      ))}
      {eventInsights.map((i) => (
        <InsightCard key={i.id} title={i.title} model={i.model} claims={i.claims} onJump={onJump} />
      ))}
      {energyInsights.map((i) => (
        <InsightCard key={i.id} title={i.title} model={i.model} claims={i.claims} onJump={onJump} />
      ))}

      {allClaims.length === 0 && !busy && (
        <div className="card">
          <div className="faint small">
            No commentary yet. The measurement panels above are complete without it —
            the reasoning layer turns them into producer-level interpretation.
          </div>
        </div>
      )}
    </>
  )
}

function InsightCard({ title, model, claims, onJump, accent }: {
  title: string
  model?: string | null
  claims: import('../types').Claim[]
  onJump: (t: number) => void
  accent?: boolean
}) {
  const [open, setOpen] = useState(true)
  return (
    <div className="card" style={accent ? { borderColor: 'var(--accent-2)' } : undefined}>
      <div className="row" style={{ cursor: 'pointer' }} onClick={() => setOpen(!open)}>
        <h3 style={{ margin: 0, flex: 1, textTransform: 'none', fontSize: 12, color: accent ? 'var(--accent-2)' : undefined }}>
          {open ? '▾' : '▸'} {title}
        </h3>
        <span className="tiny faint">{claims.length} claims</span>
      </div>
      {open && (
        <>
          {model && <div className="tiny faint" style={{ margin: '4px 0 6px' }}>{model}</div>}
          <ClaimList claims={claims} onJump={onJump} />
        </>
      )}
    </div>
  )
}

export function EnergyChangeList({
  changes, song, onExplain, onJump, selectedIndex,
}: {
  changes: import('../types').EnergyChange[]
  song: SongSummary
  onExplain: (i: number) => void
  onJump: (t: number) => void
  selectedIndex: number | null
}) {
  if (!changes.length) {
    return <div className="faint small">No significant energy changes detected.</div>
  }
  return (
    /*
      Three things were inconsistent with the timeline and with the overview, all
      showing the same numbers three different ways:

      - the component names came from `n.replace(/_/g, ' ')`, so the energy tab
        said "bass activity" and "hp ratio" while the timeline marker beside the
        same change said "bass" and "dark/bright". Two vocabularies for one
        measurement, in two views of it, one screen apart.
      - the delta is points on a song-relative 0-100 scale, not a percentage
        change in physical energy.
      - contributor magnitudes are derived model effects, not independently
        meaningful units. The bars show their relative size without false precision.
    */
    <table className="tbl">
      <thead>
        <tr><th>at</th><th>change</th><th>driven by</th><th /></tr>
      </thead>
      <tbody>
        {changes.map((c, i) => {
          const top = Math.max(
            0.01,
            ...c.contributors.slice(0, 3).map(([, v]) => Math.abs(v)),
          )
          return (
          <tr key={i} style={{ cursor: 'pointer', background: selectedIndex === i ? 'var(--bg-3)' : undefined }}
            onClick={() => onJump(c.t)}>
            <td className="num mono">
              {fmtTime(c.t)}
              <div className="tiny faint">{fmtBar(c.t, song.grid.bpm, song.grid.meter, song.grid.offset)}</div>
            </td>
            <td className="num" style={{ color: c.direction === 'rise' ? 'var(--ok)' : 'var(--bad)' }}>
              {c.delta > 0 ? '▲ +' : '▼ −'}{Math.round(Math.abs(c.delta) * 100)} points
            </td>
            <td className="tiny dim">
              {c.contributors.slice(0, 3).map(([n, v]) => (
                <span key={n} className="tag" style={{ marginRight: 3 }}>
                  <i
                    className="zbar"
                    style={{
                      background: v > 0 ? 'var(--ok)' : 'var(--bad)',
                      width: `${Math.round((Math.abs(v) / top) * 22)}px`,
                    }}
                  />
                  {componentLabel(n)}
                </span>
              ))}
              {c.contributors.length === 0 && <span className="faint">no dominant contributor</span>}
            </td>
            <td>
              <button className="tiny ghost" onClick={(e) => { e.stopPropagation(); onExplain(i) }}
                title="ask the model why this change happened">
                why
              </button>
            </td>
          </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export { groundingColor, groundingLabel }
