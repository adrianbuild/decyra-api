"""Task 5B.2 — schema-only extraction for the code-interpreter path.

The ONLY thing that ever leaves the house about an analysis upload is its
SCHEMA: the column names, the per-column dtype, and the row count. Every cell
VALUE is parsed locally and then discarded — it is never rendered into the
prompt, never sent to the LLM, and never persisted. The schema info is the
FIFTH content source and flows through the SAME PII routing/anonymisation
chokepoint as user text, history, RAG chunks and file text (see ``app/main.py``).

pandas runs here in the TRUSTED app process purely to inspect the user's own
file — the same trust level as the 5B.1 ``documents.extract_text`` openpyxl
parse, which already reads uploads in-process. It does NOT execute any
generated code; LLM-generated code only ever runs inside the Docker sandbox.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pandas as pd

from app import documents


@dataclass(frozen=True)
class DataSchema:
    """Schema-only view of an uploaded tabular file. Deliberately holds NO cell
    values — only column names, per-column dtype strings, and the row count."""

    columns: list[str]
    dtypes: dict[str, str]
    row_count: int


def extract_schema(filename: str, data: bytes) -> DataSchema:
    """Parse ``data`` for SCHEMA ONLY (column names, per-column dtype, row
    count) and discard every cell value.

    The file type is determined by content-sniffing (same allow-list as the
    analysis intake: XLSX via ``read_excel``, TXT/CSV via ``read_csv``), not by
    the (untrusted) filename. The returned ``DataSchema`` references no row
    data — the parsed DataFrame is local to this function and goes out of scope
    on return.
    """
    mime = documents.sniff_mime(data)
    if mime == documents.XLSX_MIME:
        df = pd.read_excel(io.BytesIO(data))
    else:
        # TXT/CSV (content-sniffed to TXT_MIME). Comma-separated by default —
        # the upstream analysis intake already restricted the type allow-list.
        df = pd.read_csv(io.BytesIO(data))

    columns = [str(c) for c in df.columns]
    dtypes = {str(c): str(df[c].dtype) for c in df.columns}
    row_count = int(len(df))
    return DataSchema(columns=columns, dtypes=dtypes, row_count=row_count)


def schema_for_prompt(schema: DataSchema) -> str:
    """Render a compact schema block: column names + dtypes + row count and
    NOTHING else (no cell values, no sample rows). This is exactly what is
    placed into the LLM context as the fifth content source."""
    lines = [
        f"Datei-Schema (nur Struktur, keine Werte): {schema.row_count} Zeilen.",
        "Spalten:",
    ]
    for col in schema.columns:
        lines.append(f"- {col} ({schema.dtypes.get(col, 'unknown')})")
    return "\n".join(lines)
