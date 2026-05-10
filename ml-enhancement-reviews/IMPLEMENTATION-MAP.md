# ML enhancement review → implementation map

This document ties each major recommendation in [**2026-05-09-neuro-moodle-llm-ml-enhancements.md**](2026-05-09-neuro-moodle-llm-ml-enhancements.md) to the **code that implements it**, and explains **how to use** each capability (HTTP API, CLI, Moodle UI, env vars).

**Prerequisites (typical lab setup):**

- Stack running (`docker compose up -d --build` or `bash scripts/docker-run.sh`).
- API reachable at `http://localhost:8888` (adjust base URL below if different).
- Moodle web service token in `.env` as `MOODLE_TOKEN`; models pulled in Ollama (`OLLAMA_EMBED_MODEL`, `OLLAMA_CHAT_MODEL`). For **`/v1/synth/course`**, ensure **`OLLAMA_HTTP_TIMEOUT_S`** is high enough (default **3600** in code) on slow hardware.
- Optional: `pip install -e ".[math]"` for sympy math checks; `pip install -e ".[hpo]"` for Optuna; `pip install -e ".[audio]"` for Whisper ingest (not in the default slim image).

**Interactive reference:** [OpenAPI / Swagger](http://localhost:8888/docs) lists every route and request schema.

---

## 1. Executive summary & roadmap (review → repo)

| Review theme | Where it lives now | Notes |
|--------------|-------------------|--------|
| Retrieval microservice (FastAPI) | `src/neuro_moodle_llm/api/main.py`, Docker `neuro-moodle-llm` | Single service hosts RAG, ingest, eval, agents, monitoring. |
| Measurable quality (eval + champion) | `eval.py`, `data/eval/`, `/v1/eval/run` | Golden set format below; not Ragas/Trulens yet (deferred). |
| Event-driven ingest | `events.py`, `/v1/events/moodle` | Webhook + shared secret; Moodle must call the URL (or automation). **Also** accepts `\mod_quiz\event\attempt_submitted` for closed-loop `quiz_eval`. |
| **Synthetic → Moodle publish + quiz eval** | `synthetic_course.py`, `moodle_authoring.py`, `quiz_eval.py`, `local_neurollm` plugin | `POST /v1/synth/course` with `publish_to_moodle`; observer + `/v1/eval/quiz_attempt`; see **§Innovation opportunities** rows 5–6 below. |
| Agentic feedback + HITL | `agents.py`, `feedback.py`, `/v1/agents/*` | Draft → validate → instructor edit → `mod_assign_save_grade`. |
| Drift + LLM-as-judge | `monitoring.py`, `/v1/monitor/run` | Lightweight centroid drift + probe judge; not Evidently yet. |
| DPO / continual learning | `dpo.py`, `feedback.py` logging, `/v1/dpo/export` | **Export** implemented; **training** is external (TRL on GPU). |

---

## 2. Quick wins (review § “Quick Wins”) — mapping & usage

| Review item | Implementation | How to use |
|-------------|----------------|------------|
| **Swap embedding model** | `OLLAMA_EMBED_MODEL` in `.env`; `ingest_course` embeds with `OllamaClient.embed_model` | 1. `docker exec ollama ollama pull bge-m3` (example). 2. Set `OLLAMA_EMBED_MODEL=bge-m3`, restart API. 3. Re-ingest: `curl -sS -X POST http://localhost:8888/v1/ingest/course -H 'Content-Type: application/json' -d '{"course_id":2}'`. CLI: `neuro-moodle-llm ingest-course --course-id 2`. |
| **Add a reranker** | `reranker.py` (`LexicalReranker` default); `rag.py` | **API:** `POST /v1/rag/ask` body includes `"use_rerank": true` (default). Set `"use_rerank": false` to A/B. **CLI:** `neuro-moodle-llm ask --question "..." --course-id 2` vs `--no-rerank`. Cross-encoder: extend `reranker.get_reranker()` (hook documented in code). |
| **Hybrid retrieval (dense + sparse + RRF)** | `retrieval.py` (BM25 + RRF), `rag.py`, `vectorstore.py` | **API:** `"use_hybrid": true` (default). **CLI:** default hybrid; `--no-hybrid` disables BM25 arm. |
| **Smarter chunking** | `chunker.py` (`CHUNKER_VERSION`), used by `ingest.py` | Automatic on every `ingest-course`. Bump `CHUNKER_VERSION` in `chunker.py` when chunk semantics change, then full re-ingest. |
| **Cache embeddings** | `embedding_cache.py` (Qdrant collection `embed_cache`) | Automatic on ingest. Response fields: `embeddings_from_cache`, `embeddings_new`. |
| **Semantic cache (chat)** | `qa_cache.py` (collection `qa_cache`), `rag.py` | **API:** `"use_qa_cache": true` (default). Re-ask the same question + course → response `"cache": "hit"`. **CLI:** `ask ...` vs `--no-cache`. |
| **Ground answers + scores** | `rag.py` → `sources[].score`, `sources[].components`, `confidence` | Returned on every `POST /v1/rag/ask`. Low confidence triggers refusal text; see `components.reason` when present. |
| **Eval harness** | `eval.py`, `data/eval/golden.jsonl` (seed: `golden.example.jsonl`) | **API:** `curl -sS -X POST http://localhost:8888/v1/eval/run -H 'Content-Type: application/json' -d '{"label":"baseline","top_k":5,"candidate_k":20,"use_hybrid":true,"use_rerank":true}'`. **CLI:** `neuro-moodle-llm eval --label baseline --promote` writes champion to `data/eval/champion.json`. Golden format: one JSON object per line: `{"course_id":2,"question":"...","expected_topics":["a","b"],"must_cite":"Assignment:"}`. |
| **`ollama pull` + warm-up** | `ollama.py` `pull()`, `warm()` | Call from a startup script or manually: `docker exec ollama ollama pull nomic-embed-text`. Optional: add a small `lifespan` in FastAPI that calls `warm()` (not wired by default). |
| **Long Ollama HTTP calls** | `OLLAMA_HTTP_TIMEOUT_S` in `.env` → `OllamaClient` httpx timeout | Default **3600** seconds in `config.py`. Raise if `/v1/synth/course` still returns **504** (read timeout) on very large chat models. |

---

## 3. Strategic improvements (review § “Strategic Improvements”) — mapping & usage

| # | Review | Implementation | How to use |
|---|--------|----------------|------------|
| 1 | Event-driven ingest | `events.py`, `/v1/events/moodle` | Set `NEURO_EVENT_SECRET` in `.env` on the API container. POST JSON with `secret`, `eventname`, `courseid`, and for module scope `"objecttable":"course_modules"`, `"objectid"` (course module id). Example (note escaped backslashes in JSON): `curl -sS -X POST http://localhost:8888/v1/events/moodle -H 'Content-Type: application/json' -d '{"secret":"YOUR_SECRET","eventname":"\\core\\event\\course_module_updated","courseid":2,"objecttable":"course_modules","objectid":41}'`. Allowed `eventname` values for **re-ingest** are listed in `events.py` (`_INGEST_EVENTS`). **Quiz closed-loop eval:** `\mod_quiz\event\attempt_submitted` with `"objecttable":"quiz_attempts"` and `"objectid": <attempt_id>` (as sent by `local_neurollm` observer) routes to `quiz_eval.evaluate_quiz_attempt` instead of ingest. |
| 2 | Agentic assignment feedback | `agents.py`, `feedback.py`, `/v1/agents/feedback/draft`, `/v1/agents/feedback/submit` | **Draft:** `curl -sS -X POST http://localhost:8888/v1/agents/feedback/draft -H 'Content-Type: application/json' -d '{"course_id":2,"assignment_id":5,"submission_text":"Student work...","rubric":"Rubric text..."}'`. **Submit (HITL):** after editing, `curl ... /v1/agents/feedback/submit -d '{"qid":"<from draft>","instructor_edits":"Final feedback text","user_id":3,"grade":null}'`. **CLI draft:** `neuro-moodle-llm feedback-draft --course-id 2 --assignment-id 5 --submission-file ./sub.txt`. Requires `mod_assign_save_grade` on the Moodle external service. |
| 3 | Per-learner memory | `memory.py`, `rag.py`, `/v1/rag/ask` `learner_id` | Set `NEURO_ENABLE_LEARNER_MEMORY=true` (default). **API:** include `"learner_id":"42"` on ask. **Thumbs + note** trains memory: `POST /v1/feedback` with `"learner_id"`, `"note":"struggled with week 3"`. **CLI:** `neuro-moodle-llm ask --learner-id 42 ...`. |
| 4 | Continuous evaluation in CI | `eval.py` | Wire `neuro-moodle-llm eval --label ci-$GITHUB_SHA` in GitHub Actions after `golden.jsonl` is committed. Fail job if summary metrics drop vs champion (script wrapper not included—compare JSON yourself). |
| 5 | Model registry champion/challenger | `registry.py`, `data/registry/registry.json`, `/v1/registry` | Programmatic: `from neuro_moodle_llm.registry import register, ModelCard, promote`. **HTTP:** `GET /v1/registry` to inspect. |
| 6 | Drift + quality monitoring | `monitoring.py`, `data/monitoring/`, `/v1/monitor/run` | `curl -sS -X POST http://localhost:8888/v1/monitor/run -H 'Content-Type: application/json' -d '{}'`. Optional body: `{"new_run_id":"<ingest_run_id from last ingest>"}` to compare new vectors vs index. **CLI:** `neuro-moodle-llm monitor`. Cron: `0 * * * * curl -sS -X POST http://127.0.0.1:8888/v1/monitor/run`. |
| 7 | Distillation / DPO | `dpo.py`, `data/dpo/`, `/v1/dpo/export` | After instructor edits accumulate in `data/feedback/log.jsonl`, run `curl -sS -X POST http://localhost:8888/v1/dpo/export` or `neuro-moodle-llm dpo-export` → `data/dpo/preferences.jsonl`. **Training (off-box, GPU):** `pip install trl peft bitsandbytes accelerate datasets`, then load the JSONL with HuggingFace `datasets` and run `DPOTrainer` (see TRL docs). Register the resulting model in `registry.py` / `data/registry/registry.json` and re-run eval before promoting. |

---

## Appendix A — DPO export schema

Each line of `data/dpo/preferences.jsonl`:

```json
{"prompt": "…", "chosen": "…instructor-approved…", "rejected": "…model draft…"}
```

Source rows come from `data/feedback/log.jsonl` when `draft` and `instructor_edits` differ.

## 4. Detailed analysis (review § “Detailed Analysis”) — selected rows

### Architecture

| Review finding | Status | How to use / notes |
|----------------|--------|---------------------|
| Retrieval microservice | **Done** | Same as §1. |
| Split compose networks | **Not done** | Still single bridge; follow `audit-reports/` if you harden. |
| Feature store | **Not done** | `memory.py` + Qdrant is a minimal stand-in. |

### ML techniques

| Review finding | Status | How to use |
|----------------|--------|------------|
| Hybrid + RRF | **Done** | §2 hybrid row. |
| Reranker | **Done** (lexical) | §2 reranker row. |
| Domain fine-tune embeddings | **Deferred** | Export training pairs yourself; no trainer in-repo. |
| DPO answer model | **Partial** | Export only; training external. |
| Self-supervised Q/A from chunks | **Partial** | `synthetic.py` generates questions for **audit**, not training export yet. |

### Agentic design

| Agent | Module | How to use |
|-------|--------|------------|
| Orchestrator | `agents.py` | `POST /v1/agents/run` body: `{"intent":"qa"|"feedback"|"audit","course_id":2,"question":"...","submission":"...","rubric":"..."}`. Returns `plan` + `trace`. |
| Retriever | `agents.py` | Invoked inside pipeline; uses same hybrid RAG as `rag.ask`. |
| Critic / Validator | `agents.py` | Part of `feedback` intent and feedback draft flow. |

### Performance

| Review finding | Status | How to use |
|----------------|--------|------------|
| Semantic cache | **Done** | §2 cache row. |
| Batch embeddings | **Done** | Automatic in `ingest_course` via `OllamaClient.embed_batch`. |
| Quantised / speculative / KV | **Not done** | Configure Ollama models externally. |

### Data quality

| Review finding | Status | How to use |
|----------------|--------|------------|
| Pydantic on Moodle payloads | **Partial** | Still loose `dict` parsing in `moodle.py`; settings use Pydantic. |
| Ingest lineage | **Done** | Inspect any Qdrant point payload: `ingest_run_id`, `source_hash`, `embed_model`, `chunker_version`, `ingested_at`. |
| Stale vector cleanup | **Partial** | `ingest_module` + webhook support incremental delete; no nightly sweeper yet. |
| Active learning from feedback | **Partial** | Thumbs → `data/feedback/thumbs.jsonl`; assignment pipeline → `data/feedback/log.jsonl` + DPO export. |

### User experience

| Review finding | Status | How to use |
|----------------|--------|------------|
| In-Moodle chat | **Done** | Course → **Neuro ML assistant** (`/local/neurollm/index.php`). Site admin: **Local plugins → Neuro Moodle LLM → API base URL** = `http://localhost:8888`. Chat calls `/v1/rag/ask`; thumbs call `/v1/feedback`. |
| Confidence + sources | **Done** | JSON fields on `/v1/rag/ask`. |
| Thumbs feedback | **Done** | Same as chat UI or raw `POST /v1/feedback`. |
| Graceful degradation | **Done** | Low-confidence path in `rag.py` returns refusal + closest sources when above floor. |
| Streaming | **Not done** | Would set Ollama `stream: true` + SSE in FastAPI. |

### Automation

| Review finding | Status | How to use |
|----------------|--------|------------|
| Triggered re-ingest | **Done** | §3 event row. |
| Eval in CI | **Partial** | Wire `eval` CLI (§2). |
| HPO | **Done** (grid) | `curl -sS -X POST http://localhost:8888/v1/hpo/grid` or `neuro-moodle-llm hpo`. Optuna: `pip install -e ".[hpo]"` then use `hpo.optuna_search()` from Python (no HTTP wrapper yet). |
| Alerting | **Not done** | `monitor` writes JSON files; plug in your own notifier. |

### Innovation opportunities

| # | Review | Status | How to use |
|---|--------|--------|------------|
| 1 | Multimodal (PDF / slides / audio) | **Partial** | **PDF:** mount a file into the API container or copy inside, then `curl -sS -X POST http://localhost:8888/v1/ingest/multimodal/pdf -H 'Content-Type: application/json' -d '{"course_id":2,"path":"/tmp/syllabus.pdf","title":"Syllabus"}'`. **CLI:** `neuro-moodle-llm ingest-pdf --course-id 2 --path /path/in/container.pdf`. PPTX / Whisper: optional extras (see top of this doc). |
| 2 | Contextual bandits | **Done** (library) | `bandits.py` — use from Python to pick `"intuitive"` vs `"formal"` style, then call `Bandit.update(arm, qvec, reward)` with thumb score. No HTTP route yet. |
| 3 | Synthetic Q audit | **Done** | `curl -sS 'http://localhost:8888/v1/audit/course/2?max_chunks=10'` or `neuro-moodle-llm audit --course-id 2 --max-chunks 10`. Writes under `data/audit/`. |
| 4 | Neuro-symbolic grading | **Partial** | **Python:** `POST /v1/symbolic/python` with `{"code":"...","tests":"pytest...","timeout_s":10}`. **Math:** `POST /v1/symbolic/math` with `{"pairs":[["x**2-1","(x-1)*(x+1)"]]}`. Install sympy: `pip install -e ".[math]"` (or rebuild image with that extra). |
| 5 | **Synthetic courses (Ollama)** | **Done** | **`POST /v1/synth/course`** with `{"course_id":90001,"topic":"…","weeks":2,"modules_per_week":2,"questions_per_module":2,"seed":42}`. Uses same chunk+embed+upsert as Moodle ingest; every point has `provenance: synthetic`, `synth_topic`, `synth_seed`, `synth_run_id`. Writes `data/eval/golden_synthetic_<course_id>.jsonl` (optional `append_golden_to_primary`). **CLI:** `neuro-moodle-llm synth-course --course-id 90001 --topic "…"`. **Eval on that file:** `neuro-moodle-llm eval --golden data/eval/golden_synthetic_90001.jsonl` or `POST /v1/eval/run` with `"golden_path":"/app/data/eval/golden_synthetic_90001.jsonl"`. Recommended `course_id >= 90000`. **Reference:** [`../docs/SYNTHETIC_COURSES.md`](../docs/SYNTHETIC_COURSES.md). **Per-step walkthrough:** [`../docs/SYNTHETIC_COURSES_WALKTHROUGH.md`](../docs/SYNTHETIC_COURSES_WALKTHROUGH.md). |
| 6 | **Publish synthetic courses into Moodle (Phase A + B + C)** | **Done** | Add `"publish_to_moodle": true` (and optionally `"publish_quizzes": true`) to `POST /v1/synth/course` (or `--publish-to-moodle` on the CLI). Phase A creates a real **course shell** + Page resources via `local_neurollm_create_course/_page`; Phase B creates a **multichoice Quiz per module** via `local_neurollm_create_quiz_with_questions` (Ollama generates 1 correct + 3 distractors). The response includes `course_id` (real Moodle id), `publish.shortname`, `publish.view_url`, per-module page/quiz cmids, and a re-ingest summary keyed by the real course id. Phase C: a `\mod_quiz\event\attempt_submitted` observer in the plugin webhooks the API at `/v1/events/moodle`, which dispatches to `quiz_eval.evaluate_quiz_attempt` and writes `data/monitoring/quiz_eval_<attempt_id>.json` with `agent_must_cite_hit`, `agent_topic_recall`, and per-question detail. Programmatic loop: `POST /v1/eval/quiz_attempt {"attempt_id": …}` or `neuro-moodle-llm eval-quiz --attempt-id …`. Cleanup: `POST /v1/synth/purge` or `neuro-moodle-llm purge-synth --course-id …` (deletes Moodle course **and** Qdrant vectors; refuses non-`synth-` idnumbers). **Walkthrough:** [`../docs/SYNTHETIC_COURSES_WALKTHROUGH.md`](../docs/SYNTHETIC_COURSES_WALKTHROUGH.md). |

---

## 5. Environment variables (quick reference)

| Variable | Purpose |
|----------|---------|
| `MOODLE_BASE_URL` | Moodle REST base (host vs `http://moodle` in Compose). |
| `MOODLE_HOST_HEADER` | e.g. `localhost:8080` when API calls Moodle by Docker DNS but `wwwroot` is localhost. |
| `MOODLE_TOKEN` | Web service token for ingest / agents. |
| `QDRANT_URL`, `QDRANT_COLLECTION` | Vector DB; main course collection name (default `course_content`). |
| `OLLAMA_HOST`, `OLLAMA_CHAT_MODEL`, `OLLAMA_EMBED_MODEL` | Inference endpoints and model tags. |
| `OLLAMA_HTTP_TIMEOUT_S` | httpx timeout (seconds) for each Ollama request from the API. Default **3600** in code; increase if very large models or slow hardware still hit `ReadTimeout` during `/v1/synth/course`. |
| `NEURO_API_CORS_ORIGINS` | Comma-separated origins allowed to call the API from a browser. |
| `NEURO_EVENT_SECRET` | If set, required in JSON body `secret` for `/v1/events/moodle`. |
| `NEURO_ENABLE_LEARNER_MEMORY` | `true`/`false` — gates `learner_id` behaviour in RAG. |

---

## 6. Data directories created at runtime

| Path | Contents |
|------|----------|
| `data/eval/golden_synthetic_<id>.jsonl` | Eval rows emitted by `POST /v1/synth/course` (optional merge into `golden.jsonl`). When `publish_to_moodle=true`, `<id>` is the **real Moodle course id** and `must_cite` is rewritten to the Page name. |
| `data/eval/quiz_eval_meta_<moodle_course_id>.json` | Synthetic ground truth for each quiz published into Moodle (`question_id` → `must_cite`, `expected_topics`). Read by the closed-loop quiz-attempt eval. |
| `data/eval/runs/*.json` | Each eval run output. |
| `data/eval/champion.json` | Champion summary after `--promote`. |
| `data/eval/best.json` | Best HPO knobs from `hpo.grid_search`. |
| `data/monitoring/*.json` | Drift / judge snapshots and `quiz_eval_<attempt_id>.json` records (Phase C closed-loop agentic citation eval per submitted Moodle quiz attempt). |
| `data/audit/*.json` | Course coverage audits. |
| `data/registry/registry.json` | Model registry ledger. |
| `data/feedback/thumbs.jsonl` | UI thumbs. |
| `data/feedback/log.jsonl` | Assignment HITL log for DPO. |
| `data/feedback/drafts/*.json` | Pending feedback drafts keyed by `qid`. |
| `data/dpo/preferences.jsonl` | Exported DPO pairs. |
| `data/bandits/linucb.json` | LinUCB state (if you use `bandits.py`). |

---

## 6b. Streamlit operator dashboard

A multi-page **Streamlit** app under `streamlit_app/` (built from `Dockerfile.streamlit`, run as the `streamlit` Compose service on **`:8501`**) wraps every enhancement surface for instructors and ML-ops. It is a thin client over the FastAPI service — no direct DB / Ollama / Moodle calls. Pages and what they exercise:

| Page | Calls / reads |
|------|---------------|
| **Home** | `GET /health`, `GET /health/strict` |
| **RAG Playground** | `POST /v1/rag/ask` with hybrid / rerank / cache toggles; `POST /v1/feedback` thumbs |
| **Ingest** | `POST /v1/ingest/course`, `POST /v1/ingest/multimodal/pdf` (path inside API container) |
| **Eval & Monitor** | `POST /v1/eval/run`, `POST /v1/monitor/run`; reads `data/eval/runs/*.json`, `data/eval/champion.json`, `data/monitoring/*.json` |
| **HPO & Registry** | `POST /v1/hpo/grid` (slow), `GET /v1/registry`; reads `data/eval/best.json` |
| **HITL Feedback** | Lists `data/feedback/drafts/*.json`; `POST /v1/agents/feedback/draft`, `POST /v1/agents/feedback/submit` |
| **Audit** | `GET /v1/audit/course/{id}?max_chunks=N`; reads `data/audit/*.json` |
| **Symbolic** | `POST /v1/symbolic/python`, `POST /v1/symbolic/math` |
| **DPO Export** | `POST /v1/dpo/export`; reads `data/dpo/preferences.jsonl` |
| **Event Simulator** | `POST /v1/events/moodle` (uses `NEURO_EVENT_SECRET` from env) |
| **Synthetic Course** | `POST /v1/synth/course` (Ollama; long-running). **Publish-to-Moodle toggle** wires Phase A (course shell + Page resources) and Phase B (multichoice quizzes); after publishing, the page exposes one-click "eval this golden", a closed-loop **agentic quiz-attempt eval** panel (calls `POST /v1/eval/quiz_attempt`), and a **Cleanup** panel that calls `POST /v1/synth/purge`. |

**Persistence note:** the API service now bind-mounts `./data:/app/data` so registry / eval / monitoring / audit / feedback / dpo artefacts survive container rebuilds; Streamlit mounts `./data:/data:ro` and uses `NEURO_DATA_DIR=/data`.

**Auth:** Streamlit has no built-in auth — gate port `8501` behind a reverse proxy (basic auth, SSO header from Moodle, or your existing ingress) before exposing it beyond localhost.

**Local dev (no Docker):** `pip install streamlit requests && PYTHONPATH=streamlit_app NEURO_API_BASE=http://127.0.0.1:8888 streamlit run streamlit_app/Home.py`.

---

## 6c. FastAPI `v1` route index (current)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/ingest/course` | Full course ingest → Qdrant |
| `POST` | `/v1/ingest/multimodal/pdf` | PDF → chunks → Qdrant |
| `POST` | `/v1/rag/ask` | Hybrid RAG answer (+ optional `learner_id`) |
| `POST` | `/v1/feedback` | Thumbs + optional learner note |
| `POST` | `/v1/events/moodle` | Webhook: re-ingest **or** quiz `attempt_submitted` → `quiz_eval` |
| `POST` | `/v1/eval/run` | Golden-set eval harness |
| `POST` | `/v1/hpo/grid` | Grid HPO over RAG knobs |
| `POST` | `/v1/monitor/run` | Drift + judge monitoring |
| `POST` | `/v1/agents/feedback/draft` | Assignment feedback draft |
| `POST` | `/v1/agents/feedback/submit` | HITL submit graded feedback |
| `GET` | `/v1/audit/course/{course_id}` | Synthetic Q coverage audit |
| `POST` | `/v1/synth/course` | Ollama synthetic course (+ optional publish to Moodle) |
| `POST` | `/v1/synth/purge` | Delete published synthetic course (Moodle + optional Qdrant) |
| `POST` | `/v1/eval/quiz_attempt` | Closed-loop quiz-attempt citation eval |
| `GET` | `/v1/registry` | Model registry JSON |
| `POST` | `/v1/symbolic/python` | Sandboxed Python checks |
| `POST` | `/v1/symbolic/math` | Sympy equivalence |
| `POST` | `/v1/dpo/export` | DPO preferences JSONL |
| `POST` | `/v1/agents/run` | Multi-step agent pipeline (`intent`: qa / feedback / audit) |

See also **`GET /health`** and **`GET /health/strict`** (503 if any dependency unhealthy).

---

## 7. Related documents

- Original enhancement review: [2026-05-09-neuro-moodle-llm-ml-enhancements.md](2026-05-09-neuro-moodle-llm-ml-enhancements.md)
- Security lens: [../audit-reports/README.md](../audit-reports/README.md)
- Architecture diagram + ports: [../README.md](../README.md) (Architecture section)

When the stack or review assumptions change, add a **new** dated review file here (do not rewrite the 2026-05-09 review), then extend this map or add `IMPLEMENTATION-MAP-YYYY-MM-DD.md` alongside it.

---

*Architecture and design: **Dang-Tue Hoang** — AI Engineer.*
