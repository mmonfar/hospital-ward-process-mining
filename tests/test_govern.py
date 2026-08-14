"""Tests for the governance layer.

This code decides what agents may do and reports what they spent, so it is held
to the same standard as the analysis code. A budget monitor that silently
mis-reports is worse than none.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hwpm.govern import audit, graph as graph_mod, ledger

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = REPO_ROOT / "orchestration" / "graph.yaml"


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_graph() -> graph_mod.Graph:
    return graph_mod.load(GRAPH_PATH)


def test_real_graph_is_valid(real_graph: graph_mod.Graph) -> None:
    """The shipped graph must always be structurally sound.

    This runs against the real file rather than a fixture on purpose -- it is a
    guard on the repository, not on the loader.
    """
    assert real_graph.validate() == []


def test_every_node_resolves_to_a_model(real_graph: graph_mod.Graph) -> None:
    for node in real_graph.nodes.values():
        role = real_graph.model_for(node)
        assert role.get("model"), f"{node.id} resolves to a role with no model"
        assert role.get("effort") in {"low", "medium", "high"}


def test_hard_stop_nodes_are_never_runnable(real_graph: graph_mod.Graph) -> None:
    """ADR-0005 / CLAUDE.md rule 4.

    Enforced structurally in `runnable()` rather than by a check at each call
    site, so that omitting the check is not possible.
    """
    stops = {n.id for n in real_graph.nodes.values() if n.gate == graph_mod.GATE_HARD_STOP}
    assert stops, "expected at least one hard-stop node (N17-real-data)"
    assert not stops & {n.id for n in real_graph.runnable()}


def test_real_data_node_is_a_hard_stop(real_graph: graph_mod.Graph) -> None:
    assert real_graph.nodes["N17-real-data"].gate == graph_mod.GATE_HARD_STOP


def test_exact_baseline_precedes_the_metaheuristic(real_graph: graph_mod.Graph) -> None:
    """Rule 0 of SELECTION-GUIDE.md, encoded as a dependency edge.

    If this ordering is ever reversed, the project would build NSGA-II before
    discovering whether CP-SAT already solves the problem.
    """
    nsga = real_graph.nodes["N10-nsga2"]
    assert "N09-baseline-gate" in nsga.depends_on
    assert "N08-exact-baseline" in real_graph.nodes["N09-baseline-gate"].depends_on


def test_cycle_detection(tmp_path: Path) -> None:
    bad = tmp_path / "cyclic.yaml"
    bad.write_text(
        """
version: 1
models: {builder: {model: m, effort: medium}}
gate_semantics: {autonomous: go}
nodes:
  - {id: A, agent: builder, gate: autonomous, depends_on: [B]}
  - {id: B, agent: builder, gate: autonomous, depends_on: [A]}
""",
        encoding="utf-8",
    )
    problems = graph_mod.load(bad).validate()
    assert any("cycle" in p for p in problems)


def test_missing_dependency_is_reported(tmp_path: Path) -> None:
    bad = tmp_path / "missing.yaml"
    bad.write_text(
        """
version: 1
models: {builder: {model: m, effort: medium}}
gate_semantics: {autonomous: go}
nodes:
  - {id: A, agent: builder, gate: autonomous, depends_on: [NOPE]}
""",
        encoding="utf-8",
    )
    assert any("NOPE" in p for p in graph_mod.load(bad).validate())


def test_mermaid_covers_every_node_and_edge(real_graph: graph_mod.Graph) -> None:
    rendered = graph_mod.to_mermaid(real_graph)
    for node in real_graph.nodes.values():
        assert node.id in rendered
        for dep in node.depends_on:
            assert f"{dep} --> {node.id}" in rendered


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


def _transcript(tmp_path: Path, *usages: dict, model: str = "claude-opus-5") -> Path:
    path = tmp_path / "abc123.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for usage in usages:
            handle.write(
                json.dumps({"type": "assistant", "message": {"model": model, "usage": usage}})
                + "\n"
            )
    return path


def test_cache_reads_are_discounted(tmp_path: Path) -> None:
    """The reason the ledger weights rather than sums.

    Every turn re-reads the whole conversation from cache, so an unweighted
    total counts the same context repeatedly and would trip any budget within a
    few turns.
    """
    path = _transcript(tmp_path, {"cache_read_input_tokens": 1_000_000})
    session = ledger.read_session(path)
    assert session.billable == pytest.approx(100_000)
    assert next(iter(session.by_model.values())).raw_total == 1_000_000


def test_output_is_weighted_most_heavily(tmp_path: Path) -> None:
    out = ledger.read_session(_transcript(tmp_path, {"output_tokens": 1000})).billable
    inp = ledger.read_session(_transcript(tmp_path, {"input_tokens": 1000})).billable
    assert out > inp


def test_usage_accumulates_per_model(tmp_path: Path) -> None:
    path = _transcript(
        tmp_path, {"input_tokens": 10, "output_tokens": 5}, {"input_tokens": 7}
    )
    session = ledger.read_session(path)
    usage = session.by_model["claude-opus-5"]
    assert (usage.turns, usage.fresh_input, usage.output) == (2, 17, 5)


def test_torn_and_irrelevant_lines_are_skipped(tmp_path: Path) -> None:
    """A live transcript can have a half-written final line.

    A ledger that crashes on the session it is reporting on is useless.
    """
    path = tmp_path / "torn.jsonl"
    path.write_text(
        '{"type":"user","content":"hi"}\n'
        '{"type":"assistant","message":{"model":"m","usage":{"output_tokens":3}}}\n'
        '{"type":"assistant","messa\n',
        encoding="utf-8",
    )
    session = ledger.read_session(path)
    assert session.turns == 1
    assert session.by_model["m"].output == 3


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [(0.1, "ok"), (0.65, "warn"), (0.85, "consolidate"), (0.99, "halt")],
)
def test_budget_thresholds(fraction: float, expected: str) -> None:
    budget = graph_mod.load(GRAPH_PATH).budget
    hard = budget["session_hard_limit_tokens"]
    assert ledger.assess(hard * fraction, budget).action == expected


def test_budget_is_calibrated_above_a_known_good_session() -> None:
    """Guards the calibration lesson recorded in 04-AGENT-ORCHESTRATION.md.

    The original guessed limits scored a healthy session at 184% of the hard
    limit. A budget a normal session exceeds is one everyone learns to ignore,
    so the floor is set above an observed-good session (~1.28M weighted).
    """
    budget = graph_mod.load(GRAPH_PATH).budget
    assert budget["session_hard_limit_tokens"] > 1_300_000
    assert budget["session_soft_limit_tokens"] < budget["session_hard_limit_tokens"]


def test_transcript_dir_slugifies_paths_with_spaces() -> None:
    resolved = ledger.default_transcript_dir(Path("C:/Users/x/My Proj"))
    assert " " not in resolved.name
    assert "My-Proj" in resolved.name


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------


def test_audit_appends_and_never_rewrites(tmp_path: Path) -> None:
    log = tmp_path / "AUDIT-LOG.md"
    for i in range(3):
        audit.append(
            log,
            audit.AuditEntry(action=f"act {i}", why="w", authority="SPEC-001"),
            repo=tmp_path,
        )
    text = log.read_text(encoding="utf-8")
    assert text.count("## ") == 3
    assert all(f"act {i}" in text for i in range(3))
    assert text.startswith("# Audit log")


def test_audit_records_the_required_fields(tmp_path: Path) -> None:
    log = tmp_path / "AUDIT-LOG.md"
    audit.append(
        log,
        audit.AuditEntry(
            action="Implemented ingestion",
            why="SPEC-001 acceptance criteria 1-4",
            authority="SPEC-001",
            node="N03-ingestion",
            model="claude-sonnet-5",
            artefacts=("src/hwpm/ingest/csv.py",),
            evidence="12 passed",
        ),
        repo=tmp_path,
    )
    text = log.read_text(encoding="utf-8")
    for expected in (
        "Implemented ingestion",
        "**Why:**",
        "**Authority:** SPEC-001",
        "N03-ingestion",
        "claude-sonnet-5",
        "src/hwpm/ingest/csv.py",
        "12 passed",
    ):
        assert expected in text
