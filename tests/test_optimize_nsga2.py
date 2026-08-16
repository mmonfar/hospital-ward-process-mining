"""N10 — NSGA-II.

`hwpm.optimize.nsga2` reuses N08's feasibility machinery (`allowed_starts`,
`travel_slots`, `respects_travel_time`), N09's route ordering
(`baselines._order_route`) and the shared `evaluate`/`dominates`/`pareto_front`
of the optimisation layer, so what these tests exercise is what is specific to
this module: the three NSGA-II mechanics (Alg 101 ranking, Alg 102 sparsity,
Alg 103 selection), the genome/phenotype map, the repair operator's
termination (SPEC-004 criterion 7), and the `Scheduler` contract — feasible
schedules, a genuinely non-dominated front, budget discipline, determinism
under a fixed seed (gate 8).

The hospital-scale baseline comparison SPEC-004's amended criterion 2 requires
is a benchmark, not a unit test: `tests/bench/test_nsga2_vs_baselines.py`.
"""

from __future__ import annotations

import math
import time
from random import Random

import pytest

from hwpm.domain.model import Specialty
from hwpm.domain.schedule import Schedule
from hwpm.optimize import nsga2
from hwpm.optimize.cpsat import InfeasibleInstanceError, respects_travel_time
from hwpm.optimize.evaluate import dominates
from hwpm.optimize.instances import realistic_single_ward, tiny_instance
from hwpm.optimize.nsga2 import Nsga2Scheduler, front_ranks, sparsities
from hwpm.optimize.types import Budget, Instance, Objectives


def _objectives(
    copresence: float = 0.5,
    motion_m: float = 100.0,
    disruption: int = 0,
    continuity: float = 0.5,
    makespan_s: int = 3600,
) -> Objectives:
    return Objectives(copresence, motion_m, disruption, continuity, makespan_s)


# ---------------------------------------------------------------------------
# Criterion 3: every returned schedule is feasible
# ---------------------------------------------------------------------------


def test_returned_schedules_are_feasible() -> None:
    inst = tiny_instance(Random(3), n_beds=4, n_slots=8)
    front = Nsga2Scheduler().solve(inst, Budget(max_seconds=3.0), Random(11))
    assert front, "no schedule found; the test proves nothing"
    for schedule, _ in front:
        schedule.validate(inst.constraints)  # raises on a hard violation
        assert respects_travel_time(schedule, inst) == ()


def test_returned_schedules_are_feasible_on_a_ward() -> None:
    """The tiny instance is small enough that almost any construction is
    legal; a 12-bed ward has the availability, off-ward and travel pressure
    that makes feasibility a real claim."""
    inst = realistic_single_ward(Random(17), n_beds=12, n_slots=24)
    front = Nsga2Scheduler().solve(inst, Budget(max_seconds=5.0), Random(5))
    assert front
    for schedule, _ in front:
        schedule.validate(inst.constraints)
        assert respects_travel_time(schedule, inst) == ()


# ---------------------------------------------------------------------------
# Criterion 4: the returned front is genuinely non-dominated
# ---------------------------------------------------------------------------


def test_front_is_nondominated() -> None:
    inst = realistic_single_ward(Random(17), n_beds=12, n_slots=24)
    front = Nsga2Scheduler().solve(inst, Budget(max_seconds=5.0), Random(5))
    assert front
    for _, a in front:
        for _, b in front:
            assert not dominates(b, a)
    vectors = [o.as_tuple() for _, o in front]
    assert len(vectors) == len(set(vectors)), "duplicate objective vectors in the front"


# ---------------------------------------------------------------------------
# Criterion 5 / gate 8: fixed seed, identical front
# ---------------------------------------------------------------------------


def test_determinism() -> None:
    """Bounded by `max_evaluations`, not `max_seconds`, for the reason N09's
    equivalent test records: a wall-clock deadline lets two runs of one seed
    do different amounts of work purely from machine timing, which would make
    this flaky for a reason unrelated to the algorithm's own determinism."""
    inst = tiny_instance(Random(9), n_beds=4, n_slots=8)
    budget = Budget(max_seconds=30.0, max_evaluations=120)
    first = Nsga2Scheduler().solve(inst, budget, Random(2026))
    second = Nsga2Scheduler().solve(inst, budget, Random(2026))
    assert [o.as_tuple() for _, o in first] == [o.as_tuple() for _, o in second]
    assert [s.visits for s, _ in first] == [s.visits for s, _ in second]


def test_determinism_on_a_ward() -> None:
    inst = realistic_single_ward(Random(4), n_beds=12, n_slots=24)
    budget = Budget(max_seconds=60.0, max_evaluations=150)
    first = Nsga2Scheduler().solve(inst, budget, Random(99))
    second = Nsga2Scheduler().solve(inst, budget, Random(99))
    assert [o.as_tuple() for _, o in first] == [o.as_tuple() for _, o in second]


# ---------------------------------------------------------------------------
# Budget discipline
# ---------------------------------------------------------------------------


def test_respects_time_budget() -> None:
    inst = realistic_single_ward(Random(4), n_beds=12, n_slots=24)
    started = time.perf_counter()
    Nsga2Scheduler().solve(inst, Budget(max_seconds=1.0), Random(1))
    assert time.perf_counter() - started < 3.0


def test_respects_evaluation_budget() -> None:
    inst = tiny_instance(Random(6), n_beds=4, n_slots=8)
    started = time.perf_counter()
    Nsga2Scheduler().solve(inst, Budget(max_seconds=30.0, max_evaluations=5), Random(1))
    assert time.perf_counter() - started < 10.0


# ---------------------------------------------------------------------------
# Failure mode: a requirement no clinician holds
# ---------------------------------------------------------------------------


def test_infeasible_instance_names_the_uncoverable_patient() -> None:
    inst = tiny_instance(Random(2), n_beds=2, n_slots=5)
    broken = Instance(
        patients=inst.patients,
        clinicians=inst.clinicians,
        constraints=inst.constraints,
        graph=inst.graph,
        slots=inst.slots,
        required={p.id: frozenset({Specialty.ONCOLOGY}) for p in inst.patients},
        beds=inst.beds,
        visit_slots=inst.visit_slots,
    )
    with pytest.raises(InfeasibleInstanceError, match="oncology"):
        Nsga2Scheduler().solve(broken, Budget(max_seconds=1.0), Random(1))


def test_instance_requiring_nothing_returns_the_empty_schedule() -> None:
    """No patient requires any specialty, so there is no gene and nothing to
    evolve. An empty *front* would say "no schedule exists", which is false;
    the empty schedule is the honest answer."""
    inst = tiny_instance(Random(2), n_beds=2, n_slots=5)
    nothing = Instance(
        patients=inst.patients,
        clinicians=inst.clinicians,
        constraints=inst.constraints,
        graph=inst.graph,
        slots=inst.slots,
        required={},
        beds=inst.beds,
    )
    front = Nsga2Scheduler().solve(nothing, Budget(max_seconds=1.0), Random(1))
    assert len(front) == 1
    assert front[0][0].visits == ()


# ---------------------------------------------------------------------------
# Configuration guards
# ---------------------------------------------------------------------------


def test_rejects_nonsense_configuration() -> None:
    with pytest.raises(ValueError, match="popsize"):
        Nsga2Scheduler(popsize=1)
    with pytest.raises(ValueError, match="archive_size"):
        Nsga2Scheduler(archive_size=0)
    with pytest.raises(ValueError, match="tournament_size"):
        Nsga2Scheduler(tournament_size=0)
    with pytest.raises(ValueError, match="crossover_rate"):
        Nsga2Scheduler(crossover_rate=1.5)
    with pytest.raises(ValueError, match="mutation_rate"):
        Nsga2Scheduler(mutation_rate=-0.1)
    with pytest.raises(ValueError, match="max_repair_rounds"):
        Nsga2Scheduler(max_repair_rounds=-1)


# ---------------------------------------------------------------------------
# Algorithm 101 -- front rank assignment by non-dominated sorting
# ---------------------------------------------------------------------------


def test_front_ranks_peels_fronts_in_order() -> None:
    """Three points on a motion/makespan trade-off plus one dominated by all
    of them. The first three are mutually incomparable, so they share rank 0
    and the fourth is alone in rank 1."""
    scored = [
        _objectives(motion_m=100.0, makespan_s=3000),
        _objectives(motion_m=200.0, makespan_s=2000),
        _objectives(motion_m=300.0, makespan_s=1000),
        _objectives(motion_m=400.0, makespan_s=4000),
    ]
    assert front_ranks(scored) == [[0, 1, 2], [3]]


def test_front_ranks_partitions_every_individual_exactly_once() -> None:
    rng = Random(5)
    scored = [
        _objectives(
            copresence=rng.random(),
            motion_m=rng.uniform(100, 500),
            disruption=rng.randrange(5),
            continuity=rng.random(),
            makespan_s=rng.randrange(1000, 5000),
        )
        for _ in range(40)
    ]
    ranks = front_ranks(scored)
    flat = [i for rank in ranks for i in rank]
    assert sorted(flat) == list(range(40))
    # Every individual in a later rank is dominated by something in an
    # earlier one -- the property that makes the rank a fitness at all.
    for depth, rank in enumerate(ranks[1:], start=1):
        for i in rank:
            assert any(dominates(scored[j], scored[i]) for j in ranks[depth - 1])


def test_front_ranks_puts_identical_vectors_in_one_rank() -> None:
    """Duplicates do not dominate each other, so they cannot be separated by
    rank. Worth pinning: a domination test written with `>=` on every
    objective would rank one copy above the other and quietly destroy the
    front."""
    scored = [_objectives(), _objectives(), _objectives()]
    assert front_ranks(scored) == [[0, 1, 2]]


# ---------------------------------------------------------------------------
# Algorithm 102 -- sparsity / crowding distance
# ---------------------------------------------------------------------------


def test_sparsity_is_infinite_at_the_ends_and_finite_between() -> None:
    scored = [
        _objectives(motion_m=100.0, makespan_s=3000),
        _objectives(motion_m=200.0, makespan_s=2000),
        _objectives(motion_m=300.0, makespan_s=1000),
    ]
    spread = sparsities([0, 1, 2], scored)
    assert spread[0] == math.inf
    assert spread[2] == math.inf
    assert 0 < spread[1] < math.inf


def test_sparsity_prefers_the_isolated_point() -> None:
    """The whole purpose of Alg 102: of two interior points, the one whose
    neighbours are further away scores higher, so selection spreads the
    population along the front instead of clumping it."""
    scored = [
        _objectives(motion_m=0.0, makespan_s=1000),
        _objectives(motion_m=10.0, makespan_s=900),  # crowded: neighbours close
        _objectives(motion_m=20.0, makespan_s=800),
        _objectives(motion_m=200.0, makespan_s=400),  # isolated: big gaps
        _objectives(motion_m=400.0, makespan_s=100),
    ]
    spread = sparsities([0, 1, 2, 3, 4], scored)
    assert spread[3] > spread[1]


def test_sparsity_ignores_an_objective_the_front_is_constant_on() -> None:
    """A zero range would divide by zero if the guard were missing, and
    assuming the book's fallback range of 1 instead would let raw metres
    swamp co-presence's 0-to-1 scale."""
    scored = [
        _objectives(copresence=0.5, motion_m=100.0, makespan_s=3000),
        _objectives(copresence=0.5, motion_m=200.0, makespan_s=2000),
        _objectives(copresence=0.5, motion_m=300.0, makespan_s=1000),
    ]
    spread = sparsities([0, 1, 2], scored)
    assert spread[1] == pytest.approx(2.0)  # motion and makespan, 1.0 each


def test_sparsity_of_a_tiny_front_is_all_infinite() -> None:
    """With one or two members every individual is an end point, so Alg 102's
    interior loop never runs."""
    scored = [_objectives(motion_m=100.0), _objectives(motion_m=200.0)]
    assert sparsities([0, 1], scored) == {0: math.inf, 1: math.inf}


# ---------------------------------------------------------------------------
# Genome / phenotype
# ---------------------------------------------------------------------------


def test_phenotype_is_a_deterministic_function_of_the_genome() -> None:
    """The claim the whole GA rests on: materialising one genome twice gives
    the identical schedule. If it did not, a child would not inherit its
    parents' timing and crossover would transmit nothing — this would be
    random search wearing a population (module docstring)."""
    inst = realistic_single_ward(Random(21), n_beds=12, n_slots=24)
    ground = nsga2._ground(inst)
    genome = nsga2._seeded_genome(ground, Random(3))
    first = nsga2._materialise(ground, genome)
    second = nsga2._materialise(ground, genome)
    assert isinstance(first, Schedule)
    assert isinstance(second, Schedule)
    assert first.visits == second.visits


def test_crossover_takes_every_gene_from_one_parent_or_the_other() -> None:
    inst = realistic_single_ward(Random(21), n_beds=8, n_slots=24)
    ground = nsga2._ground(inst)
    rng = Random(1)
    mother = nsga2._seeded_genome(ground, rng)
    father = nsga2._seeded_genome(ground, rng)
    first, second = nsga2._uniform_crossover(ground, mother, father, rng)
    assert set(first) == set(ground.keys)
    assert set(second) == set(ground.keys)
    for key in ground.keys:
        assert first[key] in (mother[key], father[key])
        # The two children are complementary, which is what makes uniform
        # crossover conserve the parents' gene pool rather than resample it.
        assert {first[key], second[key]} == {mother[key], father[key]}


def test_mutation_only_moves_genes_to_holders_of_that_specialty() -> None:
    inst = realistic_single_ward(Random(21), n_beds=8, n_slots=24)
    ground = nsga2._ground(inst)
    rng = Random(2)
    genome = nsga2._seeded_genome(ground, rng)
    mutated = nsga2._mutate(ground, genome, rate=1.0, rng=rng)
    assert set(mutated) == set(genome)
    for (_, specialty), (clinician, offset) in mutated.items():
        assert clinician in ground.holders[specialty]
        assert 0 <= offset < inst.slots.n_slots


# ---------------------------------------------------------------------------
# Criterion 7: repair preserves feasibility and terminates
# ---------------------------------------------------------------------------


def test_repair_terminates_and_returns_a_feasible_schedule() -> None:
    inst = realistic_single_ward(Random(20260814), n_beds=12, n_slots=24)
    ground = nsga2._ground(inst)
    rng = Random(4)
    repaired = 0
    for _ in range(10):
        outcome = nsga2._repair(ground, nsga2._seeded_genome(ground, rng), rng, 40)
        if outcome is None:
            continue
        repaired += 1
        genome, schedule = outcome
        assert set(genome) == set(ground.keys)
        schedule.validate(inst.constraints)
        assert respects_travel_time(schedule, inst) == ()
    assert repaired, "no genome repaired; the test proves nothing"


def test_repair_with_zero_rounds_still_terminates() -> None:
    """`rounds=0` means "materialise once and take the answer" — the boundary
    where a bounded loop is most likely to have been written as unbounded."""
    inst = tiny_instance(Random(8), n_beds=4, n_slots=8)
    ground = nsga2._ground(inst)
    rng = Random(6)
    outcome = nsga2._repair(ground, nsga2._seeded_genome(ground, rng), rng, 0)
    assert outcome is None or isinstance(outcome[1], Schedule)


def test_repair_gives_up_rather_than_returning_an_invalid_schedule() -> None:
    """A genome that cannot be repaired is discarded. The failure mode this
    guards is a repair loop that "succeeds" by relaxing what feasible means:
    every returned schedule is validated, so the only two outcomes are a legal
    schedule or `None`."""
    inst = realistic_single_ward(Random(20260814), n_beds=30, n_slots=36)
    ground = nsga2._ground(inst)
    rng = Random(1)
    for _ in range(20):
        outcome = nsga2._repair(ground, nsga2._random_genome(ground, rng), rng, 2)
        if outcome is not None:
            outcome[1].validate(inst.constraints)


# ---------------------------------------------------------------------------
# ADR-0004: nothing anywhere scalarises the five objectives
# ---------------------------------------------------------------------------


def test_selection_uses_the_shared_domination_test() -> None:
    """NSGA-II's ranks come from `evaluate.dominates` itself, not from a local
    re-implementation that could drift from it (or quietly acquire a weighted
    sum). Asserted by identity because every other test here would still pass
    if this module grew its own comparison."""
    from hwpm.optimize.evaluate import dominates as shared

    assert nsga2.dominates is shared
