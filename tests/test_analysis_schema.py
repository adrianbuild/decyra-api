"""Task 5B.2 Sub-Task 3 — schema-only extraction (S6, part 1).

These are pure unit tests for ``app.analysis.extract_schema`` and
``schema_for_prompt``. The security-critical property proven here is that ONLY
the schema (column names + per-column dtype + row count) is kept — every cell
VALUE is discarded and never appears in the rendered prompt block. pandas runs
in the TRUSTED app process purely to inspect the user's own upload (same trust
level as the 5B.1 ``documents.extract_text`` openpyxl parse); it never executes
generated code (that only ever runs in the sandbox).
"""

from __future__ import annotations

import io

import openpyxl

from app import analysis


# --- 1. CSV: columns + row_count + dtypes, NO cell values --------------


def test_extract_schema_csv_has_no_values():
    csv = b"kunde,umsatz\nMueller GmbH,1000\nSchmidt AG,2000\n"
    schema = analysis.extract_schema("d.csv", csv)

    assert schema.columns == ["kunde", "umsatz"]
    assert schema.row_count == 2
    # umsatz is an all-integer column -> integer-ish dtype.
    assert "int" in schema.dtypes["umsatz"]

    blob = analysis.schema_for_prompt(schema)
    # Column names ARE present (they are what goes to the LLM)...
    assert "kunde" in blob and "umsatz" in blob
    # ...but no cell VALUE ever leaks into the prompt block.
    assert "Mueller" not in blob
    assert "Schmidt" not in blob
    assert "1000" not in blob
    assert "2000" not in blob


# --- 2. XLSX: same guarantee on a spreadsheet --------------------------


def _make_xlsx(rows: list[list]) -> bytes:
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(buf)
    return buf.getvalue()


def test_extract_schema_xlsx_has_no_values():
    xlsx = _make_xlsx(
        [["kunde", "umsatz"], ["Mueller GmbH", 1000], ["Schmidt AG", 2000]]
    )
    schema = analysis.extract_schema("d.xlsx", xlsx)

    assert schema.columns == ["kunde", "umsatz"]
    assert schema.row_count == 2
    assert "int" in schema.dtypes["umsatz"]
    # The string column reads back as pandas object dtype.
    assert "object" in schema.dtypes["kunde"]

    blob = analysis.schema_for_prompt(schema)
    assert "kunde" in blob and "umsatz" in blob
    assert "Mueller" not in blob
    assert "Schmidt" not in blob
    assert "1000" not in blob
    assert "2000" not in blob
