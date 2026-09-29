# Product trust audit — 29 September 2026

The strongest product is the **shape of a song**: a song-relative energy curve,
structural boundaries, and the ability to listen at a change. Those observations
can lead to useful questions even when the system cannot confidently name every
section or instrument. The default study view should let measured change lead;
names and causal explanations should be treated as interpretations.

I inspected the running UI and saved analyses for **Taste**, **DNA**, **All the
Small Things**, and **03 - hit it and quit it**. This is a product audit, not a
formal accuracy benchmark: the four recordings do not provide human-labeled
ground truth.

| Output | How it is made | What can be trusted | Product placement |
| --- | --- | --- | --- |
| Waveform, loudness, spectrum, stereo image | Direct audio measurements. Stereo width and spectral bands are computed summaries. | Strong for relative changes; mastering, encoding and mono material affect interpretation. | Contextual evidence; the energy shape is the useful overview. |
| Song-relative energy | Weighted, normalized combination of loudness, transients, rhythmic activity, frequency activity, stereo width and separated-stem layer count. | Useful for the **within-song shape**, not an absolute energy percentage or a cross-song score. Some inputs depend on source separation. | Primary curve and major change landmarks. Show components on request. |
| Beat/bar grid | Signal-derived onset and metrical inference with confidence and irregular-pulse warnings. | Usually navigationally useful; bar claims weaken on unstable rhythm. | Reveal bars at closer zoom. Suppress precise bar-based claims when the grid is weak. |
| Source identities and activity | Source-separation model followed by per-stem activity thresholds and smoothing. The `Source.confidence` field is largely activity contrast, **not** a calibrated probability that a named instrument exists. | Broad stems such as bass/drums/vocals are useful hypotheses. Fine entrances, false piano/other activity and bleed need listening. | Helpful lanes and contextual suggestions; avoid stating doubtful instrument causation as fact. |
| Structural boundaries and repeats | Audio novelty, similarity and grouping. | Often more useful than the role name, but occasional oversegmentation remains. | Primary section landmarks, with repeated passages connected. |
| Verse/chorus/bridge names | Heuristic role scoring, optionally revised by a language model. The numeric label score is not calibrated semantic correctness. | Provisional. Taste has four consecutive “choruses”; All the Small Things misnames recurring material. | Clearly suggested names, easy user rename, and less visual authority than boundary/repetition. |
| Arrangement events | A mixture of measured changes and source-activity inferences, then deduplication. | “Something changed” may be stronger than a specific “Piano enters.” Event confidence varies by detector and is not a common probability scale. | Highlight only major events by default; show detection/evidence on selection. |
| Patterns and producer commentary | Rules over the above analysis; optional LLM text grounded against the stored evidence. | Only as strong as the inputs and grounding. Similarity/contrast observations are safer than claims about production intent. | Optional exploration and evidence, never equal authority to measurements. |

## Energy review

The existing curve remains valuable. Its coefficients are sensible as a
**product heuristic**, but have not been perceptually calibrated against human
ratings. The former weight declaration advertised an unused harmonic-density
term and hid the second percussion contribution. The applied coefficients have
now been named and normalized without changing their proportions.

A genuine timing bug read the 20 Hz onset-density curve as 1 Hz. That stretched
roughly the opening eight seconds of rhythm across a three-minute song. The
corrected curve remains strongly similar overall on the four saved tracks
(correlations 0.95–0.97 in a reconstruction), but local differences reached
0.18–0.29 on the 0–1 score and the order of prominent rises sometimes changed.
Contributor ranking also used unweighted component movement; it now ranks by
approximate effect on the actual normalized model. The four local songs were
reanalysed from cached stems with the corrected model, preserving their saved
comparison notes. Backups of the previous JSON and curve files are in
`data/backups/2026-09-29-energy-refresh/`. The analysis version is now `0.1.1`.
Refreshing the full analyses mattered: the old DNA comparison put its second
suggested chorus roughly nine energy points below the first; the corrected
means are close. Replacing only the curve would have left section summaries and
precomputed explanations inconsistent.

The rhythmic-density helper also returned average onsets per 50 ms bin divided
by a two-second window while describing the result as onsets per second. Its
absolute unit is corrected. Because the energy model standardizes that series,
this unit correction does not materially change the curve. Stored component
curves are standardized deviations, so new analyses label their units `z`
instead of incorrectly calling those values LUFS, dB or onsets/s.

At whole-song scale, a nearly silent tail can leave the musically active part
of a loud recording flattened near the top of the energy row. All the Small
Things now uses a split vertical display scale: the quiet range gets the bottom
quarter and the active range gets the other three quarters. This applies only
when the body of the song is consistently energetic and its ending approaches
silence; closer zoom and the underlying analysis retain the original values.
The timeline labels the compressed quiet range so the display treatment is
visible rather than mistaken for a model change.

## Highest-value next product changes

1. Make clicking a rise or drop answer two separate questions: **what changed
   acoustically?** and **which source parts might explain it?** The former can
   be stated firmly; the latter should use language proportional to evidence.
2. Keep the song-relative energy line and major sections as the default map.
   Show event text, detailed traces and bar-level features only at useful zoom
   or when selected.
3. Treat repeated passage comparison as a primary action. Distinguish grounded
   loudness/width/rhythm differences from tentative instrument labels.
4. Evaluate section naming against hand-labeled songs. Current role scores do
   not justify confident conventional labels across genres; preserve user names.
5. Establish a small human listening benchmark for energy landmarks and stem
   entrances before further model tuning. A new measurement is valuable only if
   it helps a producer learn something reliable from the record.
