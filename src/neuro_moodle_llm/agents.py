"""Phase 3: minimal agent framework + four built-in agents.

The framework is intentionally tiny — no LangChain, no extra runtime — because
this codebase already has direct Ollama and Qdrant clients and the agents are
specialised, not general. Each agent has a single `run(input)` method and a
declared list of tools (just other module-level functions).

Built-in agents:
- `OrchestratorAgent`: decomposes a request into sub-tasks and routes them.
- `RetrieverAgent`:    multi-hop hybrid retrieval (course content + assignment
                       brief + rubric + prerequisite topics).
- `CriticAgent`:       drafts grounded feedback against a rubric.
- `ValidatorAgent`:    LLM-as-judge + citation check; rejects unsupported
                       claims and escalates to human via the feedback flow.

Used by `feedback.assignment_feedback()` (Phase 3) and `POST /v1/agents/run`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from .config import Settings, get_settings
from .moodle import MoodleClient
from .ollama import OllamaClient
from .rag import ask
from .vectorstore import VectorStore


class Agent(Protocol):
    name: str

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass
class AgentTrace:
    name: str
    input: dict[str, Any]
    output: dict[str, Any]


@dataclass
class AgentRunResult:
    final: dict[str, Any]
    trace: list[AgentTrace] = field(default_factory=list)


class _Base:
    name: str = "base"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()


class RetrieverAgent(_Base):
    name = "retriever"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        course_id = int(payload["course_id"])
        question = str(payload.get("question") or "")
        with OllamaClient(self.settings) as ollama:
            store = VectorStore(self.settings)
            r = ask(
                question,
                course_id=course_id,
                ollama=ollama,
                store=store,
                top_k=int(payload.get("top_k", 8)),
                use_qa_cache=False,
                settings=self.settings,
            )
        return {"sources": r.sources, "draft_context": r.answer, "confidence": r.confidence}


class CriticAgent(_Base):
    name = "critic"
    SYSTEM = (
        "You are a grading critic. Draft constructive, specific feedback for the "
        "student's submission. Reference rubric criteria explicitly. Cite course "
        "context items by their title. Do not invent material that is not present "
        "in the rubric or context."
    )

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        rubric = payload.get("rubric") or "(no rubric provided)"
        submission = payload.get("submission") or ""
        sources = payload.get("sources") or []
        ctx = "\n\n".join(f"### {s.get('title','(untitled)')}\n{s.get('text','')}" for s in sources)
        with OllamaClient(self.settings) as ollama:
            text = ollama.chat(
                messages=[
                    {"role": "system", "content": self.SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Rubric:\n{rubric}\n\nCourse context:\n{ctx}\n\n"
                            f"Submission:\n{submission}\n\n"
                            "Write feedback as JSON: {summary, strengths[], improvements[], rubric_scores{}, citations[]}"
                        ),
                    },
                ],
                options={"temperature": 0.2},
            )
        return {"draft_feedback_raw": text}


class ValidatorAgent(_Base):
    name = "validator"
    SYSTEM = (
        "You are a strict validator. Given draft feedback and the source citations, "
        "decide whether each claim is supported. Output JSON: {supported: bool, "
        "issues: [string], escalate_to_human: bool, score: float in [0,1]}."
    )

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        draft = payload.get("draft_feedback_raw") or ""
        sources = payload.get("sources") or []
        ctx = "\n\n".join(f"### {s.get('title','(untitled)')}\n{s.get('text','')}" for s in sources)
        with OllamaClient(self.settings) as ollama:
            verdict = ollama.chat(
                messages=[
                    {"role": "system", "content": self.SYSTEM},
                    {"role": "user", "content": f"Draft:\n{draft}\n\nCitations:\n{ctx}"},
                ],
                options={"temperature": 0.0},
            )
        return {"verdict_raw": verdict}


class OrchestratorAgent(_Base):
    name = "orchestrator"

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        intent = (payload.get("intent") or "qa").lower()
        if intent == "feedback":
            return {"plan": ["retriever", "critic", "validator"]}
        if intent == "audit":
            return {"plan": ["synthetic", "retriever", "validator"]}
        return {"plan": ["retriever"]}


_AGENTS: dict[str, type[_Base]] = {
    "orchestrator": OrchestratorAgent,
    "retriever": RetrieverAgent,
    "critic": CriticAgent,
    "validator": ValidatorAgent,
}


def get_agent(name: str, settings: Optional[Settings] = None) -> _Base:
    cls = _AGENTS.get(name)
    if not cls:
        raise ValueError(f"unknown agent: {name!r}")
    return cls(settings=settings)


def run_pipeline(plan: list[str], context: dict[str, Any], settings: Optional[Settings] = None) -> AgentRunResult:
    settings = settings or get_settings()
    trace: list[AgentTrace] = []
    state: dict[str, Any] = dict(context)
    for name in plan:
        agent = get_agent(name, settings)
        out = agent.run(state)
        trace.append(AgentTrace(name=name, input={k: v for k, v in state.items() if k != "submission"}, output=out))
        state.update(out)
    return AgentRunResult(final=state, trace=trace)
