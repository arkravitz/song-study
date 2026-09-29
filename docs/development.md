# Arrangement Analyzer

The native Mac redesign is now available as **Song Study**. See [desktop build and usage notes](../desktop/README.md). The original web application and its data remain available through the instructions below.

Reverse-engineers how a finished record is **arranged**, and shows its reasoning.

Upload a song. The tool separates it into sources, measures exactly when each one
enters and leaves, segments the formal structure, builds two energy curves (one
measured, one a proxy for felt intensity), measures the stereo image, and then a
language model turns that bundle of measurements into producer-level commentary —
with every claim tied back to the measurement that supports it.

The design rule throughout: **the model never gets to invent a measurement.** If
the analysis did not detect an instrument, a time or a number, the model may not
mention it, and a validator checks its output and shows ungrounded claims as
such rather than hiding them.

---

## Requirements

- macOS on Apple Silicon (MPS acceleration), or any machine with Python 3.12+
- **ffmpeg** on `PATH` — the decode path for every format, including 96 kHz/24-bit
  FLAC and lossy sources
- Node 20+ (only to build the frontend; a prebuilt `dist/` is served directly)
- A DeepSeek API key (only for the commentary layer — everything else works without it)

## Setup

```bash
# 1. Python environment
uv venv --python 3.12
uv pip install --python .venv/bin/python -e .
# On Apple Silicon, enable the primary tempo/section model:
uv pip install --python .venv/bin/python -e ".[structure-mlx]"

# 2. API key — copy the example and paste your key
cp .env.example .env
$EDITOR .env

# 3. Frontend
cd frontend && npm install && npm run build && cd ..

# 4. Run
.venv/bin/arrangement-server      # or: .venv/bin/python -m arrangement.server
# open http://127.0.0.1:8848
```

`.env` is gitignored. It holds only the key and a few knobs; the variable
contract is documented in `.env.example`.

The first analysis of a song downloads model weights and separates audio, so
budget a few minutes. On Apple Silicon with the optional MLX package installed,
All-In-One supplies the beat grid, tempo and section candidates. The analyzer
reuses its Demucs stems; source activity, energy and stereo analysis remain in
the existing pipeline. Without MLX, Beat This and local structure detection
remain the fallback. Model section names are suggestions and can be renamed.

Optional extra — instrument tagging, on top of the measured descriptors. Without
it the tool still works and simply describes what was separated:

```bash
uv pip install --python .venv/bin/python -e ".[tagging]"
```

### Optional: a better vocal stem

The default separator is Demucs v4 (6 stems) on MPS — about 3.4× realtime, so
roughly a minute and a half for a 3-minute song. At `ARRANGEMENT_QUALITY=deep` a
BS-RoFormer vocals pass is added, which is markedly cleaner but runs at about
0.2× realtime on Apple Silicon (≈14 minutes for a 3-minute song), so it is opt-in.

Because `audio-separator` pins `numpy<2` and an old librosa that would break the
rest of the toolchain, it lives in a **second virtualenv**:

```bash
uv venv --python 3.12 .venv-uvr
uv pip install --python .venv-uvr/bin/python "audio-separator[cpu]" audioread
```

If `.venv-uvr` is absent the deep profile silently falls back to Demucs.

## Using it

1. Drag a file onto the library panel, or type a folder path and press **scan** to
   browse your collection without uploading.
2. Watch the stage list. Analysis takes ~1–2 minutes per song at standard quality.
3. Click anything: a section band, an event pin, a source lane, a pattern.
4. Press **Name sections** on the overview tab for the model's reading of the
   form. Until you do, the sections carry the measurement-only heuristic labels,
   and the panel says so.

### The timeline

| Row | What it shows |
| --- | --- |
| ruler | bar numbers and absolute time |
| sections | formal structure, colour-coded by label |
| waveform | min/max peaks with perceived energy filled behind |
| energy | perceived (purple) and measured raw (grey) curves |
| events | discrete arrangement changes; continuous ones drawn as bars |
| stereo width | mid/side energy ratio, scaled to the song's own range |
| onset density | how rhythmically busy the arrangement is |
| source lanes | one per detected part: when it plays and how loud |

**Controls.** Space plays. Click or drag to scrub. Wheel zooms about the cursor,
shift+wheel pans, double-click fits. Shift-drag selects range A and alt-drag range
B, which feed the comparison tools. `+`/`−`/`0` zoom, `Home` fits, arrows nudge 5 s.

### What the analysis will and will not claim

Stereo measurement is the easiest thing to overstate, so the distinction is made
explicit in the code and in the UI:

- **Measured:** left/right energy balance, mid/side width, inter-channel
  correlation, and all three per frequency band.
- **Not recoverable:** the position of the original pan controls. A hard-panned
  guitar and a wide reverb produce very similar side energy for entirely different
  reasons, and no analysis of a stereo master can tell them apart. The UI says so.
- **Mono sources** are detected and reported as having no recoverable image, rather
  than measured on duplicated channels.

Section *names* (verse/chorus/bridge) are a judgement, not a measurement, and the
two are deliberately separated:

- **Repetition grouping is measured.** Which passages are the same music comes
  from a centred chord-progression similarity plus which parts are playing.
- **The names are the model's**, made from that measured profile, restricted to a
  fixed 15-word vocabulary, with the structural rules enforced in *code* rather
  than left to the prompt: sections sharing a repeat group always share a name,
  and material that occurs mid-record is never called an intro. Trusting the
  prompt alone is how an "intro" ends up two minutes into the record.

Confidence is shown and is calibrated against how distinctive the group actually
is — a model that reports 0.9 for every group tells the user nothing. The
inspector also lists the runner-up labels with their scores, so you can see what
else the passage plausibly was.

## Configuration

Everything is an environment variable; see `.env.example` for the full contract.

| Variable | Default | Notes |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | — | required only for commentary |
| `DEEPSEEK_MODEL` | `deepseek-flash` | fast; good enough for most tasks |
| `DEEPSEEK_MODEL_DEEP` | `deepseek-v4-pro` | slower, used for "lessons"/"deeper" |
| `ARRANGEMENT_QUALITY` | `standard` | `fast` / `standard` / `deep` |
| `ARRANGEMENT_SEPARATOR` | `multi` | 6-stem Demucs by default |
| `ARRANGEMENT_DEVICE` | `auto` | `auto` / `mps` / `cuda` / `cpu` |
| `ARRANGEMENT_GROUP_FLOOR` | `0.68` | repeat-grouping similarity floor |
| `DEEPSEEK_EFFORT` | `high` | `low` / `high` / `max`; per-task overrides win |

## How it works

```
audio ─► decode ─► features ─┬─► beat/downbeat/tempo/meter
                             ├─► separation ─► per-source activity spans
                             ├─► loudness (BS.1770 via ffmpeg ebur128)
                             ├─► stereo (mid/side, per band)
                             └─► structure (SSM → novelty → sections → repeat groups)
                                        │
                                        ▼
                              intermediate representation (IR)
                                        │
                    ┌───────────────────┴───────────────────┐
                    ▼                                       ▼
             API / UI (canvas timeline)          DeepSeek (commentary)
                                                        │
                                                        ▼
                                             grounding validator
```

The IR (`backend/arrangement/schema.py`) is the contract. Every event, section
and claim carries `Evidence` records naming the model or DSP method, the metric,
the time range and the measured value, so the UI can always answer "why does it
think that?".

### Layout

```
backend/arrangement/
  config.py            settings, quality profiles, device resolution
  schema.py            the IR — the contract between every layer
  audio/io.py          ffmpeg-first decode, probe, hashing
  dsp/                 features, activity, rhythm, structure, spatial, loudness, energy
  models/              model registry + separation backends
  analysis/            pipeline, events, patterns, section naming
  llm/                 client, prompt construction, grounding, tasks
  server/              FastAPI app, analysis store, peak pyramid
backend/bench/
  sweep.py             batch accuracy report against optional truth files
  activity_map.py      ASCII per-stem activity map, for tuning the gate
backend/tests/         method tests
frontend/src/          React + canvas timeline
```

### Notable implementation choices

**MPS is the reason this is usable at all.** Demucs 6-stem takes ~61 s for a
2:47 track on MPS versus 6m24s on CPU. The `deep` profile's RoFormer pass is
subprocess-isolated so its `numpy<2` pin cannot leak into the main environment.

**Activity detection is the core of the product.** Per-source entrance and exit
times come from an Otsu threshold on a *median-filtered* level envelope, with
hysteresis so a part hovering at the threshold does not chatter into dozens of
fragments. Thresholds are tuned per source family, because a kick drum, a bass
line and a pad need very different gap-closing.

**Repeat grouping uses a centred similarity matrix.** L2-normalised chroma vectors
are non-negative, so *every* pair of sections has a cosine similarity near 0.9
whether or not they share a chord. Taking block means of that raw matrix produces
values in a 0.88–0.93 band and no discrimination at all. Subtracting each matrix's
own off-diagonal mean moves the typical value to zero, so a threshold placed
relative to the distribution means something. Grouping is then centroid-assigned,
not single-link, so a chorus the boundary detector cut in half still groups with
its repeats, and a level gate stops a quiet outro from joining a loud chorus.

**Activity detection is worth an ASCII map.** Entrance and exit times come from an
Otsu threshold on a *median-filtered* level envelope: a short RMS window dips
between strums and reports one sustained guitar as two dozen strokes, and ~0.3 s of
smoothing spans the gaps. What actually gets it right is being able to look at it —
see `activity_map.py` above. A fixed threshold tuned on aggregate statistics
produced a five-entrance bass part that plays continuously, and no summary
statistic would have shown that.

**Prompts are built to be small.** The song card quantises every number to two or
three significant figures, which roughly halves the token count and costs nothing:
a 4-minute song is ~13k tokens including the full event table, not hundreds of
thousands of curve samples. Windowed questions get a 0.5 s-resolution detail
bundle instead of the whole song.

**Grounding is enforced, not requested.** Time references must resolve to a time
the analysis knows about; instrument names are checked against the detected
sources using a vocabulary derived from the project's own layer taxonomy (so the
two cannot drift apart); numbers are matched to measurements. Ungrounded claims are
displayed in red with the reason, not dropped — the user can judge the model.

**A failed model call is never a successful one.** If reasoning exhausts the token
budget, the client raises rather than returning an empty string, and if a task
gets no usable output the endpoint fails instead of quietly falling back. An
empty response is indistinguishable from a model with nothing to say, so every
caller would silently degrade to its fallback and the user would see heuristic
labels presented as a model's.

## Evaluation

The public source checks run on synthetic fixtures without a saved music library:

```sh
pytest backend/tests desktop/worker/tests -q
npm test --prefix frontend
npm run build --prefix frontend
```

They cover stereo measurements, time alignment, source activity, energy calculations, grounding, section editing, and section-naming constraints. See the root README for native checks and a playable synthetic demo.

Earlier local evaluation used non-distributed `backend/bench` scripts, private audio and manually annotated sidecars. Those datasets, scripts and historical benchmark commands are not part of this public snapshot. The historical observations are not an independently reproducible public accuracy benchmark.


## Known limits

- **Instrument identity is coarse.** With 6 stems you get "guitar", not "clean
  electric guitar". The measured descriptors (centroid, flatness, crest, width)
  describe what was separated, and the UI presents them as such.
- **No drum breakdown.** A 6-stem model gives you "drums" as one lane. Detecting
  kick/snare/hat inside the stem would need a dedicated drum model.
- **Tangential material is flagged, not solved.** When the beat tracker's own IOI
  spread shows free time, bar numbering is marked unreliable and bar-referenced
  claims are flagged as such.
- **Lossy sources are marked.** Codec artefacts affect transient and stereo
  measurements; the analysis says so rather than pretending otherwise.
- **Repeats are graded, and grouping favours precision over recall.** A chorus that
  lasts sixteen bars often arrives as two or three repeat groups. On loose-form
  material — J. Cole's "Apparently", Les Baxter's "Ecstasy" — the repeat groups
  come back all-singletons, so naming works per-section and "repeats of this
  passage" is empty. Lowering `ARRANGEMENT_GROUP_FLOOR` to 0.64 finds plausible
  repeat pairs in those tracks; it was not made the default because on denser pop
  it also merges a chorus with a verse, and a mislabelled chorus is worse than two
  unlinked ones.
