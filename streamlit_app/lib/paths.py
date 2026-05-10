"""Resolve runtime paths for the Streamlit dashboard.

In Compose the API container writes to ``/app/data`` and the Streamlit
container mounts the same host folder at ``/data`` read-only. Outside Compose
both default to ``./data`` relative to the repo root.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Return the directory where the API persists state."""
    raw = os.environ.get("NEURO_DATA_DIR")
    if raw:
        return Path(raw)
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    return repo_root / "data"


def api_base() -> str:
    return os.environ.get("NEURO_API_BASE", "http://127.0.0.1:8888").rstrip("/")


def default_course_id() -> int:
    try:
        return int(os.environ.get("NEURO_DEFAULT_COURSE_ID", "2"))
    except ValueError:
        return 2


def event_secret_default() -> str:
    return os.environ.get("NEURO_EVENT_SECRET", "")
