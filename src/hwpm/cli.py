"""hwpm — command line entry point.

Only the governance commands exist so far; analysis commands arrive with their
specs. Deliberately argparse rather than a CLI framework: one fewer dependency
for a tool whose whole job is to be trustworthy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hwpm.design import colour, emit, tokens
from hwpm.govern import audit, auditor, ledger
from hwpm.govern import graph as graph_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = REPO_ROOT / "orchestration" / "graph.yaml"
MERMAID_PATH = REPO_ROOT / "orchestration" / "graph.mmd"
AUDIT_PATH = REPO_ROOT / "docs" / "AUDIT-LOG.md"
DESIGN_DIR = REPO_ROOT / "web" / "design"


# Node-id column width. Kept ASCII-only throughout: this runs in a Windows
# console under cp1252, where box-drawing and middot characters render as `?`.
_W = 24


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def cmd_budget(args: argparse.Namespace) -> int:
    g = graph_mod.load(GRAPH_PATH)
    transcript_dir = (
        Path(args.transcripts)
        if args.transcripts
        else ledger.default_transcript_dir(REPO_ROOT)
    )
    if not transcript_dir.is_dir():
        print(f"No transcript directory at {transcript_dir}", file=sys.stderr)
        print("Pass --transcripts to point at it explicitly.", file=sys.stderr)
        return 1

    sessions = ledger.read_project(transcript_dir)
    if not sessions:
        print(f"No session transcripts found in {transcript_dir}")
        return 0

    print(f"Transcripts: {transcript_dir}\n")
    header = f"{'session':<14}{'turns':>7}{'fresh in':>12}{'output':>10}{'billable':>13}"
    print(header)
    print("-" * len(header))
    for session in sessions:
        fresh = sum(m.fresh_input + m.cache_write for m in session.by_model.values())
        print(
            f"{session.session_id[:12]:<14}{session.turns:>7}"
            f"{_fmt(fresh):>12}{_fmt(session.output):>10}{_fmt(session.billable):>13}"
        )

    latest = sessions[-1]
    status = ledger.assess(latest.billable, g.budget)
    print(f"\nCurrent session vs budget ({_fmt(status.hard_limit)} hard limit):")
    print(f"  billable   {_fmt(status.billable)}  ({status.fraction:.0%})")
    print(f"  remaining  {_fmt(status.remaining)}")
    print(f"  action     {status.action.upper()}")
    if status.note:
        print(f"  note       {status.note}")

    if args.by_model:
        print("\nBy model, current session:")
        for usage in sorted(
            latest.by_model.values(), key=lambda m: m.billable, reverse=True
        ):
            print(
                f"  {usage.model:<32}{usage.turns:>5} turns"
                f"{_fmt(usage.billable):>14} billable"
            )
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    g = graph_mod.load(GRAPH_PATH)

    problems = g.validate()
    if problems:
        print("Graph validation FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.mermaid:
        MERMAID_PATH.write_text(graph_mod.to_mermaid(g), encoding="utf-8")
        print(f"wrote {MERMAID_PATH.relative_to(REPO_ROOT)}")
        return 0

    print(f"{len(g.nodes)} nodes, graph valid.\n")
    print("Runnable now:")
    runnable = g.runnable()
    if not runnable:
        print("  (none — every unblocked node is complete or gated)")
    for node in runnable:
        role = g.model_for(node)
        gate = (
            "" if node.gate == graph_mod.GATE_AUTONOMOUS else f"  [{node.gate.upper()}]"
        )
        print(f"  {node.id:<{_W}}{node.title}")
        print(
            f"  {'':<{_W}}{node.agent} | {role.get('model')}"
            f" | effort={role.get('effort')}"
            f" | budget={_fmt(node.budget_tokens)}{gate}"
        )

    blocked = [n for n in g.nodes.values() if g.unmet_dependencies(n)]
    if blocked and args.verbose:
        print("\nBlocked:")
        for node in blocked:
            print(f"  {node.id:<{_W}}waiting on {', '.join(g.unmet_dependencies(node))}")

    stops = [n for n in g.nodes.values() if n.gate == graph_mod.GATE_HARD_STOP]
    if stops:
        print("\nHard stops (agents never proceed past these):")
        for node in stops:
            print(f"  {node.id:<{_W}}{node.title}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    audit.append(
        AUDIT_PATH,
        audit.AuditEntry(
            action=args.action,
            why=args.why,
            authority=args.authority,
            node=args.node,
            model=args.model,
            artefacts=tuple(args.artefact or ()),
            evidence=args.evidence,
        ),
        repo=REPO_ROOT,
    )
    print(f"appended to {AUDIT_PATH.relative_to(REPO_ROOT)}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """The SPEC-006 auditor (node N15). Distinct from `govern audit`, which
    appends one hand-written entry; this runs the five machine audits and
    appends their findings.

    Writing to the log is the default, not a flag: SPEC-006 criterion 6 makes
    recording the findings the thing that distinguishes an audit from a to-do
    list, so skipping it has to be the deliberate act (`--no-log`).
    """
    result = auditor.run(REPO_ROOT, only=args.only)
    print(auditor.render(result))
    if args.log:
        audit.append(
            AUDIT_PATH, auditor.to_entry(result, model=args.model), repo=REPO_ROOT
        )
        print(f"\nfindings appended to {AUDIT_PATH.relative_to(REPO_ROOT)}")
    blocking = result.blocking
    if args.strict and result.findings:
        return 1
    return 1 if blocking else 0


def cmd_design_build(_args: argparse.Namespace) -> int:
    for path in emit.write_all(DESIGN_DIR):
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


def cmd_design_check(_args: argparse.Namespace) -> int:
    """Accessibility and drift checks, printed rather than only asserted.

    The same checks run in `tests/test_design_tokens.py`. They are exposed here
    because a designer changing a colour wants the failing ratio, not a pytest
    traceback.
    """
    failures: list[str] = []

    for problem in emit.drift(DESIGN_DIR):
        failures.append(f"drift: {problem}")

    print("Contrast (WCAG 2.1):")
    for rule in tokens.CONTRAST_REQUIREMENTS:
        ratio = colour.contrast_ratio(tokens.resolve(rule.fg), tokens.resolve(rule.bg))
        ok = ratio >= rule.minimum
        mark = "ok  " if ok else "FAIL"
        print(
            f"  {mark} {ratio:5.2f} (>= {rule.minimum:.1f})  "
            f"{rule.fg} on {rule.bg} -- {rule.use}"
        )
        if not ok:
            failures.append(f"contrast: {rule.fg} on {rule.bg} is {ratio:.2f}")

    print("\nCategorical separation (CIE76, dichromat-simulated):")
    for kind in ("none", *colour.CVD_KINDS):
        shown = [
            c if kind == "none" else colour.simulate_cvd(c, kind)
            for c in tokens.series_colours()
        ]
        worst, pair = colour.min_separation(shown)
        ok = worst >= tokens.SERIES_MIN_DELTA_E
        print(
            f"  {'ok  ' if ok else 'FAIL'} {worst:5.1f} "
            f"(>= {tokens.SERIES_MIN_DELTA_E:.0f})  {kind:<13} {pair[0]} / {pair[1]}"
        )
        if not ok:
            failures.append(f"separation: {kind} worst {worst:.1f}")

    if failures:
        print(f"\n{len(failures)} failure(s):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nDesign system checks passed.")
    return 0


def cmd_context_index(_args: argparse.Namespace) -> int:
    """Build/refresh the lexical index, and the vector index if the
    `retrieve` extra is installed (SPEC-007 Part A, `hwpm context index`).

    N19b (ADR-0007 G2 reopened) added the vector half on top of the
    lexical-only build N19 shipped. `IndexStats.model` for the lexical build
    is still `lexical-bm25`; the vector build's stats separately report the
    embedding model and its `model_sha`. If `fastembed`/`sqlite-vec` are not
    installed, the vector build is skipped with an install hint and the
    lexical build still succeeds -- never a traceback (criteria 9-10).
    """
    from hwpm.retrieve.index import (
        DEFAULT_INDEX_PATH,
        MissingOptionalDependencyError,
        build_index,
    )

    out = REPO_ROOT / DEFAULT_INDEX_PATH
    stats = build_index([REPO_ROOT], out)
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    print(f"files={stats.n_files} chunks={stats.n_chunks} bytes={stats.bytes_indexed:,}")
    print(f"lexical model={stats.model} build_seconds={stats.build_seconds:.2f}")

    try:
        from hwpm.retrieve.corpus import build_chunks
        from hwpm.retrieve.vector import DEFAULT_VECTOR_DB_PATH, build_vector_index

        vector_out = REPO_ROOT / DEFAULT_VECTOR_DB_PATH
        chunks = build_chunks(REPO_ROOT)
        vstats = build_vector_index(chunks, vector_out)
        print(f"wrote {vector_out.relative_to(REPO_ROOT)}")
        print(
            f"vector model={vstats.model} model_sha={vstats.model_sha[:12]} "
            f"dim={vstats.dim} build_seconds={vstats.build_seconds:.2f}"
        )
    except MissingOptionalDependencyError as exc:
        print(f"vector index skipped: {exc}")
    return 0


def cmd_context_search(args: argparse.Namespace) -> int:
    """Ranked passages with `path:start-end` citations. Never a paraphrase:
    what is printed is the verbatim chunk text (SPEC-007 criterion 3)."""
    from hwpm.retrieve.index import DEFAULT_INDEX_PATH, search

    hits = search(
        args.query,
        k=args.k,
        lexical_only=args.lexical_only,
        index_path=REPO_ROOT / DEFAULT_INDEX_PATH,
        repo_root=REPO_ROOT,
    )
    if not hits:
        print("No results.")
        return 0
    for hit in hits:
        if hit.lexical_rank is not None and hit.vector_rank is not None:
            retriever = "hybrid"
        elif hit.vector_rank is not None:
            retriever = "vector"
        else:
            retriever = "lexical"
        print(f"\n{hit.citation()}  [{retriever}]  score={hit.score:.2f}")
        print(f"  {hit.chunk.heading_path}")
        for line in hit.chunk.text.splitlines():
            print(f"  | {line}")
    return 0


def cmd_context_eval(args: argparse.Namespace) -> int:
    """The G2/G3 measurement (ADR-0007), JSON out.

    Full (default) mode builds the vector index and reports lexical, vector
    and hybrid recall/MRR -- this embeds the whole corpus and takes minutes
    on CPU (measured for N19b, see docs/AUDIT-LOG.md). `--lexical-only` skips
    that and reports the cheap BM25-only baseline in under a second.
    """
    import json as _json

    from hwpm.retrieve.eval import evaluate

    queries_path = REPO_ROOT / "tests" / "fixtures" / "retrieval_queries.yaml"
    scores = evaluate(queries_path, REPO_ROOT, include_vector=not args.lexical_only)
    out = {
        name: {"recall_at_5": s.recall_at_5, "mrr": s.mrr, "n_queries": s.n_queries}
        for name, s in scores.items()
    }
    print(_json.dumps(out, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hwpm")
    sub = parser.add_subparsers(dest="group", required=True)

    govern = sub.add_parser("govern", help="orchestration, budget and audit")
    gsub = govern.add_subparsers(dest="command", required=True)

    budget = gsub.add_parser("budget", help="token spend against the declared budget")
    budget.add_argument("--transcripts", help="override transcript directory")
    budget.add_argument("--by-model", action="store_true")
    budget.set_defaults(func=cmd_budget)

    graph_cmd = gsub.add_parser("graph", help="validate and inspect the work graph")
    graph_cmd.add_argument("--mermaid", action="store_true", help="regenerate graph.mmd")
    graph_cmd.add_argument("-v", "--verbose", action="store_true")
    graph_cmd.set_defaults(func=cmd_graph)

    audit_cmd = gsub.add_parser("audit", help="append an audit-log entry")
    audit_cmd.add_argument("action")
    audit_cmd.add_argument("--why", required=True)
    audit_cmd.add_argument("--authority", required=True, help="SPEC / ADR / node id")
    audit_cmd.add_argument("--node")
    audit_cmd.add_argument("--model")
    audit_cmd.add_argument("--artefact", action="append")
    audit_cmd.add_argument("--evidence")
    audit_cmd.set_defaults(func=cmd_audit)

    review = gsub.add_parser(
        "review", help="run the SPEC-006 automated code and method auditor"
    )
    review.add_argument(
        "--only",
        action="append",
        choices=list(auditor.AUDIT_NAMES),
        help="run only the named audit (repeatable)",
    )
    review.add_argument(
        "--no-log",
        dest="log",
        action="store_false",
        help="do not append findings to the audit log (SPEC-006 criterion 6 "
        "expects them to be appended; use only for a dry run)",
    )
    review.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on any finding, not only blocking ones",
    )
    review.add_argument("--model", help="model that ran the audit, for the log entry")
    review.set_defaults(func=cmd_review, log=True)

    design = sub.add_parser("design", help="design-system tokens and checks")
    dsub = design.add_subparsers(dest="command", required=True)

    build = dsub.add_parser("build", help="regenerate web/design/tokens.{css,js}")
    build.set_defaults(func=cmd_design_build)

    check = dsub.add_parser("check", help="contrast, colour-vision and drift checks")
    check.set_defaults(func=cmd_design_check)

    context = sub.add_parser("context", help="repo context retrieval (SPEC-007 Part A)")
    csub = context.add_subparsers(dest="command", required=True)

    ctx_index = csub.add_parser("index", help="build/refresh the lexical index")
    ctx_index.set_defaults(func=cmd_context_index)

    ctx_search = csub.add_parser(
        "search", help="ranked passages with file:line citations"
    )
    ctx_search.add_argument("query")
    ctx_search.add_argument("-k", type=int, default=8)
    ctx_search.add_argument(
        "--lexical-only",
        action="store_true",
        help=(
            "BM25 only, no embedding needed. Recommended: the N19b measurement "
            "(docs/AUDIT-LOG.md) found hybrid recall@5 (0.64) below lexical-only "
            "(0.75) on this corpus, so hybrid is not yet a clear improvement"
        ),
    )
    ctx_search.set_defaults(func=cmd_context_search)

    ctx_eval = csub.add_parser("eval", help="the G2/G3 retrieval-quality measurement")
    ctx_eval.add_argument(
        "--lexical-only",
        action="store_true",
        help="skip the vector/hybrid measurement (skips the full-corpus embed)",
    )
    ctx_eval.set_defaults(func=cmd_context_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
