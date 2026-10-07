# Energy equation comparison on the saved library

30 September 2026. After auditioning the alternatives below, the user selected Balanced for the app. Balanced is now the default for new analyses, and all seven saved recordings were updated to match the auditioned Balanced curves. The migration backup is in `data/backups/energy-balanced-2026-09-30/`; section edits, undo/redo history and stem assets were preserved.

The following tables document the earlier comparison against the corrected original equation. Its “Current corrected” curve is the historical reference, not the app’s current default.

## What changed in the saved library

The refresh reread each original recording for local loudness, filtering channels independently and summing their powers. It reused the cached standardized rhythm, spectrum, separated-part participation and width inputs. It regenerated raw/perceived energy, section energy summaries, local section loudness, energy-change markers/contributors and energy fingerprint fields. It did not rerun source separation or section detection.

Seven JSON/NPZ pairs and seven desktop database snapshots were updated. All section IDs, names, boundaries, native edit revisions, undo/redo cursors, origin paths and stem assets were checked against backups. Every unrelated array and analysis field stayed intact. The backup and comparison output are in `data/backups/energy-refresh-2026-09-30-final/`. Subframe sections use a midpoint sample when their duration is shorter than one energy frame, without moving their boundaries.

| Recording | Average absolute correction, points | Largest correction, points |
| --- | ---: | ---: |
| Taste | 0.50 | 3.04 |
| All the Small Things | 1.22 | 3.53 |
| DNA | 0.44 | 4.98 |
| Hit It and Quit It | 0.28 | 1.32 |
| The Odyssey | 0.44 | 9.10 |
| F It Freestyle | 0.17 | 0.95 |
| Do You Do This Often remix | 0.55 | 2.84 |

These are changes on each song's relative 0–100 display scale, not decibels or accuracy improvements. A constant stereo level offset disappears after song normalization; changing stereo relationships can alter the shape.

## Why these alternatives

Research makes dynamics and rhythmic activity reasonable foundations for perceived energy. [Friberg et al.](https://arxiv.org/abs/1403.7923) found that a few perceptual features predicted emotion well, but that generic extracted audio measurements were imperfect substitutes for listener-rated features. [Dean et al.](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0018591) experimentally demonstrated an influence of acoustic intensity on perceived musical arousal.

For arrangement study, the practical question is whether perceived growth and contraction are better represented by those dimensions alone or with a contribution from fullness. This is related to arrangement effectiveness, but no equation here measures creative quality. A quiet, sparse passage can be an excellent arrangement decision.

The exact shares below are hypotheses, not coefficients fitted by those papers. Full definitions and source discussion are in [the experiment design](ENERGY-EQUATION-EXPERIMENTS.md).

| Equation | Loudness | Rhythm | Fullness |
| --- | ---: | ---: | ---: |
| Dynamics-led | 65% | 35% | 0% |
| Balanced | 40% | 30% | 30% |
| Arrangement-led | 25% | 25% | 50% |

Rhythm combines 60% attack frequency and 40% spectral flux. Fullness combines 50% active separated-part categories, 25% bass activity and 25% occupied spectrum. These groups are not re-standardized. Each candidate uses the same cached inputs, clipped song-wide standardization, 0.35-second smoothing and 2nd/98th-percentile display scaling. The current equation is the fourth comparator. Previous saved curves are also available to isolate the stereo correction.

## What the numerical comparison shows

The balanced equation retains much of the existing shape with six inputs instead of nine. Arrangement-led is even closer overall. Dynamics-led changes which passages appear relatively strong, especially verses and rhythmic returns.

| Alternative | Mean correlation with corrected current curve | Mean absolute difference, points |
| --- | ---: | ---: |
| Dynamics-led | 0.907 | 7.35 |
| Balanced | 0.984 | 3.32 |
| Arrangement-led | 0.992 | 2.51 |

These diagnostics use the 5 Hz comparison series, averaging equally across seven songs. Correlation is similarity to the current heuristic, not evidence of perceptual accuracy. The saved curves and section means remain at 20 Hz. The listening page also shows section changes before percentile display scaling, because a bigger visual excursion can result from different scaling limits.

In Taste, all four equations detect the internal drop near 1:55. Dynamics-led selects the return near 1:59 as the largest rise; current, balanced and arrangement-led select the later expansion near 2:07. This is a useful listening test: which move actually feels like the more important expansion?

Taste's first chorus compared with its preceding pre-chorus rises approximately 17, 12, 16 and 17 display points for current, dynamics-led, balanced and arrangement-led respectively. The second chorus rises 10, 5, 8 and 10 points over its preceding pre-chorus. Fullness weighting therefore changes the prominence of those expansions rather than merely duplicating the loudness-led equation.

In All the Small Things, the alternatives disagree about the biggest internal dropout. Dynamics-led and balanced select about 1:44; arrangement-led selects about 0:59. All retain the ending on the graph. The comparison's internal-change rankings exclude the transition into the quiet terminal tail as well as the first four and final eight seconds.

DNA provides a useful agreement case: all alternatives select the large rise around 0:56 and drop around 1:49. Hit It and Quit It provides another disagreement case: dynamics-led and balanced select the drop around 1:15, while arrangement-led favors the later change around 3:32.

These are computational observations. No independent listener judgments were collected, so there is no validated winner. My preferred next candidate for listening is Balanced: it preserves most familiar arrangement shape while reducing the number of overlapping measurements. Dynamics-led is a useful contrasting reference; arrangement-led tests how much value the separated-part count contributes.

## Listening comparison

Run from the repository root:

```sh
.venv/bin/python desktop/tools/recompute_energy.py serve --out data/backups/energy-refresh-2026-09-30-final --port 8850
```

Open [the local listening comparison](http://127.0.0.1:8850/). It includes all seven original recordings, aligned section landmarks, selectable curves, click/drag seeking, section means and jumps to the largest internal rise/drop. Audio stays on this computer; the server binds only to localhost. Playback and seeking were checked in the browser. The report remains available on disk when the server stops.

A useful audition starts with Taste at 1:53, All the Small Things around 0:57 and 1:42, DNA at 0:54 and 1:47, then Hit It and Quit It near 1:13 and 3:30. Decide what sounds larger or smaller before selecting the preferred curve. More detected parts should not automatically win the judgment.

## Verification

47 focused energy/migration/worker tests pass. The broader backend and worker suite passes 158 tests with one previously logged cached-library section-label audit failure, B-111. Its edited Intro fragment was present before this refresh; its section data was preserved. The desktop bundle was rebuilt successfully. Post-apply verification confirmed the saved files match the staged curves and the desktop edit history, cursor positions and stem assets match the backup.

## Balanced promotion verification

The app bundle now contains the six-input Balanced default, verified using its private Python runtime. All seven saved curves match the previously auditioned Balanced series within its four-decimal report precision. Post-apply checks confirmed the new desktop/legacy curves agree and all section edit revisions, undo/redo cursors, origin paths and stem assets match the migration backup. The backend and worker suite passes 159 tests with the same pre-existing B-111 section-label audit failure.
