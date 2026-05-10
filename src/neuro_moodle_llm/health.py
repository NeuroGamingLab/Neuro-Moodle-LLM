"""Shared health-check logic for CLI and HTTP API."""

from __future__ import annotations

from typing import Any

from .config import Settings
from .moodle import MoodleClient, MoodleError
from .ollama import OllamaClient
from .vectorstore import VectorStore


def run_health_report(settings: Settings) -> dict[str, Any]:
    """Return the same JSON-shaped structure as `neuro-moodle-llm health`."""
    report: dict[str, Any] = {
        "moodle": {"base_url": settings.moodle_base_url, "ok": False},
        "qdrant": {"url": settings.qdrant_url, "ok": False},
        "ollama": {"host": settings.ollama_host, "ok": False},
    }

    try:
        with MoodleClient(settings) as m:
            info = m.site_info()
        report["moodle"].update(
            ok=True,
            sitename=info.get("sitename"),
            release=info.get("release"),
            username=info.get("username"),
        )
    except MoodleError as exc:
        report["moodle"]["error"] = str(exc)
    except Exception as exc:
        report["moodle"]["error"] = f"{type(exc).__name__}: {exc}"

    try:
        store = VectorStore(settings)
        report["qdrant"].update(ok=True, **store.stats())
    except Exception as exc:
        report["qdrant"]["error"] = f"{type(exc).__name__}: {exc}"

    try:
        with OllamaClient(settings) as o:
            ver = o.version()
            models = [m.get("name") for m in o.list_models()]
        report["ollama"].update(
            ok=True,
            version=ver.get("version"),
            chat_model=settings.ollama_chat_model,
            embed_model=settings.ollama_embed_model,
            installed_models=models,
        )
    except Exception as exc:
        report["ollama"]["error"] = f"{type(exc).__name__}: {exc}"

    return report


def health_all_ok(report: dict[str, Any]) -> bool:
    return all(svc.get("ok") for svc in report.values())
