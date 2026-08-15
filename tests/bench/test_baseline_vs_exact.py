"""N09 — the Rule 0 comparison. SPEC-004: "nothing fancier is reportable until
it beats these [Random Search / Hill-Climbing with Restarts]".

N08 already answered the *next* question up the chain -- whether CP-SAT
proves optimality at ward scale -- and the answer was yes
(`tests/bench/test_cpsat_scale.py`). What N09 adds is the baseline half of
that comparison: does CP-SAT's proven-optimal front actually beat what a
cheap, dependency-free search finds under the *same* `Budget`? SPEC-004's
`Budget` docstring is explicit about why this matters: "comparing algorithms
on unequal budgets is the commonest way to prove a favoured one wins". All
three schedulers here receive one `Budget(max_seconds=...)` and nothing else.

Run to see the numbers:

    ./.venv/Scripts/python.exe -m pytest tests/bench/test_baseline_vs_exact.py --bench -s
"""

from __future__ import annotations

import time
from random import Random

import pytest

from hwpm.optimize.baselines import HillClimbingScheduler, RandomSearchScheduler
from hwpm.optimize.cpsat import CpSatScheduler
from hwpm.optimize.evaluate import dominates, pareto_front
from hwpm.optimize.instances import realistic_single_ward
from hwpm.optimize.types import Budget, Objectives

INSTANCE_SEED = 20260814
SOLVER_SEED = 2026

#: A mid-size instance, not the full 30-bed ward: the baselines have no
#: proof-of-optimality budget escape hatch the way CP-SAT does, so a
#: comparison has to actually finish. 12 beds / 24 slots is the same instance
#: `test_cpsat_scale.test_realistic_instance_epsilon_sweep` uses.
N_BEDS = 12
N_SLOTS = 24

#: Equal for all three schedulers -- the entire point of `Budget`.
COMPARISON_SECONDS = 60.0


def _n_beating(
    front: list[tuple[object, Objectives]], other: list[tuple[object, Objectives]]
) -> int:
    """How many points of `other` are dominated by some point of `front`."""
    return sum(1 for _, o in other if any(dominates(f, o) for _, f in front))


@pytest.mark.bench
def test_cpsat_vs_baselines_equal_budget() -> None:
    inst = realistic_single_ward(Random(INSTANCE_SEED), n_beds=N_BEDS, n_slots=N_SLOTS)
    budget = Budget(max_seconds=COMPARISON_SECONDS)

    results: dict[str, tuple[list[tuple[object, Objectives]], float]] = {}
    schedulers = {
        "cp_sat": CpSatScheduler(grid=2, workers=1),
        "random_search": RandomSearchScheduler(),
        "hill_climbing": HillClimbingScheduler(),
    }
    for name, scheduler in schedulers.items():
        started = time.perf_counter()
        front = scheduler.solve(inst, budget, Random(SOLVER_SEED))
        elapsed = time.perf_counter() - started
        results[name] = (front, elapsed)

    print("\n--- N09 baseline gate: CP-SAT vs Random Search vs Hill-Climbing ---")
    print(f"instance: {N_BEDS} beds, {N_SLOTS} slots, budget {COMPARISON_SECONDS}s each")
    for name, (front, elapsed) in results.items():
        print(f"  {name:<14} front={len(front):3d}  wall={elapsed:6.2f}s")

    cpsat_front, _ = results["cp_sat"]
    combined = pareto_front([pair for front, _ in results.values() for pair in front])
    print(f"  combined non-dominated front (all three): {len(combined)}")
    cpsat_survivors = sum(1 for pair in cpsat_front if pair in combined)
    print(f"  of which CP-SAT contributes: {cpsat_survivors} / {len(cpsat_front)}")

    for name in ("random_search", "hill_climbing"):
        front, _ = results[name]
        beaten = _n_beating(cpsat_front, front)
        print(f"  CP-SAT dominates {beaten}/{len(front)} of {name}'s front points")

    assert cpsat_front, "CP-SAT found no schedule; the comparison proves nothing"
    # Rule 0's claim for N08: CP-SAT is not merely feasible, it is at least as
    # good as either baseline on the combined front -- no baseline point
    # survives non-domination against the union that a CP-SAT point does not
    # also survive at least as well. Concretely: every CP-SAT front point that
    # is not itself in the combined front must be *equalled*, not beaten, by
    # something else in it (weakly dominated at worst, since `quantise`
    # rounds motion to the centimetre and the metaheuristics do not).
    for _, cpsat_objectives in cpsat_front:
        assert not any(
            dominates(o, cpsat_objectives)
            for name in ("random_search", "hill_climbing")
            for _, o in results[name][0]
        ), "a baseline strictly beat a CP-SAT-proven-optimal point"
