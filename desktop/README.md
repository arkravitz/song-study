# Song Study for Mac

A native desktop workspace for understanding arrangements through sections, energy, and separated audio. It runs beside the existing web application.

## Source distribution

This repository includes native source, not a prebuilt app. A local development build targets macOS 14+ and is ad-hoc signed, not notarized. No Python runtime, model checkpoint, audio library or FFmpeg binary is checked in.

The first library scan imports snapshots of the existing analyses without changing them. Opened songs get their own playback and waveform assets. Desktop edits and analysis live in `~/Library/Application Support/Song Study/`. Desktop corrections do not update the web app, and subsequent web edits do not silently overwrite desktop corrections.

## Use it

Closing the main window quits the app and stops audio and the private analysis worker. The launch delegate loads the bundled artwork as the running Dock icon.

- Click a section to listen from its beginning. Selection keeps lane sizes stable. Click or drag across a waveform or the ruler to seek exactly; playing/paused state is preserved. Playing scrubs audition short passages while moving, then continue from the release point. Shift-click/drag seeks to detected beats.
- Click a part's **S** to solo it at the current playback position. Several parts can be soloed together. Playback volume controls the audible mix, shows 0–100%, and starts at 100%. It cannot amplify beyond the original full level.
- Click a lane name to inspect its participation and first detected entrance.
- Select a section and use its pencil or context menu to rename it inline. F2 also opens the selected section's name.
- Section colors follow the current name, including numbered labels and pre-/post-chorus variants. Custom names use a neutral color; the model label does not override a rename.
- Cmd-E splits at the playhead. Drag a shared boundary to adjust neighboring sections. Snap is on initially; Shift-drag moves freely. While dragging a divider, its readout shows precise time and the nearest detected bar/beat, for example `1:12.4 · Bar 35.2`. The bar number follows the ruler; the digit after the dot is the beat within that bar.
- Double-click a boundary to merge, retaining the left name. The section context menu also offers merging with the previous section.
- Cmd-Z and Shift-Cmd-Z undo and redo persisted section edits. Typing uses the text editor's undo.
- Compare opens two section views with aligned part rows, a common energy scale, explicit comparison subjects, and A/B listening. A/B preserves elapsed section position when the target is long enough.
- Pinch or Option-scroll zooms under the pointer. The overview navigates a zoomed song; Whole song restores the full range.
- Add song opens audio before analysis completes. Completed stems join the existing transport. Stop cancels the worker job while keeping completed data and current playback.

Click **Energy** in the toolbar or the energy lane label to adjust smoothing. Automatic smoothing follows zoom; drag the slider for a fixed window from raw to four seconds. Settings persist between sessions and change only the display. Gold upward arrows and blue downward arrows use the existing analysis. Click one for its contributors and nearby part entrances/exits. Biggest-rise and biggest-internal-drop shortcuts exclude the ending.

Ruler density follows the same principle as [Ableton’s zoom-adaptive Arrangement grid](https://www.ableton.com/en/manual/arrangement-view/).

The ruler restores detected bars above elapsed time, thinning labels at wider zoom. Downbeat indices are converted to actual beat times before rendering. The timeline and comparison waveforms grow with the window, including macOS full screen.

Energy is relative to this recording. The quiet band below the dashed line keeps low-energy passages and endings visible without allowing the final fade to set the main display range. Separated parts are estimates; quiet additional parts remain accessible.

## The old site

Its source remains in `../frontend` and `../backend`, with the same server entry point and data directory. The desktop does not replace those folders or its URL.

## Build from source

First create the Python 3.12 `.venv` described in the root README, install its dependencies, and install Xcode command-line tools and ffmpeg/ffprobe. From the repository root:

```sh
python3 desktop/build_app.py
```

`--debug` builds a development executable. The initial packaging copies the existing Python runtime and dependencies using APFS clones, then bundles audio-tool dependencies. The local bundle is about 1.5 GB. Updates replace the native executable and worker without deleting the library. Changes to Python dependencies require rebuilding the bundled `Resources/python` directory.

```sh
CLANG_MODULE_CACHE_PATH="$PWD/desktop/.cache/clang" swift test --package-path desktop --disable-sandbox --cache-path desktop/.cache
.venv/bin/python -m pytest desktop/worker/tests -q
```

The app icon source is in [Assets/SongStudy.png](Assets/SongStudy.png). Every build packages all standard macOS icon sizes. Its generation prompt is recorded in [Assets/README.md](Assets/README.md).

## Implementation

- `Sources/SongStudy/Domain.swift`: sample-frame section edits, zoom geometry, relative energy display, typed artifact contract.
- `AudioTransport.swift`: disk-backed AVAudioEngine playback and gain routing on a shared clock.
- `TimelineView.swift`: clipped waveform/energy drawing, semantic detail, section gestures and accessibility objects.
- `StudyStore.swift`: navigation, persisted edits, import coordination and comparison selection.
- `WorkerClient.swift`: private pipes, progress, worker lifecycle, and cancellation of its process group.
- `worker/worker.py`: read-only legacy import, SQLite history, owned audio/waveform assets, and existing analysis adapters.

Waveforms travel as float32 artifact files rather than large JSON arrays. Audio is decoded to a shared 44,100 Hz stereo timeline. Model curves keep their actual sampling rate. The native playhead reads player time and output latency; changing solo or volume does not seek or reschedule playback.

## Verification and remaining limits

The desktop includes worker/storage/media tests and Swift domain/transport tests. The following paragraphs record historical local checks, not a claim of a public installer or clean-machine validation. The historical cached-library audit was replaced with synthetic passage-hierarchy regressions for this source release. See B-111 in `../BUGS.md`. No saved song labels were changed. The current verification commands are in the root README.

The energy follow-up was exercised in a 1280 × 850 window and macOS full screen at 2560 × 1470. Slider changes, automatic reset, hiding/restoring markers, biggest-drop inspection, and zoom-dependent bar labels were checked visually. These historical screenshots are excluded from the public source snapshot.

The volume follow-up was checked in the native app at 100%, 50%, and 0%. Repeated increments stay at 100%, changing volume preserves solo selection, and the muted icon appears at 0%. A transport regression checks out-of-range values; this test needs access to macOS audio components when run in a restricted environment. All thirteen tests pass with that access. These historical screenshots are excluded from the public source snapshot.

The window-close follow-up was checked during muted playback in an isolated app. Clicking the red close button exited the app, confirmed by the running-app inventory. The release app rebuild passes.

A native rename check changed Chorus 2 to Verse 4 and immediately changed its green section to blue. Undo restored the chorus name and green. The eleven Swift tests pass, including name-based colors with stale model labels. These historical screenshots are excluded from the public source snapshot.

The selection/scrubbing follow-up was checked with Taste in an isolated app: section selection and closing inspection preserve waveform heights and plot width in a 1280 × 850 window and 2560 × 1470 full screen. Long evidence text scrolls inside the details area without a right-hand scrollbar or timeline movement. Forward and backward drags on song/stem waveforms seek to the release point; paused scrubs stay paused, and playing scrubs continue from the destination. The ten Swift tests pass, including a regression assertion for selection-independent timeline height. These historical screenshots are excluded from the public source snapshot.

Native interaction checks used cached Funkadelic and Taste recordings, followed by an isolated copy of the desktop library. Section playback, inline renaming, exact paused splitting, double-click merging, persisted undo/redo, solo routing, master volume, waveform visibility after zoom, and named comparisons were exercised in the app. The isolated copy kept current listener corrections untouched. Historical screenshots are not included in this public source snapshot.

A 12-second synthetic import completed all pipeline stages. Its section model returned no usable tempo, and the existing fallback handled it. Separately, the packaged MLX model analyzed Taste using local stems and weights and returned 113 BPM and eight structural candidates. Those checks establish integration, not musical accuracy.

This first desktop version does not reproduce the old application's complete advanced-analysis interface. It includes the core study workspace and basic evidence, but not the complete component-contribution browser, manual tempo-grid correction, cloud reasoning, or export tools. Model quality is unchanged. Bluetooth/interface latency, gapless short loops, device switching, clean-machine installation, notarization, and a full redistribution-license manifest still need release validation. Do not treat this local build as a finished public installer.

## Refresh saved energy and compare equations

The energy-only refresh stages updates before applying them. It rereads source audio for corrected stereo loudness and reuses cached analysis inputs; it preserves section edits, undo/redo history and stem assets. Do not reuse a staging directory or apply it after its source analyses have changed.

```sh
.venv/bin/python desktop/tools/recompute_energy.py prepare --out data/backups/energy-refresh-new
.venv/bin/python desktop/tools/recompute_energy.py apply --out data/backups/energy-refresh-new
.venv/bin/python desktop/tools/recompute_energy.py serve --out data/backups/energy-refresh-new --port 8850
```

The local comparison serves the saved recordings only on localhost. Backups, a manifest, candidate curves and the listening page are kept in the output directory. Applying updates saves the Balanced equation selected in the listening comparison. New analyses use the same default. The original corrected equation is retained as the historical comparator; the remaining alternatives stay experimental. See [the comparison results](../docs/research/ENERGY-EQUATION-RESULTS.md).
