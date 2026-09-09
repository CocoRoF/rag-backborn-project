"""The plug-in contract, checked without a database.

These pin the parts a new plug-in silently depends on: the registry has no duplicates, every
declared field is well-formed, and config coercion turns form strings into what `run` reads.
A plug-in whose form and code disagree fails here rather than three minutes into a run.
"""
from __future__ import annotations

import pytest

from ragb import plugins
from ragb.core.errors import ValidationFailed
from ragb.plugins.base import KIND_LABEL, PluginKind
from ragb.services.pipeline import coerce, spec_of

FIELD_TYPES = {"text", "textarea", "number", "bool", "select", "collection", "scorecard", "node"}


def test_every_kind_has_at_least_one_plugin():
    kinds = {s["kind"] for s in plugins.catalog()}
    assert kinds == {k.value for k in PluginKind}, "한 단계라도 비면 파이프라인에 구멍이 생긴다"


def test_specs_are_well_formed():
    seen: set[str] = set()
    for spec in plugins.catalog():
        assert spec["id"] not in seen
        seen.add(spec["id"])
        assert spec["name"] and spec["description"]
        assert spec["kind_label"] == KIND_LABEL[PluginKind(spec["kind"])]
        keys = [f["key"] for f in spec["fields"]]
        assert len(keys) == len(set(keys)), f"{spec['id']}: 중복 필드 키"
        for f in spec["fields"]:
            assert f["type"] in FIELD_TYPES, f"{spec['id']}.{f['key']}: 알 수 없는 타입 {f['type']}"
            if f["type"] == "select":
                assert f["options"], f"{spec['id']}.{f['key']}: select 인데 options 가 없다"
                assert all(o["value"] is not None for o in f["options"])
            # The form seeds itself from `defaults`; a required field with no default is fine,
            # but a default must be one of the options when there are options.
            if f["options"] and f["default"] not in (None, ""):
                assert f["default"] in [o["value"] for o in f["options"]]


def test_defaults_survive_coercion():
    """Filling only the required fields must produce a config `run` can use — the rest of the
    form is defaults, and those defaults have to survive the round trip."""
    for spec in plugins.catalog():
        cfg = dict(spec["defaults"])
        # Required fields deliberately ship empty (a CSV path cannot have a sensible default);
        # the form makes the operator supply them, so simulate that here.
        for f in spec["fields"]:
            if f["required"] and not cfg.get(f["key"]):
                cfg[f["key"]] = "x"
        out = coerce(spec_of(spec["id"]), cfg)
        for f in spec["fields"]:
            if f["type"] == "bool":
                assert isinstance(out[f["key"]], bool)
            elif f["type"] == "number" and out[f["key"]] is not None:
                assert isinstance(out[f["key"]], (int, float))


def test_coerce_reads_form_strings():
    spec = spec_of("semantic_link")
    cfg = coerce(spec, {"from_collection": "technology", "to_collection": "company",
                        "top_k": "7", "min_score": "0.4", "rerank": "true", "both_ways": False})
    assert cfg["top_k"] == 7 and isinstance(cfg["top_k"], int)
    assert cfg["min_score"] == 0.4
    assert cfg["rerank"] is True and cfg["both_ways"] is False


def test_missing_required_field_is_rejected():
    with pytest.raises(ValidationFailed):
        coerce(spec_of("csv_records"), {"collection": "technology", "title_column": "이름"})  # path 없음


def test_number_field_rejects_text():
    with pytest.raises(ValidationFailed):
        coerce(spec_of("semantic_link"), {"from_collection": "a", "to_collection": "b", "top_k": "다섯"})
