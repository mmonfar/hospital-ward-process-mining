"""N08 benchmark — is CP-SAT tractable at ward scale? SPEC-004, Rule 0.

This is the measurement the Rule 0 decision rests on, so it is written to be
re-runnable rather than quoted:

    ./.venv/Scripts/python.exe -m pytest tests/bench/test_cpsat_scale.py --bench -s

`-s` matters: the numbers are printed, because a benchmark whose result is only
a green tick tells nobody whether the answer was 2 seconds or 200.

Everything is seeded — the instance from `INSTANCE_SEED`, the solver from
`SOLVER_SEED` — and `workers=1`, so a re-run on the same machine reproduces the
schedules exactly and a re-run elsewhere reproduces the *objective values*
exactly (proven optima are machine-independent; which of several equally
optimal schedules is returned is not, though with one worker and a fixed seed
it is stable in practice).

The assertions are deliberately loose. A benchmark that fails when a laptop is
busy trains people to ignore it; the tight claim ("all five objectives proven
optimal") is asserted, the timing is reported.
"""

from __future__ import annotations

import time
from random import Random

import pytest

from hwpm.optimize.cpsat import OBJECTIVE_KEYS, CpSatScheduler, solve_single
from hwpm.optimize.evaluate import dominates
from hwpm.optimize.instances import assumptions, realistic_single_ward
from hwpm.optimize.types import Budget

#: Fixed before the run and recorded here, per SPEC-004's "Tuning to the
#: answer" failure mode. Changing either invalidates a recorded result.
INSTANCE_SEED = 20260814
SOLVER_SEED = 2026

#: Generous per-solve ceiling. It is a guard against a hang, not a budget: if a
#: solve ever needs anything close to this the headline result has changed and
#: the printed timings will say so.
PER_SOLVE_SECONDS = 300.0


@pytest.mark.bench
def test_realistic_instance_payoff_table_is_proven_optimal() -> None:
    """The headline Rule 0 measurement: five single-objective solves on SPEC-004's
    realistic instance — 30 beds, 8 clinicians, 3-hour window, 5-minute slots."""
    inst = realistic_single_ward(Random(INSTANCE_SEED))
    assert len(inst.patients) == 30
    assert len(inst.clinicians) == 8
    assert inst.slots.n_slots == 36

    print("\n--- N08 CP-SAT scale benchmark (SPEC-004 Rule 0) ---")
    print(f"instance seed {INSTANCE_SEED}, solver seed {SOLVER_SEED}, workers 1")
    for line in assumptions():
        print(f"  assumption: {line}")
    print(
        f"  {len(inst.patients)} patients, {len(inst.clinicians)} clinicians, "
        f"{len(inst.multi_specialty_patients())} multi-specialty, "
        f"{inst.slots.n_slots} slots of {inst.slots.slot_seconds}s"
    )

    total = 0.0
    proven: dict[str, bool] = {}
    for key in OBJECTIVE_KEYS:
        outcome = solve_single(
            inst, key, seconds=PER_SOLVE_SECONDS, seed=SOLVER_SEED, workers=1
        )
        total += outcome.wall_seconds
        proven[key] = outcome.proven_optimal
        print(
            f"  {key:<12} {outcome.status:<10} {outcome.wall_seconds:8.2f}s  "
            f"{outcome.objectives}"
        )
    print(f"  payoff table total: {total:.2f}s")

    unproven = sorted(k for k, ok in proven.items() if not ok)
    assert not unproven, (
        f"CP-SAT failed to prove optimality for {unproven} within "
        f"{PER_SOLVE_SECONDS}s each -- Rule 0's premise no longer holds"
    )


@pytest.mark.bench
def test_realistic_instance_epsilon_sweep() -> None:
    """The full Pareto path: payoff table plus a `grid=2` epsilon sweep.

    Reports wall-clock, how many solves proved optimality, and how many epsilon
    boxes were provably empty. An infeasible box is a *complete* answer, not a
    failure — it is the sweep discovering that a corner of the objective space
    contains no schedule at all, which is information no metaheuristic can
    produce.
    """
    inst = realistic_single_ward(Random(INSTANCE_SEED))
    scheduler = CpSatScheduler(grid=2, workers=1)
    budget = Budget(max_seconds=PER_SOLVE_SECONDS * 21)

    started = time.perf_counter()
    result = scheduler.solve_detailed(inst, budget, Random(SOLVER_SEED))
    elapsed = time.perf_counter() - started

    slowest = max(o.wall_seconds for o in result.outcomes)
    print("\n--- N08 epsilon-constraint sweep ---")
    print(
        f"  {len(result.outcomes)} solves, {elapsed:.2f}s wall, "
        f"slowest single solve {slowest:.2f}s"
    )
    print(
        f"  all_proven_optimal={result.all_proven_optimal}  "
        f"provably-empty epsilon boxes={result.n_infeasible}  "
        f"front size={len(result.front)}"
    )
    for _, objectives in result.front:
        print(f"    {objectives}")

    assert result.front, "no schedule found on the realistic instance"
    assert result.all_proven_optimal, (
        "at least one solve hit its time limit without proving optimality; "
        "the front's points are then merely feasible"
    )
    # The front is a front.
    for _, a in result.front:
        for _, b in result.front:
            assert not dominates(b, a)


@pytest.mark.bench
@pytest.mark.parametrize("n_beds", [10, 20, 30, 40, 54])
def test_scaling_curve(n_beds: int) -> None:
    """How the single-objective solve time grows with ward size.

    Answers the question Rule 0 leaves open once the 30-bed instance lands:
    *how much* headroom is there before an exact method stops finishing? 54 is
    the reference geometry's total bed count (`instances.BED_IDS`), so this is
    the largest instance the modelled building admits.

    Makespan is the objective measured because the payoff table showed it to be
    by far the hardest of the five — timing an easy objective would produce a
    flattering curve.
    """
    inst = realistic_single_ward(Random(INSTANCE_SEED), n_beds=n_beds)
    outcome = solve_single(
        inst, "makespan_s", seconds=PER_SOLVE_SECONDS, seed=SOLVER_SEED, workers=1
    )
    print(
        f"\n  n_beds={n_beds:>3}  makespan_s  {outcome.status:<10} "
        f"{outcome.wall_seconds:8.2f}s  proven={outcome.proven_optimal}"
    )
