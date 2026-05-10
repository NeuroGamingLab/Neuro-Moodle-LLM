# Strategic improvements — implementation outlines

> Planning document. **No code is touched here.** Each item is a work-package
> brief derived from the *Strategic Improvements* section of
> [`../ml-enhancement-reviews/2026-05-09-neuro-moodle-llm-ml-enhancements.md`](../ml-enhancement-reviews/2026-05-09-neuro-moodle-llm-ml-enhancements.md).
>
> Every section follows the same template
> (**Goal → Components → Phases → Artifacts → Risks → Done criteria → Effort**)
> so the briefs read as comparable.

---

## Scope

Six strategic improvements are in scope here:

1. Agentic assignment feedback (objective 6c)
2. Per-learner memory
3. Continuous evaluation in CI
4. Model registry + champion / challenger
5. Drift + quality monitoring
6. Distillation for cost / latency

**Order matters.** Items **#3 (CI eval)** and **#4 (registry / champion–challenger)**
are foundations — the other four reference them constantly. Build them first.

---

## 1. Agentic assignment feedback (objective 6c)

**Goal.** Replace "single-shot LLM grading" with a small **multi-agent
pipeline** that always grounds feedback in the rubric + course corpus, and
**never writes to Moodle without instructor approval**.

**Components.**

- **Orchestrator** — fixed pipeline at first (Retriever → Critic → Validator → HITL → Writer). No fancy planner; predictability beats cleverness for grading.
- **Retriever agent** — pulls (a) the assignment brief, (b) the **rubric** (Moodle rubric or marking guide), (c) top-k course chunks from Qdrant, (d) optional submission text.
- **Critic agent** — drafts feedback **structured per rubric criterion** (not free-form prose). Output schema: `[{criterion_id, score, evidence_quote, suggestion}]`.
- **Validator agent** — independent LLM pass that checks every Critic claim against retrieved evidence + rubric; rejects unsupported / off-rubric claims; tags low-confidence items for human attention.
- **HITL UI** — Moodle plugin block (`moodle_plugins/neurollm`) showing draft, source citations, validator flags, and Approve / Edit / Reject buttons.
- **Writer** — only after explicit Approve, posts via `mod_assign_save_grade` (token already authorised in the bootstrap script — but tighten to a dedicated "grade-writer" role first; see audit recommendation #3).

**Phases.**

| Phase | Outcome | Duration |
|------|---------|----------|
| 0. Pre-flight | Inventory which courses use **rubrics vs marking guides vs simple grade**; pick one course as pilot. Confirm `mod_assign_save_grade` accepts the structures you need. Define a **safety policy**: no autoposting, ever. | 1 wk |
| 1. Rubric ingestion | Decide canonical internal schema for rubrics. Pull rubrics via Moodle WS; cache in Postgres. | 1 wk |
| 2. Agent contracts | JSON schemas for each agent's input/output. Critic and Validator must speak the **same** schema so diffs are computable. | 0.5 wk |
| 3. Pipeline (no UI) | Wire Retriever → Critic → Validator end-to-end behind a CLI command (`grade-submission --assignment-id X --user-id Y --dry-run`). Output goes to log + JSON file. | 1.5 wk |
| 4. HITL surface | Moodle block: draft view, citations, validator flags, Approve / Edit / Reject. Persist instructor edits. | 2 wk |
| 5. Write-back | Implement `mod_assign_save_grade` call **only** on Approve. Audit log row per write. | 0.5 wk |
| 6. Pilot | One course, one instructor, ≤ 30 submissions. Measure agreement and time saved. | 2 wk |

**Artifacts.** Rubric cache table; per-submission `grading_runs` table (run_id, agent_versions, prompts, raw outputs, validator verdicts, instructor action, final grade, timestamps); Moodle audit-log entry per write.

**Risks / gotchas.**

- **Validator over-rejection** — silently drops correct feedback. Mitigate by surfacing rejected items as "low confidence" instead of deleting.
- **Rubric mismatch** — Moodle has both *rubrics* and *marking guides*; treat them as separate adapters.
- **Grade write is irreversible** to the student in their gradebook view → require **Approve + Confirm**, not single-click.
- **Bias / fairness** — log all (submission, draft) pairs and let an instructor sample-audit weekly.
- **Security** — depends on the dedicated WS role from `audit-reports/` recommendation #3; don't ship this on top of the current `Manager` token.

**Done criteria.** ≥ 80% of pilot drafts approved with edit-distance ≤ X% of draft length; zero auto-writes; instructor-reported time-to-grade reduced ≥ 30%.

**Effort.** ~6–8 engineer-weeks for a pilot-grade implementation.

---

## 2. Per-learner memory

**Goal.** Improve answers by conditioning retrieval on **what each student
already struggled with**, **strictly opt-in**, with no leakage across
students.

**Components.**

- **Storage** — two-table design:
  - Postgres `learner_profiles` (structured: opt-in flag, prerequisite gaps, recent topics, totals, retention metadata).
  - Qdrant `learner_memory` collection (vector store of past questions and corrections), payload includes `user_id`, `course_id`, `created_at`.
- **Capture pipeline** — every successful `ask` writes a memory record; every instructor correction writes a *high-weight* record.
- **Enrichment job** — periodic LLM pass per profile to extract "struggle topics" and "prerequisite gaps" from raw question history.
- **Conditioning layer** — at query time: fetch profile → boost retrieval for prerequisite chunks; insert a short "what this learner has struggled with" preface into the system prompt.
- **Consent + deletion** — opt-in switch in Moodle user preferences; explicit "delete my profile" action.

**Phases.**

| Phase | Outcome | Duration |
|------|---------|----------|
| 0. Privacy review | Plain-language data notice, retention policy (e.g. profiles auto-expire 1 yr after course end), DPIA-style mini-doc filed in `audit-reports/`. | 1 wk |
| 1. Schema | Postgres DDL + Qdrant collection layout + filter design (`user_id` filter is mandatory on every search). | 0.5 wk |
| 2. Capture | Hook in `cli.py` / future API to write memory rows on each interaction. | 0.5 wk |
| 3. Enrichment | Nightly job that summarises each profile's last N questions into structured topics. | 1 wk |
| 4. Retrieval bias | Modify the retriever to fetch profile → adjust scoring or add a "prerequisite booster" sub-query. A/B against memory-off. | 1 wk |
| 5. Consent UI | Moodle plugin: opt-in toggle, "view my data", "delete my profile". Defaults to OFF. | 1 wk |
| 6. Pilot | One course, voluntary cohort, measure retrieval-quality lift. | 2 wk |

**Artifacts.** Privacy notice doc; `learner_profiles` table; `learner_memory` Qdrant collection; consent + deletion API endpoints; A/B comparison report.

**Risks / gotchas.**

- **Cross-learner leakage** is the only catastrophic failure mode. Make `user_id` filter a Qdrant `must` condition at the *VectorStore wrapper* layer, not at the call site, so it can't be forgotten.
- **Hot-then-cold** — a student who opts in once may not want it retroactively for the next course. Scope memory **per course**, not globally.
- **Profile drift** — students change; weight recent interactions more.
- **PIPEDA / FERPA / Quebec Law 25 territory** — re-trigger steps 13–18 of the security audit before this collects real student data.

**Done criteria.** Opt-in flow audited; per-user data deletable in < 1 minute; retrieval-quality lift > X nDCG points for opt-in cohort vs control on the eval harness.

**Effort.** ~5–6 engineer-weeks; +1 wk legal/privacy review.

---

## 3. Continuous evaluation in CI

**Goal.** Make every change that touches retrieval, ingest, embedding,
chunking, or prompts produce a **measurable delta report** before merge — so
silent regressions can't ship.

**Components.**

- **Seed corpus** — small, frozen subset of Moodle courses checked into the repo under `evals/corpus/` (or git-LFS). Maybe 1 course, 200 chunks.
- **Question set** — `evals/questions.jsonl`, 30 hand-written `(course_id, question, expected_topics, optional gold_answer)` triples.
- **Harness** — script that runs the full RAG pipeline against the seed corpus for each question and computes metrics.
- **Metrics** — pick from **ragas** (faithfulness, answer-relevance, context-precision, context-recall) + **custom citation-accuracy** (did the cited title appear in expected_topics?).
- **CI workflow** — GitHub Actions: cache Ollama model layers, spin up an in-job Qdrant + Ollama, run harness, post a comment table on the PR (current vs `main` baseline).
- **Threshold gate** — PR fails if any metric drops more than X% (configurable, e.g. 3%).

**Phases.**

| Phase | Outcome | Duration |
|------|---------|----------|
| 0. Metric selection | Decide the 4–5 metrics, agree thresholds with stakeholders. | 0.5 wk |
| 1. Seed corpus | Curate 1 course, freeze it. Document why those chunks. | 1 wk |
| 2. Q/A test set | Hand-write 30 questions with expected sources. **No LLM-generated questions** (avoids self-reference bias). | 1 wk |
| 3. Harness | One command runs everything → JSON report. | 1 wk |
| 4. CI workflow | Triggers only on changed files (`src/neuro_moodle_llm/{rag,ingest,text,vectorstore,ollama}.py`, `pyproject.toml`, `.env.example`). | 1 wk |
| 5. Baseline + thresholds | Run on `main`, lock as baseline; tune thresholds to reduce false fails. | 1 wk |
| 6. Override label | `eval-skip` PR label for justified regressions, with mandatory comment. | 0.5 wk |

**Artifacts.** `evals/` folder (corpus + questions + harness + reports); CI workflow; baseline JSON; PR comment template.

**Risks / gotchas.**

- **Test-set leakage** — once Q/A is in repo, future model versions will see it during pretraining. Refresh annually; keep a private "holdout" set outside the repo for true regression checks.
- **CI cost** — running Ollama in CI is heavy. Use the smallest viable chat model in CI; pre-pull weights into a cached Docker layer; consider self-hosted runner.
- **LLM-as-judge flakiness** — pin the judge model and seed; report variance across N runs, not single point.
- **False sense of security** — a passing CI does not mean the model is good in production; only that it didn't get materially worse.

**Done criteria.** PRs touching listed files always show a delta table; baseline tracked in `evals/baseline.json`; ≥ 1 regression caught and prevented within first quarter.

**Effort.** ~4–5 engineer-weeks; ongoing CI minutes cost.

---

## 4. Model registry + champion / challenger

**Goal.** Treat every retrieval+answer **configuration tuple** as a
first-class versioned object, so swaps are auditable and improvements are
proved before promotion.

**Components.**

- **Tuple schema** — `(embed_model + digest, reranker_id, chunker_version, prompt_version, chat_model + digest)` — extends the **lineage fields** already recommended in Quick Wins.
- **Registry** — start with a flat `model-registry/registry.json` checked into git; promote to MLflow only if you actually need experiment tracking UI.
- **Stamping** — every Qdrant point payload and every answer log row carries the tuple ID it was produced under.
- **Shadow runner** — for a *sampled* fraction of queries, run **both champion and challenger** in parallel, log both answers + retrievals.
- **Comparator** — daily/weekly job that runs the same eval harness from item #3 against shadow logs and produces a champion-vs-challenger delta.
- **Promotion process** — manual sign-off first; automation comes later when the eval is trusted.

**Phases.**

| Phase | Outcome | Duration |
|------|---------|----------|
| 0. Tuple definition | Lock the schema; decide what counts as a new version vs a patch. | 0.5 wk |
| 1. Stamping | Add tuple fields to ingest payloads + answer logs. Backfill existing data with `unknown` markers. | 1 wk |
| 2. Registry | Append-only `registry.json` with `id`, `tuple`, `created_at`, `notes`, `status`. | 0.5 wk |
| 3. Shadow runner | Sample 5–10% of queries → run both configs; log into a `shadow_runs` table. | 1.5 wk |
| 4. Comparator | Reuse harness from item #3; output side-by-side report. | 1 wk |
| 5. Promotion ceremony | Doc'd manual process: "challenger must beat champion on ≥ N metrics over ≥ M runs". Add to `audit-reports/` style ledger. | 0.5 wk |
| 6. Optional: MLflow | Only after #1–5 are stable; one-way migration from JSON. | 1 wk |

**Artifacts.** `model-registry/registry.json`; tuple-stamped payloads + logs; `shadow_runs` table; weekly comparator report; promotion log.

**Risks / gotchas.**

- **Cost of shadow** — running both models doubles inference cost on shadow queries. Sample, don't mirror everything.
- **Champion creep** — without a baseline that's also re-measured, the bar drifts. Re-baseline the champion on the eval harness monthly.
- **Tuple explosion** — if every prompt tweak bumps a version, the registry becomes noisy. Reserve version bumps for changes that *could* affect quality.
- **Decision noise on small samples** — define minimum N before promotion; resist gut calls.

**Done criteria.** Every prod answer is traceable to a tuple ID; every promotion has a comparator report attached; at least one challenger has been promoted (or rejected) on data, not vibes.

**Effort.** ~4 engineer-weeks for the JSON-ledger version; +2 wk if you migrate to MLflow later.

---

## 5. Drift + quality monitoring (Evidently)

**Goal.** Detect (a) when **incoming course content** is statistically
different from what the index already knows, and (b) when **answer quality**
is silently degrading — and alert before users notice.

**Components.**

- **Two distinct drift signals** (do not conflate):
  - **(a) Embedding / data drift** — distribution of newly embedded chunks vs a frozen baseline sample of the index.
  - **(b) Answer-quality drift** — daily LLM-as-judge run over a small **probe-question set** distinct from the CI eval set.
- **Tooling** — **Evidently** for both (it ships report types for both). Data lands in Postgres; reports rendered statically and served from compose.
- **Storage** — `monitoring_runs` table (run_id, kind, timestamp, metrics_json).
- **Alerting** — threshold breach → write to log + optional webhook (Slack / email / `gh issue create`). No PagerDuty for a lab project.

**Phases.**

| Phase | Outcome | Duration |
|------|---------|----------|
| 0. Baselines | Snapshot 1k random index embeddings → freeze as the drift baseline. Snapshot a 10-question probe set + gold answers → freeze as quality baseline. | 1 wk |
| 1. Drift hook | At ingest time, score new chunks against baseline (e.g. mean cosine drift, MMD). Write to monitoring table. | 1 wk |
| 2. Quality probe | Daily cron container that runs probe questions through current production config; LLM-as-judge scores each. | 1 wk |
| 3. Reports | Evidently HTML reports generated nightly; mounted into Apache or served by the future retrieval microservice. | 1 wk |
| 4. Thresholds | Define numeric thresholds per metric; tune for false-positive rate. Document in repo. | 0.5 wk |
| 5. Alerts | Webhook on breach; weekly "no breach" summary so silence stays meaningful. | 0.5 wk |
| 6. Re-baselining cadence | Schedule quarterly baseline refresh (and gate behind a manual review). | 0.5 wk |

**Artifacts.** Frozen baselines (committed); `monitoring_runs` table; nightly Evidently HTML reports; `monitoring/thresholds.yaml`; alert webhook config.

**Risks / gotchas.**

- **Legitimate new course content looks like drift.** Tag each ingest run with `course_id`; per-course baselines beat one global baseline.
- **LLM-as-judge cost accumulates daily** — keep the probe set small (< 20 q), pin the judge model, dedupe.
- **Probe-set memorisation** by future teachers/models — refresh quarterly.
- **Overlap with CI eval (item #3)** — keep them separate: CI catches *change*, monitoring catches *time*.

**Done criteria.** Two reports rendered nightly without manual touch; ≥ 30 days of baseline data accumulated before thresholds are believed; ≥ 1 simulated regression caught by the alert pipeline during testing.

**Effort.** ~4 engineer-weeks.

---

## 6. Distillation for cost / latency

**Goal.** When (and only when) load demands it, train a **smaller student
model** on production traces of the current 3B teacher, so you can serve more
concurrent students per host without quality collapse.

**Components.**

- **Scale gate** — explicit precondition: do **not** start until concurrent users / latency complaints justify it. This is a strategic bet, not a default move.
- **Teacher model** — current `llama3.2:3b` (or whatever the registry champion is at the time).
- **Student candidate** — `llama3.2:1b` is the obvious choice; alternatives `qwen2.5:1.5b` or `phi-3.5-mini`.
- **Training data** — production logs of `(question, retrieved_context, teacher_answer, optional instructor_edit)` tuples — collected as a side-effect of items #1 (HITL edits) and #4 (logged shadow runs).
- **Method** — supervised fine-tune (SFT) on teacher answers; then DPO using `(teacher_answer ≺ instructor_edited_answer)` as preference pairs.
- **Frameworks** — Hugging Face TRL / unsloth; needs a GPU machine outside the existing Apple-Silicon dev box for serious runs.
- **Eval gate** — student must meet (or come close to) champion on the eval harness from item #3 before being promoted to challenger via item #4.

**Phases.**

| Phase | Outcome | Duration |
|------|---------|----------|
| 0. Gate check | Confirm load justifies this; pre-condition: production logging from item #4 has ≥ 30 days of data. | 0 (wait) |
| 1. Data collection | Add structured logging of `(question, retrieved chunks, teacher answer, instructor edit)` to a `distillation_dataset` table. | 1 wk |
| 2. Privacy de-identification | Strip names, emails, student IDs from training data; document the cleaning pipeline. | 1 wk |
| 3. Dataset curation | Filter low-quality answers, deduplicate, hold out a test set. Aim for ≥ 5k SFT examples + ≥ 500 preference pairs before training. | 2 wk |
| 4. SFT training | Teacher → student SFT on a GPU host. Track via the model registry tuple from item #4. | 2 wk |
| 5. DPO | Preference fine-tune using instructor edits. | 2 wk |
| 6. Eval | Run student through the harness from item #3; compare to champion. | 1 wk |
| 7. Shadow → promote | Use item #4's shadow runner to compare in production for ≥ 2 weeks before making student the champion. | 3 wk |

**Artifacts.** `distillation_dataset` table; cleaning pipeline doc; SFT + DPO checkpoints registered in the model registry; eval delta report; promotion log.

**Risks / gotchas.**

- **Long-tail collapse** — distilled models forget rare topics first. Guard with a stratified eval set covering rare modules.
- **Teacher bias inheritance** — student inherits whatever the teacher already gets wrong. Counter with the DPO step on instructor edits, where humans corrected the teacher.
- **Privacy** — production logs almost certainly contain student PII; **de-identification before training is mandatory**, and re-trigger steps 13–18 of the security audit.
- **Hardware** — local Apple Silicon is fine for SFT on a few thousand examples; serious runs (DPO, larger datasets) need a CUDA box or a brief cloud rental — that creates a *new* third-party-subprocessor finding for the security audit.
- **Diminishing returns** — for many course-Q&A workloads, retrieval improvements (items #3, #4) and quantisation will close most of the latency gap before distillation pays off. **Do quantisation and reranker-quality work first.**

**Done criteria.** Student model serves on the same host with ≥ 2× throughput, ≤ 5% absolute drop on harness metrics, and ≥ 2 weeks of clean shadow-mode comparison; privacy review filed in `audit-reports/`.

**Effort.** ~10–12 engineer-weeks **after** the prerequisites (logging infrastructure, registry, eval harness) exist; otherwise add those (~10 wk) on top.

---

## Cross-cutting notes

- **Order matters.** Items **#3 (CI eval) and #4 (registry/champion–challenger)** are foundations — the other four reference them constantly. Build them first.
- **Security touchpoints.** Items #1, #2, and #6 each create new data that is more sensitive than what the project handles today (instructor grades, learner memory, raw Q&A logs with PII). Each one should re-trigger the relevant `audit-reports/` re-audit triggers (notably steps 13–18 — Canada/sovereignty — for #2 and #6).
- **No item here requires an external subprocessor today.** All can be built with Ollama + Qdrant + Postgres + GitHub Actions on your own runner. If an item starts pulling in a SaaS (e.g. cloud DPO training, hosted MLflow, a remote LLM-as-judge), record it as a new third-party data path before the work begins.

---

## Cross-references

- ML enhancement review (parent): [`../ml-enhancement-reviews/2026-05-09-neuro-moodle-llm-ml-enhancements.md`](../ml-enhancement-reviews/2026-05-09-neuro-moodle-llm-ml-enhancements.md)
- Security audit (constraints): [`../audit-reports/2026-05-09-neuro-moodle-llm-audit.md`](../audit-reports/2026-05-09-neuro-moodle-llm-audit.md)
- Stack architecture: [`../README.md`](../README.md) → *Architecture* section
- Project objectives: [`../instructions.txt`](../instructions.txt)
