"""Phase 3: champion / challenger model registry (JSON ledger).

Tracks `(embed_model, chat_model, reranker, chunker_version, prompt_version,
hpo_knobs)` tuples produced by eval runs. The "champion" tuple is the one the
API actively uses; "challengers" are tested in shadow.

No MLflow / external registry — a single `data/registry/registry.json` file
keeps everything reviewable in git diff.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

REG_PATH = Path(__file__).resolve().parents[2] / "data" / "registry" / "registry.json"


@dataclass
class ModelCard:
    embed_model: str
    chat_model: str
    reranker: str
    chunker_version: str
    prompt_version: str
    hpo_knobs: dict[str, Any] = field(default_factory=dict)
    eval_summary: dict[str, float] = field(default_factory=dict)
    notes: str = ""
    created_ts: int = field(default_factory=lambda: int(time.time()))


def _load() -> dict[str, Any]:
    if not REG_PATH.exists():
        return {"champion": None, "challengers": []}
    return json.loads(REG_PATH.read_text())


def _save(data: dict[str, Any]) -> None:
    REG_PATH.parent.mkdir(parents=True, exist_ok=True)
    REG_PATH.write_text(json.dumps(data, indent=2))


def list_all() -> dict[str, Any]:
    return _load()


def register(card: ModelCard, *, as_champion: bool = False) -> dict[str, Any]:
    data = _load()
    entry = asdict(card)
    if as_champion:
        if data.get("champion"):
            data.setdefault("history", []).append(data["champion"])
        data["champion"] = entry
    else:
        data["challengers"].append(entry)
    _save(data)
    return data


def promote(*, embed_model: str, chat_model: str) -> dict[str, Any]:
    data = _load()
    found: Optional[dict[str, Any]] = None
    for c in list(data.get("challengers", [])):
        if c.get("embed_model") == embed_model and c.get("chat_model") == chat_model:
            found = c
            data["challengers"].remove(c)
            break
    if not found:
        raise ValueError("no matching challenger to promote")
    if data.get("champion"):
        data.setdefault("history", []).append(data["champion"])
    data["champion"] = found
    _save(data)
    return data
