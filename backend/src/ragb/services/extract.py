"""Text extraction for untrusted knowledge documents.

``extract`` preserves the original API but executes all parsing in a constrained
child process. The in-process implementation is private and used only by that
child. This prevents malformed PDF/Office/HTML files from sharing the worker's
memory, CPU budget, file-descriptor table or network capability.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field

from ragb.config import get_settings

MAX_ARCHIVE_ENTRIES = 5000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_PPT_SLIDES = 500
MAX_XLSX_SHEETS = 100
MAX_XLSX_ROWS = 2000
MAX_XLSX_COLS = 200
MAX_INPUT_BYTES = 32 * 1024 * 1024


@dataclass
class Extracted:
    text: str
    pages: list[tuple[int, str]] = field(default_factory=list)
    title: str = ""


def _clean(t: str) -> str:
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _guard_zip(data: bytes) -> None:
    """Reject Office zip bombs before handing bytes to high-level parsers."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise ValueError("archive_too_many_entries")
            total = 0
            for info in infos:
                total += max(0, int(info.file_size or 0))
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("archive_uncompressed_too_large")
                compressed = max(1, int(info.compress_size or 0))
                if info.file_size > 10 * 1024 * 1024 and info.file_size / compressed > 200:
                    raise ValueError("archive_suspicious_compression")
    except zipfile.BadZipFile as e:
        raise ValueError("invalid_office_archive") from e


def _extract_in_process(data: bytes, mime: str, filename: str = "") -> Extracted:
    """Parser implementation. Call only from ``ragb.services.extract_worker``."""
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("parser_input_too_large")
    name = (filename or "").lower()
    mime = (mime or "").lower()
    if mime == "application/pdf" or name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError("pdf_too_many_pages")
        pages = []
        for i, p in enumerate(reader.pages):
            try:
                pages.append((i + 1, _clean(p.extract_text() or "")))
            except Exception:
                pages.append((i + 1, ""))
        text = "\n\n".join(f"[page {n}]\n{t}" for n, t in pages if t)
        title = ""
        try:
            title = str((reader.metadata or {}).get("/Title") or "")
        except Exception:
            pass
        if not text.strip():
            raise ValueError("pdf_no_text")
        return Extracted(text=text, pages=pages, title=title)
    if name.endswith(".docx") or "wordprocessingml" in mime:
        _guard_zip(data)
        import docx

        d = docx.Document(io.BytesIO(data))
        parts = []
        for p in d.paragraphs:
            style = (p.style.name or "").lower() if p.style is not None else ""
            if style.startswith("heading") and p.text.strip():
                level = "".join(ch for ch in style if ch.isdigit()) or "1"
                parts.append("#" * min(int(level), 4) + " " + p.text.strip())
            elif p.text.strip():
                parts.append(p.text)
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return Extracted(text=_clean("\n".join(parts)))
    if name.endswith(".pptx") or "presentationml" in mime:
        _guard_zip(data)
        from pptx import Presentation

        prs = Presentation(io.BytesIO(data))
        if len(prs.slides) > MAX_PPT_SLIDES:
            raise ValueError("pptx_too_many_slides")
        parts = []
        for i, slide in enumerate(prs.slides, 1):
            parts.append(f"## Slide {i}")
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    parts.append(shape.text_frame.text)
        return Extracted(text=_clean("\n".join(parts)))
    if name.endswith((".xlsx", ".xlsm")) or "spreadsheetml" in mime:
        _guard_zip(data)
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        if len(wb.worksheets) > MAX_XLSX_SHEETS:
            raise ValueError("xlsx_too_many_sheets")
        parts = []
        for ws in wb.worksheets:
            parts.append(f"## Sheet {ws.title}")
            for row in ws.iter_rows(values_only=True, max_row=MAX_XLSX_ROWS, max_col=MAX_XLSX_COLS):
                cells = [str(c) for c in row if c is not None and str(c).strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return Extracted(text=_clean("\n".join(parts)))
    if name.endswith((".html", ".htm")) or "html" in mime:
        return Extracted(text=html_to_text(data.decode("utf-8", errors="replace")))
    if name.endswith((".md", ".markdown", ".txt", ".csv", ".json")) or mime.startswith("text/") or not mime:
        return Extracted(text=_clean(data.decode("utf-8", errors="replace")))
    raise ValueError("unsupported_type")


def _sandbox_env() -> dict[str, str]:
    s = get_settings()
    keep = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "RAGB_PARSER_MEMORY_MB": str(max(128, s.parser_memory_mb)),
        "RAGB_PARSER_CPU_SECONDS": str(max(2, min(60, s.parser_timeout_seconds))),
        "RAGB_PARSER_MAX_OUTPUT_BYTES": str(max(1024 * 1024, s.parser_max_output_bytes)),
        "RAGB_PARSER_MAX_INPUT_BYTES": str(MAX_INPUT_BYTES),
    }
    return keep


def extract(data: bytes, mime: str, filename: str = "") -> Extracted:
    """Extract text in a resource-limited, no-network child process."""
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("parser_input_too_large")
    s = get_settings()
    cmd = [sys.executable, "-m", "ragb.services.extract_worker", mime or "", filename or ""]
    try:
        proc = subprocess.run(
            cmd,
            input=data,
            capture_output=True,
            cwd="/tmp" if os.path.isdir("/tmp") else None,
            env=_sandbox_env(),
            timeout=max(2, s.parser_timeout_seconds + 2),
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ValueError("parser_timeout") from e
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace")[:300].strip()
        raise ValueError(detail or "parser_failed")
    if len(proc.stdout) > s.parser_max_output_bytes:
        raise ValueError("parser_output_too_large")
    try:
        payload = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError("parser_invalid_output") from e
    if not payload.get("ok"):
        raise ValueError(str(payload.get("error") or "parser_failed")[:500])
    return Extracted(
        text=str(payload.get("text") or ""),
        pages=[(int(p[0]), str(p[1])) for p in (payload.get("pages") or [])],
        title=str(payload.get("title") or ""),
    )


def html_to_text(html: str) -> str:
    try:
        from readability import Document

        doc = Document(html)
        html_main = doc.summary()
        title = doc.title()
    except Exception:
        html_main, title = html, ""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_main, "lxml")
    for t in soup(["script", "style", "nav", "footer", "noscript"]):
        t.decompose()
    for h in soup.find_all(["h1", "h2", "h3"]):
        h.insert_before("\n## ")
        h.insert_after("\n")
    text = soup.get_text("\n")
    return _clean((title + "\n\n" if title else "") + text)
