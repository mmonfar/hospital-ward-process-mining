"""N19b / ADR-0007 G2-reopened, G3 measurement: lexical vs vector vs hybrid
recall@5 and MRR over the full corpus and the 36-query labelled set.

`bench`-marked (opt-in, `pytest --bench`), not part of the default `pytest -q`
gate, for a measured reason rather than convention alone: building the vector
index over the full corpus (`refs/metaheuristics/essentials-of-metaheuristics.md`
alone chunks to ~1000+ passages) took on the order of minutes on this
reference machine's CPU when actually measured for this node -- see
`docs/AUDIT-LOG.md` node N19b for the exact figure. That is fine for a
periodic quality gate (`hwpm context eval`, or `pytest --bench`) and would be
a bad tax on every routine `pytest -q` run, exactly the reasoning
`docs/06-QA-AND-DEADCODE.md` already gives for `test_retrieval_latency.py`.
`tests/test_retrieve.py::test_lexical_recall_gate` stays in the default gate
and covers the `--lexical-only` path, which needs no embedding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastembed")
pytest.importorskip("sqlite_vec")

from hwpm.analytics.coverage import wilson_interval
from hwpm.retrieve.corpus import build_chunks
from hwpm.retrieve.eval import evaluate, load_queries

REPO_ROOT = Path(__file__).resolve().parents[2]
QUERIES_PATH = REPO_ROOT / "tests" / "fixtures" / "retrieval_queries.yaml"


@pytest.fixture(scope="module")
def full_scores() -> dict:
    chunks = build_chunks(REPO_ROOT)
    return evaluate(QUERIES_PATH, REPO_ROOT, chunks=chunks)


@pytest.mark.bench
def test_three_way_recall_reported_honestly(full_scores: dict) -> None:
    """The measurement this node was asked for: lexical, vector-only and
    hybrid recall@5/MRR on the same 36-query set, all three present and all
    three printed -- not just whichever clears a threshold."""
    n = len(load_queries(QUERIES_PATH))
    assert set(full_scores) == {"lexical", "vector", "hybrid"}
    print(f"\nN19b three-way retrieval measurement (n={n} queries):")
    for name in ("lexical", "vector", "hybrid"):
        s = full_scores[name]
        successes = round(s.recall_at_5 * s.n_queries)
        lo, hi = wilson_interval(successes, s.n_queries)
        print(
            f"  {name:<8} recall@5={s.recall_at_5:.4f}  mrr={s.mrr:.4f}  "
            f"n={s.n_queries}  95% Wilson CI [{lo:.3f}, {hi:.3f}]"
        )
        assert s.n_queries == n


@pytest.mark.bench
def test_hybrid_recall_gate(full_scores: dict) -> None:
    """ADR-0007 G3, asserted at its real threshold: hybrid recall@5 must be
    >= lexical + 0.15 absolute (and clear 0.70) for the vector half to be
    worth shipping as the *default* retrieval path.

    Measured 2026-08-18 (docs/AUDIT-LOG.md node N19b): lexical 0.750,
    vector-only 0.611, hybrid 0.639 (n=36). **G3 does not clear -- hybrid
    scores below lexical-only, not above it**, and vector-only scores lower
    still. This is asserted at the real G3 threshold rather than loosened to
    match what was measured, exactly per this node's own instruction: build
    the vector half and RRF hybrid, then report honestly, even if the
    honest answer is that it does not help on this corpus. A red gate here
    is the correct, intended signal -- it is why `hwpm context search`
    keeps `--lexical-only` as a first-class mode rather than deprecating it
    now that a vector index exists (SPEC-007 'Where grep remains strictly
    better'; ADR-0007 decision 4). See docs/AUDIT-LOG.md for the finding and
    the plausible causes recorded there (heading_path+text embedding
    diluting short technical queries; RRF depth/constant not tuned for this
    corpus; long outlier chunks near the 512-token window). Investigating
    and fixing this is future work, not something to paper over by editing
    this assertion."""
    lexical = full_scores["lexical"]
    hybrid = full_scores["hybrid"]
    assert hybrid.recall_at_5 >= lexical.recall_at_5 + 0.15, (
        f"ADR-0007 G3 not met: hybrid recall@5 {hybrid.recall_at_5:.3f} does not "
        f"reach lexical {lexical.recall_at_5:.3f} + 0.15 -- see docs/AUDIT-LOG.md "
        f"node N19b for the full measurement and honest discussion"
    )
    assert hybrid.recall_at_5 > 0.70
