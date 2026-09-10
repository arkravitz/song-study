"""The library list must be an order the reader can predict and rely on.

It was sorted by `created_at`, descending — and `created_at` is written by every
analysis, not only by adding a song. So the order of the library was an artefact
of the last analysis run: after re-analysing the four cached songs it came back

    Taste · All the Small Things · 03 - hit it and quit it · DNA

which is neither chronological nor alphabetical, but the order the jobs happened
to finish in, and it changed again on the next run. A list that reshuffles when
you re-analyse something cannot be learned, and nothing on screen tells the
reader why it moved.

Recency is not lost by this: a finished job opens its own result, so "what did I
just add" is answered by the app rather than by the order of a list.

The tiebreak matters as much as the key. `title` is nullable and two songs can
share one, so the sort is made total with the id — otherwise two loads of the
same library can disagree, which is the same defect in a quieter form.
"""

from __future__ import annotations

import pytest

from arrangement.server import store


class TestLibraryOrder:
    def test_the_order_is_alphabetical_by_title(self):
        ids = store.library_order([
            {"id": "c", "title": "Taste", "created_at": 300.0},
            {"id": "a", "title": "DNA", "created_at": 200.0},
            {"id": "b", "title": "All the Small Things", "created_at": 100.0},
        ])
        assert [d["title"] for d in ids] == [
            "All the Small Things", "DNA", "Taste"]

    def test_reanalysing_a_song_does_not_move_it(self):
        """The defect as a property: only `created_at` changes."""
        base = [
            {"id": "a", "title": "Alpha", "created_at": 100.0},
            {"id": "b", "title": "Beta", "created_at": 200.0},
        ]
        # Beta re-analysed: newest, as it would be after a re-run.
        after = [
            {"id": "a", "title": "Alpha", "created_at": 100.0},
            {"id": "b", "title": "Beta", "created_at": 999.0},
        ]
        # Compare the order, not the rows: the rows differ by `created_at` on
        # purpose, and the claim is only about where they sit.
        assert ([d["id"] for d in store.library_order(base)]
                == [d["id"] for d in store.library_order(after)] == ["a", "b"])

    def test_case_and_surrounding_space_do_not_decide_the_order(self):
        rows = [
            {"id": "3", "title": "  zest  ", "created_at": 1.0},
            {"id": "1", "title": "Apple", "created_at": 2.0},
            {"id": "2", "title": "mango", "created_at": 3.0},
        ]
        got = [d["title"].strip() for d in store.library_order(rows)]
        assert got == ["Apple", "mango", "zest"]

    def test_titles_that_differ_only_by_case_have_a_stable_order(self):
        """Otherwise the sort is not total and two loads can disagree."""
        rows = [
            {"id": "z", "title": "abc", "created_at": 1.0},
            {"id": "a", "title": "ABC", "created_at": 2.0},
        ]
        first = [d["id"] for d in store.library_order(rows)]
        second = [d["id"] for d in store.library_order(list(reversed(rows)))]
        assert first == second == ["a", "z"], "the order depends on input order"

    def test_a_song_with_no_title_sorts_last_rather_than_first(self):
        rows = [
            {"id": "a", "title": None, "created_at": 5.0},
            {"id": "b", "title": "", "created_at": 4.0},
            {"id": "c", "title": "Real Title", "created_at": 3.0},
        ]
        got = [d["id"] for d in store.library_order(rows)]
        assert got[0] == "c", "a missing title jumped the queue"
        assert set(got) == {"a", "b", "c"}, "a row was dropped"
