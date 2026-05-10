"""Helpers for reading the JSON / JSONL artefacts the API writes under data/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Optional

from .paths import data_dir


def safe_data_dir() -> Optional[Path]:
    d = data_dir()
    return d if d.exists() else None


def list_jsonl(path: Path, limit: int = 50) -> List[Any]:
    if not path.exists():
        return []
    rows: List[Any] = []
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit > 0:
        return rows[-limit:]
    return rows


def list_json_files(folder: Path, limit: int = 25) -> List[Path]:
    if not folder.exists():
        return []
    files = sorted([p for p in folder.glob("*.json") if p.is_file()], reverse=True)
    return files[:limit]


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def iter_drafts() -> Iterable[Path]:
    folder = data_dir() / "feedback" / "drafts"
    if not folder.exists():
        return []
    return sorted(folder.glob("*.json"), reverse=True)
