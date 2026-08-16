"""N11 -- Ant Colony System visit routing.

`AcsRoutingScheduler` shares `hwpm.optimize.cpsat`'s feasibility machinery
(`allowed_starts`, `travel_slots`, `respects_travel_time`) and
`hwpm.optimize.evaluate` (`evaluate`, `pareto_front`) with the N09 baselines,
so what these tests exercise is specific to `hwpm.optimize.acs`: that it
implements `Scheduler` correctly, respects `Budget`, never returns an invalid
schedule, is deterministic under a fixed seed (gate 8), and that its routing
machinery (route classes / precedence segmentation) behaves as documented.
"""

from __future__ import annotations

import time
from random import Random

import pytest

from hwpm.domain.model import Acuity, Specialty
from hwpm.optimize.acs import AcsRoutingScheduler, _route_classes
from hwpm.optimize.cpsat import InfeasibleInstanceError, respects_travel_time
from hwpm.optimize.evaluate import dominates
from hwpm.optimize.instances import realistic_single_ward, tiny_instance
from hwpm.optimize.types import Budget, Instance

pytestmark = pytest.mark.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# Criterion 3: every returned schedule is feasible
# ---------------------------------------------------------------------------


def test_returned_schedules_are_feasible() -> None:
    inst = tiny_instance(Random(3), n_beds=4, n_slots=8)
    scheduler = AcsRoutingScheduler(popsize=5)
    front = scheduler.solve(inst, Budget(max_seconds=3.0), Random(11))
    assert front, "no schedule found; the test proves nothing"
    for schedule, _ in front:
        schedule.validate(inst.constraints)  # raises on a hard violation
        assert respects_travel_time(schedule, inst) == ()


# ---------------------------------------------------------------------------
# Criterion 4: the returned front is genuinely non-dominated
# ---------------------------------------------------------------------------


def test_front_is_nondominated() -> None:
    inst = realistic_single_ward(Random(17), n_beds=12, n_slots=24)
    scheduler = AcsRoutingScheduler(popsize=5)
    front = scheduler.solve(inst, Budget(max_seconds=4.0), Random(5))
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
    """Bounded by `max_evaluations`, not `max_seconds` -- same reasoning as
    the N09 baseline determinism test: a wall-clock deadline lets machine
    jitter change how many trails get built, which would make this flaky for
    reasons unrelated to the scheduler's own determinism."""
    inst = tiny_instance(Random(9), n_beds=4, n_slots=8)
    scheduler = AcsRoutingScheduler(popsize=4)
    budget = Budget(max_seconds=30.0, max_evaluations=40)
    first = scheduler.solve(inst, budget, Random(2026))
    second = scheduler.solve(inst, budget, Random(2026))
    assert [o.as_tuple() for _, o in first] == [o.as_tuple() for _, o in second]
    assert [s.visits for s, _ in first] == [s.visits for s, _ in second]


# ---------------------------------------------------------------------------
# Budget discipline
# ---------------------------------------------------------------------------


def test_respects_time_budget() -> None:
    inst = realistic_single_ward(Random(4), n_beds=12, n_slots=24)
    scheduler = AcsRoutingScheduler(popsize=5)
    budget = Budget(max_seconds=1.0)
    started = time.perf_counter()
    scheduler.solve(inst, budget, Random(1))
    elapsed = time.perf_counter() - started
    # Generous slack: one in-flight generation may finish after the deadline
    # is crossed, but the loop must not keep starting new generations.
    assert elapsed < budget.max_seconds + 2.0


def test_respects_evaluation_budget() -> None:
    inst = tiny_instance(Random(6), n_beds=4, n_slots=8)
    scheduler = AcsRoutingScheduler(popsize=4)
    budget = Budget(max_seconds=30.0, max_evaluations=5)
    started = time.perf_counter()
    scheduler.solve(inst, budget, Random(1))
    elapsed = time.perf_counter() - started
    assert elapsed < 10.0, "max_evaluations=5 should stop the search almost immediately"


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
    scheduler = AcsRoutingScheduler()
    with pytest.raises(InfeasibleInstanceError, match="oncology"):
        scheduler.solve(broken, Budget(max_seconds=1.0), Random(1))


# ---------------------------------------------------------------------------
# Configuration guards
# ---------------------------------------------------------------------------


def test_rejects_nonsense_configuration() -> None:
    with pytest.raises(ValueError, match="popsize"):
        AcsRoutingScheduler(popsize=0)
    with pytest.raises(ValueError, match="q0"):
        AcsRoutingScheduler(q0=1.5)
    with pytest.raises(ValueError, match="rho"):
        AcsRoutingScheduler(rho=0.0)
    with pytest.raises(ValueError, match="xi"):
        AcsRoutingScheduler(xi=-0.1)
    with pytest.raises(ValueError, match="tau0"):
        AcsRoutingScheduler(tau0=0.0)
    with pytest.raises(ValueError, match="alpha"):
        AcsRoutingScheduler(alpha=-1.0)
    with pytest.raises(ValueError, match="beta"):
        AcsRoutingScheduler(beta=-1.0)


# ---------------------------------------------------------------------------
# `_route_classes`: precedence as route segmentation (module docstring)
# ---------------------------------------------------------------------------


def test_route_classes_orders_by_isolation_then_descending_acuity() -> None:
    from hwpm.domain.model import PatientId

    a, b, c, d = (PatientId(x) for x in ("A", "B", "C", "D"))
    # c is isolated and must sort after the non-isolated group, which requires
    # its acuity to be no higher than the non-isolated group's lowest (a,
    # LOW) -- otherwise the pair is jointly infeasible (see the test below).
    acuity = {a: Acuity.LOW, b: Acuity.HIGH, c: Acuity.LOW, d: Acuity.MODERATE}
    isolated = {a: False, b: False, c: True, d: False}
    groups = _route_classes(
        (a, b, c, d), acuity, isolated, acuity_active=True, isolation_active=True
    )
    assert groups is not None
    # Non-isolated first (b: HIGH, d: MODERATE, a: LOW as separate classes,
    # each its own group since acuity differs), then isolated (c: HIGH).
    assert groups == [(b,), (d,), (a,), (c,)]


def test_route_classes_groups_ties_together() -> None:
    """Patients tied on both isolation and acuity land in one group -- the
    free-order routing decision ACS actually solves."""
    from hwpm.domain.model import PatientId

    a, b = (PatientId(x) for x in ("A", "B"))
    acuity = {a: Acuity.MODERATE, b: Acuity.MODERATE}
    isolated = {a: False, b: False}
    groups = _route_classes(
        (a, b), acuity, isolated, acuity_active=True, isolation_active=True
    )
    assert groups == [(a, b)]


def test_route_classes_none_for_jointly_infeasible_pair() -> None:
    """An isolated HIGH-acuity patient and a non-isolated LOW-acuity patient
    cannot be ordered to satisfy both `AcuityOrdering` and `IsolationLast` at
    once (documented in `cpsat.build_model`); `_route_classes` must discard,
    not silently emit an invalid class order."""
    from hwpm.domain.model import PatientId

    high_isolated, low_free = PatientId("HI"), PatientId("LO")
    acuity = {high_isolated: Acuity.HIGH, low_free: Acuity.LOW}
    isolated = {high_isolated: True, low_free: False}
    groups = _route_classes(
        (high_isolated, low_free),
        acuity,
        isolated,
        acuity_active=True,
        isolation_active=True,
    )
    assert groups is None


def test_route_classes_no_active_constraints_is_one_group() -> None:
    from hwpm.domain.model import PatientId

    a, b, c = (PatientId(x) for x in ("A", "B", "C"))
    groups = _route_classes(
        (a, b, c), {}, {}, acuity_active=False, isolation_active=False
    )
    assert groups == [(a, b, c)]
