"""HTML parser using BeautifulSoup4 (MIT)."""
from pathlib import Path
from bs4 import BeautifulSoup
from app.ingest.models import ParsedDoc


def parse_html(
    raw_bytes: bytes,
    filename: str = "page.html",
    title: str | None = None,
    url: str | None = None,
) -> ParsedDoc:
    html_str = raw_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_str, "html.parser")

    # Strip non-content elements
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()

    doc_title = None
    if soup.title and soup.title.string:
        doc_title = soup.title.string.strip()

    text_lines = [line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()]
    clean_text = "\n\n".join(text_lines)

    return ParsedDoc(
        text=clean_text,
        title=title or doc_title or (url or Path(filename).stem),
        kind="web" if url else "file",
        metadata={
            "format": "html",
            "filename": filename,
            "url": url,
        },
    )
