# Arrangement-energy equation experiments

30 September 2026. These candidates use existing measurements. They are research-informed hypotheses to compare, not learned or perceptually calibrated equations.

## Research that can inform the experiment

[Friberg et al. (2014), Using perceptually defined music features in music information retrieval](https://arxiv.org/pdf/1403.7923), found that listener-rated dynamics and speed were the strongest predictors of energy in two datasets. Simple linear regression generally performed as well as more complex regression approaches. Crucially, listener-rated features predicted energy more reliably than generic extracted audio features could predict those perceptual features. The study supports a small combination of meaningful dimensions, not copying its fitted coefficients onto LUFS and spectral flux. Its excerpt-level ratings also do not validate within-song transitions.

[Dean et al. (2011)](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0018591) supplies causal evidence that intensity changes can alter continuously perceived arousal. [Eerola et al. (2013)](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2013.00487/full) supports mostly additive cue models in controlled emotional-expression experiments. Together these justify testing simpler additive combinations with a dynamics/rhythm backbone. They do not establish the percentages below. A producer's judgments of arrangement expansion remain the evaluation target, allowing arousal/dynamics evidence as useful related evidence rather than discarding it.

## Shared preparation

Use corrected per-channel loudness measurements for every candidate, including the existing equation. Resample all features to the same time grid. Keep existing clipped song-wide z-scores, 0.35-second smoothing and final 2nd/98th-percentile display scaling for the first experiment so differences isolate the equation rather than several unrelated changes at once.

Define:

- `L`: standardized local loudness.
- `O`: standardized onset density.
- `X`: standardized spectral flux (existing `percussive_energy`).
- `A`: standardized active separated-part count.
- `B`: standardized bass-band level.
- `P`: standardized occupied-spectrum fraction (existing `band_spread`).
- `R = 0.60 O + 0.40 X`: rhythmic activity; groups two related cues under one budget.
- `F = 0.50 A + 0.25 B + 0.25 P`: arrangement fullness; groups layer participation and spectral occupation under one budget.

Grouping does not make correlated inputs independent or eliminate all double counting. It prevents each extra related measurement from silently creating another major dimension. Do not re-standardize `R` or `F` after grouping: that would silently change their effective weights.

## Three candidates

| Candidate | Equation before smoothing/display scaling | Exact component weights | Prediction | Risk |
| --- | --- | --- | --- | --- |
| Dynamics-led | `0.65 L + 0.35 R` | loudness 65%; onset 21%; flux 14% | Clean crescendos, rhythmic arrivals and real acoustic dropouts should dominate. Useful simple reference informed by dynamics/speed evidence. | May understate a chorus expansion in a heavily limited recording with little loudness change. |
| Grouped balance | `0.40 L + 0.30 R + 0.30 F` | loudness 40%; onset 18%; flux 12%; layers 15%; bass 7.5%; spectrum 7.5% | Retains dynamics/rhythm while registering entrances and fuller spectral occupation. Expected practical compromise. | Layer-threshold noise and correlated bass/loudness still contribute. |
| Arrangement-led | `0.25 L + 0.25 R + 0.50 F` | loudness 25%; onset 15%; flux 10%; layers 25%; bass 12.5%; spectrum 12.5% | Should emphasize arrangement expansion/dropout even where mastering flattens loudness. | Can confuse more detected parts with more intensity, or exaggerate separation leakage and sustained quiet layers. |

All three initially omit stereo width, high-band level and harmonic/percussive ratio. This is an ablation to test whether their extra complexity improves the task, not a claim that these cues are irrelevant. Stereo width remains valuable contextual evidence. If the simpler curves miss a clearly useful timbral change, compare adding that cue back under a fixed group budget rather than increasing total influence of correlated features.

Missing optional measurements: normalize available nonzero coefficients once, retaining the relative weights of available terms. A supplied but genuinely constant component has a zero standardized curve; record this in the report rather than implying it explains a change. Weight percentages describe arithmetic influence, not causal percentages of musical perception.

## Evaluate with the existing songs

Compare the corrected existing equation, loudness alone and the three alternatives. Generate aligned plots with identical time axes and the same smoothing setting, plus section-average and transition-delta tables. Different percentile limits are necessary for the existing display convention, but report pre-display deltas too: a larger plotted excursion can result from rescaling rather than stronger underlying evidence.

For each recording, listen to short before/after excerpts and record the ordering **before choosing a winning curve**:

1. Does the first chorus become more intense than its preceding passage when the instrumentation supports it?
2. Are obvious internal drops and returns correctly signed and among the largest changes?
3. Does a repeated chorus genuinely become larger, stay similar or shrink? Do not assume a later chorus must increase.
4. Do transient fills create plausible short changes without eclipsing sustained section structure?
5. Does the ending stay visible without winning the ranking of informative internal dropouts?
6. Does a silent/near-silent separated part remain inconsequential?

Known useful stress cases: the final fade in **All The Small Things**, the dropout/return near the last chorus in **Taste**, and Taste's quiet piano/separation-leakage passages. The user's correction that Taste's last chorus starts around 1:58 is a structural listening cue, not proof that its energy must be above every earlier chorus. Edited section names/boundaries must remain intact during recomputation.

Score signed-transition agreement and pairwise section-intensity ordering, rank the most informative internal expansions/drops, and flag jitter/false peaks. Listening judgments are ground truth for these task questions; separation-derived active counts are input evidence, not independent validation. Without independent listening labels, numerical agreement with section names or active layers is a diagnostic only. Prefer the simplest candidate that consistently agrees with listening across songs. Three familiar songs cannot establish universal or research-calibrated weights.

## Next experiment if none wins clearly

Keep the winning weighting fixed, then test normalization separately. Song-wide z-scoring boosts a tiny-variance cue to the same scale as a substantially changing cue. A meaningful-variation floor or saturating physical-unit transform could suppress that artifact, but its thresholds need listening validation. Do not change weights, normalization and smoothing together and then attribute an improvement to one of them. A fitted model should be trained only after gathering labels for the actual arrangement task, splitting validation by song rather than by neighboring frames.
