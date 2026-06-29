/**
 * Playhead, transport and mixer state.
 *
 * The playhead is driven by requestAnimationFrame against the audio clock, not
 * by a React state update per frame: a 60 Hz rAF that writes to a ref and lets
 * the canvas read it is what keeps the timeline smooth while the rest of the UI
 * re-renders at a human rate.
 *
 * Solo/mute live here rather than in the Player because they are UI state the
 * timeline, the inspector and the transport all need to agree about.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Player, type StemInfo } from '../audio/Player'

export interface TransportState {
  time: number
  playing: boolean
  duration: number
  volume: number
}

export interface MixState {
  /** Every soloed stem. A set, because the buttons are a set. */
  solo: Set<string>
  loading: Set<string>
  unavailable: Set<string>
}

export function usePlayer(url: string | null, stems: StemInfo[] = []) {
  const playerRef = useRef<Player | null>(null)
  const [ready, setReady] = useState(false)
  const [state, setState] = useState<TransportState>({
    time: 0, playing: false, duration: 0, volume: 1,
  })
  const [mix, setMix] = useState<MixState>({
    solo: new Set<string>(), loading: new Set(), unavailable: new Set(),
  })
  const timeRef = useRef(0)

  // The memo key must identify the *files*, not their names.
  //
  // It was `stems.map(s => s.id).join('|')`, and every track in the library
  // separates to the same names — vocals, drums, bass, guitar, other. So
  // switching from one song to another produced an identical key while the URLs
  // pointed at completely different files, and the effect that was supposed to
  // hand the Player the new song's stems never re-ran. Combined with the ref
  // read below, the Player was permanently holding the *previous* song's stem
  // list: instrumenting `fetch` showed soloing drums on "All the Small Things"
  // request `/api/stems/<taste's id>/drums.wav`.
  const stemsKey = stems.map((s) => `${s.id}@${s.url}`).join('|')
  // The list is still read through a ref so that a re-render with an equivalent
  // list does not tear down the player, but the key above — not the ref — is
  // what decides correctness.
  const stemsRef = useRef<StemInfo[]>(stems)
  stemsRef.current = stems

  useEffect(() => {
    if (!url) {
      setReady(false)
      return
    }
    let cancelled = false
    const p = new Player()
    playerRef.current = p
    setReady(false)
    const off = p.onState(() => {
      if (cancelled) return
      setMix({
        solo: new Set(p.solo),
        loading: new Set(p.stems.filter((s) => p.isLoading(s.id)).map((s) => s.id)),
        unavailable: new Set(p.stems.filter((s) => p.isUnavailable(s.id)).map((s) => s.id)),
      })
    })
    p.load(url, stemsRef.current)
      .then((d) => {
        // `null` means this load was superseded by a newer one — a song switch
        // landed while the audio was still decoding. The newer effect owns the
        // state now, so writing anything here would be the stale-write bug all
        // over again.
        if (cancelled || d === null) return
        setState((s) => ({ ...s, duration: d }))
        setReady(true)
      })
      .catch(() => { if (!cancelled) setReady(false) })
    return () => {
      cancelled = true
      off()
      try { p.pause() } catch { /* ignore */ }
    }
  }, [url, stemsKey])

  // Dev-only handle. The mandate for this phase asks for the app to be
  // inspectable during development — playhead, view, active sources, analysis
  // state — without any of it appearing in the interface. `__aa` is exactly
  // that: a window onto the live player so a bug report can be reproduced
  // rather than argued about. Stripped from production builds by the guard.
  //
  // `timeRef` is on the handle because the playhead lag could only be settled by
  // measuring it: at whole-song zoom the playhead advances 4.75 px/s, so one
  // pixel is 210 ms and the drawn position cannot resolve a 50 ms error at all.
  // The ref is the value the canvas actually reads, and comparing it against the
  // audio clock within the same frame has no such floor.
  useEffect(() => {
    if (!import.meta.env.DEV) return
    ;(window as any).__aa = { player: playerRef, mix, ready, state, timeRef }
  })

  // rAF loop: update React state at ~20 Hz, ref at full rate for the canvas.
  useEffect(() => {
    let raf = 0
    let last = 0
    const loop = (ts: number) => {
      const p = playerRef.current
      if (p) {
        const t = p.time()
        timeRef.current = t
        if (ts - last > 45) {
          last = ts
          setState((s) => (s.playing !== p.playing || Math.abs(s.time - t) > 0.04
            ? { ...s, time: t, playing: p.playing } : s))
        }
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [])

  const play = useCallback(async () => {
    const p = playerRef.current
    if (!p) return
    await p.resume()
    p.play()
    setState((s) => ({ ...s, playing: true }))
  }, [])

  const pause = useCallback(() => {
    playerRef.current?.pause()
    setState((s) => ({ ...s, playing: false }))
  }, [])

  const toggle = useCallback(() => {
    const p = playerRef.current
    if (!p) return
    if (p.playing) pause()
    else void play()
  }, [play, pause])

  const seek = useCallback((t: number) => {
    playerRef.current?.seek(t)
    timeRef.current = playerRef.current?.time() ?? t
    setState((s) => ({ ...s, time: timeRef.current }))
  }, [])

  const nudge = useCallback((d: number) => {
    const p = playerRef.current
    if (p) { p.nudge(d); seek(p.time()) }
  }, [seek])

  const setVolume = useCallback((v: number) => {
    playerRef.current?.setVolume(v)
    setState((s) => ({ ...s, volume: v }))
  }, [])

  /** Solo a stem, or clear with the same id / null. Toggles when it matches. */
  const toggleSolo = useCallback(async (id: string) => {
    const p = playerRef.current
    if (!p) return
    await p.toggleSolo(id)
    if (!p.playing) {
      // Auditioning without pressing play is what a producer actually wants:
      // solo the bass, hear it immediately, without hunting for the transport.
      await p.resume()
      p.play()
    }
  }, [])

  const clearMix = useCallback(() => {
    playerRef.current?.clearMix()
  }, [])

  return {
    player: playerRef, timeRef, ready, state, mix,
    play, pause, toggle, seek, nudge, setVolume,
    toggleSolo, clearMix,
  }
}

/** Keyboard transport shortcuts, bound at the document level. */
export function useTransportKeys(handlers: {
  toggle: () => void
  nudge: (d: number) => void
  onLeft?: () => void
  onRight?: () => void
  onEscape?: () => void
  onClearMix?: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) {
        if (e.key === 'Escape') el.blur()
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return
      switch (e.key) {
        case ' ': e.preventDefault(); handlers.toggle(); break
        case 'ArrowLeft': e.preventDefault(); e.shiftKey ? handlers.onLeft?.() : handlers.nudge(-5); break
        case 'ArrowRight': e.preventDefault(); e.shiftKey ? handlers.onRight?.() : handlers.nudge(5); break
        case 'Home': handlers.onLeft?.(); break
        case 'End': handlers.onRight?.(); break
        // Back out of a solo without reaching for the mouse. Escape is the
        // conventional "undo the transient mode" key.
        case 'Escape': e.preventDefault(); handlers.onEscape?.(); handlers.onClearMix?.(); break
        default: break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handlers])
}
