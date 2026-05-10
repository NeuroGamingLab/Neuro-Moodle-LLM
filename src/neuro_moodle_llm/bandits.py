"""Phase 3: contextual bandit for difficulty adaptation.

Picks one of K answer styles (e.g. "intuitive" vs "formal") given a context
vector (embedding of the question, optionally concatenated with a learner
profile vector). Uses **LinUCB** — closed-form, no training loop, online by
construction. Stays small (numpy-only).

Persistence is a JSON file per arm holding `(A, b)`. We don't pickle numpy —
JSON is small enough for K=2-5 arms with a few hundred dims and is readable.

Typical use:
    bandit = Bandit(arms=["intuitive", "formal"], dim=len(qvec))
    arm = bandit.pick(qvec)
    answer = ask_in_style(arm, ...)
    bandit.update(arm, qvec, reward=thumbs_up_value)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "bandits" / "linucb.json"


class Bandit:
    def __init__(
        self,
        *,
        arms: list[str],
        dim: int,
        alpha: float = 1.0,
        path: Optional[Path] = None,
    ) -> None:
        self.arms = arms
        self.dim = dim
        self.alpha = alpha
        self.path = path or DEFAULT_PATH
        self.A: dict[str, list[list[float]]] = {a: _eye(dim) for a in arms}
        self.b: dict[str, list[float]] = {a: [0.0] * dim for a in arms}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            for a in self.arms:
                ad = data.get("A", {}).get(a)
                bd = data.get("b", {}).get(a)
                if ad and bd and len(ad) == self.dim:
                    self.A[a] = ad
                    self.b[a] = bd
        except Exception:
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"A": self.A, "b": self.b, "arms": self.arms, "dim": self.dim}))

    def pick(self, x: list[float]) -> str:
        best, best_score = self.arms[0], -1e18
        for a in self.arms:
            A_inv = _inv(self.A[a])
            theta = _matvec(A_inv, self.b[a])
            mean = sum(t * xi for t, xi in zip(theta, x))
            uncertainty = self.alpha * math.sqrt(max(_quad(x, A_inv), 0.0))
            score = mean + uncertainty
            if score > best_score:
                best_score, best = score, a
        return best

    def update(self, arm: str, x: list[float], reward: float) -> None:
        for i in range(self.dim):
            for j in range(self.dim):
                self.A[arm][i][j] += x[i] * x[j]
            self.b[arm][i] += reward * x[i]
        self._save()


def _eye(n: int) -> list[list[float]]:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _matvec(M: list[list[float]], v: list[float]) -> list[float]:
    return [sum(M[i][j] * v[j] for j in range(len(v))) for i in range(len(M))]


def _quad(v: list[float], M: list[list[float]]) -> float:
    Mv = _matvec(M, v)
    return sum(vi * mvi for vi, mvi in zip(v, Mv))


def _inv(M: list[list[float]]) -> list[list[float]]:
    """Gauss-Jordan inverse, O(n^3). Fine for K arms × small dim usage."""
    n = len(M)
    aug = [row[:] + [1.0 if j == i else 0.0 for j in range(n)] for i, row in enumerate(M)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-12:
            aug[col][col] += 1e-6
            pivot_row = col
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pv = aug[col][col]
        aug[col] = [v / pv for v in aug[col]]
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            aug[r] = [v - factor * aug[col][k] for k, v in enumerate(aug[r])]
    return [row[n:] for row in aug]
