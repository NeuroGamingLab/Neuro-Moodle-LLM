"""Semantic / heading-aware chunker.

Replaces the fixed-size chunker in `text.py` for ingest. Splits on:

1. Markdown-ish headings (#, ##, …) when present (we'll emit one ourselves
   per Moodle module/section in `ingest.py` so this is reliable).
2. Paragraph blocks otherwise.

Each chunk preserves the most-recent heading as a prefix so the embedding
captures structural context. Chunks are constrained to `max_chars` with a
small `overlap` of trailing sentences (not raw chars) to avoid mid-word breaks.

`CHUNKER_VERSION` is part of every Qdrant payload via `lineage.py`. Bump it
whenever the chunker output meaningfully changes — re-ingest is required for
champion/challenger comparisons.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CHUNKER_VERSION = "semantic-v1"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


@dataclass
class Chunk:
    text: str
    heading_path: str
    char_start: int
    char_end: int


def semantic_chunks(
    text: str,
    *,
    max_chars: int = 1200,
    overlap_sentences: int = 1,
    default_heading: str = "",
) -> list[Chunk]:
    """Split `text` into heading-prefixed semantic chunks.

    `default_heading` is used as the heading when the input has no `#` lines.
    """
    text = (text or "").strip()
    if not text:
        return []

    sections = _split_by_heading(text, default_heading=default_heading)

    out: list[Chunk] = []
    for path, body, abs_start in sections:
        out.extend(_chunk_section(path, body, abs_start, max_chars, overlap_sentences))
    return out


def _split_by_heading(text: str, *, default_heading: str) -> list[tuple[str, str, int]]:
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [(default_heading, text, 0)]

    out: list[tuple[str, str, int]] = []
    if matches[0].start() > 0:
        out.append((default_heading, text[: matches[0].start()].strip(), 0))

    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        path = " > ".join(t for _, t in stack) or default_heading
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            out.append((path, body, body_start))
    return out


def _chunk_section(
    heading_path: str,
    body: str,
    abs_start: int,
    max_chars: int,
    overlap_sentences: int,
) -> list[Chunk]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_len = 0
    cursor = abs_start

    def flush() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        chunk_text = "\n\n".join(buf).strip()
        prefix = f"{heading_path}\n\n" if heading_path else ""
        chunks.append(
            Chunk(
                text=f"{prefix}{chunk_text}",
                heading_path=heading_path,
                char_start=cursor - buf_len,
                char_end=cursor,
            )
        )

    for para in paragraphs:
        plen = len(para) + 2
        if plen > max_chars:
            flush()
            buf, buf_len = [], 0
            for hard in _hard_split(para, max_chars):
                chunks.append(
                    Chunk(
                        text=(f"{heading_path}\n\n{hard}" if heading_path else hard),
                        heading_path=heading_path,
                        char_start=cursor,
                        char_end=cursor + len(hard),
                    )
                )
                cursor += len(hard)
            continue
        if buf_len + plen > max_chars:
            flush()
            tail = _tail_sentences("\n\n".join(buf), overlap_sentences) if overlap_sentences else ""
            buf = [tail, para] if tail else [para]
            buf_len = sum(len(b) + 2 for b in buf)
            cursor += plen
        else:
            buf.append(para)
            buf_len += plen
            cursor += plen
    flush()
    return chunks


def _hard_split(text: str, max_chars: int) -> list[str]:
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _tail_sentences(text: str, n: int) -> str:
    sents = _SENT_SPLIT_RE.split(text)
    return " ".join(sents[-n:]).strip() if sents else ""


def chunker_signature() -> dict[str, str]:
    """Stable identifier embedded in vector payloads for lineage."""
    return {"chunker_version": CHUNKER_VERSION}
