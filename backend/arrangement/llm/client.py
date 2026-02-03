"""DeepSeek client.

Deliberately thin: retry with backoff, streaming disabled, usage captured, and
errors raised as a specific exception so the pipeline can degrade to DSP-only
mode rather than failing.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

import requests

from arrangement.llm import cache

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    content: str
    reasoning: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    duration: float = 0.0
    #: Why generation stopped. Worth keeping: an empty ``content`` with
    #: ``finish_reason == "length"`` means the reasoning ate the whole budget,
    #: which is a different bug from a malformed response and needs a different
    #: fix (more tokens, or less effort) rather than a retry.
    finish_reason: str = ""

    def json(self) -> Any:
        return extract_json(self.content)


def extract_json(text: str) -> Any:
    """Pull a JSON object out of a model response, tolerating prose and fences."""
    if not text:
        raise LLMError("empty response")
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i = t.find(opener)
        j = t.rfind(closer)
        if i >= 0 and j > i:
            try:
                return json.loads(t[i: j + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"no JSON found in response: {t[:200]!r}")


class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-flash",
                 deep_model: str = "deepseek-v4-pro",
                 effort: str = "high", timeout: int = 300):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.deep_model = deep_model
        self.effort = effort
        self.timeout = timeout
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int = 8000,
        temperature: float = 0.3,
        json_mode: bool = False,
        retries: int = 3,
    ) -> LLMResponse:
        if not self.enabled:
            raise LLMError("DEEPSEEK_API_KEY is not set")
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if effort or self.effort:
            payload["reasoning_effort"] = effort or self.effort
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        # Ask the cache before the network. The key is built from the payload
        # rather than from the arguments, so it cannot drift out of step with
        # what actually gets sent: if a field is added to `payload` it is
        # automatically part of the key, and if it is left out of `payload` the
        # model never saw it either.
        key = cache.request_key(
            payload["messages"], payload["model"],
            str(payload.get("reasoning_effort", "")),
            max_tokens, temperature, json_mode,
        )
        hit = cache.load(key)
        if hit is not None:
            log.info("llm cache hit %s (%d chars)", key[:12], len(hit.get("content", "")))
            return LLMResponse(**{k: v for k, v in hit.items()
                                  if k in LLMResponse.__dataclass_fields__})

        last: Exception | None = None
        for attempt in range(retries):
            t0 = time.time()
            try:
                r = self._session.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self.api_key}"},
                    data=json.dumps(payload), timeout=self.timeout,
                )
                if r.status_code >= 500 or r.status_code == 429:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
                data = r.json()
                msg = data["choices"][0]["message"]
                usage = data.get("usage") or {}
                details = usage.get("completion_tokens_details") or {}
                reasoning = details.get("reasoning_tokens", 0)
                if not isinstance(reasoning, (int, float)):
                    reasoning = 0
                out = LLMResponse(
                    content=(msg.get("content") or "").strip(),
                    reasoning=(msg.get("reasoning_content") or "").strip(),
                    model=data.get("model", payload["model"]),
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    reasoning_tokens=int(reasoning),
                    duration=time.time() - t0,
                    finish_reason=str(data["choices"][0].get("finish_reason") or ""),
                )
                if not out.content and out.finish_reason == "length":
                    # Reasoning consumed the whole budget. Raising beats
                    # returning an empty string: an empty response is
                    # indistinguishable from a model that had nothing to say,
                    # and every caller silently degrades to its fallback.
                    raise LLMError(
                        f"no content: reasoning used all {out.completion_tokens} "
                        f"completion tokens (model={out.model}, "
                        f"reasoning={out.reasoning_tokens}). Raise max_tokens or "
                        f"lower reasoning_effort for this task."
                    )
                cache.store(key, asdict(out))
                return out
            except LLMError:
                # A budget/sizing problem is deterministic. Retrying it three
                # times costs three times the latency and changes nothing.
                raise
            except Exception as exc:  # network, 5xx, rate limit
                last = exc
                if attempt < retries - 1:
                    sleep = 1.5 * (2 ** attempt)
                    log.info("DeepSeek attempt %d failed (%s); retrying in %.1fs",
                             attempt + 1, exc, sleep)
                    time.sleep(sleep)
        raise LLMError(f"DeepSeek request failed after {retries} attempts: {last}")

    def list_models(self) -> list[dict]:
        try:
            r = self._session.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"}, timeout=30)
            r.raise_for_status()
            return r.json().get("data", [])
        except Exception as exc:
            raise LLMError(str(exc)) from exc


def client_from_settings() -> DeepSeekClient:
    from arrangement.config import get_settings

    s = get_settings()
    return DeepSeekClient(
        api_key=s.deepseek_api_key, base_url=s.deepseek_base_url,
        model=s.deepseek_model, deep_model=getattr(s, "deepseek_model_deep",
                                                   "deepseek-v4-pro"),
        effort=s.deepseek_effort,
    )
