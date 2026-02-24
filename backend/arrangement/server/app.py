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
def job_stream(job_id: str, request: Request) -> StreamingResponse:
    """Server-sent progress.

    Polling every 500 ms would work, but SSE means the UI shows a live stage
    list for a 3-minute separation without hammering the server.
    """
    async def gen():
        last = None
        while True:
            if await request.is_disconnected():
                break
            j = JOBS.get(job_id)
            if j is None:
                yield "event: error\ndata: {\"message\":\"no such job\"}\n\n"
                break
            payload = j.as_dict()
            blob = json.dumps(payload)
            if blob != last:
                last = blob
                yield f"data: {blob}\n\n"
            if payload["status"] in ("done", "failed", "cancelled"):
                yield f"event: end\ndata: {blob}\n\n"
                break
            time.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
    })


# ---------------------------------------------------------------------------
# reasoning
# ---------------------------------------------------------------------------


def _client():
    c = client_from_settings()
    if not c.enabled:
        raise HTTPException(503, "DEEPSEEK_API_KEY is not set; reasoning is disabled")
    return c


def _analysis(song_id: str) -> SongAnalysis:
    try:
        return load_analysis(song_id)
    except SongNotFound:
        raise HTTPException(404, "song not found")


def _persist(a: SongAnalysis) -> None:
    save_analysis(a)


class SectionEditState(BaseModel):
    sections: list[Section]
    transitions: list[SectionTransition]
    warnings: list[str]


def _section_state(a: SongAnalysis) -> dict[str, Any]:
    state = SectionEditState(sections=a.sections, transitions=a.transitions,
                             warnings=a.warnings).model_dump(mode="json")
    revision = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    return {**state, "revision": revision}


@app.get("/api/songs/{song_id}/sections/state")
def section_state_endpoint(song_id: str) -> dict[str, Any]:
    return _section_state(_analysis(song_id))


class RestoreSectionRequest(BaseModel):
    state: SectionEditState
    expected_revision: str


@app.put("/api/songs/{song_id}/sections/state")
def restore_section_state_endpoint(song_id: str, req: RestoreSectionRequest) -> dict[str, Any]:
    a = _analysis(song_id)
    if _section_state(a)["revision"] != req.expected_revision:
        raise HTTPException(409, "Sections changed since this edit. Undo would overwrite those changes.")
    sections = req.state.sections
    if not sections or len({s.id for s in sections}) != len(sections) or any(
        s.start < 0 or s.end > a.duration + .05 or s.end <= s.start
        for s in sections
    ) or any(abs(left.end - right.start) > .05 for left, right in zip(sections, sections[1:])):
        raise HTTPException(400, "The saved section layout is invalid.")
    # Restore edit metadata exactly; audio, curves and other analysis stay intact.
    a.sections = sections
    a.transitions = req.state.transitions
    a.warnings = req.state.warnings
    _persist(a)
    return _section_state(a)


class WindowRequest(BaseModel):
    t0: float
    t1: float
    deep: bool = False


class SplitSectionRequest(BaseModel):
    macro_id: str
    at: float = Field(allow_inf_nan=False)
    before_name: str | None = Field(default=None, min_length=1, max_length=36)
    after_name: str | None = Field(default=None, min_length=1, max_length=36)


@app.post("/api/songs/{song_id}/sections/split")
def split_section_endpoint(song_id: str, req: SplitSectionRequest) -> dict[str, Any]:
    """Apply a listener's structural correction to the saved song analysis."""
    a = _analysis(song_id)
    try:
        section = split_macro_section(a, req.macro_id, req.at,
                                      req.before_name, req.after_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _persist(a)
    return {"section_id": section.id, "at": req.at,
            "macro_sections": len({s.macro_id or s.id for s in a.sections})}


class MoveSectionBoundaryRequest(BaseModel):
    right_macro_id: str
    at: float = Field(allow_inf_nan=False)


@app.post("/api/songs/{song_id}/sections/boundary")
def move_section_boundary_endpoint(song_id: str, req: MoveSectionBoundaryRequest) -> dict[str, Any]:
    a = _analysis(song_id)
    try:
        move_macro_boundary(a, req.right_macro_id, req.at)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _persist(a)
    return {"at": req.at}


class MergeSectionsRequest(BaseModel):
    right_macro_id: str


@app.post("/api/songs/{song_id}/sections/merge")
def merge_sections_endpoint(song_id: str, req: MergeSectionsRequest) -> dict[str, Any]:
    a = _analysis(song_id)
    try:
        macro_id = merge_macro_sections(a, req.right_macro_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _persist(a)
    return {"macro_id": macro_id}


class CompareRequest(BaseModel):
    a0: float
    a1: float
    b0: float
    b1: float
    label_a: str = ""
    label_b: str = ""
    deep: bool = False


class QuestionRequest(BaseModel):
    question: str
    deep: bool = False


class EventRequest(BaseModel):
    event_id: str
    deep: bool = False


class EnergyRequest(BaseModel):
    index: int
    deep: bool = False


def _guard(fn):
    """Convert LLMError into a 502.

    Two things matter here. ``functools.wraps`` preserves the signature, without
    which FastAPI inspects ``(*args, **kwargs)`` and starts demanding the
    endpoint's own parameters as query strings. And the wrapper stays *synchronous*,
    so FastAPI runs it in a threadpool — the DeepSeek call blocks for 20-30 s and
    must not occupy the event loop.
    """
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LLMError as exc:
            raise HTTPException(502, f"DeepSeek error: {exc}")

    return wrapper


@app.post("/api/songs/{song_id}/sections/label")
@_guard
def label_sections_endpoint(song_id: str, deep: bool = False) -> dict[str, Any]:
    """Name the sections.

    Naming is a judgement, so it goes through the model — but the input is
    entirely measured and the output is restricted to a fixed vocabulary, with
    the constraints (a group is named once, an intro cannot occur mid-record)
    enforced in code rather than left to the prompt.
    """
    a = _analysis(song_id)
    ins = interpret.label_sections(a, _client(), deep=deep)
    a.insights = [i for i in a.insights if i.scope != "sections"] + [ins]
    _persist(a)
    return {
        "insight": ins.model_dump(mode="json"),
        "grounding": summarise_grounding(ins.claims),
        "sections": [
            {"id": s.id, "start": s.start, "end": s.end, "label": s.label.value,
             "confidence": round(s.label_confidence, 3), "source": s.label_source,
             "repeat_group": s.repeat_group, "repeat_rank": s.repeat_rank,
             "description": s.description}
            for s in a.sections
        ],
    }


@app.post("/api/songs/{song_id}/insight")
@_guard
def insight(song_id: str, deep: bool = False) -> dict[str, Any]:
    a = _analysis(song_id)
    ins = interpret.analyse_song(a, _client(), deep=deep)
    a.insights = [i for i in a.insights if i.scope != "song"] + [ins]
    _persist(a)
    return {"insight": ins.model_dump(mode="json"),
            "grounding": summarise_grounding(ins.claims)}


@app.post("/api/songs/{song_id}/insight/window")
@_guard
def insight_window(song_id: str, req: WindowRequest) -> dict[str, Any]:
    a = _analysis(song_id)
    ins = interpret.explain_window(a, _client(), req.t0, req.t1, deep=req.deep)
    a.insights = [i for i in a.insights
                  if not i.scope.startswith(f"window:{req.t0:.2f}")] + [ins]
    _persist(a)
    return {"insight": ins.model_dump(mode="json"),
            "grounding": summarise_grounding(ins.claims)}


@app.post("/api/songs/{song_id}/insight/event")
@_guard
def insight_event(song_id: str, req: EventRequest) -> dict[str, Any]:
    a = _analysis(song_id)
    ins = interpret.explain_event(a, _client(), req.event_id, deep=req.deep)
    a.insights = [i for i in a.insights if i.scope != f"event:{req.event_id}"] + [ins]
    _persist(a)
    return {"insight": ins.model_dump(mode="json"),
            "grounding": summarise_grounding(ins.claims)}


@app.post("/api/songs/{song_id}/insight/energy")
@_guard
def insight_energy(song_id: str, req: EnergyRequest) -> dict[str, Any]:
    a = _analysis(song_id)
    ins = interpret.explain_energy_change(a, _client(), req.index, deep=req.deep)
    a.insights = [i for i in a.insights if i.scope != f"energy:{req.index}"] + [ins]
    _persist(a)
    return {"insight": ins.model_dump(mode="json"),
            "grounding": summarise_grounding(ins.claims)}


@app.post("/api/songs/{song_id}/compare")
@_guard
def compare_windows(song_id: str, req: CompareRequest) -> dict[str, Any]:
    a = _analysis(song_id)
    ins = interpret.compare(a, _client(), req.a0, req.a1, req.b0, req.b1,
                            req.label_a, req.label_b, deep=req.deep)
    a.comparisons = [c for c in a.comparisons
                     if not (c.a_ref == ins.a_ref and c.b_ref == ins.b_ref)] + [ins]
    _persist(a)
    return {"comparison": ins.model_dump(mode="json"),
            "grounding": summarise_grounding(ins.claims)}


@app.post("/api/songs/{song_id}/ask")
@_guard
def ask(song_id: str, req: QuestionRequest) -> dict[str, Any]:
    a = _analysis(song_id)
    q = req.question.strip()
    if not q:
        raise HTTPException(400, "empty question")
    ins = interpret.answer_question(a, _client(), q, deep=req.deep)
    a.insights = [i for i in a.insights if i.scope != "question"] + [ins]
    _persist(a)
    return {"insight": ins.model_dump(mode="json"),
            "grounding": summarise_grounding(ins.claims)}


@app.post("/api/songs/{song_id}/lessons")
@_guard
def lessons(song_id: str, deep: bool = True) -> dict[str, Any]:
    a = _analysis(song_id)
    ins = interpret.lessons(a, _client(), deep=deep)
    a.insights = [i for i in a.insights if i.scope != "lessons"] + [ins]
    _persist(a)
    return {"insight": ins.model_dump(mode="json"),
            "grounding": summarise_grounding(ins.claims)}


# ---------------------------------------------------------------------------
# library scan
# ---------------------------------------------------------------------------


@app.get("/api/library")
def library(root: str | None = None, limit: int = 200,
            max_dirs: int = 20000) -> dict[str, Any]:
    """List audio files under a folder so they can be queued without uploading.

    Bounded on purpose. The obvious implementation — ``sorted(base.rglob("*"))`` —
    materialises the entire recursive listing and sorts it before filtering, so
    pointing this at a home directory walks every ``node_modules`` and
    ``.git`` object on the machine. It did not fail; it simply never returned,
    which read as a dead button.

    So: walk with pruning, cap the directories visited, never build a list of
    everything, and say so in the response when the result was cut short. A
    silent cap is how a scan appears to work while quietly hiding half a library.
    """
    base = Path(root).expanduser() if root else Path.home()
    if not base.exists():
        raise HTTPException(404, f"no such folder: {base}")
    if not base.is_dir():
        raise HTTPException(400, f"not a folder: {base}")

    exts = {".flac", ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".aiff", ".aif"}
    # Directories that cannot contain a music library worth scanning. Skipping
    # them is the difference between milliseconds and minutes.
    skip = {
        "node_modules", ".git", ".svn", ".hg", "__pycache__", ".venv", "venv",
        "Library", ".Trash", ".cache", ".npm", ".cargo", ".rustup", "vendor",
        ".next", ".nuxt", "dist", "build", ".gradle", ".m2", "site-packages",
        ".venv-uvr", "Pods", "DerivedData", ".DS_Store", ".tox", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", "env", "virtualenv",
    }

    out: list[dict[str, Any]] = []
    seen_dirs = 0
    hit_limit = False
    hit_dir_cap = False

    for dirpath, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
        seen_dirs += 1
        if seen_dirs > max_dirs:
            hit_dir_cap = True
            break
        # Prune in place, which is what makes os.walk skip the subtree.
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in skip and not d.startswith(".")
        )
        for fn in sorted(filenames):
            ext = Path(fn).suffix.lower()
            if ext not in exts or fn.startswith("."):
                continue
            p = Path(dirpath) / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            out.append({"path": str(p), "name": fn, "size": size,
                        "rel": str(p.relative_to(base))})
            if len(out) >= limit:
                hit_limit = True
                break
        if hit_limit:
            break

    out.sort(key=lambda d: d["rel"].lower())
    return {
        "root": str(base),
        "files": out,
        "count": len(out),
        "truncated": hit_limit or hit_dir_cap,
        "scanned_dirs": seen_dirs,
        # A specific next step beats a bare "truncated": tell the user what to do.
        "note": (
            f"stopped after {limit} files — narrow the folder to see the rest"
            if hit_limit else
            f"stopped after {max_dirs} folders — narrow the folder to see the rest"
            if hit_dir_cap else None
        ),
    }


# ---------------------------------------------------------------------------
# static
# ---------------------------------------------------------------------------


@app.get("/")
def index() -> Any:
    if (STATIC_DIR / "index.html").exists():
        return FileResponse(str(STATIC_DIR / "index.html"))
    return JSONResponse({
        "message": "Arrangement Analyzer API is running. The frontend is not built; "
                   "run `cd frontend && npm install && npm run build`, or use the "
                   "Vite dev server with `npm run dev`.",
        "api": "/api/health",
    })
