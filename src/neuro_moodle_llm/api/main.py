"""FastAPI app: ML enhancement layer outside Moodle core.

Routes (v1):
- `/health`, `/health/strict`           — service liveness
- `/v1/ingest/course`                   — ingest a Moodle course (Phase 1 pipeline)
- `/v1/ingest/multimodal/pdf`           — ingest a local PDF (Phase 2)
- `/v1/rag/ask`                         — hybrid RAG (Phase 1) + memory hook (Phase 3)
- `/v1/feedback`                        — thumbs feedback from in-Moodle chat
- `/v1/events/moodle`                   — Moodle event webhook (Phase 2)
- `/v1/eval/run`                        — run the eval harness
- `/v1/hpo/grid`                        — grid-search HPO over RAG knobs
- `/v1/monitor/run`                     — drift + LLM-as-judge monitoring
- `/v1/agents/feedback/draft`           — agentic assignment feedback (Phase 3)
- `/v1/agents/feedback/submit`          — HITL: post instructor-approved feedback
- `/v1/audit/course/{course_id}`        — synthetic-question coverage audit
- `/v1/synth/course`                    — Ollama-generated synthetic course → Qdrant + optional golden JSONL
                                            (set `publish_to_moodle=true` to also create a real Moodle course shell + Quiz)
- `/v1/synth/purge`                     — delete a published synthetic course from Moodle and its Qdrant vectors
- `/v1/eval/quiz_attempt`               — closed-loop eval of a submitted Moodle quiz attempt against synthetic ground truth
- `/v1/registry`                        — champion/challenger model registry
- `/v1/symbolic/python` and `/v1/symbolic/math`   — neuro-symbolic verifiers
- `/v1/dpo/export`                      — emit preference pairs from feedback log
- `/v1/agents/run`                      — multi-step agent pipeline (qa / feedback / audit intents)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .. import dpo as dpo_mod
from .. import eval as eval_mod
from .. import hpo as hpo_mod
from .. import monitoring as mon_mod
from .. import quiz_eval as quiz_eval_mod
from .. import registry as reg_mod
from .. import symbolic as sym_mod
from .. import synthetic as syn_mod
from .. import synthetic_course as synth_course_mod
from ..agents import AgentRunResult, run_pipeline
from ..config import Settings, get_settings
from ..events import EventResult, handle_event, verify_secret
from ..feedback import draft_feedback, submit_feedback_to_moodle
from ..health import health_all_ok, run_health_report
from ..ingest import ingest_course
from ..memory import LearnerMemory
from ..moodle import MoodleClient
from ..moodle_authoring import delete_synth_course
from ..multimodal import extract_pdf, ingest_multimodal
from ..ollama import OllamaClient
from ..rag import ask
from ..vectorstore import VectorStore

log = logging.getLogger(__name__)


def _cors_origins(settings: Settings) -> list[str]:
    raw = (settings.neuro_api_cors_origins or "").strip()
    if not raw:
        return ["http://localhost:8080", "http://127.0.0.1:8080"]
    return [o.strip() for o in raw.split(",") if o.strip()]


_settings = get_settings()
_FEEDBACK_LOG = Path(__file__).resolve().parents[3] / "data" / "feedback" / "thumbs.jsonl"

app = FastAPI(
    title="Neuro-Moodle-LLM API",
    description=(
        "RAG + ingest + agentic feedback for Moodle course content. "
        "Moodle core unchanged; this service calls Moodle web services and Qdrant/Ollama. "
        "See `ml-enhancement-reviews/` for phase notes."
    ),
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(_settings),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, Any]:
    return run_health_report(_settings)


@app.get("/health/strict")
def health_strict() -> dict[str, Any]:
    report = run_health_report(_settings)
    if not health_all_ok(report):
        raise HTTPException(status_code=503, detail=report)
    return report


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "neuro-moodle-llm", "version": app.version, "docs": "/docs", "health": "/health"}


class IngestCourseBody(BaseModel):
    course_id: int = Field(..., ge=1)
    no_replace: bool = False


@app.post("/v1/ingest/course")
def ingest_course_api(body: IngestCourseBody) -> dict[str, Any]:
    with MoodleClient(_settings) as moodle, OllamaClient(_settings) as ollama:
        store = VectorStore(_settings)
        return ingest_course(
            course_id=body.course_id,
            moodle=moodle,
            ollama=ollama,
            store=store,
            replace=not body.no_replace,
        )


class PdfIngestBody(BaseModel):
    course_id: int = Field(..., ge=1)
    path: str = Field(..., description="Path readable by the API container.")
    title: Optional[str] = None


@app.post("/v1/ingest/multimodal/pdf")
def ingest_pdf(body: PdfIngestBody) -> dict[str, Any]:
    p = Path(body.path)
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"path not found in container: {p}")
    docs = extract_pdf(p, course_id=body.course_id, title=body.title)
    with OllamaClient(_settings) as ollama:
        store = VectorStore(_settings)
        return ingest_multimodal(docs, ollama=ollama, store=store)


class AskBody(BaseModel):
    question: str = Field(..., min_length=1)
    course_id: Optional[int] = Field(default=None, ge=1)
    top_k: int = Field(default=5, ge=1, le=50)
    candidate_k: int = Field(default=20, ge=1, le=200)
    use_hybrid: bool = True
    use_rerank: bool = True
    use_qa_cache: bool = True
    learner_id: Optional[str] = None


@app.post("/v1/rag/ask")
def rag_ask(body: AskBody) -> dict[str, Any]:
    with OllamaClient(_settings) as ollama:
        store = VectorStore(_settings)
        result = ask(
            body.question,
            course_id=body.course_id,
            ollama=ollama,
            store=store,
            top_k=body.top_k,
            candidate_k=body.candidate_k,
            use_hybrid=body.use_hybrid,
            use_rerank=body.use_rerank,
            use_qa_cache=body.use_qa_cache,
            settings=_settings,
            learner_id=body.learner_id if _settings.neuro_enable_learner_memory else None,
        )
    qid = f"q-{int(time.time() * 1000)}-{os.urandom(2).hex()}"
    return {
        "qid": qid,
        "answer": result.answer,
        "sources": result.sources,
        "cache": result.cache,
        "confidence": result.confidence,
        "components": result.components,
    }


class FeedbackBody(BaseModel):
    qid: str
    vote: str = Field(..., pattern="^(up|down)$")
    learner_id: Optional[str] = None
    course_id: Optional[int] = None
    note: Optional[str] = None


@app.post("/v1/feedback")
def feedback(body: FeedbackBody) -> dict[str, Any]:
    _FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _FEEDBACK_LOG.open("a") as f:
        f.write(json.dumps({"ts": int(time.time()), **body.dict()}) + "\n")
    if body.learner_id and body.note:
        try:
            LearnerMemory(_settings).remember(learner_id=body.learner_id, kind=f"thumb-{body.vote}", text=body.note)
        except Exception:
            pass
    return {"ok": True}


class EventBody(BaseModel):
    eventname: Optional[str] = None
    courseid: Optional[int] = None
    course_id: Optional[int] = None
    objecttable: Optional[str] = None
    objectid: Optional[int] = None
    secret: Optional[str] = None


@app.post("/v1/events/moodle")
def events_moodle(body: EventBody) -> dict[str, Any]:
    if not verify_secret(body.secret or "", _settings):
        raise HTTPException(status_code=401, detail="bad secret")
    res: EventResult = handle_event(body.dict(), _settings)
    return {
        "accepted": res.accepted,
        "action": res.action,
        "course_id": res.course_id,
        "module_id": res.module_id,
        "detail": res.detail,
    }


class EvalBody(BaseModel):
    label: str = ""
    top_k: int = 5
    candidate_k: int = 20
    use_hybrid: bool = True
    use_rerank: bool = True
    golden_path: Optional[str] = Field(
        default=None,
        description="Optional path to a golden.jsonl inside the API container (e.g. /app/data/eval/golden_synthetic_90001.jsonl).",
    )
    promote: bool = Field(
        default=False,
        description="If true, write data/eval/champion.json with this run's summary after a successful eval.",
    )


@app.post("/v1/eval/run")
def eval_run(body: EvalBody) -> dict[str, Any]:
    gp = Path(body.golden_path) if body.golden_path else None
    run = eval_mod.evaluate(
        label=body.label,
        top_k=body.top_k,
        candidate_k=body.candidate_k,
        use_hybrid=body.use_hybrid,
        use_rerank=body.use_rerank,
        use_qa_cache=False,
        golden_path=gp,
    )
    if body.promote and run.get("n", 0) > 0:
        eval_mod.promote_champion(run)
        run["promoted"] = True
    return run


@app.post("/v1/hpo/grid")
def hpo_grid() -> dict[str, Any]:
    return hpo_mod.grid_search()


class MonitorBody(BaseModel):
    new_run_id: Optional[str] = None


@app.post("/v1/monitor/run")
def monitor_run(body: MonitorBody) -> dict[str, Any]:
    return mon_mod.run_monitoring(new_run_id=body.new_run_id, settings=_settings)


class FeedbackDraftBody(BaseModel):
    course_id: int
    assignment_id: int
    submission_text: str
    rubric: str = ""


@app.post("/v1/agents/feedback/draft")
def agent_feedback_draft(body: FeedbackDraftBody) -> dict[str, Any]:
    return draft_feedback(
        course_id=body.course_id,
        assignment_id=body.assignment_id,
        submission_text=body.submission_text,
        rubric=body.rubric,
        settings=_settings,
    )


class FeedbackSubmitBody(BaseModel):
    qid: str
    instructor_edits: str
    user_id: int
    grade: Optional[float] = None


@app.post("/v1/agents/feedback/submit")
def agent_feedback_submit(body: FeedbackSubmitBody) -> dict[str, Any]:
    return submit_feedback_to_moodle(
        qid=body.qid,
        instructor_edits=body.instructor_edits,
        user_id=body.user_id,
        grade=body.grade,
        settings=_settings,
    )


@app.get("/v1/audit/course/{course_id}")
def audit_course(course_id: int, max_chunks: int = 30) -> dict[str, Any]:
    return syn_mod.audit_course(course_id, settings=_settings, max_chunks=max_chunks)


class SynthCourseBody(BaseModel):
    course_id: int = Field(..., ge=1, description="Recommended >= 90000 for synthetic-only courses.")
    topic: str = Field(..., min_length=2, max_length=500)
    weeks: int = Field(2, ge=1, le=16)
    modules_per_week: int = Field(2, ge=1, le=8)
    questions_per_module: int = Field(2, ge=0, le=10)
    seed: int = 42
    no_replace: bool = False
    write_golden: bool = True
    append_golden_to_primary: bool = False
    allow_low_course_id: bool = False
    publish_to_moodle: bool = Field(
        False,
        description="Also create the course in Moodle (real LMS course id, page resources). Requires the local_neurollm plugin.",
    )
    publish_quizzes: bool = Field(
        True,
        description="When publish_to_moodle is true, also create a Quiz activity per module. No-op otherwise.",
    )


@app.post("/v1/synth/course")
def synth_course_api(body: SynthCourseBody) -> dict[str, Any]:
    try:
        return synth_course_mod.generate_and_ingest(
            course_id=body.course_id,
            topic=body.topic,
            weeks=body.weeks,
            modules_per_week=body.modules_per_week,
            questions_per_module=body.questions_per_module,
            seed=body.seed,
            replace=not body.no_replace,
            write_golden=body.write_golden,
            append_golden_to_primary=body.append_golden_to_primary,
            allow_low_course_id=body.allow_low_course_id,
            publish_to_moodle=body.publish_to_moodle,
            publish_quizzes=body.publish_quizzes,
            settings=_settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama returned non-JSON for the skeleton prompt ({exc}); try a different seed or a stronger chat model.",
        ) from exc
    except httpx.ReadTimeout as exc:
        raise HTTPException(
            status_code=504,
            detail=(
                "Ollama did not finish within the HTTP read timeout (often first load of a large model). "
                "Try `docker exec ollama ollama pull "
                f"{_settings.ollama_chat_model}` then retry, or raise OLLAMA_HTTP_TIMEOUT_S "
                f"(currently {_settings.ollama_http_timeout_s}s)."
            ),
        ) from exc


class SynthPurgeBody(BaseModel):
    course_id: int = Field(..., ge=1, description="Real Moodle course id (typically returned by /v1/synth/course).")
    delete_qdrant: bool = Field(True, description="Also delete the course's vectors from Qdrant.")


@app.post("/v1/synth/purge")
def synth_purge_api(body: SynthPurgeBody) -> dict[str, Any]:
    moodle_result: dict[str, Any]
    try:
        with MoodleClient(_settings) as moodle:
            moodle_result = delete_synth_course(moodle=moodle, course_id=body.course_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    qdrant_result: dict[str, Any] = {"deleted": False}
    if body.delete_qdrant:
        store = VectorStore(_settings)
        if store.collection_exists():
            store.delete_course(int(body.course_id))
            qdrant_result = {"deleted": True, "course_id": int(body.course_id)}
    return {"moodle": moodle_result, "qdrant": qdrant_result}


class QuizAttemptEvalBody(BaseModel):
    attempt_id: int = Field(..., ge=1)
    course_id: Optional[int] = None
    top_k: int = Field(5, ge=1, le=20)


@app.post("/v1/eval/quiz_attempt")
def eval_quiz_attempt_api(body: QuizAttemptEvalBody) -> dict[str, Any]:
    try:
        return quiz_eval_mod.evaluate_quiz_attempt(
            attempt_id=body.attempt_id,
            course_id=body.course_id,
            top_k=body.top_k,
            settings=_settings,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/registry")
def registry_get() -> dict[str, Any]:
    return reg_mod.list_all()


class PythonCheckBody(BaseModel):
    code: str
    tests: str
    timeout_s: int = 10


@app.post("/v1/symbolic/python")
def symbolic_python(body: PythonCheckBody) -> dict[str, Any]:
    rep = sym_mod.check_python(body.code, body.tests, timeout_s=body.timeout_s)
    return rep.__dict__


class MathCheckBody(BaseModel):
    pairs: list[tuple[str, str]]


@app.post("/v1/symbolic/math")
def symbolic_math(body: MathCheckBody) -> dict[str, Any]:
    return sym_mod.check_math(body.pairs)


@app.post("/v1/dpo/export")
def dpo_export() -> dict[str, Any]:
    return dpo_mod.export_preferences()


class AgentRunBody(BaseModel):
    intent: str = "qa"
    course_id: int
    question: str = ""
    submission: str = ""
    rubric: str = ""


@app.post("/v1/agents/run")
def agents_run(body: AgentRunBody) -> dict[str, Any]:
    from .. import agents as agents_mod

    plan = agents_mod.OrchestratorAgent(_settings).run({"intent": body.intent})["plan"]
    res: AgentRunResult = run_pipeline(plan, body.dict(), settings=_settings)
    return {
        "plan": plan,
        "trace": [{"name": t.name, "input": t.input, "output": t.output} for t in res.trace],
        "final": res.final,
    }
