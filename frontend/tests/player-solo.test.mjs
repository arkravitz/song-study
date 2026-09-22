import test from 'node:test'
import assert from 'node:assert/strict'
import { Player } from '../src/audio/Player.ts'

test('soloing and returning to the mix continue from the live playhead', async () => {
  const starts = []
  class FakeContext {
    currentTime = 0
    sampleRate = 48000
    destination = {}
    createGain() {
      return { gain: { value: 1, setValueAtTime() {}, setTargetAtTime() {} }, connect() {} }
    }
    createBufferSource() {
      return {
        buffer: null, onended: null, connect() {}, stop() {}, disconnect() {},
        start(when, offset) { starts.push({ when, offset }) },
      }
    }
    async decodeAudioData() { return { duration: 180 } }
  }
  globalThis.window = { AudioContext: FakeContext }
  globalThis.fetch = async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(1) })

  const player = new Player()
  await player.load('/mix.wav', [{ id: 'drums', url: '/drums.wav' }])
  player.play(30)
  const ctx = player.ctx
  ctx.currentTime = 15
  await player.toggleSolo('drums')
  assert.ok(Math.abs(player.time() - 45) < 0.05, `solo jumped to ${player.time()}`)
  assert.ok(Math.abs(starts.at(-1).offset - 45) < 0.05)

  ctx.currentTime = 19
  await player.toggleSolo('drums')
  assert.ok(Math.abs(player.time() - 49) < 0.05, `mix return jumped to ${player.time()}`)
})

test('volume changes the audible output without unmuting the mix or restarting stems', async () => {
  const starts = []
  class FakeContext {
    currentTime = 0
    destination = {}
    createGain() {
      const node = { gain: { value: 1,
        setValueAtTime(value) { this.value = value },
        setTargetAtTime(value) { this.value = value },
      }, connect(destination) { this.destination = destination } }
      return node
    }
    createBufferSource() {
      return { connect() {}, stop() {}, disconnect() {}, onended: null,
        start(when, offset) { starts.push({when, offset}) } }
    }
    async decodeAudioData() { return { duration: 180 } }
  }
  globalThis.window = { AudioContext: FakeContext }
  globalThis.fetch = async () => ({ok:true, arrayBuffer: async () => new ArrayBuffer(1)})
  const player = new Player()
  await player.load('/mix.wav', [{id:'drums', url:'/drums.wav'}, {id:'bass', url:'/bass.wav'}])
  player.setVolume(.4)
  player.play(30)
  await player.toggleSolo('drums')
  await player.toggleSolo('bass')
  const previousStarts = starts.length
  player.ctx.currentTime = 5
  player.setVolume(.25)
  assert.equal(player.mixGain.gain.value, 0, 'changing volume must keep the mix muted')
  assert.equal(player.masterGain.gain.value, .25)
  for (const gain of player.stemGains.values()) assert.equal(gain.destination, player.masterGain)
  assert.equal(player.mixGain.destination, player.masterGain)
  assert.equal(starts.length, previousStarts, 'volume must not restart playback')
  assert.equal(player.time(), 35)
  player.clearMix()
  assert.equal(player.mixGain.gain.value, 1)
  assert.equal(player.masterGain.gain.value, .25, 'returning to the song must preserve volume')
  player.pause()
  player.setVolume(.1)
  player.play()
  assert.equal(player.masterGain.gain.value, .1)
})
