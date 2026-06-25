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
import pdfplumber
from docx import Document as DocxDocument

PDF_MIME = "application/pdf"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
TXT_MIME = "text/plain"

# Canonical MIME -> file extension used for the storage key (never the user's).
EXT = {PDF_MIME: ".pdf", DOCX_MIME: ".docx", TXT_MIME: ".txt"}

_MAX_FILENAME_LEN = 255


class UnsupportedType(Exception):
    """File content is not an allowed type (PDF/DOCX/TXT)."""


class ExtractionError(Exception):
    """File sniffed as an allowed type but could not be parsed (corrupt)."""


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


def sniff_mime(data: bytes) -> str:
    """Return a canonical MIME from the bytes, or raise UnsupportedType.

    Order: PDF (magic) -> real DOCX (structure) -> TXT (valid UTF-8 with no
    binary magic). Any other recognised binary is rejected.
    """
    kind = filetype.guess(data)
    if kind is not None and kind.mime == PDF_MIME:
        return PDF_MIME
    if _is_real_docx(data):
        return DOCX_MIME
    if kind is None:
        # No binary signature: accept only if it is genuine UTF-8 text.
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise UnsupportedType("not a supported document type") from e
        return TXT_MIME
    # A recognised binary that is not PDF/DOCX (png, elf, plain zip, ...).
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
        else:  # TXT
            text = data.decode("utf-8")
    except Exception as e:  # noqa: BLE001 — any parser failure = not extractable
        raise ExtractionError(str(e)) from e
    status = "ok" if text.strip() else "no_text"
    return text, status


def sanitize_filename(name: str | None) -> str:
    """Display-only filename: basename, control chars stripped, length capped.
    NEVER used to build a storage path (the key is UUID-derived)."""
    base = (name or "document").replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[\x00-\x1f]", "", base).strip()
    return (cleaned or "document")[:_MAX_FILENAME_LEN]
