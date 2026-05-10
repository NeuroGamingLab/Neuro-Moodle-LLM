# audit-reports/

Security and operational audit reports for this project, kept in version
control as a record of what was reviewed, when, and what was decided.

## Conventions

- **Filename:** `YYYY-MM-DD-<scope>-audit.md` (e.g.
  `2026-05-09-neuro-moodle-llm-audit.md`).
- **Scope** is usually the project name; for targeted reviews use the path or
  feature (e.g. `2026-06-01-bootstrap-webservice-audit.md`).
- One audit per file. **Never edit a past report** to "fix" findings — write
  a new report and reference the old one in its `Executive summary`.
- Reports are produced with the **security-audit-guard** skill; see
  `~/.cursor/skills/security-audit-guard/` for the rubric and rating labels.

## Redaction rules

Audit reports may quote configuration files and snippets. Before committing:

- Redact live secrets (tokens, passwords, private keys). Refer to them by
  variable name and location (e.g. "the `MOODLE_TOKEN` in `.env`") rather
  than by literal value.
- Redact internal hostnames, customer identifiers, or student PII if a
  finding required quoting any of those.
- Container image digests, version pins, and SHA-256 hashes of upstream
  artifacts are **not** secrets — keep them in the report so the audit is
  reproducible against a known build.

## Status legend

Each report ends with an overall rating: **Safe**, **Caution**, or **Risky**
(see [`report-template.md`](https://example.invalid) in the
security-audit-guard skill for definitions). The same labels are used per
finding category in the body.

## Re-audit triggers

Re-run the audit and add a new report when any of the following change:

- `Dockerfile.moodle`, `docker/`, `docker-compose.yml`
- `moodle_plugins/neurollm/` external functions, observers, or bootstrap web-service registration
- `.env` schema (new variables, new secrets)
- `src/neuro_moodle_llm/` public API or HTTP surface
- Dependency pins in `pyproject.toml`
- Network exposure (new published ports, new services)
- A new external integration (OpenAI / hosted Qdrant / S3-style storage)
- Before any non-loopback deployment
- Before ingesting real student or other personal data (also expand to
  steps 13–18 of the audit).

---

*Architecture and design: **Dang-Tue Hoang** — AI Engineer.*
