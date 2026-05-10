"""Thin client around Moodle's REST web-services API."""

from __future__ import annotations

from typing import Any

import httpx

from .config import Settings


class MoodleError(RuntimeError):
    """Raised when Moodle returns a structured error response."""


class MoodleClient:
    def __init__(self, settings: Settings, timeout: float = 30.0) -> None:
        if not settings.moodle_token:
            raise MoodleError(
                "MOODLE_TOKEN is empty. Run docker/bootstrap-webservice.php "
                "and paste the printed token into .env."
            )
        self._base_url = settings.moodle_base_url.rstrip("/")
        self._token = settings.moodle_token
        host_header = (settings.moodle_host_header or "").strip()
        headers = {"Host": host_header} if host_header else None
        # Follow 3xx so the API can talk to Moodle even when running on a different
        # hostname than wwwroot (Moodle 303-redirects to wwwroot for non-canonical hosts).
        # Pair with MOODLE_HOST_HEADER to skip the redirect entirely from inside Compose.
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        )

    def __enter__(self) -> MoodleClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def call(self, function: str, **params: Any) -> Any:
        url = f"{self._base_url}/webservice/rest/server.php"
        data = {
            "wstoken": self._token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
        }
        for k, v in _flatten_params(params).items():
            data[k] = v
        resp = self._client.post(url, data=data)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("exception"):
            raise MoodleError(
                f"{function}: {body.get('errorcode')} - {body.get('message')}"
            )
        return body

    def site_info(self) -> dict[str, Any]:
        return self.call("core_webservice_get_site_info")

    def list_courses(self) -> list[dict[str, Any]]:
        return self.call("core_course_get_courses")

    def course_contents(self, course_id: int) -> list[dict[str, Any]]:
        return self.call("core_course_get_contents", courseid=course_id)

    def assignments(self, course_ids: list[int]) -> dict[str, Any]:
        return self.call("mod_assign_get_assignments", courseids=course_ids)

    def pages(self, course_ids: list[int]) -> dict[str, Any]:
        return self.call("mod_page_get_pages_by_courses", courseids=course_ids)


def _flatten_params(params: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Moodle REST encodes lists/dicts as bracketed keys (e.g. courseids[0])."""
    out: dict[str, str] = {}
    for key, value in params.items():
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flatten_params(value, full_key))
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                ikey = f"{full_key}[{i}]"
                if isinstance(item, (dict, list, tuple)):
                    out.update(_flatten_params({str(i): item}, full_key))
                else:
                    out[ikey] = "" if item is None else str(item)
        else:
            out[full_key] = "" if value is None else str(value)
    return out
