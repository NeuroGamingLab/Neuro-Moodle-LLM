"""Ollama HTTP client (chat + embeddings + batch)."""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from .config import Settings


class OllamaClient:
    def __init__(self, settings: Settings, timeout: float | None = None) -> None:
        self._host = settings.ollama_host.rstrip("/")
        self._chat_model = settings.ollama_chat_model
        self._embed_model = settings.ollama_embed_model
        timeout_s = float(timeout) if timeout is not None else float(settings.ollama_http_timeout_s)
        self._client = httpx.Client(timeout=timeout_s)

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @property
    def chat_model(self) -> str:
        return self._chat_model

    @property
    def embed_model(self) -> str:
        return self._embed_model

    def version(self) -> dict[str, Any]:
        resp = self._client.get(f"{self._host}/api/version")
        resp.raise_for_status()
        return resp.json()

    def list_models(self) -> list[dict[str, Any]]:
        resp = self._client.get(f"{self._host}/api/tags")
        resp.raise_for_status()
        return resp.json().get("models", [])

    def pull(self, model: str) -> None:
        with self._client.stream(
            "POST", f"{self._host}/api/pull", json={"model": model, "stream": True}
        ) as resp:
            resp.raise_for_status()
            for _ in resp.iter_lines():
                pass

    def warm(self, model: str | None = None) -> None:
        try:
            self._client.post(
                f"{self._host}/api/generate",
                json={"model": model or self._chat_model, "prompt": "", "stream": False, "keep_alive": "10m"},
                timeout=10.0,
            )
        except Exception:
            pass

    def embed(self, text: str) -> list[float]:
        resp = self._client.post(
            f"{self._host}/api/embeddings",
            json={"model": self._embed_model, "prompt": text},
        )
        resp.raise_for_status()
        body = resp.json()
        embedding = body.get("embedding")
        if not embedding:
            raise RuntimeError(
                f"Ollama returned no embedding (model={self._embed_model}). "
                "Did you run `docker exec ollama ollama pull "
                f"{self._embed_model}`?"
            )
        return embedding

    def embed_batch(self, texts: Iterable[str]) -> list[list[float]]:
        """Try Ollama's `/api/embed` (batch) first; fall back to per-text loop."""
        items = list(texts)
        if not items:
            return []
        try:
            resp = self._client.post(
                f"{self._host}/api/embed",
                json={"model": self._embed_model, "input": items},
            )
            resp.raise_for_status()
            embs = resp.json().get("embeddings")
            if isinstance(embs, list) and len(embs) == len(items):
                return embs
        except Exception:
            pass
        return [self.embed(t) for t in items]

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        options: dict[str, Any] | None = None,
        format: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self._chat_model,
            "messages": messages,
            "stream": False,
        }
        if options:
            payload["options"] = options
        if format:
            payload["format"] = format
        resp = self._client.post(f"{self._host}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")
