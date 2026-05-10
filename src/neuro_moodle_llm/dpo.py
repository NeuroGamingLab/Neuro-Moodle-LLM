"""Phase 3: DPO training-pair exporter.

Every time an instructor edits an LLM-drafted feedback, the original draft
becomes the "rejected" sample and the edited version becomes the "preferred"
sample — exactly the pair shape required by Direct Preference Optimisation.

This module reads `data/feedback/log.jsonl` (produced by `feedback.py`) and
emits a `data/dpo/preferences.jsonl` file in the schema TRL/Axolotl/LLaMA-Factory
expect:

    {"prompt": "...", "chosen": "...", "rejected": "..."}

Actually running DPO needs a training environment (GPU + TRL); this module
is intentionally small — the runbook in the README explains how to do that.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "feedback" / "log.jsonl"
OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "dpo"
OUT_PATH = OUT_DIR / "preferences.jsonl"


def export_preferences(*, log_path: Optional[Path] = None) -> dict:
    log_path = log_path or LOG_PATH
    if not log_path.exists():
        return {"exported": 0, "path": str(OUT_PATH), "note": "no feedback log yet"}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    with log_path.open() as src, OUT_PATH.open("w") as dst:
        for line in src:
            try:
                row = json.loads(line)
            except Exception:
                continue
            draft = (row.get("draft") or "").strip()
            edits = (row.get("instructor_edits") or "").strip()
            if not draft or not edits or draft == edits:
                continue
            prompt = f"Course {row.get('course_id')} – assignment {row.get('assignment_id')}: write feedback."
            dst.write(json.dumps({"prompt": prompt, "chosen": edits, "rejected": draft}) + "\n")
            n += 1
    return {"exported": n, "path": str(OUT_PATH)}
