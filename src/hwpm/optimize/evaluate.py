"""Objective evaluation and Pareto machinery. SPEC-004, node N08.

`evaluate` is the function ADR-0003's profile gate is about: it is called
once per candidate schedule, which at NSGA-II population x generation counts
means millions of calls. SPEC-004 is explicit that its plain-Python form is
**permanent, not scaffolding** -- it is the correctness oracle any future C
kernel is differentially tested against, and deleting it deletes the kernel's
justification with it (ADR-0003). Kept readable on purpose.

Pareto domination is Alg 98 (p.139) and the front extraction Alg 100/101
(p.139-141) of the metaheuristics reference, per SELECTION-GUIDE.
"""

from __future__ import annotations

from itertools import pairwise

from hwpm.domain.schedule import NursingProtectedWindow, Schedule
from hwpm.optimize.types import Instance, Objectives


def evaluate(schedule: Schedule, inst: Instance) -> Objectives:
    """Score a schedule on SPEC-004's five objectives.

    Does not validate. Callers that need a legal schedule call
    `Schedule.validate` first; keeping validation out of the hot loop matters
    because the optimiser evaluates far more candidates than it returns.
    """
    return Objectives(
        copresence=_copresence(schedule, inst),
        motion_m=_motion_metres(schedule, inst),
        disruption=_disruption(schedule, inst),
        continuity=_continuity(schedule, inst),
        makespan_s=_makespan_seconds(schedule, inst),
    )


def _copresence(schedule: Schedule, inst: Instance) -> float:
    """Objective 1: fraction of multi-specialty patients for whom >=2 of their
    *required* specialties are simultaneously at the bedside.

    Required, not merely present. 01-DOMAIN-MODEL.md: a gathering that
    satisfies no requirement "is not an MDT moment; it is two people who
    happened to collide", and counting collisions would make the governance
    figure improve when clinicians bump into each other.
    """
    denominator = inst.multi_specialty_patients()
    if not denominator:
        # No patient needs an MDT, so co-presence is vacuous. Report 1.0 rather
        # than 0.0: the schedule has failed nobody. Recorded explicitly because
        # the opposite convention would make single-specialty wards look
        # permanently non-compliant.
        return 1.0
    specialties = inst.specialties
    satisfied = 0
    for patient in denominator:
        present = schedule.specialties_at_bedside(patient, specialties)
        required = inst.required.get(patient, frozenset())
        if len(present & required) >= 2:
            satisfied += 1
    return satisfied / len(denominator)


def _motion_metres(schedule: Schedule, inst: Instance) -> float:
    """Objective 2: total routed metres walked, summed over clinicians.

    Uses `TravelGraph.cost` -- the only sanctioned distance in the system
    (N06/SPEC-003). Straight-line distance between beds on different floors is
    the wrong answer and `Point.euclidean_distance_to` exists only for the
    synthetic generator's interim ground truth.

    Travel *to* the first bed is not counted: where a clinician starts the
    round is not modelled, and inventing a start location would put a number
    into the motion figure that no event in the log supports.
    """
    total = 0.0
    for route in schedule.routes().values():
        for earlier, later in pairwise(route):
            a = inst.beds.get(earlier.patient)
            b = inst.beds.get(later.patient)
            if a is None or b is None:
                continue
            total += inst.graph.cost(a, b).metres
    return total


def _disruption(schedule: Schedule, inst: Instance) -> int:
    """Objective 3: count of visits colliding with protected nursing windows.

    Counts visits, not slots: a nursing round interrupted once for fifteen
    minutes is one interruption, and counting slots would make a long visit
    look like several separate disruptions.
    """
    windows = [c for c in inst.constraints if isinstance(c, NursingProtectedWindow)]
    if not windows:
        return 0
    protected: set[int] = set()
    for window in windows:
        protected |= window.slots
    return sum(
        1
        for visit in schedule.visits
        if any(slot in protected for slot in range(visit.start, visit.end))
    )


def _continuity(schedule: Schedule, inst: Instance) -> float:
    """Objective 4: fraction of patients seen by the same clinician as
    yesterday.

    With no previous day recorded the result is 0.0, not 1.0. An unknown that
    scores as a perfect result is the kind of default that quietly makes a
    first-run report look excellent.
    """
    if not inst.previous_day:
        return 0.0
    seen: dict[str, set[str]] = {}
    for visit in schedule.visits:
        seen.setdefault(visit.patient.value, set()).add(visit.clinician.value)
    matched = 0
    for patient, previous in inst.previous_day.items():
        if previous.value in seen.get(patient.value, set()):
            matched += 1
    return matched / len(inst.previous_day)


def _makespan_seconds(schedule: Schedule, inst: Instance) -> int:
    """Objective 5: wall-clock seconds to the last visit's completion."""
    if not schedule.visits:
        return 0
    last = max(visit.end for visit in schedule.visits)
    return inst.slots.seconds(last)


def dominates(a: Objectives, b: Objectives) -> bool:
    """Pareto domination, Alg 98 p.139: `a` dominates `b` if it is at least as
    good on every objective and strictly better on at least one."""
    at_least_as_good = True
    strictly_better = False
    for field, larger_is_better in Objectives.SENSES.items():
        av = getattr(a, field)
        bv = getattr(b, field)
        if av == bv:
            continue
        a_better = av > bv if larger_is_better else av < bv
        if a_better:
            strictly_better = True
        else:
            at_least_as_good = False
    return at_least_as_good and strictly_better


def pareto_front(
    scored: list[tuple[Schedule, Objectives]],
) -> list[tuple[Schedule, Objectives]]:
    """The non-dominated subset, Alg 100 p.139.

    Duplicate objective vectors are collapsed to one representative: a front
    listing seven schedules that score identically is not seven options for a
    clinical director, it is one option printed seven times. The representative
    is chosen deterministically (first by input order) so the same input gives
    the same front (gate 8).
    """
    front: list[tuple[Schedule, Objectives]] = []
    seen: set[tuple[float, float, int, float, int]] = set()
    for schedule, objectives in scored:
        if any(dominates(other, objectives) for _, other in scored):
            continue
        key = objectives.as_tuple()
        if key in seen:
            continue
        seen.add(key)
        front.append((schedule, objectives))
    return front
