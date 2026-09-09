"""The Korean term reducer is what makes the lexical legs fire at all; tsvector's 'simple'
tokenizer treats "보증기간을" and "보증기간이" as unrelated words."""
from __future__ import annotations

from ragb.services.retrieval import query_terms


def test_particles_are_stripped():
    assert "보증기간" in query_terms("보증기간은 얼마나 되나요?")
    assert "보증기간" in query_terms("보증기간이 궁금합니다")


def test_question_words_are_dropped():
    terms = query_terms("어떤 결제사를 지원하나요?")
    assert "어떤" not in terms
    assert "결제사" in terms


def test_short_and_empty_inputs_are_safe():
    assert query_terms("") == []
    assert query_terms("아") == []
