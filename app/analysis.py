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
import re
from dataclasses import dataclass
from typing import Callable, Protocol

import pandas as pd

from app import documents
from app.sandbox.runner import SandboxResult


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


# =====================================================================
# Sub-Task 4 — codegen + bounded retry
# =====================================================================

# The system prompt is the FIRST line of defence (the SANDBOX is the real
# boundary). It instructs the model to write ONLY pandas/matplotlib code that
# reads the data from the global ``INPUT_PATH`` and writes exactly ONE chart to
# the global ``CHART_PATH`` — no network, no imports beyond pandas/matplotlib.
# It is given the SCHEMA only (column names + dtypes + row count), never a cell
# value, so nothing the model sees can echo private data back into the code.
_CODEGEN_SYSTEM = (
    "Du bist ein Python-Datenanalyse-Codegenerator. Schreibe AUSSCHLIESSLICH "
    "Python-Code (pandas + matplotlib), KEINE Erklaerungen, KEINE Markdown-"
    "Codefences.\n"
    "Harte Regeln:\n"
    "- Lies die Daten NUR aus der globalen Variable INPUT_PATH "
    "(z. B. pandas.read_csv(INPUT_PATH) oder pandas.read_excel(INPUT_PATH)).\n"
    "- Speichere GENAU EIN Diagramm in die Datei unter der globalen Variable "
    "CHART_PATH (z. B. plt.savefig(CHART_PATH)).\n"
    "- Verwende KEINEN Netzwerkzugriff (no network).\n"
    "- Importiere NICHTS ausser pandas und matplotlib.\n"
    "- Verwende NUR die unten angegebenen Spalten (Schema). Es liegen dir KEINE "
    "Zellwerte vor.\n"
    "- Gib ausschliesslich lauffaehigen Python-Code zurueck."
)


def build_codegen_messages(schema: DataSchema, question: str) -> list[dict]:
    """System prompt (run contract) + schema block (SCHEMA ONLY) + user question.

    No cell value can enter here: the only data input is a ``DataSchema``, which
    by construction holds column names, dtypes and a row count and nothing else.
    """
    return [
        {"role": "system", "content": _CODEGEN_SYSTEM},
        {"role": "system", "content": schema_for_prompt(schema)},
        {"role": "user", "content": question},
    ]


# A ``<generated>`` traceback frame line: ``File "<generated>", line 12``.
_GEN_LINE_RE = re.compile(r'File "<generated>", line (\d+)')
# The final ``ExceptionClass: message`` line of a Python traceback. We keep ONLY
# the class (a Python type identifier — never user data); the message frequently
# quotes the offending DATA ROW / cell value, so it is dropped entirely.
_EXC_CLASS_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*Error|[A-Za-z_][A-Za-z0-9_.]*Exception)\b", re.MULTILINE)

_GENERIC_FIX_HINT = (
    "Die vorige Code-Ausfuehrung schlug fehl. Korrigiere den Code anhand des "
    "oben gezeigten Schemas (Spaltennamen/Datentypen) und der Lauf-Regeln. "
    "Gib nur den korrigierten Python-Code zurueck."
)


def build_retry_feedback(result: SandboxResult) -> str:
    """Construct SCHEMA-SAFE retry feedback from a failed sandbox run.

    A pandas traceback routinely quotes the offending DATA ROW / cell value, and
    in strict mode the box ran REAL-column code, so ``result.output`` may contain
    real identifiers. The cloud LLM must therefore NEVER see the raw traceback or
    any cell value. We forward ONLY:

    * the exception CLASS name (a Python type identifier, e.g. ``KeyError``), and
    * the failing line number within the GENERATED code (``<generated>`` frame),

    plus a generic instruction to fix using the schema already shown. The
    exception MESSAGE text is dropped (it may quote data), and we never restate
    raw column names here. For ``timeout``/``killed``/``no_chart`` we emit a
    fixed safe generic message. The full raw output may be logged/shown to the
    USER (their own local data) but is NEVER part of this feedback string.
    """
    if result.status == "timeout":
        return "Die Ausfuehrung hat das Zeitlimit ueberschritten (execution timed out). " + _GENERIC_FIX_HINT
    if result.status == "killed":
        return "Die Ausfuehrung hat das Speicherlimit ueberschritten (exceeded memory). " + _GENERIC_FIX_HINT
    if result.status == "no_chart":
        return "Der Code hat kein Diagramm erzeugt (no chart produced) — speichere genau ein Chart nach CHART_PATH. " + _GENERIC_FIX_HINT

    # status == "error" (or anything else non-ok): parse SAFE parts only.
    output = result.output or ""
    parts: list[str] = []

    line_m = _GEN_LINE_RE.search(output)
    if line_m:
        parts.append(f"Fehler in Zeile {line_m.group(1)} des generierten Codes")

    class_m = _EXC_CLASS_RE.search(output)
    if class_m:
        # Strip any module qualifier, keep the bare type identifier.
        exc_class = class_m.group(1).split(".")[-1]
        parts.append(f"Ausnahmetyp: {exc_class}")

    if parts:
        return ". ".join(parts) + ". " + _GENERIC_FIX_HINT
    # No recognisable safe token → degrade to generic guidance (still no raw
    # output, so no value can leak).
    return "Die Code-Ausfuehrung schlug mit einem Fehler fehl. " + _GENERIC_FIX_HINT


class _Anonymizerish(Protocol):
    def deanonymize(self, text: str) -> str: ...


class _Runnerish(Protocol):
    def run(self, *, file_bytes: bytes, filename: str, code: str) -> SandboxResult: ...


@dataclass(frozen=True)
class CodegenOutcome:
    """Result of the bounded codegen+run loop. ``ok`` true => ``chart_png`` set.
    Never carries a raw traceback meant for the LLM — ``error_feedback`` is the
    last SCHEMA-SAFE feedback (safe to log); the raw box output stays in
    ``last_output`` for USER-facing display/logging only, never the cloud."""

    ok: bool
    chart_png: bytes | None
    attempts: int
    status: str
    error_feedback: str
    last_output: str


def generate_and_run(
    *,
    schema: DataSchema,
    question: str,
    file_bytes: bytes,
    filename: str,
    complete_fn: Callable[[list[dict]], str],
    runner: _Runnerish,
    anonymizer: _Anonymizerish | None,
    max_retries: int = 3,
) -> CodegenOutcome:
    """Bounded retry orchestrator: up to ``max_retries`` attempts of
    (LLM -> code -> de-anonymise (strict) -> sandbox run).

    * ``complete_fn(messages) -> code``: calls the LLM through the EXISTING gated
      path (so sovereignty/fallback are preserved and ``stub_llm`` captures it).
      The messages it is handed in STRICT mode already carry placeholder column
      names; the code that comes back therefore references placeholders.
    * STRICT-MODE CODE DE-ANONYMISATION: before running, if an ``anonymizer`` is
      present we ``anonymizer.deanonymize(code)`` so the code references the REAL
      columns and actually runs against the real file. In sovereign mode
      (``anonymizer is None``) the code runs as-is.
    * On ``status == "ok"`` -> success, return the chart bytes.
    * Otherwise build SCHEMA-SAFE feedback (``build_retry_feedback``) and append
      it for the next attempt — the raw traceback / cell values NEVER recur in
      the LLM messages.
    * After ``max_retries`` failures -> a clean failure outcome (no exception).
    """
    messages = build_codegen_messages(schema, question)
    last_result: SandboxResult | None = None

    for attempt in range(1, max_retries + 1):
        code = complete_fn(messages)
        # STRICT-MODE de-anonymisation: placeholder columns -> real columns, with
        # the SAME anonymizer used for chat responses. Sovereign: run verbatim.
        run_code = anonymizer.deanonymize(code) if anonymizer is not None else code

        result = runner.run(file_bytes=file_bytes, filename=filename, code=run_code)
        last_result = result

        if result.status == "ok" and result.chart_png is not None:
            return CodegenOutcome(
                ok=True,
                chart_png=result.chart_png,
                attempts=attempt,
                status="ok",
                error_feedback="",
                last_output=result.output,
            )

        # Failure: append SCHEMA-SAFE feedback for the next attempt. We append it
        # as an assistant turn (the just-generated code) + a user turn (the safe
        # fix request) so the model sees what to repair without ANY raw data.
        feedback = build_retry_feedback(result)
        if attempt < max_retries:
            messages = messages + [
                {"role": "assistant", "content": code},
                {"role": "user", "content": feedback},
            ]

    # Exhausted: clean failure outcome (the endpoint maps this to an HTTP-200
    # user-facing error). The raw output stays local; only schema-safe feedback
    # is retained.
    status = last_result.status if last_result is not None else "error"
    return CodegenOutcome(
        ok=False,
        chart_png=None,
        attempts=max_retries,
        status=status,
        error_feedback=build_retry_feedback(last_result)
        if last_result is not None
        else "",
        last_output=last_result.output if last_result is not None else "",
    )
