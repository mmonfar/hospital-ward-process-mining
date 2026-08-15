"""N08 — CP-SAT exact scheduler. SPEC-004 acceptance criteria 1 and 3.

The oracle here is brute force, as SPEC-004's "Test oracle" section requires:
enumerate *every* legal schedule of a tiny instance and take the best value of
each objective. That is the only test in this project that can distinguish "the
solver found a good answer" from "the solver found *the* answer", and it is
worth the enumeration cost because everything downstream — the Rule 0 decision,
the N10 gate — rests on CP-SAT's optima being real optima.

The brute-force enumerator deliberately shares `allowed_starts` and
`respects_travel_time` with the solver. Anything it did not share would be a
second, silently divergent statement of what "feasible" means, and the test
would then compare two different problems and pass.
"""

from __future__ import annotations

from datetime import datetime
from itertools import combinations, product
from random import Random

import pytest

from hwpm.domain.model import (
    Acuity,
    Clinician,
    ClinicianId,
    IsolationStatus,
    LocationId,
    Patient,
    PatientId,
    Role,
    Specialty,
)
from hwpm.domain.schedule import (
    AcuityOrdering,
    ClinicianAvailability,
    ConstraintViolationError,
    IsolationLast,
    NursingProtectedWindow,
    PatientUnavailable,
    PlannedVisit,
    Schedule,
)
from hwpm.domain.travel import TravelGraph
from hwpm.optimize.cpsat import (
    OBJECTIVE_KEYS,
    CpSatScheduler,
    InfeasibleInstanceError,
    allowed_starts,
    candidate_clinicians,
    payoff_table,
    respects_travel_time,
    solve_single,
    travel_slots,
)
from hwpm.optimize.evaluate import dominates, evaluate
from hwpm.optimize.instances import (
    BED_IDS,
    realistic_single_ward,
    tiny_instance,
)
from hwpm.optimize.types import Budget, Instance, Objectives, SlotGrid

_MAXIMISED = {"copresence", "continuity"}


# ---------------------------------------------------------------------------
# The brute-force oracle
# ---------------------------------------------------------------------------


def _covering_sets(inst: Instance, patient: PatientId) -> list[tuple[ClinicianId, ...]]:
    """Every set of clinicians that covers a patient's required specialties
    with *exactly one* holder per specialty — the same coverage rule the
    CP-SAT model states as `AddExactlyOne`."""
    required = inst.required.get(patient, frozenset())
    candidates = candidate_clinicians(inst)[patient]
    specialties = inst.specialties
    out: list[tuple[ClinicianId, ...]] = []
    for size in range(1, len(candidates) + 1):
        for subset in combinations(candidates, size):
            if all(sum(1 for c in subset if s in specialties[c]) == 1 for s in required):
                out.append(subset)
    return out


def brute_force(inst: Instance) -> list[tuple[Schedule, Objectives]]:
    """Every feasible schedule of `inst`, scored. Exponential on purpose."""
    starts = allowed_starts(inst)
    per_patient = [
        (patient.id, _covering_sets(inst, patient.id)) for patient in inst.patients
    ]
    scored: list[tuple[Schedule, Objectives]] = []
    for assignment in product(*[options for _, options in per_patient]):
        pairs: list[tuple[ClinicianId, PatientId]] = []
        for (patient_id, _), chosen in zip(per_patient, assignment, strict=True):
            pairs.extend((clinician, patient_id) for clinician in chosen)
        slot_choices = [starts[(c.value, p.value)] for c, p in pairs]
        if any(not choices for choices in slot_choices):
            continue
        for slots in product(*slot_choices):
            schedule = Schedule(
                visits=tuple(
                    PlannedVisit(
                        clinician=c, patient=p, start=t, duration=inst.visit_slots
                    )
                    for (c, p), t in zip(pairs, slots, strict=True)
                )
            )
            if schedule.hard_violations(inst.constraints):
                continue
            if respects_travel_time(schedule, inst):
                continue
            scored.append((schedule, evaluate(schedule, inst)))
    return scored


def _best(scored: list[tuple[Schedule, Objectives]], key: str) -> float:
    values = [getattr(objectives, key) for _, objectives in scored]
    return max(values) if key in _MAXIMISED else min(values)


# ---------------------------------------------------------------------------
# Criterion 1 — proven optimum matching brute force
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 5, 11])
def test_cpsat_optimal(seed: int) -> None:
    """SPEC-004 criterion 1: CP-SAT returns a proven optimum on a small
    instance, matching brute force — on every one of the five objectives, not
    just a convenient one."""
    inst = tiny_instance(Random(seed), n_beds=3, n_slots=5)
    exhaustive = brute_force(inst)
    assert exhaustive, "the oracle found no feasible schedule; the test proves nothing"

    for key in OBJECTIVE_KEYS:
        outcome = solve_single(inst, key, seconds=20, seed=seed)
        assert outcome.proven_optimal, f"{key}: status {outcome.status}"
        assert outcome.objectives is not None
        expected = _best(exhaustive, key)
        got = getattr(outcome.objectives, key)
        assert got == pytest.approx(expected, abs=0.02), (
            f"{key}: CP-SAT proved {got}, brute force found {expected}"
        )


def test_cpsat_optimum_is_feasible_for_the_oracle_too() -> None:
    """The optimum CP-SAT proves is a schedule brute force would have accepted.

    Guards the failure mode the equality test above cannot see: if the solver's
    feasible region were *wider* than the oracle's, it could beat brute force
    on value while returning something the oracle considers illegal, and a
    naive `==` comparison would simply fail without saying why.
    """
    inst = tiny_instance(Random(3), n_beds=3, n_slots=5)
    exhaustive = {
        tuple(sorted((v.clinician.value, v.patient.value, v.start) for v in s.visits))
        for s, _ in brute_force(inst)
    }
    for key in OBJECTIVE_KEYS:
        outcome = solve_single(inst, key, seconds=20, seed=2)
        assert outcome.schedule is not None
        signature = tuple(
            sorted(
                (v.clinician.value, v.patient.value, v.start)
                for v in outcome.schedule.visits
            )
        )
        assert signature in exhaustive, (
            f"{key}: solver returned a schedule the oracle rejects"
        )


# ---------------------------------------------------------------------------
# Criterion 3 — every returned schedule satisfies every hard constraint
# ---------------------------------------------------------------------------


def test_constraint_satisfaction() -> None:
    """SPEC-004 criterion 3: all returned schedules satisfy every hard
    constraint, and a violation raises."""
    rng = Random(17)
    inst = realistic_single_ward(rng, n_beds=12, n_slots=24)
    result = CpSatScheduler(grid=2).solve_detailed(
        inst, Budget(max_seconds=60.0), Random(4)
    )

    assert result.front, "no schedule found; criterion 3 is vacuous"
    for schedule, _ in result.front:
        # Raises ConstraintViolationError if any hard constraint is broken.
        schedule.validate(inst.constraints)
        assert respects_travel_time(schedule, inst) == ()

    # ...and a violation really does raise, rather than returning a flag
    # somebody forgets to check.
    broken = Schedule(
        visits=(
            *result.front[0][0].visits,
            PlannedVisit(
                clinician=inst.clinicians[0].id,
                patient=inst.patients[0].id,
                start=0,
                duration=inst.slots.n_slots,
            ),
        )
    )
    with pytest.raises(ConstraintViolationError):
        broken.validate(inst.constraints)


def test_hard_constraints_are_each_individually_respected() -> None:
    """Each `Constraint` type named in SPEC-004 actually binds the solver.

    A single feasible schedule proves nothing about a constraint that happened
    to be slack. Here every constraint is made tight enough that violating it
    would be the *attractive* option: the clinician's only free slots are late,
    the patient is away early, and acuity forces an order that costs motion.
    """
    graph = TravelGraph()
    grid = SlotGrid(start=datetime(2026, 8, 14, 9, 0), slot_seconds=300, n_slots=10)
    clinicians = (
        Clinician(
            ClinicianId("C0"), Role.CONSULTANT, frozenset({Specialty.GENERAL_MEDICINE})
        ),
        Clinician(ClinicianId("C1"), Role.REGISTRAR, frozenset({Specialty.CARDIOLOGY})),
    )
    patients = (
        Patient(PatientId("P0"), Acuity.CRITICAL, IsolationStatus.NONE),
        Patient(PatientId("P1"), Acuity.LOW, IsolationStatus.CONTACT),
        Patient(PatientId("P2"), Acuity.MODERATE, IsolationStatus.NONE),
    )
    acuity = {p.id: p.acuity for p in patients}
    isolation = {p.id: p.isolation_status for p in patients}
    constraints = (
        # C0 is in clinic until slot 4.
        ClinicianAvailability(ClinicianId("C0"), frozenset(range(4, 10))),
        # P2 is in imaging for the first half of the window.
        PatientUnavailable(PatientId("P2"), frozenset(range(0, 5))),
        AcuityOrdering(acuity=acuity),
        IsolationLast(isolation=isolation),
        NursingProtectedWindow(slots=frozenset({9})),
    )
    inst = Instance(
        patients=patients,
        clinicians=clinicians,
        constraints=constraints,
        graph=graph,
        slots=grid,
        required={
            PatientId("P0"): frozenset(
                {Specialty.GENERAL_MEDICINE, Specialty.CARDIOLOGY}
            ),
            PatientId("P1"): frozenset({Specialty.GENERAL_MEDICINE}),
            PatientId("P2"): frozenset({Specialty.CARDIOLOGY}),
        },
        beds={p.id: LocationId(BED_IDS[i]) for i, p in enumerate(patients)},
        visit_slots=1,
    )

    for key in OBJECTIVE_KEYS:
        outcome = solve_single(inst, key, seconds=20, seed=9)
        assert outcome.proven_optimal, f"{key}: {outcome.status}"
        schedule = outcome.schedule
        assert schedule is not None
        schedule.validate(inst.constraints)
        assert respects_travel_time(schedule, inst) == ()
        for visit in schedule.visits:
            if visit.clinician == ClinicianId("C0"):
                assert visit.start >= 4, "clinician availability not enforced"
            if visit.patient == PatientId("P2"):
                assert visit.start >= 5, "patient unavailability not enforced"


def test_infeasible_instance_names_the_uncoverable_patient() -> None:
    """A requirement no clinician holds is a malformed instance, not a hard
    search — SPEC-004's "optimising a fiction" failure mode."""
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
        solve_single(broken, "motion_m", seconds=5, seed=1)


# ---------------------------------------------------------------------------
# The epsilon-constraint sweep
# ---------------------------------------------------------------------------


def test_front_is_nondominated() -> None:
    """SPEC-004 criterion 4, for the exact path. Epsilon-constraint's plain
    form can emit weakly dominated points; `pareto_front` must remove them."""
    inst = tiny_instance(Random(7), n_beds=4, n_slots=8)
    front = CpSatScheduler(grid=2).solve(inst, Budget(max_seconds=60.0), Random(1))
    assert front
    for _, a in front:
        for _, b in front:
            assert not dominates(b, a)
    vectors = [objectives.as_tuple() for _, objectives in front]
    assert len(vectors) == len(set(vectors)), "duplicate objective vectors in the front"


def test_epsilon_sweep_returns_a_spread_of_trade_offs() -> None:
    """The sweep earns its cost: more than one distinct trade-off, which is the
    whole of ADR-0004's case for not scalarising.

    On a 4-bed instance the five objectives do *not* conflict — one schedule is
    simultaneously best on all five and the front is a single point, correctly.
    Conflict needs enough patients that co-presence and continuity start
    costing motion and makespan, so the instance here is mid-sized rather than
    tiny. Asserting spread on the tiny instance would have been asserting a
    property the problem does not have.
    """
    inst = realistic_single_ward(Random(17), n_beds=12, n_slots=24)
    front = CpSatScheduler(grid=2).solve(inst, Budget(max_seconds=120.0), Random(4))
    assert len(front) >= 2
    # Genuinely different trade-offs, not five ties on one objective.
    assert (
        len({o.copresence for _, o in front}) + len({o.continuity for _, o in front}) > 2
    )


def test_determinism() -> None:
    """SPEC-004 criterion 5, for the exact path: same seed, same front.

    Holds because `workers=1` and every solve proves optimality here. The
    module docstring is explicit that a time-limited FEASIBLE solve is
    reproducible only on the same machine under the same load, and
    `all_proven_optimal` is asserted so this test fails rather than flakes if
    that ever stops being true.
    """
    inst = tiny_instance(Random(7), n_beds=4, n_slots=8)
    scheduler = CpSatScheduler(grid=2)
    first = scheduler.solve_detailed(inst, Budget(max_seconds=90.0), Random(99))
    second = scheduler.solve_detailed(inst, Budget(max_seconds=90.0), Random(99))
    assert first.all_proven_optimal and second.all_proven_optimal
    assert [o.as_tuple() for _, o in first.front] == [
        o.as_tuple() for _, o in second.front
    ]
    assert [s.visits for s, _ in first.front] == [s.visits for s, _ in second.front]


def test_payoff_table_brackets_the_front() -> None:
    """Every front point lies inside the payoff table's ideal/nadir box on the
    objectives the box was built from — the property the epsilon grid relies on
    when it lays levels across that range."""
    inst = tiny_instance(Random(5), n_beds=3, n_slots=6)
    ranges, outcomes = payoff_table(inst, seconds=20, seed=3)
    assert all(o.proven_optimal for o in outcomes)
    assert set(ranges) == set(OBJECTIVE_KEYS)
    for key, (ideal, nadir) in ranges.items():
        if key in _MAXIMISED:
            assert ideal >= nadir
        else:
            assert ideal <= nadir


def test_scheduler_rejects_nonsense_configuration() -> None:
    with pytest.raises(ValueError, match="grid"):
        CpSatScheduler(grid=0)
    with pytest.raises(ValueError, match="primary"):
        CpSatScheduler(primary="cost")
    with pytest.raises(ValueError, match="workers"):
        CpSatScheduler(workers=0)
    with pytest.raises(ValueError, match="unknown objective"):
        solve_single(tiny_instance(Random(1), n_beds=2), "cost", seconds=1, seed=1)


# ---------------------------------------------------------------------------
# Modelling helpers
# ---------------------------------------------------------------------------


def test_travel_time_rounds_up() -> None:
    """Rounding travel down would buy the schedule free walking time. Two beds
    in different wards are never zero slots apart."""
    inst = tiny_instance(Random(1), n_beds=2)
    assert travel_slots(inst, PatientId("P0"), PatientId("P0")) == 0
    assert travel_slots(inst, PatientId("P0"), PatientId("P1")) >= 1


def test_respects_travel_time_catches_an_impossible_walk() -> None:
    inst = tiny_instance(Random(1), n_beds=2, n_slots=6)
    back_to_back = Schedule(
        visits=(
            PlannedVisit(inst.clinicians[2].id, PatientId("P0"), 0, 1),
            PlannedVisit(inst.clinicians[2].id, PatientId("P1"), 1, 1),
        )
    )
    assert respects_travel_time(back_to_back, inst)


def test_realistic_instance_matches_the_spec_shape() -> None:
    """SPEC-004's named benchmark instance really is 30 beds, 8 clinicians and
    a 3-hour window at 5-minute resolution."""
    inst = realistic_single_ward(Random(0))
    assert len(inst.patients) == 30
    assert len(inst.clinicians) == 8
    assert inst.slots.n_slots == 36
    assert inst.slots.slot_seconds == 300
    assert inst.slots.n_slots * inst.slots.slot_seconds == 3 * 60 * 60
    assert len(set(inst.beds.values())) == 30
    # Every required specialty has a holder, or the instance is a fiction.
    held = frozenset().union(*(c.specialties for c in inst.clinicians))
    for required in inst.required.values():
        assert required <= held


def test_realistic_instance_is_reproducible_from_its_seed() -> None:
    a = realistic_single_ward(Random(42))
    b = realistic_single_ward(Random(42))
    assert a.patients == b.patients
    assert a.required == b.required
    assert a.beds == b.beds
    assert a.previous_day == b.previous_day
