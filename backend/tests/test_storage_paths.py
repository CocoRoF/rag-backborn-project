"""Path rules, checked without a database. `path` is the scoping key for subtree search, so a
name that escapes its folder escapes its permissions too."""
from __future__ import annotations

import pytest

from ragb.core.errors import ValidationFailed
from ragb.services.storage import clean_name, slugify


@pytest.mark.parametrize("raw", ["../../etc/passwd", "a/b/c.txt", "..\\..\\win.ini"])
def test_traversal_is_reduced_to_a_basename(raw):
    assert "/" not in clean_name(raw) and "\\" not in clean_name(raw)
    assert clean_name(raw) not in ("..", ".")


@pytest.mark.parametrize("raw", ["", "   ", ".", "..", "\x00"])
def test_empty_and_dot_names_are_rejected(raw):
    with pytest.raises(ValidationFailed):
        clean_name(raw)


def test_slug_keeps_korean_and_drops_punctuation():
    assert slugify("사내 규정 v2!!") == "사내-규정-v2"
