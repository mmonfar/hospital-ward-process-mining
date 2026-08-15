"""Optimisation interface types. SPEC-004 "Interface", node N08.

`Objectives` deliberately keeps five separate fields and offers no combined
score. ADR-0004: scalarising hides a clinical management decision inside a
weight vector nobody will ever see or challenge. There is no `.total()` here
and adding one would be the single easiest way to undo that decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, Protocol

from hwpm.domain.model import (
    Clinician,
    ClinicianId,
    LocationId,
    Patient,
    PatientId,
    Specialty,
    Visit,
)
from hwpm.domain.schedule import Constraint, Schedule
from hwpm.domain.travel import TravelGraph


@dataclass(frozen=True)
class SlotGrid:
    """The discretisation of the round window.

    SELECTION-GUIDE's scale sanity check is stated in these terms: "a 30-bed
    ward with 8 clinicians over a 3-hour round window discretised to 5-minute
    slots". Slot size is a modelling choice with a cost — halving it quadruples
    parts of the CP-SAT search — so it is a named parameter, not a constant.
    """

    start: datetime
    slot_seconds: int
    n_slots: int

    def __post_init__(self) -> None:
        if self.slot_seconds <= 0:
            raise ValueError(f"slot_seconds must be >= 1, got {self.slot_seconds!r}")
        if self.n_slots <= 0:
            raise ValueError(f"n_slots must be >= 1, got {self.n_slots!r}")

    def to_visit(
        self, clinician: ClinicianId, patient: PatientId, start: int, duration: int
    ) -> Visit:
        """Convert an integer-slot visit back to the wall-clock domain type."""
        return Visit(
            clinician_id=clinician,
            patient_id=patient,
            start_slot=self.start + timedelta(seconds=start * self.slot_seconds),
            duration=timedelta(seconds=duration * self.slot_seconds),
        )

    def seconds(self, slots: int) -> int:
        return slots * self.slot_seconds


@dataclass(frozen=True)
class Instance:
    """One ward-day scheduling problem.

    `required` is the output of an N04 `RequiredSpecialtyStrategy`, passed in
    rather than computed here: SPEC-001 made the definition a runtime parameter
    of every downstream analysis, so an `Instance` that derived it internally
    would re-bake the choice this project spent a node removing.
    """

    patients: tuple[Patient, ...]
    clinicians: tuple[Clinician, ...]
    constraints: tuple[Constraint, ...]
    graph: TravelGraph
    slots: SlotGrid
    #: Which specialties each patient requires, per the selected N04 strategy.
    required: Mapping[PatientId, frozenset[Specialty]]
    #: Where each patient's bed is, for routed travel cost.
    beds: Mapping[PatientId, LocationId]
    #: Slots one bedside visit takes. One value for now; SPEC-004 does not
    #: specify per-patient service times and inventing them would be modelling
    #: beyond the spec.
    visit_slots: int = 1
    #: Yesterday's clinician per patient, for objective 4 (continuity). Empty
    #: means "no prior day", and continuity is then reported as 0.0 rather than
    #: 1.0 -- an unknown must never score as a perfect result.
    previous_day: Mapping[PatientId, ClinicianId] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.previous_day is None:
            object.__setattr__(self, "previous_day", {})
        if self.visit_slots <= 0:
            raise ValueError(f"visit_slots must be >= 1, got {self.visit_slots!r}")

    @property
    def specialties(self) -> dict[ClinicianId, frozenset[Specialty]]:
        return {c.id: c.specialties for c in self.clinicians}

    def multi_specialty_patients(self) -> tuple[PatientId, ...]:
        """Patients requiring >=2 specialties — the denominator of objective 1.

        Patients requiring one specialty cannot have an MDT moment by
        definition, so including them would dilute the co-presence percentage
        with cases that were never at issue and make the headline figure look
        better the more single-specialty patients a ward happens to hold.
        """
        return tuple(
            p.id for p in self.patients if len(self.required.get(p.id, frozenset())) >= 2
        )


@dataclass(frozen=True)
class Objectives:
    """SPEC-004's five objectives. No combined score -- see the module
    docstring and ADR-0004."""

    copresence: float
    motion_m: float
    disruption: int
    continuity: float
    makespan_s: int

    #: Per field: True if larger is better. Used by the domination test, which
    #: must know each objective's direction; hard-coding the senses inside the
    #: comparison is how a sign error hides for months.
    #: A ClassVar mapping, not an instance field -- the senses are a property of
    #: the objective space itself and must never vary per solution.
    SENSES: ClassVar[dict[str, bool]] = {
        "copresence": True,
        "motion_m": False,
        "disruption": False,
        "continuity": True,
        "makespan_s": False,
    }

    def as_tuple(self) -> tuple[float, float, int, float, int]:
        return (
            self.copresence,
            self.motion_m,
            self.disruption,
            self.continuity,
            self.makespan_s,
        )


@dataclass(frozen=True)
class Budget:
    """What a scheduler may spend. Shared by the exact and the metaheuristic
    paths so the baseline gate (SPEC-004 criterion 2) can hold evaluation count
    equal across both -- comparing algorithms on unequal budgets is the
    commonest way to prove a favoured one wins."""

    max_seconds: float = 60.0
    max_evaluations: int | None = None

    def __post_init__(self) -> None:
        if self.max_seconds <= 0:
            raise ValueError(f"max_seconds must be > 0, got {self.max_seconds!r}")


class Scheduler(Protocol):
    """SPEC-004's scheduler interface. Returns a Pareto front, never one
    schedule (ADR-0004)."""

    def solve(
        self, inst: Instance, budget: Budget, rng: object
    ) -> list[tuple[Schedule, Objectives]]: ...
