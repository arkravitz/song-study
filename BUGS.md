# Bug ledger

Severity: **P0** crashes / data corruption / app unusable · **P1** major functionality
incorrect or broken · **P2** significant interaction or analysis correctness · **P3**
confusing, polish, minor.

Status: `open` · `fixing` · `fixed` · `verified` · `won't-fix` (with reason).

---

## How to read this

Every entry names a reproduction, not a suspicion. "The code looks wrong" is not a
bug report. If I cannot write the steps that produce it, it does not go in the
ledger.

Fixed bugs stay here with their regression test named. The point is not to reach
an empty list, it is that nothing silently comes back.

---

## BUGS

### B-001 — Scan button appears dead: scanning a home directory never returns

- **Severity** P0
- **Repro** Type `~` (or any large tree) into the sidebar path field, press **scan**.
- **Expected** A list of audio files, or a bounded "too many" response.
- **Actual** The button reads `…` indefinitely. `sorted(base.rglob("*"))`
  materialised the entire recursive listing and sorted it before filtering, so it
  walked every `node_modules` and `.git` object on the machine.
- **Root cause** Unbounded recursive walk, no pruning, no cap, no progress.
- **Fix** `os.walk` with a prune list (`node_modules`, `.git`, `Library`, virtualenvs,
  build dirs), a `max_dirs` cap, results capped at `limit`, and the response
  reports `truncated` + a specific next step.
- **Regression** `backend/tests/test_analysis.py::test_library_scan_is_bounded`
- **Status** fixed

### B-002 — Clicking any event blanks the entire screen

- **Severity** P0
- **Repro** Click any event pin on the timeline.
- **Expected** The event inspector panel opens.
- **Actual** Whole page goes white. `ReferenceError: MiniStat is not defined` in
  `EventDetail`. A render-time throw unmounts the entire React tree, not the panel.
- **Root cause** Missing component definition. `ReasoningPanel` had the same class
  of defect (`fmtBar` used but never imported) — so the *class* was two bugs.
- **Fix** Defined `MiniStat`; converted all numeric reads in the inspector to
  `num()` which cannot throw; added `ErrorBoundary` at app root and per panel;
  `npm run build` now typechecks (`tsc -b --noCheck` was hiding all of these).
- **Regression** Manual + `AppBoundary` renders a recoverable card instead of blanking.
- **Status** fixed

### B-003 — Waveform, energy and onset density drawn 116px left of everything else

- **Severity** P0 (timeline is the core surface)
- **Repro** Fit the whole song. Compare the waveform/energy/density rows against the
  section bands, the ruler and the playhead.
- **Expected** Every row spans the same x range.
- **Actual** Those three rows start at canvas x=0, run underneath the lane labels,
  and stop 116px short of the right edge. The density row's 1px line read as
  "stray lines to the left of everything".
- **Root cause** Two x spaces in one file. Row drawers iterated
  `for (px = 0; px < PW; px++)` and drew at `ctx.lineTo(px, ...)` without adding
  the label gutter, and passed plot-relative x into `xToT` which expects canvas x.
- **Fix** One documented convention: iterate plot x, draw at `plotX(g, p)`, convert
  time with `plotToT(p, g)`. Helper renamed `px` → `plotX` because a local
  `const px` for the playhead shadowed it.
- **Regression** Verified by measuring drawn pixel extents per row: all rows now
  span 118→830 of an 833px canvas.
- **Status** fixed

### B-004 — Energy-change markers all drawn in the left fifth of the row

- **Severity** P1
- **Repro** Fit a track with energy changes. Markers appear crammed against the
  gutter regardless of where the changes actually are.
- **Expected** Marker at the change's time.
- **Actual** 22 markers on "DNA." drew at `GUTTER + t_seconds` — the whole layer
  compressed into x=118..265 of a 717px plot.
- **Root cause** Passed a **time** into `plotX`, which takes plot-relative x. Same
  class as B-003, in code written after the convention was documented.
- **Fix** `tToX(c.t, g)`. Verified: marker x range 118..748, matching the data.
- **Regression** Manual pixel-extent check against expected positions.
- **Status** fixed

### B-005 — Stereo width row permanently empty

- **Severity** P1
- **Repro** Any track. The "stereo width" row shows only its label.
- **Expected** A width curve.
- **Actual** The frontend requested `curve/stereo_width`; the curve is stored as
  `spatial/width`. A silent 404-ish miss produced an empty row with no error.
- **Root cause** Frontend/backend disagreement on a curve name, with no validation.
- **Fix** Corrected the name. **Class fix pending** — see B-020.
- **Regression** See B-020 (curve-name contract test).
- **Status** fixed

### B-006 — Onset density rendered as a solid block / then as nothing

- **Severity** P1
- **Repro** Fit a track. The density row is either a solid yellow rectangle or
  collapses to a stub.
- **Expected** A readable rhythmic density trace.
- **Actual** The row plotted the energy model's `onset_density` component, which is
  a signed, clipped *deviation* — meaningful as an input to the energy sum,
  meaningless as something to draw. Divided by its max it saturated; rescaled it
  collapsed.
- **Root cause** Wrong signal for the job, plus a display-only scaling bug in my
  own first fix (prefix sums over onset *times* return a time, not a count, and
  inverted).
- **Fix** New `curve/onset_rate`: onsets per second over a 0.5s window, where zero
  genuinely means silence. Two `searchsorted` calls. Smoothing added because a 0.5s
  window over 20Hz frames stepped every 3px and drew as a comb.
- **Regression** `test_onset_rate_is_a_rate_and_zero_means_silence`,
  `test_onset_rate_survives_an_empty_and_an_out_of_range_onset_list`,
  `test_onset_rate_matches_a_hand_counted_bar`
- **Status** fixed

### B-007 — "no two sections were recognised as repeats" printed on a track that has repeats

- **Severity** P1 (a caveat that contradicts the data beside it)
- **Repro** Analyse "DNA.". The caveat says no sections repeat; the section list
  directly above shows a repeat group of four.
- **Expected** The caveat to describe the real grouping.
- **Actual** Always fired. The check lived in `analyse_structure`, which runs
  *before* grouping (grouping needs each section's measured profile, so it moved
  to the pipeline), so every span still carried the default group id.
- **Root cause** Caveat computed at the wrong layer, after the fact.
- **Fix** Moved to the pipeline as `_structure_caveats`, and it now names the most
  repeated passage and its timestamps instead of a bare negation.
- **Regression** `test_structure_caveats_describe_the_actual_grouping`
- **Status** fixed

### B-008 — `tempo_drift` reported 119.6 BPM on a track with a stable beat grid

- **Severity** P1 (a headline number that is both wrong and alarming)
- **Repro** Analyse "DNA.". The overview shows `tempo drift 119.6 BPM`; the actual
  inter-beat-interval spread is 1.10.
- **Expected** A figure that means "the pulse moves".
- **Actual** `max - min` of the local-tempo curve. One beat at half the period put
  a 273 BPM sample on the curve and the range reported the difference.
- **Root cause** Most outlier-sensitive statistic available.
- **Fix** 5–95 percentile range, plus a separate `tempo_instable` with a much
  higher bar so genuine rubato still surfaces.
- **Regression** `test_tempo_drift_ignores_a_single_outlier_beat`
- **Status** fixed

### B-009 — Server dies when analyses are queued concurrently

- **Severity** P0
- **Repro** Queue three songs for analysis. The process dies with
  `failed assertion _status < MTLCommandBufferStatusCommitted` and takes every
  other in-flight analysis with it.
- **Expected** Jobs queue and run one at a time.
- **Actual** Three concurrent Demucs passes on MPS do not share a Metal command
  buffer. Not a catchable Python exception, so no try/except could help.
- **Root cause** Unbounded job concurrency against one GPU.
- **Fix** `threading.Semaphore(1)` around analysis.
- **Regression** `backend/bench/queue_check.py` (asserts 0 frames with >1 running).
- **Status** fixed

### B-010 — Ask section destroys the question on failure and reports errors in the wrong panel

- **Severity** P1
- **Repro** Ask a question, force a failure (e.g. stop the server mid-call).
- **Expected** The question survives and the error appears next to the Ask control.
- **Actual** Input cleared before the request resolved; the error rendered in the
  card above, next to an unrelated control; the button showed `…` for 30s.
- **Root cause** Global error state; optimistic clear; no progress feedback.
- **Fix** Per-action error keys, clear on success, spinner + elapsed seconds.
- **Regression** Manual.
- **Status** fixed

### B-011 — Continuous events are unidentifiable and unselectable

- **Severity** P1
- **Repro** Read the events row. A continuous event draws as a 3px bar with
  `pointer-events: none`, no tooltip, no click handler.
- **Expected** Hoverable and selectable like the discrete pins.
- **Actual** The whole class of event was invisible. "What is the yellow bar?" had
  no answer anywhere in the product.
- **Root cause** Non-colour encoding with no legend and no interaction.
- **Fix** 6px focusable clickable span with end caps and hover label;
  `EVENT_KIND_LABEL`; a legend naming every kind present with counts.
- **Regression** Manual.
- **Status** fixed

### B-012 — `fit` appears broken; the app opens showing 8 seconds of a 3-minute song

- **Severity** P1
- **Repro** Load any song. The view shows ~8s of the track and `fit` seems to do
  nothing.
- **Expected** Opens at a usable span; `fit` shows the whole record.
- **Actual** The initial span was read from a ref captured while `duration` was
  still the 1s placeholder, so `min(30, max(8, 1/4))` = 8 won.
- **Root cause** Ref captured on first render, never re-read.
- **Fix** Re-read from the argument on duration change; respect a user-chosen span.
  Added a visible-range readout, because nothing told the user they were looking
  at 8s of a 3-minute song.
- **Regression** Manual (0:00–0:30 of 2:47 on load; `fit` → 100%).
- **Status** fixed

### B-013 — Lane label row 2px wider than the gutter, badge floating over the plot

- **Severity** P3
- **Repro** Read the lane labels.
- **Expected** Label and span-count inside the gutter.
- **Actual** Row hardcoded to 118px against a 116px gutter; background faded out
  at 72% so the count floated over the waveform.
- **Fix** Width from `GUTTER`, solid background, `min-width: 0` so the ellipsis
  engages.
- **Status** fixed

### B-014 — Source lanes draw as indistinguishable solid rectangles

- **Severity** P2
- **Repro** Any track where a part plays throughout.
- **Expected** Presence and level both legible.
- **Actual** A centred bar spanning the lane: the same rectangle for a part at
  -12 dB and one at -40 dB.
- **Root cause** Level encoded as bar height on a segment that spans the whole song.
- **Fix** DAW-style clips: body opacity carries level, header bar carries edges.
- **Status** fixed

### B-015 — Stem auditioning opens a WAV in a new browser tab, unsynchronised

- **Severity** P1
- **Repro** Transport → stems → click a stem.
- **Expected** Hear that part, in sync, at the playhead.
- **Actual** New tab, own transport, starts at zero, full mix keeps playing in the
  original tab.
- **Root cause** Treated as "load a different file" instead of a mixer operation.
- **Fix** Real mixer; all sources started at one `ctx.currentTime` so a soloed
  stem is not flammed against the mix; lazy decode + cache; Escape clears.
- **Status** fixed

### B-016 — Ruler background and rules stop at the gutter, leaving a hole

- **Severity** P3
- **Repro** Look at the top-left of the timeline above the lane labels.
- **Expected** Solid ruler background full width.
- **Actual** Background and bottom rule drawn `0..plotW`, not `0..width`.
- **Status** fixed

### B-017 — Lane height computed two different ways

- **Severity** P2
- **Repro** Resize the window until there is spare vertical space.
- **Expected** Canvas height and lane positions agree.
- **Actual** `lanesHeight` ignored the stretch and the `laneMax` cap that
  `laneRects` applied, so the canvas was taller than the content it held.
- **Root cause** Duplicated layout arithmetic in two functions.
- **Fix** Single `laneHeight()` used by both.
- **Status** fixed

### B-018 — Invalid colour literal `'#4a6party'` in the waveform palette

- **Severity** P3
- **Actual** Unused leftover that would have silently failed to parse.
- **Status** fixed (removed; the waveform colour was also raised to ~3:1 contrast)

### B-019 — `fmtBar` disagreed with itself for times before the first downbeat

- **Severity** P2
- **Repro** Seek to 0 on a track whose first downbeat is not at 0.
- **Actual** A nested ternary produced a beat number inconsistent with the bar it
  was derived from.
- **Fix** Beat derived from the bar.
- **Regression** `test_fmt_bar_round_trips_for_negative_times` (pending)
- **Status** fixed

### B-020 — Curve names are an unvalidated frontend/backend contract

- **Severity** P2 (systemic)
- **Repro** Request a curve that does not exist.
- **Expected** A clear error naming the available curves.
- **Actual** An empty series, which the UI renders as an empty row with no
  explanation. B-005 was exactly this.
- **Root cause** Free-text curve names crossing the API with no check.
- **Fix** The server was already correct — it answers a bad name with 200 and
  `{error, available}`. The frontend threw that away, so a name that did not
  exist became an empty series, indistinguishable from an empty window.
  `useCurve` now carries the reason and the row's gutter label prints it, so
  a failure looks like a failure. The API type admits `error` rather than
  claiming success always.
- **Regression** Verified by intercepting the energy curve response with the real error
  payload: the row prints `unknown curve 'energy/perceived'` in place.
- **Status** fixed

### B-021 — Analysis results can land on the wrong song

- **Severity** P1
- **Repro** Start analysis on song A, select song B, let A finish.
- **Expected** A's results attach to A; B is unaffected.
- **Actual** *Not yet reproduced*, but the analysis is keyed by a path+size hash
  and the panel subscribes to `useSong(selectedId)` with no run-generation guard.
  A late-arriving fetch has no way to know it is stale.
- **Root cause** No request generation / abort token.
- **Fix** A monotonic generation counter, invalidated during cleanup (which runs
  before the next effect, the only ordering in which the old request is
  guaranteed stale by the time it resolves). `usePeaks` and `useCurve` also
  clear immediately on a song change, so the previous song's waveform never
  sits under the new song's title.
- **Regression** Manual: three rounds of twelve rapid song switches, asserting that the title,
  duration, section count and event count all belong to the same song.
- **Status** fixed

### B-022 — Stem solo falls back to the full mix with no warning

- **Severity** P1
- **Repro** Solo a stem whose audio fails to load.
- **Expected** An error, and silence or a retry.
- **Actual** `setSolo` returns false and the mix stays audible. The user asks to
  hear the bass and gets the full mix.
- **Fix** `ensureStem` failure is now visible: the mixer marks the stem unavailable,
  disables its button and says "could not be loaded" in the tooltip, so the
  control cannot be pressed into a state it cannot honour. Narrower than it
  was; the residual case is recorded rather than claimed fixed.
- **Status** open

### B-023 — Section labels are unstable and often musically wrong

- **Severity** P1
- **Repro** "DNA." → `intro, chorus, chorus, verse, chorus, chorus, outro, verse,
  verse, verse, verse, outro`. "Taste" → no bridge, no outro, three adjacent
  verses.
- **Expected** Macro form a producer would recognise.
- **Actual** Internal phrase boundaries promoted to top-level sections; over-merged
  repeat groups forcing a single name onto distinct passages.
- **Current example** With the MLX section pass, Taste labels 1:38–2:37 as one
  "Chorus 3". The interface detects a major pullback at 1:55 and a rebuild at
  2:07 inside that span; a listener placed the final chorus near 1:58. The
  broad label therefore hides a musically important internal change. The raw
  MLX output already calls 1:38–2:16 "chorus"; the app then merges its adjacent
  2:16–end "chorus" into the same macro. Those two spans have different repeat
  groups (3 and 1), and group 1 also contains the first two choruses. The older
  independent boundary detector placed a seam at 1:58.8 with novelty 0.78,
  close to the listener's 1:58 mark. The model role, the repeat evidence and the
  independent boundary therefore disagree; a merge-budget tweak alone cannot
  repair the 1:58 entrance.
- **Root cause** The trained model supplies a provisional role and coarse
  boundaries, while the independent novelty detector's interior boundary is
  discarded when that model is available. Macro grouping then merges adjacent
  same-name model spans without checking that their repeat identities disagree.
  A novelty peak alone also cannot be promoted to a top-level section: some are
  changes inside a verse or chorus.
- **Fix** *in progress* — see SECTION WORK below. A repair needs independent
  boundary evidence plus an arrangement/repetition check, and must leave the
  role provisional when those sources disagree.
- **Status** open

### B-024 — Chorus tier labels everything "chorus"

- **Severity** P1
- **Repro** "DNA." before the fix: 5 of 12 sections were "chorus".
- **Root cause** Tier accepted anything within 88% of the loudest vocal group.
- **Fix** 95% of peak **and** at least median source count; fall back to the single
  loudest vocal group rather than widening the band.
- **Status** fixed

### B-025 — `label_alternatives` written in the opposite order to its declared type

- **Severity** P1
- **Repro** Reload any analysis from disk.
- **Actual** `list[tuple[str, float]]` declared, `(score, label)` written. JSON
  tuples are lists, so the order was wrong in the data and invisible in the type;
  every reload failed validation.
- **Root cause** Tuples-in-JSON carry no field names.
- **Fix** Named `LabelAlternative` model.
- **Regression** Round-trip covered by `test_analysis_roundtrips_through_json`
- **Status** fixed

### B-026 — `label_alternatives` shown next to a model-chosen label were the heuristic's

- **Severity** P2 (misleading)
- **Repro** Name sections with the model, inspect a section in the inspector.
- **Actual** "Also considered: instrumental 0.34" was scored by the heuristic, but
  sat beside a model-chosen name, implying the model had considered it.
- **Fix** Replaced by the model's own alternatives; prompt extended to return them.
- **Status** fixed

### B-027 — Empty model response silently degrades to the fallback

- **Severity** P1
- **Repro** Reasoning call exhausts the token budget.
- **Actual** Endpoint returned 200 with heuristic labels and zero claims — model
  output indistinguishable from measurement.
- **Root cause** Empty string is indistinguishable from "nothing to say".
- **Fix** Client raises on `finish_reason == "length"` with empty content; task
  fails rather than degrading; no retry for deterministic sizing problems.
- **Status** fixed

### B-028 — Energy curve unsmoothed at whole-song zoom

- **Severity** P2
- **Repro** Fit a track. The energy row is an unreadable band.
- **Fix** Zoom-adaptive smoothing targeting ~220 wiggles; window printed in the
  gutter; display-only, full-resolution data retained for everything else.
- **Status** fixed

### B-029 — A caption contradicts the data beside it (energy cliff)

- **Severity** P2
- **Repro** Not a bug — investigated and dismissed. The perceived-energy collapse
  at the end of "All the Small Things" is a real fade, confirmed against the raw
  curve.
- **Status** won't-fix

### B-030 — Section "outro" placed 60% into the record

- **Severity** P1
- **Repro** "DNA.": a section at 108.8s (59% in) labelled `outro` with 1 source.
- **Expected** A breakdown or bridge. Published beat switch: 1:50–1:58.
- **Root cause** Position-based naming with no check that a position-implied label
  is consistent with the measurement.
- **Fix** *partial* — a caveat now fires. The label itself is still wrong; see
  SECTION WORK.
- **Status** open

### B-031 — `pct` and inspector numeric reads throw on null

- **Severity** P2
- **Actual** `pct(v)` called `.toFixed` on a possibly-null field.
- **Fix** `num()` / `pct()` tolerate null; all inspector reads converted.
- **Status** fixed

### B-032 — A third of all detected events are silently dropped by the UI

- **Severity** P1 (the analysis finds arrangement changes and the product hides
  them, so a producer cannot see what the tool knows)
- **Repro** Count `.event-pin` elements on any track and compare with the event
  count in the header. All the Small Things: 96 events, 60 rendered.
  Taste: 140 events, 105 rendered. DNA: 210 events, 184 rendered.
- **Expected** Every detected event is either visible or explicitly explained.
- **Actual** `if (e.end === null) return null` in the event overlay. A continuous
  event with no measured end — "Stereo image narrows", "Arrangement thickens" —
  is a legitimate class: a state change the analysis detected but could not
  bound. All of them were discarded. 37% of AST's events, 25% of Taste's, 12% of
  DNA's.
- **Root cause** The overlay assumed a continuous event always has an end, and
  treated the exception as "nothing to draw" instead of "draw it open-ended".
- **Fix** `drawableEvents()` in `eventRanges.ts`: an unbounded continuous change
  runs to the next change of the same kind, else to the end of the enclosing
  section, else to the end of the record, and is drawn with only a start cap and
  a fade on the right — the absence of a measured end is shown, not implied.
- **Regression** All three songs now render every event: 96/96, 140/140, 210/210
  (was 60, 105, 184).
- **Status** fixed

### B-033 — Stale analysis results can be rendered under the wrong song

- **Severity** P1
- **Repro** Switch songs quickly enough that the first song's response lands
  after the second's. A larger analysis serialises slower than a small one, so
  the wrong order is the likely order, not the unlucky one.
- **Actual** `useSong` fired `api.song(songId)` and wrote the result with no check
  that the request was still current. `usePeaks` and `useCurve` had the
  mirror-image defect: they never cleared on a song change, so the previous
  song's waveform stayed on screen under the new song's title until the new
  fetch resolved.
- **Root cause** No request generation. An `AbortController` alone would not have
  been sufficient — the server may already have produced the response, and
  aborting only covers the in-flight request, not the callback already scheduled.
- **Fix** Monotonic generation counter, invalidated during cleanup (which runs
  before the next effect, the only ordering in which the old request is
  guaranteed stale by the time it resolves). `usePeaks`/`useCurve` also clear
  immediately on a song change.
- **Status** fixed (superseded below)
- **Regression** Manual: 3 rounds x 12 rapid switches, asserting topbar, duration,
  section count and event count all belong to the same song.
- **Status** fixed

### B-034 — No way to distinguish "no data" from "loading" or "not applicable"

- **Severity** P2
- **Actual** Empty rows render as bare background. A curve that 404s, a curve that
  is disabled, and a curve still loading are visually identical, so a genuine
  failure is indistinguishable from a deliberate one.
- **Fix** Same fix as B-020, which is why they were the same bug. An empty row is
  three different things — still loading, deliberately off, or the curve does
  not exist — and all three rendered as bare background. The gutter says which
  one it is, and a row that loaded suppresses the reason so the two never
  overprint each other.
- **Status** fixed

### B-035 — Section labels: the measurements were right, the labeller was wrong

- **Severity** P1
- **Repro** "All the Small Things" read `intro, intro, verse, verse, verse,
  instrumental, verse, verse, verse, instrumental, chorus, verse, outro` — one
  chorus out of six, on a song whose chorus is the most memorable thing in it.
- **Expected** The repeat groups were already correct (both verses in one group,
  all four choruses in two). Only the *name* was wrong.
- **Root cause** Two mistakes compounding. The chorus tier was a fixed fraction
  of the single loudest vocal group — 88% over-labelled "DNA.", 95% left this
  track with one chorus. And the verse/chorus energy gap is a property of the
  record, not a constant: here the vocal groups sit at 0.69, 0.69, 0.77, 0.82,
  0.83, 0.89 with a clean hole between 0.70 and 0.77 and nothing else.
- **Fix** Find the hole rather than pick a threshold: sort the vocal groups by
  energy and split at the widest gap, excluding the first and last groups from
  the search because intro and outro are identified by position and a quiet
  outro would otherwise dominate the split. Both the energy gap *and* the part
  count must hold.
- **Status** fixed

### B-036 — A boundary is not a section: internal changes promoted to top level

- **Severity** P1
- **Repro** Count the section list against the form. "Taste" listed ten sections
  for eight formal ones, with three adjacent "verse" entries.
- **Root cause** Boundary detection and section interpretation were the same
  thing. Every prominence-filtered novelty peak became a section, so a chorus
  that thickens for its last four bars arrived as two top-level "chorus"
  sections.
- **Fix** `merge_macro_sections()`: adjacent segments with the same name become
  one macro section, keeping the internal boundary on the change layer where it
  belongs. Macros are numbered by repeat group, so every occurrence of one
  passage carries the same number.
- **Status** fixed

### B-037 — Merging without a duration cap presented mistakes as confident form

- **Severity** P1
- **Repro** Before the cap: "Taste" came back as a single 74-second "chorus";
  "DNA." as a 51-second one.
- **Root cause** Merging hides a labelling error instead of exposing it. Nothing
  in a form is 74 seconds long.
- **Fix** A macro section longer than `max(24s, 22% of the record)` is not
  merged, and the leftover is shown rather than absorbed.
- **Status** fixed

### B-038 — Ambiguity was resolved by guessing

- **Severity** P1
- **Repro** "DNA.": five of twelve sections labelled "chorus" when the form has
  two, because the widest energy gap happened to fall above the quiet passage.
- **Root cause** A cut found in a distribution with no clean hole still produces
  a confident answer.
- **Fix** Groups sitting within the ambiguous zone around the cut are labelled
  `unknown` with the reason recorded, rather than being assigned a tier.
- **Status** fixed

### B-039 — Repeat grouping had no part-count discriminator

- **Severity** P1 (systemic — the cause of B-035's symptoms on two of three songs)
- **Repro** "Taste" and "DNA." grouped a verse and a chorus together; the
  sections then shared a group and therefore a name.
- **Expected** A chorus is usually its verse plus a layer or two, so the source
  *set* barely differs — Jaccard lands near 0.75 and carries almost no
  information — while the part *counts* differ every time. Taste: choruses six
  sources, verses three. DNA: five against three.
- **Fix** Part-count difference priced into the similarity. Weights rescaled to
  sum to 0.86 (level and width are discounts, not contributions) and the grouping
  floor moved 0.68 -> 0.58 to match, since keeping the old floor broke AST's
  previously-correct verse grouping in the opposite direction.
- **Status** fixed

### B-040 — Macro numbering was a global counter, not a count of each name

- **Severity** P2
- **Repro** Section list read `verse 3, chorus 4, instrumental 5` — a sequence
  of positions masquerading as a form.
- **Root cause** The index incremented across all labels rather than within each.
- **Fix** Per-name counter keyed on `(label, repeat_group)`.
- **Status** fixed

### B-041 — No automated check that an analysis is internally consistent

- **Severity** P2 (process)
- **Repro** Sections overlapping, ending before they began, stopping short of the
  track, or a header disagreeing with the body all rendered as a plausible-looking
  form. Several were present for a while and only a manual count found them.
- **Fix** `backend/tests/test_invariants.py` — ordering, non-overlap, coverage
  of the full track, event times in range, energy changes ordered, source spans
  valid and consistent with their declared entry counts, plausible loudness,
  header agreement, and no unrepresentable events. Eleven of the tests feed it
  deliberately broken analyses and assert it objects, so it cannot pass vacuously.
- **Regression** `python backend/tests/test_invariants.py` against a running
  server; currently 0 violations across three songs.
- **Status** fixed

### B-042 — Lane labels detached from the lanes on resize

- **Severity** P1 (timeline correctness is sacred)
- **Repro** Resize the window so the timeline gets taller. Bass/Drums/Guitar/
  Piano/Other/Vocals stay stacked in the top-left corner overlapping the energy
  and stereo-width rows, while the lane rectangles sit 400px lower with nothing
  beside them.
- **Expected** Each label beside its lane at any height.
- **Root cause** Ownership. Both the canvas and the DOM overlay read their
  geometry from `geomRef`, a mutable ref assigned during render, and the
  overlay's memo listed `[song, hiddenSources, collapsedFamilies, options, width]`
  — which does not include `availH`, `stretch` or `totalH`, the three things a
  resize changes. Two consumers reading a mutable ref cannot be made to agree,
  only lucky.
- **Fix** The geometry is computed once per render as a value and handed to
  both. The ref survives only so the canvas rAF loop can read the latest value
  without re-subscribing, and it is written from the same value the overlay
  reads.
- **Regression** Measured label positions after a resize: six labels evenly
  spaced 57px apart from y=507, matching the lane rects.
- **Status** fixed

### B-043 — Soloing a stem played the previous song's audio

- **Severity** P0 (wrong audio attributed to the wrong track)
- **Repro** Solo drums on "All the Small Things", then on "Taste", instrumenting
  `fetch`.
- **Actual** `AST solo Drums` requested `/api/stems/ad2abafc0aaf/...` — Taste's
  id. `Taste solo Drums` requested `/api/stems/blink_ast/...` — AST's id. Every
  stem was off by exactly one song.
- **Root cause** The memo key was `stems.map(s => s.id).join('|')` — the *names*
  only. Every track separates to `vocals|drums|bass|guitar|other`, so switching
  songs produced an identical key while the URLs pointed at different files, and
  the effect that should have handed the Player the new stems never re-ran. The
  ref read made it permanent rather than momentary.
- **Fix** Key on `id@url` so identity is what decides. The Player also takes a
  load token, and a stem decode records which track it started for, so audio
  that finishes decoding after a switch can never be played under the new song.
- **Regression** Read the live Player's stem URLs after switching between all
  three songs: each holds only its own song's ids.
- **Status** fixed

### B-044 — Double-clicking a lane did nothing

- **Severity** P2
- **Repro** Double-click a source lane.
- **Actual** The row's only handler was a single click that toggles focus, so a
  double click toggled it twice and landed back where it started. The row reads
  as a track header and behaved like nothing.
- **Fix** `onDoubleClick` solos, which is the DAW convention. Single click still
  isolates.
- **Status** fixed

### B-045 — Mute was a control that could not do anything

- **Severity** P1
- **Repro** Press M on any stem or lane.
- **Actual** `_muted` was written by `toggleMute`, cleared by `clearMix`, carried
  through `MixState` to two buttons per source — and never read by `play()`.
  The full mix is a single decoded file, so an instrument cannot be subtracted
  from it; only replaced by the sum of the other stems, which is a different
  recording because a stem is an estimate.
- **Fix** Deleted. The button, the state, the plumbing and the `M` on every lane
  are gone. The stems panel now says why in a sentence, because "why can't I
  mute this?" is the next question and silence is not an answer.
- **Status** fixed

### B-046 — The vocals lane hid the loudest vocal material on "DNA."

- **Severity** P1 (the tool's core claim — "when does this part play" — was wrong)
- **Repro** Measure the vocals stem of "DNA." against the reported activity.
- **Expected** The regions reported as absent are quieter than the regions
  reported as playing.
- **Actual** They were not. The detected regions sat at a median of -20.6 dB; the
  *excluded* ones at -24.7 dB with a p90 of -21.5 dB against an on-threshold of
  -20.8 dB. A 6.25 s stretch was reported absent while carrying a level 164% of
  the median of what the tool *was* calling vocal. Measured over the whole
  source, the excluded frames had a p99 of 0.190 RMS against 0.092 for the
  included ones.
- **Root cause** Two things compounding. The running median that turns notes
  into parts takes the *lower* values in its window, so sparse breathy phrasing
  reads as near-silence. And `on_margin_db = 5` put the on-threshold above the
  material it needed to admit, so those passages sat in the hysteresis dead band
  and never crossed.
- **Fix** The switching envelope is the heavier of the two curves, but only
  where the lightly-smoothed curve is convincingly above the floor, and the
  rescue is skipped entirely for a source with no dynamic range. Vocals get
  `on_margin_db = 3`.
- **Evidence** Duty 68.0% -> 83.7% -> **93.7%**, spans 48 -> 39 -> 22, and the
  separation between included and excluded *improved* from 4.6 dB to 6.7 dB —
  the material coming back is part of the vocal, not bleed. "All the Small
  Things" (60.8% -> 61.3%) and "Taste" (89.5% -> 89.6%) are untouched, because
  their excluded regions sit 50+ dB down: genuine silence, which a lower bar
  cannot admit. A change that moved those numbers would have been inventing
  activity on tracks that do not have any.
- **Regression** `python backend/tests/test_invariants.py --stems`
- **Status** fixed

### B-047 — The stem check could not have caught B-046

- **Severity** P2 (process — a test that cannot fail is worse than none)
- **Repro** Write the regression test from a synthetic signal, disable the fix,
  run it.
- **Actual** It passed. Three attempts at a synthetic reproduction all passed
  with the fix disabled, so all three were deleted rather than shipped. The
  failure needs real vocal content; a clean two-tone signal never reproduces it.
- **Fix** The invariant is now measured against the actual stems, and its bound
  is **0 dB**, not the 2 dB first tried: the original failure measured +1.7 dB,
  so a 2 dB tolerance would have passed the exact case it was written for. The
  check skips a source playing more than 90% of the record, where there is
  nothing meaningful left to exclude.
- **Status** fixed

### B-048 — The instrument count was the number of separator files

- **Severity** P2
- **Repro** Read the header on any track.
- **Actual** "6 sources". A separator returns six files whether or not the track
  contains six instruments, and the empty ones are not empty — they carry bleed
  at around -80 dB. "DNA." has no piano, and the header counted one.
- **Root cause** A count with no notion of audibility.
- **Fix** `Source.is_silent`, measured from the *unfiltered* level curve (a
  running median raises an isolated dip to its neighbours, so span peaks are not
  a trustworthy test), the header counts instruments rather than files, and a
  caveat names any lane that is bleed. The header also now counts macro
  sections rather than detected segments.
- **Status** fixed

### B-049 — "Taste" piano activity is detected in the wrong regions

- **Severity** P2
- **Repro** Open Taste. The piano lane shows a continuous purple passage from
  0:30.5 to 2:33.2, including nearly empty waveform stretches around 1:00.
- **Expected** Activity highlights track the louder passages in the separated
  audio; separation residue should not read as a sustained piano arrangement.
- **Actual** The detected span averages -75.4 dB. The stem at 1:00 measures
  -76.5 dB, but is marked playing. Disabling gap closing alone still leaves
  50.6 seconds of false activity.
- **Root cause** Otsu splits the two large populations of quiet residue when a
  louder passage is rare. The -78 dB floor admits that residue; gap closing then
  joins its fluctuating detections into a 122.7-second passage. Earlier notes
  incorrectly ruled out a threshold correction without testing a peak-relative
  floor.
- **Fix** Piano's threshold stays within 30 dB of its own robust 99.5th-percentile
  peak. Other stems retain their existing configuration. Quiet musical passages
  remain detectable relative to the same stem's peak. Refreshed Taste's cached
  piano activity and dependent counts, events, energy, section summaries and
  transitions, preserving all listener section boundaries and names. Its
  detected piano passage is now 1:55.0–1:58.4 (3.4 seconds).
- **Regression** `backend/tests/test_activity.py` reproduces fluctuating residue
  plus one loud phrase, keeps genuine quieter playing, and verifies cached
  activity refresh preserves listener edits and raw energy.
- **Status** fixed

### B-050 — The waveform was one solid rectangle and scaling could not fix it

- **Severity** P1 (the largest element on screen carried no information)
- **Repro** Fit any track and read the waveform row.
- **Expected** A loudness envelope — quiet passages visibly quieter.
- **Actual** One flat block. Measured: a single column was `157,187,221`, the
  bright peak colour, for every one of its 148 rows. And the cause was numeric,
  not aesthetic: at whole-song zoom the min/max bound is **exactly 1.0000 at the
  10th, 50th and 90th percentile**. The maximum of a quarter second of a mastered
  track is near full scale almost everywhere, so the bound is saturated and
  carries nothing.
- **Root cause** A bound is the wrong statistic for "how loud is this moment". The
  loudness envelope is the rms, and the peak pyramid did not carry one.
- **Fix** The pyramid carries `[min, max, rms]`, pooling exactly down the levels,
  and the row scales to the visible window's 98th percentile. rms spans 4x across
  "DNA." where the bound spans 1.6x.
- **Two errors found by measuring, not looking.** The first attempt stored the
  arithmetic *mean*, which is signed: the half-cycles cancel, and it came back at
  0.0002 against a bound of 1.0 — a ratio of 4118x, which would have drawn a flat
  line at zero. Then pairing buckets for that mean failed on an odd count, a 500
  on the peaks endpoint. A format version guards the cache both times, because a
  stale pyramid read as a three-column one is the same failure as asking the API
  for a curve that does not exist.
- **Status** fixed

### B-051 — The layout had no width breakpoints at all

- **Severity** P2
- **Repro** Narrow the window.
- **Actual** The only media queries in the stylesheet were
  `prefers-reduced-motion`. The sidebar was a fixed 260px and the inspector a
  fixed 400px, so a 1280px laptop gave the song 620px and a 1024px one hit the
  320px floor and started squeezing the panels instead.
- **Fix** Three tiers, with the timeline as the thing that keeps its space
  because it is the only surface that cannot be read some other way. The library
  collapses to an overlay under 1240px and starts closed there — and only until
  the user touches the toggle, after which their choice wins and a song change
  does not undo it.
- **Status** fixed

### B-052 — The default panel led with the schema

- **Severity** P2 (the first thing anyone sees)
- **Actual** "container/codec, native rate 44100 Hz, bit depth 24, lossy no", then
  "confidence 67%, beats found 441", then loudness, then stereo correlation. All
  correct, all one click from interesting, none of it what someone opening this
  came for.
- **Fix** The default tab is the form: every section with its bar number, its
  length and the instruments actually in it, plus the largest energy changes. The
  measurement tables move to `measurements` alongside the models, fingerprint and
  pipeline. Tabs are words now — the song / instruments / measurements.
- **Status** fixed

### B-053 — Change contributors were rendered as percentages

- **Severity** P1 (a wrong number, stated confidently)
- **Actual** "bass +243%", "layers −227%". The contributor values are **z-scores**
  — standard deviations of each component's own distribution — so 2.43 means two
  and a half standard deviations, an ordinary large move, and "+243%" read as a
  tripling that did not happen.
- **Fix** There is no honest percentage for a z-score, so none is shown. The
  components are named in order of how far each moved, which is the part worth
  acting on. The same class of fix went into the section deltas, which said
  "energy falls 0.47" and "image widens 0.09" — bare fractions with no unit, now
  percentages — and into the section inspector's 0-1 scores, now shown as the
  share of full scale they are.
- **Status** fixed

### B-054 — The events row was a picket fence that lied about the rest

- **Severity** P2
- **Actual** 160 events across 1100 pixels: a pin every seven. Not information, a
  texture — and worse than nothing, because it looks like a deliberate encoding.
  Also 160 DOM nodes with 160 hit targets on a row that cannot be read.
- **Fix** A significance budget, ranked by the same measure the inspector uses
  for "biggest changes" so the two agree about what big means. The row states
  what it is holding back — "+119 smaller changes — zoom in" — and zooming
  reveals them (119 → 49 over five zoom steps). Display-only; nothing is dropped
  from the analysis or from the events tab.
- **Status** fixed

### B-055 — An open-ended change painted a display limit as a span

- **Severity** P2
- **Actual** A 400px amber slab across a third of the events row. Its "end" is
  wherever the next same-kind change happened to land, not a measured boundary,
  so the bar was a limit dressed as a span — and it read as a highlighted region
  rather than as "this changed here and it is still going".
- **Fix** Drawn as a 26px stub with a fade. The tooltip says how long the state
  actually held, which is the part worth knowing.
- **Status** fixed

### B-056 — Internal enum names in the events table

- **Severity** P2
- **Actual** A `kind` column rendering `e.kind.slice(0, 9)`: "width_cha",
  "density_c", "enter", "exit" — directly beside a `what` column already saying
  "Stereo image narrows" and "Vocals enters". Two vocabularies for one row, one
  of them the schema's.
- **Fix** The column is now the swatch the timeline draws that event in, which is
  what it was for — matching a row to a pin — plus the plain-language kind name.
  Confidence became a percentage with a bar; 0.95 is a number with no scale, and
  the eye cannot rank it against 0.67 without arithmetic.
- **Status** fixed

### B-057 — The caveats were printed twice on one screen

- **Severity** P3
- **Actual** A "Caveats (5)" card in the default view and a `notes` tab holding
  the identical list, with a tab badge counting them.
- **Fix** One place. They stay in the default view, because they qualify the
  numbers directly above them and one click away is the wrong place for honesty.
  The `notes` tab is gone.
- **Status** fixed

### B-058 — The library metadata wrapped mid-item

- **Severity** P3
- **Actual** "3:06.0 · 136 BPM 4/4 · 5 src · 12 sec · 160 ev" broke after the
  tempo and left a stray "ev" on the next line.
- **Fix** Words instead of a three-letter code, split across two non-wrapping
  lines: duration and tempo on the first (what you scan a library for), counts on
  the second in a dimmer tone.
- **Status** fixed

### B-059 — The section list was not a control

- **Severity** P2
- **Actual** The form list is the default panel and a list of the record's
  sections, and every row had `cursor: default` and no handler. Clicking
  "Breakdown" did nothing, while clicking the same section on the timeline
  selected it.
- **Fix** Rows select their section and move the playhead there. A macro covers
  several detected segments, so it selects the first and says so in the tooltip
  rather than pretending a row is one segment.
- **Status** fixed

### B-060 — The app opened showing one second of the song

- **Severity** P1
- **Repro** Cold load, no interaction. The view readout said
  `0:00–0:01 of 2:47 · 1%`.
- **Expected** A usable opening span. The caller asks for
  `min(30, max(8, duration/4))`, i.e. 30 seconds.
- **Actual** One second. The timeline was effectively empty and `fit` and every
  readout looked correct, so it read as a broken zoom control rather than a
  broken initial state.
- **Root cause** A side effect inside a state updater. The effect tracked which
  duration it had already applied by writing a ref *from inside* the `setView`
  callback. Updaters must be pure, and React runs them twice under StrictMode:
  the first pass wrote the ref and performed the reset, the second pass read the
  ref, concluded there was nothing to do, and discarded the result. The comment
  above the code described a bug that had already been fixed once — the earlier
  fix had moved the span calculation out of a ref captured on mount, and left the
  write inside the updater.
- **Fix** The bookkeeping happens in the effect body. Verified across all three
  paths: cold load 0:00–0:30, a user-chosen zoom survives until a song changes,
  and switching songs reopens at 0:00–0:30.
- **Status** fixed

### B-061 — The waveform was grass at intermediate zoom

- **Severity** P2
- **Repro** Open a song at the default 30-second span.
- **Actual** Vertical grass rather than a shape. A column is 37 ms there — about
  1600 samples — and the rms of a dense mix still swings wildly over that.
- **Root cause** The envelope was drawn at full column resolution. The whole-song
  view looked right only because each column was coarse enough to average the
  variation away; anything in between exposed it.
- **Fix** The smoothing window widens with the zoom, the same approach the energy
  row already used, with a floor of one column so a fully zoomed-in waveform keeps
  its transient detail.
- **Status** fixed

### B-062 — Energy attribution labels ran into each other

- **Severity** P3
- **Actual** Two changes a few hundred milliseconds apart both got a label and the
  two touched — "high end" and "dark/bright" at the same x, reading as one garbled
  word rather than two facts.
- **Fix** The right edge of the last label on each edge of the row is tracked, and
  a label that would overlap it is dropped. The marker and its hairline still say
  *that* something changed there; the label is the bonus.
- **Status** fixed

### B-063 — The header and the sidebar gave different counts for the same song

- **Severity** P2
- **Repro** Open any song. The header reads "5 instruments · 8 sections"; the
  sidebar item for the same record, one screen away, reads "6 instruments ·
  10 sections".
- **Root cause** Two computations of one fact. The library list returned
  `len(sources)` and `len(sections)` — raw counts — while the header computed
  "instruments that are actually audible" and "sections as a producer counts
  them" in the browser, because those are the numbers worth showing. Neither was
  wrong on its own terms; having both was the bug.
- **Fix** One computation, on the server, in `audible_source_count` and
  `macro_section_count`, used by both the list endpoint and the detail payload.
  The header reads the server's fields. The raw counts are still sent, so the UI
  can explain the difference rather than hide it.
- **Verified** All four songs, list against detail: agree on every one.
- **Status** fixed

### B-064 — A caveat said a lane was absent while the lane was drawn at full weight

- **Severity** P2
- **Repro** Analyse a track with no piano in it.
- **Actual** The caveat read "no audible Piano was found in this track … that lane
  is not evidence of an instrument and should be read as absent", and 40px below
  it the piano lane was drawn with 39 solid clips in it. The caveat and the
  picture contradicted each other on the same screen.
- **Fix** A source declared silent is drawn as a dashed outline labelled "no
  audible content in this stem" rather than as clips. The lane is still visible —
  the user can see what was checked — but it no longer asserts presence.
- **Status** fixed

### B-065 — The energy tab and the timeline used different words for the same thing

- **Severity** P2
- **Actual** The energy tab built its contributor names with
  `n.replace(/_/g, ' ')`, so it said "bass activity" and "hp ratio" while the
  marker beside the same change on the timeline said "bass" and "dark/bright".
  Two vocabularies for one measurement, in two views of it, one screen apart. The
  delta printed as "+0.48" where the overview says 48%, and the contributors
  printed as signed decimals — z-scores, so "+2.43" read as a quantity in the
  same units as the delta beside it and is not one.
- **Fix** `componentLabel` everywhere, percentages for the delta, and the
  contributor magnitude as a bar: how far each component moved is worth knowing,
  the misleading number is not.
- **Status** fixed

### B-066 — The patterns tab printed the schema's names and repeated one finding four times

- **Severity** P2
- **Repro** Open the patterns tab on any song.
- **Actual** Every card carried its internal kind as a tag: `change_grid`,
  `width_energy`, `repeat_delta` — beside titles already written in plain English.
  And the four repeat deltas were four separate cards, all at strength 0.3, with
  deltas of −0.11, −0.13, −0.11 and +0.08. Four cards of near-noise read as four
  findings and buried the two that are actually about the record.
- **Why it matters** The product's own instruction is not to expose names like
  these: they are what the detection is called, not what was found. The card said
  "Higher-energy sections are narrower" and then labelled it `width_energy`, which
  teaches the reader the vocabulary and gives them nothing.
- **Fix** The tag is gone with nothing replacing it, and repeat deltas collapse
  into one card: how many returns, the range, and every timestamp as a chip. Same
  information, different impression. Six findings became three on "DNA.", and the
  remaining titles are already musical — "Bass is withheld until 17s",
  "Transitions are built by subtraction".
- **A mistake inside the fix, caught by looking at the output:** the regex
  `[+-][\d.]+` swallowed the sentence-ending period, so "energy -0.11." parsed as
  `-0.11.` and the summary read "from NaN to NaN". A number has to be a number,
  and a period at the end of a sentence is not a decimal point.
- **Status** fixed

### B-067 — Clicking the left-hand side of the timeline restarted the song

- **Severity** P1
- **Repro** Put the playhead anywhere past zero. Click anywhere in the 116px
  label gutter on the left. Also: click a lane's solo button.
- **Expected** The gutter is where the controls are. Clicking it should do what
  the control says, and nothing else.
- **Actual** The playhead jumped to 0:00 and playback restarted. Clicking solo
  did the same, *and* toggled the solo.
- **Root cause** Two bugs, one cause. The scrub handler is on
  `.timeline-scroll`, which spans the whole timeline including the gutter, and it
  mapped `clientX` straight to a time — so a press at x = 40 became a time before
  the start of the view, and `Math.max(0, …)` turned that into `onSeek(0)`. The
  solo button made it worse by stopping propagation on `click`, which fires
  *after* `pointerdown` has already reached the scrub handler; `stopPropagation`
  in the wrong handler is the same as none at all. This was the most-clicked part
  of the interface.
- **Fix** One guard at the top of `onPointerDown`: a press that lands in the
  gutter, or on anything that handles its own clicks, does not scrub. The solo
  button also stops `pointerdown` directly, and the section bands and event pins
  already did — so the lane labels are now consistent with them rather than a
  second convention.
- **Regression** Playhead at 0:15.0, click the gutter → 0:15.0. Press solo →
  0:15.8 after 3.5 s of playback, solo engaged.
- **Status** fixed

### B-068 — Shift+wheel panned by a browser-dependent amount

- **Severity** P2
- **Repro** Shift+wheel over the timeline.
- **Expected** A scroll, at the same rate everywhere.
- **Actual** Reported as "zooms in really weirdly". `dt = deltaY / width * span`
  used `deltaY` raw, and it is not pixels everywhere: Firefox reports lines and
  some configurations report pages, so the same gesture moved seconds in one
  browser and fractions of a second in another. A single notch also moved 11% of
  the window, which is a lurch rather than a scroll, and there was no cap, so a
  trackpad fling could throw the view across the record.
- **Fix** The delta is normalised by `deltaMode`, a notch moves about 3% of the
  window, and the step is capped at a sixth of the span.
- **Verified** Notch 1.0 s of a 30 s window; Firefox line mode a consistent 0.5 s;
  a 9000-pixel delta 5 s rather than off the record; at full-song view the clamp
  holds at the start.
- **Status** fixed

### B-069 — Curve failures were invisible, and a name mismatch looked like a design choice

- **Severity** P2 (systemic — this is the class that produced B-005)
- **Repro** Ask the API for a curve name that does not exist.
- **Actual** The server was already correct: 200 with `{error, available}`. The
  frontend discarded it, so a bad name became an empty series — indistinguishable
  from an empty window, from a row that is deliberately off, and from a row still
  loading. An empty row is three different things and all three rendered as bare
  background. This is how the stereo-width row sat permanently blank with nothing
  anywhere saying the name was wrong.
- **Fix** `useCurve` carries the reason and the row's gutter label prints it. The
  API type admits `error` rather than claiming success always. One secondary line
  per row, so a failure and a number never overprint each other.
- **Regression** Intercepting the energy curve with the real error payload prints
  `unknown curve 'energy/perceived'` in the row.
- **Status** fixed

---

## SECTION WORK (B-023, B-030)

The mandate is explicit that section labelling is a correctness problem, not a
cosmetic one, and that the fix is *not* more analysis features.

The diagnosis, in order:

1. **Boundary detection is conflated with section interpretation.** Every
   prominence-filtered Foote peak becomes a top-level section. A 16-bar chorus
   that changes texture at bar 9 yields two "chorus" sections, not one chorus with
   an internal change. The first job is to stop promoting internal boundaries.

2. **Grouping over-merges on sparse material.** Verse and chorus in boom-bap share
   every instrument and differ only in density, so timbre/harmony similarity puts
   them in one group, and a single name is then propagated across both.

3. **Naming is per-group but grouping is not trustworthy.** The labeller cannot
   recover from a bad group. The two need to be separable so that uncertainty is
   representable.

The target representation is hierarchical, and it now exists: detected segments
carry `macro_id`, `macro_label`, `macro_index`, `macro_part` and `macro_parts`, so
the section list is the form while the underlying segments stay addressable.

Where each track stands against its published structure:

The table below is the pre-MLX baseline. In the current app, a conservative
boundary repair splits Taste at 1:58.1 and leaves the 1:38–1:58 passage
unnamed; listeners can split and name other sections directly. Section naming
and grouping on other material remain open design/correctness work.

| | detected | macro | still wrong |
| --- | --- | --- | --- |
| All the Small Things | 13 | 9 | two passages called "instrumental" that are a verse tail and a bridge |
| Taste | 10 | 7 | the bridge is read as verse 3, and the last chorus may contain an outro (flagged by caveat) |
| DNA. | 12 | 9 | the four-way group spanning 81 s is still one passage; the beat switch at 1:50 is now correctly a breakdown |

Tempo and boundary placement are good on all three (136.4 vs a documented 140;
111 vs 113; a boundary at 108.8 s where the beat switch is independently reported
at 1:50–1:58). What remains is the *grouping* on sparse material, where verse
and chorus differ in density rather than in instrumentation.

---

## B-070 — "DNA." vocals still reported absent where they are plainly present

- **Severity** P1 — a wrong answer about the most-asked-about question a
  producer has ("is there a vocal here?")
- **Reported by** the user, verbatim: *"DNA still has gaps where there are
  vocals."* A follow-on from B-046, which had been declared fixed.
- **Repro** Open "DNA.", look at the Vocals lane at whole-song zoom. Between
  0:03 and 3:06 there are 18 gaps longer than 0.4 s, totalling 10.9 s.
- **Expected** The gaps are instrumental breaks.
- **Actual** 17 of the 18 last 0.42–1.28 s and sit within ±3 dB of the material
  the detector *was* reporting. Kept passages: median −21.0 dB. Dropped ones:
  90th percentile −19.7 dB. The excluded material was **louder** than the
  included material, so the detector was hiding the loudest vocal on the record.
  The one genuine gap (184.98–186.02 s, −81.5 dB) is correctly excluded.
- **Root cause** `on_margin_db` was 3 dB for vocals and 5 dB for everything else,
  chosen before the library had stems to measure against. With the stems on disk
  the margin could be swept for real.
- **Fix** Per-source margins in `activity.STEM_MARGIN_DB`, looked up with the
  source id as well as the family, because guitar and piano are both "harmony"
  and need different values:

  | source | margin | why |
  | --- | --- | --- |
  | vocals | 3 → **1.0** | "DNA." 93.5% → 98.0% duty, separation +0.1 → +3.1 dB |
  | guitar | 5 → **2.0** | "All the Small Things" 82.7% → 84.6%, separation +4.4 → +4.6 dB |
  | piano | **5.0, unchanged** | see below |
  | drums, bass, other | **5.0, unchanged** | see below |

  The user's hypothesis was that *most* instruments are threshold-limited. The
  measurement does not support it, and the code says so in a comment rather than
  quietly doing what was asked: sweeping the margin from +3 to −1 moves bass duty
  on "DNA." by 0.0%, drums by 0.5% and other by 0.3%. Their duty cycles are set
  by real rests in the music. Only vocals and guitar respond, so only they moved.

  **Piano is the reason a single global margin cannot be lowered.** Sweeping
  "DNA."'s piano from 3 to −1 takes it from 11.5% to 35.3% duty, and the
  Funkadelic piano from 34.7% to 63.6% — a phantom instrument filling a third of
  the lane. Those stems peak at −68.9 dB and −65.7 dB: separator bleed. Lowering
  it invents parts.
- **Result** "DNA." vocals: 93.4% → **98.0%** duty, 22 segments → 7, gaps over
  0.4 s **18 → 4**, longest gap 10.9 s → **0.78 s**. The other three songs are
  unchanged, which is the check that makes this a targeted fix rather than a
  loosening: their excluded regions sit 30–50 dB down, so there was nothing for a
  lower bar to admit.
- **Regression test** `test_invariants.py --stems` — the kept-vs-dropped
  separation is now computed against the stems themselves and is positive for
  every source on every song. 19 checked, 1 violation, and that one is B-049,
  which is *supposed* to fail.
- **Status** verified

### B-071 — Every repeat of a model question was paid for again

- **Severity** P1 — real money, silently
- **Repro** Click "Name sections" on a song. Click it again on the same song.
  Open the reasoning panel, run "what is this song doing", run it again.
- **Expected** An identical question about unchanged data costs nothing the
  second time.
- **Actual** All eight interpretation endpoints call the API unconditionally.
  There was no cache, no memo, and no record of what had already been asked.
- **Root cause** `DeepSeekClient.chat` posts, full stop. The measurements
  underneath are deterministic, but the bill for asking about them was not, which
  made the cost of *iterating on the tool* proportional to how many times you
  looked at it.
- **Fix** `llm/cache.py`, a content-addressed store checked before the network.
  The key hashes the full request — model, effort, sampling parameters and every
  message. There is exactly one outbound call site in the backend, so all eight
  endpoints are covered; verified by grep rather than assumed.
  Because the key includes the messages, **re-analysing a song changes the key
  and still goes to the model**, so nothing stale can be served for changed data.
  No TTL and no invalidation button to forget.
- **Regression test** `test_llm_cache.py` — 21 tests. The load-bearing one is
  that the key changes when any request parameter changes, enumerated one field
  at a time, because a field that reaches the API but not the key is a field
  whose change would serve the wrong answer. Plus a stubbed-session test: a repeat
  makes 0 network requests, changed data makes 1, and `DEEPSEEK_CACHE=0` makes
  every ask cost again.
- **Also** `/api/health` reports `llm_cache`, so whether a repeat costs money is
  visible rather than something to guess at.
- **Status** verified. **Turn-off reminder: `TURN-OFF-DEEPSEEK-CACHE.md`** —
  `DEEPSEEK_CACHE=0` in `.env`, restart. Deliberately temporary.

### B-072 — Soloing a part made the stems panel unfindable

- **Severity** P2
- **Repro** Click solo on any lane. Then look for the button that opens the stem
  list.
- **Expected** The button that opens the stem list still says what it opens.
- **Actual** The transport button doubles as the solo readout: "stems (6)"
  normally, "solo: bass + drums" once anything is soloed. The control that opens
  the list stops being findable by its own name at the exact moment you most
  often want it.
- **Root cause** One control carrying two meanings. It predates this session as a
  single-value solo, and got worse when the label grew to a list.
- **Fix** The button's label is now always "stems (N)". The solo state is a
  separate always-visible control: "soloing bass + drums ✕", which states what is
  soloed and clears it.
- **Status** verified in-browser

### B-073 — The mixer's own description of solo was false

- **Severity** P2 — a lying control
- **Repro** Open the stem mixer and read the paragraph under the rows: *"Solo
  replaces the mix with that part."*
- **Expected** The description matches what the control does.
- **Actual** Solo became a set: clicking a second part plays both together and
  the mix stays out of it. The copy still described the old single-value
  behaviour, and copy is what a reader checks their understanding against.
- **Fix** Rewritten to describe accumulation, plus where the reset lives.
- **Status** verified

### B-074 — Two controls for one reset, a few centimetres apart

- **Severity** P3
- **Repro** Open the stem mixer with something soloed. A `solo: drums ✕` chip and
  a `full mix` button sit side by side in the header, and both clear the solo.
- **Fix** Deleted the mixer's copy. The transport control is labelled with what it
  will stop, so a second button with different wording for the same action is a
  duplicate the reader has to arbitrate. The mixer header now shows the count as
  passive text: "2 of 6 soloed".
- **Status** verified

### B-075 — Stem waveforms were normalised per lane, so a bleed stem looked like the drums

- **Severity** P2
- **Repro** Taste, whole-song zoom, compare the Piano lane with the Drums lane.
- **Expected** A stem that is 30 dB down draws visibly smaller than one that
  isn't.
- **Actual** The first implementation scaled each lane by its own 98th
  percentile, so every lane filled its row whatever its level. Taste's piano is
  separator bleed peaking at −73 dB and drew exactly as substantial as the drums.
  The lanes had been scaled to be identical, which is the one thing a waveform
  must not do — and it actively invited the comparison it destroyed.
- **Root cause** The scale was decided per lane because each envelope was built
  as it arrived, before the others were known.
- **Fix** `scaleEnvelopes` builds all of them first and divides by the loudest
  stem on screen. The absolute reference stays arbitrary; the *ratios* between
  lanes are real, which is the fact worth being able to see. A genuinely silent
  stem now draws as a flat line.
- **Status** verified — and this is what makes B-049 self-evident rather than
  something to take on trust: 18 piano clips sitting on a flat line.

### B-076 — Stereo width was a whole-spectrum average, which is the one number that says least

- **Severity** P2 — a correct measurement, presented in a way that discards the
  finding
- **Reported by** the user: *"stereo width should probably be multi-band tbh
  because it's pretty useless to get for all bands… at least 3 bands should do
  the trick."*
- **Repro** Open any song, turn on **stereo width**, read the row.
- **Expected** Three traces, one per band, comparable.
- **Actual** One line: the side/mid energy ratio across the whole spectrum.
- **Root cause** Not a bug in the measurement. `spatial.py` has computed
  `low_width`, `mid_width` and `high_width` per frame since the beginning, and
  the persister has written all three to the npz. The analysis was *already
  reasoning* about them — "the high band is considerably wider than the low band"
  is a caveat the tool emits, and the caveat generator compares
  `high_width > low_width * 1.8`. So the row was showing the average of the two
  facts the tool was making a point of, next to a sentence asserting one of them.
  `mid_width` was measured, stored, and reported to nobody: not in
  `spatial_summary`, not in the payload, and absent from the frontend's type.
- **Fix**
  - `SpatialBand` in `spatial.py` carries `name`, a display `label`, the edges,
    and the `field` it is stored under. `SPATIAL_BANDS` is the single source and
    the server ships it in `spatial_summary.bands`, so a label printed next to a
    curve cannot come to describe a different band than the one that was measured.
  - Three stacked lanes, one shared scale. Stacked rather than three lines on one
    plot because overlaid traces occlude, and they cross repeatedly at
    whole-song zoom — the last one drawn hides the other exactly where they
    differ, which is the only reason to draw them together.
  - `/curve` takes `names` and reads several curves in one request, because the
    window changes on every zoom and three separate fetches can land on different
    windows. A row showing a low band from the previous zoom beside a high band
    from this one describes no moment at all.
  - `mid_width` added to `spatial_summary` and to the frontend's type, which was
    also missing `low_width` and `high_width` that the server had been sending
    undeclared.
  - Row 22px → 66px. At 7px per lane there is no room for a label, and the row
    would have cost the same height while saying a third as much.
- **Two things wrong in the first attempt, both caught by looking at the result
  rather than at the code**
  1. **The shared scale made every band flat.** Side/mid is spiky and capped at
     0.8; the raw 98th percentile on "DNA." was 0.67, so the scale top was 0.77
     while the three bands sit at 0.047, 0.137 and 0.109. All three drew as flat
     lines at 6–18% of the available height. Fixed by setting the scale from the
     *smoothed* curve using the same zoom-following window the energy row already
     uses — top 0.77 → 0.53, shape visible. The window is printed in the gutter,
     because a row whose scale depends on an undeclared average is a row that will
     be over-read.
  2. **The band label was drawn at the lane's centre**, which is where the trace
     and its fill are — unreadable exactly where you look to identify the band.
- **And a third, from the user's report that the result was "very hard to
  read".** The bands were drawn as three *stacked lanes*, on the reasoning that
  overlaid traces occlude. That was wrong, and the reason is arithmetic rather
  than taste: three baselines and three label strips inside a 66px row left each
  trace `0.36 × 22` ≈ **8px of amplitude**. A shared axis gives every trace the
  full height. The occlusion worry also turned out to be much weaker than
  claimed — the three bands differ in level anyway (0.05 / 0.14 / 0.11), so they
  mostly occupy different vertical regions rather than piling up.
  Now: **one shared y axis**, three lines, no fills (filled areas from a common
  baseline occlude each other completely where they overlap, which is where the
  reader is looking), drawn low → high so the most prominent band is on top.
- **Two further bugs found while fixing the above**
  - **The legend was drawn at `GUTTER + 6`** — six pixels *into the plot*, printed
    on top of the traces it was labelling. It now sits inside the gutter, aligned
    with the row label, as a swatch + band + range + mean.
  - **The traces were drawn raw**, which at whole-song zoom is a hairball:
    side/mid is a ratio between two channels of the same frame, so it is spiky by
    construction and the spikes are frame-level phase noise, not musical events.
    The row now draws the smoothed column, like the energy row, and the window
    follows the zoom — verified at 9% zoom, where the window has shrunk to 0.1s
    and individual events are visible again.
- **Also fixed** a fixed pixel offset placed the mean number on top of the band
  name for `"mid 250-2000"` while fitting `"low 20-250"`. Now measured with
  `measureText`. The same class of bug as a hard-coded column width: right until
  the content changes, wrong silently when it does.
- **Regression test** `test_spatial_bands.py` — 15 tests. The load-bearing one is
  not "the bands are these three" but that **every name a consumer could derive
  from the band list resolves to a field the analysis actually writes.** Deriving
  one produced `spatial/low_mid_width`, which does not exist, because the middle
  band was `low_mid` in the DSP and `mid_width` everywhere else. That failed at
  runtime, as an empty row, rather than at import. Also checks the bands are
  contiguous (a gap would leave a hole in the measurement), that labels carry no
  internal names, and that the persister's field list is a named constant the
  test reads rather than a retyped copy.
- **Status** verified. 71 tests, typecheck clean.

### B-077 — The middle band was measured, stored, and reported to nobody

- **Severity** P3 on its own; it is the reason B-076 needed a fix at all
- **Repro** `GET /api/songs/{id}` before this change. `spatial_summary` carried
  `low_width` and `high_width`. There was no `mid_width`.
- **Expected** Every band the analysis measures is reported.
- **Actual** `SpatialCurve.mid_width` was populated by the pipeline, written to
  the npz by the persister, and read by nothing on either side of the wire.
- **Why it matters more than P3** the caveat generator compares
  `high_width > low_width * 1.8`, so a finding that spans three bands was being
  decided by two of them. A band that exists in the measurement and not in the
  report is a band whose contribution to any conclusion is invisible.
- **Fix** Reported in `spatial_summary`, typed in `SongSummary`, and drawn.
- **Status** verified

### B-078 — Three things that looked interactive and were not

- **Severity** P2 — the lying-control class, three instances
- **Reported by** the user, across three separate messages: *"after shift
  zooming, the highlight stays even though it's functionless atm. Idk if it had
  an intended function or not, but can you either make that clearer or fix it?"*
  and later *"Don't forget about the shift drag zoom keeping the highlight. It
  seems to still be happening…"*
- **Root cause, common to all three** a visual convention was borrowed without
  the behaviour that goes with it. Each of these looks like a control in a tool
  the user already knows, and each of them does less than that look implies.

  | what it looked like | what it did |
  | --- | --- |
  | a vertical rule marking the pointer | nothing — no time, no bar, and after a pan it marked an instant the pointer was no longer over |
  | a full-height tinted band | nothing — no handles, no drag, no click-away, and one `clear` button that appeared only for A and took B with it |
  | a control that had been switched off and on | nothing, and claimed "no onsets detected in view" on a track with 130 events |

  The common defect is not that they did nothing. It that each of them was
  **drawn as the affordance of an interaction while offering none of it**, which
  is worse than a missing control: a missing one is not mistaken for a working
  one.

- **Fixes**
  - **Hover guide** — the first response made it *more* prominent, which was the
    wrong answer to the right observation. The user had twice said it "stays" and
    "feels useless"; adding a bordered box with a time readout answered a bug they
    had not reported and made the thing they had reported louder. It now
    **disappears on any wheel event**, pan or zoom. A wheel means navigating, not
    pointing: you rest the pointer somewhere, scroll, the content moves under a
    stationary cursor, and the line then describes a moment you did not choose and
    cannot act on. Since the pointer has not moved, nothing would bring it back
    except moving the mouse, which defeats the purpose of using the wheel.
    Verified: present while pointing (`1:09.0 bar 40`), gone after shift+wheel,
    gone after plain wheel, back on the next real pointer move (`1:14.8 bar 43`).
    It keeps the position fix, which was a real bug worth having on its own.
  - **A/B bands** each get a dismiss mark, and the footer readouts clear their
    own range. Unlike the hover guide these *survive* a scroll, and the
    difference is the point: a selection is a deliberate act that something
    downstream consumes, where the guide was a cursor that had stopped
    meaning anything.
    The band stays `pointer-events: none` on purpose: making it
    clickable would mean a lane or an event pin inside a selection could no
    longer be clicked, which is a worse bug than the one being fixed. So the mark
    sits in the ruler strip — the one part of the band that overlays nothing you
    would want to click. B's mark is at the *foot* of its band, because A and B
    are frequently the same passage selected twice and two marks at the same
    point is one too many. The old detached `clear` button is gone; the hint now
    says the reasoning panel compares them, because that is the only thing that
    consumes them.
  - **Row toggles** — see B-076's sibling below; the third row of that table is
    the same class and is fixed in `useWindowed`.
- **Also** a fixed pixel offset placed a number on top of a label for one band
  name and not another, now measured with `measureText`.
- **Status** verified. 71 tests, typecheck clean.

### B-079 — The onset density row: removed, with the measurement that decided it

- **Severity** P3 — not broken, just not earning its place
- **Asked by** the user: *"I honestly don't know what information the onset thing
  gives me. But up to you whether you think it's valuable for the intended purpose
  of the app."*
- **Decision** Removed. Three measurements, on all four analysed songs.

  | song | mean onsets/s | p5 | p95 | **distinct values** |
  | --- | --- | --- | --- | --- |
  | DNA. | 6.03 | 4 | 10 | **7** |
  | All the Small Things | 5.97 | 2 | 10 | **8** |
  | Taste | 4.97 | 2 | 8 | **7** |
  | hit it and quit it | 5.65 | 2 | 10 | **8** |

  1. **It is not a curve.** Seven or eight distinct values across an entire song,
     because the density is computed over a 2-second window and quantised. Drawn
     as a filled area it implies a continuous shape it does not have. The
     screenshot showed exactly this: an amber slab with three visible steps.
  2. **Its one job is already done, better.** `density_change` events already
     exist — **12, 12, 14 and 19 per song** — coloured
     (`#fb923c`), labelled "rhythm density change", and drawn on the events row
     in the default view. "Where does the rhythm change" is answered by discrete
     labelled events, which is more actionable than a continuous curve, and it is
     already on screen.
  3. **It is already inside the energy row twice.** The raw curve is
     `0.62*z(LUFS) + 0.20*z(onset density) + 0.18*z(spectral flux)` and the
     perceived curve weights `onset_density` at 0.11, the third-largest of nine
     components. The "measured" toggle surfaces the raw one without a dedicated
     row.

  And what it cannot do that the interface already does better: attribute
  rhythmic activity to an *instrument*. The lanes and the stem waveforms do,
  because they are per-stem. A mix-wide onset count cannot tell you the drums
  came in.

- **Kept** the analysis, the `density_change` events, and the energy components.
  Only the row, its toggle and its plumbing are gone. Nothing that was measured
  was deleted.
- **Not restored by** turning `showDensity` back on in a stored state — the
  option no longer exists, so a persisted `showDensity: true` is read and
  ignored rather than resurrecting a row with no code behind it.
- **Reversible** one commit, if this turns out to be wrong.

### B-080 — Stopping an analysis looked exactly like a broken stop button

- **Severity** P2 — a control that appeared to do nothing
- **Reported by** the user: *"Clicking stop on analysis takes a long time to
  actually stop and there's no indicator it's actually trying to stop."*
- **Two defects, and the first one caused the second to read as a hang.**

  **1. The pending-stop flag never left the server.** `JOBS.cancel()` set
  `job.cancel_requested = True` and returned `{"cancelled": true}` — but
  `Job.as_dict()` did not include the field. So between the click and the job
  actually stopping, the job looked *identical to before*: same stage, same
  percentage, same enabled `stop` button. Clicking again did nothing visible,
  which reads as a broken control rather than a pending one. This is B-005 and
  B-069 again — a state the server had and the client could not see.

  **2. Six of the twelve stages had no cancellation check at all.** `check()`
  ran only after decode, features, rhythm, separation, sources and structure, so
  a stop during loudness waited for loudness *and* spatial to finish, and one
  during spatial waited for spatial and sources. The stages are not equal:
  separation is a neural net over the whole track, so a stop during it waits a
  minute or more.

- **Fix**
  - `as_dict()` now reports `stopping` — derived server-side, so there is one
    definition of the state: a stop asked for, on a job that has not finished. A
    finished job is not stopping whatever the flag says, because by then there
    is nothing left to stop.
  - The `stop` button **becomes** `stopping…`, and the line below says
    `stopping after <stage>` — which stage has to finish first. That is the
    honest and useful information: the server does not know how long that will
    take either, and inventing a countdown would be a guess.
  - `check()` before **every** stage, so the question of which stages have one no
    longer exists. A stop now waits for one stage, not two.
  - The `except Exception` around separation re-raises `AnalysisCancelled`. It
    is a latent bug: a cancellation raised from inside the separator — which is
    where it would be raised if the separator ever became interruptible — would
    have been caught there and recorded as "separation unavailable", so the job
    would carry on and file a cancelled analysis as one that merely failed to
    separate.

- **Deliberately not done** interrupting separation mid-pass. Raising an
  exception through the Demucs loop would leave the MPS command buffer in an
  unknown state, and the concurrency note in `app.py` records that a bad state
  there takes the server and every other in-flight job with it. A slow stop that
  says which stage it is waiting for beats a fast stop that occasionally crashes
  the process.
- **Verified** end to end. After a stop request the flag is visible within one
  poll, the UI reads `stopping…` / `stopping after features`, and the job
  finishes as **`cancelled`**, not `failed`, at the end of the current stage.
  71 tests, typecheck clean.

### B-081 — Three ways the span boundaries were wrong, all found by measuring the stems

- **Severity** P1 — the timeline was reporting boundaries that the audio contradicts
- **Reported by** the user: *"There's still a gap in the highlighted sections
  where it's very clear the waveform is still present. And micro gaps in vocals
  shouldn't count as vocals dropping out completely… Also other seems to be
  randomly highlighted especially in 'All the Small Things'. So your section
  highlighting algorithm is not great."* and then *"the grid isn't very accurate
  because the bpm detection doesn't seem to be that accurate."*
- **Method** The stem waveforms added earlier turn this from an argument into a
  measurement. For every source on every song, the level *inside* each reported
  gap was compared with the level inside the segments beside it. That single
  number separates the two populations cleanly.

  | | gap level vs segments | reading |
  | --- | --- | --- |
  | DNA. vocals, all 6 gaps | −3 to −10 dB | **artefact** — the vocal never stopped |
  | AST guitar, 6 gaps | −3 to −4 dB | artefact |
  | Funkadelic guitar, 4 gaps | −6 to −8 dB | artefact |
  | DNA. drums, shortest real break | −15 dB | real absence |
  | Taste vocals, 0.56 s break | −22 dB | real absence |
  | DNA. bass, 2.06 s | −53 dB | real absence |

  So the two populations are 10 dB apart with nothing in between, which is what
  makes a threshold-based fix possible rather than a guess.

  **First, a measurement that redirected the work:** the *macro sections* tile
  perfectly — 100% coverage, zero internal gaps, on all four songs. So the gap
  the user could see is in the **lanes**, and it is the same defect as the vocals
  one. The section ribbon was never the problem.

- **Defect 1 — spans collapsed to zero length when snapped to the beat grid.**
  `_snap_to_grid` moved `start` and `end` independently, so a span shorter than
  the grid spacing had both ends pulled onto the same grid point. **16 of Taste's
  78 segments** were emitted with `start == end`, and 1 of the Funkadelic track's
  105. A zero-length span is not a rounding detail: it draws as a hairline,
  contributes nothing to duty cycle, and still counts towards the lane's entry
  count — which is part of why Taste's drums read as arbitrarily busy.
  *Fix:* each span keeps its unsnapped boundaries, snaps a copy, and keeps the
  snapped pair only if it is still a span. Collapsed, inverted, and
  over-shortened spans are restored. Snapping never changes how many spans there
  are.

- **Defect 2 — gaps were closed by duration, which is the wrong question.**
  "How long was it quiet" cannot distinguish a half-second dip in a continuous
  vocal from a real rest. "Was it quiet" can.
  *Fix:* `_close_quiet_gaps` bridges a gap whose loudest *sustained* moment comes
  within 10 dB of the quieter of the two spans it separates. A single transient
  inside a genuine rest does not bridge it, because the level compared is a
  smoothed percentile rather than the peak — otherwise a hi-hat inside a drum rest
  would report drums playing through it.

- **Defect 3 — the beat grid is measurably wrong, and boundaries were snapped
  to it anyway.** A single global tempo's error accumulates:

  | song | reported | documented | error | grid drift by the end | `bpm_confidence` |
  | --- | --- | --- | --- | --- | --- |
  | DNA. | 136.4 | ~140 | −2.60% | **4.8 s** | 0.67 |
  | Taste | 111.1 | ~113 | −1.67% | **2.6 s** | 0.60 |

  So near the end of "DNA." a snapped boundary is placed up to 4.8 s from the
  beat it is being snapped *to*. Snapping there does not tidy the boundary up, it
  moves the analysis's answer — towards a tempo that is measurably wrong. The app
  already knew: `bpm_confidence` is 0.67 and 0.60, and "DNA." carries a caveat
  saying every bar number is an approximation.
  *Fix:* snapping is gated on `bpm_confidence >= 0.80`. Neither song qualifies,
  so neither is snapped and boundaries stay where the audio put them. A track
  with a firm tempo still is.

- **Regression tests** `test_activity_spans.py`, 13 tests. Each is written to fail
  if its fix is reverted, and — more importantly — each checks the *other*
  direction too, because a fix that bridges everything is not a fix:
  - snapping: a span shorter than the grid keeps its length; an inverted span is
    restored; **the number of spans never changes**; a good grid still snaps (the
    guard must not disable the feature)
  - gap closing: a 3-10 dB dip is bridged, a 15 dB dip is not, and the threshold
    is asserted to sit *between* the two measured populations; a lone transient
    does not bridge a rest; a gap at either end is not bridged
  - the gate: the two measured confidences are asserted to be below it, so a
    future change cannot quietly re-enable snapping on a grid that is seconds out
- **Status** 84 tests pass. The re-analysis and the stem-backed invariant check
  are the remaining verification.

### B-082 — "other" is drawn as an instrument on residue, and it is still wrong

- **Severity** P2 — **open, not fixed.** Recorded rather than quietly dropped.
- **Reported by** the user, with "All the Small Things" as the example.
- **Measured** AST's `other` has 12 segments at a median active level of
  **−37.5 dB**, while every real instrument on the same track sits between −23.5
  and −26.4 dB. It is 11-14 dB below the quietest thing that is actually an
  instrument, and it is drawn with 12 clips, which is what "randomly highlighted"
  looks like. `is_silent` is false because the stem's overall RMS clears the
  −60 dB cut.
- **Why it is not fixed here** the honest test is relative, not absolute — "is
  this more than ~10 dB below the loudest real source on this track" — and that
  needs the *other* sources' levels, which the per-source activity pass does not
  have. The two candidate fixes both have a failure mode I could not measure away
  in the time available:
  - a relative level cut would make one quiet real instrument vanish on a mix
    where it is genuinely there;
  - a higher on-margin for the residual family is a guess, and I have already
    measured once today that moving a threshold without the stems in hand is how
    B-046 happened.
- **What is true now** the stem waveform draws under it, so a reader can see the
  lane is faint; `duty_cycle` and the gutter state its actual level rather than a
  proportion of full scale. The lane is honest about *how loud* it is and still
  claims 12 spans it should not.
- **Next** measure the relative cut across the library first — what is the
  smallest gap between a real source and a residual one, on all four songs — and
  only then choose a threshold. Same discipline as B-070.

### B-083 — Every lane waveform ran at double speed

- **Severity** P0 — the timeline drew the wrong audio at the wrong times
- **Reported by** the user, three times, and each time the report was more precise
  than my hypothesis:
  - *"The waveforms seem to be a little bit behind where they actually play at
    the moment too"*
  - *"100% the sound comes before the playhead reaches the waveform on all of the
    waveforms"*
  - *"even at the beginning of DNA you can see the blue box around the Bass which
    is an 808. The 808 actually comes in where the blue box comes in, not aligned
    to the waveform"*
  - and, when I attributed it to DNA: *"idk if they're correct on taste"* —
  - *"nope they're wrong in taste too… Or the playhead is off I should say"*
- **Root cause** `compute_peaks` resampled a 44.1 kHz source like this:

  ```python
  data = librosa.resample(data.T, orig_sr=sr, target_sr=22050, axis=0).T
  ```

  In this librosa (1.0.0) that call returns shape **`(1, 8203392)`** for a
  `(8203392, 2)` input. It halves the sample count correctly and then puts the
  samples in the wrong shape. The transposed call therefore left an
  8,203,392-row single column, and every line after it read that column as 22050
  Hz. The stored time base came out at **exactly twice** the file's length —
  a 186.018 s stem became a 372.036 s pyramid — so every lane waveform was drawn
  at 2× speed.
- **Why it hid so well** three things had to line up, and each one is a lesson:
  - **The mix was correct**, because `_decoded_wav` resamples with ffmpeg and
    lands on 22050 Hz already, so `sr == sample_rate` and the broken branch was
    never entered. Only 44.1 kHz *stems* took it. A bug that only affects one of
    two data paths through the same function reads as a bug in the other path.
  - **The boxes were correct**, because they come from the analysis, not from the
    peaks. So the highlight and the waveform under it disagreed, and the
    highlight was the trustworthy one — which inverts the usual assumption that
    the drawing is derived from the claim being drawn.
  - **Nothing validated the length.** The cache key is the file's name, size,
    mtime and target rate — none of which change when the code that *builds* the
    pyramid changes. So the wrong pyramid was written, and then read back as
    current, indefinitely.
- **How it was found** not by reading the code, but by refusing to accept an
  unverifiable claim. Measuring, in order:
  1. the peaks pyramid against the audio it is built from, bucket by bucket —
     exact for the mix (0.00 dB across 71,562 buckets), which cleared the whole
     data path;
  2. stems against the mix — 0 ms on 15 of 18, the other three noise at r≈0.06;
  3. the playhead's own clock — 32 ms median, 48 ms p90, **59 ms worst** behind
     the audio clock over 80 animation frames, which is a real bug (B-084) but
     three orders of magnitude too small to explain a 1.73 s displacement;
  4. the lane peaks against the lane's own audio: **+1.730 s, r=0.741.** The 808
     is at 1.730 s and was being drawn at 3.470 s.
- **Fix**
  - `_resample_to` uses `scipy.signal.resample_poly` with the ratio reduced by
    gcd, and **asserts the output length and channel count** before returning, so
    a future substitution cannot quietly reintroduce a wrong length.
  - `_time_base_agrees` checks a cached pyramid's stored duration against the
    real file's on every cache hit, and discards it if they differ by more than
    `max(0.25 s, 0.5%)`. A factor-of-2 error is 186 s; a decoder trimming encoder
    delay is milliseconds. The six bad pyramids on disk were rejected by this
    check automatically.
- **Regression tests** `test_timeline_alignment.py`, 13 tests. Five are specific
  to this, and each fails if either half of the fix is reverted:
  - a 44.1 kHz file produces a pyramid covering the file, not twice the file
  - a transient is drawn within 10 ms of where it occurs (the defect was 1740 ms)
  - the stored envelope is the **mean of the channels**, which is what rules out
    the interleave: with the transient 400 samples apart between channels, an
    interleaved array read at double rate produces one impulse 18 ms early
  - a pyramid whose length disagrees with its file is refused, and a correct one
    is accepted
  - the tolerance accepts ±50 ms of decoder rounding and rejects 0.5 s and above,
    so it cannot be loosened into uselessness or tightened into a rebuild loop
- **Status** fixed and verified end to end through the live API. All twelve lanes
  across the four songs now agree with their own audio: onsets match to 10-40 ms
  at r = 0.82-0.99, where DNA's bass was 1730 ms out at r = 0.741. 98 tests pass;
  the stem invariant's only violation is still B-049.
- **Note for the record** the three measurements in step 3 above were worth doing
  even though they were not the cause. Had I "fixed" the 59 ms of playhead
  staleness and declared victory, the lane waveforms would still have been at
  double speed, and the next report would have been indistinguishable from the
  first. Measuring the *whole* chain, in the order that rules the most out
  earliest, is what found the actual cause.

### B-084 — The playhead was drawn from a 20 Hz snapshot of a 60 Hz clock

- **Severity** P1 — the playhead trailed the sound it was supposed to be on
- **Reported by** the user, as the "waveforms are behind" report above, and
  correctly distinguished from the lane bug: *"or the playhead is off I should
  say"*
- **Root cause** `usePlayer` maintains `timeRef` at 60 Hz specifically so the
  canvas can read the audio clock rather than a React value, and documented that
  intent. `App.tsx` passed `playhead={state.time}` instead — the 20 Hz state — and
  the canvas drew that. Measured in the browser over 80 animation frames, the
  drawn playhead trailed the live audio clock by **32 ms median, 48 ms p90, 59 ms
  worst**. On a transient that is the difference between the playhead reaching a
  drum hit and reaching it after it.
- **Fix** the canvas reads `geom.playheadRef.current` inside `draw()`. The ref
  object is stable, so the value is current by construction.
  - **The first attempt was wrong and was caught before shipping.** The redraw
    gate was `g.playing` from React state. Calling the Player directly — which is
    how the player is driven while investigating a bug report, since `__aa`
    exists for exactly that — left `state.playing` false while the audio
    advanced, so the playhead stopped moving entirely. A gate that has to agree
    with the transport is the same class of bug as a gate that reads a stale
    snapshot: it can disagree, and when it does the symptom is a *frozen*
    playhead, which is worse than the 50 ms it replaced.
  - The gate now compares the live playhead against **the value the canvas last
    drew**. It cannot disagree with anything: the transport rolling means a
    redraw per frame, paused means none, and a seek while paused means one.
- **Verified** the ref the canvas reads is **0 ms** behind the audio clock across
  75 samples, down from 32/48/59 ms. Playhead movement confirmed on 20 of 20
  frames while rolling.
- **What is left, stated as unverified** the clock compensates for
  `ctx.baseLatency + ctx.outputLatency` = 5.33 + 8 = 13.33 ms. Using
  `getOutputTimestamp()` to measure the *real* end-to-end output latency rather
  than trusting the advertised value, it comes out at **about 14 ms**, so the
  residual is roughly 0.7 ms. Two caveats I cannot close from inside a browser:
  that figure is specific to this machine's audio stack, and adding
  `baseLatency` to `outputLatency` is arguably a double-count, since the spec
  defines `outputLatency` as the delay from the graph to the device. It is not
  changed here because changing it would be a guess in the direction that makes
  the error *larger* to fix, and the measurement says the current value is right
  to under a millisecond.
- **Note** this could not be measured from the drawn pixels at first. At
  whole-song zoom the playhead advances 4.75 px/s, so one pixel is 210 ms and the
  drawn position cannot resolve a 50 ms error at all — a measurement that cannot
  fail is not a measurement. `__aaView` was added alongside `timeRef` so a
  screenshot can be turned back into times, which is what makes this class of
  report reproducible instead of arguable.

### B-085 — A passage two thirds of the way in was labelled "outro"

- **Severity** P1 — the form of the song was visibly wrong, in the app's most
  prominent panel
- **How it was found** by reading the Form list off the screen rather than the
  data, which is the point of the list. It read, for "DNA.":

  Intro · Chorus 1 · Chorus 1 · **Outro** · Chorus 1 · Chorus 1 · Breakdown ·
  Chorus 2 · Chorus 2 · Outro

  An outro in fourth position with five substantial sections after it, in a
  three-minute single.
- **Root cause** a repeat group is named by `_classify_group`, and the name was
  then written to *every* member of the group. `outro` is a **position** label —
  it is only ever scored when the group contains a section that ends the record
  — but applying it group-wide meant the group holding **41-57 s** and
  **182-186 s** was an outro throughout, and the first of those is a quiet
  breakdown two thirds of the way in.

  Position is a fact about one passage, not about a group. A group is a *similarity*
  claim; "the last thing in the record" is a *position* claim. Conflating them is
  the bug, and it is the same mistake the existing `intro` rule was already
  defending against in the other direction (`intro` is downgraded to `verse` when
  a group has a mid-record member).
- **Fix** `_POSITION_LABELS` names the labels that come from position. A member
  that does not hold that position is re-classified with `use_position=False`,
  re-using the same content rules with the position cues removed, so it gets a
  name from what is in it. "DNA." 41-57 s is now a **verse** at 0.55 confidence.
- **Regression tests** `test_section_labels.py`, 5 tests, over the whole library
  and written to fail on the pre-fix analyses (they did, three of five):
  - every `outro`/`fade` belongs to a section that ends the record, and every
    `intro` to a section that starts it
  - **the symptom, stated so it cannot return in another costume**: no outro may
    leave more than a third of the record after it. Not "an outro must be last" —
    a coda after a fade is fine — but the specific shape of this bug
  - the first and last sections still get their position names, so the fix
    cannot have been "stop calling anything an outro"
  - the specific passage on "DNA.", pinned by name
  - a chorus that fades keeps its own name
- **Also found while auditing the labels** every section's `loudness_lufs` was
  the *record's* integrated loudness, so all twelve sections of "DNA." reported
  −9.4 LUFS and all ten of "Taste" reported −7.3. One distinct value per song
  under a name promising a per-section measurement, and nothing reads it. Now
  `None`, which says "not measured" and cannot mislead. A section's LUFS is not
  the record's LUFS over a window either — LUFS is gated — so it would have to be
  integrated properly rather than averaged if it is ever wanted.
- **Checked and found sound** "All the Small Things" labels three passages
  "instrumental", which sounded like the same bug in a different place. It is
  not: the vocals' segments do not cover those spans, so the claim is true.

### B-086 — Three numbers for one thing, and a count that counted the wrong set

- **Severity** P2 — small, but it is the kind of thing that makes a reader stop
  trusting the rest of the screen
- **Symptom** on "DNA." the transport said **stems (6)**, the timeline drew
  **5** lanes, and the sidebar said **5 instruments**. The mixer it opened listed
  six parts.
- **Root cause** separation is not a judgement — `htdemucs_6s` always returns six
  stems, including a `piano` that on "DNA." sits 47 dB below the instruments and
  which the analysis therefore refuses to give a lane. The button counted
  `stem_audio.length` (files on disk) while everything else counted
  `song.sources.length` (things the app treats as instruments), and the mixer
  offered a solo button for a part the app had already said is inaudible.
- **Fix** the mixer lists, and the count reports, the stems that have a lane. The
  silent ones are still on disk and still checked by the stem invariant — they
  are just not offered as instruments, which is the position the app already
  takes everywhere else.
- **Also in this class** a click target that matched nothing. `.mixer-btn` had
  CSS — including a `font-size: 8px` glyph in an 18x16 box — and **no component
  has ever rendered it**; the real control is the name button, `.mixer-name`. So
  the mixer's only action had no visual affordance at all: a coloured dot and a
  word in a card, discoverable only by hovering for a tooltip. Removed the dead
  rule and gave the name a border, a background and a row-hover state, plus one
  line of copy: *click a part to hear it alone*.

### B-087 — Zoom ignored every event that arrived before the next frame

- **Severity** P1 — the zoom step per gesture was a function of frame rate
- **Root cause** `viewRef.current = view` was assigned **during render**, so the
  ref only caught up when React committed. `zoomAt` reads the ref, so N zoom
  events arriving before the next render all computed from the same starting
  window and produced the same answer: **N events zoomed by one step, not N**.
  A trackpad flick delivers several wheel events per frame, so the amount of zoom
  per gesture was however many events happened to land in separate frames.
- **Measured** 25 clicks on ＋ inside one frame stopped at a **47.0 s** span
  instead of reaching the 0.5 s floor. That is roughly 3.4 steps' worth.
- **Fix** the ref is now the *pending* view, written synchronously by
  `setViewSafe`, and every writer of the view goes through it. There were **six**
  other call sites calling `setView(clampView(...))` directly and leaving the ref
  stale behind them — the same bug in six places, found by grepping after fixing
  one.
- **Verified** 25 clicks in one frame now reach the 0.5 s floor; a 6-event
  trackpad flick gives a per-event ratio of 0.843 against the 0.847 the code
  intends; pan, zoom-out and `fit` all still behave.

### B-088 — The library reordered itself whenever anything was re-analysed

- **Severity** P2
- **Root cause** the list was sorted by `created_at` descending, and
  `created_at` is written by **every** analysis, not only by adding a song. So
  the order was an artefact of the last analysis run: after re-analysing the four
  cached songs it came back **Taste, All the Small Things, 03 - hit it and quit
  it, DNA** — neither chronological nor alphabetical, just the order the jobs
  finished in, and it changed again on the next run. A list that reshuffles when
  you re-analyse something cannot be learned, and nothing on screen says why it
  moved.
- **Fix** alphabetical by title, with the id as a tiebreak so the order is
  *total* — two loads of the same library now always agree, and a row with no
  usable title sorts last instead of first. Recency is not lost: a finished job
  opens its own result, so "what did I just add" is answered by the app rather
  than by the order of a list.
- **Regression tests** `test_library_order.py`, 5 tests, including the property
  the defect was about (re-analysing a song does not move it) and the
  quieter version of the same thing (two titles differing only by case have a
  stable order regardless of input order).

### B-089 — Four small things that each said something false or nothing

- **Severity** P3 individually; together they are most of what made the screen
  feel machine-made
- **`bar 0.4`.** `fmtBar` did `floor(beats / bpb) + 1` with no floor on the
  result, so any time before the first downbeat produced a bar that does not
  exist. A clean load of "Taste" read **bar 0.4** at 0:00.0 — the first thing a
  producer sees. Clamped at both ends, because bars *and* beats are 1-indexed;
  clamping only the bar traded "bar 0.4" for "bar 1.0", the same mistake one
  field over. Now **bar 1.1**.
- **The sidebar default became a preference.** `showSidebar` was derived from a
  media query — open above 1240px, closed below — and then persisted
  unconditionally. The listener only fires on a *change*, so the sequence was:
  open the app once in a narrow window, have the library close for that reason,
  and never see it again, including on a wide monitor. A default that gets
  written to storage stops being a default. It is now stored only once the
  reader has actually pressed the button, and an absent value means "ask the
  width".
- **Fourteen font sizes, two of them illegible.** The stylesheet used 8, 9, 9.5,
  10, 10.5, 11, 11.5, 12, 12.5, 13, 14, 15 and 16 px. When everything is nearly
  the same size nothing reads as more important, and 8px and 9px are not
  readable at all. Collapsed to **seven** (10-16) with the smallest text at 10px.
- **Targets you can miss, and one you cannot see.** The lane solo button was
  **14x13 px with an 8px glyph** and `opacity: 0` until the row was hovered — the
  only control in the gutter that does something, invisible and hard to hit. Now
  20x18 with a 10px letter at `opacity: 0.4`, coming up on hover.

### B-090 — Copy that explained the app to itself

- **Severity** P3
- **The selection strip carried four permanent hints** — two gestures for A and B,
  a sentence of rationale ("the reasoning panel compares them"), and two more
  gestures for the timeline at large, joined with separators, in the strip whose
  job is showing the selections. Two of them duplicated the zoom and fit buttons
  a few pixels away, and a rationale is not a gesture so it does not belong in a
  list of them. Now shown only while the slots are empty, where it is read at the
  moment it is needed.
- **`1 %` next to `0:00–0:02 of 3:06`.** The range already answers "how much am I
  looking at"; a bare percentage beside it reads as a proportion of something
  unspecified, and as an alarming number when you are looking at the whole
  record. Removed.
- **A path box and a button called "scan"**, with no label, in a card with no
  heading. Now *…or a folder on this computer* with a button reading **find
  files**, and the placeholder generalised from one particular folder on this
  machine to `~/Music`.
- **`refresh`** in the library header, which did not say what it refreshed. Now
  titled: *look on disk again — for files added outside the app, or after a
  scan*.

---

### B-091 — The lane waveform was smeared over the boxes, by the filter meant to stop grass

- **Severity** P1 — the thing the reader uses to check the analysis
- **Reported by** the user, twice, and the second report is the precise one:
  *"The boxes start at the correct places over the stems, but the waveform is
  still not aligned with the boxes. And the boxes end at the right place and the
  waveform actually goes past them. I think the boxes are properly placed, but
  the waveform is not still"*
- **Not the peaks.** B-083 was the peaks and it was the right diagnosis then;
  the server data is provably aligned now — all 22 lanes across the library agree
  with their own audio to 10-40 ms, measured through the live endpoint. So the
  displacement had to be in the drawing, and it was, in two places.
- **Root cause, part 1: a mean is the wrong filter for the job it was given.**
  `buildEnvelope` smooths the lane envelope with a symmetric moving average,
  widening to 9 columns as the view widens. Grass **is** spikes — isolated
  columns far above their neighbours — and a mean does not remove a spike, it
  *spreads* it: every column within half the window inherits part of it. A mean
  is also symmetric, so a step is blurred symmetrically about its true position,
  and the envelope both starts before the sound does and runs on after it stops.
  At the default whole-song zoom that is 9 columns of 216 ms: **±864 ms**.
- **Measured, not argued** on a synthetic step through the real function:
  - leading error at the step: mean **−864 ms**, median **0 ms**
  - a body of 0.10 with spikes to 1.00 every 9 columns: mean filter lifts the
    body to **0.20** (it doubled the level by smearing), median leaves it at
    **0.10** with every spike gone
  So the median does the original job *better* and fixes the misalignment. The
  prefix sum is gone with it; `O(n log w)` at 900 columns and a window of at most
  9 is not worth measuring.
- **Root cause, part 2: nothing stopped the lane drawing outside its claim.**
  Fixed in two places — the mix row's `drawWaveform` had its own copy of the
  same filter, and the lane is now clipped to the union of its analysed spans, so
  the invariant the reader checks by eye ("does the box line up with the
  waveform") holds exactly rather than approximately.
- **The trade-off, stated rather than hidden** drawing the stem under the
  highlight existed so that audio the detector *missed* would be visible as
  evidence against the analysis — a real idea, and the reason the "DNA." vocal
  gaps were once findable. Clipping gives that up in the lane. It is much less
  costly than it was: gap closing is why "DNA."'s vocals are one span over 99% of
  the record now instead of seven with six holes, and the mix row above the lanes
  still shows the whole song. A source with no spans at all is left unclipped
  rather than blanked, so nothing new can draw nothing.

### B-092 — A count, a sign, and two numbers for one idea

- **Severity** P2
- **"Arrangement patterns (6)" above three cards.** The heading was
  `song.patterns.length`, written in the caller, while the grouping that decides
  how many rows are drawn lived inside the list. The list owns both now, and the
  count is `grouped.length` — the same array it maps.
- **"energy from +-0.13 to +0.10".** The sign was read off the *last* delta and
  applied to the *minimum*. A double sign, from two different numbers.
- **"width (side/mid) 0.051" against a timeline showing 0.047 / 0.137 / 0.109.**
  The inspector reported the broadband mean; the timeline drew three bands; the
  mid band is nearly three times the headline, so comparing them suggests one is
  broken. The inspector now lists the three bands with their crossover edges and
  keeps the broadband figure labelled as such — the same words for both, which
  is the only way they can be compared. `bands[].key` is typed as the union of
  the three fields rather than `string`, so reading a value back no longer needs
  a cast, and a renamed field stops compiling.
- **"tempo drift 89.6 BPM"** directly under "tempo 136.36 BPM", which reads as a
  contradiction. It is not a drift: it is the span of the local pulse, and on
  "DNA." the curve really does run 130-250 BPM because the metre changes at 1:50.
  The claim was true and the label made it look like an error. Now a range with
  its ends.
- **"3 passage(s) repeat"** — a template's way of avoiding a decision. There is a
  count; it is either one or not.
- **An energy model written as a formula.** `Raw = 0.62*z(short-term LUFS) +
  0.20*z(onset density) + 0.18*z(spectral flux). Perceived = weighted mean of
  z-scored components: loudness 0.26, onset_density 0.11, ...` — `z()`,
  underscores and plus signs, in the main inspector, directly under a caveat
  carefully written for a reader ("Names are provisional — derived from level and
  instrumentation alone"). It was the only place in the product that spoke the
  source's language. Prose now, naming the components in words and the largest
  contributor; the numbers are on the curves' own tooltips.
- **"Vocals enters · enters", "Guitar returns · restart"** in the events list:
  a human phrase followed by the internal kind restating it, on all 115 rows.

---

### B-094 — The one piece of guidance on screen was wrong

- **Severity** P2
- **Symptom** after a server outage and restart, the workspace read:
  ```
  Nothing loaded
  Add a song from the library panel. Everything runs locally: …
  ```
  while the library panel beside it held four songs.
- **Why it matters more than it looks** the instruction is not merely
  unnecessary, it is the expensive kind. A reader who has four songs analysed and
  is told to add a song will add a song — a duplicate, or a new download, or a
  re-import — and then have two entries for the same music.
- **Root cause** the empty state had a single branch, and its copy assumed the
  library was empty. It was reached whenever no song was *selected*, which is a
  different fact: the library can be full with nothing open.
- **Fix** two branches. "No song open" says pick one from the library, and says
  so more helpfully when the library is hidden behind the width rule — the ☰
  button at the top left. The first-run paragraph about what the app does stays
  with the first-run state, where it belongs.
- **Checked, not assumed** auto-select is not the culprit. It was verified to
  work from a clean load, and again with `selectedId: null` written into storage
  deliberately — both open a song. So this is a copy problem, not a selection bug,
  and the fix does not touch the selection logic.
- **Not verified on screen** the "no song open" branch needs a full library with
  nothing selected, which needs either a server outage or deleting a song. The
  branch is a conditional on `songs.length` and compiles, and the *other* branch
  was confirmed rendering, but the new copy has not been read on a screen.

### B-093 — A stopped server emptied the library on screen

- **Severity** P1 — reads as data loss
- **How it was found** by stopping the backend mid-session and pressing refresh,
  which is what a reader does when the app has gone quiet.
- **Symptom** the sidebar said
  ```
  Library (0)
  Nothing analysed yet.
  ```
  while the topbar still read *DNA — Kendrick Lamar · 5 instruments · 9
  sections · 115 events* and the timeline was still on screen. Four songs had
  become none, on screen, in response to a server that was merely down. That is
  the reading which invites someone to add songs they already have.
- **Root cause** `useSongList` answered a failed request with `setSongs([])`. The
  `json` helper in `api.ts` throws correctly on a non-OK response, so the catch
  was reached and the failure was converted into an empty result. "The server is
  not running" and "you have no songs" are different facts and were the same
  state.
- **Fix** a failure keeps the list it had and records the message; the sidebar
  distinguishes the two and says that analysed songs are not lost. Verified both
  ways with the backend stopped and then restarted: the warning appears with the
  list preserved, and it clears with all four rows on recovery.
- **The class, checked** `grep 'catch(() =>'` found five other sites. Three are
  correct as written — a job poll, a jobs poll and the audio decode all *want* to
  degrade quietly rather than shout at a reader mid-playback. The fifth is
  `setReady(false)` on a failed load, which is right: nothing can be played, and
  the play button is disabled. So this was the only one, but it was found by
  looking rather than by assuming.

---

### B-095 — Zooming refetched everything, on every notch

- **Severity** P1 — the reported complaint: *"the waveforms and everything load so
  slowly when changing zoom levels. It's really funky how they work and load"*
- **Root cause** one line, and it is a self-inflicted one from earlier in the
  same pass:

  ```ts
  const step = Math.max(0.1, span / 40)
  ```

  The quantisation grid was **derived from the span**, so the grid moved every
  time the span changed. Two views one zoom notch apart landed on different
  boundaries and therefore produced a different key, so every notch refetched
  everything. Measured from the network log: twenty consecutive wheel events
  produced twenty sets of six peaks requests and twenty curve requests — about
  **200 requests** to go from the whole song to four seconds, with the waveform
  emptying and refilling at each step. There was no hysteresis of any kind: the
  fetched window *was* the view.
- **Fix, in three parts, each measured**
  1. **A fixed ladder** (0.5 s … 2 h) instead of a span-derived step. The step is
     the smallest rung at least as wide as the view, and the window is a full rung
     long, so a small pan or a small zoom keeps the same window. Twenty steps now
     cost nine windows instead of twenty, each fetched exactly once.
  2. **Wait for the gesture to settle.** A zoom is a *stream* of keys, and a
     trackpad flick delivers several per frame; fetching on each one fetches
     windows the user has already zoomed past. `settleMs` on the four media hooks
     (not on the song payload, which has one key per song and nothing to
     coalesce). Measured: a continuous 60-event flick went from **9 windows to
     1**.
  3. **One request for all six stems.** The lanes need every envelope at once and
     were asking one at a time — six round trips to move a few hundred kilobytes,
     which is most of the latency of a zoom. `/peaks` now takes `sources=` the way
     `/curve` already took `names=`, and a stem with no file is reported in
     `errors` rather than failing the batch, which is what the six-request version
     did with a `catch`.
- **Result, measured** one continuous zoom gesture, whole song to half a second:
  **3 requests in 1 window**, against roughly 200 across 20 windows before.

### B-096 — The waveform did not load past 64% when zoomed out — my bug, 20 minutes old

- **Severity** P0 — the most visible defect in the product
- **Reported by** the user, with a screenshot: *"The waveform literally doesn't
  even load here when I zoom all the way out too. You can see it on the right of
  the image."* The mix waveform stopped about 70% across while the view was the
  whole song, and the stem lanes ran the full width beside it.
- **Root cause** the ladder I added in B-095, in the same sitting. It rounded the
  window's **centre** to a multiple of the step:

  ```ts
  const q = Math.round(centre / step) * step
  return { q0: q - step / 2, q1: q + step / 2 }
  ```

  A 186 s view centres at 93 s, which rounds to **0** on a 240 s grid. The
  requested window was therefore **−120…120 s**: the second half of the song was
  never requested. The response covered 0…120 s, the drawing code returns nothing
  outside the returned window, and the waveform stopped at 64% — with the stem
  lanes, fetched on a different key, running the full width.
- **The part worth keeping** the comment on that version said the fetched window
  "covers the view **by construction**". It did not, and it was checked by reading
  the comment rather than by running it. `step >= span` guarantees the window is
  *wide enough*; it says nothing about *position*, and the two are independent
  exactly when the span and the centre disagree about which rung to use.
- **Fix** coverage is now enforced instead of argued. The window is a full rung
  long, and if it does not contain the view it slides right until it does —
  sliding rather than growing, because growing would make the window depend on
  the exact view and every pixel of panning would become a new key, which is the
  thing the function exists to prevent.
- **Verified as a property, not an example** 1320 views (110 start times × 12
  spans) checked for "does the fetched window contain the view": **0 failures**.
  The whole-song view now requests 0…240 s, the response covers 0…186.018 s, and
  **all 900 columns are non-silent** with the last at 185.8 s of 186.0 s. All five
  stems likewise cover the song, in one request.

---

### B-097 — Section names were clipped mid-glyph: "Intro" rendered as "Intrc"

- **Severity** P2 — visible in the screenshot, and a typo rather than a label
- **Symptom** the intro band of "DNA." read **Intrc**, and the closing one read
  **Outr**.
- **Root cause** `labelForWidth` decided whether the name fitted using the
  **band's** width, and the band is styled `padding: 0 7px` with
  `overflow: hidden`. So 14px of the measured width was never available for text:
  a 35px band was judged able to hold a 26px word when it could hold 21px, and
  the browser clipped the overflow mid-glyph.
- **Why it was missed** the function had already been fixed once, from an
  unconditional `slice(0, 4)` that rendered "intr vers chor" — so there was a
  comment asserting that abbreviation only happened "when the band genuinely
  cannot hold the word", and the padding was never in the arithmetic. A *sliced*
  word at least looks deliberate; a clipped one reads as a spelling mistake, which
  is worse than the bug it replaced.
- **Fix** the padding is subtracted before measuring, and the partial branch is
  **gone**. There is no abbreviation any more: a band that cannot hold its whole
  name shows none, and the name is in the tooltip and in the inspector. An empty
  band is honest where "Intrc" is not.
- **Verified** across every band on screen: `scrollWidth > clientWidth` is false
  on all of them, and no band carries a clipped word. The narrow ones (24-34px)
  now correctly show nothing.

### B-098 — The app's own configuration line was the only unreadable text on screen

- **Severity** P3
- **Symptom** the sidebar's setup summary read `Setup standard · multi · deepseek…`
  — clipped mid-word, in a column wide enough for about two of the three values.
- **Root cause** two problems in one line. The values are internal configuration
  identifiers (`standard`, `multi`, `deepseek-flash`) rather than anything a
  producer chose or would recognise, and the row was a single fixed-height line
  in a narrow column, so it was guaranteed to clip.
- **Fix** the collapsed summary is now prose that wraps: *standard quality · six
  stems · commentary on*. The identifiers are still there on expand, in mono,
  because that is where a troubleshooting question wants them — and the expanded
  panel now also names the model, which it did not.
- **Deliberately not removed** the panel is the answer to "which models ran",
  which is a real question. Only its collapsed form was illegible.

### B-099 — Current frontend source cannot reproduce the running song study

- **Severity** P1
- **Status** verified
- **Repro** Run `npm run build` in `frontend` while the current song-study UI is served.
- **Expected** The build passes and produces the same song-study interface.
- **Actual** TypeScript rejects `SongStudy.tsx` because `MacroSection` has no
  `userNamed` property. The current `App.tsx` does not import `SongStudy`, while
  the served `dist` bundle includes it. A fresh build would replace the running
  redesign with an older interface.
- **Fix** Recovered the reset song-study implementation from Git objects, wired
  renaming and listening into `App`, and restored the chronological section map
  and numbering. The source now builds the running study experience.
- **Verified** `npm run build` passes. A fresh browser tab shows the song map;
  renaming Chorus 3 to Bridge updates the map and later chorus number, survives
  a fresh tab, and "Use suggested name" restores both.

### B-100 — Rhythmic energy was stretched across the song

- **Severity** P1
- **Status** verified
- **Repro** Give `build_energy` a 20 Hz onset-density peak at 6–7 seconds.
  The resulting onset component used to stay flat because it read the series
  as 1 Hz. Saved songs consequently spread the opening seconds of rhythmic
  activity across their full duration.
- **Expected** The component peaks at the same song time as the input.
- **Actual** The 20 Hz series was interpolated on a 1 Hz clock. The density
  helper also reported values roughly 40 times below its claimed onsets/s unit.
- **Fix** Pass the real 20 Hz rate, convert density bins to events per second,
  and label stored standardized components as `z`, not physical units.
- **Regression** `test_energy_onset_density_stays_aligned_with_song_time` and
  `test_onset_density_reports_events_per_second`.
- **Data refresh** Reanalysed the four local songs from cached stems so their
  curves, section summaries, and change explanations use the same corrected
  calculation. Previous files are backed up in
  `data/backups/2026-09-29-energy-refresh/`.

### B-101 — Energy attribution and declared weights disagreed with the model

- **Severity** P2
- **Status** verified
- **Repro** Compare `DEFAULT_WEIGHTS` with the components created by
  `build_energy`, or make a low-weight component move more than a high-weight
  one. The unused `harmonic_density` key appeared in the declaration;
  `percussive_ratio` secretly weighted two other components; attribution ranked
  unweighted movement above actual model effect.
- **Expected** Every declared weight names an applied component, and the
  leading contributor is the component that most affects the score.
- **Fix** Name and normalize the existing applied coefficients without changing
  their proportions. Attribute changes with those weights in the normalized
  model's units.
- **Regression** `test_energy_onset_density_stays_aligned_with_song_time`
  checks the component/weight set and sum;
  `test_energy_change_ranks_contributors_by_effect_on_the_model` checks ranking.

### B-102 — The section band covered half of the energy curve

- **Severity** P2
- **Status** verified
- **Repro** Open a whole-song view with the default timeline. The section band
  is 48px tall, but the canvas reserves only 30px before drawing energy.
- **Expected** The energy shape begins below the section band and has its full
  row height available.
- **Actual** The band covered the top 18px of a 34px energy row, making the
  curve look cramped and encouraging microscopic labels to compete with it.
- **Fix** Use the same 48px section height in layout and styling. Hide energy
  marker words at whole-song scale while keeping their clickable landmarks;
  reveal words on closer zoom.
- **Verified** Fresh DNA whole-song view shows the complete energy trace under
  the section band, with major markers but no text clutter.

### B-103 — Empty changes row remained in the default timeline

- **Severity** P3
- **Status** verified
- **Repro** Open a song at whole-song scale with arrangement changes hidden.
  The gutter still showed an events label and green square beside an empty row.
- **Expected** An optional row takes up space only when its layer is visible.
- **Fix** Include the changes row in canvas geometry and gutter labels only
  while that layer is shown. The row and its markers return when enabled.
- **Verified** In All the Small Things, the default view has no empty row;
  turning on Changes restores an aligned row and event markers.

### B-104 — Waveform peaks drifted later than the playhead

- **Severity** P2
- **Status** verified
- **Repro** Place a transient at 50.000 seconds in 22,050 Hz audio and query
  the peak display around it. The waveform drew it at 50.114 seconds. The
  offset grew with song time on both the mix and separated stems.
- **Expected** The displayed transient shares the audio/playhead time base.
- **Cause** The pyramid used 55-sample buckets but advertised exactly 400
  buckets/second; the true rate is 22,050 / 55 = 400.909 buckets/second.
- **Fix** Store the actual bucket rate and bump the peak-cache format so stale
  pyramids are rebuilt. The mix and summed stems of All the Small Things also
  cross-correlate at zero lag at 10, 80 and 150 seconds, so the source files
  themselves are aligned.
- **Regression** `test_late_transient_does_not_drift_from_playhead` failed at
  114 ms of drift before the fix and now passes within 15 ms.

### B-105 — Shift zoom left a misleading selection behind

- **Severity** P2
- **Status** verified
- **Repro** Shift-drag the timeline after selecting a section. The gesture
  doubled as selecting range A and zooming, sometimes used stale range state,
  and left the A band and section highlight after the zoom. Starting over a
  section band did not work because its button intercepted pointerdown.
- **Expected** Shift-drag previews and zooms to a range without leaving a
  selection behind; A/B comparison ranges remain available separately.
- **Fix** Give Shift-drag a temporary zoom preview and calculate its final
  range from pointerup. Shift-Option-drag selects A; Option-drag selects B.
  Shift-wheel now reads horizontal deltas as well as vertical ones and clears
  the stale section selection.

### B-106 — Continuous event bars crossed into the timeline gutter

- **Severity** P3
- **Status** verified
- **Repro** In Taste, show Changes and zoom to a window beginning inside an
  earlier continuous event. The event bar and occasional section band extended
  left of the plot into its label gutter.
- **Expected** Time-based overlays stop at the plot boundary.
- **Fix** Clip event spans and section bands to the plot; hide out-of-view pins.
  Open-ended spans now use their derived end time rather than treating a pixel
  coordinate as seconds.
- **Verified** Taste at 0:23–0:49 shows the long event bar ending at the plot's
  left edge, with the gutter clear.

### B-107 — Restored zoom can leave the playhead outside the visible song

- **Severity** P3
- **Status** open
- **Repro** Zoom into Taste at 0:25–0:47, then reload the app. The saved view
  returns, while playback time resets to 0:00. The playhead is offscreen and
  the context below the timeline says Intro although the map shows Chorus 1.
- **Expected** A resumed song view makes the current playback position clear,
  whether by restoring the listening position or returning to a view that
  contains it.
- **Actual** The app restores the zoom window without a matching transport
  position. The per-song view is saved in `frontend/src/App.tsx`; playback state
  starts at zero in `frontend/src/hooks/useTransport.ts`.
- **Note** Left for a separate session-state design pass because changing the
  restore contract affects how producers resume work on a song.

### B-108 — "Choose parts" showed two chips per part, named with internal keys, filled with six saturated colours

- **Severity** P1
- **Status** verified
- **Reported as** *"click on 'choose parts' and it shows a whole bunch of random
  parts that are coloured strangely as well."*
- **Repro** Open any song, click **Choose parts**. On Taste the strip reads
  `bass bass  drums drums  harmony guitar piano  unknown other  vocals vocals`.
  DNA reads the same minus piano.
- **Expected** A row of the song's parts, each named the way the lane below it
  is named, each a control with one clear meaning.
- **Actual** Three separate faults in one 30px row.
  1. **Every part appeared twice.** The strip rendered a family chip and then a
     chip per source. For a family holding one part the family key and the
     source id are the same string, so `bass`, `drums` and `vocals` each printed
     the same word twice — once as a grey chip, once as a coloured one, with
     nothing to mark which was the control and which the thing controlled. The
     two controls were also the same control: for a one-part family,
     `collapsedFamilies` and `hiddenSources` remove the same single lane.
  2. **Internal enum names reached the screen.** The chips printed `s.id` and
     the raw family key. `unknown` — the internal placeholder for a part the
     classifier could not place — and `harmony` — a bucket holding a guitar and
     a piano — are not words a producer would use, and the payload has always
     carried `label` and the app has always had `sourceName` to name them
     properly. So the same part was called `other` on the chip, "Other sounds"
     in the lane gutter and "Other / unclassified" in the payload.
  3. **Six fully-saturated fills in one row.** Each chip was filled with its
     own source colour at full strength. Those colours are chosen to be told
     apart as *thin outlines* on the canvas; as filled lozenges at 30px tall
     they stop reading as labels and start reading as the data.
- **Root cause** The strip was built from `song.sources` grouped by family and
  then rendered both levels of that grouping as if both were content, with the
  group's key as its label. Grouping is a data structure, not something to show.
- **Fix**
  - A family chip renders **only when the family holds more than one part**. For
    a one-part family it was the same control twice, so it is deleted rather than
    restyled. Taste keeps exactly one: "Harmony", heading Guitar and Piano.
  - Names come from `sourceName` — the same function the lane gutter and the song
    study use — so a chip, its lane and the study cannot disagree. Verified in
    the browser: the strip is now byte-identical to the lane labels.
  - Group order derives from `Map` insertion order over `song.sources`, which is
    the order `laneRects` draws the lanes in. The strip cannot drift out of step
    with the lanes, and a family chip lands on the group it heads.
  - Colour is a 7px swatch and a border on a 20% tint, with the group's chip
    dashed and transparent so it cannot be mistaken for a part inside it.
- **Measured after** six chips, no repeated word, no raw key, chip text 16.56:1
  on every part. Hiding a part and collapsing a group both still remove the
  right lanes and restore correctly.
- **Correcting an earlier guess** contrast was *not* the failure. Measured, the
  old near-black-on-full-colour chips ran 4.93:1 (bass) to 10.14:1 (guitar) —
  all passing AA, the worst comfortably above 4.5:1. The fault was volume, not
  legibility, and the fix is recorded as a reduction in visual weight rather than
  an accessibility repair.

---

---

## VERIFIED-FIXED (regression status)

| Bug | Regression test | Automated |
| --- | --- | --- |
| B-002 blank on click | `ErrorBoundary` + `num()` + build typecheck | partial |
| B-003 row misalignment | measured pixel extents | manual |
| B-004 marker x | measured marker extents vs data | manual |
| B-006 onset rate | 3 tests | yes |
| B-007 repeat caveat | `test_structure_caveats_describe_the_actual_grouping` | yes |
| B-008 tempo drift | `test_tempo_drift_ignores_a_single_outlier_beat` | yes |
| B-009 concurrent crash | `queue_check.py` | manual |
| B-001 scan hang | `test_library_scan_is_bounded` | yes |
| B-025 alt order | `test_analysis_roundtrips_through_json` | yes |
| B-070 vocal gaps | `test_invariants.py --stems`, separation vs the stem | yes |
| B-071 uncached model calls | `test_llm_cache.py` (21 tests) | yes |
| B-072 stems button relabel | — | manual |
| B-073 false solo copy | — | manual |
| B-074 duplicate reset | — | manual |
| B-075 per-lane scaling | — | manual |
| B-076 whole-spectrum width average | `test_spatial_bands.py` (15 tests) | yes |
| B-077 mid band unreported | `test_field_is_a_real_attribute_on_the_model` | yes |
| B-078 hover guide / A-B bands / row toggles | — | manual |
| B-079 onset row removed | measured distinct-value count across 4 songs | yes |
| B-080 stop had no pending state | end-to-end: flag visible, job ends `cancelled` | manual |

Still to automate: B-003/B-004 (a canvas-level test would catch the whole
coordinate-space class), B-021 (needs the generation guard first).

**Known gap: the frontend has no test runner.** `npm run build` runs a real
`tsc --noEmit`, which has repeatedly caught genuine bugs, but there is no
vitest/jest, so B-072 through B-075 are verified by hand in the browser. A
regression in `Player.toggleSolo` would be a P0 — soloing the wrong song's audio
is B-043 — and nothing would fail. Worth fixing; deliberately not started
mid-change, because adding a runner to a frozen build is its own risk.

### B-109 — A no-op boundary click blocked double-click merge in the first desktop build

- **Severity** P2
- **Repro** Split a desktop section, then double-click its new separator.
- **Expected** Remove the separator and keep the left section's name.
- **Actual** Selection closed, but the separator remained. The first click's unchanged boundary preview started a save request; the second click's merge was rejected while that save was pending.
- **Fix** `desktop/Sources/SongStudy/StudyStore.swift::save` ignores unchanged section snapshots before marking an edit in flight.
- **Status** fixed; final native interaction verification recorded in `desktop/README.md`.

### B-110 — A short clip with no MLX tempo exposes an internal conversion error

- **Severity** P3
- **Repro** Analyze the 12-second synthetic fixture described in the desktop import verification, or load All-In-One output with `bpm: null`.
- **Expected** Report that no usable tempo was found and use the supported fallback.
- **Actual** Fallback succeeds, but the warning includes `float() argument must be a string or a real number, not 'NoneType'`.
- **Observations** The complete desktop import succeeds. Full-song Taste inference with the packaged runtime succeeds at 113 BPM. The issue concerns missing-output handling and warning language, not evidence that all inference is broken.
- **Relevant file** `backend/arrangement/models/allinone_mlx.py::load_result`.
- **Status** open

### B-111 — Position-label audit rejected an internal Intro fragment

- **Severity** P3
- **Repro** Move the Intro/Verse boundary from 13.58 to 13.7607 seconds. The moved Intro contains two internal segments under one macro passage.
- **Cause** The cached-library audit tested each internal segment's position as if it were an independent formal passage. It also depended on private analysis files.
- **Fix** Replace the private-library audit with synthetic classifier and boundary-edit regressions. Audit contiguous macro occurrences, while keeping separate occurrences of reused IDs separate. Negative cases still reject mid-song generated Intro, Outro and Fade labels. No saved song labels are changed.
- **Regression** `backend/tests/test_section_labels.py`.
- **Status** fixed in the publication copy; nine portable regressions pass.

### B-112 — Desktop downbeat guides used beat indices as seconds

- **Severity** P2
- **Repro** Open an analyzed song in the original desktop build and zoom below a minute. Compare its downbeat guides against the detected beats.
- **Expected** Downbeat guides and bar labels use the corresponding beat timestamps.
- **Actual** The private worker passed downbeat indices directly to a native view that treated them as seconds. The native overview also omitted the bar ruler.
- **Fix** Convert valid downbeat indices to beat timestamps in `desktop/worker/worker.py`; render zoom-adaptive bar numbering from those timestamps. No tempo or source analysis is changed.
- **Status** fixed; worker contract test, bar-ruler tests, and native whole-song/zoom/full-screen checks pass.

### B-113 — Selecting a desktop section resized the timeline lanes

- **Severity** P2
- **Repro** Open a desktop song, then click a section name and close its inspection. Repeat in full screen.
- **Expected** Section selection changes context and playback without moving or resizing the timeline lanes.
- **Actual** The timeline reserved 116 pixels for the listening footer but 190 for inspection, so its waveforms and energy lane changed height. A newly visible scrollbar also changed plot width.
- **Fix** Reserve the same context space in both states. Keep the timeline outside scrolling; only details scroll, with indicators disabled. Layout regression checks require identical heights before and after selection.
- **Relevant files** `desktop/Sources/SongStudy/EnergyPresentation.swift`, `SongStudyApp.swift`.
- **Status** fixed; native verification recorded in `desktop/README.md`.

### B-114 — Desktop timeline ignored mouse dragging outside section boundaries

- **Severity** P2
- **Repro** Click and drag on the song waveform or a stem waveform.
- **Expected** Move the playhead with the pointer; preserve playing/paused state when released.
- **Actual** Only the initial click sought. `mouseDragged` returned unless a section boundary was being edited.
- **Fix** Add an independent scrub gesture and transport lifecycle. Drag positions are authoritative; playing audio seeks are coalesced into short auditions, and the exact release position resumes the original playback state. Section handles still adjust boundaries.
- **Relevant files** `desktop/Sources/SongStudy/TimelineView.swift`, `AudioTransport.swift`.
- **Status** fixed; native verification recorded in `desktop/README.md`.

### B-115 — Renaming a chorus to a verse retained chorus green

- **Severity** P3
- **Repro** Rename a model-labelled Chorus to Verse in the desktop timeline.
- **Expected** Its color follows the listener's current name.
- **Actual** Rendering used the original model kind OR a chorus substring, so former choruses stayed green and pre-/post-choruses also looked like choruses.
- **Fix** Classify current names using anchored, case-insensitive regular expressions. Apply distinct section-role colors, and a neutral color for custom names. No stored model labels are changed.
- **Relevant files** `desktop/Sources/SongStudy/Domain.swift`, `TimelineView.swift`.
- **Status** fixed; rename/model-disagreement and name-variant regression checks pass.

### B-116 — Closing the desktop window left playback running invisibly

- **Severity** P2
- **Repro** Play a song and click the red window close button.
- **Expected** Quit Song Study and stop playback.
- **Actual** macOS closed the window while the app's audio transport continued running.
- **Fix** Quit when the last application window closes. On termination, stop audio and terminate the private worker process group, covering both window-close and Cmd-Q.
- **Relevant files** `desktop/Sources/SongStudy/SongStudyApp.swift`.
- **Status** fixed.

### B-117 — Generated app artwork appeared in Finder but not the running Dock

- **Severity** P3
- **Repro** Launch the app after adding the generated icon. Finder shows the icon; the running Dock entry does not.
- **Expected** Use the same artwork in Finder and the Dock.
- **Fix** Explicitly set the application's runtime icon from its bundled `SongStudy.icns` during launch. Keep the bundle icon registration for Finder.
- **Relevant files** `desktop/Sources/SongStudy/SongStudyApp.swift`, `desktop/build_app.py`.
- **Status** fixed.

### B-118 — New volume clamp recursively assigned its published property

- **Severity** P1
- **Repro** Change playback volume in the initial percentage-display build.
- **Expected** Update gain and its percentage without interrupting the app.
- **Actual** The clamp reassigned the published volume property on every change, repeatedly invoking its setter until the app crashed.
- **Fix** Only reassign an out-of-range value; valid values update the mixer directly. Keep the 0–100% limit, visible percentage, and 100% initial level.
- **Relevant files** `desktop/Sources/SongStudy/AudioTransport.swift`, `SongStudyApp.swift`.
- **Status** fixed; transport regression and native repeated adjustments, lower/upper endpoints, and solo preservation checks pass. All thirteen Swift tests pass with macOS audio component access.

### B-119 — Energy loudness input could cancel stereo channels before measuring power

- **Severity** P2
- **Repro** Run `lufs_1s` on a two-channel tone with opposite channel polarity, then on the same tone in phase.
- **Expected** Equal channel powers give equal stereo loudness regardless of interchannel polarity.
- **Actual** Averaging channel samples before measuring power canceled the opposite-polarity signal, understating the regression example by about 97 dB. This could turn a stereo/phase change into a false energy dropout.
- **Fix** Filter each channel independently and sum channel powers. The existing one-second filter remains an approximation, not a complete BS.1770 loudness meter.
- **Relevant files** `backend/arrangement/dsp/loudness.py`, `backend/tests/test_analysis.py`.
- **Status** fixed; opposite-polarity, mono/dual-mono, and level-monotonicity tests pass. All seven saved legacy analyses and desktop-library records were recomputed on 30 September 2026, with section edits and undo history preserved.
