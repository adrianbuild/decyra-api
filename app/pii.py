"""Task 4.5a — PII detection for sovereign routing.

Two layers, OR'd into a single boolean:
1. Local German regex recognizers (pure Python, unit-tested) for what
   Presidio lacks natively — the Steuer-ID (IdNr, with Mod-11 check digit)
   and a keyword-anchored Kundennummer.
2. Microsoft Presidio (German NLP) over HTTP for EMAIL/IBAN/PERSON/PHONE/…

The caller only needs a boolean + a status: a real detection vs a clean
text vs "couldn't check" (Presidio down) — the last drives the fail-safe
reroute, and is kept distinguishable from a real hit.

Honesty: detection is statistical. NER + regex have false negatives
(esp. company-specific Kundennummern, unusually formatted PII, English
names under the German model). This LOWERS leak risk; it does not
guarantee 100% recall. See PROGRESS > Security-Härtung.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

import httpx

from app.config import Settings

logger = logging.getLogger("decyra.pii")

_PRESIDIO_TIMEOUT = 5.0

PiiStatus = Literal["detected", "clean", "unavailable"]


@dataclass(frozen=True)
class PiiOutcome:
    status: PiiStatus
    detected: bool

    @property
    def needs_protection(self) -> bool:
        """Fail-safe: a real detection OR an unavailable check both mean we
        must protect (reroute to a sovereign model)."""
        return self.detected or self.status == "unavailable"


# --- Local German recognisers (Presidio's gap) -------------------------

# Candidate run of 11+ digits possibly grouped by single spaces.
_DIGIT_RUN = re.compile(r"(?<!\d)\d[\d ]{9,21}\d(?!\d)")

# Kundennummer: keyword-anchored so false positives stay low (a bare number
# does not trip it). Matches "Kundennummer: 12345", "Kd-Nr. 4711", "KdNr A123".
_KUNDENNUMMER = re.compile(
    r"\b(?:kunden(?:nummer|nr)|kd[ .\-]?nr)\b[\s:.\-]*([A-Za-z]?\d{3,})",
    re.IGNORECASE,
)


def steuer_id_check_digit(first_ten: str) -> int:
    """ISO 7064 MOD 11,10 check digit used by the German IdNr."""
    product = 10
    for ch in first_ten:
        s = (int(ch) + product) % 10
        if s == 0:
            s = 10
        product = (s * 2) % 11
    return (11 - product) % 10


def is_valid_steuer_id(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit():
        return False
    return steuer_id_check_digit(digits[:10]) == int(digits[10])


def has_local_pii(text: str) -> bool:
    """Steuer-ID (checksum-validated) or a keyword-anchored Kundennummer."""
    if _KUNDENNUMMER.search(text):
        return True
    for m in _DIGIT_RUN.finditer(text):
        digits = m.group().replace(" ", "")
        if len(digits) == 11 and is_valid_steuer_id(digits):
            return True
    return False


# --- Presidio (HTTP) ---------------------------------------------------


def presidio_detect(text: str, settings: Settings) -> bool:
    """True if Presidio finds any entity at/above the threshold. Raises on
    any failure (unconfigured URL, network, non-200) so the caller can mark
    the check 'unavailable' and fail-safe."""
    if not settings.presidio_url:
        raise RuntimeError("presidio_url not configured")
    resp = httpx.post(
        f"{settings.presidio_url.rstrip('/')}/analyze",
        json={
            "text": text,
            "language": "de",
            "score_threshold": settings.pii_score_threshold,
        },
        timeout=_PRESIDIO_TIMEOUT,
    )
    resp.raise_for_status()
    return len(resp.json()) > 0


def contains_pii(text: str, settings: Settings) -> PiiOutcome:
    """Local regex first (works even if Presidio is down); then Presidio.
    A local hit short-circuits to 'detected'. A Presidio failure yields
    'unavailable' (caller fail-safe reroutes)."""
    if has_local_pii(text):
        return PiiOutcome("detected", True)
    try:
        hit = presidio_detect(text, settings)
    except Exception as e:  # noqa: BLE001 — any failure => can't check
        logger.warning("Presidio PII check unavailable: %s", e)
        return PiiOutcome("unavailable", False)
    return PiiOutcome("detected", True) if hit else PiiOutcome("clean", False)
