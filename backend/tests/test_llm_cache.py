"""Tests for the model-response cache.

The property worth testing is not "it returns something". It is that the cache
cannot serve an answer for data it was not asked about, because that is the
failure mode that would make a cached answer worse than a wrong one: a
confidently-stale section name attached to a re-analysed song.

Each test below is written so that it fails if the key stops covering the thing
it is supposed to cover. A test that only checked the happy path would keep
passing after someone added a parameter to the request and forgot the key.
"""

from __future__ import annotations

import pytest

from arrangement.llm import cache


def _msgs(text: str = "hello") -> list[dict[str, str]]:
    return [{"role": "user", "content": text}]


@pytest.fixture(autouse=True)
def _cache_on(tmp_path, monkeypatch):
    """Point the cache at a tmp dir and force it on.

    The directory is redirected rather than the real one so a test run cannot
    write into the user's cache, and so a failed run cannot leave an entry that
    makes a later run pass for the wrong reason.
    """
    monkeypatch.setenv(cache.ENV_VAR, "1")
    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path / "llm_cache")
    yield


class TestKeyCoversTheRequest:
    def test_same_request_same_key(self):
        a = cache.request_key(_msgs(), "m", "high", 8000, 0.3, False)
        b = cache.request_key(_msgs(), "m", "high", 8000, 0.3, False)
        assert a == b

    def test_message_text_changes_the_key(self):
        # This is the property the whole cache rests on: a different analysis
        # produces different messages, and therefore a miss.
        a = cache.request_key(_msgs("DNA"), "m", "high", 8000, 0.3, False)
        b = cache.request_key(_msgs("Taste"), "m", "high", 8000, 0.3, False)
        assert a != b

    @pytest.mark.parametrize("i,kwargs", [
        (0, dict(model="other")),
        (1, dict(effort="low")),
        (2, dict(max_tokens=4000)),
        (3, dict(temperature=0.0)),
        (4, dict(json_mode=True)),
    ])
    def test_every_request_parameter_changes_the_key(self, i, kwargs):
        """Each field that reaches the API must reach the key.

        A field present in the request but absent from the key is a field whose
        change would silently serve the wrong answer, so the test enumerates them
        instead of trusting that the list in `request_key` is complete.
        """
        base = dict(model="m", effort="high", max_tokens=8000,
                    temperature=0.3, json_mode=False)
        other = {**base, **kwargs}
        a = cache.request_key(_msgs(), **{k: base[k] for k in
                                          ("model", "effort", "max_tokens",
                                           "temperature", "json_mode")})
        b = cache.request_key(_msgs(), **{k: other[k] for k in
                                          ("model", "effort", "max_tokens",
                                           "temperature", "json_mode")})
        assert a != b, f"changing {list(kwargs)[0]} did not change the key"

    def test_message_order_matters(self):
        a = cache.request_key(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            "m", "high", 8000, 0.3, False)
        b = cache.request_key(
            [{"role": "user", "content": "u"}, {"role": "system", "content": "s"}],
            "m", "high", 8000, 0.3, False)
        assert a != b


class TestRoundTrip:
    def test_store_then_load(self):
        key = cache.request_key(_msgs(), "m", "high", 8000, 0.3, False)
        cache.store(key, {"content": "Chorus 1", "reasoning": "", "model": "m",
                          "prompt_tokens": 10, "completion_tokens": 5,
                          "reasoning_tokens": 0, "duration": 1.0,
                          "finish_reason": "stop"})
        got = cache.load(key)
        assert got is not None
        assert got["content"] == "Chorus 1"

    def test_miss_for_an_unknown_key(self):
        key = cache.request_key(_msgs("never asked"), "m", "high", 8000, 0.3, False)
        assert cache.load(key) is None

    def test_a_corrupt_entry_is_a_miss_and_is_removed(self):
        """A half-written entry must not take the feature down.

        Otherwise one interrupted write permanently breaks interpretation for
        that request, and the failure looks like the model refusing to answer.
        """
        key = cache.request_key(_msgs(), "m", "high", 8000, 0.3, False)
        d = cache.cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.json").write_text("{not json")
        assert cache.load(key) is None
        assert not (d / f"{key}.json").exists(), "corrupt entry was left in place"


class TestSwitch:
    def test_off_means_both_a_miss_and_no_write(self, monkeypatch):
        key = cache.request_key(_msgs(), "m", "high", 8000, 0.3, False)
        monkeypatch.setenv(cache.ENV_VAR, "0")
        cache.store(key, {"content": "x"})
        assert cache.load(key) is None, "a write happened while the cache was off"

    @pytest.mark.parametrize("raw,expected", [
        ("1", True), ("true", True), ("", True),
        ("0", False), ("false", False), ("off", False), ("no", False),
    ])
    def test_the_switch_reads_the_obvious_spellings(self, monkeypatch, raw, expected):
        monkeypatch.setenv(cache.ENV_VAR, raw)
        assert cache.llm_cache_enabled() is expected


def test_stats_counts_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path / "llm_cache")
    for i in range(3):
        key = cache.request_key(_msgs(f"q{i}"), "m", "high", 8000, 0.3, False)
        cache.store(key, {"content": f"a{i}"})
    s = cache.stats()
    assert s["entries"] == 3
    assert s["bytes"] > 0
    assert s["enabled"] is True


def test_stats_on_a_machine_that_has_never_asked(tmp_path, monkeypatch):
    """Reported honestly rather than raising.

    This is surfaced on the health endpoint, and an exception here would look
    like the server being down — the worst possible reading of "you have not used
    this feature yet".
    """
    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path / "never_created")
    assert cache.stats() == {"enabled": True, "entries": 0, "bytes": 0}
