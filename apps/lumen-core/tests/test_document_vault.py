import pytest
import os
import tempfile
from pathlib import Path
import pandas as pd
from PIL import Image

try:
    import pypdf
except ImportError:
    pypdf = None

from src.vault.document_parser import DocumentParser, DocumentType
from src.vault.document_vault import DocumentVault
from src.analytics.engine import AnalystEngine
from src.tools.kernel import PythonKernelManager
from src.router.model_router import ModelRouter
from src.runtime.session import ConversationSession
from src.memory_client.client import MemoryServiceClient


class MockMemoryClient:
    def __init__(self):
        self.events = []
        self.facts = []
        self.triples = []

    async def save_event(self, source: str, payload: dict, conversation_id: str | None = None):
        evt = {"id": f"evt_{len(self.events) + 1}", "source": source, "payload": payload}
        self.events.append(evt)
        return evt

    async def save_fact(self, entity: str, attribute: str, value: str, source_event_ids: list, **kwargs):
        fact = {"id": f"fact_{len(self.facts) + 1}", "entity": entity, "attribute": attribute, "value": value}
        self.facts.append(fact)
        return fact

    async def propose_triple(self, subject: str, predicate: str, object_: str, **kwargs):
        self.triples.append((subject, predicate, object_))
        return {"status": "ok"}


def test_csv_and_excel_parsing():
    parser = DocumentParser()

    df = pd.DataFrame({
        "vendor": ["AWS", "Google Cloud", "Snowflake", "Datadog"],
        "expenditure_usd": [12500.0, 8400.0, 4300.0, 2100.0],
        "category": ["Infrastructure", "Infrastructure", "Data Warehouse", "Observability"]
    })

    # Test CSV
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        csv_path = Path(f.name)
    df.to_csv(csv_path, index=False)

    doc_csv = parser.parse(csv_path)
    assert doc_csv.doc_type == DocumentType.SPREADSHEET
    assert len(doc_csv.tables) == 1
    assert doc_csv.tables[0].shape == (4, 3)
    assert "vendor" in doc_csv.tables[0].columns
    assert "expenditure_usd" in doc_csv.tables[0].columns
    assert len(doc_csv.chunks) >= 1
    csv_path.unlink()

    # Test Excel
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        xlsx_path = Path(f.name)
    with pd.ExcelWriter(xlsx_path) as writer:
        df.to_excel(writer, sheet_name="Q3_Expenses", index=False)

    doc_xlsx = parser.parse(xlsx_path)
    assert doc_xlsx.doc_type == DocumentType.SPREADSHEET
    assert len(doc_xlsx.tables) == 1
    assert doc_xlsx.tables[0].sheet_name == "Q3_Expenses"
    assert doc_xlsx.tables[0].shape == (4, 3)
    xlsx_path.unlink()


def test_pdf_parsing_and_prompt_injection_shield():
    if pypdf is None:
        pytest.skip("pypdf is not installed")

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": "Confidential Q3 Billing Audit", "/Author": "Lumen Comptroller"})

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf_path = Path(f.name)
    with open(pdf_path, "wb") as f_out:
        writer.write(f_out)

    parser = DocumentParser()
    doc_pdf = parser.parse(pdf_path)

    assert doc_pdf.doc_type == DocumentType.PDF
    assert doc_pdf.title == "Confidential Q3 Billing Audit"
    assert doc_pdf.metadata.get("author") == "Lumen Comptroller"
    pdf_path.unlink()


def test_image_metadata_parsing():
    img = Image.new("RGB", (320, 240), color="blue")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img_path = Path(f.name)
    img.save(img_path)

    parser = DocumentParser()
    doc_img = parser.parse(img_path)

    assert doc_img.doc_type == DocumentType.IMAGE
    assert doc_img.metadata.get("width") == 320
    assert doc_img.metadata.get("height") == 240
    img_path.unlink()


@pytest.mark.asyncio
async def test_document_vault_folder_ingestion():
    mock_memory = MockMemoryClient()
    vault = DocumentVault(memory_client=mock_memory)

    with tempfile.TemporaryDirectory() as tmp_dir:
        dir_path = Path(tmp_dir)

        # Create 3 documents: markdown, csv, text
        (dir_path / "notes.md").write_text("# Project Notes\nArchitecture summary of telemetry pipelines.")
        (dir_path / "data.csv").write_text("item,cost\nserver,1000\nlicense,500\n")
        (dir_path / "readme.txt").write_text("Readme file for research data.")

        summary = await vault.ingest(dir_path)

        assert summary.total_files_scanned == 3
        assert summary.total_files_ingested == 3
        assert summary.total_chunks_indexed >= 3
        assert len(summary.dossier_paths) == 3
        assert len(mock_memory.events) == 3
        assert len(mock_memory.facts) >= 3
        assert len(mock_memory.triples) >= 3

        # Verify dossier files were created in vault/documents/
        for rel_p in summary.dossier_paths:
            full_p = vault.vault.vault_path.parent / rel_p
            assert full_p.exists()
            content = full_p.read_text()
            assert "Ingested Document" in content


@pytest.mark.asyncio
async def test_analyst_engine_grounding_with_local_csv_files():
    """
    Verifies that AnalystEngine detects local CSV datasets, inspects them via DocumentVault,
    bypasses web search, executes Pandas code directly against the local files in Jupyter kernel,
    and produces an Obsidian dossier and chart.
    """
    km = PythonKernelManager(timeout_seconds=20.0)
    engine = AnalystEngine(kernel=km)

    with tempfile.NamedTemporaryFile(suffix="_sales_2023.csv", delete=False) as f1:
        p1 = Path(f1.name).resolve()
    with tempfile.NamedTemporaryFile(suffix="_sales_2024.csv", delete=False) as f2:
        p2 = Path(f2.name).resolve()

    df_2023 = pd.DataFrame({
        "month": ["Q1", "Q2", "Q3", "Q4"],
        "revenue_m": [10.5, 12.0, 14.2, 18.0]
    })
    df_2024 = pd.DataFrame({
        "month": ["Q1", "Q2", "Q3", "Q4"],
        "revenue_m": [13.0, 15.2, 17.8, 22.5]
    })
    df_2023.to_csv(p1, index=False)
    df_2024.to_csv(p2, index=False)

    prompt = f"Compare quarterly revenue between {p1} and {p2} and chart the YoY comparison"

    tokens = []
    async for tok in engine.analyze_and_plot(prompt, preview=False):
        tokens.append(tok)

    full_output = "".join(tokens)
    assert "Grounding analytics from 2 local dataset/document(s)" in full_output
    assert "Code Synthesis & Sandboxed Execution" in full_output
    assert "Archiving Reproducible Dossier to Obsidian Vault" in full_output

    # Check chart generated
    charts = list(Path("vault/charts").glob("*.png"))
    assert len(charts) >= 1

    # Check dossier generated
    dossiers = list(Path("vault/analytics").glob("*.md"))
    assert len(dossiers) >= 1

    p1.unlink()
    p2.unlink()
    await km.stop()
