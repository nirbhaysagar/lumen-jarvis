"""Markdown parser using markdown-it-py (MIT)."""
from pathlib import Path
import re
from markdown_it import MarkdownIt
from app.ingest.models import ParsedDoc


def parse_markdown(raw_bytes: bytes, filename: str = "note.md", title: str | None = None) -> ParsedDoc:
    raw_text = raw_bytes.decode("utf-8", errors="replace").strip()
    md = MarkdownIt()
    tokens = md.parse(raw_text)

    headings: list[dict] = []
    detected_title: str | None = None

    for i, tok in enumerate(tokens):
        if tok.type == "heading_open" and i + 1 < len(tokens):
            level = int(tok.tag[1]) if len(tok.tag) > 1 and tok.tag[1].isdigit() else 1
            heading_text = tokens[i + 1].content.strip()
            if heading_text:
                headings.append({"level": level, "text": heading_text})
                if level == 1 and detected_title is None:
                    detected_title = heading_text

    # Clean up excessive markdown formatting while keeping headings and structure readable
    clean_text = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), raw_text)

    return ParsedDoc(
        text=clean_text.strip(),
        title=title or detected_title or Path(filename).stem,
        kind="file",
        metadata={
            "format": "markdown",
            "filename": filename,
            "headings": headings,
        },
    )
