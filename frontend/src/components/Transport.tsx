/** Transport bar: play/pause, time, bar position, volume, stem soloing. */
import { useState } from 'react'
import type { SongSummary } from '../types'
import { fmtBar, fmtTime } from '../lib/format'
import { StemMixer } from './StemMixer'

export interface TransportProps {
  song: SongSummary | null
  time: number
  playing: boolean
  ready: boolean
  volume: number
  onToggle: () => void
  onSeek: (t: number) => void
  onNudge: (d: number) => void
  onVolume: (v: number) => void
  /** Mixer state, owned by the player so the timeline can show it too. */
  mix: {
    solo: Set<string>
    loading: Set<string>
    unavailable: Set<string>
  }
  /** Adds or removes a stem from the solo set. */
  onSoloStem: (stem: string) => void
  onClearMix: () => void
}

export function Transport(props: TransportProps) {
  const {
    song, time, playing, ready, volume, onToggle, onSeek, onNudge, onVolume,
    mix, onSoloStem, onClearMix,
  } = props
  const [showStems, setShowStems] = useState(false)

  // The stems the app treats as instruments: the ones with a lane on the
  // timeline. A separated file the analysis rejected as bleed is still on disk
  // and still playable, but offering it as a part to audition contradicts what
  // the timeline says exists, and it is why the count used to disagree with the
  // lane count and with the sidebar's "N instruments".
  const audibleStems = song
    ? song.stem_audio.filter((s) => song.sources.some((src) => src.id === s.stem))
    : []

  return (
    <div className="transport" style={{ flexWrap: 'wrap' }}>
      <button className="play-btn" onClick={onToggle} disabled={!ready}
        title="Play / pause (space)">{playing ? '❚❚' : '▶'}</button>
      <button className="ghost tiny" onClick={() => onNudge(-5)} disabled={!ready} title="back 5s">−5</button>
      <button className="ghost tiny" onClick={() => onNudge(5)} disabled={!ready} title="forward 5s">+5</button>

      <div className="time">
        {fmtTime(time)} <span className="of">/ {song ? fmtTime(song.duration) : '0:00.0'}</span>
      </div>
      <div className="bar-label">
        {song && song.grid.bpm > 0
          ? fmtBar(time, song.grid.bpm, song.grid.meter, song.grid.offset)
          : '—'}
      </div>

      {song && (
        <div className="row tiny dim" style={{ gap: 10 }}>
          <span className="mono" title={song.grid.bpm_confidence < 0.4
            ? 'The beat track is irregular; this tempo and the bar grid are approximate.'
            : 'Tempo estimated from the beat track'}>
            {song.grid.bpm_confidence < 0.4 ? '≈' : ''}{Math.round(song.grid.bpm)} BPM
          </span>
          <span className="mono">{song.grid.meter}</span>
          {song.loudness.integrated_lufs !== null && (
            <span className="mono">{song.loudness.integrated_lufs.toFixed(1)} LUFS</span>
          )}
          {song.is_mono_source && <span className="tag warn">mono</span>}
        </div>
      )}

      <div className="spacer grow" />

      {song && (
        <>
          {/*
            Two controls, because these are two things.

            The button's label used to double as the solo readout — "stems (6)"
            normally, "solo: bass" once something was soloed — which meant that
            soloing a part made the button that opens the stem list stop being
            findable by its own name. One control with two meanings is worse than
            either meaning on its own: the reader has to notice that the label
            changed in order to find the list they were looking for.

            The count is now the number of *lanes*, not the number of files.
            Separation is not a judgement: `htdemucs_6s` always returns six
            stems, including a piano that on "DNA." sits 47 dB below the
            instruments and which the analysis therefore refuses to give a lane.
            So the button said 6, the timeline showed 5, and the sidebar said
            "5 instruments" — three numbers for one thing, and a solo button
            for a part the app had already said is not audible. The mixer now
            lists what the timeline has lanes for, so one number describes one
            set of parts. The silent stems are still on disk and still checked
            by the stem invariant; they are just not offered as instruments.
          */}
          <button className={`tiny ${showStems ? 'primary' : ''}`}
            onClick={() => setShowStems(!showStems)}
            title="Audition separated parts, in sync with the timeline">
            Listen to parts
          </button>
          {mix.solo.size > 0 && (
            <button className="tiny primary" onClick={onClearMix}
              title="stop soloing and play the whole song (esc)">
              soloing {[...mix.solo].join(' + ')} ✕
            </button>
          )}
          {showStems && (
            <div style={{ width: '100%' }}>
              <StemMixer
                stems={audibleStems}
                colors={Object.fromEntries(song.sources.map((s) => [s.id, s.color]))}
                solo={mix.solo}
                loading={mix.loading}
                unavailable={mix.unavailable}
                onSolo={onSoloStem}
                onClose={() => setShowStems(false)}
              />
            </div>
          )}
        </>
      )}

      <span className="tiny faint">vol</span>
      <input className="vol" aria-label="Volume" type="range" min={0} max={1} step={0.01} value={volume}
        onChange={(e) => onVolume(Number(e.target.value))} />
    </div>
  )
}
