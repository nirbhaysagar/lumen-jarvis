"""Plain text parser."""
from pathlib import Path
from app.ingest.models import ParsedDoc


def parse_text(raw_bytes: bytes, filename: str = "document.txt", title: str | None = None) -> ParsedDoc:
    text = raw_bytes.decode("utf-8", errors="replace").strip()
    return ParsedDoc(
        text=text,
        title=title or Path(filename).stem,
        kind="file",
        metadata={"format": "text", "filename": filename},
    )
