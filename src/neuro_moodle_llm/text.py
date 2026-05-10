"""Lightweight HTML stripping + chunking utilities (no heavy deps)."""

from __future__ import annotations

import re
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip > 0:
            self._skip -= 1
        elif tag in {"p", "br", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.text()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, *, max_chars: int = 1200, overlap: int = 150) -> list[str]:
    """Greedy paragraph-aware chunker; falls back to hard slicing for long blocks."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(para), max_chars - overlap):
                chunks.append(para[i : i + max_chars])
            continue
        if not buf:
            buf = para
        elif len(buf) + 2 + len(para) <= max_chars:
            buf = f"{buf}\n\n{para}"
        else:
            chunks.append(buf)
            tail = buf[-overlap:] if overlap > 0 else ""
            buf = f"{tail}\n\n{para}".strip() if tail else para
    if buf:
        chunks.append(buf)
    return chunks
