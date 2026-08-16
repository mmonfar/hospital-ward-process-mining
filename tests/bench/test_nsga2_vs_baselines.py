"""N10 — the baseline gate, run **at hospital scale**. SPEC-004 criterion 2,
`docs/06-QA-AND-DEADCODE.md` gate 9.

N09 ran this comparison on one 12-bed ward and CP-SAT won it outright, which
is why N08 demoted NSGA-II. N21 then measured above one ward and found CP-SAT
proves nothing there — 36, 42, 48 and 54 beds all FEASIBLE-not-proven at 300s
with the roster scaled to match, and 36 beds with 16 clinicians still 92.3%
from its bound after 900s. SPEC-004's amended criterion 2 therefore requires
this node to clear the gate *at that scale*, and the graph's N10 rationale
names the comparators: "against Random Search, HC+restarts AND the unproven
CP-SAT schedule".

Two tests, because those two comparisons are not the same question and
running them on one budget would answer neither honestly:

1. `test_nsga2_clears_the_baseline_gate_at_hospital_scale` — NSGA-II against
   N09's two baselines on an equal 60s `Budget`, at 36 and 54 beds. This is
   Rule 0's gate as written.
2. `test_nsga2_against_the_unproven_cpsat_incumbent` — NSGA-II against CP-SAT
   at 300s, the budget N21 measured a feasible hospital-scale incumbent at.
   Giving CP-SAT the 60s of test 1 would be a rigged comparison in NSGA-II's
   favour: `CpSatScheduler` divides its budget across 21 planned solves, so
   60s leaves under 3s each and it returns nothing at all at this size. That
   would let this node claim a win it had not earned.

Losing the optimality *guarantee* is not the same as losing to a
metaheuristic. N21 said so explicitly and it is worth restating where the
comparison actually runs: CP-SAT still returns schedules at hospital scale,
and on some objectives they are still better. This file settles that by
measurement, not by argument.

Fronts are passed through `cpsat.quantise` before any comparison, so the
centimetre-level float dust `_motion_metres` accumulates cannot read as a
genuine trade-off in either direction — without it a scheduler that happened
to round differently could "dominate" on a difference of femtometres.

Run to see the numbers:

    ./.venv/Scripts/python.exe -m pytest tests/bench/test_nsga2_vs_baselines.py --bench -s
"""

from __future__ import annotations

import time
from random import Random

import pytest

from hwpm.domain.schedule import Schedule
from hwpm.optimize.baselines import HillClimbingScheduler, RandomSearchScheduler
from hwpm.optimize.cpsat import CpSatScheduler, quantise, solve_single
from hwpm.optimize.evaluate import dominates, pareto_front
from hwpm.optimize.instances import realistic_single_ward, teams_for_beds
from hwpm.optimize.nsga2 import Nsga2Scheduler
from hwpm.optimize.types import Budget, Instance, Objectives

INSTANCE_SEED = 20260814
SOLVER_SEED = 2026

#: Equal for all three schedulers in the Rule 0 gate -- the entire point of
#: `Budget`. Sixty seconds because the gate has to run at two sizes and the
#: baselines have no proof-of-optimality escape hatch: they either find
#: schedules in the time given or they do not, and that is the finding.
COMPARISON_SECONDS = 60.0

#: What N21 measured CP-SAT's hospital-scale behaviour at.
CPSAT_SECONDS = 300.0

Front = list[tuple[Schedule, Objectives]]


def _instance(n_beds: int) -> Instance:
    """N21's hospital-scale instance: the roster scales with the beds
    (`teams_for_beds`), so the sweep measures size rather than the ward
    filling up. Same generator, seed and slot grid as N21's bench, so the two
    sets of numbers describe the same instances."""
    return realistic_single_ward(
        Random(INSTANCE_SEED),
        n_beds=n_beds,
        n_slots=36,
        n_teams=teams_for_beds(n_beds),
    )


def _run(scheduler: object, inst: Instance, seconds: float) -> tuple[Front, float]:
    started = time.perf_counter()
    front = scheduler.solve(  # type: ignore[attr-defined]
        inst, Budget(max_seconds=seconds), Random(SOLVER_SEED)
    )
    elapsed = time.perf_counter() - started
    return [(s, quantise(o)) for s, o in front], elapsed


def _report(name: str, front: Front, elapsed: float) -> None:
    if not front:
        print(f"  {name:<14} front=   0 wall={elapsed:6.2f}s  NO SCHEDULE FOUND")
        return
    print(
        f"  {name:<14} front={len(front):4d} wall={elapsed:6.2f}s "
        f"best_copresence={max(o.copresence for _, o in front):.3f} "
        f"best_motion={min(o.motion_m for _, o in front):9.2f} "
        f"best_makespan={min(o.makespan_s for _, o in front)}"
    )


def _dominated_by(front: Front, other: Front) -> int:
    """How many points of `other` some point of `front` strictly dominates."""
    return sum(1 for _, o in other if any(dominates(f, o) for _, f in front))


@pytest.mark.bench
@pytest.mark.parametrize("n_beds", [36, 54])
def test_nsga2_clears_the_baseline_gate_at_hospital_scale(n_beds: int) -> None:
    """36 beds is the first size past N21's practical crossover; 54 is the
    whole modelled hospital (`instances.BED_IDS`, 9 wards x 6 beds).

    Measured 2026-08-16, both sizes: **NSGA-II is the only one of the three
    that returns a schedule at all.** Fronts of 202 (36 beds) and 114 (54
    beds) against 0 and 0. Front sizes move by a few points between runs
    because the budget is wall-clock; determinism is asserted where it can be,
    under `max_evaluations`, in `tests/test_optimize_nsga2.py::test_determinism`.
    That is not a narrow win and it is not really a win
    about search: N09's baselines construct candidates by drawing coverage
    uniformly and discarding whatever is illegal, and at this scale
    essentially every draw is illegal — `nsga2._ordering_compatible`'s
    inequality has to hold for every clinician simultaneously, and 0 of 50
    uniform draws satisfied it on the 48-bed instance. The gate is cleared on
    the only terms Rule 0 offers, and the reason is representation, which is
    exactly what Rule 0 says a failed gate would have been evidence about.
    """
    inst = _instance(n_beds)
    print(
        f"\n--- N10 baseline gate at {n_beds} beds "
        f"({len(inst.clinicians)} clinicians, "
        f"{len(inst.multi_specialty_patients())} multi-specialty patients), "
        f"{COMPARISON_SECONDS}s each ---"
    )
    schedulers: dict[str, object] = {
        "nsga2": Nsga2Scheduler(),
        "random_search": RandomSearchScheduler(),
        "hill_climbing": HillClimbingScheduler(),
    }
    results: dict[str, Front] = {}
    for name, scheduler in schedulers.items():
        front, elapsed = _run(scheduler, inst, COMPARISON_SECONDS)
        results[name] = front
        _report(name, front, elapsed)

    combined = pareto_front([pair for front in results.values() for pair in front])
    surviving = {o.as_tuple() for _, o in combined}
    print(f"  combined non-dominated front (all three): {len(combined)}")
    for name, front in results.items():
        contributed = sum(1 for _, o in front if o.as_tuple() in surviving)
        print(f"    {name:<14} contributes {contributed:4d} / {len(front)}")

    nsga = results["nsga2"]
    for name in ("random_search", "hill_climbing"):
        other = results[name]
        print(
            f"    nsga2 dominates {_dominated_by(nsga, other)}/{len(other)} of "
            f"{name}; {name} dominates {_dominated_by(other, nsga)}/{len(nsga)} "
            f"of nsga2"
        )

    # The gate. Three separate claims because they fail for different reasons
    # and one combined assertion would hide which.
    assert nsga, "NSGA-II found no schedule; there is nothing to report"
    for name in ("random_search", "hill_climbing"):
        # 1. Nothing NSGA-II returns is beaten outright by a baseline. This is
        #    the assertion that fails loudly if the representation or the
        #    fitness function is wrong -- Rule 0's stated reason for the gate.
        assert _dominated_by(results[name], nsga) == 0, (
            f"{name} strictly beat an NSGA-II front point at {n_beds} beds"
        )
        # 2. No baseline point survives against NSGA-II's front. Vacuously
        #    true at 0/0 while the baselines find nothing, which is why the
        #    printed front sizes above are part of the evidence and not
        #    decoration.
        assert _dominated_by(nsga, results[name]) == len(results[name])
    # 3. NSGA-II is present in the answer, not merely un-beaten.
    assert any(o.as_tuple() in surviving for _, o in nsga)


@pytest.mark.bench
def test_nsga2_against_the_unproven_cpsat_incumbent() -> None:
    """The comparison the graph's N10 rationale demands, at the budget N21
    used. **NSGA-II does not simply beat CP-SAT here, and this test records
    that rather than avoiding it.**

    Measured 2026-08-16 at 36 beds, 300s each:

    - `CpSatScheduler`'s epsilon sweep returns a **5-point** front in 127.22s
      against NSGA-II's **271**. The two fronts are **mutually
      incomparable**: NSGA-II dominates 0 of the sweep's 5, and the sweep
      dominates 0 of NSGA-II's 271.
    - CP-SAT still proves **motion** optimal in 4.75s — 1129.61 m against
      NSGA-II's best 1250.42 m, **10.7% better**, and no NSGA-II point
      dominates that incumbent.
    - CP-SAT on **makespan** reproduces N21 exactly: FEASIBLE, 92.3% gap, and
      **14 of NSGA-II's 271 points dominate it outright**.
    - NSGA-II reaches copresence 1.000 and continuity 1.000; the two
      single-objective CP-SAT incumbents reach 0.273 and 0.545 copresence.

    The honest reading, and the reason this test asserts so little: **NSGA-II
    does not beat CP-SAT at 36 beds.** What it does is supply the *front* —
    the thing ADR-0004 requires and CP-SAT can no longer produce at usable
    density above one ward, 271 browsable options against 5 — while a
    single-objective CP-SAT run still wins the objective it is pointed at
    whenever that objective is still provable. Both halves are true. Reporting
    only the favourable one would be exactly the overclaim N21 warned against,
    and the assertion below is deliberately the weakest claim that would still
    catch a real defect.
    """
    n_beds = 36
    inst = _instance(n_beds)
    print(f"\n--- N10 vs unproven CP-SAT at {n_beds} beds, {CPSAT_SECONDS}s each ---")
    nsga, elapsed = _run(Nsga2Scheduler(), inst, CPSAT_SECONDS)
    _report("nsga2", nsga, elapsed)
    assert nsga, "NSGA-II found no schedule; the comparison proves nothing"

    sweep, elapsed = _run(CpSatScheduler(grid=2, workers=1), inst, CPSAT_SECONDS)
    _report("cp_sat_sweep", sweep, elapsed)
    print(
        f"    nsga2 dominates {_dominated_by(nsga, sweep)}/{len(sweep)} of the "
        f"sweep; the sweep dominates {_dominated_by(sweep, nsga)}/{len(nsga)} "
        f"of nsga2"
    )

    for objective in ("motion_m", "makespan_s"):
        outcome = solve_single(
            inst, objective, seconds=CPSAT_SECONDS, seed=SOLVER_SEED, workers=1
        )
        if outcome.objectives is None:
            print(f"  cp_sat single {objective}: {outcome.status}, no incumbent")
            continue
        incumbent = quantise(outcome.objectives)
        beating = sum(1 for _, o in nsga if dominates(o, incumbent))
        beaten = sum(1 for _, o in nsga if dominates(incumbent, o))
        print(
            f"  cp_sat single {objective}: status={outcome.status} "
            f"gap={outcome.relative_gap} -> {incumbent}"
        )
        print(
            f"    nsga2 points dominating it: {beating}/{len(nsga)}; "
            f"it dominates {beaten}/{len(nsga)} of nsga2"
        )
        # The one thing that must hold whatever the trade-off: a
        # single-objective CP-SAT incumbent must never dominate an NSGA-II
        # point outright. It can beat one on its own objective -- and on
        # motion it does -- but a point that is better on *all five* would
        # mean NSGA-II's front contains something strictly pointless.
        assert beaten == 0, (
            f"a CP-SAT {objective} incumbent strictly dominated an NSGA-II point"
        )
