# Synthetic course generator — end-to-end walkthrough

> Companion to [`SYNTHETIC_COURSES.md`](SYNTHETIC_COURSES.md). That document is the **reference** (what to call, what fields, what's in the payload). This document is the **operational walkthrough**: what happens at each step, where in the code, what to observe, and how to verify it.

- Audience: operators, instructors, and ML-ops watching the pipeline run.
- Stack assumed: `docker-run.sh` (or `docker compose up -d --build`) is up; Ollama has `llama3.2` and `nomic-embed-text` pulled; `./data` is bind-mounted into the API container at `/app/data`.
- For long first-token latency on synthetic generation, set **`OLLAMA_HTTP_TIMEOUT_S`** (see `README.md` / `IMPLEMENTATION-MAP.md` §5). **`GET /health/strict`** should show `moodle.ok`, `qdrant.ok`, and `ollama.ok` before you rely on publish-to-Moodle.

---

## 0. Pipeline at a glance

```mermaid
sequenceDiagram
  autonumber
  participant U as You (CLI/curl/Streamlit)
  participant API as neuro-moodle-llm (FastAPI)
  participant SC as synthetic_course.py
  participant O as Ollama (chat + embed)
  participant CHUNK as chunker.py
  participant EC as embed_cache (Qdrant)
  participant Q as course_content (Qdrant)
  participant FS as data/eval/golden_synthetic_<id>.jsonl
  participant EV as eval.py
  participant RAG as rag.py

  U->>API: POST /v1/synth/course {course_id, topic, weeks, modules_per_week, questions_per_module, seed}
  API->>SC: generate_and_ingest(...)

  SC->>O: chat(skeleton, format=json) (one call)
  O-->>SC: {"course_title": ..., "weeks":[ {modules:[...]}, ... ]}

  loop per module
    SC->>O: chat(module body, Markdown)
    O-->>SC: "# Title\n## ...\n" (~600 words)
    SC->>O: chat(questions, format=json)
    O-->>SC: {"questions":[ {question, expected_topics, must_cite}, ... ]}
    SC->>SC: force must_cite = "Week N: <module title>"
  end

  SC->>CHUNK: semantic_chunks(per module body)
  CHUNK-->>SC: heading-aware chunks

  loop per chunk
    SC->>EC: get(source_hash, embed_model)
    alt cache hit
      EC-->>SC: cached vector
    else miss
      SC->>O: /api/embed (batched)
      O-->>SC: vectors
      SC->>EC: put(source_hash → vector)
    end
  end

  alt publish_to_moodle = false
    SC->>Q: upsert points (lineage + provenance:synthetic, synth_*)
  else publish_to_moodle = true
    SC->>API: skip the synthetic-keyed upsert; defer to re-ingest below
    SC->>SC: build Moodle course shell + Page resources via local_neurollm_*
    SC->>O: distractors per question (one chat per Q, format=json)
    SC->>SC: create per-module Quiz with multichoice items via local_neurollm_create_quiz_with_questions
    SC->>SC: ingest_course(real_moodle_id) → Qdrant under the real id
  end
  SC->>FS: write golden JSONL (course_id retagged to real Moodle id; must_cite to Page name)
  SC-->>API: {course_id, synth_course_id, ingest, publish?, golden, n_golden_questions}
  API-->>U: 200 OK + JSON

  Note over U,EV: Eval phase (any time after ingest)

  U->>EV: neuro-moodle-llm eval --golden <FS>
  loop per golden row
    EV->>RAG: ask(question, course_id, top_k, hybrid, rerank)
    RAG->>O: embed(question)
    RAG->>Q: dense + BM25 → RRF → reranker → top_k (filter course_id)
    RAG->>O: chat(system + context + question)
    O-->>RAG: grounded answer
    RAG-->>EV: sources[ {title, heading_path, text_snippet, score, components} ], confidence
    EV->>EV: _score_case → topic_recall@k, must_cite_hit@k, mrr@k
  end
  EV->>EV: write data/eval/runs/<run_id>.json
  EV->>EV: delta_vs_champion (data/eval/champion.json)
  alt --promote
    EV->>EV: champion.json := this run
  end
  EV-->>U: summary JSON
```

---

## 1. Request a synthetic course

Three equivalent entry points:

```bash
# CLI (host venv: pip install -e . in .venv)
.venv/bin/neuro-moodle-llm synth-course \
  --course-id 90001 --topic "Bayes theorem for ML" \
  --weeks 1 --modules-per-week 2 --questions-per-module 2 --seed 7

# curl
curl -sS -X POST http://localhost:8888/v1/synth/course \
  -H 'Content-Type: application/json' \
  -d '{"course_id":90001,"topic":"Bayes theorem for ML","weeks":1,"modules_per_week":2,"questions_per_module":2,"seed":7}'

# Streamlit
# http://localhost:8501 → "Synthetic Course" → fill form → Generate & ingest
```

All three land at **`POST /v1/synth/course`** (`src/neuro_moodle_llm/api/main.py`), which calls **`generate_and_ingest()`** in `src/neuro_moodle_llm/synthetic_course.py`. The route refuses `course_id < 90000` with **`HTTP 400`** unless `allow_low_course_id=true`.

---

## 2. Outline pass — Ollama returns the course skeleton

`generate_skeleton()` makes **one** chat call with `format="json"`:

- System prompt `_SKELETON_SYS` declares the JSON schema (`course_title`, `weeks[]`, `modules[]`, `objectives[]`).
- User prompt: topic, week count, module-per-week count, seed.
- `_chat_json()` parses the response; if the JSON is malformed it retries once with a stricter system prompt; if still malformed the API returns **`HTTP 502`** with a hint to try a different seed or a stronger chat model.

**Verify:**

```bash
docker logs neuro-moodle-llm 2>&1 | grep -E 'JSON parse|/api/chat' | tail -10
```

You should see one `POST /api/chat ... 200 OK` line for the skeleton plus zero or one `JSON parse failed` warning if the first attempt was malformed.

---

## 3. Per-module pass — body + eval questions

For every module in the skeleton (`weeks × modules_per_week` total):

### 3a. Module body (`generate_module_markdown`)

One chat call. Output is **Markdown** starting with `# <module title>`, then `##` / `###` sections (~400–900 words). This goes straight to the chunker; it is **not** parsed as JSON.

### 3b. Questions (`generate_questions_for_module`)

One chat call with `format="json"`. Returns `[{question, expected_topics, must_cite}, ...]`. After parsing, **`must_cite` is overwritten** server-side with `"Week N: <module title>"` so the metric is deterministic and not at the mercy of the LLM:

```184:200:src/neuro_moodle_llm/synthetic_course.py
        out.append(
            {
                "question": q,
                "expected_topics": topics,
                "must_cite": module_title,
            }
        )
    return out
```

If the question pass returns unparseable JSON twice in a row, that module yields **0 questions** and the run continues — the whole course never crashes for one bad module.

---

## 4. Chunk + embed (same path as a real Moodle course)

`generate_and_ingest()` builds one `_RawDoc` per module body and hands the list to **`ingest.ingest_raw_docs()`** — the same function `ingest_course()` uses for Moodle ingest. Inside:

| # | Step | Module |
|---|------|--------|
| 1 | Heading-aware split (H1/H2/H3 boundaries, ~1200 chars, 1-sentence overlap) | `chunker.semantic_chunks` |
| 2 | Per-chunk cache lookup keyed by `sha1(model|chunk_text)` | `embedding_cache.EmbeddingCache.get` |
| 3 | Batch embed cache misses via Ollama `/api/embed`, populate cache | `ollama.OllamaClient.embed_batch` |
| 4 | Upsert points to Qdrant `course_content` with full payload | `vectorstore.VectorStore.upsert` |

Every synthetic point carries the **standard payload + lineage** plus four synthetic-only fields:

```json
{
  "course_id": 90001,
  "title": "Week 1: Defining the Problem",
  "heading_path": "Defining the Problem > Components of Bayes Theorem",
  "text": "...",
  "modtype": "synthetic",

  "ingest_run_id": "...",
  "source_hash": "...",
  "embed_model": "nomic-embed-text",
  "chunker_version": "semantic-v1",
  "ingested_at": "2026-05-09T19:55:00+00:00",

  "provenance": "synthetic",
  "synth_topic": "Bayes theorem for ML",
  "synth_seed": 7,
  "synth_run_id": "...",
  "synth_course_title": "Bayes Theorem for Machine Learning"
}
```

**Verify:**

```bash
curl -sS -X POST http://localhost:6333/collections/course_content/points/scroll \
  -H 'Content-Type: application/json' \
  -d '{"limit":3,"filter":{"must":[{"key":"course_id","match":{"value":90001}}]},"with_payload":true,"with_vector":false}'
```

Distinct content per course (no cache aliasing) is checked by counting distinct `source_hash` values; courses on different topics should share **0** hashes. The embedding cache short-circuits subsequent re-runs of the same course (see `embeddings_from_cache` vs `embeddings_new` in the response).

---

## 5. Persist the golden eval JSONL

If `write_golden=true` (default), `_write_golden_lines()` writes one file per synthetic course:

```
data/eval/golden_synthetic_<course_id>.jsonl
```

Each line:

```json
{"course_id": 90001, "question": "...", "expected_topics": ["..."], "must_cite": "Week N: <module title>"}
```

Optionally, with `append_golden_to_primary=true`, rows are appended to the harness default at `data/eval/golden.jsonl`.

The API response always echoes the file path so callers do not have to guess.

**Verify:**

```bash
ls -la data/eval/golden_synthetic_*.jsonl
cat data/eval/golden_synthetic_90001.jsonl
```

---

## 6. Run the eval harness

`eval.evaluate(golden_path=...)` walks each row and calls the **production** RAG path (`rag.ask`):

```bash
# CLI
.venv/bin/neuro-moodle-llm eval --label synth-90001 \
  --golden data/eval/golden_synthetic_90001.jsonl \
  [--promote]

# HTTP — golden_path is the path inside the API container
curl -sS -X POST http://localhost:8888/v1/eval/run \
  -H 'Content-Type: application/json' \
  -d '{"label":"synth-90001","golden_path":"/app/data/eval/golden_synthetic_90001.jsonl"}' \
  | python3 -m json.tool

# Streamlit: "Eval & Monitor" → paste the same path → Run eval
```

For each question:

1. `rag.ask` embeds the question, runs hybrid (dense + BM25) → RRF → reranker → top-k filtered by `course_id`.
2. Returns `sources[]` containing `title`, `heading_path`, **`text_snippet`** (first 800 chars of the chunk body — required by the metrics below), `score`, `components`, `confidence`.
3. `eval._score_case` computes:

| Metric | Definition |
|--------|------------|
| `must_cite_hit@k` | `1.0` if any source `title` startswith `case.must_cite`, else `0.0`. |
| `topic_recall@k` | Fraction of `expected_topics` substrings present in `title + heading_path + text_snippet` across top-k (case-insensitive). |
| `mrr@k` | Reciprocal rank of the first source whose `title + heading_path + text_snippet` contains any expected topic. |
| `confidence` | Returned by `rag.ask` (RRF / reranker score floor). |
| `latency_ms` | Wall-clock per question. |

Aggregates are written to **`data/eval/runs/<ts>-<label>.json`**, the response shows `delta_vs_champion` against **`data/eval/champion.json`**, and `--promote` overwrites `champion.json` with this run's summary.

### Key metric notes

- **`text_snippet` exists on every RAG response** (`rag.py` adds it to the source dict, capped at 800 chars). API/Streamlit/Moodle plugin already ignore unknown fields, so this is a backward-compatible addition.
- **`must_cite` is forced to `"Week N: <module title>"`** in `synthetic_course.py`, so `must_cite_hit@k` measures retrieval quality, not LLM-prompt-following.

---

## 7. Champion vs challenger (the CI signal)

Once one run is `--promote`d, every subsequent eval prints a `delta_vs_champion` block:

```json
"delta_vs_champion": {
  "champion_run_id": "1778386649-synth-90001-rescore",
  "delta": {
    "topic_recall@k": 0.0,
    "must_cite_hit@k": 0.0,
    "mrr@k": 0.0,
    "confidence": 0.301,
    "latency_ms_p50": 692.6,
    "latency_ms_avg": 692.6
  }
}
```

A CI job parses that JSON and fails the build on `delta < threshold` for any chosen metric — the same wiring lives in `eval.py`'s output and in `data/eval/runs/<ts>-<label>.json` on disk.

---

## 8. End-to-end demo (copy/paste)

```bash
# 1. Generate two synthetic courses on the same topic (different seeds)
for cid in 90011 90012; do
  curl -sS -X POST http://localhost:8888/v1/synth/course \
    -H 'Content-Type: application/json' \
    -d "{\"course_id\":$cid,\"topic\":\"Linear regression intuition\",\"weeks\":1,\"modules_per_week\":2,\"questions_per_module\":2,\"seed\":$cid}" \
    -o /tmp/synth_$cid.json -w "course=$cid HTTP %{http_code} time=%{time_total}s\n"
done

# 2. Promote the first as champion
.venv/bin/neuro-moodle-llm eval --label baseline --promote \
  --golden data/eval/golden_synthetic_90011.jsonl

# 3. Eval the second; the output shows delta_vs_champion
.venv/bin/neuro-moodle-llm eval --label challenger \
  --golden data/eval/golden_synthetic_90012.jsonl

# 4. Inspect artefacts
ls -la data/eval/runs | tail -5
cat data/eval/champion.json
```

Streamlit reflects the same data without any extra step:

- **Eval & Monitor → Recent runs** lists the two new runs.
- **Synthetic Course** page can drive steps 1–3 from a form.

---

## 9. What lives where (cheat sheet)

| Concern | File / route |
|---|---|
| Generator | `src/neuro_moodle_llm/synthetic_course.py` |
| Shared chunk → embed → upsert | `src/neuro_moodle_llm/ingest.py::ingest_raw_docs` |
| Ollama JSON-mode wrapper | `src/neuro_moodle_llm/ollama.py::OllamaClient.chat(format="json")` |
| Lineage payload (+ `synth_*` extras) | `src/neuro_moodle_llm/lineage.py` (+ extras in `synthetic_course.py`) |
| Embedding cache | `src/neuro_moodle_llm/embedding_cache.py` |
| Eval harness | `src/neuro_moodle_llm/eval.py` |
| Source field used by metrics | `src/neuro_moodle_llm/rag.py` (`text_snippet`) |
| HTTP route | `src/neuro_moodle_llm/api/main.py::synth_course_api` (+ `eval_run` `golden_path`) |
| CLI | `src/neuro_moodle_llm/cli.py::cmd_synth_course`, `cmd_eval` |
| Streamlit pages | `streamlit_app/pages/10_Synthetic_Course.py`, `3_Eval_and_Monitor.py` |
| Golden JSONL output | `data/eval/golden_synthetic_<course_id>.jsonl` |
| Run history | `data/eval/runs/<ts>-<label>.json` |
| Champion record | `data/eval/champion.json` |
| Reference (what / fields / cleanup) | [`SYNTHETIC_COURSES.md`](SYNTHETIC_COURSES.md) |

---

## 10. Cleanup

```bash
# Drop one synthetic course's vectors (Qdrant filter)
docker exec qdrant curl -sS -X POST http://localhost:6333/collections/course_content/points/delete \
  -H 'Content-Type: application/json' \
  -d '{"filter":{"must":[
        {"key":"course_id","match":{"value":90001}},
        {"key":"provenance","match":{"value":"synthetic"}}
      ]}}'

# Drop the golden file
rm data/eval/golden_synthetic_90001.jsonl

# data/eval/runs/*.json is intentionally kept as eval audit history; delete by hand if not wanted
```

---

## 11. Publish synthetic courses into Moodle (Phase A + B + C)

When you want the synthetic course to live as a **real Moodle course** that learners can browse, take quizzes from, and trigger closed-loop agentic eval, set `publish_to_moodle: true` on the same `/v1/synth/course` call (or pass `--publish-to-moodle` on the CLI).

```bash
curl -sS -X POST http://localhost:8888/v1/synth/course \
  -H 'Content-Type: application/json' \
  -d '{
    "course_id": 90099,
    "topic": "Naive Bayes classifiers",
    "weeks": 1, "modules_per_week": 1, "questions_per_module": 1,
    "seed": 71,
    "publish_to_moodle": true,
    "publish_quizzes": true
  }'
```

What changes vs. preview-only:

1. **Phase A — course shell + Page resources** (`local_neurollm_create_course` and `local_neurollm_create_page` in `moodle_plugins/neurollm/classes/external/`). The plugin auto-creates a "Synthetic / Not for production" category if missing and uses a stable `idnumber` (`synth-<topic-slug>-<runid>`) so re-runs with the same `synth_run_id` are idempotent.
2. **Phase B — Quiz activity per module** (`local_neurollm_create_quiz_with_questions`). For each golden question we ask Ollama for one correct answer + three distractors (`generate_multichoice_for_question`), then write a multichoice question into a **course-level question category** named `Neuro synthetic` and add it to a freshly created Quiz. The synthetic ground truth (`must_cite`, `expected_topics`) is encoded in each question's `generalfeedback` field as JSON so the closed-loop eval can read it back without a sidecar table.
3. **Re-ingest from Moodle**: after publishing, the function calls `ingest_course(real_moodle_id)` so Qdrant carries the real Moodle course id (the synthetic preview pass is skipped to avoid duplicate vectors). Vectors flow through the normal ingest pipeline including the new `mod_page_get_pages_by_courses` call (Page bodies aren't returned inline by `core_course_get_contents`).
4. **Golden retag**: the `course_id` and `must_cite` in `data/eval/golden_synthetic_<id>.jsonl` are rewritten to the real Moodle course id and the Page name (no `Week N:` prefix), so the same eval harness scores correctly against the published course.

The response gains a `publish` block:

```json
{
  "course_id": 7,                  // real Moodle course id (use this everywhere from now on)
  "synth_course_id": 90099,        // your original placeholder
  "synth_run_id": "...",
  "publish": {
    "course_id": 7,
    "shortname": "synth-naive-bayes-classifiers-177839",
    "idnumber":  "synth-naive-bayes-classifiers-177839050219",
    "view_url":  "http://localhost:8080/course/view.php?id=7",
    "created_course": true,
    "pages":   [ { "module_title": "...", "section_num": 1, "cmid": 11, "view_url": "..." } ],
    "quizzes": [ { "module_title": "...", "quiz_id": 8, "cmid": 12, "view_url": "...", "n_questions": 1 } ],
    "quiz_eval_meta_file": "/app/data/eval/quiz_eval_meta_7.json",
    "reingest_from_moodle": { "documents": 1, "chunks": 3, "vectors": 3, "embeddings_new": 3 }
  },
  "golden": { "golden_file": "/app/data/eval/golden_synthetic_7.jsonl", "n_lines": 1, "written": true }
}
```

### Closed-loop agentic citation eval (Phase C)

Once a learner submits one of the published quizzes, the plugin's `\mod_quiz\event\attempt_submitted` observer (in `moodle_plugins/neurollm/classes/observer.php`) POSTs `{eventname, courseid, objecttable: "quiz_attempts", objectid, secret}` to `/v1/events/moodle`. The `events.handle_event` router recognises the quiz-attempt event and dispatches to `quiz_eval.evaluate_quiz_attempt`, which:

1. Pulls the attempt via `local_neurollm_get_quiz_attempt` (stem, learner answer, correctness, `must_cite`, `expected_topics`).
2. For each question, runs the **agentic Retriever** against the published course's vectors.
3. Scores `agent_must_cite_hit` (any retrieved source title startswith `must_cite`) and `agent_topic_recall` (fraction of `expected_topics` present in the haystack of retrieved chunks).
4. Writes `data/monitoring/quiz_eval_<attempt_id>.json` for the dashboard to surface.

Programmatic loop (no event needed):

```bash
curl -sS -X POST http://localhost:8888/v1/eval/quiz_attempt \
  -H 'Content-Type: application/json' \
  -d '{"attempt_id": 6, "course_id": 7, "top_k": 3}'

# or via CLI
.venv/bin/neuro-moodle-llm eval-quiz --attempt-id 6 --course-id 7
```

For the observer path you need to:

1. Add `NEURO_EVENT_SECRET=<random-hex>` to `.env` and restart the API container.
2. Configure the same value plus the in-network webhook URL in Moodle:

   ```bash
   docker exec moodle php -r "
     define('CLI_SCRIPT', true); require('/var/www/html/config.php');
     set_config('event_secret', getenv('SECRET'), 'local_neurollm');
     set_config('webhook_base_url', 'http://neuro-moodle-llm:8888', 'local_neurollm');"
   ```

The webhook is best-effort with a 4 s timeout; if the API is down the learner's submission is unaffected.

### Cleanup

`local_neurollm_delete_course` refuses any course whose `idnumber` doesn't begin with `synth-`, so the API can never delete a real instructor-authored course by accident. Use either route:

```bash
curl -sS -X POST http://localhost:8888/v1/synth/purge \
  -H 'Content-Type: application/json' -d '{"course_id": 7, "delete_qdrant": true}'

# CLI
.venv/bin/neuro-moodle-llm purge-synth --course-id 7
```

Both delete the Moodle course (cascading to pages, quizzes, attempts) and remove the matching Qdrant vectors. The Streamlit Synthetic Course page exposes the same purge action under "Cleanup".

---

## 12. Common failure modes and how to read them

| Symptom | Where it surfaces | Likely cause | Fix |
|---|---|---|---|
| `HTTP 504` on `/v1/synth/course` or log shows `ReadTimeout` to Ollama | API / container logs | First chat/embed call exceeded **`OLLAMA_HTTP_TIMEOUT_S`** | Default 3600s in `config.py`; set `OLLAMA_HTTP_TIMEOUT_S` in `.env`, restart API; `docker exec ollama ollama pull <OLLAMA_CHAT_MODEL>`. |
| **`GET /health/strict` → `moodle.ok: false`** (`ConnectError` / `Name or service not known`) | Streamlit Home or curl | `moodle` container not on the same Docker network or crash-looping | `docker ps` — ensure `moodle` is **Up**; if entrypoint says `config.php missing` with DB already initialised, recover `config.php` (see README / ops notes) or follow entrypoint guidance. |
| `HTTP 400 ... below recommended minimum` | `/v1/synth/course` response | `course_id < 90000` | Pick a higher id, or pass `"allow_low_course_id": true`. |
| `HTTP 502 ... Ollama returned non-JSON` | `/v1/synth/course` response | Skeleton failed JSON parse twice with `format="json"` | Try a different `seed`, or pull a stronger chat model. |
| Run completes but `n_golden_questions` < expected | API response | One module's question pass returned unparseable JSON; that module is skipped (logged warning) | Re-run with a different seed; check container logs. |
| `must_cite_hit@k` low for all questions | Eval summary | Synthetic vectors not in the index for that `course_id`, or chunk titles drifted | Confirm Qdrant has points for that `course_id` (Step 4 verify); regenerate. |
| `embeddings_from_cache` is high on a *brand new* course | API response | The same `(model, chunk_text)` was seen earlier (e.g. an aborted previous attempt) | Inspect with the cache-presence check in Step 4. Not a bug. |
| Eval CLI crashes with `AttributeError: 'Namespace' object has no attribute 'promote'` | CLI traceback | Old code path; this was fixed | Pull latest, restart venv. |
| `publish.reingest_from_moodle` shows `chunks: 0, vectors: 0` after a successful publish | API response | Page bodies aren't being fetched | Make sure the API container has the latest `ingest._collect_course_docs` (calls `mod_page_get_pages_by_courses`). Rebuild with `docker build --no-cache -t neuro-moodle-llm/api:local -f Dockerfile.neuro .` and restart. |
| `local_neurollm_*` WS error `Web service is not available` from curl | Moodle response | The plugin functions aren't attached to the `neurollm` service | Re-run `docker exec moodle php /tmp/bootstrap-webservice.php` (idempotent — appends new function rows to `external_services_functions`). |
| `dml_write_exception ... null value in column "password"` | Apache error log | Old code that called `add_moduleinfo()` on the quiz/page is in the container | Pull latest plugin code; the new external functions insert directly into `mdl_quiz` / `mdl_page` and bypass the form pipeline. |
| `Call to undefined function quiz_update_sumgrades` | Apache error log | Moodle 5 deprecated this; the plugin now calls `\mod_quiz\quiz_settings::create()->get_grade_calculator()->recompute_quiz_sumgrades()` | Pull latest `create_quiz_with_questions.php` and run `docker exec moodle apache2ctl graceful` to flush opcache. |
| `quiz_eval` writes `agent_must_cite_hit: 0` for every question | `data/monitoring/quiz_eval_*.json` | The `must_cite` doesn't match the Moodle Page name (e.g. has a `Week N:` prefix that the source title lacks) | Synthetic flow already aligns these via `_retag_golden_rows` + the multichoice publish path; if you're using a hand-curated golden, set `must_cite` to the bare Page name. |

---

## 13. Related documents

- Reference: [`SYNTHETIC_COURSES.md`](SYNTHETIC_COURSES.md)
- Implementation map for everything else: [`../ml-enhancement-reviews/IMPLEMENTATION-MAP.md`](../ml-enhancement-reviews/IMPLEMENTATION-MAP.md)
- Original review that motivated the feature: [`../ml-enhancement-reviews/2026-05-09-neuro-moodle-llm-ml-enhancements.md`](../ml-enhancement-reviews/2026-05-09-neuro-moodle-llm-ml-enhancements.md)
- Architecture, ports, services: [`../README.md`](../README.md)
- Streamlit dashboard layout: §6b in the implementation map.

---

*Architecture and design: **Dang-Tue Hoang** — AI Engineer.*
