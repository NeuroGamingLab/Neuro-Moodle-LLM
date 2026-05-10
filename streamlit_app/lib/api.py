"""Thin HTTP client around the neuro-moodle-llm FastAPI service.

Everything in the Streamlit dashboard goes through this module so the
network surface is in one place. All methods raise ``APIError`` on a
non-2xx response so pages can render a single error banner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

import requests

from .paths import api_base


class APIError(RuntimeError):
    def __init__(self, status: int, body: str, url: str) -> None:
        super().__init__(f"{status} {url}: {body[:300]}")
        self.status = status
        self.body = body
        self.url = url


@dataclass
class NeuroAPI:
    base: str
    timeout: float = 120.0

    @classmethod
    def from_env(cls) -> "NeuroAPI":
        return cls(base=api_base())

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        url = self._url(path)
        try:
            resp = requests.request(
                method,
                url,
                json=json,
                params=params,
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as exc:
            raise APIError(0, str(exc), url) from exc
        if resp.status_code >= 400:
            raise APIError(resp.status_code, resp.text or "", url)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def get(self, path: str, *, params: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        return self._request("GET", path, params=params, timeout=timeout)

    def post(self, path: str, *, json: Optional[Mapping[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        return self._request("POST", path, json=json, timeout=timeout)

    def health(self) -> Any:
        return self.get("/health")

    def health_strict(self) -> Any:
        return self.get("/health/strict")

    def root(self) -> Any:
        return self.get("/")

    def ingest_course(self, course_id: int) -> Any:
        return self.post("/v1/ingest/course", json={"course_id": course_id}, timeout=600)

    def ingest_pdf(self, course_id: int, path: str, title: str) -> Any:
        return self.post(
            "/v1/ingest/multimodal/pdf",
            json={"course_id": course_id, "path": path, "title": title},
            timeout=600,
        )

    def rag_ask(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/rag/ask", json=payload, timeout=300)

    def feedback(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/feedback", json=payload)

    def eval_run(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/eval/run", json=payload, timeout=900)

    def monitor_run(self, payload: Optional[Mapping[str, Any]] = None) -> Any:
        return self.post("/v1/monitor/run", json=payload or {}, timeout=900)

    def hpo_grid(self, payload: Optional[Mapping[str, Any]] = None) -> Any:
        return self.post("/v1/hpo/grid", json=payload or {}, timeout=3600)

    def registry(self) -> Any:
        return self.get("/v1/registry")

    def agents_run(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/agents/run", json=payload, timeout=300)

    def feedback_draft(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/agents/feedback/draft", json=payload, timeout=300)

    def feedback_submit(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/agents/feedback/submit", json=payload, timeout=120)

    def dpo_export(self, payload: Optional[Mapping[str, Any]] = None) -> Any:
        return self.post("/v1/dpo/export", json=payload or {}, timeout=120)

    def symbolic_python(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/symbolic/python", json=payload, timeout=120)

    def symbolic_math(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/symbolic/math", json=payload, timeout=60)

    def audit_course(self, course_id: int, max_chunks: int = 8) -> Any:
        return self.get(f"/v1/audit/course/{course_id}", params={"max_chunks": max_chunks}, timeout=900)

    def event_post(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/events/moodle", json=payload, timeout=120)

    def synth_course(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/synth/course", json=payload, timeout=3600)

    def synth_purge(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/synth/purge", json=payload, timeout=120)

    def eval_quiz_attempt(self, payload: Mapping[str, Any]) -> Any:
        return self.post("/v1/eval/quiz_attempt", json=payload, timeout=600)
