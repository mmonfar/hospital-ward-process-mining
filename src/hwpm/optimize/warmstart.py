"""Nearest-neighbour warm starts for the SPEC-004 optimiser. SPEC-007 Part B.

The third and least confident of SPEC-007 Part B's three uses ("in decreasing
order of confidence that they are worth it"): seed a search with schedules
shaped like the ones that worked on the most similar historical ward-days,
rather than from scratch.

What a "neighbour" contributes, and what it cannot
--------------------------------------------------
A historical ward-day and today's `Instance` do not share patients. Nothing of
a neighbour schedule transfers as an assignment; what transfers is the **bed
visiting order** — the route shape a team actually walked on a structurally
similar day. `seed_population` extracts that order and replays it over today's
patients, in the bed positions they occupy.

That is a deliberately weak form of transfer and it is stated as such. The
strong form — carrying over which clinician saw whom — is meaningless across
instances and would also be the form with a re-identification problem, since a
seed carrying historical clinician-patient pairs is historical data travelling
inside an optimiser artefact.

Why this does not preempt N11
-----------------------------
`hwpm.optimize.acs` *searches* the routing space. This only decides where the
initial population starts. The same distinction `hwpm.optimize.nsga2`'s
`_seed_offsets` docstring draws for its greedy seeder applies unchanged.

Governance
----------
This module does not import `hwpm.mining.embed` at all, and does not need to.
`seed_population` takes `Schedule`s; choosing *which* ward-days are neighbours
happens upstream in `hwpm.mining.embed.neighbours`, in whichever space the
caller fitted. That keeps the representation decision out of the optimiser
entirely: a warm start is one of the four uses ADR-0007 decision 5 explicitly
permits an embedding to drive, and this module stays indifferent to whether one
was involved. Nothing here is published and nothing here is a governance
figure.

Determinism: every draw comes from the `rng` argument (CLAUDE.md rule 3, and
SPEC-004 criterion 5 has to keep holding with warm starts enabled — SPEC-007
criterion 19).
"""

from __future__ import annotations

from collections.abc import Sequence
from random import Random

from hwpm.domain.model import Acuity, ClinicianId, IsolationStatus, LocationId, PatientId
from hwpm.domain.schedule import ConstraintViolationError, PlannedVisit, Schedule
from hwpm.optimize.cpsat import travel_slots
from hwpm.optimize.types import Instance


def bed_priority(neighbour: Schedule, beds: dict[PatientId, LocationId]) -> list[str]:
    """The bed ids of `neighbour`, in the order they were first visited.

    Across all clinicians, not per clinician: the transferable signal is "this
    ward was walked in roughly this order", and a per-clinician order would be
    a claim about a team composition that today's instance does not have.
    """
    events: list[tuple[int, str, str]] = []
    for visit in neighbour.visits:
        bed = beds.get(visit.patient)
        if bed is None:
            continue
        events.append((visit.start, bed.value, visit.patient.value))
    events.sort()
    seen: set[str] = set()
    order: list[str] = []
    for _start, bed_value, _patient in events:
        if bed_value in seen:
            continue
        seen.add(bed_value)
        order.append(bed_value)
    return order


def _cover(inst: Instance, rng: Random) -> dict[ClinicianId, list[PatientId]]:
    """Assign each (patient, specialty) requirement to a holder of it.

    Least-loaded first among the holders, ties broken by a draw from `rng` so
    that repeated calls give a *population* rather than one schedule repeated.
    A patient requiring two specialties held by two different clinicians is
    covered by both, which is what produces an MDT moment at all.
    """
    holders: dict[str, list[ClinicianId]] = {}
    for clinician in inst.clinicians:
        for specialty in clinician.specialties:
            holders.setdefault(specialty.value, []).append(clinician.id)
    for key in holders:
        holders[key].sort(key=lambda c: c.value)

    load: dict[ClinicianId, list[PatientId]] = {c.id: [] for c in inst.clinicians}
    requirements = [
        (patient.id, specialty.value)
        for patient in inst.patients
        for specialty in sorted(
            inst.required.get(patient.id, frozenset()), key=lambda s: s.value
        )
    ]
    rng.shuffle(requirements)
    for patient, specialty_key in requirements:
        candidates = holders.get(specialty_key)
        if not candidates:
            continue
        fewest = min(len(load[c]) for c in candidates)
        tied = [c for c in candidates if len(load[c]) == fewest]
        chosen = tied[rng.randrange(len(tied))]
        if patient not in load[chosen]:
            load[chosen].append(patient)
    return load


def _ordered(
    inst: Instance, patients: Sequence[PatientId], priority: list[str]
) -> list[PatientId]:
    """One clinician's route: forced class order first, bed priority within it.

    The class order is not a preference. `IsolationLast` and `AcuityOrdering`
    are hard constraints (`hwpm.domain.schedule`), so a seed that ignored them
    would be a seed that never validates. Within a class the neighbour's bed
    order decides, and beds the neighbour never visited go last in bed-id
    order — deterministically, so a fixed seed gives a fixed route.
    """
    rank = {bed: index for index, bed in enumerate(priority)}
    acuity = {p.id: p.acuity for p in inst.patients}
    isolation = {p.id: p.isolation_status for p in inst.patients}

    def key(patient: PatientId) -> tuple[int, int, int, str]:
        isolated = (
            isolation.get(patient, IsolationStatus.NONE) is not IsolationStatus.NONE
        )
        bed = inst.beds.get(patient)
        bed_value = bed.value if bed is not None else ""
        return (
            1 if isolated else 0,
            -int(acuity.get(patient, Acuity.LOW)),
            rank.get(bed_value, len(rank)),
            bed_value,
        )

    return sorted(dict.fromkeys(patients), key=key)


def _lay_out(
    inst: Instance, routes: dict[ClinicianId, list[PatientId]]
) -> Schedule | None:
    """Place each route on the slot grid, leaving room to walk between beds.

    Returns `None` if any clinician's route does not fit inside the round
    window. A seed that overruns the horizon is not a seed to be truncated: the
    truncated version covers fewer patients than the caller was told it would,
    and would be scored as though that were a choice the optimiser made.
    """
    visits: list[PlannedVisit] = []
    horizon = inst.slots.n_slots
    for clinician in sorted(routes, key=lambda c: c.value):
        clock = 0
        previous: PatientId | None = None
        for patient in routes[clinician]:
            if previous is not None:
                clock += travel_slots(inst, previous, patient)
            if clock + inst.visit_slots > horizon:
                return None
            visits.append(
                PlannedVisit(
                    clinician=clinician,
                    patient=patient,
                    start=clock,
                    duration=inst.visit_slots,
                )
            )
            clock += inst.visit_slots
            previous = patient
    return Schedule(visits=tuple(visits))


def seed_population(
    inst: Instance,
    neighbours: Sequence[Schedule],
    rng: Random,
    *,
    size: int | None = None,
) -> list[Schedule]:
    """SPEC-007 Part B's `seed_population`: legal schedules shaped like the
    nearest historical ward-days.

    One schedule is attempted per neighbour, plus repeats with fresh coverage
    draws until `size` is reached (default: one per neighbour). **Only legal
    schedules are returned** — every result has been through
    `Schedule.validate` against `inst.constraints`, so a caller cannot be
    handed a seed that violates a hard constraint and discover it during
    scoring. If nothing legal can be built the result is an empty list, which
    is the honest answer and is not the same as "no neighbours were supplied".

    `neighbours` empty is allowed and yields an empty list rather than falling
    back to a random population: silently substituting a different seeding
    strategy would make a measurement of warm starts a measurement of the
    fallback.
    """
    if not neighbours:
        return []
    target = size if size is not None else len(neighbours)
    if target < 1:
        raise ValueError(f"size must be >= 1, got {target!r}")

    out: list[Schedule] = []
    attempts = 0
    limit = target * 10
    while len(out) < target and attempts < limit:
        neighbour = neighbours[attempts % len(neighbours)]
        attempts += 1
        priority = bed_priority(neighbour, dict(inst.beds))
        load = _cover(inst, rng)
        routes = {
            clinician: _ordered(inst, patients, priority)
            for clinician, patients in load.items()
            if patients
        }
        schedule = _lay_out(inst, routes)
        if schedule is None:
            continue
        try:
            schedule.validate(inst.constraints)
        except ConstraintViolationError:
            continue
        out.append(schedule)
    return out


__all__ = ["bed_priority", "seed_population"]
