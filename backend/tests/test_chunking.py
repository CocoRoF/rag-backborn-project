"""Chunking is where a document's structure is either preserved or lost, and every downstream
score depends on it. These pin the properties the retrieval legs actually rely on."""
from __future__ import annotations

from ragb.services.chunking import build

MARKDOWN = """# 매뉴얼

## 보증 정책
보증 기간은 24개월입니다.

### 예외
소모품은 제외됩니다.

## 배송
2~3일 내 발송됩니다.
"""


def test_heading_tree_is_nested():
    h = build(MARKDOWN, target=40, max_tokens=80, overlap=10)
    titles = [s.title for s in h.sections]
    assert "보증 정책" in titles and "예외" in titles
    exception = next(s for s in h.sections if s.title == "예외")
    warranty = next(s for s in h.sections if s.title == "보증 정책")
    assert exception.parent == warranty.index
    assert exception.heading_path == "매뉴얼 > 보증 정책 > 예외"


def test_every_chunk_belongs_to_a_section():
    h = build(MARKDOWN, target=40, max_tokens=80, overlap=10)
    valid = {s.index for s in h.sections}
    assert h.chunks and all(c.section in valid for c in h.chunks)


def test_headingless_text_still_gets_sections():
    # The section leg would be dead weight on PDFs if this synthesis did not happen.
    body = "\n\n".join(f"문단 {i}. " + "가나다라마바사아자차카타파하 " * 20 for i in range(40))
    h = build(body, target=60, max_tokens=120, overlap=10)
    assert len(h.sections) > 1
    assert all(s.chunk_to >= s.chunk_from for s in h.sections)


def test_page_markers_become_sections_and_survive_on_chunks():
    text = "\n".join(f"[page {p}]\n" + f"{p}페이지 본문입니다. " * 30 for p in range(1, 6))
    h = build(text, target=50, max_tokens=100, overlap=10)
    assert {c.page for c in h.chunks} >= {1, 5}
    assert any("p." in s.title for s in h.sections)
