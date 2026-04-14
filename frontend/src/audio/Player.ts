/**
 * Sample-accurate playback, with per-stem solo and mute.
 *
 * The reason this exists rather than an <audio> element: `HTMLMediaElement
 * .currentTime` is coalesced to roughly 4 Hz in some browsers and drifts
 * relative to the audio hardware clock. A timeline that draws a playhead from
 * it visibly judders against the music.
 *
 * So: decode into AudioBuffers, play from one-shot AudioBufferSourceNodes, and
 * derive time from `AudioContext.currentTime` minus the known start time.
 * `ctx.outputLatency` is subtracted so the cursor lines up with what is
 * actually reaching the speakers.
 *
 * ## Soloing
 *
 * Soloing is a mixer operation, not a "load a different file" operation, and
 * the distinction decides the whole design. Every audible source — the full mix
 * and each soloed stem — gets its own GainNode, and all of them are started at
 * the *same* `ctx.currentTime` with the same offset. Starting them in separate
 * calls would drift by a few milliseconds per source, which is audible as a
 * flam between the drums and the bass and is exactly the thing a producer is
 * trying to judge when they solo a part.
 *
 * When nothing is soloed the mix plays alone, so normal playback is unaffected
 * and costs one source node.
 *
 * There is deliberately no mute. The mix is one decoded file, so an instrument
 * cannot be taken out of it — only replaced by the sum of the other stems, which
 * is a different recording. A control that cannot act on the signal is worse
 * than no control, so the mixer offers solo and says why.
 *
 * Stems are decoded lazily and cached. A three-minute stereo stem is ~60 MB of
 * float32 in memory, so eagerly decoding six of them on load would be a
 * ~380 MB bill the user never asked for. Loading is therefore explicit and
 * reports progress through `onLoad`.
 */
export interface StemInfo {
  id: string
  url: string
}

export class Player {
  private ctx: AudioContext
  private mixBuffer: AudioBuffer | null = null
  private stemBuffers = new Map<string, AudioBuffer>()
  private loading = new Set<string>()
  private failed = new Set<string>()

  private masterGain: GainNode
  private mixGain: GainNode
  private stemGains = new Map<string, GainNode>()

  private sources: AudioBufferSourceNode[] = []
  private startedAt = 0
  private offsetAtStart = 0
  private _playing = false
  private _duration = 0
  private _volume = 1
  private _solo: Set<string> = new Set()
  private _stems: StemInfo[] = []
  /** Which track the currently-loaded audio and stems belong to. */
  private trackUrl = ''
  /** Incremented per load; a stale async continuation compares against it. */
  private loadToken = 0
  private onEnded: (() => void) | null = null
  private listeners = new Set<(t: number, playing: boolean) => void>()
  private stateListeners = new Set<() => void>()
  /** User-adjustable calibration offset, in seconds. */
  latencyOffset = 0

  constructor() {
    const Ctor = window.AudioContext ?? (window as any).webkitAudioContext
    this.ctx = new Ctor({ latencyHint: 'interactive' })
    this.masterGain = this.ctx.createGain()
    this.masterGain.gain.value = this._volume
    this.masterGain.connect(this.ctx.destination)
    this.mixGain = this.ctx.createGain()
    this.mixGain.gain.value = 1
    this.mixGain.connect(this.masterGain)
  }

  get duration() { return this._duration }
  get playing() { return this._playing }
  get volume() { return this._volume }
  get solo(): Set<string> { return this._solo }

  /** True when nothing is soloed, so the full mix is what you hear. */
  get playingFullMix(): boolean { return this._solo.size === 0 }
  get stems() { return this._stems }
  get sampleRate() { return this.ctx.sampleRate }
  get baseLatency() {
    return (this.ctx.baseLatency || 0) + (this.ctx.outputLatency || 0)
  }
  isLoading(id: string) { return this.loading.has(id) }
  isUnavailable(id: string) { return this.failed.has(id) }

  onTick(fn: (t: number, playing: boolean) => void) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  /** Fires when solo/mute/loading changes, so the UI can re-render. */
  onState(fn: () => void) {
    this.stateListeners.add(fn)
    return () => this.stateListeners.delete(fn)
  }

  /**
   * Load the full mix, and declare which stems are available for soloing.
   * Passing a new stem list drops any cached buffers for stems no longer listed.
   */
  /**
   * Load the full mix, and declare which stems are available for soloing.
   *
   * Resolves to the track duration, or to `null` if a newer load superseded this
   * one before it finished. A caller must treat `null` as "do nothing" rather
   * than as a failure.
   */
  async load(url: string, stems: StemInfo[] = []): Promise<number | null> {
    // Every load supersedes the last. A stem decode that finishes after the user
    // has moved to another track must not be played under the new one, and the
    // only way to know that is to number the loads and compare.
    const mine = ++this.loadToken
    const res = await fetch(url)
    if (!res.ok) throw new Error(`audio fetch failed: ${res.status}`)
    const bytes = await res.arrayBuffer()
    const decoded = await this.ctx.decodeAudioData(bytes)
    if (mine !== this.loadToken) return null
    this.mixBuffer = decoded
    this._duration = decoded.duration

    const nextIds = new Set(stems.map((s) => s.id))
    for (const id of [...this.stemBuffers.keys()]) {
      if (!nextIds.has(id)) {
        this.stemBuffers.delete(id)
        this.stemGains.delete(id)
      }
    }
    this._stems = stems
    this.trackUrl = url
    // A soloed part from the previous song has no stem here, so it leaves the
    // set. One at a time was a single value to null out; a set needs each entry
    // checked, and leaving a stale id in would make the buttons claim to be on
    // for a part that cannot play.
    if (this._solo.size) {
      for (const id of [...this._solo]) if (!nextIds.has(id)) this._solo.delete(id)
    }
    this.emitState()
    return this._duration
  }

  /** Current playhead position in seconds, corrected for output latency. */
  time(): number {
    if (!this.mixBuffer) return 0
    if (!this._playing) return this.offsetAtStart
    const raw = this.ctx.currentTime - this.startedAt + this.offsetAtStart
    return Math.max(0, Math.min(this._duration, raw - this.baseLatency - this.latencyOffset))
  }

  play(from?: number) {
    if (!this.mixBuffer) return
    // A mixer change restarts source nodes, but it must use the position they
    // have reached *now*. offsetAtStart is the position of the previous start;
    // reusing it rewinds the song every time a stem is soloed or unsoloed.
    const position = from ?? (this._playing
      ? this.offsetAtStart + this.ctx.currentTime - this.startedAt
      : this.offsetAtStart)
    this.stopSources()
    const t = Math.max(0, Math.min(this._duration - 0.01, position))
    // One shared start instant for every audible source. Scheduling them in a
    // single pass at the same time is what keeps a soloed stem phase-locked to
    // the mix; separate `start()` calls are not guaranteed to be sample-aligned.
    const when = this.ctx.currentTime

    const soloed = [...this._solo]
    // With any stem soloed the mix must be silent, or you hear both.
    this.mixGain.gain.setValueAtTime(soloed.length ? 0 : 1, when)

    const start = (buf: AudioBuffer, dest: AudioNode) => {
      const src = this.ctx.createBufferSource()
      src.buffer = buf
      src.connect(dest)
      src.start(when, t)
      this.sources.push(src)
      return src
    }

    const mixSrc = start(this.mixBuffer, this.mixGain)
    // Every soloed stem that has finished decoding plays, scheduled in the same
    // pass at the same instant as the mix — separate `start()` calls are not
    // guaranteed to be sample-aligned, and two soloed parts drifting against
    // each other is worse than either one alone.
    let anyStem = false
    for (const id of soloed) {
      const buf = this.stemBuffers.get(id)
      if (!buf) continue
      start(buf, this.stemGain(id))
      anyStem = true
    }
    if (!anyStem) {
      // Every soloed stem is still decoding, or failed. Falling back to the mix
      // silently is the worst option: you asked to hear the bass and would get
      // the full mix with nothing saying so. The mix is restored so playback
      // makes progress, and `emitState` publishes which stems are missing so the
      // mixer can say "bass could not be loaded" next to its own button.
      this.mixGain.gain.setValueAtTime(1, when)
      this.emitState()
    }

    mixSrc.onended = () => {
      if (this.sources.includes(mixSrc)) {
        this._playing = false
        this.offsetAtStart = 0
        this.emit()
        this.onEnded?.()
      }
    }
    this.startedAt = when
    this.offsetAtStart = t
    this._playing = true
    this.emit()
  }

  pause() {
    if (!this._playing) return
    const t = this.time()
    this.stopSources()
    this.offsetAtStart = t
    this._playing = false
    this.emit()
  }

  toggle() {
    if (this._playing) this.pause()
    else void this.resume().then(() => this.play())
  }

  seek(t: number) {
    const clamped = Math.max(0, Math.min(this._duration, t))
    if (this._playing) this.play(clamped)
    else {
      this.offsetAtStart = clamped
      this.emit()
    }
  }

  setVolume(v: number) {
    this._volume = Math.max(0, Math.min(1, v))
    this.masterGain.gain.setTargetAtTime(this._volume, this.ctx.currentTime, 0.01)
  }

  nudge(seconds: number) { this.seek(this.time() + seconds) }

  /**
   * Add or remove a stem from the solo set.
   *
   * A set, not a single value, because the control was lying: six independently
   * drawn `S` buttons, of which exactly one could ever be on. Clicking bass
   * after drums silently switched to bass and left drums looking selected, so
   * the interface showed two states at once and the only way to hear two parts
   * was to solo one, listen, un-solo it, and solo the other.
   *
   * Toggling membership is what the six buttons imply, and it makes "bass and
   * drums together" one click each instead of three. `clearMix` is the reset to
   * the full song, and Escape is bound to it.
   *
   * Async because the first solo of a stem decodes ~60 MB of audio; the UI shows
   * the loading state meanwhile.
   */
  async toggleSolo(id: string): Promise<boolean> {
    const wasOn = this._solo.has(id)
    if (wasOn) this._solo.delete(id)
    else this._solo.add(id)
    this.emitState()
    if (wasOn) {
      // Removing the last soloed stem means the mix becomes the audio again.
      if (this._playing) this.play()
      return true
    }
    const ok = await this.ensureStem(id)
    if (!ok) {
      // Do not leave a button lit for a part that cannot play. It is reported
      // through `failed`, which the mixer already reads for its tooltip.
      this._solo.delete(id)
      this.emitState()
    }
    // Re-seek so the stem joins at the right place rather than from the top.
    if (this._playing) this.play()
    return ok
  }

  /** Back to the whole song. Bound to Escape and to a visible control. */
  clearMix() {
    if (!this._solo.size) return
    this._solo.clear()
    this.emitState()
    if (this._playing) this.play()
  }

  private stemGain(id: string): GainNode {
    let g = this.stemGains.get(id)
    if (!g) {
      g = this.ctx.createGain()
      g.gain.value = 1
      g.connect(this.masterGain)
      this.stemGains.set(id, g)
    }
    return g
  }

  private async ensureStem(id: string): Promise<boolean> {
    const existing = this.stemBuffers.get(id)
    if (existing) return true
    if (this.loading.has(id) || this.failed.has(id)) return false
    const info = this._stems.find((s) => s.id === id)
    if (!info) {
      this.failed.add(id)
      this.emitState()
      return false
    }
    this.loading.add(id)
    this.emitState()
    // The track this stem belongs to, captured now and checked after the decode.
    // A three-minute stereo stem takes long enough to decode that the user can
    // select another song inside the window, and the buffer would otherwise be
    // cached under a name that the next track also uses.
    const forUrl = this.trackUrl
    const mine = this.loadToken
    try {
      const res = await fetch(info.url)
      if (!res.ok) throw new Error(String(res.status))
      const buf = await this.ctx.decodeAudioData(await res.arrayBuffer())
      if (mine !== this.loadToken || forUrl !== this.trackUrl) return false
      this.stemBuffers.set(id, buf)
      return true
    } catch {
      this.failed.add(id)
      return false
    } finally {
      this.loading.delete(id)
      this.emitState()
    }
  }

  private stopSources() {
    for (const s of this.sources) {
      try {
        s.onended = null
        s.stop()
        s.disconnect()
      } catch {
        /* already stopped */
      }
    }
    this.sources = []
  }

  private emit() {
    const t = this.time()
    this.listeners.forEach((fn) => fn(t, this._playing))
  }

  private emitState() {
    this.stateListeners.forEach((fn) => fn())
  }

  tick() {
    if (this._playing) this.emit()
  }

  async resume() {
    if (this.ctx.state === 'suspended') await this.ctx.resume()
  }
}
