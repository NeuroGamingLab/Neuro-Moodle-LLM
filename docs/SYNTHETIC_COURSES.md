# Synthetic course generator (reference)

> Generate complete courses (outline → module bodies → eval questions) with **Ollama** and ingest them through the **same chunk → embed-cache → Qdrant pipeline** as real Moodle ingest. Lets you stand up the system, the dashboard, and the eval harness with zero real student data.
>
> **Operational walkthrough (per-step what-happens-and-how-to-verify):** [`SYNTHETIC_COURSES_WALKTHROUGH.md`](SYNTHETIC_COURSES_WALKTHROUGH.md).

- **Module:** `src/neuro_moodle_llm/synthetic_course.py`
- **HTTP:** `POST /v1/synth/course`
- **CLI:** `neuro-moodle-llm synth-course`
- **Streamlit:** `Synthetic Course` page (10) at `http://localhost:8501`
- **Eval JSONL:** `data/eval/golden_synthetic_<course_id>.jsonl`

---

## Why this exists

The enhancement review (`ml-enhancement-reviews/2026-05-09-neuro-moodle-llm-ml-enhancements.md`) calls out three things that were blocked on **having real course content**:

1. **Continuous evaluation in CI** — needed a golden set, which needs a corpus.
2. **Drift / quality monitoring** — needed a baseline distribution to monitor against.
3. **DPO export warm-up** — needed instructor-edit pairs we did not yet have at volume.

Synthetic courses unblock all three: the generator emits a corpus **and** a matching golden eval JSONL in one call, and the eval harness can run end-to-end immediately. It also gives the **Streamlit dashboard** something to demo against without recruiting students.

---

## Quick start

```bash
# 1. Stack up + Ollama models pulled (one-time)
docker compose up -d --build
docker compose exec ollama ollama pull llama3.2
docker compose exec ollama ollama pull nomic-embed-text

# 2. Generate a small synthetic course (course_id >= 90000 by convention)
neuro-moodle-llm synth-course \
  --course-id 90001 \
  --topic "Introduction to gradient descent" \
  --weeks 2 --modules-per-week 2 --questions-per-module 2 --seed 42

# 3. Ask the synthetic course (the same RAG pipeline as a real one)
neuro-moodle-llm ask --course-id 90001 \
  --question "What is the role of the learning rate?"

# 4. Run the eval harness against the matching golden JSONL
neuro-moodle-llm eval \
  --label synth-90001 \
  --golden data/eval/golden_synthetic_90001.jsonl
```

HTTP equivalent of step 2 (works in `http://localhost:8888/docs` Swagger too):

```bash
curl -sS -X POST http://localhost:8888/v1/synth/course \
  -H 'Content-Type: application/json' \
  -d '{
        "course_id": 90001,
        "topic": "Introduction to gradient descent",
        "weeks": 2,
        "modules_per_week": 2,
        "questions_per_module": 2,
        "seed": 42
      }'
```

Streamlit: open **http://localhost:8501**, sidebar → **Synthetic Course**, fill the form, hit **Generate & ingest**.

---

## What gets generated

For each call the generator runs three Ollama chat passes per module + one outline pass:

| Pass | Output | Temperature | Persisted to |
|------|--------|-------------|--------------|
| **Outline (skeleton)** | JSON: `course_title`, `weeks[]`, `modules[]`, `objectives[]` | 0.55 | Returned in the response (`skeleton`) |
| **Module body** (per module) | Markdown: `# Title`, `##`/`###` sections (~400–900 words) | 0.45 | Qdrant points (chunked) |
| **Questions** (per module) | JSON: `question`, `expected_topics`, `must_cite` | 0.35 | `data/eval/golden_synthetic_<course_id>.jsonl` |

All three system prompts live as constants in `synthetic_course.py` (`_SKELETON_SYS`, `_MODULE_SYS`, `_QUESTIONS_SYS`) — edit them there, no other wiring required.

### Qdrant point payload

Every point upserted by the synthetic ingest carries the **standard ingest payload + lineage** plus four synthetic-only fields:

```json
{
  "course_id": 90001,
  "section_id": 1,
  "section_name": "Week 1",
  "module_id": 900001,
  "module_name": "What is gradient descent?",
  "modtype": "synthetic",
  "title": "Week 1: What is gradient descent?",
  "heading_path": "Week 1: What is gradient descent? > ## Intuition",
  "url": null,
  "chunk_no": 0,
  "text": "...",

  "ingest_run_id": "0001731288000000-abcd1234",
  "source_hash": "…sha1…",
  "embed_model": "nomic-embed-text",
  "chunker_version": "semantic-v1",
  "ingested_at": "2026-05-09T19:55:00+00:00",

  "provenance": "synthetic",
  "synth_topic": "Introduction to gradient descent",
  "synth_seed": 42,
  "synth_run_id": "0001731288000000-efgh5678",
  "synth_course_title": "Introduction to gradient descent"
}
```

That gives you two filtering levers (`provenance`, `synth_run_id`) plus the existing `course_id` filter — see **Cleanup / hygiene** below.

### Eval JSONL format

`data/eval/golden_synthetic_<course_id>.jsonl` (also the format consumed by `eval.evaluate(load_golden=…)`):

```jsonl
{"course_id": 90001, "question": "What does the learning rate control?", "expected_topics": ["learning rate", "step size"], "must_cite": "Week 1: What is gradient descent?"}
```

`must_cite` is set to the synthetic chunk **`title`** verbatim (`Week N: <module title>`) so the existing `must_cite_hit@k` metric in `eval.py` works without modification.

---

## API / CLI / dashboard reference

### `POST /v1/synth/course`

Request body (`SynthCourseBody` in `api/main.py`):

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `course_id` | int | — | **Recommend `>= 90000`**; lower IDs are rejected with HTTP 400 unless `allow_low_course_id=true`. |
| `topic` | string | — | 2–500 chars. Free-form; the more specific, the better the outline. |
| `weeks` | int | 2 | 1–16. |
| `modules_per_week` | int | 2 | 1–8. |
| `questions_per_module` | int | 2 | 0–10. Set to 0 to skip the question pass entirely. |
| `seed` | int | 42 | Forwarded to Ollama `options.seed` for reproducibility (model-dependent). |
| `no_replace` | bool | `false` | If `true`, **merge** with existing vectors for this `course_id`; default deletes them first. |
| `write_golden` | bool | `true` | Write `data/eval/golden_synthetic_<course_id>.jsonl`. |
| `append_golden_to_primary` | bool | `false` | Also append rows to `data/eval/golden.jsonl` (the harness default). |
| `allow_low_course_id` | bool | `false` | Bypass the `>= 90000` guard. |
| `publish_to_moodle` | bool | `false` | **Phase A + B + C.** Also create a real Moodle course shell + Page resources; re-ingest under the real Moodle course id; retag the golden file accordingly. |
| `publish_quizzes` | bool | `true` | Effective only when `publish_to_moodle=true`. Adds a multichoice Quiz activity per module (1 correct + 3 distractors via Ollama). |

Response (abridged):

```json
{
  "course_id": 90001,
  "topic": "...",
  "synth_run_id": "0001731288000000-efgh5678",
  "skeleton": { "course_title": "...", "weeks": [ ... ] },
  "ingest": {
    "documents": 4, "chunks": 12, "vectors": 12,
    "embeddings_from_cache": 0, "embeddings_new": 12,
    "embed_model": "nomic-embed-text",
    "ingest_run_id": "..."
  },
  "golden": { "written": true, "golden_file": "...", "n_lines": 8, "appended_to_golden_jsonl": 0 },
  "n_golden_questions": 8
}
```

### CLI

```
neuro-moodle-llm synth-course \
  --course-id 90001 --topic "..." \
  [--weeks N] [--modules-per-week N] [--questions-per-module N] \
  [--seed N] [--no-replace] [--no-golden] [--append-golden] \
  [--allow-low-course-id] \
  [--publish-to-moodle] [--no-quizzes]

# Closed-loop eval of one Moodle quiz attempt (Phase C):
neuro-moodle-llm eval-quiz --attempt-id <id> [--course-id <real_moodle_id>]

# Cleanup: delete a published synthetic course (Moodle + Qdrant) — refuses non-`synth-` idnumbers.
neuro-moodle-llm purge-synth --course-id <real_moodle_id> [--keep-qdrant]
```

### Streamlit page

**Synthetic Course** (page 10) wraps the same body in a form, shows the streaming spinner, and renders the response (skeleton + ingest stats + golden info). It calls `POST /v1/synth/course` over the in-Compose service-name DNS.

When **Publish to Moodle** is checked the page also surfaces:

- The real Moodle course id + a deep link (`/course/view.php?id=<id>`).
- Page / quiz creation counts and the re-ingest stats from the Moodle pull.
- A **Closed-loop quiz-attempt eval** panel — paste an `attempt_id` and trigger `POST /v1/eval/quiz_attempt` (Phase C) without leaving the dashboard.
- A **Cleanup** panel that calls `POST /v1/synth/purge` (gated by the `synth-` `idnumber` guard).

### Eval against the synthetic golden file

CLI:

```bash
neuro-moodle-llm eval --label synth-90001 \
  --golden data/eval/golden_synthetic_90001.jsonl
```

HTTP — `golden_path` must be a path **inside the API container** (the repo `./data` is bind-mounted at `/app/data`):

```bash
curl -sS -X POST http://localhost:8888/v1/eval/run \
  -H 'Content-Type: application/json' \
  -d '{
        "label": "synth-90001",
        "top_k": 5,
        "candidate_k": 20,
        "use_hybrid": true,
        "use_rerank": true,
        "golden_path": "/app/data/eval/golden_synthetic_90001.jsonl"
      }'
```

Streamlit: **Eval & Monitor** page → enter the same path in **`golden_path (optional)`** and click **Run eval**.

---

## Conventions and trade-offs

### Course IDs

`course_id >= 90000` is enforced unless you opt out. Pick a stable range per generator run (e.g. `90001` for "gradient-descent demo", `90002` for "Bayes demo") so you can:

- Filter / purge synthetic vectors with one Qdrant condition.
- Eval each synthetic course independently (one golden file per id).
- Avoid colliding with real Moodle course ids (typically small ints).

### Reproducibility

`seed` is forwarded to Ollama's `options.seed`. With Llama 3.2 the same `(seed, prompt, model)` should re-generate the same outline; the module-body and question passes vary the seed deterministically per module so re-runs of the same call produce the same corpus. Reproducibility is **best-effort** — model upgrades change outputs.

### Hallucinations are part of the design

The generator does **not** check facts. Synthetic content can contain confidently-wrong material; the system will then "correctly" retrieve hallucinations. That is acceptable for **pipeline evaluation** (does retrieval find the chunk? does the answer cite it?) but misleading for **content evaluation** (is the answer true?). Keep synthetic and real ingestion clearly separated by `course_id` and by `provenance` payload.

### Cost and latency

Per generated course: one outline call + `weeks * modules_per_week` body calls + `weeks * modules_per_week` question calls + `chunks` embeddings (cache-hit on regenerations of the same outline).

A `2 × 2 × 2` run is roughly:

- Llama 3.2 3B on a laptop: ~30–90s.
- Larger chat models (e.g. Qwen2.5 14B): 5–15min.
- Embedding cache (`embedding_cache.py`) means **regenerating the same outline is free** for the embed pass.

The HTTP / Streamlit timeouts are set to **60 minutes** for `/v1/synth/course`; the CLI has no timeout. Watch the `neuro-moodle-llm` container logs (`docker logs -f neuro-moodle-llm`) if you want progress.

---

## Publish into Moodle (Route A: Phase A + B + C)

Setting `publish_to_moodle=true` flips the generator from "Qdrant-only" mode into a four-step pipeline:

1. **Phase A — Course shell + Pages.** Calls the `local_neurollm` plugin's `local_neurollm_create_course` and `local_neurollm_create_page` external functions. Idempotent on `idnumber` (`synth-<seed>-<slug(topic)>` for the course, `synth-page-<course_id>-<module_id>` for each page). The synthetic Markdown body is converted to safe HTML (`moodle_authoring.markdown_to_simple_html`) before insertion.
2. **Phase B — Multichoice quizzes.** When `publish_quizzes=true`, every synthetic question is expanded into a multichoice item (correct + 3 distractors via Ollama, system prompt `_DISTRACTORS_SYS` in `synthetic_course.py`) and published with `local_neurollm_create_quiz_with_questions`. The `must_cite` and `expected_topics` ground truth is JSON-encoded into each question's `generalfeedback` field so Phase C can read it back without a separate datastore.
3. **Re-ingest from Moodle.** The initial Qdrant ingest is **skipped**; instead the pipeline calls `ingest.ingest_course(real_moodle_course_id)` after publishing. This guarantees the vectors carry the **real** Moodle `course_id` (so RAG queries from the LMS work) and use the **bare Page name** as `title` (so `must_cite_hit@k` works without a prefix mismatch).
4. **Retag the golden file.** `synthetic_course._retag_golden_rows` rewrites `course_id` → real Moodle id and `must_cite` → bare page title before writing `data/eval/golden_synthetic_<real_moodle_course_id>.jsonl`. A sibling `data/eval/quiz_eval_meta_<real_moodle_course_id>.json` records the per-question quiz id / correct option for later debugging.

### Phase C — Closed-loop quiz-attempt eval

Two independent paths feed `quiz_eval.evaluate_quiz_attempt`:

- **Event-driven.** The `local_neurollm` plugin registers a `\mod_quiz\event\attempt_submitted` observer (`db/events.php` → `classes/observer.php`) that POSTs `{ event, attempt_id, course_id, secret }` to `<webhook_base_url>/v1/events/moodle`. `events._handle_quiz_attempt` then calls the same evaluator. Configure both `webhook_base_url` and `event_secret` under **Site administration → Plugins → Local plugins → NeuroGamingLab integration** (or seed them in `bootstrap-webservice.php`).
- **On-demand.** `POST /v1/eval/quiz_attempt`, `neuro-moodle-llm eval-quiz`, or the Streamlit panel call the evaluator directly — useful for backfills and for environments where the observer can't reach the API container.

For each question in the attempt, `quiz_eval` does:

1. Fetch the attempt (`local_neurollm_get_quiz_attempt`) — returns stem, learner answer, correctness, plus the synthetic ground truth.
2. Run `RetrieverAgent` against the real Moodle `course_id`.
3. Score `agent_must_cite_hit` (does any retrieved chunk's `title` equal the synthetic `must_cite`?) and `agent_topic_recall` (fraction of `expected_topics` present in the concatenated retrieved text, case-insensitive).
4. Persist a per-attempt JSON report to `data/monitoring/quiz_eval_<attempt_id>.json` with both the learner-side correctness and the agent-side retrieval signal.

### Cleanup of published courses

```bash
neuro-moodle-llm purge-synth --course-id 17     # the real Moodle id printed by the publish step
```

The CLI calls `POST /v1/synth/purge` → `moodle_authoring.delete_synth_course` (Moodle-side guard: idnumber must start with `synth-`) and then deletes Qdrant points for that `course_id` unless `--keep-qdrant`. Streamlit exposes the same button.

---

## Cleanup / hygiene

### Drop everything synthetic for a course

```bash
docker exec qdrant curl -sS -X POST \
  http://localhost:6333/collections/course_content/points/delete \
  -H 'Content-Type: application/json' \
  -d '{
        "filter": {
          "must": [
            {"key":"course_id","match":{"value":90001}},
            {"key":"provenance","match":{"value":"synthetic"}}
          ]
        }
      }'
```

### Drop only one generation run

Same call, replace the `provenance` clause with:

```json
{"key":"synth_run_id","match":{"value":"0001731288000000-efgh5678"}}
```

(Find the id in the synthesis response or any point payload.)

### Drop the golden JSONL

```bash
rm data/eval/golden_synthetic_90001.jsonl
```

If you used `append_golden_to_primary=true`, also clean rows out of `data/eval/golden.jsonl` (no automatic reverse).

---

## What it changes (and what it doesn't)

**Major modules and surfaces touched by synthetic courses + Moodle publish:**

- `src/neuro_moodle_llm/synthetic_course.py` — Ollama outline, per-module bodies and questions; optional **`publish_to_moodle`** / **`publish_quizzes`**; Ollama multichoice distractors; re-ingest from Moodle under the real `course_id`; golden retag.
- `src/neuro_moodle_llm/moodle_authoring.py` — Python wrappers for **`local_neurollm_*`** web services (create course/page/quiz, delete course, get quiz attempt) + Markdown→HTML for Page content.
- `src/neuro_moodle_llm/ingest.py` — Moodle ingest merges **`mod_page_get_pages_by_courses`** so Page module bodies are not empty after publish.
- `src/neuro_moodle_llm/quiz_eval.py` — Phase C closed-loop eval of a submitted quiz attempt vs synthetic ground truth.
- `src/neuro_moodle_llm/events.py` — Routes **`\mod_quiz\event\attempt_submitted`** on `/v1/events/moodle` to `quiz_eval`.
- `src/neuro_moodle_llm/ollama.py` — Configurable **`OLLAMA_HTTP_TIMEOUT_S`** (default 3600s) for long Ollama calls.
- `src/neuro_moodle_llm/api/main.py` — `POST /v1/synth/course`, `/v1/synth/purge`, `/v1/eval/quiz_attempt`; `504` on Ollama read timeout for synth.
- `src/neuro_moodle_llm/cli.py` — `synth-course`, `purge-synth`, `eval-quiz`; `eval --golden`.
- `streamlit_app/pages/10_Synthetic_Course.py` — publish toggles, Moodle summary, quiz eval panel, purge; `3_Eval_and_Monitor.py` — `golden_path`.
- `moodle_plugins/neurollm/` — PHP external functions (`create_course`, `create_page`, `create_quiz_with_questions`, `delete_course`, `get_quiz_attempt`), `db/services.php`, `db/events.php`, observer, admin settings for webhook URL + secret.
- `docker/bootstrap-webservice.php` — registers **`local_neurollm_*`** on the `neurollm` external service.
- `scripts/test-enhancements.sh`, `ml-enhancement-reviews/IMPLEMENTATION-MAP.md`, `README.md`, `docs/SYNTHETIC_COURSES*.md` — documentation and smoke checks.

**Unchanged at the architectural level:** Moodle **core** tarball and Apache/PHP image base; Qdrant collection name (`course_content`); default chat/embed model tags in `.env.example`; hybrid RAG + reranker + answer-cache **algorithm** in `rag.py` (synthetic and Moodle ingests both produce normal points consumed the same way).

---

## Pitfalls / FAQ

**Q: `/v1/synth/course` returns 504 or the API log shows `ReadTimeout` to Ollama.**

The API uses httpx with **`OLLAMA_HTTP_TIMEOUT_S`** (default 3600s). Pull models (`docker exec ollama ollama pull llama3.2`) and raise the env var if your hardware needs longer per request.

**Q: The skeleton came back empty / not JSON.**
The generator strips ```` ```json ```` fences and parses the rest. If parsing still fails, the call falls back to a single placeholder module so the run never silently no-ops; check the `synth_run_id` in the response and regenerate with a different `seed` or a stronger chat model.

**Q: My eval scores look great. Is the system actually retrieving anything?**
Synthetic eval can be **memorisation**, not generalisation: the chunk is in the index *and* its question is in the golden set. To measure generalisation, regenerate with a different `seed` (different `course_id`) and run eval against the **first** golden file while serving the **second** course — answers should be mostly low-confidence refusals (that's a *good* sign).

**Q: Can I keep multiple synthetic courses indexed at once?**
Yes. They live in the same Qdrant collection but are isolated by `course_id`. RAG queries already filter by `course_id`.

**Q: Can synthetic content leak into a real course's RAG answer?**
Only if a caller queries with that synthetic `course_id`. If you want a stronger boundary, run real ingest into one Qdrant collection and synthetic ingest into another — `vectorstore.py` accepts a collection name via settings, so this is config, not refactor.

**Q: Will this train the model?**
**No.** The generator only writes Qdrant vectors and an eval JSONL. `data/dpo/preferences.jsonl` is the only file used for DPO training, and it is populated by **instructor edits** in the HITL feedback flow, not by synthetic generation. (You *could* later script DPO pairs from synthetic data — that's a separate change.)

---

## Related

- Operational walkthrough (per-step what-happens-and-how-to-verify): [`SYNTHETIC_COURSES_WALKTHROUGH.md`](SYNTHETIC_COURSES_WALKTHROUGH.md)
- Implementation map for everything else: [`../ml-enhancement-reviews/IMPLEMENTATION-MAP.md`](../ml-enhancement-reviews/IMPLEMENTATION-MAP.md)
- Original review that motivated the feature: [`../ml-enhancement-reviews/2026-05-09-neuro-moodle-llm-ml-enhancements.md`](../ml-enhancement-reviews/2026-05-09-neuro-moodle-llm-ml-enhancements.md)
- Top-level architecture + ports: [`../README.md`](../README.md)
- Streamlit dashboard layout: §6b in the implementation map.

---

*Architecture and design: **Dang-Tue Hoang** — AI Engineer.*
