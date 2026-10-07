# Song-study foundations: technical research

Reviewed September 30, 2026. This note separates source facts from proposed engineering choices. No new performance or model-accuracy benchmarks were run.

## Recommended foundation

For a Mac-first product, use a native SwiftUI shell, an AppKit-backed custom timeline where needed, AVAudioEngine for playback, and a bundled Python analysis worker in a separate process. Keep the existing useful analysis until measured replacements justify a migration. The recommendation is an engineering judgment, not evidence that a native rewrite alone fixes model errors or makes playback correct.

The native timeline still needs deliberate rendering, hit-testing, keyboard focus, accessibility, clipping, semantic zoom, and gesture design. Standard SwiftUI controls do not supply a finished arrangement editor.

## Structure and tempo

The original All-In-One returns tempo, beats, downbeats, section boundaries, and functional labels. Its loader uses PyTorch and downloads pretrained checkpoints from `taejunkim/allinone`. Its `harmonix-all` model is an eight-model ensemble. [Upstream repository](https://github.com/mir-aidj/all-in-one), [checkpoint loader](https://raw.githubusercontent.com/mir-aidj/all-in-one/main/src/allin1/models/loaders.py).

The MLX port targets Apple Silicon and macOS 14+, returns the same classes of structural output, and explicitly aims at upstream parity. It supports externally supplied or cached stems, so a product should avoid separating the same song twice. Releases do not bundle weights; standalone downloads are pinned and hash-verified. Its documented speed example is one file on one M4 Max and is not a product performance promise. [MLX port](https://github.com/ssmall256/all-in-one-mlx).

**Decision:** pin the already integrated MLX implementation as the initial Mac worker, preserving the original implementation as a regression reference. Treat boundaries and names as editable proposals. An acceleration port is not demonstrated better semantic understanding. Retain beat timestamps rather than reducing rhythm to a single BPM; keep any half/double-time or manual grid choice distinct from the underlying result. The existing local [MLX benchmark](../MLX-BENCHMARK.md) remains relevant and records known labeling limitations.

**Licenses are separate artifacts:** both code repositories use MIT licenses. The upstream weights repository independently declares `license: mit`, but its model-card body is empty. Record code commit, weight revision/hash, originating model, and individual notices in a release manifest. The sources establish those declarations; this review did not audit every converted checkpoint or all transitive dependencies. [Upstream code license](https://raw.githubusercontent.com/mir-aidj/all-in-one/main/LICENSE), [port code license](https://raw.githubusercontent.com/ssmall256/all-in-one-mlx/main/LICENSE), [upstream weights metadata](https://huggingface.co/taejunkim/allinone/raw/main/README.md).

## Separation and energy

The Meta Demucs repository was archived January 1, 2025. Its author points to a fork while saying only important bug fixes will be processed; it is not an actively developing product dependency. [Repository status](https://github.com/facebookresearch/demucs), [author fork](https://github.com/adefossez/demucs).

Demucs provides four-source separation and an experimental six-source model. The official README warns specifically of piano bleeding and artifacts. It also warns that independently rescaling exported stems to avoid clipping can alter their relative levels. Its project license is MIT; this review did not independently establish a separate license record for every downloadable checkpoint. [Demucs documentation](https://raw.githubusercontent.com/facebookresearch/demucs/main/README.md), [license](https://raw.githubusercontent.com/facebookresearch/demucs/main/LICENSE).

**Decision:** make vocals, drums, bass, and remaining accompaniment the reliable initial separation vocabulary. Preserve access to existing six-source results, with their limitations; do not promise that piano or guitar lanes prove those instruments are present. Keep a replaceable separator adapter, but do not migrate merely because another repository is newer. Benchmark any candidate with listening tests and actual target Macs before selecting it.

**Energy design judgment:** retain the existing perceived-energy calculation as the baseline. Make its declared/applied components agree, preserve component contributions for evidence, and version every curve. Analyze consistently calibrated audio, not independently normalized stem exports. Separate full-song ending silence from the robust display range and from internal-drop rankings. A relative curve should not imply an absolute cross-song energy score. Source activity should require audible participation beyond separation leakage; an almost empty stem must not become a long confident instrument bar.

## Playback clock and audio files

AVAudioPlayerNode schedules buffers or file segments at defined times. File-segment scheduling takes a starting frame and frame count. Apple describes separate node and player timelines and conversion between them; stopping a node resets the player timeline and discards scheduled events. [Player-node documentation](https://developer.apple.com/documentation/avfaudio/avaudioplayernode), [segment scheduling](https://developer.apple.com/documentation/avfaudio/avaudioplayernode/schedulesegment(_:startingframe:framecount:at:completionhandler:)).

The engine exposes the latest render time and player-time conversion; output presentation latency describes downstream render-pipeline delay. These are useful primitives, not a guarantee that an arbitrary on-screen timer matches audible output. [Render time](https://developer.apple.com/documentation/avfaudio/avaudionode/lastrendertime), [player time](https://developer.apple.com/documentation/avfaudio/avaudioplayernode/playertime(fornodetime:)), [presentation latency](https://developer.apple.com/documentation/avfaudio/avaudionode/outputpresentationlatency).

**Decision:** one transport owns an integer frame position in a canonical decoded song. Mix and stems share the same frame-zero, sample rate, and scheduled start. Seek reschedules all sources together. Solo/mute/volume change gain routing with short ramps and do not stop or recreate the transport. UI drawing observes audio time; it never advances the song by accumulating animation-frame deltas. Evaluate presentation compensation with built-in output, headphones, Bluetooth, and external audio interfaces. Keep analysis resampling offsets explicit. These are implementation requirements, not verified outcomes of this design note.

Web Audio is a valid alternative: its specification supports sample-accurate scheduling and an audio graph, so prior browser bugs do not prove browser audio inherently fails. A web implementation still needs one AudioContext, shared start offsets, output-time-aware drawing, and bounded memory or streaming for long multistem files. Test actual WebKit/browser behavior rather than assuming every draft API is supported. [W3C Web Audio specification](https://www.w3.org/TR/webaudio/).

## Worker and distribution

Foundation Process provides executable arguments and standard-stream pipes, suitable primitives for a private request/progress/result protocol. Argument arrays do not undergo shell expansion. [Process input](https://developer.apple.com/documentation/foundation/process/standardinput), [arguments](https://developer.apple.com/documentation/foundation/process/arguments).

**Decision:** ship a pinned runtime and worker; do not ask musicians to install Python or packages. Use validated messages over private pipes, bounded jobs, cancellation, and restart after a worker failure. Audio playback remains in the app process. Store large curves and waveforms as indexed binary artifacts, with job and analysis metadata in a small transactional database. Cache by content hash and analysis-version manifest, never basename alone.

Tauri is the explicit alternative when retaining React or reaching other desktop platforms matters. It supports bundled Python executables, architecture-specific sidecar names, and scoped spawn permissions. This does not establish that an MLX/PyTorch frozen worker will package successfully: a clean-machine proof of concept is still required. [Tauri sidecars](https://v2.tauri.app/develop/sidecar/).

For direct Mac distribution, sign bundled executables, adopt hardened runtime, notarize the deliverable, and test its installed version away from the development environment. Model data can download separately; executable runtime dependencies belong in the signed distribution. [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution), [packaging](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution).

Model downloads should be explicit and resumable, with size, purpose, version, license link, checksum, progress, and deletion controls. After installation, song analysis should work offline. The port's hash-verified download path is useful, but final product support must cover missing caches and interrupted downloads without exposing developer errors.

## Unverified before committing to a rebuild

- Packaged Python/MLX/PyTorch size, signing, notarization, and clean-Mac execution.
- Minimum supported Mac memory, practical separation throughput, and cancellation responsiveness.
- Label and beat accuracy across genres, live tempo, and deliberately ambiguous song forms.
- Auditory alignment under changing output devices and multiple simultaneous stems.
- Transitive code, codec, model-checkpoint, and redistribution licenses.
- Custom native timeline responsiveness and accessibility at real library scale.

These should be short feasibility experiments before a broad rewrite. Research confirms available primitives and upstream limitations; it does not substitute for these measurements.
