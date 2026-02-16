"""FastAPI application.

Routes are grouped by concern: library, analysis, audio, curves, reasoning.
Long analyses run on a worker thread with progress streamed over SSE, because
separation alone can take minutes and a silent HTTP request is not acceptable.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from arrangement.config import get_settings, hardware_report
from arrangement.analysis.section_edit import merge_macro_sections, move_macro_boundary, split_macro_section
from arrangement.llm import interpret
from arrangement.llm.client import LLMError, client_from_settings
from arrangement.llm import cache as llm_cache
from arrangement.llm.ground import summarise_grounding
from arrangement.models.registry import describe as describe_models
from arrangement.schema import SongAnalysis, Section, SectionTransition
from arrangement.server import peaks as P
from arrangement.server.store import (
    JOBS, SongNotFound, analysis_path, audible_source_count, curve_names,
    curves_path, delete_analysis, list_songs, load_analysis, load_summary,
    macro_section_count, read_curve, save_analysis,
)

log = logging.getLogger(__name__)

app = FastAPI(title="Arrangement Analyzer", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


# ---------------------------------------------------------------------------
# health / system
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    s = get_settings()
    return {
        "ok": True,
        "llm_enabled": s.llm_enabled,
        "llm_model": s.deepseek_model if s.llm_enabled else None,
        # Whether a repeat of a question costs money. Reported rather than
        # assumed: the difference between "asking again" and "asking again for
        # free" is a bill, and a bill you cannot see is the kind of thing that
        # has to be discovered rather than managed.
        "llm_cache": llm_cache.stats(),
        "quality": s.quality.name,
        "separator": s.separator,
        "data_dir": str(s.data_dir),
        "hardware": hardware_report(),
    }


@app.get("/api/models")
def models() -> dict[str, Any]:
    return {"models": describe_models()}


@app.get("/api/songs")
def songs() -> dict[str, Any]:
    return {"songs": list_songs()}


@app.get("/api/songs/{song_id}")
def song(song_id: str) -> dict[str, Any]:
    try:
        a = load_analysis(song_id)
    except SongNotFound:
        raise HTTPException(404, f"no analysis for '{song_id}'")
    return summary_payload(a)


def summary_payload(a: SongAnalysis) -> dict[str, Any]:
    d = a.model_dump(mode="json")
    d.pop("curves", None)
    d["energy"]["raw"] = a.energy.raw.model_dump(mode="json", exclude={"values"}) \
        if a.energy.raw else None
    d["energy"]["perceived"] = a.energy.perceived.model_dump(mode="json", exclude={"values"}) \
        if a.energy.perceived else None
    d["energy"]["components"] = {
        k: v.model_dump(mode="json", exclude={"values"})
        for k, v in (a.energy.components or {}).items()}
    d.pop("spatial", None)
    d["spatial_summary"] = _spatial_summary(a)
    d["beat_count"] = len(a.beats.beats)
    # Shared with the library list, so the sidebar and the header cannot disagree.
    d["audible_sources"] = audible_source_count(d)
    d["macro_sections"] = macro_section_count(d)
    d["section_groups"] = sorted({
        s.repeat_group for s in a.sections if s.repeat_group is not None
    })
    d["onset_count"] = len(a.beats.onset_times)
    d["curve_names"] = curve_names(a.id)
    d["stem_audio"] = P.list_stem_wavs(a.id)
    d["grounding"] = {
        "insights": [summarise_grounding(i.claims) for i in a.insights],
    }
    d["grid"] = {
        "bpm": a.beats.bpm, "bpm_confidence": a.beats.bpm_confidence,
        "meter": a.beats.meter, "meter_confidence": a.beats.meter_confidence,
        "offset": a.beats.offset_seconds, "tempo_drift": a.beats.tempo_drift,
        "is_tangent": a.beats.is_tangent,
        "beats": a.beats.beats,
        "downbeats": a.beats.downbeats,
        "onsets": a.beats.onset_times,
        "bpm_curve": a.beats.bpm_curve,
    }
    return d


def _mean_of(a: SongAnalysis, name: str) -> float | None:
    c = a.curves.get(name)
    if c and c.values:
        return round(sum(c.values) / len(c.values), 4)
    return None


def _spatial_summary(a: SongAnalysis) -> dict:
    """Spatial stats from the stored per-frame curve.

    Read from ``a.spatial`` rather than from ``a.curves``: curves are stripped
    from the saved document and live in the companion npz, so the curves view is
    empty for a song loaded back from disk.
    """
    from arrangement.dsp.spatial import SPATIAL_BANDS

    sp = a.spatial
    # The band edges travel with the numbers. The width row is drawn per band,
    # and a frontend that hard-coded "20-250" next to a curve the server
    # filtered at 20-250 would be one edit away from labelling the wrong band —
    # the same class of bug as two components computing the same grouping, which
    # is how they drift.
    bands = [
        {"key": b.field, "label": b.label, "lo_hz": b.lo, "hi_hz": b.hi,
         "curve": b.curve}
        for b in SPATIAL_BANDS
    ]
    if a.is_mono_source or not sp.pan:
        return {"is_mono": True, "mean_pan": None, "mean_width": None,
                "mean_correlation": None, "low_width": None, "mid_width": None,
                "high_width": None, "bands": bands}
    def mean(v: list[float]) -> float | None:
        return round(sum(v) / len(v), 4) if v else None
    return {
        "is_mono": False,
        "mean_pan": mean(sp.pan),
        "mean_width": mean(sp.width),
        "mean_correlation": mean(sp.correlation),
        "low_width": mean(sp.low_width),
        # The middle band was measured and stored all along and simply never
        # reported. With the row drawn per band, omitting it would have shown
        # two of the three curves that decide the answer.
        "mid_width": mean(sp.mid_width),
        "high_width": mean(sp.high_width),
        "bands": bands,
    }


@app.delete("/api/songs/{song_id}")
def remove_song(song_id: str) -> dict[str, Any]:
    return {"deleted": delete_analysis(song_id)}


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


@app.get("/api/songs/{song_id}/audio")
def song_audio(song_id: str, original: bool = False) -> Response:
    try:
        a = load_analysis(song_id)
    except SongNotFound:
        raise HTTPException(404, "song not found")
    if original and a.source_path and Path(a.source_path).exists():
        return FileResponse(a.source_path, media_type=_mime(a.source_path))
    wav = P._decoded_wav(Path(a.source_path))
    if wav is None or not wav.exists():
        raise HTTPException(404, "decoded audio unavailable")
    return FileResponse(str(wav), media_type="audio/wav",
                        headers={"Accept-Ranges": "bytes"})


def _mime(p: Path) -> str:
    return {
        ".flac": "audio/flac", ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
        ".opus": "audio/ogg", ".aiff": "audio/aiff", ".aif": "audio/aiff",
    }.get(p.suffix.lower(), "application/octet-stream")


@app.get("/api/stems/{song_id}/{model}/{stem}.wav")
def stem_audio(song_id: str, model: str, stem: str) -> FileResponse:
    p = P.load_stem_wav(song_id, model, stem)
    if p is None:
        raise HTTPException(404, "stem not found")
    return FileResponse(str(p), media_type="audio/wav",
                        headers={"Accept-Ranges": "bytes"})


@app.get("/api/songs/{song_id}/peaks")
def song_peaks(song_id: str, t0: float = 0.0, t1: float | None = None,
               width: int = Query(2000, ge=16, le=8000),
               source: str | None = Query(
                   None, description="a stem id such as 'vocals'; omit for the full mix"),
               sources: str | None = Query(
                   None, description="comma-separated stem ids; one request for all of "
                                     "them. Omit for the full mix."),
               ) -> dict[str, Any]:
    """Windowed peaks for the full mix, or for one separated stem.

    The client asks for exactly the pixels it is about to draw and gets back that
    many columns, which is what makes the waveform scrub smoothly.

    ``source`` exists so a lane can show the audio it is making a claim about. A
    lane that draws only a detected span is a lane that asks to be believed: the
    reader cannot tell "this part is not here" from "the detector missed it",
    which is the confusion behind every under-detection report. On "DNA." the
    regions reported as absent measured *louder* than the regions reported as
    playing, and nothing in the interface could have shown that. Drawing the
    stem's own envelope underneath the highlight makes the measurement falsifiable
    against the audio, which no threshold tuning can do.

    The stem file is resolved through the same loader the playback URL uses, so
    the waveform and the audio can never come from different files. The pyramid is
    keyed on that file like any other: measured 1.6 MB on disk and 7 ms to serve
    a whole song at 1100 columns, so six lanes cost one disk read each and about
    340 KB in total.
    """
    try:
        a = load_analysis(song_id)
    except SongNotFound:
        raise HTTPException(404, "song not found")
    # Several stems in one request.
    #
    # The lanes need every stem's envelope at once, and asking for them one at a
    # time cost six round trips per window. That is the whole of the latency when
    # a zoom crosses a window boundary: the bytes are small and the serialisation
    # is the same work either way, so six trips buy nothing. The curve endpoint
    # already takes a list this way, and the batch is assembled the same way --
    # every stem resolved, one that is missing reported in `errors` rather than
    # failing the batch, because a lane with no file is a lane to draw as absent.
    if sources:
        wanted = [s for s in (x.strip() for x in sources.split(",")) if s][:12]
        out: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for sid in wanted:
            model = next((sep.model for sep in (a.separation or [])
                          if sid in sep.stems), None)
            if not model:
                errors[sid] = f"no separated audio for '{sid}' on this track"
                continue
            path = P.load_stem_wav(song_id, model, sid)
            if path is None:
                errors[sid] = f"separated audio for '{sid}' is not on disk"
                continue
            tt1 = a.duration if t1 is None else min(t1, a.duration)
            out[sid] = {
                "t0": t0, "t1": tt1, "width": width,
                "duration": a.duration, "source": sid,
                "peaks": P.peaks_for_range(P.get_peaks(path), t0, tt1, width),
            }
        return {"series": out, "errors": errors}

    if source:
        # The model comes from the analysis, not from a `glob` over the cache: a
        # song separated twice has two model directories, and reading whichever
        # sorted first would show a waveform for audio that the stem list on
        # screen is not playing. `load_stem_wav` is the same resolver the
        # `/api/stems/...` playback URL uses, so the two cannot drift apart.
        model = next((sep.model for sep in (a.separation or [])
                      if source in sep.stems), None)
        if not model:
            raise HTTPException(404, f"no separated audio for '{source}' on this track")
        p = P.load_stem_wav(song_id, model, source)
        if p is None:
            raise HTTPException(404, f"separated audio for '{source}' is not on disk")
        t1 = a.duration if t1 is None else min(t1, a.duration)
        return {
            "t0": t0, "t1": t1, "width": width,
            "duration": a.duration, "source": source,
            "peaks": P.peaks_for_range(P.get_peaks(p), t0, t1, width),
        }
    if not a.source_path:
        raise HTTPException(404, "no source audio")
    pk = P.get_peaks(Path(a.source_path))
    t1 = a.duration if t1 is None else min(t1, a.duration)
    return {
        "t0": t0, "t1": t1, "width": width,
        "duration": a.duration,
        "peaks": P.peaks_for_range(pk, t0, t1, width),
    }


@app.get("/api/songs/{song_id}/curve")
def song_curve(song_id: str, name: str | None = Query(
                   None, description="e.g. energy/perceived; omit when using `names`"),
               names: str | None = Query(
                   None, description="comma-separated curve names, read in one request"),
               t0: float = 0.0, t1: float | None = None,
               points: int = Query(1600, ge=32, le=6000)) -> dict[str, Any]:
    """Windowed curve read.

    The curve name is a query parameter rather than a path segment because the
    names contain slashes (``energy/perceived``), which a ``{name}`` path
    parameter cannot receive.

    ``names`` reads several curves in one request and answers with ``series``, a
    name-keyed object, each entry shaped exactly like a single response. It
    exists because the stereo width row needs three curves that are always
    wanted together, and the window changes on every zoom: three requests per
    zoom step is three chances for one of them to be a frame behind the other
    two, which would draw three bands that do not describe the same instant.
    One request cannot interleave.
    """
    if not analysis_path(song_id).exists():
        raise HTTPException(404, "song not found")
    if names:
        wanted = [n.strip() for n in names.split(",") if n.strip()]
        # A cap, not a formality: `names` is user-supplied and this loops over
        # disk reads. 12 is comfortably above the widest real request (3) and
        # stops one request from pinning the thread.
        if len(wanted) > 12:
            raise HTTPException(400, "at most 12 curve names per request")
        return {"series": {n: read_curve(song_id, n, t0, t1, points) for n in wanted}}
    if not name:
        raise HTTPException(400, "give either `name` or `names`")
    return read_curve(song_id, name, t0, t1, points)


# ---------------------------------------------------------------------------
# upload + analysis jobs
# ---------------------------------------------------------------------------


#: Analysis is serialised. Not a throughput compromise — a correctness one.
#:
#: Separation runs a neural network on the GPU, and two or three concurrent
#: Demucs passes on MPS do not make progress: they contend for the same Metal
#: command buffer and the process dies with
#: `failed assertion _status < MTLCommandBufferStatusCommitted ... setCurrent-
#: CommandEncoder`, taking the server and every other in-flight job with it. That
#: is not a catchable Python exception, so no `try` around the analysis can save
#: it. Queuing three songs at once therefore crashed the app and lost the other
#: two analyses.
#:
#: One job at a time also matches what a single GPU can actually do: sequential
#: jobs finish sooner overall than three that thrash.
_ANALYSIS_SLOT = threading.Semaphore(1)


def _run_job(job, path: Path, song_id: str, quality: str | None) -> None:
    from arrangement.analysis.pipeline import run_analysis

    def progress(stage: str, frac: float, msg: str) -> None:
        job.stage = stage
        job.progress = frac
        job.message = msg
        for s in job.stages:
            if s["name"] == stage:
                s.update(status="running", progress=frac, message=msg)
                break
        else:
            job.stages.append({"name": stage, "status": "running",
                               "progress": frac, "message": msg})

    if job.status != "cancelled":
        job.message = "waiting for the analysis slot"
    with _ANALYSIS_SLOT:
        if job.cancel_requested:
            job.status = "cancelled"
            return
        job.status = "running"
        _run_job_locked(job, path, song_id, quality, progress, run_analysis)


def _run_job_locked(job, path: Path, song_id: str, quality: str | None,
                    progress, run_analysis) -> None:
    try:
        a = run_analysis(path, song_id=song_id, quality=quality,
                         progress=progress,
                         should_cancel=lambda: job.cancel_requested)
        for s in a.stages:
            for js in job.stages:
                if js["name"] == s.name:
                    js.update(status=s.status, progress=1.0 if s.status == "done" else 0.0,
                              message=s.message, duration=s.duration)
        save_analysis(a)
        job.song_id = a.id
        job.title = a.title
        job.status = "done"
        job.progress = 1.0
        job.message = "analysis complete"
    except Exception as exc:
        log.exception("analysis job failed")
        job.status = "cancelled" if job.cancel_requested else "failed"
        job.error = str(exc)
        job.message = str(exc)
    finally:
        job.finished_at = time.time()


@app.post("/api/upload")
async def upload(file: UploadFile) -> dict[str, Any]:
    settings = get_settings()
    dest_dir = settings.uploads_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "upload.bin").name
    dest = dest_dir / f"{int(time.time() * 1000)}_{name}"
    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            fh.write(chunk)
            size += len(chunk)
            if size > 600 * 1024 * 1024:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file larger than 600 MB")
    return {"path": str(dest), "filename": name, "size": size,
            "url": f"/api/files/{dest.name}"}


@app.get("/api/files/{name}")
def uploaded_file(name: str) -> FileResponse:
    p = get_settings().uploads_dir / Path(name).name
    if not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(str(p), media_type=_mime(p))


class AnalyseRequest(BaseModel):
    path: str
    quality: str | None = None
    song_id: str | None = None
    start_analysis: bool = True


@app.post("/api/analyse")
def analyse(req: AnalyseRequest) -> dict[str, Any]:
    settings = get_settings()
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(404, f"file not found: {req.path}")
    allowed = {settings.uploads_dir.resolve(), Path(req.path).resolve().parent}
    try:
        path.resolve().relative_to(settings.uploads_dir.resolve())
    except ValueError:
        if not settings.allow_download and str(path) != req.path:
            raise HTTPException(403, "path not allowed")
    song_id = req.song_id or _id_for(path)
    existing = JOBS.active_for_song(song_id)
    if existing:
        return {"job": existing.as_dict(), "song_id": song_id, "resumed": True}
    job = JOBS.create("analyse", title=path.name)
    job.song_id = song_id
    threading.Thread(
        target=_run_job, args=(job, path, song_id, req.quality), daemon=True
    ).start()
    return {"job": job.as_dict(), "song_id": song_id, "resumed": False}


def _id_for(path: Path) -> str:
    from arrangement.audio.io import probe, short_id

    info = probe(path)
    return short_id(str(path.resolve()), str(info.get("size_bytes", 0)))


@app.get("/api/jobs")
def jobs() -> dict[str, Any]:
    return {"jobs": JOBS.all()}


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    return j.as_dict()


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str) -> dict[str, Any]:
    return {"cancelled": JOBS.cancel(job_id)}


@app.get("/api/jobs/{job_id}/stream")
