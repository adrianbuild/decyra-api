# app/chunking.py
"""Task 5.2 — token-approximate text chunking for RAG embeddings.

~500-token chunks with ~50-token overlap (WORKPLAN 5.2). Token count is a
deterministic, OFFLINE heuristic (~4 chars/token) — no tokenizer download, no
network (matters for the sovereign/Docker runtime). "~500" is approximate by
spec; swapping in a real tokenizer later is a one-function change (replace
_est_tokens). Splits on whitespace so words stay intact.
"""
from __future__ import annotations

CHARS_PER_TOKEN = 4      # rough EN/DE average
TARGET_TOKENS = 500
OVERLAP_TOKENS = 50


def _est_tokens(s: str) -> int:
    return max(1, round(len(s) / CHARS_PER_TOKEN))


def chunk_text(
    text: str,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[str]:
    """Greedy word-packing into ~target_tokens chunks, carrying ~overlap_tokens
    of words from the end of each chunk into the next. Returns [] for blank
    input. Never splits a word; a single over-long word becomes its own chunk."""
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    n = len(words)
    i = 0
    while i < n:
        cur: list[str] = []
        tok = 0
        j = i
        while j < n:
            wtok = _est_tokens(words[j])
            if cur and tok + wtok > target_tokens:
                break
            cur.append(words[j])
            tok += wtok
            j += 1
        chunks.append(" ".join(cur))
        if j >= n:
            break
        # Back up ~overlap_tokens worth of words for the next chunk's start.
        back = 0
        k = j
        while k > i and back < overlap_tokens:
            back += _est_tokens(words[k - 1])
            k -= 1
        i = max(k, i + 1)  # always progress, even if overlap >= chunk size
    return chunks
