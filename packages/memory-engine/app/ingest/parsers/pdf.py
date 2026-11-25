"""PDF parser using pypdf (BSD-3-Clause)."""
from io import BytesIO
from pathlib import Path
from pypdf import PdfReader
from app.ingest.models import ParsedDoc


def parse_pdf(raw_bytes: bytes, filename: str = "document.pdf", title: str | None = None) -> ParsedDoc:
    reader = PdfReader(BytesIO(raw_bytes))
    pages_text: list[str] = []
    page_spans: list[dict] = []
    cursor = 0

    for idx, page in enumerate(reader.pages, start=1):
        extracted = (page.extract_text() or "").strip()
        if not extracted:
            continue
        section = f"[Page {idx}]\n{extracted}"
        start = cursor
        pages_text.append(section)
        cursor += len(section) + 2  # +2 for '\n\n' joiner
        page_spans.append({"page": idx, "char_start": start, "char_end": cursor - 2})

    full_text = "\n\n".join(pages_text).strip()
    meta_title = None
    if reader.metadata and reader.metadata.title:
        meta_title = str(reader.metadata.title).strip()

    return ParsedDoc(
        text=full_text,
        title=title or meta_title or Path(filename).stem,
        kind="file",
        metadata={
            "format": "pdf",
            "filename": filename,
            "page_count": len(reader.pages),
            "page_spans": page_spans,
        },
    )
