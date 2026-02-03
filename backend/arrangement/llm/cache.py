"""A content-addressed cache of model responses.

Why this exists
---------------
Every interpretation endpoint re-asks the model a question whose answer is a
function of data that has not changed. "Name the sections" on a song that has
already been named sends the same messages and pays for the same tokens again;
so does opening the reasoning panel twice, so does asking the same question of
the same window. The measurements underneath are deterministic, but the bill for
asking about them was not, which made the cost of *iterating on this tool*
proportional to how many times you looked at it.

The key is the request, hashed
------------------------------
The cache key is a hash of everything that determines the response: the model,
the effort, the sampling parameters and the full message list. Nothing else.

That has one property worth stating plainly, because it is what makes the cache
safe rather than a source of confidently wrong answers: **re-analysing a song
changes the messages, so it changes the key, so the next call goes to the
model.** A stale answer cannot be served for changed data, because changed data
cannot produce the same key. There is no TTL and no invalidation button to
forget to press, and therefore no way for the cache to be the reason a number on
screen is wrong.

What it does change
-------------------
Two identical calls now return the same text. At ``temperature=0.3`` they were
previously free to differ, so a repeated question used to produce a fresh
phrasing each time. For this tool that is an improvement rather than a loss:
the interesting part of a model response here is whether it agrees with the
measurements, and a stable answer is one that can be argued with. It also means
a claim in `BUGS.md` that cites what the model said stays checkable.

Set ``DEEPSEEK_CACHE=0`` to turn it off. ``llm_cache_enabled`` reports the
current state so the server can say so out loud rather than leaving you to
guess whether you are paying.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Off by default in the environment so that a deployment which did not ask for
#: this does not silently start serving answers from disk. The project `.env`
#: turns it on.
ENV_VAR = "DEEPSEEK_CACHE"


def llm_cache_enabled() -> bool:
    raw = os.environ.get(ENV_VAR, "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def cache_dir() -> Path:
    from arrangement.config import get_settings
    return get_settings().tmp_dir / "llm_cache"


def request_key(
    messages: list[dict[str, str]],
    model: str,
    effort: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
) -> str:
    """A stable digest of everything that determines the response.

    ``json.dumps`` with ``sort_keys`` and no whitespace, so a dict that was
    built in a different order still hashes the same. Any field left out of this
    is a field the cache would treat as irrelevant, and therefore a field whose
    change would serve the wrong answer — so the parameter list here has to be
    the whole of `DeepSeekClient.chat`'s signature that reaches the API.
    """
    payload = {
        "messages": messages,
        "model": model,
        "effort": effort,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "json_mode": json_mode,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load(key: str) -> dict[str, Any] | None:
    """The cached response for ``key``, or None. A corrupt file is a miss.

    A half-written cache entry must not be able to take the feature down, so a
    read or parse failure deletes the entry and reports a miss. The alternative —
    propagating the error — would mean one interrupted write permanently disabled
    interpretation for that request.
    """
    if not llm_cache_enabled():
        return None
    p = cache_dir() / f"{key}.json"
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("llm cache entry %s is unreadable (%s); discarding", key[:12], exc)
        try:
            p.unlink()
        except OSError:
            pass
        return None


def store(key: str, response: dict[str, Any]) -> None:
    if not llm_cache_enabled():
        return
    try:
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        # Write beside the target and rename, so a reader never sees half an
        # entry. `asdict` keeps this working when fields are added to
        # LLMResponse, which a hand-written dict would silently drop.
        tmp = d / f".{key}.tmp"
        tmp.write_text(json.dumps(asdict(response) if not isinstance(response, dict)
                                  else response, sort_keys=True))
        tmp.replace(d / f"{key}.json")
    except OSError as exc:
        # A cache that cannot be written is a performance problem, not a
        # correctness one. The call already happened and was paid for; failing
        # here would throw away a good answer over a disk problem.
        log.warning("could not write llm cache entry %s: %s", key[:12], exc)


def stats() -> dict[str, Any]:
    """How much is cached. Cheap enough to expose on the health endpoint."""
    d = cache_dir()
    if not d.exists():
        return {"enabled": llm_cache_enabled(), "entries": 0, "bytes": 0}
    files = list(d.glob("*.json"))
    return {
        "enabled": llm_cache_enabled(),
        "entries": len(files),
        "bytes": sum(f.stat().st_size for f in files),
    }
