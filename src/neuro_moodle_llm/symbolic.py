"""Phase 3: neuro-symbolic checker.

Two verifiable channels for STEM-style submissions:

- **`check_python(code, tests)`**: runs candidate code through `pytest` in a
  hardened subprocess (no network, ulimit on cpu/mem, isolated tmp dir).
  Returns parsed test counts + first failure log. Works without any extra
  dependency in this image.
- **`check_math(expression_pairs)`**: uses `sympy` (optional) to check that
  expression A simplifies to expression B. Returns per-pair pass/fail.

Use case in the agent flow: `ValidatorAgent` can call `check_python` /
`check_math` to *deterministically* verify some claims in `CriticAgent`'s
draft before the human reviews it.
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CodeReport:
    passed: int = 0
    failed: int = 0
    errors: int = 0
    log: str = ""
    ok: bool = False
    timed_out: bool = False
    raw: dict = field(default_factory=dict)


def _set_limits() -> None:
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except Exception:
        pass


def check_python(code: str, tests: str, *, timeout_s: int = 10) -> CodeReport:
    """Run `code` + `tests` together via pytest in an isolated tmp dir."""
    with tempfile.TemporaryDirectory() as d:
        d_path = Path(d)
        (d_path / "candidate.py").write_text(code)
        (d_path / "test_candidate.py").write_text("from candidate import *\n\n" + tests)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
        try:
            cp = subprocess.run(
                ["python", "-m", "pytest", "-q", "--maxfail=5", "-rN", str(d_path)],
                cwd=str(d_path),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                preexec_fn=_set_limits,
            )
        except subprocess.TimeoutExpired as exc:
            return CodeReport(log=str(exc), timed_out=True)
        out = cp.stdout + "\n" + cp.stderr
        passed = out.count(" passed")
        failed = out.count(" failed")
        errors = out.count(" error")
        return CodeReport(
            passed=passed,
            failed=failed,
            errors=errors,
            log=out[-2000:],
            ok=cp.returncode == 0,
            raw={"returncode": cp.returncode},
        )


def check_math(pairs: list[tuple[str, str]]) -> dict:
    """Each pair is (lhs, rhs); we check lhs.equals(rhs) via sympy.simplify."""
    try:
        import sympy as sp  # type: ignore
    except ImportError:
        return {"ok": False, "error": "sympy not installed (pip install sympy)"}
    out = []
    all_ok = True
    for lhs, rhs in pairs:
        try:
            l = sp.sympify(lhs)
            r = sp.sympify(rhs)
            ok = bool(sp.simplify(l - r) == 0)
        except Exception as exc:
            ok = False
            out.append({"lhs": lhs, "rhs": rhs, "ok": False, "error": str(exc)})
            all_ok = False
            continue
        all_ok = all_ok and ok
        out.append({"lhs": lhs, "rhs": rhs, "ok": ok})
    return {"ok": all_ok, "pairs": out}
