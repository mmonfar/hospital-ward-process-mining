"""ADR-0003's profile gate. Node N12.

The decision ladder (ADR-0003) escalates to a C fitness kernel (N13) only if
**both** hold on the instance ADR-0003 names verbatim -- "one 30-bed ward, 8
clinicians, 3-hour round window at 5-minute resolution, NSGA-II with
population 200 for 500 generations":

1. The realistic run takes more than **10 minutes** wall clock.
2. Profiling attributes more than **60%** of that wall clock to the fitness
   loop -- `hwpm.optimize.evaluate.evaluate`, called once per candidate
   schedule.

Both conditions, not either: a slow run whose time is mostly elsewhere (repair,
route construction, selection) is not a fitness-loop problem, and a fast run
that happens to spend most of its (short) time in `evaluate` is not worth a
build toolchain and a memory-safety burden for.

**Measurement method, and why it is not `cProfile`.** `cProfile` was tried
first and its per-call overhead across ~100k evaluations changes the wall
clock the gate is supposed to be judging -- the number that answers "is a
realistic run interactively slow" has to come from a run that is not itself
slowed down by profiling it. Instead this benchmark wraps `evaluate` with a
`time.perf_counter()` accumulator (monkeypatched onto `hwpm.optimize.nsga2`'s
own bound name, since it is imported by name into that module's namespace) and
measures the *unprofiled* solve around it. The accumulator's overhead is two
`perf_counter()` calls per candidate, which is negligible next to `evaluate`
itself and does not need subtracting out.

**500 generations, expressed as an evaluation budget.** `Nsga2Scheduler.solve`
is budgeted by wall clock and/or evaluation count, not generation count
directly. Population 200 for 500 generations is therefore
`Budget(max_evaluations=200 * (500 + 1))` -- the `+1` for the initial
population, which the algorithm also scores. `max_seconds` is set high enough
that the evaluation cap binds, not the clock, so the benchmark is not
truncated before it reaches the instance ADR-0003 names.

Run to see the numbers:

    ./.venv/Scripts/python.exe -m pytest tests/bench/test_fitness_throughput.py --bench -s
"""

from __future__ import annotations

import time
from random import Random

import pytest

import hwpm.optimize.nsga2 as nsga2_module
from hwpm.optimize import evaluate as evaluate_module
from hwpm.optimize.instances import realistic_single_ward
from hwpm.optimize.nsga2 import Nsga2Scheduler
from hwpm.optimize.types import Budget

INSTANCE_SEED = 20260814
SOLVER_SEED = 2026

#: ADR-0003's realistic instance, verbatim: one 30-bed ward, 8 clinicians (one
#: team, `instances.realistic_single_ward`'s default), a 3-hour window at
#: 5-minute resolution (36 slots of 300s). All defaults; named here so the
#: instance this gate turns on is legible without opening `instances.py`.
POPSIZE = 200
GENERATIONS = 500

#: ADR-0003's own thresholds. Both must fire for N13 to be offered.
RUNTIME_THRESHOLD_SECONDS = 10 * 60.0
FITNESS_FRACTION_THRESHOLD = 0.60


def _instance():
    return realistic_single_ward(Random(INSTANCE_SEED), n_beds=30, n_slots=36, n_teams=1)


def _timed_evaluate_wrapper():
    """Wrap `evaluate` with a `perf_counter` accumulator and monkeypatch it
    onto `nsga2`'s own bound name (see module docstring for why there, not on
    `evaluate_module`). Returns `(restore, elapsed_fn, calls_fn)`."""
    original = evaluate_module.evaluate
    state = {"time": 0.0, "calls": 0}

    def timed(schedule, inst):
        started = time.perf_counter()
        result = original(schedule, inst)
        state["time"] += time.perf_counter() - started
        state["calls"] += 1
        return result

    nsga2_module.evaluate = timed

    def restore() -> None:
        nsga2_module.evaluate = original

    return restore, state


@pytest.mark.bench
def test_fitness_throughput_and_profile_gate() -> None:
    """ADR-0003's measurement, run at the exact instance and population/
    generation counts it names.

    Measured 2026-08-16 (this machine, `Random` seeds 20260814/2026), from
    this exact test: **wall clock 466.04s** (7.77 min) for 500 generations /
    100,200 `evaluate` calls, of which **29.94s (6.42%)** is inside `evaluate`
    itself. (An earlier hand-run of the same instance/seeds/counts measured
    445.37s / 6.24% -- consistent within ordinary machine-load variance, and
    nowhere near either threshold either way.)

    **Neither condition fires.** The run is under the 10-minute threshold
    (466s < 600s) and the fitness loop is nowhere near 60% of it (6.42%). The
    two facts point the same way independently: this instance's wall clock is
    dominated by everything *around* `evaluate` -- route materialisation,
    repair, non-dominated sorting, crowding distance, selection, breeding --
    not by scoring candidates. A C kernel for `evaluate` would speed up 6% of
    a run that is already comfortably interactive.

    **Decision, recorded here and in the audit log: N13-native-kernel's gate
    does not fire.** `native/` stays empty. This is not a one-off reading of
    the numbers -- the assertions below encode the actual gate, so a future
    run that crosses either threshold fails the test and forces the decision
    to be revisited rather than silently drifting stale in a comment.
    """
    inst = _instance()
    max_evaluations = POPSIZE * (GENERATIONS + 1)
    scheduler = Nsga2Scheduler(popsize=POPSIZE)
    # max_seconds set well above any plausible run so the evaluation cap is
    # what actually bounds the search -- this benchmark is about running the
    # full 500 generations, not about what fits in a clock budget.
    budget = Budget(max_seconds=3600.0, max_evaluations=max_evaluations)

    restore, state = _timed_evaluate_wrapper()
    try:
        started = time.perf_counter()
        front = scheduler.solve(inst, budget, Random(SOLVER_SEED))
        wall_seconds = time.perf_counter() - started
    finally:
        restore()

    fitness_seconds = state["time"]
    fitness_calls = state["calls"]
    fitness_fraction = fitness_seconds / wall_seconds if wall_seconds else 0.0

    print(
        f"\n--- N12 profile gate: {POPSIZE=} {GENERATIONS=} "
        f"({max_evaluations} evaluations) ---"
    )
    print(f"  front size:            {len(front)}")
    print(f"  evaluate() calls:      {fitness_calls}")
    print(f"  wall clock:            {wall_seconds:.2f}s ({wall_seconds / 60:.2f} min)")
    print(f"  time inside evaluate():{fitness_seconds:.2f}s")
    print(f"  fraction in fitness loop: {fitness_fraction * 100:.2f}%")

    runtime_fires = wall_seconds > RUNTIME_THRESHOLD_SECONDS
    fraction_fires = fitness_fraction > FITNESS_FRACTION_THRESHOLD
    gate_fires = runtime_fires and fraction_fires
    print(
        f"  runtime > 10 min:       {runtime_fires} "
        f"({wall_seconds:.1f}s vs {RUNTIME_THRESHOLD_SECONDS:.0f}s)"
    )
    print(
        f"  fitness loop > 60%:     {fraction_fires} "
        f"({fitness_fraction * 100:.2f}% vs {FITNESS_FRACTION_THRESHOLD * 100:.0f}%)"
    )
    print(f"  N13-native-kernel gate FIRES: {gate_fires}")

    # Sanity: the run actually did the work the gate is measured on. A front
    # this benchmark's assertions could pass vacuously on (e.g. an empty
    # search that never called evaluate) would make the percentages above
    # meaningless rather than reassuring.
    assert front, "NSGA-II found no schedule; there is nothing to profile"
    assert fitness_calls == max_evaluations, (
        f"expected exactly {max_evaluations} evaluate() calls "
        f"(population {POPSIZE} seeded cleanly and no repair discards across "
        f"{GENERATIONS} generations), got {fitness_calls} -- if this instance "
        "or scheduler now discards genomes, the evaluation count and the "
        "generation count it stands for have decoupled and GENERATIONS above "
        "no longer describes what ran"
    )

    # The gate itself. This is deliberately an assertion, not just a printed
    # number: ADR-0003's ladder says the C kernel is pursued only when both
    # conditions hold, and this project's practice (docs/AUDIT-LOG.md) is that
    # a graph node's finding is recorded where a later run re-checks it, not
    # only in prose that can go stale. As measured, the gate does not fire.
    # If a future change makes it fire, this assertion fails and that failure
    # *is* the signal that N13-native-kernel's condition should be
    # re-evaluated -- not evidence the benchmark is broken.
    assert not gate_fires, (
        f"ADR-0003's C-kernel gate now FIRES: wall={wall_seconds:.1f}s "
        f"(threshold {RUNTIME_THRESHOLD_SECONDS:.0f}s), "
        f"fitness_fraction={fitness_fraction:.3f} "
        f"(threshold {FITNESS_FRACTION_THRESHOLD:.2f}). N13-native-kernel's "
        "condition is now met and this must be recorded in the audit log and "
        "graph.yaml, not silently overridden here."
    )
