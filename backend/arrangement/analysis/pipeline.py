"""The analysis pipeline.

Stages run in dependency order, each one cached by content hash so re-running
is cheap.  Every stage is wrapped so a failure degrades the product rather than
breaking it: no separation still yields tempo, loudness, structure and energy.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

from arrangement.audio.io import AudioFile, decode, probe, short_id
from arrangement.config import Settings, get_settings, resolve_device
from arrangement.dsp import energy as E
from arrangement.dsp import rhythm as RH
from arrangement.dsp import spatial as SP
from arrangement.dsp.activity import ActivityConfig, ActivityResult, detect_activity
from arrangement.dsp.features import compute_frame_features
from arrangement.dsp.loudness import analyse_loudness, lufs_1s
from arrangement.dsp.structure import SectionSpan, analyse_structure, assign_repeat_groups
from arrangement.analysis.section_repair import propose_repairs
from arrangement.models.allinone_mlx import MODEL_VERSION, run_model
from arrangement.models.separation import SeparationResult, load_stem_file, run_separation
from arrangement.schema import (
    ActivitySegment, ArrangementEvent, BeatGrid, Curve, CurveSet, EnergyChange,
    EnergyModel, EventKind, HarmonySummary, LabelAlternative, Loudness,
    PatternFinding, PipelineStage, Section, SectionLabel, SectionTransition, Source,
    SourceDescriptor, SourceFamily, SourceSeparationInfo, Span,
    layer_meta,
)

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, float, str], None]

FAMILY_OF_STEM = {
    "vocals": "vocals", "drums": "drums", "bass": "bass",
    "guitar": "harmony", "piano": "harmony", "other": "unknown",
}


class AnalysisCancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_path(settings: Settings, song_id: str, stage: str) -> Path:
    return settings.cache_dir / song_id / f"{stage}.json"


def _load_cache(settings: Settings, song_id: str, stage: str) -> dict | None:
    p = _cache_path(settings, song_id, stage)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _store_cache(settings: Settings, song_id: str, stage: str, data: dict) -> None:
    p = _cache_path(settings, song_id, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, separators=(",", ":")))


def _np_cache(settings: Settings, song_id: str, stage: str) -> Path:
    return settings.cache_dir / song_id / f"{stage}.npz"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_analysis(
    path: str | Path,
    song_id: str | None = None,
    quality: str | None = None,
    progress: ProgressFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
    force: bool = False,
) -> "Any":
    """Analyse one file and return a :class:`SongAnalysis`."""
    from arrangement.schema import SongAnalysis

    t_start = time.time()
    settings = get_settings()
    if quality:
        from arrangement.config import PROFILES

        settings.quality = PROFILES.get(quality, settings.quality)
    path = Path(path)
    info = probe(path)
    song_id = song_id or short_id(str(path.resolve()), info.get("size_bytes", 0))

    def report(stage: str, frac: float, msg: str) -> None:
        if progress:
            progress(stage, max(0.0, min(1.0, frac)), msg)

    def check() -> None:
        if should_cancel and should_cancel():
            raise AnalysisCancelled()

    stages: list[PipelineStage] = []
    warnings: list[str] = []
    device = resolve_device(settings.device)

    def stage(name: str) -> PipelineStage:
        s = PipelineStage(name=name, status="running", started_at=time.time())
        stages.append(s)
        return s

    def finish(s: PipelineStage, status: str = "done", err: str | None = None) -> None:
        s.status = status
        s.finished_at = time.time()
        if s.started_at:
            s.duration = s.finished_at - s.started_at
        if err:
            s.error = err

    # ---------------------------------------------------------------- audio
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("decode")
    report("decode", 0.01, "decoding audio")
    audio = decode(path, sr=settings.quality.sr)
    is_mono = (audio.native_channels or 2) < 2
    duration = audio.duration
    if duration < 3.0:
        raise ValueError(f"file is too short to analyse ({duration:.1f}s)")
    check()
    finish(s)

    title = info.get("tags", {}).get("title") or path.stem
    artist = info.get("tags", {}).get("artist")
    album = info.get("tags", {}).get("album")
    year = info.get("tags", {}).get("date") or info.get("tags", {}).get("year")

    analysis = SongAnalysis(
        id=song_id, source_path=str(path), title=title, artist=artist, album=album,
        year=year, duration=duration, sample_rate=audio.sample_rate,
        channels=audio.channels, is_mono_source=is_mono,
        file_info=info, source_quality={
            "container": info.get("container"), "codec": info.get("codec"),
            "native_sample_rate": info.get("sample_rate"),
            "native_channels": info.get("channels"),
            "bits_per_sample": info.get("bits_per_sample"),
            "lossy": info.get("lossy"),
        },
        quality=settings.quality.name, created_at=time.time(),
        analysis_version="0.2.0", llm_enabled=settings.llm_enabled,
    )
    if info.get("lossy"):
        analysis.warnings.append(
            "Source is a lossy encode; transient and stereo measurements are "
            "affected by codec artefacts and should be read as approximate."
        )

    # ------------------------------------------------------------- features
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("features")
    report("features", 0.04, "computing spectral features")
    feats, _ = compute_frame_features(
        audio.mono(), audio.sample_rate, n_fft=settings.quality.n_fft,
        hop=settings.quality.hop)
    check()
    finish(s)

    # ----------------------------------------------------------- separation
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("separation")
    report("separation", 0.10, "separating stems")

    def sep_progress(frac: float, msg: str) -> None:
        report("separation", 0.10 + 0.34 * max(0.0, min(1.0, frac)), msg)

    try:
        results, sep_warnings = run_separation(
            audio.data, audio.sample_rate, song_id, settings.separator, device,
            settings.stems_dir, settings.allow_download, progress=sep_progress,
            quality=settings.quality.name)
        warnings.extend(sep_warnings)
    except Exception as exc:
        log.warning("separation failed: %s", exc)
        warnings.append(f"separation unavailable: {exc}")
        results = []
    for r in results:
        analysis.separation.append(SourceSeparationInfo(
            model=r.model, stems=r.stem_names, duration_seconds=r.duration,
            device=r.device, sample_rate=r.sample_rate,
            extra={"from_cache": r.from_cache, "notes": r.notes or []},
        ))
    if not results:
        analysis.warnings.append(
            "No stems were produced. Timing and energy analysis still work, but "
            "per-instrument entrance/exit detection is unavailable."
        )
    check()
    finish(s, "skipped" if not results else "done")

    # ----------------------------------------------------------------- beat
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("rhythm")
    report("rhythm", 0.46, "tracking beats, tempo and meter")
    model_result = None
    try:
        model_result = run_model(path, song_id, duration, settings, results, force=force)
    except Exception as exc:
        log.warning("All-In-One MLX unavailable for %s: %s", song_id, exc)
        warnings.append(f"All-In-One MLX failed; used the previous beat and section analysis ({exc})")
    rr = RH.analyse_rhythm(
        audio.mono(), audio.sample_rate, device=device,
        external_beats=np.asarray(model_result.beats) if model_result else None,
        external_downbeats=np.asarray(model_result.downbeats) if model_result else None,
        external_bpm=model_result.bpm if model_result else None,
    )
    check()
    analysis.beats = BeatGrid(
        bpm=rr.bpm, bpm_confidence=rr.bpm_confidence, bpm_curve=rr.bpm_curve,
        beats=[float(b) for b in rr.beats], downbeats=[int(i) for i in range(0)],
        meter=rr.meter, meter_confidence=rr.meter_confidence,
        offset_seconds=rr.offset, beat_grid_confidence=float(np.clip(
            len(rr.beats) / max(1.0, duration * rr.bpm / 60.0), 0, 1)),
        method=rr.method, onset_times=[float(t) for t in rr.onsets],
        onset_strength=[float(v) for v in rr.onset_strength],
        tempo_drift=rr.tempo_drift, is_tangent=rr.is_tangent,
        metrical_confidence=rr.metrical_confidence,
    )
    # downbeats expressed as beat indices for the schema
    db_idx = []
    for t in rr.downbeats:
        if len(rr.beats):
            i = int(np.argmin(np.abs(rr.beats - t)))
            if abs(rr.beats[i] - t) < 0.12:
                db_idx.append(i)
    analysis.beats.downbeats = db_idx
    for n in rr.notes:
        analysis.warnings.append(n)
    finish(s)

    # ------------------------------------------------------------- loudness
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("loudness")
    report("loudness", 0.52, "measuring loudness")
    lo = analyse_loudness(audio.data, audio.sample_rate)
    lufs_curve, lufs_hz = lufs_1s(audio.data, audio.sample_rate)
    analysis.loudness = Loudness(
        integrated_lufs=lo.integrated_lufs, lra=lo.lra,
        true_peak_dbfs=lo.true_peak_dbfs,
        momentary_lufs=lo.momentary_lufs, short_term_lufs=lo.short_term_lufs,
        lra_curve=[float(v) for v in lufs_curve], plr_db=lo.plr_db,
        crest_db=lo.crest_db, method=lo.method,
    )
    for n in lo.notes:
        analysis.warnings.append(n)
    finish(s)

    # -------------------------------------------------------------- spatial
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("spatial")
    report("spatial", 0.56, "analysing the stereo image")
    spatial = SP.analyse_spatial(audio.data, audio.sample_rate, frame_hz=20.0,
                                 is_mono_source=is_mono)
    from arrangement.schema import SpatialCurve

    analysis.spatial = SpatialCurve(
        frame_hz=spatial.frame_hz,
        pan=[f.pan for f in spatial.frames],
        width=[f.width for f in spatial.frames],
        correlation=[f.correlation for f in spatial.frames],
        low_width=[f.low_width for f in spatial.frames],
        mid_width=[f.mid_width for f in spatial.frames],
        high_width=[f.high_width for f in spatial.frames],
        low_pan=[f.low_pan for f in spatial.frames],
        mid_pan=[f.mid_pan for f in spatial.frames],
        high_pan=[f.high_pan for f in spatial.frames],
    )
    for n in spatial.notes:
        analysis.warnings.append(n)
    finish(s)

    # -------------------------------------------------- sources + activity
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("sources")
    report("sources", 0.60, "detecting where each element plays")
    activities: dict[str, ActivityResult] = {}
    source_meta: dict[str, dict] = {}
    stems: dict[str, np.ndarray] = {}

    for res in results:
        for stem_name, data in res.stems.items():
            mono = data.mean(axis=1)
            rms = float(np.sqrt((mono ** 2).mean()) + 1e-12)
            rms_db = 20 * np.log10(rms)
            if rms_db < -70.0:
                continue  # this stem is genuinely absent from the mix
            family = FAMILY_OF_STEM.get(stem_name.lower(), "unknown")
            sid = stem_name.lower()
            if sid in source_meta:
                # keep the loudest version when two models produce a stem
                if rms_db > source_meta[sid].get("rms_db", -999):
                    stems[sid] = data
                else:
                    continue
            else:
                stems[sid] = data
            source_meta[sid] = {"label": layer_meta(sid)["label"], "family": family,
                                "separator_model": res.model, "rms_db": rms_db}
            # The source id as well as the family: guitar and piano are both
            # "harmony" but need different on-margins, because a piano stem on a
            # track with no piano is separator bleed and a lower bar fills a third
            # of the lane with an instrument that is not in the recording.
            cfg = ActivityConfig.for_family(family, sid)
            act = detect_activity(
                data.mean(axis=1), audio.sample_rate, cfg=cfg,
                frame_hz=86.13, onsets=rr.onsets, onset_times=rr.onset_strength,
                beat_times=rr.beats, downbeats=rr.downbeats,
                # The grid is only used to tidy boundaries if the tempo is
                # trusted. See `ActivityConfig.snap_min_confidence`.
                bpm_confidence=rr.bpm_confidence,
            )
            activities[sid] = act
    check()
    finish(s)

    # ------------------------------------------------------------- sections
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("structure")
    report("structure", 0.72, "segmenting formal structure")
    st = analyse_structure(audio.mono(), audio.sample_rate, rr.beats, rr.downbeats,
                           bpm=rr.bpm)
    fallback_sections = tuple(st.sections)
    if model_result:
        # Retain the local similarity features for repeated-section comparisons,
        # but let the trained model own the formal boundaries and names.
        times = np.asarray(st.ssm_times if st.ssm_times is not None else rr.beats)
        st.sections = []
        for section in model_result.sections:
            a = int(np.searchsorted(times, section.start, side="left"))
            b = int(np.searchsorted(times, section.end, side="left"))
            st.sections.append(SectionSpan(
                start=section.start, end=section.end, label=section.label,
                frame_start=a, frame_end=max(a + 1, b),
                novelty=(float(st.novelty[a]) if st.novelty is not None and a < len(st.novelty)
                         else 0.0),
            ))
        st.boundaries = [s.start for s in st.sections] + [duration]
        st.boundary_idx = [s.frame_start for s in st.sections]
        st.method = f"All-In-One MLX {MODEL_VERSION}"
        st.notes = []
    for n in st.notes:
        analysis.warnings.append(n)
    check()
    finish(s)

    # --------------------------------------------------------------- energy
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("energy")
    report("energy", 0.80, "building the energy model")
    from arrangement.dsp.rhythm import density_curve

    onset_density = density_curve(rr.onsets, rr.onset_strength, 0, duration, window=2.0)
    layer_counts, layers_hz = _layer_count_curve(activities, duration)
    energy = E.build_energy(
        frame_hz=20.0, duration=duration,
        loudness_curve=lufs_curve, loudness_hz=lufs_hz,
        onset_density=onset_density, onset_density_hz=20.0,
        flux=feats.flux, flux_hz=feats.frame_hz,
        band_db=feats.band_db, band_names=feats.band_names,
        hp_ratio=feats.hp_ratio, band_hz=feats.frame_hz,
        active_layers=layer_counts, layers_hz=layers_hz,
        width=[f.width for f in spatial.frames] if spatial.frames else None,
        width_hz=spatial.frame_hz,
    )
    analysis.energy = EnergyModel(
        frame_hz=energy.frame_hz,
        raw=Curve(name="raw", frame_hz=energy.frame_hz, values=[float(v) for v in energy.raw],
                  unit="norm", description="measured acoustic intensity"),
        perceived=Curve(name="perceived", frame_hz=energy.frame_hz,
                        values=[float(v) for v in energy.perceived], unit="norm",
                        description="proxy for felt arrangement energy"),
        components={c.name: Curve(name=c.name, frame_hz=energy.frame_hz,
                                  values=[float(v) for v in c.curve], unit=c.unit,
                                  description=c.description)
                    for c in energy.components},
        weights={c.name: c.weight for c in energy.components},
        model_notes=(
            "Balanced energy combines how loud the recording feels, how rhythmically "
            "busy it is, and how full the arrangement is. Their shares are 40%, 30% "
            "and 30% when all inputs are available. The curve shows changes within "
            "this song."
        ),
        norm_lo=energy.norm_lo, norm_hi=energy.norm_hi,
    )
    finish(s)

    # ----------------------------------------------------- assemble sources
    for sid, act in activities.items():
        meta = source_meta[sid]
        lm = layer_meta(sid)
        pan_v, width_v, stability = SP.source_pan(stems[sid], audio.sample_rate)
        segs = []
        for sp in act.spans:
            segs.append(ActivitySegment(
                start=sp.start, end=sp.end, level_db=sp.level_db, peak_db=sp.peak_db,
                confidence=sp.conf, intensity=sp.mean_intensity,
                onset_slope_db_s=sp.attack_db_s, offset_slope_db_s=sp.release_db_s,
                pan=pan_v, width=width_v,
            ))
        desc = _describe_stem(stems[sid], audio.sample_rate, meta, pan_v, width_v)
        total = sum(s.end - s.start for s in act.spans)
        src = Source(
            id=sid, label=lm["label"], family=SourceFamily(lm["family"]),
            color=lm["color"], origin="stem", stem_key=sid,
            separator_model=meta["separator_model"],
            confidence=float(np.clip(0.45 + 0.5 * min(1.0, act.otsu_contrast * 3), 0.2, 0.95)),
            detection_method=f"{meta['separator_model']} + Otsu activity detection",
            descriptor=desc, segments=segs,
            mean_pan=pan_v, mean_width=width_v, pan_stability=stability,
            total_active_seconds=total, duty_cycle=total / max(duration, 1e-6),
            mean_level_db=float(np.mean([s.level_db for s in act.spans])) if segs else -70.0,
            max_level_db=float(np.max([s.peak_db for s in act.spans])) if segs else -70.0,
            n_entries=len(act.spans), n_exits=max(0, len(act.spans) - 1),
        )
        src.active_bars = _bars_for_segments(src.segments, analysis.beats)
        # A separator always returns six files. When the track contains no piano
        # the "piano" stem is not empty — it holds bleed at around -80 dB — and a
        # threshold placed against that bleed finds "activity" in it. The lane
        # then draws a part that is not in the recording.
        #
        # Measured across the library, the p99 of a real source runs from -6 dB
        # (drums) to -24 dB (guitar). The piano stems here peak at -68.9 dB on
        # "DNA." and -41.3 dB on "Taste", and in every case their *median* sits
        # near -80 dB: a few notes in a minute of nothing. So the flag is on the
        # 99th percentile, not the median, and -60 dB sits an octave below the
        # quietest real source measured and well above the bleed floor.
        # From the *unfiltered* level curve, not the span peaks. `sp.peak_db` is
        # the max of a running-median envelope, and a running median raises an
        # isolated dip to its neighbours' level — so a stem that is silence with
        # one quiet blip in it can report a span peak well above its own p99 and
        # pass a test it should fail. The raw curve cannot do that.
        src.is_silent = bool(
            act.level_db is not None and len(act.level_db)
            and float(np.percentile(np.asarray(act.level_db, dtype=float), 99.0))
            < SILENT_STEM_DB
        )
        analysis.sources.append(src)
    analysis.sources.sort(key=lambda s: (s.family.value, s.label))
    finish(s)

    # --------------------------------------------------------------- events
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("events")
    report("events", 0.88, "detecting arrangement events")
    from arrangement.analysis import events as EV

    cands = []
    cands += EV.events_from_activity(activities, source_meta)
    cands += EV.events_from_spatial(spatial, frame_hz=spatial.frame_hz)
    cands += EV.events_from_density(layer_counts, layers_hz, duration)
    cands += EV.events_from_silence(feats.rms_db, feats.frame_hz)
    cands += EV.events_from_transients(rr.onset_strength, rr.onset_hz, feats.rms_db,
                                      feats.frame_hz)
    cands += EV.events_from_rhythm_change(rr.onsets, rr.beats, rr.bpm)
    cands = EV.dedupe_events(cands)

    for i, c in enumerate(cands):
        bar, beat_in_bar = _bar_beat(c.t, analysis.beats)
        analysis.events.append(ArrangementEvent(
            id=f"ev{i:04d}", t=c.t, end=c.end, kind=EventKind(c.kind),
            label=c.label, source_ids=c.source_ids, strength=c.strength,
            confidence=c.confidence, bar=bar, beat_in_bar=beat_in_bar,
            is_continuous=c.kind in EV.CONTINUOUS_KINDS,
            evidence=[_to_evidence(e) for e in c.evidence],
        ))
    finish(s)

    # --------------------------------------------------------- energy change
    for t, delta, mag in E.find_energy_changes(energy):
        contributors, pos, neg = E.attribute_change(energy, t, t + 0.35)
        direction = "rise" if delta > 0 else "fall"
        contrib = [(n, v) for n, v in (pos if delta > 0 else neg)]
        analysis.energy_changes.append(EnergyChange(
            t=t, end=t + 0.4, direction=direction, delta=float(delta), magnitude=mag,
            bar=_bar_beat(t, analysis.beats)[0], contributors=contrib,
            evidence=[{
                "kind": "dsp", "method": "energy:model", "metric": "perceived_energy",
                "t0": round(max(0.0, t - 1.2), 2), "t1": round(t + 1.55, 2),
                "value": round(float(delta), 3), "unit": "norm",
                "strength": "derived",
                "note": (f"perceived energy {direction} by {delta:+.2f}; driven by "
                         + (", ".join(f"{n} ({v:+.2f})" for n, v in pos[:3]) or "no single component")),
            }],
        ))

    # -------------------------------------------------------------- sections
    _build_sections(analysis, st, energy, layer_counts, layers_hz, spatial)
    if model_result and fallback_sections:
        repairs = propose_repairs(analysis.sections, fallback_sections,
                                  analysis.energy_changes)
        if repairs:
            # The model's coarse role is useful, but its middle span conflicts
            # with both repetition and an independently detected drop/return.
            # Keep the earlier half unnamed; retaining "chorus" there would
            # immediately merge the missed boundary away again.
            by_index = dict(repairs)
            times = np.asarray(st.ssm_times if st.ssm_times is not None else rr.beats)
            refined: list[SectionSpan] = []
            for i, span in enumerate(st.sections):
                t = by_index.get(i)
                if t is None:
                    refined.append(span)
                    continue
                frame = int(np.searchsorted(times, t, side="left"))
                refined.append(replace(span, end=t, label="unknown",
                                       frame_end=max(span.frame_start + 1, frame)))
                refined.append(replace(span, start=t, frame_start=frame,
                                       novelty=next((s.novelty for s in fallback_sections
                                                     if abs(s.start - t) < .01), span.novelty)))
                analysis.warnings.append(
                    f"a strong arrangement change near {t:.0f}s splits a model-labelled "
                    "passage; the earlier part is left unnamed because the role "
                    "and repetition evidence disagree")
            st.sections = refined
            st.boundaries = [s.start for s in refined] + [duration]
            st.boundary_idx = [s.frame_start for s in refined]
            analysis.sections.clear()
            _build_sections(analysis, st, energy, layer_counts, layers_hz, spatial)
    _build_transitions(analysis, st)
    finish(s)

    # -------------------------------------------------------------- patterns
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("patterns")
    report("patterns", 0.93, "finding arrangement patterns")
    from arrangement.analysis.patterns import find_patterns

    analysis.patterns = find_patterns(analysis)
    _compute_fingerprint(analysis)
    finish(s)

    # ---------------------------------------------------------------- curves
    # Cancellation is only noticed here, at a stage boundary, so every
    # stage gets a check. Six of the twelve had none, which meant a
    # stop could wait through two stages instead of one for no reason.
    check()
    s = stage("curves")
    report("curves", 0.96, "packaging feature curves")
    _build_curves(analysis, feats, energy, rr, spatial)
    finish(s)

    analysis.stages = stages
    analysis.warnings.extend(warnings)
    # Repeat and section caveats can only be written once grouping has run.
    analysis.warnings.extend(_structure_caveats(analysis, st))
    analysis.warnings.extend(_silent_stem_caveats(analysis))
    if getattr(rr, "tempo_instable", False):
        # Describe what was measured, and what follows from it, rather than
        # implying the tracker failed. A wide local-pulse spread on a track that
        # genuinely changes its metrical feel is a fact about the music: Kendrick
        # Lamar's "DNA." runs a 136 BPM median with triplets pushing the local
        # pulse past 270, and that is the arrangement, not an error.
        analysis.warnings.append(
            f"the local pulse varies widely around the {rr.bpm:.0f} BPM median "
            f"(5-95% range {rr.tempo_drift:.0f} BPM"
            + (", and the beat tracker itself is inconsistent"
               if rr.is_tangent else "")
            + f"): this track{'s' if rr.is_tangent else ''} metrical feel moves, so "
            f"the single tempo and every bar number here are approximations"
        )

    analysis.models_used = {
        "separation": ", ".join(r.model for r in results) or "none",
        "beat": rr.method,
        "structure": st.method,
        "loudness": lo.method,
        "device": device,
    }
    analysis.metrics["analysis_seconds"] = round(time.time() - t_start, 1)
    report("done", 1.0, "analysis complete")
    return analysis


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _layer_count_curve(activities: dict[str, ActivityResult], duration: float,
                       hz: float = 10.0) -> tuple[np.ndarray, float]:
    n = max(2, int(duration * hz) + 1)
    out = np.zeros(n)
    for act in activities.values():
        for sp in act.spans:
            i0 = max(0, int(sp.start * hz))
            i1 = min(n, int(sp.end * hz) + 1)
            if i1 > i0:
                out[i0:i1] += 1
    return out, hz


def _bar_beat(t: float, grid: BeatGrid) -> tuple[int | None, float | None]:
    if not grid.beats or grid.bpm <= 0:
        return None, None
    spb = 60.0 / grid.bpm
    beats_from_zero = (t - grid.offset_seconds) / spb
    return int(np.floor(beats_from_zero / 4.0)), float(beats_from_zero % 4.0)


def _bars_for_segments(segments: list[ActivitySegment], grid: BeatGrid) -> list[int]:
    bars: set[int] = set()
    for seg in segments:
        a, _ = _bar_beat(seg.start, grid)
        b, _ = _bar_beat(seg.end, grid)
        if a is None or b is None:
            continue
        for b_i in range(a, max(a, b) + 1):
            bars.add(b_i)
    return sorted(bars)


def _to_evidence(d: dict):
    from arrangement.schema import Evidence, EvidenceSource, EvidenceStrength

    return Evidence(
        kind=EvidenceSource(d.get("kind", "dsp")),
        method=d.get("method", "?"), metric=d.get("metric"),
        t0=d.get("t0"), t1=d.get("t1"), value=d.get("value"),
        unit=d.get("unit"),
        strength=EvidenceStrength(d.get("strength", "direct")),
        note=d.get("note"),
    )


def _describe_stem(data: np.ndarray, sr: int, meta: dict, pan: float,
                   width: float) -> SourceDescriptor:
    """Measured descriptors for one stem, phrased the way a producer would."""
    from arrangement.dsp.features import hz_to_note

    mono = data.mean(axis=1)
    peak = float(np.max(np.abs(mono)) + 1e-12)
    rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-12)
    hop = max(1, int(round(sr / 86.13)))
    n_fft = 1024
    if len(mono) < n_fft:
        mono2 = np.pad(mono, (0, n_fft - len(mono)))
    else:
        mono2 = mono
    n_frames = 1 + max(0, len(mono2) - n_fft) // hop
    frames = np.lib.stride_tricks.as_strided(
        mono2, shape=(n_frames, n_fft), strides=(mono2.strides[0] * hop, mono2.strides[0]),
        writeable=False) * np.hanning(n_fft + 1)[:-1]
    S = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    tot = S.sum(axis=1) + 1e-12
    centroid = float(np.median((S * freqs[None, :]).sum(axis=1) / tot))
    csum = np.cumsum(S, axis=1)
    roll = float(np.median(freqs[np.argmax(csum >= 0.85 * tot[:, None], axis=1)]))
    gmean = np.exp(np.log(S + 1e-12).mean(axis=1))
    flatness = float(np.median(np.clip(gmean / (S.mean(axis=1) + 1e-12), 0, 1)))

    phrases: list[str] = []
    if centroid < 900:
        phrases.append("dark")
    elif centroid > 3200:
        phrases.append("bright")
    else:
        phrases.append("mid-forward")
    if flatness < 0.02:
        phrases.append("tonal")
    elif flatness > 0.12:
        phrases.append("noisy / percussive")
    if abs(pan) < 0.12:
        phrases.append("centred")
    elif abs(pan) > 0.45:
        phrases.append(f"{'left' if pan < 0 else 'right'} of centre")
    if width > 0.25:
        phrases.append("wide")
    elif width < 0.05:
        phrases.append("mono")
    if 20 * np.log10(peak / rms) > 12:
        phrases.append("transient-heavy")

    role = _infer_role(meta["label"], centroid, flatness, phrases)
    return SourceDescriptor(
        spectral_centroid_hz=centroid, spectral_rolloff85_hz=roll,
        spectral_flatness=flatness,
        rms_db=float(20 * np.log10(rms)),
        crest_factor_db=float(20 * np.log10(peak / rms)),
        mean_pan=pan, mean_width=width,
        role=role, descriptors=phrases,
    )


def _infer_role(label: str, centroid: float, flatness: float,
                phrases: list[str]) -> str:
    l = label.lower()
    if "drum" in l:
        return "rhythmic bed"
    if "bass" in l:
        return "low foundation"
    if "vocal" in l:
        return "foreground"
    if "guitar" in l:
        return "rhythmic / harmonic support"
    if "piano" in l or "key" in l:
        return "harmonic bed"
    if "other" in l:
        return "supporting texture"
    return "unclassified"


def _build_sections(analysis, st, energy, layer_counts, layers_hz, spatial) -> None:
    """Create Section records, then group repeats and name them as groups.

    The order matters.  Repeat grouping needs the *measured* arrangement
    profile of each section (which parts play, how loud, how wide), which only
    exists once the sections have been built.  Naming then has to work on
    groups, not on sections: if the same passage is called a chorus in one
    place and a verse in another, the labels are not describing the music and
    no amount of confidence values will fix that.
    """
    for i, sp in enumerate(st.sections):
        bar_a, _ = _bar_beat(sp.start, analysis.beats)
        bar_b, _ = _bar_beat(sp.end, analysis.beats)
        sw = SP.spatial_window(spatial, sp.start, sp.end)
        mid = (sp.start + sp.end) / 2
        # A source counts as present if it plays for a meaningful part of the
        # section, not merely if it touches the midpoint: a one-bar fill at the
        # top of a section should not define that section's character.
        dur = max(1e-6, sp.end - sp.start)
        active = []
        for src in analysis.sources:
            covered = sum(max(0.0, min(g.end, sp.end) - max(g.start, sp.start))
                          for g in src.segments)
            if covered / dur >= 0.45:
                active.append(src)
        if not active:
            active = [s for s in analysis.sources if s.is_active(mid)]
        i0 = max(0, int(sp.start * energy.frame_hz))
        i1 = max(i0 + 1, int(sp.end * energy.frame_hz))
        analysis.sections.append(Section(
            id=f"sec{i:03d}", start=sp.start, end=sp.end,
            label=(SectionLabel(sp.label) if st.method.startswith("All-In-One MLX")
                   else SectionLabel.UNKNOWN),
            # The port has no per-label confidence. Zero means unavailable,
            # not a claim that the musical name has zero chance of being right.
            label_confidence=0.0,
            label_source=("model" if st.method.startswith("All-In-One MLX") else "heuristic"),
            repeat_group=None, repeat_rank=0,
            frame_start=int(sp.frame_start), frame_end=int(sp.frame_end),
            bar_start=bar_a, bar_end=bar_b,
            energy_raw=float(np.mean(energy.raw[i0:i1])),
            energy_perceived=float(np.mean(energy.perceived[i0:i1])),
            # Not measured, and now honestly so.
            #
            # This was the *record's* integrated loudness, copied into every
            # section, so all twelve sections of "DNA." reported -9.4 LUFS and
            # all ten of "Taste" reported -7.3: one distinct value per song,
            # under a name that promises a per-section measurement. Nothing reads
            # it -- the transport and the inspector both use
            # `song.loudness.integrated_lufs`, which is the real thing -- so the
            # cost of leaving a wrong number here is that any future reader
            # inherits a claim that cannot be true.
            #
            # A section's loudness is not simply the record's loudness over a
            # window either: LUFS is a gated measurement, and a naive mean over
            # 20 seconds is not a LUFS number. Reporting None says "not
            # measured", which is true and cannot mislead. If it is ever wanted,
            # it has to be integrated properly, not averaged.
            loudness_lufs=None,
            source_count=len(active), source_ids=[s.id for s in active],
            stereo_width=float(sw.get("mean_width", 0.0)),
            stereo_correlation=float(sw.get("mean_correlation", 1.0)),
            density=len(active) / max(1, len(analysis.sources)),
            dominant_source=(max(active, key=lambda s: s.mean_level_db).id
                             if active else None),
            novelty=sp.novelty,
        ))

    if len(analysis.sections) >= 2:
        try:
            assign_repeat_groups(analysis.sections, st.raw_ssm, st.boundary_idx,
                                 st.features, S_chroma=st.chroma_ssm)
        except Exception as exc:  # grouping is an enhancement, not a hard step
            log.info("repeat grouping failed: %s", exc)
            for sec in analysis.sections:
                sec.repeat_group = sec.id
                sec.repeat_rank = 0
    else:
        for sec in analysis.sections:
            sec.repeat_group = sec.id
            sec.repeat_rank = 0

    if st.method.startswith("All-In-One MLX"):
        # Similarity grouping should never imply that unlike named passages
        # have the same musical role. Keep comparisons within the model's label.
        group_ids: dict[tuple[int | None, SectionLabel], int] = {}
        seen: dict[int, int] = {}
        for sec in analysis.sections:
            key = (sec.repeat_group, sec.label)
            sec.repeat_group = group_ids.setdefault(key, len(group_ids))
            sec.repeat_rank = seen.get(sec.repeat_group, 0)
            seen[sec.repeat_group] = sec.repeat_rank + 1
    else:
        label_sections_by_group(analysis)
    merge_macro_sections(analysis)
    for i, sec in enumerate(analysis.sections):
        prev = analysis.sections[i - 1] if i else None
        sec.deltas_from_previous = _section_delta(prev, sec) if prev else []


#: A separated stem whose loudest detected moment is below this carries no
#: instrument. Measured: real sources peak between -6 and -24 dB; the piano stems
#: on this library peak at -68.9 and -41.3 dB with a median near -80 dB. An octave
#: of headroom below the quietest real source, and far above the bleed floor.
SILENT_STEM_DB = -60.0


def _silent_stem_caveats(analysis) -> list[str]:
    """Name the lanes that are separation bleed rather than instruments.

    A separator emits a fixed number of files whether or not the track contains
    that instrument, and the empty ones are not empty — they carry bleed. Left
    unremarked, the interface draws a lane for an instrument that is not in the
    recording, and a producer reading the arrangement map counts it.
    """
    silent = [s for s in analysis.sources if getattr(s, "is_silent", False)]
    if not silent:
        return []
    names = ", ".join(s.label for s in silent)
    return [
        f"no audible {names} was found in this track. The separator still returns a "
        f"file for every instrument, and an empty one holds only bleed, so "
        f"{'that lane is' if len(silent) == 1 else 'those lanes are'} not evidence "
        f"of an instrument and should be read as absent"
    ]


