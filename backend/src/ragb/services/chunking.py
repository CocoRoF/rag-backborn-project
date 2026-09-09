"""Turn a document's plain text into a section tree plus the chunks under it.

Two outputs from one pass, because they answer different questions. Chunks are what a
specific question matches ("보증 기간이 몇 개월?"); sections are what a topical question
matches ("보증 정책 설명해줘"), and a section carries its whole subtree's text so a summary
can be written for it later.

Headings come from the extractors, which normalise DOCX heading styles, PPTX slides and
XLSX sheets into ``#``-prefixed lines, and PDF pages into ``[page N]`` markers. A document
with no headings at all still gets sections — synthesised from page boundaries, or from
fixed runs of chunks — because a hierarchy that only exists for Markdown would be a
hierarchy nobody can rely on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")
MAX_BYTES = 7000            # one chunk must fit a single embedding request comfortably
SYNTH_SECTION_CHUNKS = 6    # headingless documents: this many chunks make one section
_HEAD = re.compile(r"^(#{1,4})\s+(.*)$")
_PAGE = re.compile(r"^\[page (\d+)\]$")


def count_tokens(t: str) -> int:
    return len(_enc.encode(t, disallowed_special=()))


@dataclass
class Section:
    index: int
    level: int
    title: str
    heading_path: str
    parent: int | None
    page: int | None = None
    chunk_from: int = 0
    chunk_to: int = -1
    tokens: int = 0
    text: str = ""


@dataclass
class Chunk:
    ordinal: int
    text: str
    tokens: int
    heading_path: str
    page: int | None
    section: int


@dataclass
class Hierarchy:
    sections: list[Section] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)


def _byte_cap(t: str) -> list[str]:
    """A chunk is also bounded in bytes: token budgets say nothing about UTF-8 width, and
    Korean text at the token limit can still be three times the size an API accepts."""
    b = t.encode("utf-8")
    if len(b) <= MAX_BYTES:
        return [t]
    out = []
    while b:
        piece = b[:MAX_BYTES]
        cut = piece.rfind(b"\n") if len(b) > MAX_BYTES else len(piece)
        if cut < MAX_BYTES // 2:
            cut = len(piece)
        out.append(b[:cut].decode("utf-8", errors="ignore"))
        b = b[cut:]
    return [x for x in out if x.strip()]


def _split_long(text: str, max_tokens: int, overlap: int) -> list[str]:
    toks = _enc.encode(text, disallowed_special=())
    out, i = [], 0
    while i < len(toks):
        out.append(_enc.decode(toks[i:i + max_tokens]))
        i += max_tokens - overlap if len(toks) - i > max_tokens else max_tokens
    return out


def build(text: str, *, target: int = 700, max_tokens: int = 1000, overlap: int = 100,
          max_chunks: int = 4000) -> Hierarchy:
    h = Hierarchy()
    heading_stack: list[tuple[int, int]] = []     # (level, section index)
    path_stack: list[str] = []
    page: int | None = None
    cur: Section | None = None
    buf: list[str] = []
    buf_tokens = 0

    def open_section(level: int, title: str, parent: int | None) -> Section:
        s = Section(index=len(h.sections), level=level, title=title,
                    heading_path=" > ".join(path_stack), parent=parent, page=page,
                    chunk_from=len(h.chunks))
        h.sections.append(s)
        return s

    def flush() -> None:
        nonlocal buf, buf_tokens
        body = "\n".join(buf).strip()
        buf, buf_tokens = [], 0
        if not body or cur is None:
            return
        for piece in _byte_cap(body):
            if len(h.chunks) >= max_chunks:
                return
            h.chunks.append(Chunk(len(h.chunks), piece, count_tokens(piece), cur.heading_path, page, cur.index))

    def close_section() -> None:
        if cur is not None:
            cur.chunk_to = len(h.chunks) - 1
            cur.tokens = count_tokens(cur.text)

    for raw in text.split("\n"):
        line = raw.rstrip()
        m = _PAGE.match(line.strip())
        if m:
            page = int(m.group(1))
            continue
        head = _HEAD.match(line)
        if head:
            flush()
            close_section()
            level = len(head.group(1))
            title = head.group(2).strip() or f"섹션 {len(h.sections) + 1}"
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            path_stack[:] = path_stack[:level - 1] + [title]
            parent = heading_stack[-1][1] if heading_stack else None
            cur = open_section(level, title, parent)
            heading_stack.append((level, cur.index))
            continue
        if cur is None:
            path_stack[:] = []
            cur = open_section(1, "본문", None)
            heading_stack.append((1, cur.index))
        cur.text += line + "\n"
        t = count_tokens(line)
        if t > max_tokens:
            flush()
            for piece in _split_long(line, max_tokens, overlap):
                if len(h.chunks) >= max_chunks:
                    break
                h.chunks.append(Chunk(len(h.chunks), piece, count_tokens(piece), cur.heading_path, page, cur.index))
            continue
        if buf_tokens + t > target and buf_tokens > 0:
            flush()
        buf.append(line)
        buf_tokens += t
    flush()
    close_section()

    h.sections = [s for s in h.sections if s.chunk_to >= s.chunk_from]
    if not h.chunks:
        return h
    # A document whose extractor produced no headings would otherwise have one giant
    # section, which makes the section leg useless exactly where it is needed most (PDFs).
    if len(h.sections) <= 1:
        h.sections = _synthesise_sections(h.chunks)
    return h


def _synthesise_sections(chunks: list[Chunk]) -> list[Section]:
    """Group a flat chunk list into sections: by page run where pages exist, else fixed runs."""
    groups: list[list[Chunk]] = []
    if any(c.page for c in chunks):
        current_page, run = None, []
        for c in chunks:
            if run and c.page != current_page and len(run) >= 2:
                groups.append(run)
                run = []
            current_page = c.page
            run.append(c)
        if run:
            groups.append(run)
    if not groups:
        groups = [chunks[i:i + SYNTH_SECTION_CHUNKS] for i in range(0, len(chunks), SYNTH_SECTION_CHUNKS)]

    sections: list[Section] = []
    for i, g in enumerate(groups):
        pages = [c.page for c in g if c.page]
        if pages:
            title = f"p.{pages[0]}" if pages[0] == pages[-1] else f"p.{pages[0]}–{pages[-1]}"
        else:
            title = f"본문 {i + 1}"
        s = Section(index=i, level=1, title=title, heading_path=title, parent=None, page=pages[0] if pages else None,
                    chunk_from=g[0].ordinal, chunk_to=g[-1].ordinal, text="\n".join(c.text for c in g))
        s.tokens = sum(c.tokens for c in g)
        sections.append(s)
        for c in g:
            c.section = i
            if not c.heading_path:
                c.heading_path = title
    return sections
