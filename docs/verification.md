# Publication verification

Local checks on October 5, 2026:

- Backend and desktop worker: 165 pytest tests passed, including portable positional-label regressions and the synthetic demo generator.
- Frontend: 9 behavior tests passed; TypeScript and production build passed.
- Native Mac source: 13 Swift tests passed with macOS audio-component access; release build passed. Restricted sandbox execution blocks audio and Xcode debug-symbol generation, so those checks used ordinary host permissions.
- A fresh minimal demo environment imported the API, performed a real section split, and passed headless-browser rendering, audio playback and seeking checks without ML dependencies.
- The README screenshot shows only the generated synthetic arrangement. No real songs, stems, cached analyses, weights or packaged installer are included.

These checks do not establish musical-analysis accuracy, checkpoint redistribution rights, clean-machine desktop installation, or notarization. Paid inference and a full real-song analysis were not rerun for this publication snapshot. GitHub CI repeats automated checks on clean runners.
