"""Document validation + text extraction (Task 5.1).

Type detection is CONTENT-based and authoritative: the client filename and
Content-Type are never trusted. A binary renamed ``.pdf``/``.txt`` is rejected;
a bare or foreign ZIP renamed ``.docx`` is rejected (we verify the real DOCX
structure, not just "is a zip").

5.1 only stores the extracted text. NOTE (PII boundary): that text is real
potential PII living in the RLS-protected, deletable ``documents`` table — like
``messages`` (4.5b). No LLM call happens here, so no PII routing applies YET.
Task 5.2/5.3, which pull document text into the chat context, MUST route it
through the same Sovereign/Strict logic as user input ("re-evaluate for RAG").
"""

import io
import re
import zipfile

import filetype
import openpyxl
import pdfplumber
from docx import Document as DocxDocument

PDF_MIME = "application/pdf"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
XLSX_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
TXT_MIME = "text/plain"

# Canonical MIME -> file extension used for the storage key (never the user's).
EXT = {PDF_MIME: ".pdf", DOCX_MIME: ".docx", XLSX_MIME: ".xlsx", TXT_MIME: ".txt"}

_MAX_FILENAME_LEN = 255

# Binary guard for the text path (csv/txt have no magic bytes): once a byte
# stream has no recognised binary signature, we still refuse it as text if it
# looks like binary garbage. cp1252/latin-1 decodes almost ANY byte sequence,
# so a strict-UTF-8 failure no longer protects us — this guard does instead.
_TEXT_WHITESPACE = frozenset(b"\t\n\r\f\v")
# Reject if > this fraction of the sampled bytes are non-printable controls.
_MAX_CONTROL_RATIO = 0.10


class UnsupportedType(Exception):
    """File content is not an allowed type (PDF/DOCX/XLSX/TXT)."""


class ExtractionError(Exception):
    """File sniffed as an allowed type but could not be parsed (corrupt)."""


class TooLargeExtract(Exception):
    """Extracted text exceeds the configured character cap (maps to HTTP 413)."""


def _is_real_docx(data: bytes) -> bool:
    """Hard DOCX check. A real Word document is a ZIP that contains
    ``word/document.xml`` AND a ``[Content_Types].xml`` declaring the
    wordprocessingml main document. A bare/foreign ZIP renamed ``.docx`` fails
    this — that is the requested sharpening over "it's just a ZIP"."""
    if data[:2] != b"PK":  # not even a ZIP local-file header
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                return False
            content_types = z.read("[Content_Types].xml").decode("utf-8", "ignore")
            return "wordprocessingml.document" in content_types
    except (zipfile.BadZipFile, KeyError, OSError):
        return False


def _is_real_xlsx(data: bytes) -> bool:
    """Hard XLSX check, parallel to ``_is_real_docx``. A real workbook is a ZIP
    containing ``xl/workbook.xml`` AND ``[Content_Types].xml`` declaring the
    spreadsheetml content type. A bare/foreign ZIP renamed ``.xlsx`` fails."""
    if data[:2] != b"PK":  # not even a ZIP local-file header
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            if "xl/workbook.xml" not in names or "[Content_Types].xml" not in names:
                return False
            content_types = z.read("[Content_Types].xml").decode("utf-8", "ignore")
            return "spreadsheetml" in content_types
    except (zipfile.BadZipFile, KeyError, OSError):
        return False


def _decode_text(data: bytes) -> str:
    """Decode candidate text bytes, or raise UnsupportedType if they look binary.

    Binary guard (runs BEFORE any decode): reject on a NUL byte or a high ratio
    of non-printable control chars (tab/newline/CR/FF/VT are fine). This is the
    protection that strict UTF-8 used to provide implicitly — cp1252/latin-1
    would otherwise silently "accept" binary garbage. Only after the guard:
    decode UTF-8 first, falling back to cp1252/latin-1 (German cp1252 text is a
    valid document; it is no longer rejected just for not being UTF-8)."""
    if b"\x00" in data:
        raise UnsupportedType("binary content (NUL byte) is not text")
    if data:
        controls = sum(
            1 for b in data if b < 0x20 and b not in _TEXT_WHITESPACE
        )
        if controls / len(data) > _MAX_CONTROL_RATIO:
            raise UnsupportedType("binary content (control chars) is not text")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def sniff_mime(data: bytes) -> str:
    """Return a canonical MIME from the bytes, or raise UnsupportedType.

    Order: PDF (magic) -> real XLSX/DOCX (ZIP structure) -> TXT/CSV (text that
    survives the binary guard). xlsx is a ZIP like docx, so both structure
    checks run before any text handling. Any other recognised binary is
    rejected. CSV has no magic, so it rides the text path behind the guard.
    """
    kind = filetype.guess(data)
    if kind is not None and kind.mime == PDF_MIME:
        return PDF_MIME
    # Both OOXML types present as a ZIP; disambiguate by real structure.
    if _is_real_xlsx(data):
        return XLSX_MIME
    if _is_real_docx(data):
        return DOCX_MIME
    if kind is None:
        # No binary signature: accept as text only if it survives the guard
        # (this is where csv/txt, incl. cp1252 German, are admitted).
        _decode_text(data)
        return TXT_MIME
    # A recognised binary that is not PDF/DOCX/XLSX (png, elf, plain zip, ...).
    raise UnsupportedType(f"unsupported type: {kind.mime}")


def extract_text(mime: str, data: bytes) -> tuple[str, str]:
    """Return (text, status). status='no_text' when nothing is extractable
    (e.g. a scanned image PDF) — stored, not rejected; 5.2 skips no_text and
    OCR can fill it later. Raises ExtractionError on a corrupt file."""
    try:
        if mime == PDF_MIME:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        elif mime == DOCX_MIME:
            doc = DocxDocument(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs)
        elif mime == XLSX_MIME:
            text = _extract_xlsx(data)
        else:  # TXT / CSV (text path; cp1252 fallback inside _decode_text)
            text = _decode_text(data)
    except Exception as e:  # noqa: BLE001 — any parser failure = not extractable
        raise ExtractionError(str(e)) from e
    status = "ok" if text.strip() else "no_text"
    return text, status


def _extract_xlsx(data: bytes) -> str:
    """Flatten every worksheet to text. ``data_only=True`` returns last computed
    values (not formula strings). Sheets are separated by a title header line;
    each row joins its non-empty cell values with tabs."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        parts: list[str] = []
        for ws in wb.worksheets:
            parts.append(f"# {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None and str(c) != ""]
                if cells:
                    parts.append("\t".join(cells))
        return "\n".join(parts)
    finally:
        wb.close()


def enforce_extract_cap(text: str, max_chars: int) -> None:
    """Raise TooLargeExtract when the extracted text exceeds ``max_chars``.
    Pure check (no truncation) so the caller can map it to HTTP 413."""
    if len(text) > max_chars:
        raise TooLargeExtract(f"extracted text {len(text)} > cap {max_chars}")


def sanitize_filename(name: str | None) -> str:
    """Display-only filename: basename, control chars stripped, length capped.
    NEVER used to build a storage path (the key is UUID-derived)."""
    base = (name or "document").replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[\x00-\x1f]", "", base).strip()
    return (cleaned or "document")[:_MAX_FILENAME_LEN]
