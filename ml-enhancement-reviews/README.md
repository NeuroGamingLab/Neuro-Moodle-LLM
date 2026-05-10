# ml-enhancement-reviews/

ML / AI system enhancement reviews for this project, kept in version control
as a record of what was analysed, when, and what was recommended.

Sister folder of [`../audit-reports/`](../audit-reports/) (security) — same
filing conventions, different lens.

## Conventions

- **Filename:** `YYYY-MM-DD-<scope>-ml-enhancements.md` (e.g.
  `2026-05-09-neuro-moodle-llm-ml-enhancements.md`).
- **Scope** is usually the project name; for targeted reviews use the
  subsystem (e.g. `2026-07-01-rag-retrieval-ml-enhancements.md`).
- One review per file. **Never edit a past review** to "fix" recommendations
  — write a new review and reference the old one in its `Assumptions` or
  `Executive Summary` section.
- Reviews are produced with the **ml-app-enhancement** skill; the section
  order (`Assumptions → Executive Summary → Quick Wins → Strategic
  Improvements → Detailed Analysis → Roadmap`) follows that skill's template
  and should not be reorganised on a whim.

## Style guide for findings

Lead **every** recommendation in the *Detailed Analysis* section with an
impact/effort tag in this exact form:

```text
[Impact: High/Med/Low | Effort: High/Med/Low]
```

This makes it trivial to filter the document for "what should I do this
week?" vs "what's a quarter-scale bet?" and to compare across reviews over
time.

## Re-review triggers

Run a fresh review (and add a new file here) when any of the following
change:

- Embedding model, chat model, reranker, or chunker
- Retrieval pipeline (dense/sparse/hybrid, top-k, filters)
- Ingest pipeline (sources, transforms, lineage fields)
- Moodle integration surface (web-services functions used, plugin features, **synthetic publish / quiz webhook**)
- Throughput / scale targets (single-developer → classroom → institution)
- Constraints (budget, on-prem-only, privacy posture, regulatory scope)
- Whenever a finding from `audit-reports/` materially restricts an ML choice
  (e.g. "no third-party LLM endpoints" rules out cloud reranking)

## Relationship to other folders

| Folder | Lens | Owns the question |
|--------|------|-------------------|
| `audit-reports/` | Security | "What can break or leak?" |
| `ml-enhancement-reviews/` | ML systems | "What can be smarter, faster, or more reliable?" |
| `instructions.txt` + `README.md` | Product / architecture | "What is the system supposed to do?" |

Recommendations in this folder must respect findings in `audit-reports/`. If
a recommendation increases security risk, call it out explicitly and
reference the relevant audit finding.

## Implementation map (review → running system)

After the 2026-05-09 review, the repo ships a **feature-by-feature map** with
HTTP examples, CLI commands, env vars, and **done / partial / not done** status
against the original review sections:

**[IMPLEMENTATION-MAP.md](IMPLEMENTATION-MAP.md)**

Start there when you need to operate or extend retrieval, ingest, agents, or
monitoring without re-reading the whole dated review.

---

*Architecture and design: **Dang-Tue Hoang** — AI Engineer.*
