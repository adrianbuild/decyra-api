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


# --- Strict mode (Task 4.5b): spans + reversible anonymisation ---------
#
# Strict anonymises PII to opaque placeholders, sends ONLY placeholders to the
# (possibly non-EU) cloud model, and de-anonymises the answer. We build the
# anonymisation ourselves over the analyzer's spans (which contains_pii throws
# away) PLUS the local-regex spans — the local entities (Steuer-ID,
# Kundennummer) are not Presidio recognizers, so a Presidio anonymizer service
# could not cover them anyway. One pass, one mapping, pure-Python reverse.


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity_type: str


def local_spans(text: str) -> list[Span]:
    """Spans for the local German entities Presidio lacks: the keyword-anchored
    Kundennummer value and the checksum-validated Steuer-ID."""
    spans: list[Span] = []
    for m in _KUNDENNUMMER.finditer(text):
        spans.append(Span(m.start(1), m.end(1), "KUNDENNR"))
    for m in _DIGIT_RUN.finditer(text):
        if is_valid_steuer_id(m.group().replace(" ", "")):
            spans.append(Span(m.start(), m.end(), "STEUERID"))
    return spans


def analyze_entities(text: str, settings: Settings) -> list[Span]:
    """Presidio ``/analyze`` spans (entity_type/start/end). Raises on any
    failure (same contract as ``presidio_detect``) so the strict caller can
    fail-safe to a sovereign reroute rather than send un-anonymised PII."""
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
    return [
        Span(e["start"], e["end"], e["entity_type"]) for e in resp.json()
    ]


def all_spans(text: str, settings: Settings) -> list[Span]:
    """Presidio spans + local-regex spans. Raises if Presidio is unavailable."""
    return analyze_entities(text, settings) + local_spans(text)


def anonymize_messages(
    messages: list[dict], settings: Settings
) -> tuple[list[dict], "Anonymizer"]:
    """Anonymise every message with ONE shared Anonymizer (so the same value
    maps to the same placeholder across history + new turn). Raises if Presidio
    is unavailable — the strict caller then fail-safe reroutes to a sovereign
    model rather than risk sending un-masked PII. Returns (anon_messages,
    anonymizer); the anonymizer carries the mapping for de-anonymising the
    answer."""
    anon = Anonymizer()
    out: list[dict] = []
    for m in messages:
        content = m.get("content") or ""
        out.append(
            {"role": m["role"], "content": anon.anonymize(content, all_spans(content, settings))}
        )
    return out, anon


# Tolerant reverse matcher (Invariant 4): the model may lower-case, swap the
# underscore for a space, or wrap the token in markdown. A miss leaves the
# placeholder visible (acceptable) — it never invents PII.
_PLACEHOLDER_RE = re.compile(
    r"\[\[\s*DCY[_ ]?([A-Za-z]+)[_ ]?(\d+)\s*\]\]", re.IGNORECASE
)


class Anonymizer:
    """Per-request reversible anonymiser. Build once, ``anonymize`` every
    message of the LLM input with a SHARED mapping (so the same value gets the
    same placeholder across history + new turn — coreference for the model),
    then ``deanonymize`` the answer. The mapping is ephemeral: it lives only
    for the request, never persisted (placeholders never reach storage)."""

    def __init__(self) -> None:
        self._value_to_ph: dict[str, str] = {}
        self._ph_to_value: dict[str, str] = {}
        self._type_counters: dict[str, int] = {}

    @property
    def mapping(self) -> dict[str, str]:
        """placeholder -> original value (read-only view for callers)."""
        return dict(self._ph_to_value)

    def _placeholder_for(self, value: str, entity_type: str) -> str:
        if value in self._value_to_ph:
            return self._value_to_ph[value]
        # Strip non-letters so the type stays a SINGLE token the tolerant reverse
        # regex can span: Presidio emits IBAN_CODE / EMAIL_ADDRESS / PHONE_NUMBER
        # with underscores, which would otherwise break round-tripping.
        etype = re.sub(r"[^A-Za-z]", "", entity_type).upper() or "PII"
        n = self._type_counters.get(etype, 0)
        self._type_counters[etype] = n + 1
        ph = f"[[DCY_{etype}_{n}]]"
        self._value_to_ph[value] = ph
        self._ph_to_value[ph] = value
        return ph

    @staticmethod
    def _resolve_overlaps(spans: list[Span]) -> list[Span]:
        """Drop spans that overlap an already-kept one (longest-first wins),
        so replacing right-to-left can never corrupt a neighbour."""
        chosen: list[Span] = []
        for s in sorted(spans, key=lambda x: (x.start, -(x.end - x.start))):
            if all(s.start >= c.end or s.end <= c.start for c in chosen):
                chosen.append(s)
        return chosen

    def anonymize(self, text: str, spans: list[Span]) -> str:
        for s in sorted(
            self._resolve_overlaps(spans), key=lambda x: x.start, reverse=True
        ):
            value = text[s.start : s.end]
            ph = self._placeholder_for(value, s.entity_type)
            text = text[: s.start] + ph + text[s.end :]
        return text

    def deanonymize(self, text: str) -> str:
        def _repl(m: re.Match) -> str:
            key = f"[[DCY_{m.group(1).upper()}_{m.group(2)}]]"
            return self._ph_to_value.get(key, m.group(0))

        return _PLACEHOLDER_RE.sub(_repl, text)


class StreamDeanonymizer:
    """Stateful de-anonymiser for the strict streaming path (Invariant 1).

    ``feed(chunk)`` returns the text that is SAFE to emit now: complete
    placeholders are de-anonymised, and the only thing held back is a bounded
    tail that could still grow into one of OUR placeholders. ``flush()`` emits
    whatever remains (an un-closed fragment at the real stream end was never a
    real placeholder, so it surfaces as literal — no PII, no silent drop).

    Persistence does NOT rely on this buffer: the stored/audited text is rebuilt
    from the collected raw chunks and de-anonymised in one shot, so a truncated
    live buffer can never corrupt what lands in the DB.
    """

    # Upper bound on a Decyra placeholder length ("[[DCY_" + TYPE + "_" + n +
    # "]]"). A dangling "[[" further than this from the tail cannot be ours, so
    # it is released as literal — preventing a stall / unbounded buffer growth.
    MAX_PH = 64

    def __init__(self, anonymizer: Anonymizer) -> None:
        self._anon = anonymizer
        self._buffer = ""

    def _hold_start(self, buf: str) -> int:
        """Index from which the tail must be withheld (it may still become one
        of our placeholders). ``len(buf)`` means nothing is held."""
        i = buf.rfind("[[")
        if i != -1 and "]]" not in buf[i + 2 :] and (len(buf) - i) <= self.MAX_PH:
            return i
        if buf.endswith("["):  # a lone trailing '[' could become '[['
            return len(buf) - 1
        return len(buf)

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        hold = self._hold_start(self._buffer)
        emit_region = self._buffer[:hold]
        self._buffer = self._buffer[hold:]
        return self._anon.deanonymize(emit_region)

    def flush(self) -> str:
        out = self._anon.deanonymize(self._buffer)
        self._buffer = ""
        return out
