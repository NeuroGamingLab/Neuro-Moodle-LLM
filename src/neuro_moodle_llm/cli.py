"""`neuro-moodle-llm` console entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from . import dpo as dpo_mod
from . import eval as eval_mod
from . import hpo as hpo_mod
from . import monitoring as mon_mod
from . import quiz_eval as quiz_eval_mod
from . import registry as reg_mod
from . import synthetic as syn_mod
from . import synthetic_course as synth_course_mod
from .config import get_settings
from .feedback import draft_feedback
from .health import health_all_ok, run_health_report
from .ingest import ingest_course
from .moodle import MoodleClient
from .moodle_authoring import delete_synth_course
from .multimodal import extract_pdf, ingest_multimodal
from .ollama import OllamaClient
from .rag import ask
from .vectorstore import VectorStore


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_health(args: argparse.Namespace) -> int:
    settings = get_settings()
    report = run_health_report(settings)
    _print_json(report)
    return 0 if health_all_ok(report) else 1


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    with MoodleClient(settings) as moodle, OllamaClient(settings) as ollama:
        store = VectorStore(settings)
        result = ingest_course(
            course_id=args.course_id,
            moodle=moodle,
            ollama=ollama,
            store=store,
            replace=not args.no_replace,
        )
    _print_json(result)
    return 0


def cmd_ingest_pdf(args: argparse.Namespace) -> int:
    settings = get_settings()
    docs = extract_pdf(Path(args.path), course_id=args.course_id, title=args.title)
    with OllamaClient(settings) as ollama:
        store = VectorStore(settings)
        _print_json(ingest_multimodal(docs, ollama=ollama, store=store))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    settings = get_settings()
    with OllamaClient(settings) as ollama:
        store = VectorStore(settings)
        result = ask(
            args.question,
            course_id=args.course_id,
            ollama=ollama,
            store=store,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            use_hybrid=not args.no_hybrid,
            use_rerank=not args.no_rerank,
            use_qa_cache=not args.no_cache,
            settings=settings,
            learner_id=args.learner_id,
        )
    _print_json({"answer": result.answer, "sources": result.sources, "cache": result.cache, "confidence": result.confidence, "components": result.components})
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    gpath = Path(args.golden) if getattr(args, "golden", None) else None
    run = eval_mod.evaluate(
        label=args.label,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        golden_path=gpath,
    )
    _print_json({"summary": run["summary"], "delta_vs_champion": run.get("delta_vs_champion"), "n": run["n"], "run_id": run.get("run_id")})
    if args.promote:
        eval_mod.promote_champion(run)
    return 0


def cmd_hpo(args: argparse.Namespace) -> int:
    _print_json(hpo_mod.grid_search())
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    _print_json(mon_mod.run_monitoring(new_run_id=args.run_id))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    _print_json(syn_mod.audit_course(args.course_id, max_chunks=args.max_chunks)["summary"])
    return 0


def cmd_synth_course(args: argparse.Namespace) -> int:
    _print_json(
        synth_course_mod.generate_and_ingest(
            course_id=args.course_id,
            topic=args.topic,
            weeks=args.weeks,
            modules_per_week=args.modules_per_week,
            questions_per_module=args.questions_per_module,
            seed=args.seed,
            replace=not args.no_replace,
            write_golden=not args.no_golden,
            append_golden_to_primary=args.append_golden,
            allow_low_course_id=args.allow_low_course_id,
            publish_to_moodle=args.publish_to_moodle,
            publish_quizzes=not args.no_quizzes,
        )
    )
    return 0


def cmd_purge_synth(args: argparse.Namespace) -> int:
    settings = get_settings()
    out: dict[str, Any] = {}
    with MoodleClient(settings) as moodle:
        out["moodle"] = delete_synth_course(moodle=moodle, course_id=args.course_id)
    if not args.keep_qdrant:
        from .vectorstore import VectorStore

        store = VectorStore(settings)
        if store.collection_exists():
            store.delete_course(int(args.course_id))
            out["qdrant"] = {"deleted": True, "course_id": int(args.course_id)}
        else:
            out["qdrant"] = {"deleted": False, "reason": "collection-missing"}
    _print_json(out)
    return 0


def cmd_eval_quiz(args: argparse.Namespace) -> int:
    _print_json(
        quiz_eval_mod.evaluate_quiz_attempt(
            attempt_id=args.attempt_id,
            course_id=args.course_id,
            top_k=args.top_k,
        )
    )
    return 0


def cmd_registry(args: argparse.Namespace) -> int:
    _print_json(reg_mod.list_all())
    return 0


def cmd_dpo_export(args: argparse.Namespace) -> int:
    _print_json(dpo_mod.export_preferences())
    return 0


def cmd_feedback_draft(args: argparse.Namespace) -> int:
    submission = Path(args.submission_file).read_text() if args.submission_file else (args.submission or "")
    rubric = Path(args.rubric_file).read_text() if args.rubric_file else (args.rubric or "")
    _print_json(draft_feedback(course_id=args.course_id, assignment_id=args.assignment_id, submission_text=submission, rubric=rubric))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="neuro-moodle-llm")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("health").set_defaults(func=cmd_health)

    pi = sub.add_parser("ingest-course")
    pi.add_argument("--course-id", type=int, required=True)
    pi.add_argument("--no-replace", action="store_true")
    pi.set_defaults(func=cmd_ingest)

    pp = sub.add_parser("ingest-pdf")
    pp.add_argument("--course-id", type=int, required=True)
    pp.add_argument("--path", required=True)
    pp.add_argument("--title", default=None)
    pp.set_defaults(func=cmd_ingest_pdf)

    pa = sub.add_parser("ask")
    pa.add_argument("--course-id", type=int, default=None)
    pa.add_argument("--question", required=True)
    pa.add_argument("--top-k", type=int, default=5)
    pa.add_argument("--candidate-k", type=int, default=20)
    pa.add_argument("--no-hybrid", action="store_true")
    pa.add_argument("--no-rerank", action="store_true")
    pa.add_argument("--no-cache", action="store_true")
    pa.add_argument("--learner-id", default=None)
    pa.set_defaults(func=cmd_ask)

    pe = sub.add_parser("eval")
    pe.add_argument("--label", default="cli")
    pe.add_argument("--top-k", type=int, default=5)
    pe.add_argument("--candidate-k", type=int, default=20)
    pe.add_argument("--golden", default=None, help="Path to golden.jsonl (default: data/eval/golden.jsonl)")
    pe.add_argument("--promote", action="store_true", help="Write champion.json with this run's summary")
    pe.set_defaults(func=cmd_eval)

    sub.add_parser("hpo").set_defaults(func=cmd_hpo)

    pm = sub.add_parser("monitor")
    pm.add_argument("--run-id", default=None)
    pm.set_defaults(func=cmd_monitor)

    pad = sub.add_parser("audit")
    pad.add_argument("--course-id", type=int, required=True)
    pad.add_argument("--max-chunks", type=int, default=30)
    pad.set_defaults(func=cmd_audit)

    ps = sub.add_parser("synth-course")
    ps.add_argument("--course-id", type=int, required=True, help="Use >=90000 for synthetic-only courses")
    ps.add_argument("--topic", required=True)
    ps.add_argument("--weeks", type=int, default=2)
    ps.add_argument("--modules-per-week", type=int, default=2)
    ps.add_argument("--questions-per-module", type=int, default=2)
    ps.add_argument("--seed", type=int, default=42)
    ps.add_argument("--no-replace", action="store_true")
    ps.add_argument("--no-golden", action="store_true", help="Skip writing golden JSONL")
    ps.add_argument("--append-golden", action="store_true", help="Append rows to data/eval/golden.jsonl")
    ps.add_argument(
        "--allow-low-course-id",
        action="store_true",
        help="Allow course_id < 90000 (risks mixing with real Moodle course ids)",
    )
    ps.add_argument(
        "--publish-to-moodle",
        action="store_true",
        help="Also create the course in Moodle (real LMS course id, page resources, optional quiz). Requires local_neurollm.",
    )
    ps.add_argument(
        "--no-quizzes",
        action="store_true",
        help="When publishing to Moodle, skip quiz creation (pages only).",
    )
    ps.set_defaults(func=cmd_synth_course)

    pp = sub.add_parser("purge-synth", help="Delete a synthetic course from Moodle and Qdrant")
    pp.add_argument("--course-id", type=int, required=True, help="Real Moodle course id (returned by synth-course --publish-to-moodle).")
    pp.add_argument("--keep-qdrant", action="store_true", help="Skip deleting the matching Qdrant vectors.")
    pp.set_defaults(func=cmd_purge_synth)

    peq = sub.add_parser("eval-quiz", help="Closed-loop eval of one Moodle quiz attempt against synthetic ground truth")
    peq.add_argument("--attempt-id", type=int, required=True)
    peq.add_argument("--course-id", type=int, default=None)
    peq.add_argument("--top-k", type=int, default=5)
    peq.set_defaults(func=cmd_eval_quiz)

    sub.add_parser("registry").set_defaults(func=cmd_registry)
    sub.add_parser("dpo-export").set_defaults(func=cmd_dpo_export)

    pf = sub.add_parser("feedback-draft")
    pf.add_argument("--course-id", type=int, required=True)
    pf.add_argument("--assignment-id", type=int, required=True)
    pf.add_argument("--submission", default=None)
    pf.add_argument("--submission-file", default=None)
    pf.add_argument("--rubric", default=None)
    pf.add_argument("--rubric-file", default=None)
    pf.set_defaults(func=cmd_feedback_draft)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
