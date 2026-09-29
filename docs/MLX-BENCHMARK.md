# All-In-One MLX trial — 2026-09-29

Evaluated [`all-in-one-mlx` 1.0.14](https://github.com/ssmall256/all-in-one-mlx) on the four songs already in the app before integration. The package ran on this Apple Silicon Mac with existing six-source Demucs stems. GPU access was required. The model returns BPM, beats, downbeats, and section boundaries/labels. It does not return a confidence score. The app now uses it as the primary structural source when installed; this table records why its output should remain editable rather than treated as ground truth.

| Song | App BPM | MLX BPM | Section observations |
| --- | ---: | ---: | --- |
| Taste | 113 | 113 | Both find the first two chorus entrances near 0:26 and 1:13. A listener places the final chorus entrance at about 1:58. MLX starts it at 1:38 (about 20 seconds early), while our app starts it at 2:11 (about 13 seconds late). MLX misses our app's short opening intro. |
| All the Small Things | 148 | 146 | MLX finds three chorus regions near 0:45, 1:31, and 2:10, and recognizes the instrumental solo near 1:44–2:10. Our app puts choruses a few seconds later and calls that solo instrumental. MLX extends its last chorus to the end instead of identifying an outro. |
| DNA | 140 | 140 | BPM agrees. Section labels differ substantially; neither map has been listener-validated. |
| Hit It and Quit It | ≈141, low confidence | 74 | The MLX beat track is mostly near 0.8 s per beat, with some 0.4 s intervals. Half/double-time ambiguity is unresolved; doubling its BPM would yield 148, still distinct from the app's unstable estimate. The section maps also differ. |

The MLX beat intervals were fairly consistent on Taste (5th–95th percentile: 0.52–0.54 s), All the Small Things (0.39–0.42 s), and DNA (0.42–0.47 s). On Hit It and Quit It they ranged from 0.39 to 0.83 s, so its single-number tempo should not be accepted uncritically. Our beat intervals on DNA were less consistent (0.22–0.48 s).

With cached stems and downloaded weights, each run took roughly 15–25 seconds on this machine. The port's Apple Silicon/Metal requirement means it cannot become the only analysis path for non-Mac environments. It also labels adjacent sections with the same name and sometimes emits near-zero-length start/end segments; an integration would need normalization.

**Update after the boundary repair:** The table above records the original model-only comparison. The current app treats the 1:38–1:58 passage in Taste as an unnamed passage and starts its final chorus at 1:58.1. This repair requires a long same-labelled model span with an A-B-A repeat identity, a strong internal novelty seam, and a fall-then-rise in energy. None of the other five saved songs met that rule in the current library. A listener can also split a displayed section and name both sides without running the models again. More listener-marked songs are still needed before broadening the automatic rule. The model adds no direct instrument, energy, or stereo explanation; those still come from the current pipeline.
