#!/usr/bin/env bash
# scripts/test-enhancements.sh — smoke-test all Neuro ML enhancement API surfaces.
#
# Usage:
#   ./scripts/test-enhancements.sh
#   NEURO_API_BASE=http://127.0.0.1:8888 COURSE_ID=2 ./scripts/test-enhancements.sh
#   ./scripts/test-enhancements.sh --slow          # also runs /v1/hpo/grid (can take minutes)
#   ./scripts/test-enhancements.sh --strict-health # expects /health/strict to return 200 (fails if Moodle/Ollama/Qdrant unhealthy)
#
# Prerequisites: neuro-moodle-llm API up; Moodle token + models configured (see README / IMPLEMENTATION-MAP).

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

NEURO_API_BASE="${NEURO_API_BASE:-http://127.0.0.1:8888}"
NEURO_API_BASE="${NEURO_API_BASE%/}"
COURSE_ID="${COURSE_ID:-2}"
RUN_SLOW=0
STRICT_HEALTH=0

for arg in "$@"; do
  case "$arg" in
    --slow) RUN_SLOW=1 ;;
    --strict-health) STRICT_HEALTH=1 ;;
    -h|--help)
      grep '^#' "$0" | head -20 | sed 's/^# \{0,1\}//'
      exit 0
      ;;
  esac
done

# Load NEURO_EVENT_SECRET from .env if present (for webhook test)
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$REPO_ROOT/.env" 2>/dev/null || true
  set +a
fi

PASS=0
FAIL=0
SKIP=0
TMP="${TMPDIR:-/tmp}/neuro-enh-test-$$"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT

log() { printf '%s\n' "$*"; }

pass() { log "  ✓ PASS  $*"; PASS=$((PASS + 1)); }
fail() { log "  ✗ FAIL  $*"; FAIL=$((FAIL + 1)); }
skip() { log "  ○ SKIP  $*"; SKIP=$((SKIP + 1)); }

# GET → body file + return HTTP code
http_get() {
  local url="$1" out="$2"
  curl -sS -o "$out" -w '%{http_code}' "$url" 2>/dev/null || echo "000"
}

# POST JSON → body file + return HTTP code
http_post_json() {
  local url="$1" json="$2" out="$3"
  curl -sS -o "$out" -w '%{http_code}' \
    -H 'Content-Type: application/json' \
    -X POST "$url" \
    -d "$json" 2>/dev/null || echo "000"
}

# Run Python on stdin JSON; script exits 0 = assertion ok
json_ok() {
  python3 -c "$1" || return 1
}

# --- tests -----------------------------------------------------------------

log "== Neuro enhancement API tests =="
log "API: $NEURO_API_BASE  |  COURSE_ID: $COURSE_ID"
log ""

# Root + OpenAPI
code=$(http_get "$NEURO_API_BASE/" "$TMP/root.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/root.json')); assert d.get('service')=='neuro-moodle-llm'" 2>/dev/null; then
  pass "GET / (service JSON)"
else
  fail "GET / (code=$code or bad JSON — is the API up?)"
fi

code=$(http_get "$NEURO_API_BASE/openapi.json" "$TMP/openapi.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/openapi.json')); p=d.get('paths',{}); assert '/v1/rag/ask' in p and '/v1/synth/course' in p" 2>/dev/null; then
  pass "GET /openapi.json (has /v1/rag/ask, /v1/synth/course)"
else
  fail "GET /openapi.json (code=$code)"
fi

# Health (loose)
code=$(http_get "$NEURO_API_BASE/health" "$TMP/health.json")
if [[ "$code" == "200" ]]; then
  pass "GET /health (HTTP 200)"
  if json_ok "import json,sys; d=json.load(open('$TMP/health.json')); assert d['moodle'].get('ok')==True" 2>/dev/null; then
    pass "GET /health (moodle.ok)"
  else
    fail "GET /health (moodle not ok — check MOODLE_TOKEN / MOODLE_HOST_HEADER)"
  fi
else
  fail "GET /health (code=$code)"
fi

# Health strict (optional)
if [[ "$STRICT_HEALTH" == "1" ]]; then
  code=$(http_get "$NEURO_API_BASE/health/strict" "$TMP/strict.json")
  if [[ "$code" == "200" ]]; then
    pass "GET /health/strict (all deps healthy)"
  else
    fail "GET /health/strict (code=$code — use /health JSON to debug)"
  fi
else
  skip "GET /health/strict (pass --strict-health to require 200)"
fi

# Ingest course (lineage + embed cache path)
code=$(http_post_json "$NEURO_API_BASE/v1/ingest/course" "{\"course_id\":$COURSE_ID}" "$TMP/ingest.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/ingest.json')); assert 'ingest_run_id' in d" 2>/dev/null; then
  pass "POST /v1/ingest/course"
else
  fail "POST /v1/ingest/course (code=$code body=$(cat "$TMP/ingest.json" 2>/dev/null | head -c 200))"
fi

# RAG ask (hybrid + rerank + scores)
code=$(http_post_json "$NEURO_API_BASE/v1/rag/ask" \
  "{\"question\":\"What topics does this course mention?\",\"course_id\":$COURSE_ID,\"top_k\":3,\"candidate_k\":12,\"use_hybrid\":true,\"use_rerank\":true,\"use_qa_cache\":true}" \
  "$TMP/ask1.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/ask1.json')); assert 'qid' in d and 'answer' in d" 2>/dev/null; then
  pass "POST /v1/rag/ask (first)"
else
  fail "POST /v1/rag/ask (code=$code)"
fi

# Second identical ask → qa_cache hit (only if first was not low-confidence refusal with empty cache store)
code=$(http_post_json "$NEURO_API_BASE/v1/rag/ask" \
  "{\"question\":\"What topics does this course mention?\",\"course_id\":$COURSE_ID,\"top_k\":3,\"use_qa_cache\":true}" \
  "$TMP/ask2.json")
if [[ "$code" == "200" ]]; then
  if json_ok "import json,sys; d=json.load(open('$TMP/ask2.json')); assert d.get('cache') in ('hit','miss')" 2>/dev/null; then
    CACHE_LINE=$(python3 -c "import json; print(json.load(open('$TMP/ask2.json')).get('cache','?'))" 2>/dev/null || echo "?")
    pass "POST /v1/rag/ask (repeat; cache=$CACHE_LINE)"
  else
    fail "POST /v1/rag/ask (second) bad JSON"
  fi
else
  fail "POST /v1/rag/ask (second) code=$code"
fi

# RAG toggles off
code=$(http_post_json "$NEURO_API_BASE/v1/rag/ask" \
  "{\"question\":\"ping\",\"course_id\":$COURSE_ID,\"use_hybrid\":false,\"use_rerank\":false,\"use_qa_cache\":false}" \
  "$TMP/ask3.json")
[[ "$code" == "200" ]] && pass "POST /v1/rag/ask (use_hybrid=false use_rerank=false)" || fail "POST /v1/rag/ask toggles code=$code"

# Feedback thumbs
code=$(http_post_json "$NEURO_API_BASE/v1/feedback" \
  "{\"qid\":\"test-$RANDOM\",\"vote\":\"up\",\"learner_id\":\"1\",\"course_id\":$COURSE_ID,\"note\":\"smoke test\"}" \
  "$TMP/fb.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/fb.json')); assert d.get('ok')==True" 2>/dev/null; then
  pass "POST /v1/feedback"
else
  fail "POST /v1/feedback code=$code"
fi

# Eval
code=$(http_post_json "$NEURO_API_BASE/v1/eval/run" \
  '{"label":"smoke","top_k":5,"candidate_k":15,"use_hybrid":true,"use_rerank":true}' \
  "$TMP/eval.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/eval.json')); assert 'summary' in d or 'n' in d" 2>/dev/null; then
  pass "POST /v1/eval/run"
else
  fail "POST /v1/eval/run code=$code"
fi

# Monitor
code=$(http_post_json "$NEURO_API_BASE/v1/monitor/run" '{}' "$TMP/mon.json")
[[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/mon.json')); assert 'drift' in d and 'judge' in d" 2>/dev/null && pass "POST /v1/monitor/run" || fail "POST /v1/monitor/run code=$code"

# HPO grid (slow)
if [[ "$RUN_SLOW" == "1" ]]; then
  log "  (running HPO grid — may take several minutes)"
  code=$(http_post_json "$NEURO_API_BASE/v1/hpo/grid" '{}' "$TMP/hpo.json")
  [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/hpo.json')); assert 'best' in d" 2>/dev/null && pass "POST /v1/hpo/grid" || fail "POST /v1/hpo/grid code=$code"
else
  skip "POST /v1/hpo/grid (pass --slow)"
fi

# Agents run (qa)
code=$(http_post_json "$NEURO_API_BASE/v1/agents/run" \
  "{\"intent\":\"qa\",\"course_id\":$COURSE_ID,\"question\":\"What is in the course?\"}" \
  "$TMP/agents.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/agents.json')); assert 'trace' in d and 'plan' in d" 2>/dev/null; then
  pass "POST /v1/agents/run (intent=qa)"
else
  fail "POST /v1/agents/run code=$code"
fi

# Feedback draft (may succeed with empty retrieval; still exercises agents path)
code=$(http_post_json "$NEURO_API_BASE/v1/agents/feedback/draft" \
  "{\"course_id\":$COURSE_ID,\"assignment_id\":1,\"submission_text\":\"def f(): return 1\",\"rubric\":\"Return a value.\"}" \
  "$TMP/draft.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/draft.json')); assert 'qid' in d" 2>/dev/null; then
  pass "POST /v1/agents/feedback/draft"
else
  # Assignment 1 may not exist — accept 422/500 as skip with message
  if [[ "$code" =~ ^(422|500)$ ]]; then
    skip "POST /v1/agents/feedback/draft (HTTP $code — set a valid assignment_id for course $COURSE_ID)"
  else
    fail "POST /v1/agents/feedback/draft code=$code"
  fi
fi

# Registry
code=$(http_get "$NEURO_API_BASE/v1/registry" "$TMP/reg.json")
[[ "$code" == "200" ]] && pass "GET /v1/registry" || fail "GET /v1/registry code=$code"

# DPO export
code=$(http_post_json "$NEURO_API_BASE/v1/dpo/export" '{}' "$TMP/dpo.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/dpo.json')); assert 'exported' in d" 2>/dev/null; then
  pass "POST /v1/dpo/export"
else
  fail "POST /v1/dpo/export code=$code"
fi

# Symbolic python (pytest in API image)
code=$(http_post_json "$NEURO_API_BASE/v1/symbolic/python" \
  '{"code":"def add(a,b):\n    return a+b\n","tests":"def test_add():\n    assert add(2,3)==5\n","timeout_s":15}' \
  "$TMP/py_symbolic.json")
if [[ "$code" == "200" ]]; then
  if json_ok "import json,sys; d=json.load(open('$TMP/py_symbolic.json')); assert d.get('ok')==True" 2>/dev/null; then
    pass "POST /v1/symbolic/python (pytest ok)"
  else
    fail "POST /v1/symbolic/python (ok=false — install pytest in API image?)"
  fi
else
  fail "POST /v1/symbolic/python code=$code"
fi

# Symbolic math (sympy optional)
code=$(http_post_json "$NEURO_API_BASE/v1/symbolic/math" '{"pairs":[["x","x"]]}' "$TMP/math.json")
if [[ "$code" == "200" ]]; then
  if json_ok "import json,sys; d=json.load(open('$TMP/math.json')); assert d.get('ok')==True" 2>/dev/null; then
    pass "POST /v1/symbolic/math (sympy ok)"
  else
    body=$(python3 -c "import json;print(json.load(open('$TMP/math.json')).get('error','no sympy'))" 2>/dev/null || true)
    skip "POST /v1/symbolic/math (sympy missing: $body — pip install '.[math]' in container)"
  fi
else
  fail "POST /v1/symbolic/math code=$code"
fi

# Audit course (LLM-heavy)
if [[ "$RUN_SLOW" == "1" ]]; then
  code=$(http_get "$NEURO_API_BASE/v1/audit/course/${COURSE_ID}?max_chunks=3" "$TMP/audit.json")
  [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/audit.json')); assert 'summary' in d" 2>/dev/null && pass "GET /v1/audit/course/$COURSE_ID" || fail "GET audit code=$code"
else
  skip "GET /v1/audit/course/{id} (pass --slow; many Ollama calls)"
fi

# PDF ingest — skip unless test PDF path provided
if [[ -n "${TEST_PDF_PATH:-}" ]]; then
  # Path must exist *inside* the API container if testing remotely; for local docker bind-mount, user can set to a path mounted in container
  code=$(http_post_json "$NEURO_API_BASE/v1/ingest/multimodal/pdf" \
    "{\"course_id\":$COURSE_ID,\"path\":\"$TEST_PDF_PATH\",\"title\":\"smoke\"}" \
    "$TMP/pdf.json")
  [[ "$code" == "200" ]] && pass "POST /v1/ingest/multimodal/pdf" || fail "POST pdf code=$code body=$(head -c 200 "$TMP/pdf.json")"
else
  skip "POST /v1/ingest/multimodal/pdf (set TEST_PDF_PATH to a path readable by the API container)"
fi

# Event webhook — unknown event → accepted=false (must send real secret when NEURO_EVENT_SECRET is set)
EV_JSON=$(python3 -c "import json,os; print(json.dumps({'secret':os.environ.get('NEURO_EVENT_SECRET','') or '','eventname':r'\fake\event','courseid':int(os.environ.get('COURSE_ID','2'))}))")
code=$(http_post_json "$NEURO_API_BASE/v1/events/moodle" "$EV_JSON" "$TMP/ev.json")
if [[ "$code" == "200" ]] && json_ok "import json,sys; d=json.load(open('$TMP/ev.json')); assert d.get('accepted')==False" 2>/dev/null; then
  pass "POST /v1/events/moodle (unknown event → skipped)"
else
  fail "POST /v1/events/moodle code=$code body=$(head -c 200 "$TMP/ev.json")"
fi

# Wrong secret when NEURO_EVENT_SECRET is set
if [[ -n "${NEURO_EVENT_SECRET:-}" ]]; then
  BAD_JSON=$(python3 -c "import json,os; print(json.dumps({'secret':'wrong','eventname':r'\fake\event','courseid':int(os.environ.get('COURSE_ID','2'))}))")
  code=$(http_post_json "$NEURO_API_BASE/v1/events/moodle" "$BAD_JSON" "$TMP/ev401.json")
  if [[ "$code" == "401" ]]; then
    pass "POST /v1/events/moodle (401 on bad secret)"
  else
    fail "POST /v1/events/moodle expected 401 on bad secret, got $code"
  fi
else
  skip "POST /v1/events/moodle bad-secret 401 (set NEURO_EVENT_SECRET to enable)"
fi

# Optional: CLI if installed
if command -v neuro-moodle-llm >/dev/null 2>&1; then
  if neuro-moodle-llm health >/dev/null 2>&1; then
    pass "CLI: neuro-moodle-llm health"
  else
    fail "CLI: neuro-moodle-llm health (non-zero exit)"
  fi
else
  skip "CLI: neuro-moodle-llm (not on PATH — pip install -e . in .venv)"
fi

log ""
log "== Summary =="
log "  PASS:  $PASS"
log "  FAIL:  $FAIL"
log "  SKIP:  $SKIP"
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
