# A cleaner app for studying songs

September 30, 2026 · Product and technical redesign proposal

## The recommendation

Build a Mac-first, local song-study app around three things: **the sections tell you where you are, energy shows the shape of the record, and stems let you hear how it works.** One timeline connects them. Everything else earns its place by helping someone listen, understand, or correct that timeline.

The core experience should be: open a song, see its structure, play a section, isolate a part, compare another section, and leave with a useful understanding of the arrangement. The app should be useful before anyone reads an explanation.

I would use a native Mac interface and audio engine, with the existing analysis running in a separate local worker. I would retain the useful models and energy calculation initially. A rewrite is an opportunity to simplify the product and make its foundations reliable; it is not evidence that we need a new model for every task.

This assumes Apple Silicon Macs are the first target and offline use matters. If Windows or browser access is a release requirement, I would change the implementation choice before starting. The product design still applies.

## What the current app taught us

The current version has real value. Its best moments happen when a section, a change in energy, and an audible part line up. Its weaker moments make the user translate several visual signals or work around uncertain analysis.

A fresh look at Taste shows the remaining problems clearly:

- Sections appear in a scrolling strip and again in the timeline. Small timeline sections turn into numbers, so the user must look elsewhere to identify them.
- The same passage is described in several places below the timeline. This consumes space without adding much understanding.
- BPM, meter, loudness, instrument counts, and analysis entry points compete with the listening controls.
- Confident-looking section names can still be musically wrong. A correction should feel like normal use, not repairing a failed machine.
- Stem activity rectangles can suggest certainty that the separated audio does not support. The recent piano issue is a particularly good example.
- Playhead alignment, solo continuity, and undo are foundational. If they behave strangely, the user loses trust in otherwise useful analysis.

The first redesign decision is therefore to remove duplicate representations and make listening the shortest path to verification.

## The workspace

[Illustrative concept for the song-study workspace](../README.md)

*Generated visual concept, not a working interface or analysis of a real song. It demonstrates the intended hierarchy and space allocation. Some generated details are inconsistent: the playhead and selected-section text do not match, and the mock omits the quiet-tail scale treatment. The behavior specified below is authoritative.*

### One timeline, one place to look

The header contains the song title, library access, play/pause, elapsed time, and master volume. The transport also shows the current section in readable text. Tempo is available beside the musical ruler, not presented as a headline about the song. Loudness belongs in evidence.

The central workspace has five layers sharing exactly the same horizontal time axis:

1. A readable section band.
2. A modest original-song waveform for locating and seeking.
3. One energy curve.
4. The separated-part lanes, with solo controls directly beside their names.
5. A contextual area that opens when something is inspected.

There is no permanent analysis sidebar. The library opens temporarily. There is no separate parts picker and no requirement to enter an audition mode before pressing Solo. The default view should use most of the available width for the song.

The contextual area sits below the timeline on wider displays and becomes a dismissible sheet on small windows. It contains one selected subject, one short explanation, and relevant actions. It does not repeat a second permanent section report. An empty inspector stays closed.

### What changes with zoom

| Scale | What should be visible |
| --- | --- |
| Whole song | Structural landmarks, song waveform, broad energy shape, major part participation, a few important changes |
| A few sections | Readable waveforms, bar lines when the beat map is usable, section transitions, local entrances and exits |
| A few bars | Beats, waveform detail, boundary handles, finer changes and exact positions |

Grid density follows readable spacing, rather than showing every bar at every scale. Whole-song stem lanes summarize audible participation; closer views expose the audio. Waveforms never disappear because an activity detector decided a part was absent.

When zoomed in, a thin overview appears for navigation and shows the visible range. It is not another fully labeled section editor. Pinch zoom stays anchored under the pointer. Visible +, −, and Whole song controls provide a dependable alternative. Shift is reserved for deliberate overrides, not a second ambiguous zoom mode.

This takes the overview-and-detail navigation pattern from [Ableton’s Arrangement View](https://www.ableton.com/en/manual/arrangement-view/), without adopting its playback-launch quantization.

## Sections should feel editable, not authoritative

Sections are the main navigation objects. A single click on a section starts playback at its exact beginning and identifies it in the transport. Clicking the waveform seeks to the exact position while preserving playing or paused state. Ordinary seeking never snaps.

A small pencil appears when a section is hovered or keyboard-focused. It replaces the label with an inline text field. Return commits, Escape cancels, and the field uses the same readable colors as the surrounding interface. F2 and a context-menu Rename action provide alternatives. Renaming does not move playback.

The boundary between two sections is one shared object. Dragging moves that boundary, shortening one neighbor and lengthening the other. It cannot cross adjacent boundaries. Snap is on by default; Shift-drag temporarily disables it. The target line and destination time/bar are visible before release. When the grid is unreliable, the app explains that snapping is unavailable rather than silently inventing a grid.

Split works at the live playhead. Both children initially inherit the original musical type; the new right-hand label can be edited immediately. Double-clicking a separator removes it and keeps the left name. A context-menu Merge action and Delete on a focused separator make this discoverable and accessible. Every edit is one undo step; a continuous drag is not hundreds of steps.

| Action | Shortcut or alternative |
| --- | --- |
| Play / pause | Space, except while editing text |
| Split at playhead | Cmd-E |
| Rename focused section | F2 or pencil |
| Undo / redo | Cmd-Z / Shift-Cmd-Z; native text undo while typing |
| Merge at focused separator | Delete or context menu |
| Freely drag a snapped boundary | Hold Shift |
| Deliberately snap a seek | Shift-click the waveform |
| Return to whole song | 0 or Whole song button |

Short sections retain their true width. We should never enlarge a five-second passage to make its label fit. Use a second label tier with non-overlapping callouts for narrow sections; their connectors point to the actual interval. If the overview still cannot fit every name, focused/hovered sections show the full name, and an optional section list exposes all names and times. Numbers alone are insufficient. Keyboard focus and screen-reader names must identify every section regardless of width.

Top-level song form and internal events must remain distinct. A fill, dropout, or instrumental change can be a small marker within a chorus; it should not automatically become another equally weighted structural block.

Detected labels remain proposals. Store the musical type, display name, repeat relationship, and user correction separately. A user can call something “Final hook” without losing its relationship to earlier choruses. Neutral “Section” is preferable to a confidently wrong Verse 5. Do not invent confidence scores when the model supplies none.

[Logic’s arrangement markers](https://support.apple.com/guide/logicpro/edit-arrangement-markers-lgcpf7c0a3d7/mac) support the broader idea of editable musical landmarks. [Moises’s section workflow](https://help.moises.ai/hc/en-us/articles/10138829000988-How-do-I-use-Sections) reinforces that sections are useful listening and looping units. Here, correction belongs directly on the timeline.

## Energy should reveal shape, not manufacture drama

Keep the existing energy calculation as the initial baseline, after verifying its declared and applied components agree. Its usefulness is already demonstrated in this app; major reweighting needs listening evidence.

The curve is a relative description of this recording. It is not a universal excitement score, and it cannot prove why a listener feels something. No default decimal values or component legend.

The ending needs deliberate treatment:

- Establish a stable display range from the audible body of the song, excluding terminal silence and fade from the range calculation.
- Keep the entire ending visible. If it falls below the body range, use a small reserved quiet band with a visible scale break and a “Quiet ending” label. Never silently clip it or draw a misleading continuous axis.
- Keep the display transform fixed during zoom and comparison. Otherwise a small verse fluctuation can look larger than a chorus lift.
- Rank internal changes separately from the final fade. Use an ending-aware eligibility rule, not a blanket exclusion of the final minute. A meaningful late dropout should still qualify.
- Offer the full linear curve in evidence, so users can inspect the uncompressed scale.

The precise active-body and tail rules require testing on abrupt endings, long fades, quiet intros, ambient pieces, and songs whose final section is genuinely sparse. If the quiet band is not understandable in testing, use a labeled full-scale inset for the tail instead. Presentation changes must not rewrite the underlying energy values.

Clicking an energy change opens a compact explanation tied to that time:

> **At 1:12, the arrangement expands.** Bass and drums return, while the recording becomes louder.

That sentence is only valid when the underlying observations support it. If only the curve changes, say “Energy rises here” and offer listening. Use “coincides with” rather than pretending correlation establishes cause. Evidence exposes component contributions and the compared windows.

## Stems are a listening lens

Solo is a normal track control. It changes the routing at the current position, with a short gain ramp, without restarting playback. Several solos can combine. Master volume controls the audible mix, including soloed parts. The current routing remains visible in the transport.

The initial separation vocabulary should be vocals, drums, bass, and other accompaniment. “Other parts” is less specific, but more honest than promising six original instruments. Preserve existing guitar and piano results, and support a finer-parts option in song settings when a tested separator justifies it. This is a tradeoff: guitar is valuable enough to benchmark explicitly, not discard permanently for architectural tidiness.

[Demucs’s documentation](https://github.com/facebookresearch/demucs) warns about piano leakage and artifacts in its experimental six-source model, as well as export rescaling that changes relative stem levels. Those limitations should affect both model selection and UI claims.

A lane’s waveform shows the separated recording. Participation shading is a separate, quieter layer that distinguishes audible contribution from low-level residue. Use one stable per-song lane scale; never magnify each quiet window into apparent activity. Quiet or uncertain parts can be disclosed through an inline “Additional parts” row, without restoring the removed Choose parts workflow.

Click a lane name to inspect that part: where it first becomes audibly active, where it drops out, and which sections it contributes to. Clicking a supported entrance jumps to it and offers a short context loop. If the detector is uncertain, listening remains available and the language reflects that uncertainty.

The waveform always stays available. Hiding it behind activity masks would prevent users from judging the detector itself.

## Comparison should name both sides and let you hear them

Selecting Chorus 2 exposes one action: Compare with Chorus 1, with the reference time shown. A user can choose another reference. Do not require manually finding two panels or interpreting an unlabeled delta.

Comparison temporarily replaces the contextual area, while keeping the whole-song location visible. Two compact timelines align at section beginnings. Their original durations remain visible; no automatic time stretching or concealed truncation.

```text
Chorus 2 · 1:12–1:36       compared with       Chorus 1 · 0:42–1:06

In Chorus 2:
  Bass plays through more of the passage.
  The recording is more energetic.
  Vocals and drums participate similarly.

[Listen to Chorus 1]   [Listen to Chorus 2]   [Return to song]
```

Every statement names the current section or appears under an explicit “In Chorus 2” heading. Part rows align across both views, with additions and absences written in words as well as shown visually.

Switching A/B maintains the elapsed position within each section. If that position falls beyond the shorter section, restart both from their beginnings and explain the reset. Section loops have independent lengths and never spill unexpectedly into the next section. Original relative levels are the default, because level differences are part of arrangement energy. Optional loudness-matched listening must be labeled and must not change the displayed analysis.

Comparison facts should come from deterministic, testable calculations. An LLM can paraphrase or answer an optional question later; it should not be required to understand what changed.

## Discovery without an observation feed

Show at most three restrained markers for supported, musically useful events: a substantial expansion, an internal dropout, or a later section that differs from its repeat. Hover or focus previews a short phrase; click seeks and opens its explanation. No permanent stream of generated insights.

A change marker must say what made it interesting, not merely expose that an internal novelty score peaked. If the evidence supports nothing more useful than “something changed,” omit the automatic recommendation and leave the curve available for exploration.

The display follows [progressive disclosure](https://www.nngroup.com/articles/progressive-disclosure/): technical detail stays available, while repeated listening controls remain immediately accessible. Named sections, explicit comparison subjects, and visible edit affordances reduce the memory burden described by [recognition rather than recall](https://www.nngroup.com/articles/recognition-and-recall/).

## The technical foundation

### Choose a platform deliberately

| Option | Why choose it | Main cost |
| --- | --- | --- |
| **Native Mac: recommended** | Local files, integrated audio transport, native menus/undo/accessibility, focused Mac product | New UI implementation; Mac only |
| Tauri + React + native audio | Reuse frontend work and pursue multiple desktop platforms | More integration boundaries; separate platform/model support |
| Browser + local server | Fastest continuation of the prototype | Installation/server lifecycle and memory/device behavior need product-quality handling |

I would choose SwiftUI for the shell, an AppKit-backed timeline with Core Graphics/layers, AVAudioEngine for playback, SQLite for song and edit metadata, and a bundled Python analysis worker communicating through private pipes. Add Metal rendering only if profiling establishes a need.

Web Audio supports accurately scheduled playback too. Our previous bugs do not prove browsers cannot do this. The native choice is about the intended Mac product, distribution, and ownership of audio, not a guarantee that rewriting fixes timing. See the [technical research notes](research/song-study-foundations.md) for source links, alternative details, and unverified packaging assumptions.

### One transport owns time

Decode the original song into a canonical timeline with an explicit sample rate. Store editable positions as integer sample frames. Preserve model-input sample rates, hop sizes, padding, and offsets so every result maps back to that timeline. Tempo is not a substitute for actual beat timestamps.

One audio engine schedules the original and all stems against the same origin. UI drawing reads that clock. Seeking reschedules aligned sources; solo and volume change gains without resetting the transport. Account for output latency when drawing the audible playhead, and test different devices. Apple’s [player-node scheduling](https://developer.apple.com/documentation/avfaudio/avaudioplayernode) supplies useful primitives; correct application behavior still has to be engineered.

Use bounded disk-backed playback and multiresolution waveform caches. Avoid decoding every long stem into a separate permanent UI buffer. Loading a stem asynchronously must not seek or restart a song that is already playing. If it is not ready, show a brief local loading state and join it at the current transport time.

### Separate analysis from human edits

The stored song has three distinct layers:

- Immutable media and analysis artifacts, identified by audio hash, model/configuration version, and artifact schema.
- A persistent edit history: rename, split, merge, move boundary, musical-grid correction, and repeat relationships.
- Temporary viewing state: zoom, selection, routing, and open inspection.

Shared boundaries are stored once. Stable IDs identify sections; array indexes do not. Derived summaries and comparisons depend on the effective edited intervals. Editing a boundary recomputes inexpensive summaries from cached features, without rerunning separation.

Use database transactions for edits and one undo command per user action. Edits survive restarts. Reanalysis produces a new candidate layer and a reviewable reconciliation; it never silently overwrites human work. An undo must not erase analysis completed in the background.

[Sonic Visualiser’s reference](https://sonicvisualiser.org/doc/reference/3.2/en/) is a useful precedent for shared audio-time coordinates and editable annotations. We can retain that rigor without exposing its layer-management complexity in the default workspace.

### Keep analysis replaceable and reproducible

Start with the integrated All-In-One MLX model for structure/beat proposals, the existing energy heuristic, and a pinned separator. The [MLX port](https://github.com/ssmall256/all-in-one-mlx) accelerates the upstream approach; it has not demonstrated better section semantics. The existing [four-song benchmark](MLX-BENCHMARK.md) records why manual correction remains essential.

Define narrow model adapters that output timestamped structural candidates, aligned stems, or versioned feature series. Keep UI labels and producer explanations outside model-specific payloads. Reuse compatible separation artifacts across stages instead of separating twice. Record joint stem gain calibration and any timing correction.

The worker owns resumable stages: decode, peaks, separation, structure, energy, summaries. Playback becomes available after decoding; later results appear without moving selection or replacing edits. UI copy says “Finding sections” or “Separating parts,” not filenames or stack traces. Cancellation and worker crashes leave completed artifacts usable. New results are committed atomically.

Bundle the runtime; do not ask musicians to install Python. Download large model data explicitly, with purpose, size, progress, checksum, license, and removal controls. Analyze offline after setup. Code, checkpoints, codecs, and redistribution notices need an artifact-level release manifest. The archived Demucs dependency makes a tested replacement path important, but does not justify switching blindly to a newer model.

### What happens to DeepSeek and the extra analysis?

DeepSeek is unnecessary for the core product. Remove section naming as an LLM task. Structure correction, solo, energy explanations, and comparison must work with no account or network connection.

An optional later role is answering a specific producer question using timestamped, verified facts: “Where does the arrangement thin out before a chorus?” Answers should link to evidence and admit missing data. It must not invent instrument presence or replace measured results. A hosted provider requires explicit opt-in and an explanation of what is sent; audio stays local by default.

Preserve existing useful advanced analysis in a compact evidence inspector or compatibility viewer during migration. Do not recreate every old panel in the new default interface. Lyrics alignment, new source classifiers, automatic theory analysis, and a full DAW editing environment are outside this rebuild’s first release.

## How I would build and validate it

### 1. Prove the risky foundation before rewriting the interface

Build a small transport prototype using one original and several aligned stems. Verify seeking, looping, solo, volume, device changes, long-file memory use, and playhead alignment. Separately package the existing worker, sign/notarize the app, and run it on a clean Mac without developer dependencies. Establish minimum supported hardware and measure throughput; this document makes no new performance promises.

If either prototype fails, resolve that limitation before committing to the native rebuild. A web or Tauri product remains a valid choice.

### 2. Build the complete listening workspace

Implement import, immediate playback, one timeline, semantic zoom, section navigation/editing, persistent undo, energy, and direct solo. Import current cached songs and user corrections. Prove that a producer can spend twenty minutes studying a song without opening an evidence panel.

### 3. Add comparison and restrained explanations

Implement explicit current/reference comparison, aligned A/B listening, supported entrance/dropout navigation, and evidence disclosure. Recompute explanations when edits change their intervals. Never show stale section text while newer boundaries are drawn.

### 4. Select better analysis using a real listening benchmark

Use a diverse held-out collection with human annotations for boundaries, labels, beat alignment, audible entrances, and separation artifacts. Include live tempo, half-time ambiguity, instrumental music, sparse endings, long fades, and genre changes. Compare the current four- and six-source options before deciding whether finer guitar/piano lanes earn their place.

Measure boundary timing separately from semantic labels. Record time required to correct a song, not only model scores. The current songs are useful regression cases, not sufficient evidence of general accuracy.

## Acceptance criteria

These are proposed targets, not results already achieved.

| Producer task | Intended path |
| --- | --- |
| When does bass first enter? | Inspect Bass → audible entrance → listen in context |
| What changes at the first chorus? | Click Chorus 1 → short transition explanation and aligned parts |
| Why does it feel bigger? | Energy-change inspection → supported contributors → optional evidence |
| How is Chorus 2 different? | Click Chorus 2 → Compare with Chorus 1 → named differences and A/B |
| Where is the biggest internal dropout? | One labeled marker → hear it; final fade ranked separately |
| Which instruments are active now? | Aligned lanes and current-part readout, without opening a sidebar |
| Did I split in the wrong place? | Cmd-Z or remove the separator; no repair workflow |

Run a five-second cold-view test: can a first-time user identify the song’s broad form, current position and section, main parts, and biggest changes? Then run a twenty-minute study session. Count clicks, searching, wrong interpretations, and corrections. Compare with the current app, rather than assuming fewer pixels means better comprehension.

Test keyboard navigation, VoiceOver, visible focus, non-color cues, readable input contrast, short-section labels, and small windows. Use [WCAG 2.2](https://www.w3.org/TR/WCAG22/) as an accessibility benchmark alongside native Mac behavior, not as an unverified compliance claim.

The release bar is dependable listening, readable structure, honest energy, and easy correction. More measurements would not compensate for a timeline that makes those four things hard.
