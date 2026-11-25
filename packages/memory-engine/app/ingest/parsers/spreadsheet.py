"""Spreadsheet parser using openpyxl (MIT) for .xlsx and csv stdlib for .csv."""
import csv
from io import BytesIO, StringIO
from pathlib import Path
import openpyxl
from app.ingest.models import ParsedDoc


def parse_spreadsheet(raw_bytes: bytes, filename: str = "sheet.xlsx", title: str | None = None) -> ParsedDoc:
    ext = Path(filename).suffix.lower()
    sections: list[str] = []

    if ext == ".csv":
        text_data = raw_bytes.decode("utf-8", errors="replace")
        reader = csv.reader(StringIO(text_data))
        rows = list(reader)
        if rows:
            headers = rows[0]
            lines = []
            for idx, r in enumerate(rows[1:], start=2):
                pairs = [f"{headers[i] if i < len(headers) else f'col{i+1}'}: {val}" for i, val in enumerate(r) if val]
                if pairs:
                    lines.append(f"Row {idx}: " + ", ".join(pairs))
            sections.append("\n".join(lines))
    else:
        wb = openpyxl.load_workbook(BytesIO(raw_bytes), read_only=True, data_only=True)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue
            headers = [str(h) if h is not None else f"col{i+1}" for i, h in enumerate(rows[0])]
            lines = [f"[Sheet: {sheet_name}]"]
            for idx, r in enumerate(rows[1:], start=2):
                pairs = [
                    f"{headers[i] if i < len(headers) else f'col{i+1}'}: {val}"
                    for i, val in enumerate(r)
                    if val is not None and str(val).strip() != ""
                ]
                if pairs:
                    lines.append(f"Row {idx}: " + ", ".join(pairs))
            if len(lines) > 1:
                sections.append("\n".join(lines))
        wb.close()

    return ParsedDoc(
        text="\n\n".join(sections).strip(),
        title=title or Path(filename).stem,
        kind="file",
        metadata={"format": ext.lstrip(".") or "xlsx", "filename": filename},
    )
