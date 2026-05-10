# ML app enhancement review — Neuro-Moodle-LLM

> Generated using the **ml-app-enhancement** skill. The recommendations below
> reflect the state of the repository as of the date in the header. Re-run
> after any material change to retrieval, ingest, models, or the Moodle
> integration surface.

---

## Assumptions

- **App type:** RAG-style, course-scoped Q&A over Moodle content; potential expansion to assignment grading/feedback (per `instructions.txt` objective 6).
- **Stack (current):** Moodle 5.2 (custom image) → Python CLI (`neuro-moodle-llm`) → Qdrant 1.12 + Ollama (`llama3.2` chat, `nomic-embed-text` embeddings), all in Docker Compose.
- **Maturity:** Working prototype on a single host; no real student data yet; no UI surface (CLI only); no production users.
- **Scale:** Single-developer / lab-bench. No throughput SLA today.
- **Constraints:** Critical-infrastructure framing (custom builds, audit trail), keep inference **local** (Ollama) by default, prefer self-hosted tools, security-audit-guard already at `Caution`.

If any of those are wrong, two answers below shift materially: (a) "should we add a feedback model?" depends on whether real users will hit this and (b) the agentic recommendations get larger if Moodle goes multi-tenant.

---

## Executive Summary

The biggest near-term gains come from **upgrading the retrieval quality** (better embeddings + chunking + hybrid + rerank), **adding evaluation and observability** so quality changes are measurable, and **making ingest event-driven** so the index follows Moodle changes instead of needing manual `ingest-course` runs. Strategic bets are an **agentic layer** for assignment feedback (multi-step retrieval, rubric checks, grounded critique), **drift / quality monitoring**, and a **lightweight personalisation loop** that learns per learner without leaving the local stack.

---

## Quick Wins (High Impact, Low Effort) — this week / next week

- **Swap the embedding model.** `nomic-embed-text` is fine, but `bge-m3` or `mxbai-embed-large` (both available via Ollama) tend to win on retrieval-quality benchmarks for English course material. Re-ingest, A/B against your eval set.
- **Add a reranker.** Run a small cross-encoder (e.g. `BAAI/bge-reranker-base` via a separate Ollama / sentence-transformers process) on top-k = 20 → keep top-5 for the prompt. Usually a 5–15 nDCG-point lift over pure cosine.
- **Switch to hybrid retrieval.** Qdrant supports sparse vectors / BM25-style scoring alongside dense. Combine with **Reciprocal Rank Fusion** — large quality lift on numeric/code-heavy course content where dense embeddings under-index.
- **Smarter chunking.** Replace the fixed-1200-char chunker with **semantic / structural chunking** (Moodle sections + module titles as boundaries; preserve headings inside chunks; add 1–2 sentence overlap). Embedding quality is bounded by chunk quality.
- **Cache embeddings.** Hash chunk text → store embedding alongside payload in Qdrant. On re-ingest, only embed *changed* chunks. ~10× speedup on iterative ingest.
- **Cache chat answers.** A minimal **semantic cache** (embed question, look up by cosine in Qdrant `qa_cache` collection) — for repeated student questions you skip Ollama entirely.
- **Ground every answer with citations.** You already prompt for source titles; also surface chunk **scores** and **module URLs** in the CLI output so users can verify.
- **Eval harness with `ragas` or `trulens`.** Even 30 hand-written `(course_id, question, expected_topics)` triples lets you compare embedding/chunker/reranker changes objectively.
- **`ollama pull` pinned model digests + warm-up call** at container start. Cold-start latency drops from seconds to ~100ms.

---

## Strategic Improvements (High Impact, High Effort) — next quarter

1. **Event-driven ingest from Moodle.** Use Moodle's **Events API** + a webhook (or Moodle's **scheduled task** invoking a small endpoint on the Python service) to trigger re-embedding on `\core\event\course_module_updated`, `assignment_*_created`, etc. No more manual `ingest-course`; the index stays fresh.
2. **Agentic assignment feedback (objective 6c).** A small **multi-agent** flow: (a) *Retriever* pulls assignment brief + rubric + relevant course content; (b) *Critic* drafts grounded feedback; (c) *Validator* checks the critique against the rubric and rejects unsupported claims; (d) human-in-the-loop UI lets the instructor approve/edit before posting back via `mod_assign_save_grade`.
3. **Per-learner memory.** A small Postgres table or Qdrant collection storing each student's prior questions, struggle topics, and corrections. The retriever conditions on this profile (e.g. boost chunks that explain prerequisites the student has missed). Local, private, opt-in.
4. **Continuous evaluation in CI.** On every change to `rag.py`, `ingest.py`, embedder, or chunker, run the eval harness against a fixed corpus + question set and post a delta report. Catches silent regressions.
5. **Model registry + champion/challenger.** Track `(embed_model, reranker, chunker_version, prompt_version)` as a tuple in MLflow (or even a simple JSON ledger). Run shadow deployments — `llama3.2:3b` vs `qwen2.5:7b-instruct` — and only promote when eval scores beat the champion.
6. **Drift + quality monitoring.** **Evidently** on (a) **embedding drift** of newly ingested content vs the existing index distribution and (b) **answer quality drift** (LLM-as-judge running daily on a probe-question set). Alert when scores drop below a threshold.
7. **Distillation for cost / latency.** If you ever expose this to many students concurrently, **distil** Llama 3.2 3B → a 1B student fine-tuned on (`question, retrieved_context, gold_answer`) tuples logged from the production system. Same answers at ~3× throughput on the same hardware.

---

## Detailed Analysis

### Architecture

- **[Impact: High | Effort: Med] Insert a retrieval microservice between CLI and Qdrant/Ollama.** Today `cli.py` orchestrates everything in-process. Move retrieval + RAG into a small FastAPI service inside the compose network so (a) the Moodle plugin can call it, (b) caching/reranking/eval are in one place, (c) you can scale it independently.
- **[Impact: Med | Effort: Low] Split compose networks.** Already in the security audit recommendations — also relevant here because the retrieval service should *not* be able to talk to Postgres directly.
- **[Impact: Med | Effort: Med] Add a feature store layer.** For a Moodle context, "features" are things like *student-prior-question-vector*, *course-difficulty-tier*, *recent-quiz-scores*. Even a **Postgres-backed mini feature store** beats stuffing everything into prompt context.

### ML techniques

- **[Impact: High | Effort: Med] Hybrid retrieval (dense + sparse + RRF)** — see Quick Wins.
- **[Impact: High | Effort: Low] Reranker (cross-encoder)** — see Quick Wins.
- **[Impact: High | Effort: High] Domain fine-tune** the embedding model on (course chunk, paraphrased query) pairs auto-generated by Llama 3.2. Cheap synthetic data, real lift on academic-vocabulary retrieval.
- **[Impact: Med | Effort: Med] Continual learning of the answer model** — collect (question, retrieved context, instructor-approved answer) tuples and **DPO-fine-tune** Llama 3.2 monthly on those preferences. Keeps the assistant's tone aligned with each course/instructor.
- **[Impact: Med | Effort: Low] Self-supervised pretraining signal** — generate question/answer pairs from each course chunk with a small LLM, store as additional training data for embedding fine-tuning.
- **[Impact: Low | Effort: Low] Ensembling** — for assignment grading, average two model judges (e.g. Llama 3.2 + Qwen2.5) to reduce single-model bias.

### Agentic design

A four-agent sketch maps cleanly onto your objectives:

| Agent | Role | Tools |
|-------|------|-------|
| **Orchestrator** | Decompose instructor/student request, route | Internal task router |
| **Retriever** | Hybrid search, multi-hop ("for question X, also fetch prerequisite Y from course history") | `vectorstore.search`, `moodle.course_contents`, `moodle.assignments` |
| **Critic / Grader** | Draft grounded feedback against rubric | LLM, retrieved context |
| **Validator** | Reject answers with unsupported claims, escalate to human | LLM-as-judge, citation check |

- **[Impact: High | Effort: High] Build the orchestrator + validator pair first** — they are what differentiates a "RAG demo" from something an instructor will trust.
- **[Impact: Med | Effort: Med] Monitoring agent** — runs nightly: probes the system with a fixed question set, scores answers via LLM-as-judge, flags drops. Auto-files a "needs re-ingest" issue if a particular course's score collapses.
- **[Impact: Med | Effort: Low] User-clarification agent** — when a question is ambiguous (e.g. "what is the deadline?"), ask one disambiguating question instead of hallucinating.

### Performance

- **[Impact: High | Effort: Low] Semantic cache** for chat — Quick Wins.
- **[Impact: Med | Effort: Low] Quantise the chat model** — `llama3.2:3b-instruct-q4_K_M` halves RAM and roughly doubles tokens/sec on Apple Silicon vs `q8`.
- **[Impact: Med | Effort: Med] Speculative decoding** — pair Llama 3.2 3B as draft + a 1B as target (or vice-versa); 1.5–2× tokens/sec for free.
- **[Impact: Med | Effort: Low] Batch embeddings.** Today `ingest.py` calls `/api/embeddings` per chunk; use Ollama's batch endpoint when bulk-ingesting whole courses — 5–10× speed-up.
- **[Impact: Low | Effort: Low] KV cache reuse** by keeping a long-running `chat` context per user session instead of re-sending the system prompt.

### Scalability

For lab-bench scope, do **not** over-engineer. Keep these on the radar for "if/when this gets shared with a class":

- **[Impact: Med | Effort: Med] Stateless retrieval service + Qdrant cluster.** Qdrant supports sharding/replication out of the box.
- **[Impact: Med | Effort: Med] Autoscaling on Ollama replicas** behind a small load balancer (LiteLLM router or vLLM gateway). Round-robin is fine; model-aware routing only matters once you have multiple models.
- **[Impact: Low | Effort: High] Multi-region** — irrelevant unless you serve outside one institution; if it ever applies, also re-trigger steps 13–18 of the security audit.

### Data quality

- **[Impact: High | Effort: Low] Validate Moodle pulls with `pydantic` models** at the boundary in `moodle.py` (you already use pydantic for settings). Catches Moodle schema drift early.
- **[Impact: High | Effort: Med] Track ingest lineage.** For each Qdrant point, payload already has `course_id`, `module_id`, `title`. Add **`ingest_run_id`, `source_hash`, `embed_model`, `chunker_version`** so you can answer "which build of the system produced this answer?" — essential when something goes wrong.
- **[Impact: Med | Effort: Med] Detect stale / orphaned vectors** — periodic job that compares Qdrant payload `module_id`s against Moodle's current modules; delete points whose source has been removed.
- **[Impact: Med | Effort: Low] Active learning loop on assignment feedback** — collect every instructor edit of an LLM-drafted feedback. Those edits are gold training pairs for DPO/RLHF.

### User experience

- **[Impact: High | Effort: Med] In-Moodle UI surface (the reserved `moodle_plugins/neurollm` slot)** — a simple block on the course page that shows: "Ask the course assistant", with a chat box that calls the retrieval service. CLI is fine for ops; real users live inside Moodle.
- **[Impact: High | Effort: Low] Show confidence and sources.** Even a `score: 0.42 → 0.81` colour bar next to each cited chunk teaches the user when to trust the answer.
- **[Impact: Med | Effort: Low] In-product feedback** — thumbs up/down + free-text "what should the answer have said?" stored in Postgres, fed into the active-learning loop above.
- **[Impact: Med | Effort: Med] Graceful degradation.** If the validator agent is unsure, do not answer — say "I don't have enough course material on this; here are 3 places that come closest" and surface the chunks. Beats hallucination.
- **[Impact: Low | Effort: Low] Streaming responses** in the future plugin and CLI — just enable Ollama's `stream=true`.

### Automation

- **[Impact: High | Effort: Med] Triggered re-ingest** (Strategic #1).
- **[Impact: High | Effort: Med] Eval harness in CI/CD** (Strategic #4) — `ragas` faithfulness, answer-relevance, context-precision; fail the PR on a regression.
- **[Impact: Med | Effort: Med] HPO over RAG knobs.** Use **Optuna** to tune `top_k`, `chunk_size`, `overlap`, reranker `top_n`, retrieval `alpha` (dense vs sparse weight), prompt template — against the eval harness.
- **[Impact: Med | Effort: Low] Champion/challenger** — already in Strategic #5; only matters once you have a baseline to beat.
- **[Impact: Med | Effort: Low] Alerting on Ollama / Qdrant errors and on answer-quality drops** — push to a webhook or simple email; don't need a full SRE stack.

### Innovation opportunities

1. **Multimodal course content.** Moodle hosts PDFs, slides, and recorded lectures. Add **PDF/slide ingestion** (e.g. `unstructured`, `marker-pdf`) and **Whisper-based transcription** for video — your RAG suddenly covers the full course corpus, not just text resources. This is the single highest-leverage *content* expansion.
2. **Personalised difficulty adaptation via contextual bandits.** When the assistant decides between "explain at intuitive level" vs "explain with formalism", treat it as an arm of a contextual bandit conditioned on (student profile, topic). Low-cost, online, no labelled data needed.
3. **Synthetic question generation for course-quality audits.** Run Llama 3.2 over each course to generate would-be student questions; if the RAG cannot answer them well, surface a "weak coverage" report to the instructor — a *teaching quality* signal, not just a chatbot feature.
4. **Neuro-symbolic grading.** For STEM assignments with verifiable answers, combine LLM critique with a **deterministic checker** (e.g. running submitted Python through pytest, sympy-verifying maths). Each side covers the other's failure mode and the result is interpretable.

---

## Recommended Roadmap

- **Phase 1 (0–4 weeks):** swap embedding model → add reranker → hybrid retrieval → semantic chunking on Moodle structure → embedding cache → eval harness with 30 questions → ingest payload lineage fields. *Goal: measurable retrieval quality and a regression baseline.*
- **Phase 2 (1–3 months):** retrieval microservice → event-driven ingest from Moodle Events API → in-Moodle plugin block (chat UI + thumbs feedback) → multimodal ingest (PDF + slide + video) → Optuna HPO → drift monitoring. *Goal: real users, fresh index, instrumentation.*
- **Phase 3 (3–6 months):** agentic assignment feedback (Retriever / Critic / Validator) → human-in-the-loop grading flow → DPO fine-tune on instructor edits → champion/challenger model registry → per-learner memory. *Goal: cross from "RAG over Moodle" to a credible AI teaching assistant.*

---

## Cross-references

- Security posture: [`audit-reports/2026-05-09-neuro-moodle-llm-audit.md`](../audit-reports/2026-05-09-neuro-moodle-llm-audit.md)
- Stack architecture & data flow diagrams: [`README.md`](../README.md) → *Architecture* section
- Project objectives this review serves: [`instructions.txt`](../instructions.txt)
