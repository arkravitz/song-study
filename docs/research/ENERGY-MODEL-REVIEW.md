# Energy model review

Reviewed 30 September 2026. Scope: research relevant to the existing song-relative arrangement-energy curve, not a new emotion-recognition system.

The current equation is a defensible, useful **heuristic for relative arrangement intensity within a recording**. Research supports combining loudness, rhythmic activity and timbral information. It does not establish the application's exact coefficients, nor make the output a calibrated measure of perceived energy.

## Evidence

| Primary research | Finding | Implication and limit |
| --- | --- | --- |
| [Dean, Bailes & Schubert (2011), Acoustic Intensity Causes Perceived Changes in Arousal Levels in Music](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0018591) | In 38 listeners' continuous ratings, reversing a piece's intensity contour also reversed its perceived-arousal contour. Other pieces given that intensity contour produced related arousal trajectories. | Strong direct evidence for retaining loudness as a major contributor. The stimuli were orchestral, piano and filtered-noise examples; this does not determine a coefficient for compressed pop recordings. |
| [Gomez & Danuser (2007), Relationships between musical structure and psychophysiological measures of emotion](https://pubmed.ncbi.nlm.nih.gov/17516815/) | Across 16 excerpts and 11 structural features, tempo, accentuation and rhythmic articulation differentiated high from low arousal and related strongly to physiological measures. | Rhythmic activity belongs in an intensity proxy. Onset rate and spectral flux are imperfect substitutes for articulation and accentuation; the paper does not validate counting every detected transient equally. |
| [Fabiani & Friberg (2011), Influence of pitch, loudness, and timbre on the perception of instrument dynamics](https://pubmed.ncbi.nlm.nih.gov/21974491/) | A factorial listening study of five instruments found similarly large effects of loudness and timbre on judged dynamics. | Loudness alone misses musically meaningful intensity. This concerns isolated tones and performance dynamics, not full arrangements; it cannot justify the application's bass/high-band coefficients. |
| [Eerola, Friberg & Bresin (2013), Emotional expression in music: contribution, linearity, and additivity of primary musical cues](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2013.00487/full) | Controlled variations of six cues in 200 synthesized examples were largely explained by additive linear models; nonlinear gains were generally modest. | A transparent weighted sum is a reasonable starting architecture. These were ratings of four expressed emotions, not this application's within-song arrangement-energy target. Their fitted coefficients cannot simply be copied. |

## What follows for this application

These are design and mathematical inferences, not claims that the cited studies validated this implementation:

- **Keep the separate concept of arrangement intensity.** Acoustic power, perceived loudness, expressive arousal and a producer's sense of an arrangement becoming bigger overlap, but are not interchangeable. The curve helps locate changes; it should not claim to measure a listener's emotional state.
- **Keep the useful combination for now.** Loudness and rhythmic/transient activity have good conceptual support. Separated-part count and occupied spectrum are interpretable arrangement cues. Stereo width is useful evidence when explaining an expansion, but the papers above do not establish a universal monotonic relation between width and energy. A wide soft pad can be less energetic than a narrow drum fill.
- **Do not add BPM merely because research implicates tempo.** A constant song BPM adds no within-song variation and becomes zero under song-wide z-scoring. Rhythmic activity changes at constant BPM remain relevant. A local tempo estimate would require separate validation, not a token new term.
- **Treat standardization as the larger modeling risk than a constant divisor.** Equal z-score variance lets a component with tiny real variation participate on the same scale as one with a large musical change. Small fluctuations in width, separation leakage or activity thresholds can therefore have disproportionate influence. Several spectral components and loudness also co-vary, so their weights are not independent causal percentages.
- **Normalize the available coefficients once.** For `S(t) = sum(w_i * z_i(t)) / sum(w_i)`, define `p_i = w_i / sum(w_i)` and compute `S(t) = sum(p_i * z_i(t))`. This is the same equation. Optional missing components mean the denominator is not always the declared default total, so effective weights should be normalized after assembling available components. A constant component contributes zero after z-scoring; its nominal weight is not evidence of actual influence.
- **The final percentile scaling already cancels a constant positive divisor.** If the displayed curve is `(S - percentile_2(S)) / (percentile_98(S) - percentile_2(S))`, multiplying every frame by one positive constant leaves the result unchanged, including subsequent clipping. Cleaning up coefficient representation is worthwhile; changing that divisor alone cannot improve the displayed curve.
- **Preserve qualification in explanations.** Weighted component changes explain this model's arithmetic. They are not measured causal percentages of why a listener experiences a chorus as bigger. Smoothing and clipping additionally make attribution approximate.

## Stereo loudness implementation

[ITU-R BS.1770-4, Annex 1, equations 1–2](https://www.itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.1770-4-201510-S!!PDF-E.pdf) measures filtered mean-square power separately in each channel, then combines those powers. It does not first average left and right waveforms. That distinction is essential: opposite-polarity channels can cancel in a mono sum even though both channels contain substantial sound. The main implementation review found that premature waveform averaging in `lufs_1s`; correcting it is a measurement bug fix, not an arbitrary change to arrangement-energy weighting.

The implementation now sums independently filtered channel powers, preserving mono measurements and giving dual-mono stereo the expected 3.01 dB increment. For song-relative standardization that constant increment cancels; phase cancellation does not, and can change the curve within a stereo recording. This correction alone does **not** establish full BS.1770 compliance: the existing weighting filter is approximate and the application's one-second local curve serves a different purpose from gated programme loudness. [BS.1770-5 is the current revision](https://www.itu.int/rec/R-REC-BS.1770/en); the freely accessible fourth revision supplies the channel-processing equations cited here.

## Better ways to evaluate future changes

The next substantial improvement should come from validation, not another guessed set of percentages. Assemble a small, varied set of finished recordings and ask musicians to mark relative expansion, dropout and sustained intensity, with uncertain passages allowed. Compare the existing curve against loudness alone and a simpler loudness/rhythm/layer combination. Include highly limited pop mixes, quiet but dense music, stereo-only changes, separation leakage, and endings. Measure whether meaningful transitions are ranked correctly before considering a learned predictor or altered weights.

A learned arousal predictor could be useful later, but would need labels that match the product's target and within-song time resolution. General emotional-arousal ratings across excerpts do not automatically supply those labels. Keep the existing explainable curve until a replacement demonstrably answers producers' questions better.

## Changes made after review

- Declared the existing normalized coefficient shares directly, removing the historical `0.955` divisor from their definition.
- Normalized available component shares once before summing, so exported weights reflect the actual calculation even when stems or width are unavailable. The curve and attribution retain the existing balance.
- Fixed the loudness input's premature stereo waveform averaging. The test compares identical-power in-phase and opposite-polarity stereo signals; the old implementation understated the latter by about 97 dB in that example.
- Retained the existing z-scoring, percentile display scaling, smoothing, and coefficient balance. Those need listening validation before changing musical behavior.

Existing saved analyses are not rewritten. The channel-power correction applies when analysis is computed again, and new desktop builds bundle the corrected backend.

### Current default shares

These are heuristic coefficients on standardized signals, not causal shares of musical energy.

| Input | Share |
| --- | ---: |
| Loudness | 27.23% |
| Active separated-part categories | 17.80% |
| Onset density | 11.52% |
| Bass-band activity | 10.47% |
| High-band activity | 9.42% |
| Spectral flux | 7.33% |
| Percussive share | 3.66% |
| Occupied frequency bands | 6.28% |
| Stereo width | 6.28% |

Rounding makes the displayed percentages sum to 99.99%; stored coefficients sum to one. Missing optional inputs redistribute the remaining shares proportionally.

Validation: all 33 analysis tests and eight desktop worker tests pass, including curve-equivalence, effective-weight sums, scaled-coefficient invariance, change attribution, and stereo-channel power cases.
