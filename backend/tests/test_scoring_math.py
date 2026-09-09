"""Weight normalisation, because the same intent typed two ways must score the same."""
from __future__ import annotations

from ragb.services.records import DEFAULT_SCORECARD, normalise_dimensions


def test_percentages_and_fractions_agree():
    as_pct = normalise_dimensions([{"key": "a", "weight": 25}, {"key": "b", "weight": 75}])
    as_frac = normalise_dimensions([{"key": "a", "weight": 0.25}, {"key": "b", "weight": 0.75}])
    assert [d["weight"] for d in as_pct] == [d["weight"] for d in as_frac] == [0.25, 0.75]


def test_zero_weights_fall_back_to_equal_shares():
    dims = normalise_dimensions([{"key": "a", "weight": 0}, {"key": "b", "weight": 0}])
    assert [d["weight"] for d in dims] == [0.5, 0.5]


def test_shipped_scorecard_sums_to_one():
    dims = normalise_dimensions(DEFAULT_SCORECARD["dimensions"])
    assert abs(sum(d["weight"] for d in dims) - 1.0) < 1e-9
    assert {d["key"] for d in dims} == {"momentum", "market", "demand", "feasibility", "fit"}


def test_unkeyed_dimensions_are_dropped():
    assert normalise_dimensions([{"label": "이름만 있음", "weight": 1}]) == []
