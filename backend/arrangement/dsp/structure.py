"""Local structure features and the fallback section detector.

All-In-One MLX supplies primary section candidates on supported Macs. This
local detector remains the fallback and supplies similarity features for
repeated-section comparisons. It separates two questions:

1. **Where are the boundaries?**  Self-similarity + novelty.  This is objective
   and we can be confident about it.
2. **What is each section?** In fallback mode, names are derived from measured
   energy, density, sources, and repetition. In primary mode, the trained model
   supplies provisional names. Neither path makes a listener's interpretation
   unnecessary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SectionSpan:
    start: float
    end: float
    label: str = "unknown"
    confidence: float = 0.0
    cluster: int = -1
    novelty: float = 0.0
    # similarity to each other section of the same cluster
    repeat_rank: int = 0
    repeat_group: int = -1
    frame_start: int = 0
    frame_end: int = 0
    #: filled in by the analysis layer before repeat grouping, so the grouping
    #: can use the *arrangement* profile and not only the timbre similarity
    source_ids: list[str] = field(default_factory=list)
    energy_perceived: float = 0.0
    stereo_width: float = 0.0


@dataclass
class StructureResult:
    sections: list[SectionSpan]
    boundaries: list[float]
    ssm: np.ndarray | None = None       # beat-synchronous self-similarity
    ssm_times: np.ndarray | None = None
    ssm_freqs: np.ndarray | None = None
    novelty: np.ndarray | None = None
    novelty_times: np.ndarray | None = None
    cluster_labels: list[int] = field(default_factory=list)
    method: str = "chroma+mfcc SSM + Foote novelty + agglomerative"
    notes: list[str] = field(default_factory=list)
    #: raw (unsmoothed) similarity and features, kept so the analysis layer can
    #: group repeats using the arrangement profile as well as timbre
    raw_ssm: np.ndarray | None = None
    features: np.ndarray | None = None
    boundary_idx: list[int] = field(default_factory=list)
    chroma_ssm: np.ndarray | None = None


# ---------------------------------------------------------------------------
# Features for similarity
# ---------------------------------------------------------------------------


def structure_features(
    y: np.ndarray, sr: int, beats: np.ndarray, n_fft: int = 2048, hop: int = 1024
) -> tuple[np.ndarray, np.ndarray]:
    """Beat-synchronous features.

    Returns ``(X, C)``: the combined, L2-normalised per-frame block used for
    the display self-similarity, and the chroma-only block used for repetition
    grouping.

    Chroma captures *what* the harmony is doing, MFCC captures *how it sounds*,
    and the band profile captures arrangement density.  All three together
    separate "same tune, different instrumentation" from "different music",
    which a single feature cannot.
    """
    import librosa

    hop = min(hop, 512)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop, bins_per_octave=24)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, hop_length=hop)
    contrast = librosa.feature.spectral_contrast(
        y=y, sr=sr, n_fft=n_fft, hop_length=hop)
    # 6 broad mel bands: a compact stand-in for "how full is the arrangement"
    mel = librosa.feature.melspectrogram(
        y=y, sr=sr, n_fft=n_fft, hop_length=hop, n_mels=6, fmin=30,
        fmax=min(14000, sr / 2), power=2.0)
    bands = np.log1p(mel)

    def beat_sync(F: np.ndarray) -> np.ndarray:
        """Average frames between consecutive beats -> (n_beats, n_features).

        Every row of the output is written.  (An earlier version sized the array
        with np.empty but wrote one row fewer than it allocated, so one row was
        uninitialised memory — which made the whole analysis silently
        non-reproducible and occasionally produced NaN features.)
        """
        if len(beats) < 2:
            return F.T
        F = np.asarray(F)
        n_feat, n_frames = F.shape
        idx = np.clip((np.asarray(beats, dtype=np.float64) * sr / hop).astype(int),
                      0, max(0, n_frames - 1))
        n_rows = len(idx) + 1
        out = np.zeros((n_rows, n_feat), dtype=np.float32)
        for i in range(len(idx)):
            a = 0 if i == 0 else int(idx[i - 1])
            b = int(idx[i])
            out[i] = F[:, a:b].mean(axis=1) if b > a else F[:, a]
        tail = int(idx[-1])
        out[-1] = F[:, tail:].mean(axis=1) if n_frames > tail else F[:, tail]
        # Defensive: a NaN here silently destroys every downstream stage.
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    parts = []
    for F in (chroma, mfcc, contrast, bands):
        Fs = beat_sync(F)
        if Fs.shape[0] < 2:
            Fs = F.T
        parts.append(Fs.astype(np.float32))

    n = min(p.shape[0] for p in parts)
    X = np.hstack([p[:n] for p in parts])
    X = np.nan_to_num(X.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    # Scale each view so no single feature type dominates the distance.
    lo = 0
    for p in parts:
        hi = lo + p.shape[1]
        X[:, lo:hi] = _zscore_rows(X[:, lo:hi])
        lo = hi
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    X = X / np.maximum(norms, 1e-9)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # The chroma block is kept separately because repetition is fundamentally a
    # harmonic claim. Two passages with a similar guitar-and-bass *texture* can
    # be completely different music; two passages that share a chord progression
    # usually are the same passage.
    C = beat_sync(chroma).astype(np.float32)
    C = np.nan_to_num(C, nan=0.0, posinf=0.0, neginf=0.0)
    Cn = C / np.maximum(np.linalg.norm(C, axis=1, keepdims=True), 1e-9)
    return X, Cn


def _zscore_rows(A: np.ndarray) -> np.ndarray:
    mu = A.mean(axis=0, keepdims=True)
    sd = A.std(axis=0, keepdims=True)
    return (A - mu) / np.maximum(sd, 1e-6)


# ---------------------------------------------------------------------------
# Self-similarity
# ---------------------------------------------------------------------------


def self_similarity(X: np.ndarray, k: int = 64) -> np.ndarray:
    """Slim (checkerboard-enhanced) self-similarity matrix.

    The checkerboard kernel is zero-sum, so it must be normalised by the number
    of *elements*, not by its sum — dividing by the sum divides by zero and
    produces a matrix of astronomical values whose peaks are meaningless.
    """
    n = len(X)
    if n < 4:
        return np.ones((n, n), dtype=np.float32)
    S = X @ X.T
    S = ((S + S.T) / 2.0).astype(np.float32)
    np.fill_diagonal(S, 1.0)
    k = max(4, min(k, n // 2))
    S = _convolve2d(S, _checkerboard(k))
    return np.clip(S, -1.0, 1.0)


def _center_similarity(M: np.ndarray) -> np.ndarray:
    """Subtract the off-diagonal mean so 0 means 'as similar as typical'."""
    n = M.shape[0]
    if n < 3:
        return M
    mask = ~np.eye(n, dtype=bool)
    return (M - float(M[mask].mean())).astype(np.float32)


def _checkerboard(k: int) -> np.ndarray:
    k = max(2, int(k))
    if k % 2:
        k += 1
    ck = np.zeros((k, k), dtype=np.float32)
    h = k // 2
    ck[:h, :h] = 1.0
    ck[h:, h:] = 1.0
    ck[:h, h:] = -1.0
    ck[h:, :h] = -1.0
    return ck


def _convolve2d(A: np.ndarray, K: np.ndarray) -> np.ndarray:
    from scipy.signal import fftconvolve

    full = fftconvolve(A.astype(np.float32), K[::-1, ::-1], mode="same")
    return full / float(K.size)  # element count: K.sum() is exactly 0


def foote_novelty(S_raw: np.ndarray, half_window: int = 8) -> np.ndarray:
    """Boundary novelty from checkerboard energy (Foote 2000).

    ``half_window`` is in frames, which here means beats.  Eight beats either
    side is the usual choice: wide enough that a real structural change beats an
    ordinary arrangement tweak, narrow enough to localise it in time.
    """
    n = S_raw.shape[0]
    nov = np.zeros(n, dtype=np.float64)
    if n < 2 * half_window + 2:
        return nov
    ck = _checkerboard(2 * half_window)
    h = half_window
    for i in range(h, n - h):
        block = S_raw[i - h: i + h, i - h: i + h]
        nov[i] = float((block * ck).mean())
    lo, hi = float(nov.min()), float(nov.max())
    if hi - lo > 1e-9:
        nov = (nov - lo) / (hi - lo)
    return nov


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------


def pick_peaks(novelty: np.ndarray, min_distance: int = 8, threshold_pct: float = 0.25,
               max_boundaries: int = 64) -> list[int]:
    """Prominence-filtered peaks.  Plain argmax-style picking over-segments."""
    if len(novelty) < 4:
        return []
    from scipy.signal import find_peaks

    hi = float(np.max(novelty))
    if hi <= 0:
        return []
    peaks, props = find_peaks(novelty, height=hi * threshold_pct,
                              prominence=hi * 0.12, distance=max(1, min_distance))
    if len(peaks) == 0:
        return []
    if len(peaks) > max_boundaries:
        heights = props["peak_heights"]
        keep = np.argsort(heights)[::-1][:max_boundaries]
        peaks = np.sort(peaks[keep])
    return [int(p) for p in peaks]


def prune_boundaries(peaks: list[int], strength: np.ndarray, min_gap_frames: int,
                     min_gap: int) -> list[int]:
    """Drop weak peaks that sit too close to a stronger one.

    Without this, novelty peaks come in clusters around a single real boundary
    and the song fragments.  We keep the strongest peak in each window.
    """
    if not peaks:
        return []
    order = sorted(peaks, key=lambda i: -strength[i])
    kept: list[int] = []
    for p in order:
        if all(abs(p - q) >= min_gap_frames for q in kept):
            kept.append(p)
    return sorted(kept)


def cluster_sections(
    X: np.ndarray, boundaries_idx: list[int], n_clusters: int | None = None
) -> np.ndarray:
    """Agglomerative clustering of sections by their mean feature vector.

    Averages the section (not its boundary) so a long chorus is not split by a
    transient and so two sections are compared as wholes.
    """
    edges = [0] + list(boundaries_idx) + [len(X)]
    secs = []
    for a, b in zip(edges, edges[1:]):
        if b > a:
            secs.append(X[a:b].mean(axis=0))
    if not secs:
        return np.zeros(1, dtype=int)
    S = np.stack(secs)
    n = len(S)
    k = n_clusters
    if k is None:
        k = int(np.clip(round(np.sqrt(n / 2.0)), 2, min(8, n)))
    k = int(np.clip(k, 1, n))
    if k == 1 or n < 3:
        return np.zeros(n, dtype=int)

    D = 1.0 - (S @ S.T) / (np.linalg.norm(S, axis=1, keepdims=True) *
                           np.linalg.norm(S, axis=1)[None, :] + 1e-9)
    D = (D + D.T) / 2.0
    np.fill_diagonal(D, 0.0)
    try:
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import squareform

        Z = linkage(squareform(D, checks=False), method="average")
        labels = fcluster(Z, t=k, criterion="maxclust")
    except Exception:
        labels = _kmeans_labels(S, k)
    return np.asarray(labels, dtype=int)


def _kmeans_labels(X: np.ndarray, k: int, iters: int = 60, seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    n = len(X)
    k = int(np.clip(k, 1, n))
    if k == 1:
        return np.zeros(n, dtype=int)
    # k-means++ seeding
    centers = [X[rng.randint(n)]]
    for _ in range(k - 1):
        d = np.min(((X[:, None, :] - np.stack(centers)[None]) ** 2).sum(axis=2), axis=1)
        probs = d / max(d.sum(), 1e-12)
        centers.append(X[rng.choice(n, p=probs)])
    C = np.stack(centers)
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        d = ((X[:, None, :] - C[None]) ** 2).sum(axis=2)
        new = np.argmin(d, axis=1)
        if np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            m = labels == j
            if m.any():
                C[j] = X[m].mean(axis=0)
    return labels


def analyse_structure(
    y: np.ndarray, sr: int, beats: np.ndarray, downbeats: np.ndarray,
    max_sections: int = 20, min_section_bars: float = 4.0, bpm: float = 0.0,
) -> StructureResult:
    """Beat-synchronous structure analysis.

    Sections are deliberately **coarse**: this is formal structure, and the fine
    arrangement changes inside a section are reported as events instead.  A
    minimum section length in bars enforces that separation — without it,
    novelty peaks cluster around every one-bar arrangement tweak and the song
    shreds into 29 meaningless "sections".
    """
    notes: list[str] = []
    if len(beats) < 8:
        return StructureResult(sections=[SectionSpan(0.0, len(y) / sr)],
                               boundaries=[], method="none",
                               notes=["too few beats for structure analysis"])

    X, C = structure_features(y, sr, beats)
    if len(X) < 8:
        return StructureResult(sections=[SectionSpan(0.0, len(y) / sr)], boundaries=[],
                               method="degenerate", notes=["feature matrix too small"])

    S_raw = (X @ X.T).astype(np.float32)
    S_raw = (S_raw + S_raw.T) / 2.0
    np.fill_diagonal(S_raw, 1.0)
    S_chroma = (C @ C.T).astype(np.float32)
    S_chroma = (S_chroma + S_chroma.T) / 2.0
    np.fill_diagonal(S_chroma, 1.0)
    # Centre both similarity matrices on their own off-diagonal mean.
    #
    # This matters more than it looks. L2-normalised chroma vectors are
    # non-negative, so *every* pair of sections has a cosine similarity around
    # 0.9 whether or not they share a single chord; the raw block mean was
    # saturated into a 0.88-0.93 band and carried no information at all.
    # Subtracting the typical value restores the resolution that decides whether
    # a pair is actually unusual, which is the only question repetition asks.
    S_chroma = _center_similarity(S_chroma)
    S_raw = _center_similarity(S_raw)
    nov = foote_novelty(S_raw, half_window=8)
    S = self_similarity(X, k=min(64, max(4, len(X) // 4)))

    times = beats[: len(nov)] if len(beats) >= len(nov) else np.linspace(
        0, len(y) / sr, len(nov))
    beat_dur = float(np.median(np.diff(beats))) if len(beats) > 2 else 0.5
    min_section_s = max(3.0, min_section_bars * beat_dur * 4.0)
    min_gap_frames = max(4, int(min_section_s / max(beat_dur, 1e-6)))

    peaks = pick_peaks(nov, min_distance=min_gap_frames, threshold_pct=0.32,
                       max_boundaries=max_sections)
    peaks = prune_boundaries(peaks, nov, min_gap_frames, min_gap_frames)

    boundary_times = [float(times[i]) for i in peaks]
    duration = len(y) / sr
    boundary_times = sorted(set([0.0, duration] + boundary_times))

    bidx = sorted({int(np.argmin(np.abs(times - t))) for t in boundary_times})
    labels = cluster_sections(X, bidx)

    starts = bidx
    ends = bidx[1:] + [len(X)]
    sections: list[SectionSpan] = []
    for i, (a, b) in enumerate(zip(starts, ends)):
        t0 = float(times[min(a, len(times) - 1)])
        t1 = float(times[min(b, len(times) - 1)]) if b < len(times) else duration
        if t1 <= t0:
            continue
        if t1 - t0 < 0.4:
            continue
        cl = int(labels[i]) if i < len(labels) else -1
        novv = float(nov[a]) if a < len(nov) else 0.0
        sections.append(SectionSpan(start=t0, end=t1, cluster=cl, novelty=novv,
                                    frame_start=int(a), frame_end=int(b)))

    if sections:
        sections[-1].end = duration
    if not sections:
        sections = [SectionSpan(0.0, duration)]

    if len(sections) > 18:
        notes.append(
            f"{len(sections)} candidate sections is unusually many for this track; "
            f"some are probably arrangement changes rather than formal structure"
        )
    # Deliberately no "no two sections were recognised as repeats" note here.
    # Repeat grouping happens in the pipeline, once sections have their measured
    # arrangement profile, so at this point every span still carries the default
    # group id. The check used to live here and therefore always fired: "DNA."
    # was told it had no repeats while its own section list showed a group of
    # three and a group of four. A caveat that contradicts the data beside it is
    # worse than no caveat — it teaches the user to distrust the whole panel.
    # See `_structure_caveats` in the pipeline.

    return StructureResult(
        sections=sections, boundaries=boundary_times, ssm=S, ssm_times=times,
        novelty=nov, novelty_times=times, cluster_labels=[s.cluster for s in sections],
        method="chroma+mfcc+contrast SSM, Foote novelty, agglomerative", notes=notes,
        raw_ssm=S_raw, features=X, boundary_idx=bidx, chroma_ssm=S_chroma)


def assign_repeat_groups(
    sections: list[SectionSpan], S_raw: np.ndarray, bidx: list[int],
    X: np.ndarray | None = None, bpm: float = 0.0,
    level_curve: np.ndarray | None = None, level_hz: float = 20.0,
    S_chroma: np.ndarray | None = None,
) -> None:
    """Group sections that are recognisably the same music.

    Three complementary cues, because no single one is reliable:

    * **SSM block similarity** — does the *path* through harmony and timbre
      line up? This is what makes two choruses the same and not merely
      similar. Mean feature vectors are useless here: for most pop sections
      they collapse to a near-constant and every pair looks alike.
    * **Source-presence overlap** — do the same parts play? Two choruses with
      different instrumentation are still the same chorus, and a verse and a
      chorus rarely share a part set. This cue alone finds most repeats in
      practice, and it is immune to the key/tuning drift that wrecks timbre
      matching.
    * **Level similarity** — repeats sit at a similar perceived energy.

    Earlier versions used only the SSM block with a fixed threshold, which
    produced almost entirely singleton groups on ordinary pop material; every
    section then had to be labelled on its own and the labels contradicted one
    another (the same passage called a chorus in one place and a verse in
    another).
    """
    if not sections:
        return
    n = len(sections)
    spans = [(s.frame_start, max(s.frame_start + 1, s.frame_end)) for s in sections]
    nfr = S_raw.shape[0] if S_raw is not None and S_raw.ndim == 2 else 0

    # ---- mean timbre vector per section --------------------------------
    vecs: list[np.ndarray] = []
    if X is not None:
        for a0, a1 in spans:
            a0, a1 = max(0, a0), min(len(X), a1)
            vecs.append(X[a0:a1].mean(axis=0) if a1 > a0 else np.zeros(X.shape[1]))
    if vecs:
        V = np.stack(vecs)
        Vn = V / np.maximum(np.linalg.norm(V, axis=1, keepdims=True), 1e-9)
    else:
        Vn = None

    # ---- measured arrangement profile per section ---------------------
    src_sets: list[set[str]] = [set(getattr(s, "source_ids", []) or []) for s in sections]
    energies = np.array([getattr(s, "energy_perceived", 0.0) for s in sections])
    widths = np.array([getattr(s, "stereo_width", 0.0) for s in sections])
    counts = np.array([len(x) for x in src_sets], dtype=float)
    e_lo, e_hi = float(energies.min()), float(energies.max())
    erange = max(1e-6, e_hi - e_lo)
    counts = (counts - counts.min()) / max(1e-6, float(np.ptp(counts)) or 1.0)

    def block_mean(M, i: int, j: int):
        if M is None or getattr(M, "ndim", 0) != 2 or not nfr:
            return None
        a0, a1 = max(0, spans[i][0]), min(M.shape[0], spans[i][1])
        b0, b1 = max(0, spans[j][0]), min(M.shape[1], spans[j][1])
        w = min(a1 - a0, b1 - b0)
        if w < 2:
            return None
        return float(np.clip(M[a0:a0 + w, b0:b0 + w].mean(), -1.0, 1.0))

    S = np.eye(n, dtype=np.float32)
    for i in range(n):
        for j in range(i + 1, n):
            timbre = block_mean(S_raw, i, j)
            if timbre is None:
                timbre = float(Vn[i] @ Vn[j]) if Vn is not None else 0.0
            harm = block_mean(S_chroma, i, j)
            if harm is None:
                harm = timbre  # no chroma available: fall back, never invent
            # Both matrices are centred, so 0 is "as similar as an unrelated
            # pair" and the value carries the full discrimination in [-1, 1].
            harm_n = max(0.0, min(1.0, (harm + 0.5) / 1.0))
            timbre_n = max(0.0, min(1.0, (timbre + 0.5) / 1.0))
            si, sj = src_sets[i], src_sets[j]
            union = si | sj
            jac = len(si & sj) / len(union) if union else 1.0
            d_energy = abs(energies[i] - energies[j]) / erange
            d_width = abs(widths[i] - widths[j])
            # How many parts each passage has, not just which ones.
            #
            # A chorus is nearly always its verse plus a layer or two, so the
            # source *set* barely differs — the Jaccard lands around 0.75 and
            # carries almost no information, while the counts differ every time.
            # On "Taste" the choruses play six sources and the verses three; on
            # "DNA." five against three. That is the discriminator the set
            # similarity was missing, and without it verse and chorus land in one
            # group and then share a name.
            d_count = abs(counts[i] - counts[j])
            # Harmony dominates, because "is this the same passage" is mostly a
            # question about which notes are being played. Texture alone merged
            # a mid-song passage into the intro (both are guitar-and-bass rock);
            # source presence alone merged a verse into a chorus that shares
            # three of four parts. Level and width only nudge.
            score = (0.36 * harm_n
                     + 0.20 * timbre_n
                     + 0.18 * jac
                     + 0.09 * (1.0 - min(1.0, d_energy))
                     + 0.03 * (1.0 - min(1.0, d_width * 2.5))
                     - 0.20 * min(1.0, d_count / 2.0))

            # Level gate. A passage far quieter than its candidate twin is not
            # the same passage, whatever the chords and instruments say: a
            # final chorus that fades out is not an outro, and an outro tail is
            # not a fifth chorus. Left to the weighted sum this merged them,
            # because level only carried 0.10 and everything else was high.
            #
            # The threshold is absolute rather than relative to the song's own
            # spread, because on a record whose sections all sit within 0.35 of
            # each other, a relative threshold makes every pair look different
            # and breaks the verse/chorus grouping it was meant to protect.
            if abs(energies[i] - energies[j]) > 0.16:
                score *= 0.35
            S[i, j] = S[j, i] = float(score)

    # ---- centroid assignment at a data-driven threshold ----------------
    #
    # Single-link chains anything where consecutive pairs clear the bar, which
    # split a chorus that the boundary detector had cut in half: section A
    # matched the first repeat, section B matched the second, and the two never
    # compared directly. Assigning each section to the *centroid* of an existing
    # group instead lets both halves of a repeated passage land in the same
    # group, and keeps a genuinely different passage out.
    thresh = _group_threshold(S)
    assigned = [-1] * n
    groups: dict[int, list[int]] = {}
    for i in range(n):
        best_g, best_score = None, -1.0
        for g, members in groups.items():
            if not members:
                continue
            score = float(np.mean([S[i, m] for m in members]))
            if score > best_score:
                best_g, best_score = g, score
        if best_g is not None and best_score >= thresh:
            assigned[i] = best_g
            groups[best_g].append(i)
        else:
            g = len(groups)
            groups[g] = [i]
            assigned[i] = g
    for i, sec in enumerate(sections):
        sec.repeat_group = assigned[i]
    seen: dict[int, int] = {}
    for sec in sections:
        r = seen.get(sec.repeat_group, 0)
        sec.repeat_rank = r
        seen[sec.repeat_group] = r + 1
    del bpm, level_curve, level_hz


def _group_threshold(S: np.ndarray) -> float:
    """Pick a similarity cut from the off-diagonal distribution.

    Using the data rather than a constant matters: a dense funk mix and a sparse
    ballad have very different similarity distributions, and a fixed threshold
    collapses one into all-singletons and the other into a single group.
    """
    n = S.shape[0]
    off = S[~np.eye(n, dtype=bool)] if n > 1 else np.array([0.0])
    if off.size == 0:
        return 0.75
    hi = float(np.percentile(off, 97))
    lo = float(np.percentile(off, 40))
    # With centred similarity matrices the off-diagonal distribution is
    # meaningful, so the adaptive part does the work. The floor is a precision
    # choice, not a recall one, and the trade is deliberate.
    #
    # Sweeping the floor over real songs shows the asymmetry. On blink-182's
    # "All the Small Things": 0.68 gives {intro}, {verse}, {chorus}, {chorus},
    # ... with the two halves of each chorus left unlinked. 0.60 links all five
    # chorus sections correctly *and* pulls one chorus in with a verse, so that
    # chorus ends up named "verse". A missed link is harmless — the two groups
    # are independently within reach of the loudest vocal group, so both get
    # named "chorus" — while a wrong merge mislabels a section. Precision wins.
    #
    # The cost is that loose-form material groups rarely at all: J. Cole's
    # "Apparently" and Les Baxter's "Ecstasy" come back all-singletons at 0.68
    # where 0.64 finds three plausible repeat pairs. Naming then works
    # per-section, which is still consistent, just less connected.
    import os as _os

    # The similarity weights sum to 0.86, not 1.0 — the level and width terms are
    # discounts, not contributions. The floor therefore has to sit proportionally
    # lower than the old 0.68, or a pair of genuine repeats can no longer reach
    # it: "All the Small Things" lost its verse grouping entirely at 0.68 with
    # the new weights, which is a silent regression in the opposite direction.
    floor = float(_os.environ.get("ARRANGEMENT_GROUP_FLOOR", "0.58"))
    return float(np.clip(max(floor, lo + 0.5 * (hi - lo)), floor, 0.86))
