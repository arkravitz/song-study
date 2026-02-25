"""Analysis store: on-disk analysis documents and job tracking.

Analyses are persisted as JSON next to the uploaded audio so a library of songs
survives restarts.  The JSON is the source of truth for metadata, events,
sections and claims; the heavy curves live in a companion ``.npz`` and are
served windowed rather than inlined, which keeps a 4-minute song's payload in
the tens of kilobytes instead of the tens of megabytes.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from arrangement.config import get_settings
from arrangement.schema import SongAnalysis


class SongNotFound(KeyError):
    pass


def analysis_path(song_id: str) -> Path:
    return get_settings().analyses_dir / f"{song_id}.json"


def curves_path(song_id: str) -> Path:
    return get_settings().analyses_dir / f"{song_id}.npz"


# ---------------------------------------------------------------------------


#: Every `SpatialCurve` field persisted as a standalone windowed curve, written
#: as `spatial/<field>`.
#:
#: Named rather than inlined because two places need to agree on it: this
#: persister, and the test that checks each name `SPATIAL_BANDS` advertises is
#: actually written. The middle band is `mid_width` here while the band list
#: historically called it `low_mid`, and nothing caught the disagreement until a
#: request for `spatial/low_mid_width` came back empty.
SPATIAL_CURVE_FIELDS: tuple[str, ...] = (
    "pan", "width", "correlation",
    "low_width", "mid_width", "high_width",
    "low_pan", "mid_pan", "high_pan",
)


def save_analysis(a: SongAnalysis) -> Path:
    p = analysis_path(a.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = a.model_dump(mode="json")
    curves = payload.pop("curves", None)
    _write_curves(a, curves or {})
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    tmp.replace(p)
    return p


def _write_curves(a: SongAnalysis, curves: dict[str, Any]) -> None:
    arrays: dict[str, np.ndarray] = {}
    for name, c in (curves.get("curves") or {}).items():
        v = c.get("values") or []
        if v:
            arrays[f"curve/{name}"] = np.asarray(v, dtype=np.float32)
    for name, c in (curves.get("band_energy") or {}).items():
        v = c.get("values") or []
        if v:
            arrays[f"band/{name}"] = np.asarray(v, dtype=np.float32)
    for name, c in (a.energy.components or {}).items():
        v = c.get("values") if isinstance(c, dict) else getattr(c, "values", None)
        if v:
            arrays[f"energy/{name}"] = np.asarray(v, dtype=np.float32)
    if a.energy.raw and a.energy.raw.values:
        arrays["energy/raw"] = np.asarray(a.energy.raw.values, dtype=np.float32)
    if a.energy.perceived and a.energy.perceived.values:
        arrays["energy/perceived"] = np.asarray(a.energy.perceived.values, dtype=np.float32)
    sp = a.spatial
    for field_name in SPATIAL_CURVE_FIELDS:
        v = getattr(sp, field_name, None)
        if v:
            arrays[f"spatial/{field_name}"] = np.asarray(v, dtype=np.float32)
    if a.beats.beats:
        arrays["grid/beats"] = np.asarray(a.beats.beats, dtype=np.float32)
    if a.beats.onset_times:
        arrays["grid/onsets"] = np.asarray(a.beats.onset_times, dtype=np.float32)
    # per-source activity, stored as float32 sparse spans (start,end,level)
    for src in a.sources:
        if src.segments:
            m = np.asarray([[s.start, s.end, s.level_db] for s in src.segments],
                           dtype=np.float32)
            arrays[f"activity/{src.id}"] = m
    if arrays:
        np.savez_compressed(curves_path(a.id), **arrays)


def load_analysis(song_id: str) -> SongAnalysis:
    p = analysis_path(song_id)
    if not p.exists():
        raise SongNotFound(song_id)
    return SongAnalysis.model_validate(json.loads(p.read_text()))


def load_summary(song_id: str) -> dict[str, Any]:
    """The library view: everything except the heavy curve payloads."""
    a = load_analysis(song_id)
    d = a.model_dump(mode="json")
    d.pop("curves", None)
    for c in (a.energy.components or {}).values():
        (c if isinstance(c, dict) else c).pop("values", None) if isinstance(
            c, dict) else setattr(c, "values", [])
    if isinstance(a.energy.raw, dict):
        a.energy.raw.pop("values", None)
    if isinstance(a.energy.perceived, dict):
        a.energy.perceived.pop("values", None)
    d["energy"]["raw"] = a.energy.raw.model_dump(mode="json", exclude={"values"})
    d["energy"]["perceived"] = a.energy.perceived.model_dump(mode="json", exclude={"values"})
    d["energy"]["components"] = {
        k: v.model_dump(mode="json", exclude={"values"})
        for k, v in (a.energy.components or {}).items()}
    d.pop("spatial", None)
    d["beat_count"] = len(a.beats.beats)
    d["onset_count"] = len(a.beats.onset_times)
    return d


def audible_source_count(d: dict) -> int:
    """Sources that carry an audible instrument.

    A separator returns a file per instrument whether or not the track has one,
    and the empty ones hold bleed at around -80 dB. Counting files made "DNA."
    claim a piano it does not contain.
    """
    return sum(1 for s in (d.get("sources") or []) if not s.get("is_silent"))


def macro_section_count(d: dict) -> int:
    """Sections as a producer counts them: macros, not detected segments.

    A chorus that changes texture inside itself is two detected segments and one
    section, so the raw count over-reports the form.
    """
    secs = d.get("sections") or []
    return len({s.get("macro_id") or s.get("id") for s in secs})


def list_songs() -> list[dict[str, Any]]:
    out = []
    for p in sorted(get_settings().analyses_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        beats = d.get("beats", {}) or {}
        out.append({
            "id": d.get("id", p.stem),
            "title": d.get("title"),
            "artist": d.get("artist"),
            "album": d.get("album"),
            "duration": d.get("duration", 0.0),
            "bpm": (beats.get("bpm") or 0.0),
            "bpm_confidence": beats.get("bpm_confidence", 0.0),
            "meter": beats.get("meter"),
            # The same two counts the detail view reports, computed the same
            # way. They used to be `len(sources)` and `len(sections)` here while
            # the header computed "instruments that are actually audible" and
            # "sections as a producer counts them" in the browser — so the sidebar
            # said "6 instruments · 10 sections" and the header beside it said
            # "5 instruments · 8 sections" for the same record, one screen apart.
            # One computation, one answer, both read from the same fields.
            "sources": audible_source_count(d),
            "sections": macro_section_count(d),
            "raw_sources": len(d.get("sources", [])),
            "raw_sections": len(d.get("sections", [])),
            "events": len(d.get("events", [])),
            "llm_enabled": d.get("llm_enabled", False),
            "created_at": d.get("created_at", 0.0),
            "source_path": d.get("source_path"),
        })
    return library_order(out)


def library_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Alphabetical by title, with the id as a tiebreak.

    Not most-recently-analysed. `created_at` is written by every analysis, not
    only by adding a song, so sorting on it made the library an artefact of the
    last analysis run: the four cached songs came back Taste, All the Small
    Things, 03 - hit it and quit it, DNA -- neither chronological nor
    alphabetical, just the order the jobs happened to finish in, and it changed
    again on the next re-analysis. A list that reshuffles when you re-analyse
    something cannot be learned, and nothing on screen says why it moved.

    The "what did I just add?" case is answered better elsewhere: a finished job
    opens its own result, so recency does not need encoding in the list order.

    The tiebreak matters as much as the key. `title` is nullable and two songs
    can share one, so the order is made total with the id -- otherwise two loads
    of the same library can disagree, which is the same defect more quietly. A
    row with no usable title sorts last rather than first, and is never dropped.
    """
    return sorted(rows, key=lambda d: (
        (d.get("title") or "").strip().casefold() or "\uffff",
        d.get("id") or "",
    ))


def delete_analysis(song_id: str) -> bool:
    p = analysis_path(song_id)
    if not p.exists():
        return False
    p.unlink()
    c = curves_path(song_id)
    if c.exists():
        c.unlink()
    return True


# ---------------------------------------------------------------------------
# curve access
# ---------------------------------------------------------------------------


def curve_names(song_id: str) -> list[str]:
    p = curves_path(song_id)
    if not p.exists():
        return []
    with np.load(p, allow_pickle=False) as z:
        return list(z.files)


def read_curve(
    song_id: str, name: str, t0: float | None = None, t1: float | None = None,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Windowed, resampled curve read.

    The client never receives the full-resolution array for a long song: it gets
    at most ``max_points`` samples over the window it asked about. This is what
    keeps the transport responsive while scrubbing.
    """
    p = curves_path(song_id)
    if not p.exists():
        return {"name": name, "points": [], "frame_hz": 0.0, "error": "no curve data"}
    with np.load(p, allow_pickle=False) as z:
        if name not in z.files:
            return {"name": name, "points": [], "error": f"unknown curve '{name}'",
                    "available": sorted(z.files)}
        arr = z[name]
        hz = _frame_hz_for(song_id, name, len(arr))
        if t0 is None:
            t0 = 0.0
        if t1 is None:
            t1 = len(arr) / hz if hz else float(len(arr))
        i0 = max(0, int(round(t0 * hz)))
        i1 = min(len(arr), int(round(t1 * hz)))
        if i1 <= i0:
            return {"name": name, "points": [], "frame_hz": hz, "t0": t0, "t1": t1}
        seg = arr[i0:i1]
        n = len(seg)
        if n > max_points:
            idx = np.linspace(0, n - 1, max_points).astype(int)
            seg = seg[idx]
        else:
            idx = np.arange(n)
        t = (i0 + idx) / hz if hz else idx.astype(float)
        return {
            "name": name, "frame_hz": hz, "t0": float(i0 / hz) if hz else 0.0,
            "t1": float(i1 / hz) if hz else 0.0, "count": int(len(seg)),
            "min": float(np.min(seg)), "max": float(np.max(seg)),
            "mean": float(np.mean(seg)),
            "points": [round(float(v), 4) for v in seg],
            "times": [round(float(v), 4) for v in t],
        }


_HZ_CACHE: dict[str, dict[str, float]] = {}


def _frame_hz_for(song_id: str, name: str, n: int) -> float:
    key = f"{song_id}/{name}"
    if key in _HZ_CACHE:
        return _HZ_CACHE[key]
    hz = 0.0
    try:
        a = load_analysis(song_id)
        if name.startswith("curve/") or name.startswith("band/"):
            c = a.curves.get(name.split("/", 1)[1])
            hz = c.frame_hz if c else 0.0
        elif name.startswith("energy/"):
            c = a.energy.components.get(name.split("/", 1)[1])
            if c is not None:
                hz = c.frame_hz
            elif name == "energy/raw" and a.energy.raw is not None:
                hz = a.energy.raw.frame_hz
            elif name == "energy/perceived" and a.energy.perceived is not None:
                hz = a.energy.perceived.frame_hz
        elif name.startswith("spatial/"):
            hz = a.spatial.frame_hz
        else:
            hz = 1.0
    except Exception:
        hz = 1.0
    if hz <= 0:
        hz = 1.0
    _HZ_CACHE[key] = hz
    return hz


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    kind: str  # "analyse" | "upload"
    status: str = "queued"  # queued|running|done|failed|cancelled
    song_id: str | None = None
    title: str | None = None
    stage: str | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    stages: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "song_id": self.song_id, "title": self.title, "stage": self.stage,
            "progress": round(self.progress, 4), "message": self.message,
            "error": self.error, "stages": self.stages,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "elapsed": round((self.finished_at or time.time()) - self.started_at, 1),
            # A stop that has been asked for but not yet acted on.
            #
            # This was missing, and it is the whole reason the interface could
            # not tell the user anything: `cancel()` sets the flag and returns,
            # but the flag never left the server, so the job kept its ordinary
            # running appearance with an enabled `stop` button. Clicking it again
            # did nothing visible, which reads as "stop is broken" rather than
            # "stop is in progress and the current stage has to finish first".
            #
            # `stopping` is derived here rather than left to the client so there
            # is one definition of the state: a stop asked for, on a job that has
            # not finished. A finished job is not stopping, whatever the flag
            # says, because by then there is nothing left to stop.
            "stopping": bool(
                self.cancel_requested
                and self.status in ("queued", "running")
            ),
            "cancel_requested": self.cancel_requested,
        }


class JobStore:
    def __init__(self, keep: int = 60):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self.keep = keep

    def create(self, kind: str, title: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self.keep:
                old = self._order.pop(0)
                self._jobs.pop(old, None)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[dict[str, Any]]:
        return [self._jobs[j].as_dict() for j in reversed(self._order)
                if j in self._jobs]

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.cancel_requested = True
            return True
        return False

    def active_for_song(self, song_id: str) -> Job | None:
        for j in reversed(self._order):
            job = self._jobs.get(j)
            if job and job.song_id == song_id and job.status in ("queued", "running"):
                return job
        return None


JOBS = JobStore()
