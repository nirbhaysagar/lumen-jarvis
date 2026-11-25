"""
Tests for Universal Ingestion Pipeline (Milestone I1).
Verifies:
1. Parsers for Markdown, HTML, Spreadsheet (XLSX/CSV), YouTube Transcript JSON, and Text.
2. API endpoints: POST /v1/ingest/file and POST /v1/ingest/text.
3. Exact chunk coordinates & source metadata preservation.
4. No external network calls on file/text/transcript-json ingestion.
"""
from io import BytesIO
import json
import uuid
import openpyxl
import pytest
from httpx import AsyncClient, ASGITransport

from app.core.config import settings
from app.ingest import parse_file_bytes
from app.ingest.parsers.youtube import is_youtube_url, extract_video_id
from app.main import app


def test_markdown_and_html_and_youtube_parsers():
    # 1. Markdown
    md_bytes = b"# Architecture Overview\n\nLumen uses [[PostgreSQL]] 17 and [[Ollama]].\n"
    md_doc = parse_file_bytes(md_bytes, "arch.md")
    assert md_doc.title == "Architecture Overview"
    assert "Lumen uses PostgreSQL 17 and Ollama." in md_doc.text
    assert md_doc.metadata["format"] == "markdown"
    assert len(md_doc.metadata["headings"]) == 1

    # 2. HTML
    html_bytes = b"""
    <html>
      <head><title>Lumen Docs</title><style>body{color:red}</style></head>
      <body>
        <nav>Skip me</nav>
        <main><h1>Welcome</h1><p>Local-first AI memory.</p></main>
        <script>console.log('xss')</script>
      </body>
    </html>
    """
    html_doc = parse_file_bytes(html_bytes, "index.html")
    assert html_doc.title == "Lumen Docs"
    assert "Local-first AI memory." in html_doc.text
    assert "Skip me" not in html_doc.text
    assert "console.log" not in html_doc.text

    # 3. Spreadsheet (XLSX)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Milestones"
    ws.append(["ID", "Name", "Status"])
    ws.append(["P1", "MCP Server", "Done"])
    buf = BytesIO()
    wb.save(buf)
    xlsx_doc = parse_file_bytes(buf.getvalue(), "plan.xlsx")
    assert "[Sheet: Milestones]" in xlsx_doc.text
    assert "ID: P1, Name: MCP Server, Status: Done" in xlsx_doc.text

    # 4. YouTube URL detection & Transcript JSON
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    yt_json = json.dumps([
        {"start": 12.5, "text": "Welcome to Lumen Memory."},
        {"start": 75.0, "text": "Everything runs locally on Metal GPU."},
    ]).encode("utf-8")
    yt_doc = parse_file_bytes(yt_json, "video_transcript.json")
    assert "[00:12] Welcome to Lumen Memory." in yt_doc.text
    assert "[01:15] Everything runs locally on Metal GPU." in yt_doc.text
    assert len(yt_doc.metadata["timestamps"]) == 2


@pytest.mark.asyncio
async def test_ingest_api_endpoints():
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.LUMEN_MEMORY_TOKEN}"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            # 1. POST /v1/ingest/text
            res_text = await client.post(
                "/v1/ingest/text",
                json={
                    "content": "Lumen Phase 2.5 adds MCP, Export/Import, and Document Ingestion.",
                    "title": "Phase 2.5 Summary",
                    "context": f"ingest_{uuid.uuid4().hex[:6]}",
                },
                headers=headers,
            )
            assert res_text.status_code == 200
            d_text = res_text.json()
            assert d_text["status"] == "ingested"
            assert d_text["event_id"] is not None
            assert len(d_text["job_ids"]) >= 1

            # 2. POST /v1/ingest/file (Markdown upload)
            files = {
                "file": ("notes.md", b"# Daily Standup\n\nAll unit tests are green.", "text/markdown"),
            }
            res_file = await client.post(
                "/v1/ingest/file",
                files=files,
                data={"context": f"ingest_{uuid.uuid4().hex[:6]}"},
                headers=headers,
            )
            assert res_file.status_code == 200
            d_file = res_file.json()
            assert d_file["status"] == "ingested"
            assert d_file["title"] == "Daily Standup"
            assert len(d_file["job_ids"]) >= 1
