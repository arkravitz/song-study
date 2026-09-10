"""The spatial bands, and the names things are stored under.

The bug this exists for: `SPATIAL_BANDS` called the middle band `low_mid` while
the analysis wrote its result to `spatial/mid_width` and reported it as
`mid_width`. Nothing was wrong until something needed to *derive* a curve name
from the band list — at which point it produced `spatial/low_mid_width`, which
does not exist, and the failure appeared as an empty row at runtime rather than
an error at import.

So the property under test is not "the bands are these three". It is that every
name a consumer could plausibly derive from the band list resolves to something
the analysis actually wrote.
"""

from __future__ import annotations

import pytest

from arrangement.dsp.spatial import SPATIAL_BANDS, SpatialBand
from arrangement.schema import SpatialCurve


class TestBandDefinitions:
    def test_three_bands(self):
        # "At least 3 bands" is the requirement; three is what the analysis
        # computes, and inventing a fourth here would mean a band with no
        # measurement behind it.
        assert len(SPATIAL_BANDS) == 3

    def test_bands_are_contiguous_and_ordered(self):
        """A gap or an overlap would silently drop or double-count audio.

        Contiguity is load-bearing rather than tidy: these are the same STFT
        bins the width ratio is computed over, and a band that does not meet its
        neighbour leaves a hole in the measurement.
        """
        for a, b in zip(SPATIAL_BANDS, SPATIAL_BANDS[1:]):
            assert a.hi == b.lo, f"{a.name} ends at {a.hi} but {b.name} starts at {b.lo}"
        assert SPATIAL_BANDS[0].lo == 20
        for a, b in zip(SPATIAL_BANDS, SPATIAL_BANDS[1:]):
            assert a.lo < a.hi
            assert b.lo < b.hi

    def test_labels_are_not_internal_names(self):
        """What a producer reads is not `low_mid`.

        The internal name is allowed to be whatever the code finds convenient;
        the label is not, because it is printed next to a curve in the interface.
        """
        for b in SPATIAL_BANDS:
            assert b.label == b.label.lower()
            assert "_" not in b.label, f"{b.label!r} is an internal name, not a label"
            assert len(b.label) <= 5


class TestDerivedNamesResolve:
    @pytest.mark.parametrize("field", [
        "low_width", "mid_width", "high_width",
        "pan", "width", "correlation", "low_pan", "mid_pan", "high_pan",
    ])
    def test_field_is_a_real_attribute_on_the_model(self, field):
        """The name a consumer derives must be a name the model has.

        `low_mid_width` was not, and nothing said so until a request 404'd on it.
        """
        assert field in SpatialCurve.model_fields, (
            f"SpatialCurve has no field {field!r}; SPATIAL_BANDS names a curve "
            f"the analysis never writes"
        )

    def test_curve_names_are_the_ones_the_persister_writes(self):
        """`spatial/<field>` is exactly what store.py puts in the npz."""
        from arrangement.server import store
        for b in SPATIAL_BANDS:
            assert b.curve == f"spatial/{b.field}"
            # The persister's list, read from the source rather than retyped, so
            # this test fails if a band is added without a matching write.
            assert b.field in store.SPATIAL_CURVE_FIELDS

    def test_every_band_has_a_distinct_field_and_curve(self):
        fields = [b.field for b in SPATIAL_BANDS]
        assert len(set(fields)) == len(fields), "two bands share a stored field"


def test_a_band_is_frozen():
    """The bands are module-level and read by every caller.

    A mutable one could be edited in place by a caller and every later
    measurement would use a different crossover than the label says.
    """
    b = SPATIAL_BANDS[0]
    with pytest.raises(Exception):
        b.lo = 1000  # type: ignore[misc]
    assert isinstance(b, SpatialBand)
