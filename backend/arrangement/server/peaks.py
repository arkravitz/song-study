"""Peak generation for waveform display.

Computed once at load and cached as a compact binary.  The client never gets
raw samples: a 4-minute song is ~85 MB as float32, but its peaks are ~2 MB of
int8 and render instantly at any zoom.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import soundfile as sf

#: Peaks per second. 400 gives sub-3ms resolution, far finer than any screen.
PEAK_RATE = 400

#: Bumped when the stored layout changes. A cached pyramid without a matching
#: version is recomputed rather than read with the wrong number of columns, which
#: is the same failure as asking the API for a curve that does not exist and
#: getting an empty series instead of an error.
# 4 records the true bucket rate. Version 3 stored the requested 400 Hz even
# when 22,050 Hz audio was divided into 55-sample buckets (400.909 Hz), so a
# transient near the end of a song was drawn hundreds of milliseconds late.
# Version 2 stored the signed mean, which cancels to nothing.
# The version is what stops a stale pyramid being read with the wrong meaning.
PEAK_FORMAT = 4


def peaks_path(source_path: str | Path, sample_rate: int) -> Path:
    from arrangement.config import get_settings

    s = get_settings()
    try:
        st = source_path.stat()
        key = f"{Path(source_path).name}:{st.st_size}:{int(st.st_mtime)}:{sample_rate}"
    except OSError:
        key = str(source_path)
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    return s.cache_dir / "peaks" / f"{h}.npz"


def _resample_to(data: np.ndarray, sr: int, target: int) -> np.ndarray:
    """Resample to `target` Hz, returning the same ``(frames, channels)`` shape.

    This is not `librosa.resample`, and the reason is worth recording.

    `librosa.resample(data.T, orig_sr=44100, target_sr=22050, axis=0)` returns
    shape ``(1, 8203392)`` where the input was ``(8203392, 2)``. It resamples the
    right number of samples — the element count does halve — and then puts them
    in the wrong shape. The transposed call in this function therefore turned a
    stereo file into an 8.2-million-row single column, and every downstream
    frame was interpreted as 22050 Hz, so the stored time base came out at
    exactly twice the file's length.

    Nothing noticed, because the column count is never checked against anything
    and the pyramid is only read through `peaks_for_range`. What it looked like
    on screen: the 808 in "DNA." enters at 1.730 s and was drawn at 3.470 s. Every
    lane of every song was running at double speed, with the boxes — which come
    from the analysis, not from here — sitting in the right place and the
    waveform underneath them in the wrong one. The mix was correct, which is why
    it read as a problem with the lanes rather than with the peaks: the mix goes
    through ffmpeg in `_decoded_wav` and lands on 22050 Hz already, so it never
    took this branch.

    `resample_poly` is exact for the rational ratios that matter here, has no
    shape surprises, and costs one FFT pass. The output length is asserted
    against the real sample count so a future substitution cannot quietly
    reintroduce this.
    """
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(int(sr), int(target)) or 1
    up, down = int(target) // g, int(sr) // g
    out = resample_poly(data, up, down, axis=0)
    expect = int(round(data.shape[0] * up / down))
    if out.shape[0] != expect or out.shape[1] != data.shape[1]:
        raise RuntimeError(
            f"resample {sr}->{target} Hz gave {out.shape}, expected "
            f"({expect}, {data.shape[1]}); refusing to store a pyramid with "
            f"the wrong length"
        )
    return out


def compute_peaks(source_path: str | Path, sample_rate: int = 22050,
                  rate: int = PEAK_RATE) -> dict:
    """Build a min/max peak pyramid.

    ``rate`` peaks per second at the base level, then each further octave halves
    the rate, so zooming out is just reading a coarser level rather than
    resampling.  This is the same idea as a mipmap and it is what makes
    scrub-while-loading feel instant.
    """
    out_path = peaks_path(source_path, sample_rate)
    if out_path.exists():
        cached = _load_peak_file(out_path)
        if cached is not None:
            return cached

    data, sr = sf.read(str(source_path), dtype="float32", always_2d=True)
    if data.shape[0] == 0:
        raise ValueError("empty audio file")
    if sr != sample_rate:
        data = _resample_to(data, sr, sample_rate)
        sr = sample_rate
    duration = data.shape[0] / sr

    mono = data.mean(axis=1)
    levels: dict[str, np.ndarray] = {}
    block = max(1, int(round(sr / rate)))
    n = (len(mono) + block - 1) // block
    padded = np.pad(mono, (0, max(0, n * block - len(mono))))
    frames = padded.reshape(n, block)
    # [min, max, rms].
    #
    # The rms is the loudness envelope; the min/max pair is the bound. At
    # whole-song zoom the bound saturates — measured on "DNA." it is exactly
    # 1.0000 at the 10th, 50th and 90th percentile, which is why the row was one
    # solid rectangle — and the rms is the only one of the three that still
    # describes the music.
    #
    # rms, not mean. The first attempt stored the arithmetic mean, which is
    # signed, and the positive and negative half-cycles cancel: it came back at
    # 0.0002 where the bound was 1.0, a ratio of 4118x, and the row would have
    # drawn a flat line near zero. A mean of samples is not a loudness.
    cur_min = frames.min(axis=1)
    cur_max = frames.max(axis=1)
    cur_rms = np.sqrt(np.maximum(0.0, (frames.astype(np.float64) ** 2).mean(axis=1)))
    levels["0"] = np.stack([cur_min, cur_max, cur_rms], axis=1).astype(np.float16)
    level = 0
    while len(cur_min) > 256:
        # Pairing for the mean needs an even count. A track whose length in
        # buckets is odd (37289, say) leaves one bucket unpaired, and
        # `arr[0::2] + arr[1::2]` then fails to broadcast — a 500 on the peaks
        # endpoint, which is the waveform. The odd bucket is dropped, which is a
        # rounding error against a level that is about to be halved again.
        if len(cur_rms) % 2:
            cur_rms = cur_rms[:-1]
            cur_min = cur_min[:-1]
            cur_max = cur_max[:-1]
        k = 2
        # Every level halves buckets of equal sample count, so pooling the two
        # children's mean squares is the parent's mean square exactly. No
        # weighting, no approximation.
        cur_rms = np.sqrt((cur_rms[0::2] ** 2 + cur_rms[1::2] ** 2) / 2.0)
        cur_min = np.minimum(cur_min[0::2], cur_min[1::2])
        cur_max = np.maximum(cur_max[0::2], cur_max[1::2])
        level += 1
        levels[str(level)] = np.stack([cur_min, cur_max, cur_rms], axis=1).astype(np.float16)
        if level >= 9:
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Store the metadata *inside* the file so a cached pyramid is
    # self-describing.  Returning it only from the fresh-compute path meant a
    # cache hit silently lost the sample rate, and every range query after that
    # returned nothing.
    payload = dict(levels)
    payload["_duration"] = np.float64(duration)
    payload["_sr"] = np.float64(sr)
    # The block must contain a whole number of samples. At 22,050 Hz a nominal
    # 400 Hz request rounds to 55 samples, whose actual rate is 400.909 Hz.
    # Range queries must use that true rate or their time mapping drifts.
    payload["_peak_rate"] = np.float64(sr / block)
    payload["_format"] = np.float64(PEAK_FORMAT)
    np.savez_compressed(out_path, **payload)
    return _peak_dict(payload, out_path)


def _peak_dict(payload: dict, path: Path) -> dict:
    out = {k: v for k, v in payload.items() if not k.startswith("_")}
    for k in ("_duration", "_sr", "_peak_rate", "_format"):
        if k in payload:
            out[k] = float(np.asarray(payload[k]).ravel()[0])
    out["_path"] = str(path)
    return out


def _load_peak_file(path: Path) -> dict | None:
    try:
        with np.load(path) as z:
            payload = {k: z[k] for k in z.files}
    except Exception:
        return None
    if "_duration" not in payload:
        return None
    # A pyramid written before the mean existed has two columns. Reading it as
    # three would take the max as the mean and draw a saturated row again, so it
    # is discarded and recomputed.
    if int(np.asarray(payload.get("_format", 0)).ravel()[0]) != PEAK_FORMAT:
        return None
    return _peak_dict(payload, path)


def get_peaks(source_path: str | Path, sample_rate: int = 22050) -> dict:
    """Load the peak pyramid, reading from a decoded file if it is not built."""
    out_path = peaks_path(source_path, sample_rate)
    if out_path.exists():
        data = _load_peak_file(out_path)
        if data is not None and _time_base_agrees(Path(source_path), data,
                                                   sample_rate):
            return data

    decoded = _decoded_wav(source_path)
    if decoded is not None:
        return compute_peaks(decoded, sample_rate)
    return compute_peaks(source_path, sample_rate)


#: How far a stored pyramid's time base may differ from the file's real duration
#: before it is treated as belonging to some other file, or to some other version
#: of this code. A pyramid stores whole buckets, so it is always marginally
#: *longer* than the audio, and a decoder can trim a few milliseconds of encoder
#: delay. Both are far inside this. The failure it exists to catch is not subtle:
#: a pyramid built with the wrong sample rate is off by a factor, not a margin —
#: 186 s of audio recorded as 372 s.
_TIME_BASE_TOLERANCE_S = 0.25
_TIME_BASE_TOLERANCE_FRACTION = 0.005


def _time_base_agrees(path: Path, peaks: dict, sample_rate: int) -> bool:
    """Does this pyramid describe *this* file, at this length?

    The cache key is the file's name, size, mtime and the target rate. None of
    those change when the code that *builds* the pyramid changes, so a pyramid
    written by a different version of `compute_peaks` is read back as though it
    were current — which is exactly how a double-speed set of lane waveforms
    survived in the cache while the code that produced it was being edited.

    So the length is checked, not just the identity. A header read is cheap and
    this runs on a cache hit, where the alternative is drawing the wrong thing.
    """
    stored = float(peaks.get("_duration") or 0.0)
    if stored <= 0:
        return False
    try:
        real = float(sf.info(str(path)).duration or 0.0)
    except Exception:
        # A file we cannot stat is a file we cannot check. The stored length is
        # then the only evidence there is, and refusing to draw is worse than
        # the risk of a stale pyramid on a format `sf` does not read.
        return True
    if real <= 0:
        return True
    tol = max(_TIME_BASE_TOLERANCE_S, real * _TIME_BASE_TOLERANCE_FRACTION)
    return abs(stored - real) <= tol


def _decoded_wav(source_path: str | Path) -> Path | None:
    """A 22.05 kHz mono-safe stereo WAV, written once per source file."""
    from arrangement.config import get_settings
    from arrangement.audio.io import probe

    s = get_settings()
    info = probe(source_path)
    digest = hashlib.sha1(
        f"{Path(source_path).name}:{info.get('size_bytes')}:{info.get('duration')}"
        .encode()).hexdigest()[:16]
    out = s.cache_dir / "decoded" / f"{digest}.wav"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        sf.write(str(out), _read_full(source_path), 22050, subtype="PCM_16")
        return out
    except Exception:
        return None


def _read_full(path: str | Path) -> np.ndarray:
    """Read any supported audio file via ffmpeg, so we do not depend on codecs."""
    import subprocess
    import tempfile

    from arrangement.config import get_settings

    s = get_settings()
    with tempfile.TemporaryDirectory(prefix="pk_") as td:
        wav = Path(td) / "x.wav"
        proc = subprocess.run(
            [s.ffmpeg, "-nostdin", "-v", "error", "-i", str(path),
             "-map", "a:0", "-f", "f32le", "-acodec", "pcm_f32le",
             "-ar", "22050", "-ac", "2", "-"],
            capture_output=True, timeout=3600,
        )
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError(f"ffmpeg failed: {proc.stderr[-300:]!r}")
        arr = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2)
    return arr


def peaks_for_range(peaks: dict, t0: float, t1: float, width: int) -> list[list[float]]:
    """Min/max pairs for a pixel range, picking the right mip level."""
    duration = peaks.get("_duration") or 0.0
    rate = peaks.get("_peak_rate") or PEAK_RATE
    if duration <= 0 or width <= 0:
        return []
    samples_per_px = (t1 - t0) / width
    # choose the coarsest level that still has >= 1 bucket per pixel
    level = 0
    effective_rate = rate
    while level < 9:
        arr = peaks.get(str(level))
        if arr is None:
            break
        if effective_rate / (2 ** level) <= samples_per_px:
            level += 1
        else:
            break
    level = max(0, level - 1)
    arr = peaks[str(level)]
    eff = rate / (2 ** level)
    has_mean = arr.shape[1] >= 3
    out: list[list[float]] = []
    for i in range(width):
        a = t0 + i * samples_per_px
        b = a + samples_per_px
        i0 = max(0, int(a * eff))
        i1 = min(len(arr), max(i0 + 1, int(b * eff)))
        if i1 <= i0:
            out.append([0.0, 0.0, 0.0] if has_mean else [0.0, 0.0])
            continue
        seg = arr[i0:i1]
        row = [float(seg[:, 0].min()), float(seg[:, 1].max())]
        if has_mean:
            row.append(float(seg[:, 2].mean()))
        out.append(row)
    return out


def load_stem_wav(song_id: str, model: str, stem: str) -> Path | None:
    from arrangement.config import get_settings
    from arrangement.models.separation import cache_dir

    safe = stem.replace("/", "_").replace(" ", "_")
    d = cache_dir(get_settings().stems_dir, song_id, model)
    p = d / f"{safe}.wav"
    return p if p.exists() else None


def list_stem_wavs(song_id: str) -> list[dict]:
    from arrangement.config import get_settings
    from arrangement.models.separation import cache_dir

    s = get_settings()
    out = []
    root = s.stems_dir / song_id
    if not root.exists():
        return out
    for model_dir in sorted(root.iterdir()):
        if not model_dir.is_dir():
            continue
        for w in sorted(model_dir.glob("*.wav")):
            try:
                info = sf.info(str(w))
            except Exception:
                continue
            out.append({
                "model": model_dir.name, "stem": w.stem,
                "url": f"/api/stems/{song_id}/{model_dir.name}/{w.stem}.wav",
                "duration": float(info.duration or 0),
                "sample_rate": int(info.samplerate or 0),
            })
    return out
