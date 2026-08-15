"""`Schedule` and the `Constraint` hierarchy. SPEC-004, node N08.

01-DOMAIN-MODEL.md, "Schedule (the optimisation subject)": a `Schedule` is a
set of `Visit`s that "knows how to validate itself against `Constraint`s but
does **not** know how to score itself; scoring lives in the optimisation layer
so the domain stays free of objective-function politics". That split is the
whole reason this module has no notion of an objective: `hwpm.optimize`
scores, `hwpm.domain` says only what is *legal*.

**Hard and soft constraints.** SPEC-004 requires that every returned schedule
satisfies every hard constraint and that a violation raises, while its
objective 3 *counts* round events colliding with protected nursing windows.
Those two statements are only consistent if protected windows are soft — a
cost to be traded off — and the rest are hard. SPEC-004 did not say so; the
N08 amendment does, and `Constraint.is_hard` makes it a property of the type
rather than a convention each caller has to remember. Adding a constraint
class therefore forces an explicit answer to "may a solver break this?".

The distinction is not cosmetic. A hard `NursingProtectedWindow` makes the
instance infeasible the moment a ward's protected window overlaps its only
viable round slot, and the honest output there is a schedule that costs the
nurses something, not no schedule at all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from itertools import pairwise

from hwpm.domain.model import (
    Acuity,
    ClinicianId,
    IsolationStatus,
    PatientId,
    Specialty,
)


@dataclass(frozen=True)
class PlannedVisit:
    """A visit placed on an integer slot grid.

    `hwpm.domain.model.Visit` carries a wall-clock `start_slot: datetime` and a
    `timedelta` duration, which is the right shape for an artefact a human
    reads. Optimisation needs integer slots: CP-SAT variables are integers, and
    reasoning about overlap in `datetime` invites timezone and rounding bugs in
    the innermost loop of the whole system. `SlotGrid.to_visit` converts back at
    the boundary, so the wall-clock type stays the one that leaves the layer.
    """

    clinician: ClinicianId
    patient: PatientId
    start: int
    duration: int

    def __post_init__(self) -> None:
        if self.duration <= 0:
            raise ValueError(f"duration must be >= 1 slot, got {self.duration!r}")
        if self.start < 0:
            raise ValueError(f"start must be >= 0, got {self.start!r}")

    @property
    def end(self) -> int:
        """Exclusive end slot: a visit starting at 3 for 2 slots occupies 3, 4
        and ends at 5. Exclusive so that `a.end <= b.start` means "no overlap"
        without an off-by-one at every comparison."""
        return self.start + self.duration

    def overlaps(self, other: PlannedVisit) -> bool:
        return self.start < other.end and other.start < self.end

    def occupies(self, slot: int) -> bool:
        return self.start <= slot < self.end


class Constraint(ABC):
    """A rule a `Schedule` may be checked against.

    `is_hard` is declared per class rather than passed per instance: whether a
    theatre list may be double-booked is a property of what a theatre list *is*,
    not a per-call option, and making it an instance flag would let a caller
    quietly soften a safety rule.
    """

    #: Hard constraints must hold in any returned schedule; soft ones are
    #: costed by `hwpm.optimize` instead. See the module docstring.
    is_hard: bool = True

    @abstractmethod
    def violations(self, schedule: Schedule) -> tuple[str, ...]:
        """Human-readable violations, empty if satisfied.

        Strings rather than booleans because the only useful thing to do with a
        failed schedule is explain it, and an auditor asking "why was this
        rejected" is a question the type should be able to answer.
        """


@dataclass(frozen=True)
class ClinicianAvailability(Constraint):
    """Slots a clinician can be on the ward at all — the complement of theatre
    lists and clinic commitments (SELECTION-GUIDE P1). Hard: a consultant in
    theatre is not available to be scheduled elsewhere."""

    clinician: ClinicianId
    available: frozenset[int]

    def violations(self, schedule: Schedule) -> tuple[str, ...]:
        out = []
        for visit in schedule.visits:
            if visit.clinician != self.clinician:
                continue
            outside = [
                s for s in range(visit.start, visit.end) if s not in self.available
            ]
            if outside:
                out.append(
                    f"{self.clinician.value} scheduled at slots {outside} "
                    f"outside availability (patient {visit.patient.value})"
                )
        return tuple(out)


@dataclass(frozen=True)
class PatientUnavailable(Constraint):
    """Slots a patient is off the ward — theatre, imaging, dialysis
    (01-DOMAIN-MODEL.md). Hard: you cannot round on an empty bed."""

    patient: PatientId
    slots: frozenset[int]

    def violations(self, schedule: Schedule) -> tuple[str, ...]:
        out = []
        for visit in schedule.visits:
            if visit.patient != self.patient:
                continue
            clash = [s for s in range(visit.start, visit.end) if s in self.slots]
            if clash:
                out.append(
                    f"patient {self.patient.value} visited at slots {clash} "
                    f"while off the ward (clinician {visit.clinician.value})"
                )
        return tuple(out)


@dataclass(frozen=True)
class NursingProtectedWindow(Constraint):
    """Medication rounds and handover windows.

    **Soft** — the one soft constraint in this module. SPEC-004's objective 3
    counts collisions with these windows, which only makes sense if collisions
    are possible. Enforcing it hard would make a ward whose protected window
    covers its only feasible round slot simply infeasible, hiding a real
    trade-off behind "no solution".
    """

    is_hard: bool = False
    slots: frozenset[int] = frozenset()

    def violations(self, schedule: Schedule) -> tuple[str, ...]:
        out = []
        for visit in schedule.visits:
            clash = [s for s in range(visit.start, visit.end) if s in self.slots]
            if clash:
                out.append(
                    f"{visit.clinician.value} visits {visit.patient.value} at "
                    f"slots {clash} inside a protected nursing window"
                )
        return tuple(out)


@dataclass(frozen=True)
class AcuityOrdering(Constraint):
    """Sickest first, per clinician (SPEC-001's modelling-assumptions table).

    Compares only consecutive visits in a clinician's own ordered route: a
    global sort would forbid a clinician ever walking back past a sicker
    patient they have already seen, which is not what "sickest first" means.
    """

    acuity: dict[PatientId, Acuity]

    def violations(self, schedule: Schedule) -> tuple[str, ...]:
        out = []
        for clinician, route in schedule.routes().items():
            for earlier, later in pairwise(route):
                a_earlier = self.acuity.get(earlier.patient)
                a_later = self.acuity.get(later.patient)
                if a_earlier is None or a_later is None:
                    continue
                if a_later > a_earlier:
                    out.append(
                        f"{clinician.value} sees {earlier.patient.value} "
                        f"(acuity {a_earlier.name}) before "
                        f"{later.patient.value} (acuity {a_later.name})"
                    )
        return tuple(out)


@dataclass(frozen=True)
class IsolationLast(Constraint):
    """Isolation patients come last in a clinician's route — infection control:
    a clinician leaving an isolation room should not then walk into a
    non-isolation bay on the same round."""

    isolation: dict[PatientId, IsolationStatus]

    def _is_isolated(self, patient: PatientId) -> bool:
        status = self.isolation.get(patient, IsolationStatus.NONE)
        return status is not IsolationStatus.NONE

    def violations(self, schedule: Schedule) -> tuple[str, ...]:
        out = []
        for clinician, route in schedule.routes().items():
            seen_isolated = False
            for visit in route:
                if self._is_isolated(visit.patient):
                    seen_isolated = True
                elif seen_isolated:
                    out.append(
                        f"{clinician.value} visits non-isolation patient "
                        f"{visit.patient.value} after an isolation patient"
                    )
        return tuple(out)


class ConstraintViolationError(Exception):
    """Raised by `Schedule.validate`. SPEC-004 acceptance criterion 3 requires
    that a hard-constraint violation raises rather than returning a flag —
    a returned flag is a flag somebody forgets to check."""

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        joined = "; ".join(violations)
        super().__init__(f"{len(violations)} hard constraint violation(s): {joined}")


@dataclass(frozen=True)
class Schedule:
    """A set of planned visits. Validates, never scores.

    Concurrent visits to one patient are legal and are the entire point: an MDT
    moment *is* two clinicians at one bedside at once (01-DOMAIN-MODEL.md). A
    clinician being in two places at once is not, and is checked here rather
    than left to each constraint.
    """

    visits: tuple[PlannedVisit, ...]

    def routes(self) -> dict[ClinicianId, tuple[PlannedVisit, ...]]:
        """Each clinician's visits in time order. Ties broken by patient id so
        the result is deterministic (gate 8) rather than dependent on the order
        a solver happened to emit."""
        by_clinician: dict[ClinicianId, list[PlannedVisit]] = {}
        for visit in self.visits:
            by_clinician.setdefault(visit.clinician, []).append(visit)
        return {
            clinician: tuple(sorted(vs, key=lambda v: (v.start, v.patient.value)))
            for clinician, vs in by_clinician.items()
        }

    def double_bookings(self) -> tuple[str, ...]:
        out = []
        for clinician, route in self.routes().items():
            for earlier, later in pairwise(route):
                if earlier.overlaps(later):
                    out.append(
                        f"{clinician.value} double-booked: "
                        f"{earlier.patient.value} [{earlier.start},{earlier.end}) "
                        f"overlaps {later.patient.value} [{later.start},{later.end})"
                    )
        return tuple(out)

    def hard_violations(self, constraints: tuple[Constraint, ...]) -> tuple[str, ...]:
        out = list(self.double_bookings())
        for constraint in constraints:
            if constraint.is_hard:
                out.extend(constraint.violations(self))
        return tuple(out)

    def validate(self, constraints: tuple[Constraint, ...]) -> None:
        """Raise `ConstraintViolationError` if any hard constraint is broken."""
        violations = self.hard_violations(constraints)
        if violations:
            raise ConstraintViolationError(violations)

    def specialties_at_bedside(
        self,
        patient: PatientId,
        clinician_specialties: dict[ClinicianId, frozenset[Specialty]],
    ) -> frozenset[Specialty]:
        """Specialties present in the largest *simultaneous* gathering at this
        patient's bedside.

        Simultaneity is the point. Two clinicians visiting the same patient
        three hours apart satisfy no MDT requirement; counting them would turn
        the headline co-presence figure into a visit count, which is exactly
        the inflation ADR-0006 exists to prevent. Evaluated at visit-start
        boundaries only — an overlap of any set of intervals always contains
        the latest start among them, so no gathering can be missed.
        """
        visits = [v for v in self.visits if v.patient == patient]
        best: frozenset[Specialty] = frozenset()
        for boundary in sorted({v.start for v in visits}):
            present: set[Specialty] = set()
            for visit in visits:
                if visit.occupies(boundary):
                    present |= clinician_specialties.get(visit.clinician, frozenset())
            if len(present) > len(best):
                best = frozenset(present)
        return best
