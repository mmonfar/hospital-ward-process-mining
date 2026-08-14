"""hwpm — command line entry point.

Only the governance commands exist so far; analysis commands arrive with their
specs. Deliberately argparse rather than a CLI framework: one fewer dependency
for a tool whose whole job is to be trustworthy.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hwpm.govern import audit, graph as graph_mod, ledger

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH_PATH = REPO_ROOT / "orchestration" / "graph.yaml"
MERMAID_PATH = REPO_ROOT / "orchestration" / "graph.mmd"
AUDIT_PATH = REPO_ROOT / "docs" / "AUDIT-LOG.md"


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
        gate = "" if node.gate == graph_mod.GATE_AUTONOMOUS else f"  [{node.gate.upper()}]"
        print(f"  {node.id:<{_W}}{node.title}")
        print(
            f"  {'':<{_W}}{node.agent} | {role.get('model')} | effort={role.get('effort')}"
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
