"""SPEC-007 criterion 16, the Part B gate. Node N20-trace-embeddings.

    "Learned embeddings beat the interpretable feature baseline by >= 0.10
    adjusted Rand index against `GroundTruth` day-types, or the embedding path
    is not built."

`--bench` only: it needs `fastembed`, the cached ONNX weights, and about 90 s
of CPU to embed 216 ward-day traces. Same arrangement as N19b's
`tests/bench/test_retrieval_quality.py`, and for the same reason -- a benchmark
that runs on every commit either makes the suite too slow to run or gets its
budget cut until it stops measuring anything.

**The gate did not clear.** Measured 2026-08-21 over 216 ward-days, five
k-means seeds, three generator seeds: the interpretable feature baseline beat
the learned embedding every time, by 0.07 to 0.50 ARI. `test_embedding_gate`
below asserts the real threshold and is therefore deliberately red, exactly as
N19b left `test_hybrid_recall_gate` red rather than loosening it. The number a
green suite would have to show is written into the assertion, not into a
comment, so nobody can quietly move it.

Run it:

    pytest tests/bench/test_embedding_gate.py --bench -s
"""

from __future__ import annotations

import json
import statistics
import time
from random import Random

import pytest

from hwpm.domain import Event, Trajectory
from hwpm.ingest.synthetic import WardDayConfig, generate_ward_days
from hwpm.mining import (
    EpisodeParams,
    attach_clinician_specialties,
    attach_patients,
    derive_episodes,
    reconstruct_rounds,
)
from hwpm.mining.embed import (
    MIN_WARD_DAYS,
    adjusted_rand_index,
    attach_embeddings,
    cluster_ward_days,
    encode_ward_days,
    group_ward_days,
)

pytestmark = pytest.mark.bench

#: SPEC-007 criterion 16 / ADR-0007 ("Part B carries its own gate"). Absolute
#: ARI margin the learned embedding must clear.
GATE_MARGIN = 0.10

#: k-means seeds. The median over these is the reported figure: a single seed
#: would report the luck of one k-means++ draw as a property of a
#: representation.
CLUSTER_SEEDS = (11, 23, 37, 53, 71)

#: Generator seeds. One synthetic corpus is one draw of a data-generating
#: process, and a gate decided on one draw is a gate decided on noise.
GENERATOR_SEEDS = (7, 101, 202)

N_DAYS = 24


def _pipeline(seed: int):
    events, truth = generate_ward_days(WardDayConfig(n_days=N_DAYS), Random(seed))
    by_subject: dict[object, list[Event]] = {}
    for event in events:
        by_subject.setdefault(event.subject, []).append(event)
    episodes = []
    for subject, subject_events in by_subject.items():
        traj = Trajectory(
            subject=subject,
            events=tuple(sorted(subject_events, key=lambda e: e.timestamp)),
        )
        episodes.extend(derive_episodes(traj, EpisodeParams()))
    episodes = attach_clinician_specialties(
        attach_patients(episodes, truth.occupancy), truth.clinician_specialties
    )
    days = sorted({e.start.date() for e in episodes})
    rounds = []
    for day in days:
        rounds.extend(reconstruct_rounds(episodes, day))
    vectors = encode_ward_days(episodes, rounds, required=truth.required_specialties)
    return episodes, rounds, vectors, truth


def _measure(seed: int) -> dict[str, object]:
    episodes, rounds, vectors, truth = _pipeline(seed)
    assert len(vectors) >= MIN_WARD_DAYS
    ordered = sorted(vectors, key=lambda v: v.key)
    labels = [truth.day_types[v.key].value for v in ordered]
    k = len(set(labels))

    feature_ari = [
        adjusted_rand_index(labels, cluster_ward_days(vectors, k, Random(s)).labels)
        for s in CLUSTER_SEEDS
    ]

    started = time.monotonic()
    embedded = attach_embeddings(vectors, group_ward_days(episodes), rounds)
    build_seconds = time.monotonic() - started

    embedding_ari = [
        adjusted_rand_index(
            labels,
            cluster_ward_days(embedded, k, Random(s), use_embedding=True).labels,
        )
        for s in CLUSTER_SEEDS
    ]

    feature_median = statistics.median(feature_ari)
    embedding_median = statistics.median(embedding_ari)
    return {
        "generator_seed": seed,
        "n_ward_days": len(vectors),
        "k": k,
        "feature_ari": [round(a, 6) for a in feature_ari],
        "embedding_ari": [round(a, 6) for a in embedding_ari],
        "feature_ari_median": round(feature_median, 6),
        "embedding_ari_median": round(embedding_median, 6),
        "delta": round(embedding_median - feature_median, 6),
        "embed_build_seconds": round(build_seconds, 2),
    }


def test_embedding_gate() -> None:
    """SPEC-007 criterion 16. **Deliberately red** -- see the module docstring.

    Not skipped, not marked xfail, and the threshold is not softened. A gate
    that reports itself as passing because it was allowed to is worse than no
    gate: the whole point of ADR-0007's measured-gate discipline is that the
    measurement decides, and this one decided against the embedding.
    """
    results = [_measure(seed) for seed in GENERATOR_SEEDS]
    print(json.dumps(results, indent=2))

    deltas = [float(r["delta"]) for r in results]
    worst = min(deltas)
    best = max(deltas)
    print(
        f"\nSPEC-007 criterion 16: learned embedding minus interpretable "
        f"baseline, median ARI over {len(CLUSTER_SEEDS)} k-means seeds, across "
        f"{len(GENERATOR_SEEDS)} generator seeds: worst {worst:+.4f}, best "
        f"{best:+.4f}. Gate needs >= {GATE_MARGIN:+.2f} on every draw."
    )
    assert worst >= GATE_MARGIN, (
        f"the learned embedding does not beat the interpretable feature "
        f"baseline by {GATE_MARGIN} ARI (worst {worst:+.4f}, best {best:+.4f}). "
        f"Per SPEC-007 criterion 16 the embedding path is therefore not the "
        f"recommended representation, and `WardDayVector.features` is. This "
        f"assertion is left failing on purpose so the gate cannot be mistaken "
        f"for cleared; see docs/AUDIT-LOG.md, node N20-trace-embeddings."
    )


def test_learned_path_makes_no_network_call(monkeypatch) -> None:
    """SPEC-007 criterion 20 for the learned half.

    Runs after the model cache is populated -- which is the one moment a
    download can occur (`hwpm.retrieve.vector.model_sha256`'s docstring says
    so) -- and asserts that embedding a ward-day trace touches no socket.
    ADR-0007 decision 2 admits no exceptions clause for patient-derived data.
    """
    import socket

    from hwpm.retrieve.vector import embed_texts

    embed_texts(["warm the model cache"])

    def refuse(*_args, **_kwargs):  # pragma: no cover - the point is it never runs
        raise AssertionError("network access attempted while embedding a ward-day")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    _episodes, rounds, vectors, _truth = _pipeline(7)
    subset = vectors[:8]
    cells = group_ward_days(_episodes)
    embedded = attach_embeddings(subset, cells, rounds)
    assert all(v.embedding is not None for v in embedded)
    assert all(len(v.embedding or ()) == 384 for v in embedded)
