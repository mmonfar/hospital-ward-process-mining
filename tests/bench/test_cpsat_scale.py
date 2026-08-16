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
from ortools.sat.python import cp_model

from hwpm.optimize.cpsat import OBJECTIVE_KEYS, CpSatScheduler, build_model, solve_single
from hwpm.optimize.evaluate import dominates
from hwpm.optimize.instances import (
    BED_IDS,
    assumptions,
    realistic_single_ward,
    teams_for_beds,
)
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


# ---------------------------------------------------------------------------
# N21 — hospital scale. SPEC-004 "Rule 0 re-decided at hospital scale (N21)".
# ---------------------------------------------------------------------------

#: The bed counts N21 sweeps: whole 6-bed ward bays, from the 30 that N08 proved
#: up to 54.
#:
#: **54 is a hard ceiling, not a choice.** `instances.BED_IDS` enumerates the
#: reference geometry — 9 wards x 6 beds — and `realistic_single_ward` raises
#: above it. Sweeping 60, 80 or 100 beds would mean inventing floor-plan
#: geometry the `web/hospital-ward.html` prototype does not have, which
#: `instances`' module docstring refuses to do on the grounds that it would put
#: made-up metres into the project's headline motion figure. 54 beds *is* the
#: whole modelled hospital, so this sweep covers the multi-ward regime the
#: reference geometry admits and no further. Anything beyond it needs SPEC-003
#: geometry first, and that is recorded as an open question in SPEC-004 rather
#: than fudged here.
N21_BED_COUNTS: tuple[int, ...] = (30, 36, 42, 48, 54)


@pytest.mark.bench
@pytest.mark.parametrize("n_beds", N21_BED_COUNTS)
def test_fixed_roster_is_a_saturation_curve_not_a_scale_curve(n_beds: int) -> None:
    """N21, part 1: with the roster pinned at 8, growing the ward measures the
    ward filling up rather than the solver slowing down.

    This exists because N08's scaling curve — which held the roster at 8 —
    reported "not proven in 300s at 40 beds" and was read as a solver limit.
    Pure *feasibility* is checked here (the model with no objective at all), so
    the result cannot be confused with a hard optimisation. If a bed count comes
    back INFEASIBLE then no schedule exists for it under any objective, and its
    row on N08's curve was never a timing.
    """
    inst = realistic_single_ward(Random(INSTANCE_SEED), n_beds=n_beds)
    assert len(inst.clinicians) == 8
    encoding = build_model(inst)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = PER_SOLVE_SECONDS
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = SOLVER_SEED
    started = time.perf_counter()
    status = solver.Solve(encoding.model)
    elapsed = time.perf_counter() - started
    name = solver.StatusName(status)
    print(
        f"\n  [fixed roster] n_beds={n_beds:>3}  8 clinicians  "
        f"feasibility={name:<12} {elapsed:8.2f}s"
    )
    assert name != "UNKNOWN", (
        f"feasibility of the {n_beds}-bed fixed-roster instance was not settled "
        f"in {PER_SOLVE_SECONDS}s; the saturation reading below is then a guess"
    )


@pytest.mark.bench
@pytest.mark.parametrize("n_beds", N21_BED_COUNTS)
def test_hospital_scale_curve(n_beds: int) -> None:
    """N21, part 2: the Rule 0 question proper — with staff scaled alongside
    beds (`teams_for_beds`), does CP-SAT still *prove* optimality?

    Makespan again, for N08's reason: it was by far the hardest of the five, and
    timing an easy objective produces a flattering curve. `PER_SOLVE_SECONDS` is
    N08's 300s unchanged so the two curves can be laid side by side.

    Only the 30-bed anchor is asserted. It is the instance N08 proved, at the
    same roster, so a failure there means the measurement apparatus moved rather
    than the finding. Every larger row is *reported*: turning "54 beds does not
    prove in 300s" into an assertion would make a faster machine or a newer
    OR-Tools fail the build for getting a better answer, and the recorded
    crossover lives in SPEC-004 and SELECTION-GUIDE.md where a human reads it.
    """
    teams = teams_for_beds(n_beds)
    inst = realistic_single_ward(Random(INSTANCE_SEED), n_beds=n_beds, n_teams=teams)
    assert len(inst.clinicians) == 8 * teams
    assert len({c.id for c in inst.clinicians}) == len(inst.clinicians)
    outcome = solve_single(
        inst, "makespan_s", seconds=PER_SOLVE_SECONDS, seed=SOLVER_SEED, workers=1
    )
    print(
        f"\n  [hospital scale] n_beds={n_beds:>3}  teams={teams}  "
        f"{len(inst.clinicians):>2} clinicians  makespan_s  "
        f"{outcome.status:<12} {outcome.wall_seconds:8.2f}s  "
        f"proven={outcome.proven_optimal}"
    )
    if n_beds == 30:
        assert outcome.proven_optimal, (
            "the 30-bed/8-clinician anchor N08 proved no longer proves; the "
            "apparatus has moved, so nothing else on this curve is comparable"
        )


#: The 2x2 that separates the two things "hospital scale" changes at once.
#: `teams_for_beds` steps the roster from 1 team to 2 between 30 and 36 beds, so
#: the first row of the hospital-scale curve moves *both* bed count and roster
#: size and on its own cannot say which one costs the proof. These four cells
#: vary one at a time.
N21_ISOLATION_CELLS: tuple[tuple[int, int], ...] = (
    (30, 1),  # the anchor: the instance N08 proved
    (30, 2),  # roster only
    (36, 1),  # beds only
    (36, 2),  # both -- the first row of the hospital-scale curve
)


#: The isolation cells get 900s, not `PER_SOLVE_SECONDS`, and the difference is
#: the whole finding. At 300s all three non-anchor cells come back unproven and
#: the curve reads "one step in any direction kills it" — which is false. At
#: 900s two of them prove (see SELECTION-GUIDE.md) and only the cell that moved
#: *both* variables stays stuck. A benchmark run at the shorter cap would
#: reproduce the wrong conclusion, so the cap is part of the measurement.
N21_DEEP_SECONDS = 900.0


@pytest.mark.bench
@pytest.mark.parametrize(("n_beds", "n_teams"), N21_ISOLATION_CELLS)
def test_isolate_beds_from_roster(n_beds: int, n_teams: int) -> None:
    """N21, part 3: beds or clinicians — which costs CP-SAT its proof?

    Neither, separately. Both, together. Run at `N21_DEEP_SECONDS` so that the
    cells which merely need more time can show that they only need more time,
    and the one that is genuinely stuck stands out against them.

    Reports two things a bare timing cannot give. The encoding size (`presence`
    is one boolean per plausible clinician-patient pairing) is what the roster
    multiplies. The **MIP gap** is what separates "expensive" from "stuck": a
    capped solve at a 0.1% gap and one at 92% are both "not proven", and only
    the second is a reason to reach for a metaheuristic.

    Reported, not asserted, for `test_hospital_scale_curve`'s reason: the
    finding belongs in SPEC-004 where a human reads it, and a machine that
    proves a cell this one could not should not fail the build.
    """
    inst = realistic_single_ward(Random(INSTANCE_SEED), n_beds=n_beds, n_teams=n_teams)
    encoding = build_model(inst)
    outcome = solve_single(
        inst, "makespan_s", seconds=N21_DEEP_SECONDS, seed=SOLVER_SEED, workers=1
    )
    gap = outcome.relative_gap
    print(
        f"\n  [isolation] beds={n_beds:>3} teams={n_teams} "
        f"clinicians={len(inst.clinicians):>2} "
        f"pairings={len(encoding.presence):>4}  makespan_s "
        f"{outcome.status:<12} {outcome.wall_seconds:8.2f}s  "
        f"proven={outcome.proven_optimal}  "
        f"gap={'n/a' if gap is None else f'{gap:.1%}'}"
    )


def test_bed_ceiling_is_the_reference_geometry() -> None:
    """Not a benchmark — a guard, so it runs on every commit.

    `N21_BED_COUNTS` stops at 54 because that is every bed the modelled building
    has. If someone extends the geometry, this fails and forces the sweep to be
    re-run rather than left quietly covering a fraction of the hospital.
    """
    assert max(N21_BED_COUNTS) == len(BED_IDS) == 54
