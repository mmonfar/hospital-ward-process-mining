"""Random Search and Hill-Climbing with Random Restarts. SPEC-004, node N09.

Rule 0 of `SELECTION-GUIDE.md`: nothing fancier than exact search (N08) is
reportable until it demonstrably beats plain **Random Search** (Alg 9, p.22)
and **Hill-Climbing with Random Restarts** (Alg 10, p.23) on the same
instances under the same evaluation budget. NSGA-II (N10) is gated on beating
these; this module is what it has to beat.

**Candidate representation.** Neither reference algorithm says what a
"candidate solution" or "Tweak" is for this problem -- that is domain
modelling this module has to supply, same as N08 had to supply an encoding for
CP-SAT. An assignment is `{patient -> {specialty -> covering clinician}}`, the
same coverage decision `AddExactlyOne` makes in `hwpm.optimize.cpsat`. A
candidate `Schedule` is *materialised* from an assignment by grouping visits
per clinician, ordering each clinician's route to respect `AcuityOrdering` and
`IsolationLast` where an ordering respecting both exists, and drawing a start
slot for each visit uniformly from the slots that are legal for that
clinician/patient pair (`cpsat.allowed_starts`) and leave room to walk from the
previous visit (`cpsat.travel_slots`). No repair operator: if no such ordering
or no such start slot exists, the draw is **discarded** and another is drawn.
SPEC-004's routing and ordering constraints are tight enough (see
`cpsat.build_model`'s note on acuity/isolation jointly infeasible pairs) that a
generate-and-repair operator would itself be a second, undocumented statement
of "feasible" alongside `Schedule.hard_violations` and
`cpsat.respects_travel_time`; discarding keeps there being exactly one.

**Multi-objective acceptance, not scalarisation.** Both reference algorithms
compare candidates with `Quality(R) > Quality(S)`, a single number. ADR-0004
forbids inventing that number here just as it forbids it in the CP-SAT
encoding, so the comparison used throughout this module is **strict Pareto
dominance** (`hwpm.optimize.evaluate.dominates`): a move is accepted only if it
is at least as good on every objective and strictly better on one. This is a
strengthening of `Quality(R) > Quality(S)`, not an unrelated substitute -- on a
single objective the two tests coincide. The consequence, stated plainly: most
Tweaks in a five-objective space are *incomparable* (better on some axes,
worse on others) rather than dominating or dominated, so Hill-Climbing accepts
fewer moves per restart than a scalar-quality reader might expect and restarts
more often. That is the honest behaviour of "no scalarisation" applied to a
local search, not a bug to average away.

**"Best" is a Pareto archive.** Algorithm 9 and Algorithm 10 both track one
`Best`. Here every schedule the search ever evaluates is retained and the
final return value is `pareto_front(everything)` -- the direct multi-objective
generalisation of "the best thing seen so far" that ADR-0004 requires a
Scheduler to return.

**Tweak** (Hill-Climbing only) is one of two moves, chosen with equal
probability each call:

1. **Reassignment.** Pick one `(patient, specialty)` covering decision at
   random and swap it for a different clinician who also holds that
   specialty, if more than one exists; re-materialise. This is the move that
   can change which specialties end up co-present (objective 1) and who sees
   whom (objective 4).
2. **Re-timing.** Keep the assignment fixed and re-materialise with a fresh
   random draw of start slots. Because start-slot selection is itself random
   within `_materialize`, calling it again on an unchanged assignment is a
   legitimate small, bounded, random change to the schedule's timing (motion,
   disruption, makespan) without touching who does what.

Both algorithms are seeded through an explicit `rng: Random`
(`04-AGENT-ORCHESTRATION.md` / this project's stochastic-component rule) --
never module-level `random`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from itertools import pairwise
from random import Random

from hwpm.domain.model import Acuity, ClinicianId, PatientId, Specialty
from hwpm.domain.schedule import PlannedVisit, Schedule
from hwpm.optimize.cpsat import (
    InfeasibleInstanceError,
    acuity_of,
    allowed_starts,
    isolated_of,
    ordering_active,
    respects_travel_time,
    travel_slots,
)
from hwpm.optimize.evaluate import dominates, evaluate, pareto_front
from hwpm.optimize.types import Budget, Instance, Objectives

#: (patient, specialty) -> covering clinician. The one decision both the
#: CP-SAT coverage constraint (`AddExactlyOne`) and this module's candidates
#: make; kept as its own type alias because every helper below either builds
#: or reads one.
Assignment = dict[PatientId, dict[Specialty, ClinicianId]]


@dataclass(frozen=True)
class _State:
    """One evaluated candidate: the decision that produced it, the schedule,
    and its score, kept together so a Tweak can read the decision without
    re-deriving it from the `Schedule` it produced."""

    assignment: Assignment
    schedule: Schedule
    objectives: Objectives


def _specialty_holders(inst: Instance) -> dict[Specialty, tuple[ClinicianId, ...]]:
    specialties = inst.specialties
    pool = {s for required in inst.required.values() for s in required}
    return {
        specialty: tuple(c.id for c in inst.clinicians if specialty in specialties[c.id])
        for specialty in pool
    }


def _check_feasible(
    inst: Instance, holders: dict[Specialty, tuple[ClinicianId, ...]]
) -> None:
    """Same check `cpsat.build_model` makes before spending any search effort:
    a required specialty no rostered clinician holds is a malformed instance,
    not a hard search. Raising here rather than looping forever on empty
    draws matches N08's `InfeasibleInstanceError` contract exactly."""
    for patient in inst.patients:
        for specialty in inst.required.get(patient.id, frozenset()):
            if not holders.get(specialty):
                raise InfeasibleInstanceError(
                    f"patient {patient.id.value} requires {specialty.value} "
                    f"and no rostered clinician holds it"
                )


def _random_assignment(
    inst: Instance, holders: dict[Specialty, tuple[ClinicianId, ...]], rng: Random
) -> Assignment:
    assignment: Assignment = {}
    for patient in inst.patients:
        required = inst.required.get(patient.id, frozenset())
        cov: dict[Specialty, ClinicianId] = {}
        for specialty in required:
            cov[specialty] = rng.choice(holders[specialty])
        assignment[patient.id] = cov
    return assignment


def _order_route(
    patients: tuple[PatientId, ...],
    acuity: dict[PatientId, Acuity],
    isolated: dict[PatientId, bool],
    acuity_active: bool,
    isolation_active: bool,
) -> tuple[PatientId, ...] | None:
    """Order one clinician's assigned patients so that, if an ordering exists
    respecting both `AcuityOrdering` (never step up in acuity) and
    `IsolationLast` (nothing follows isolation but isolation), this returns
    it. Heuristic, not exhaustive: non-isolated patients first (descending
    acuity), then isolated patients (descending acuity); the two ordering
    rules can be jointly unsatisfiable for a pair assigned to one clinician
    (documented in `cpsat.build_model`), in which case this returns `None` and
    the candidate is discarded rather than silently emitting an invalid
    route."""
    if isolation_active:
        first_group = [p for p in patients if not isolated.get(p, False)]
        second_group = [p for p in patients if isolated.get(p, False)]
    else:
        first_group, second_group = list(patients), []
    if acuity_active:
        first_group.sort(key=lambda p: acuity.get(p, Acuity.LOW), reverse=True)
        second_group.sort(key=lambda p: acuity.get(p, Acuity.LOW), reverse=True)
    ordered = tuple(first_group + second_group)
    for earlier, later in pairwise(ordered):
        if acuity_active and acuity.get(later, Acuity.LOW) > acuity.get(
            earlier, Acuity.LOW
        ):
            return None
        if (
            isolation_active
            and isolated.get(earlier, False)
            and not isolated.get(later, False)
        ):
            return None
    return ordered


def _assign_starts(
    inst: Instance,
    clinician: ClinicianId,
    ordered_patients: tuple[PatientId, ...],
    starts_table: dict[tuple[str, str], tuple[int, ...]],
    rng: Random,
) -> list[PlannedVisit] | None:
    """Draw a start slot for each visit in route order, uniformly among the
    slots that are legal for the pair (availability, patient off-ward) and
    leave enough room to walk from the previous visit. Random, not earliest --
    this is what makes the candidate space Random Search actually samples,
    rather than a single greedy schedule with random assignment layered on
    top."""
    duration = inst.visit_slots
    visits: list[PlannedVisit] = []
    prev_end: int | None = None
    prev_patient: PatientId | None = None
    for patient in ordered_patients:
        key = (clinician.value, patient.value)
        legal = starts_table.get(key, ())
        if prev_patient is not None and prev_end is not None:
            need = travel_slots(inst, prev_patient, patient)
            min_start = prev_end + need
        else:
            min_start = 0
        feasible = [t for t in legal if t >= min_start]
        if not feasible:
            return None
        start = rng.choice(feasible)
        visits.append(
            PlannedVisit(
                clinician=clinician, patient=patient, start=start, duration=duration
            )
        )
        prev_end = start + duration
        prev_patient = patient
    return visits


def _materialize(inst: Instance, assignment: Assignment, rng: Random) -> Schedule | None:
    """Turn a coverage assignment into a `Schedule`, or `None` if no legal
    route/timing exists for it. Every hard constraint is re-checked on the way
    out (`Schedule.hard_violations`, `respects_travel_time`) rather than
    trusted to follow from the construction, so a bug in the construction
    fails a discard rather than returning an invalid schedule."""
    per_clinician: dict[ClinicianId, list[PatientId]] = {}
    for patient, cov in assignment.items():
        for clinician in set(cov.values()):
            per_clinician.setdefault(clinician, []).append(patient)

    acuity = acuity_of(inst)
    isolated = isolated_of(inst)
    acuity_active, isolation_active = ordering_active(inst)
    starts_table = allowed_starts(inst)

    visits: list[PlannedVisit] = []
    for clinician in sorted(per_clinician, key=lambda c: c.value):
        patients = tuple(sorted(per_clinician[clinician], key=lambda p: p.value))
        ordered = _order_route(
            patients, acuity, isolated, acuity_active, isolation_active
        )
        if ordered is None:
            return None
        clinician_visits = _assign_starts(inst, clinician, ordered, starts_table, rng)
        if clinician_visits is None:
            return None
        visits.extend(clinician_visits)

    schedule = Schedule(visits=tuple(visits))
    if schedule.hard_violations(inst.constraints):
        return None
    if respects_travel_time(schedule, inst):
        return None
    return schedule


def _random_state(
    inst: Instance, holders: dict[Specialty, tuple[ClinicianId, ...]], rng: Random
) -> _State | None:
    assignment = _random_assignment(inst, holders, rng)
    schedule = _materialize(inst, assignment, rng)
    if schedule is None:
        return None
    return _State(assignment, schedule, evaluate(schedule, inst))


def _tweak(
    state: _State,
    inst: Instance,
    holders: dict[Specialty, tuple[ClinicianId, ...]],
    rng: Random,
) -> _State | None:
    """One small, bounded, random change -- see the module docstring for the
    two move kinds."""
    reassignable = [
        (patient, specialty)
        for patient, cov in state.assignment.items()
        for specialty in cov
        if len(holders[specialty]) > 1
    ]
    if reassignable and rng.random() < 0.5:
        patient, specialty = rng.choice(reassignable)
        current = state.assignment[patient][specialty]
        alternatives = [c for c in holders[specialty] if c != current]
        new_clinician = rng.choice(alternatives)
        new_cov = dict(state.assignment[patient])
        new_cov[specialty] = new_clinician
        new_assignment = dict(state.assignment)
        new_assignment[patient] = new_cov
    else:
        new_assignment = state.assignment

    schedule = _materialize(inst, new_assignment, rng)
    if schedule is None:
        return None
    return _State(new_assignment, schedule, evaluate(schedule, inst))


def _within_budget(started: float, evaluations: int, budget: Budget) -> bool:
    if time.perf_counter() - started >= budget.max_seconds:
        return False
    return budget.max_evaluations is None or evaluations < budget.max_evaluations


@dataclass(frozen=True)
class RandomSearchScheduler:
    """Algorithm 9, p.22, adapted to a Pareto archive in place of a scalar
    `Best` (see module docstring). Implements `hwpm.optimize.types.Scheduler`.
    """

    def solve(
        self, inst: Instance, budget: Budget, rng: Random
    ) -> list[tuple[Schedule, Objectives]]:
        holders = _specialty_holders(inst)
        _check_feasible(inst, holders)
        started = time.perf_counter()
        evaluations = 0
        scored: list[tuple[Schedule, Objectives]] = []
        while _within_budget(started, evaluations, budget):
            state = _random_state(inst, holders, rng)
            if state is None:
                continue
            evaluations += 1
            scored.append((state.schedule, state.objectives))
        return pareto_front(scored)


@dataclass(frozen=True)
class HillClimbingScheduler:
    """Algorithm 10, p.23: Hill-Climbing with Random Restarts, adapted to
    Pareto-dominance acceptance and a Pareto archive (see module docstring).
    Implements `hwpm.optimize.types.Scheduler`.

    `min_inner_iters`/`max_inner_iters` stand in for Algorithm 10's `T`, "a
    distribution of possible time intervals" -- the reference algorithm leaves
    the distribution unspecified, so a discrete uniform range over Tweak
    counts is the stated choice here (counts rather than wall-clock seconds,
    so the range is stable across machines of different speed).
    """

    min_inner_iters: int = 5
    max_inner_iters: int = 20

    def __post_init__(self) -> None:
        if self.min_inner_iters < 1:
            raise ValueError(
                f"min_inner_iters must be >= 1, got {self.min_inner_iters!r}"
            )
        if self.max_inner_iters < self.min_inner_iters:
            raise ValueError(
                f"max_inner_iters ({self.max_inner_iters!r}) must be >= "
                f"min_inner_iters ({self.min_inner_iters!r})"
            )

    def solve(
        self, inst: Instance, budget: Budget, rng: Random
    ) -> list[tuple[Schedule, Objectives]]:
        holders = _specialty_holders(inst)
        _check_feasible(inst, holders)
        started = time.perf_counter()
        evaluations = 0
        scored: list[tuple[Schedule, Objectives]] = []

        state: _State | None = None
        while state is None and _within_budget(started, evaluations, budget):
            state = _random_state(inst, holders, rng)
            if state is not None:
                evaluations += 1
        if state is None:
            # Budget exhausted before a single feasible draw landed.
            return []
        scored.append((state.schedule, state.objectives))

        while _within_budget(started, evaluations, budget):
            inner_iters = rng.randint(self.min_inner_iters, self.max_inner_iters)
            for _ in range(inner_iters):
                if not _within_budget(started, evaluations, budget):
                    break
                neighbour = _tweak(state, inst, holders, rng)
                if neighbour is None:
                    continue
                evaluations += 1
                scored.append((neighbour.schedule, neighbour.objectives))
                if dominates(neighbour.objectives, state.objectives):
                    state = neighbour

            if not _within_budget(started, evaluations, budget):
                break

            # Random restart: a fresh draw, independent of the climb just
            # finished (Algorithm 10 line 13).
            restart: _State | None = None
            while restart is None and _within_budget(started, evaluations, budget):
                restart = _random_state(inst, holders, rng)
                if restart is not None:
                    evaluations += 1
            if restart is None:
                break
            state = restart
            scored.append((state.schedule, state.objectives))

        return pareto_front(scored)
