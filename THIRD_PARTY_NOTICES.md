# Third-party components and model terms

The project MIT license covers original source, including the synthetic demo generator. External software and model weights retain their own licenses. No song, checkpoint, packaged application runtime, or ffmpeg binary is distributed in this repository.

Dependencies are declared in `pyproject.toml` and `frontend/package-lock.json`. Installation retains their distribution notices. The principal upstream projects are:

- Demucs: https://github.com/facebookresearch/demucs
- Beat This: https://github.com/CPJKU/beat_this
- All-In-One MLX, optional Apple Silicon backend: https://github.com/ssmall256/all-in-one-mlx; original model: https://github.com/mir-aidj/all-in-one
- librosa: https://github.com/librosa/librosa
- PyTorch: https://github.com/pytorch/pytorch
- NumPy: https://github.com/numpy/numpy
- SciPy: https://github.com/scipy/scipy
- FFmpeg, supplied separately: https://ffmpeg.org/legal.html
- React: https://github.com/facebook/react

Model-checkpoint licenses must be checked separately from library-code licenses before redistributing weights. FFmpeg obligations depend on how a binary was built. The local desktop packager is not a public release mechanism or a complete redistribution-license audit. The checked-in app icon is AI-generated project artwork; its prompt is recorded in `desktop/Assets/README.md`.
