/**
 * The stem mixer: audition separated parts in sync with the timeline.
 *
 * Solo is a *set*. It was a single value, so of six independently drawn
 * buttons exactly one could ever be on: clicking bass after drums
 * silently switched to bass while drums stayed lit, and hearing two parts
 * together meant soloing one, listening, un-soloing it, and soloing the
 * other. Six buttons that look independent now behave independently.
 *
 * This replaces a panel that opened each stem as a WAV in a new browser tab.
 * That "worked" in the sense that audio came out of it, and was useless in
 * every other respect: the tab had its own transport, started at zero, was not
 * sample-locked to the playhead, and left the full mix playing underneath in
 * the original tab. There was no way to hear the bass *at 1:29* and compare it
 * with the section boundary, which is the only reason to solo a stem.
 */
import type { StemFile } from '../types'

export interface MixerProps {
  stems: StemFile[]
  colors: Record<string, string>
  /** Every soloed stem. Clicking a second one adds it; only one could be on before. */
  solo: Set<string>
  loading: Set<string>
  unavailable: Set<string>
  onSolo: (id: string) => void
  onClose: () => void
}

export function StemMixer(props: MixerProps) {
  const { stems, colors, solo, loading, unavailable, onSolo, onClose } = props

  if (!stems.length) {
    return (
      <div className="card mixer">
        <div className="row" style={{ marginBottom: 4 }}>
          <h3 style={{ margin: 0, flex: 1 }}>
            Stems
            <span className="tiny faint" style={{ fontWeight: 400, marginLeft: 8 }}>
              click a part to hear it alone
            </span>
          </h3>
          <button className="ghost tiny" onClick={onClose}>close</button>
        </div>
        <div className="tiny faint">
          No separated audio is available for this track. Stems are produced during
          analysis; if the separator was skipped or failed, re-run it.
        </div>
      </div>
    )
  }

  return (
    <div className="card mixer">
      <div className="row" style={{ marginBottom: 6 }}>
        <h3 style={{ margin: 0, flex: 1 }}>Stems</h3>
        {/*
          No reset button here. There is one, in the transport directly above
          this panel, and it is labelled with what it will stop — "soloing bass +
          drums ✕" — so a second copy a few centimetres away would be two
          controls for one action with the reader having to pick between them.
        */}
        {solo.size > 0 && (
          <span className="tiny faint">{solo.size} of {stems.length} soloed</span>
        )}
        <button className="ghost tiny" onClick={onClose}>close</button>
      </div>

      {/*
        One control, and it does one thing. There was a second `M` per row that
        looked like a mute and was not one: the full mix is a single rendered
        file, so an instrument cannot be removed from it. The state it wrote was
        never read by the audio path at all.
      */}
      <div className="mixer-grid">
        {stems.map((s) => {
          const isSolo = solo.has(s.stem)
          const busy = loading.has(s.stem)
          const dead = unavailable.has(s.stem)
          return (
            <div key={s.stem} className={`mixer-row ${isSolo ? 'solo' : ''}`}>
              <span className="swatch" style={{ background: colors[s.stem] ?? '#888' }} />
              <button
                className={`mixer-name ${isSolo ? 'on' : ''}`}
                onClick={() => onSolo(s.stem)}
                disabled={dead}
                title={
                  dead ? `${s.stem} could not be loaded`
                    : busy ? `loading ${s.stem}…`
                    : isSolo ? `stop soloing ${s.stem}`
                    : `solo ${s.stem} — hear it alone, in sync`
                }
              >
                {busy ? 'loading…' : s.stem}
              </button>
            </div>
          )
        })}
      </div>

      <div className="tiny faint" style={{ marginTop: 6 }}>
        {/*
          This said "solo replaces the mix with that part" while solo was a
          single value, and it is now a set: clicking a second part plays both
          together and the mix stays out of it. Copy that describes the old
          behaviour is worse than no copy, because it is the sentence a reader
          checks their understanding against.
        */}
        <strong>Solo</strong> replaces the mix with the parts you pick, at the
        playhead and in sync — click as many as you want to hear together, against
        the section boundaries. Double-click a lane on the timeline to add it;
        Escape, or the button in the transport, brings the full mix back.
        <br />
        There is no mute, because the full mix is a single rendered file and an
        instrument cannot be taken out of it.
        {stems[0] && <> Stems are {stems[0].model} — a separated estimate, not the original track.</>}
      </div>
    </div>
  )
}
