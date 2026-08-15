"""Exact CP-SAT scheduler for the single-ward instance. SPEC-004, node N08.

This module exists to answer one question, and the answer is allowed to be
"you do not need the rest of SPEC-004". Rule 0 of `SELECTION-GUIDE.md` says a
metaheuristic is what you reach for when no exact method finishes in time; the
guide's own scale sanity check ("a 30-bed ward with 8 clinicians over a 3-hour
round window discretised to 5-minute slots is well inside exact-solver range")
is a prediction this node is here to test rather than assume. If CP-SAT proves
optimality at that scale within the interaction budget, NSGA-II (N10) becomes a
cross-check and a large amount of planned work is deleted. See
`hwpm.optimize.instances.realistic_single_ward` and `tests/bench/` for the
measurement.

**No scalarisation.** ADR-0004 forbids collapsing the five objectives into one
weighted sum, and that prohibition binds the exact solver exactly as it binds
the metaheuristic — CP-SAT minimises a single expression, so a naive exact
formulation is *precisely* the weighted sum ADR-0004 rejects. The way out is
the standard one: **epsilon-constraint** (Haimes et al.). Optimise one
objective subject to bounds on the other four, and sweep the bounds. Each solve
is single-objective and exact; the union of their answers, filtered through
`pareto_front`, is a Pareto set.

**What epsilon-constraint does not give you.** It produces a *representative*
set, not a provably complete front, and this is stated plainly because the
opposite claim is easy to make by accident:

- Coverage is bounded by the grid. With `grid=g` levels on each of four
  non-primary objectives the sweep is `g**4` solves; points of the true front
  that fall between grid lines are simply never asked for.
- The grid is laid over the **payoff table**'s ranges, whose upper ends are an
  estimated nadir (the worst value each objective takes across the individual
  optima), not the true nadir. With more than two objectives the true nadir is
  itself hard to compute, so parts of the front can lie outside the swept box.
- The plain (non-augmented) form can return *weakly* dominated points when a
  bound is not tight. We filter every collected solution through
  `pareto_front`, which removes them, at the cost of some solves contributing
  nothing.
- Objectives are optimised in integer native units (centimetres, whole visits,
  whole slots). Motion is therefore optimal to the centimetre, not to the float
  metre `evaluate` reports.

What it *does* give is a set of points each of which is a **proven optimum of
its own constrained problem** — which is more than any metaheuristic can say
about any point it returns, and is the thing Rule 0 is asking about.

**Every returned schedule is re-scored with `hwpm.optimize.evaluate.evaluate`**
rather than read out of the solver's objective value. The encoding here and the
reference implementation there are two independent statements of the same five
measures, and reporting the solver's own arithmetic back to itself would make
the pair useless as a cross-check.

Determinism (SPEC-004 criterion 5) holds under two conditions, both of which
this module makes explicit rather than assumes: `workers=1` (CP-SAT's portfolio
search is deterministic per-worker but the winning worker is a race), and every
solve reaching OPTIMAL before its time limit. A time-limited solve that returns
FEASIBLE is reproducible only on the same machine under the same load.
`SolveOutcome.proven_optimal` and `FrontResult.all_proven_optimal` carry that
distinction so a caller never has to guess.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise, product
from random import Random

from ortools.sat.python import cp_model

from hwpm.domain.model import Acuity, ClinicianId, IsolationStatus, PatientId
from hwpm.domain.schedule import (
    AcuityOrdering,
    ClinicianAvailability,
    IsolationLast,
    NursingProtectedWindow,
    PatientUnavailable,
    PlannedVisit,
    Schedule,
)
from hwpm.optimize.evaluate import evaluate, pareto_front
from hwpm.optimize.types import Budget, Instance, Objectives

#: Objective keys, spelled exactly as `Objectives`' fields so that a caller
#: naming a primary objective cannot name one that does not exist.
OBJECTIVE_KEYS: tuple[str, ...] = (
    "copresence",
    "motion_m",
    "disruption",
    "continuity",
    "makespan_s",
)

#: Native integer units the solver works in, per objective key. Kept as a
#: written-down table rather than folded into the encoding because the
#: conversion between these and `Objectives`' floats is where an off-by-a-factor
#: error would silently corrupt every epsilon bound in the sweep.
#:
#:   copresence  count of multi-specialty patients with an MDT moment
#:   motion_m    centimetres (integerised metres)
#:   disruption  count of visits colliding with a protected nursing window
#:   continuity  count of patients kept with yesterday's clinician
#:   makespan_s  slots to the last visit's completion
_MAXIMISE: frozenset[str] = frozenset({"copresence", "continuity"})

#: A visit's routed travel time is rounded **up** to whole slots. Rounding down
#: would buy the schedule free walking time and produce an optimum that cannot
#: be walked; on a 5-minute grid the conservative direction is the only
#: defensible one. The consequence is that any two consecutive visits are at
#: least one slot apart even when the beds are adjacent, which is a real cost
#: of the discretisation and not a modelling accident.
_ROUND_TRAVEL_UP = True


class InfeasibleInstanceError(Exception):
    """The instance cannot be scheduled for a reason visible without searching.

    Raised at *build* time, not returned as a status, and distinct from CP-SAT's
    own INFEASIBLE. A patient requiring a specialty no rostered clinician holds
    is not a hard scheduling problem, it is a malformed instance — most likely a
    wrong `RequiredSpecialty` strategy (SPEC-004's first named failure mode,
    "optimising a fiction"). Letting the solver spend its time budget proving
    that unsatisfiable is both slow and diagnostically useless.
    """


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SolveOutcome:
    """One single-objective (optionally epsilon-constrained) solve.

    `proven_optimal` is separate from "we got a schedule" on purpose. Rule 0
    turns on whether the exact method *proves* optimality at ward scale, so a
    time-limited FEASIBLE answer must never be reported in a way that reads as
    a proof.
    """

    objective: str
    schedule: Schedule | None
    objectives: Objectives | None
    status: str
    proven_optimal: bool
    wall_seconds: float
    epsilon: tuple[tuple[str, int], ...] = ()

    @property
    def feasible(self) -> bool:
        return self.schedule is not None


@dataclass(frozen=True)
class FrontResult:
    """The output of a full epsilon-constraint sweep.

    Carries the individual solves alongside the front because the front alone
    cannot answer "was this proven?", and that is the question N08 exists to
    answer.
    """

    front: tuple[tuple[Schedule, Objectives], ...]
    outcomes: tuple[SolveOutcome, ...]
    wall_seconds: float
    seed: int
    primary: str
    grid: int
    workers: int

    @property
    def all_proven_optimal(self) -> bool:
        """True only if every *feasible* solve reached OPTIMAL. Infeasible
        solves are excluded: an epsilon box that provably contains no schedule
        is a complete answer, not an unfinished one."""
        return all(o.proven_optimal for o in self.outcomes if o.feasible)

    @property
    def n_infeasible(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "INFEASIBLE")


# ---------------------------------------------------------------------------
# Modelling helpers, shared with the brute-force oracle in the tests
# ---------------------------------------------------------------------------


def travel_slots(inst: Instance, earlier: PatientId, later: PatientId) -> int:
    """Whole slots a clinician needs to walk between two patients' beds.

    Uses `TravelGraph.cost(...).seconds`, not metres: what separates two visits
    in time is time, and the two are deliberately not proportional in
    `hwpm.domain.travel` — a lift ride costs seconds far out of proportion to
    its metres, which is the entire point of that module.
    """
    a = inst.beds.get(earlier)
    b = inst.beds.get(later)
    if a is None or b is None:
        return 0
    seconds = inst.graph.cost(a, b).seconds
    ratio = seconds / inst.slots.slot_seconds
    return math.ceil(ratio) if _ROUND_TRAVEL_UP else round(ratio)


def respects_travel_time(schedule: Schedule, inst: Instance) -> tuple[str, ...]:
    """Consecutive visits leave room to walk between the beds.

    Not a `Constraint` subclass, and deliberately so. `hwpm.domain.schedule`'s
    constraints are statements about clinical rules; this is a statement about
    ward geometry, which the domain layer models in `TravelGraph` and which a
    `Constraint` would have to reach across the layer boundary to consult. It
    lives here, with the solver that enforces it, and the brute-force oracle in
    the tests calls the same function so that "feasible" means one thing.
    """
    out: list[str] = []
    for clinician, route in schedule.routes().items():
        for earlier, later in pairwise(route):
            need = travel_slots(inst, earlier.patient, later.patient)
            if later.start < earlier.end + need:
                out.append(
                    f"{clinician.value} cannot walk {earlier.patient.value} -> "
                    f"{later.patient.value} in {later.start - earlier.end} slot(s); "
                    f"needs {need}"
                )
    return tuple(out)


def _motion_centimetres(inst: Instance, earlier: PatientId, later: PatientId) -> int:
    a = inst.beds.get(earlier)
    b = inst.beds.get(later)
    if a is None or b is None:
        return 0
    return round(inst.graph.cost(a, b).metres * 100)


def _availability(inst: Instance) -> dict[ClinicianId, frozenset[int] | None]:
    """Per clinician, the slots they may be on the ward. `None` means no
    `ClinicianAvailability` was supplied, which is "unconstrained" — distinct
    from an empty frozenset, which is "never available"."""
    out: dict[ClinicianId, frozenset[int] | None] = {c.id: None for c in inst.clinicians}
    for constraint in inst.constraints:
        if isinstance(constraint, ClinicianAvailability):
            existing = out.get(constraint.clinician)
            out[constraint.clinician] = (
                constraint.available
                if existing is None
                else existing & constraint.available
            )
    return out


def _patient_blocked(inst: Instance) -> dict[PatientId, frozenset[int]]:
    out: dict[PatientId, set[int]] = {}
    for constraint in inst.constraints:
        if isinstance(constraint, PatientUnavailable):
            out.setdefault(constraint.patient, set()).update(constraint.slots)
    return {k: frozenset(v) for k, v in out.items()}


def _protected_slots(inst: Instance) -> frozenset[int]:
    out: set[int] = set()
    for constraint in inst.constraints:
        if isinstance(constraint, NursingProtectedWindow):
            out |= constraint.slots
    return frozenset(out)


def acuity_of(inst: Instance) -> dict[PatientId, Acuity]:
    """Acuity per patient, taken from an `AcuityOrdering` constraint if one is
    present and from `Patient.acuity` otherwise.

    The constraint wins when both exist: the constraint is what
    `Schedule.validate` will be checked against, and a solver optimising
    against a different ordering than the validator enforces is the exact shape
    of a bug that only shows up in production.

    Public (not `_acuity`): `hwpm.optimize.baselines`' candidate construction
    needs the same reading of "acuity" the CP-SAT encoding uses, and a second,
    private copy of this logic would be the two-statements-of-one-fact problem
    this module's docstring warns about elsewhere.
    """
    for constraint in inst.constraints:
        if isinstance(constraint, AcuityOrdering):
            return dict(constraint.acuity)
    return {p.id: p.acuity for p in inst.patients}


def isolated_of(inst: Instance) -> dict[PatientId, bool]:
    """Public for the same reason as `acuity_of`: shared with
    `hwpm.optimize.baselines`."""
    for constraint in inst.constraints:
        if isinstance(constraint, IsolationLast):
            return {
                p.id: constraint.isolation.get(p.id, IsolationStatus.NONE)
                is not IsolationStatus.NONE
                for p in inst.patients
            }
    return {p.id: p.isolation_status is not IsolationStatus.NONE for p in inst.patients}


def ordering_active(inst: Instance) -> tuple[bool, bool]:
    """Public for the same reason as `acuity_of`: shared with
    `hwpm.optimize.baselines`."""
    return (
        any(isinstance(c, AcuityOrdering) for c in inst.constraints),
        any(isinstance(c, IsolationLast) for c in inst.constraints),
    )


def candidate_clinicians(inst: Instance) -> dict[PatientId, tuple[ClinicianId, ...]]:
    """Clinicians who could usefully visit each patient: those holding at least
    one of the patient's required specialties.

    A visit that satisfies no requirement is not modelled at all. It could only
    ever add motion and makespan, and permitting it would let the solver spend
    search effort on visits no objective rewards.
    """
    specialties = inst.specialties
    out: dict[PatientId, tuple[ClinicianId, ...]] = {}
    for patient in inst.patients:
        required = inst.required.get(patient.id, frozenset())
        out[patient.id] = tuple(
            c.id for c in inst.clinicians if specialties[c.id] & required
        )
    return out


def allowed_starts(inst: Instance) -> dict[tuple[str, str], tuple[int, ...]]:
    """Start slots at which clinician `c` may begin a visit to patient `p`,
    keyed by `(clinician value, patient value)`.

    Folds `ClinicianAvailability` and `PatientUnavailable` together: both are
    hard, both are expressed as sets of slots, and a visit occupies every slot
    from its start to its end, so the legal starts are those whose whole
    footprint clears both.
    """
    duration = inst.visit_slots
    horizon = inst.slots.n_slots
    availability = _availability(inst)
    blocked = _patient_blocked(inst)
    candidates = candidate_clinicians(inst)
    out: dict[tuple[str, str], tuple[int, ...]] = {}
    for patient in inst.patients:
        off_ward = blocked.get(patient.id, frozenset())
        for clinician in candidates[patient.id]:
            available = availability.get(clinician)
            starts = tuple(
                t
                for t in range(0, horizon - duration + 1)
                if all(
                    (available is None or t + k in available) and t + k not in off_ward
                    for k in range(duration)
                )
            )
            out[(clinician.value, patient.id.value)] = starts
    return out


# ---------------------------------------------------------------------------
# The encoding
# ---------------------------------------------------------------------------


@dataclass
class _Encoding:
    """A built CP-SAT model plus the handles needed to constrain and read it.

    Mutable, unlike almost everything else in this codebase: a `CpModel` is a
    builder and pretending otherwise by freezing the wrapper would be dishonest
    about what the object is.
    """

    model: cp_model.CpModel
    inst: Instance
    presence: dict[tuple[str, str], cp_model.IntVar]
    start: dict[tuple[str, str], cp_model.IntVar]
    native: dict[str, object]  # objective key -> LinearExpr / IntVar
    n_multi: int
    n_previous: int


def build_model(inst: Instance) -> _Encoding:
    """Encode `inst` as a CP-SAT model with all five objectives available as
    linear expressions and none of them selected.

    Selecting one is `solve_single`'s job. Keeping the objective out of the
    build is what makes the epsilon sweep possible without re-encoding: the
    same structural model is re-used with a different objective and different
    bounds each time. (The model is rebuilt per solve anyway — `CpModel` has no
    supported way to retract an objective — but the *code path* is shared, so
    the encoding cannot drift between the payoff table and the sweep.)
    """
    model = cp_model.CpModel()
    duration = inst.visit_slots
    horizon = inst.slots.n_slots
    specialties = inst.specialties
    candidates = candidate_clinicians(inst)
    starts = allowed_starts(inst)
    acuity = acuity_of(inst)
    isolated = isolated_of(inst)
    acuity_active, isolation_active = ordering_active(inst)
    protected = _protected_slots(inst)

    presence: dict[tuple[str, str], cp_model.IntVar] = {}
    start: dict[tuple[str, str], cp_model.IntVar] = {}

    # --- visit variables -------------------------------------------------
    for patient in inst.patients:
        for clinician in candidates[patient.id]:
            key = (clinician.value, patient.id.value)
            legal = starts[key]
            y = model.NewBoolVar(f"y[{key[0]},{key[1]}]")
            presence[key] = y
            if not legal:
                # No slot clears both availability sets. The visit is not
                # merely unattractive, it is impossible.
                model.Add(y == 0)
                start[key] = model.NewConstantVar(0)
                continue
            t = model.NewIntVarFromDomain(
                cp_model.Domain.FromValues(list(legal)), f"t[{key[0]},{key[1]}]"
            )
            start[key] = t

    # --- coverage: exactly one clinician per (patient, required specialty)
    #
    # `== 1` rather than `>= 1`. A second cardiologist at the same bedside
    # satisfies no additional requirement and would show up as pure motion; and
    # a clinician holding two of a patient's required specialties satisfies
    # both rows with one visit, which is exactly the MDT efficiency the project
    # is looking for and is why the rows are per-specialty rather than per-visit.
    for patient in inst.patients:
        required = inst.required.get(patient.id, frozenset())
        for specialty in sorted(required, key=lambda s: s.value):
            covering = [
                presence[(c.value, patient.id.value)]
                for c in candidates[patient.id]
                if specialty in specialties[c]
            ]
            if not covering:
                raise InfeasibleInstanceError(
                    f"patient {patient.id.value} requires {specialty.value} "
                    f"and no rostered clinician holds it"
                )
            model.AddExactlyOne(covering)

    # --- per-clinician route: AddCircuit over {depot} u candidate patients
    #
    # A circuit rather than a NoOverlap plus pairwise ordering booleans,
    # because routing is what this half of the problem *is*: the same arc
    # literals carry the travel-time propagation, the motion objective, and the
    # two ordering constraints, and a formulation where those three agree by
    # construction cannot drift out of step.
    motion_terms: list[tuple[int, cp_model.IntVar]] = []
    for clinician in inst.clinicians:
        served = [
            p.id for p in inst.patients if (clinician.id.value, p.id.value) in presence
        ]
        if not served:
            continue
        arcs: list[tuple[int, int, cp_model.IntVar]] = []
        visits_any = model.NewBoolVar(f"any[{clinician.id.value}]")
        model.AddMaxEquality(
            visits_any, [presence[(clinician.id.value, p.value)] for p in served]
        )
        # Node 0 is the depot. Its self-loop closes the degenerate circuit in
        # which this clinician visits nobody.
        arcs.append((0, 0, visits_any.Not()))
        for i, patient in enumerate(served, start=1):
            y = presence[(clinician.id.value, patient.value)]
            arcs.append((i, i, y.Not()))
            arcs.append((0, i, model.NewBoolVar(f"in[{clinician.id.value},{i}]")))
            arcs.append((i, 0, model.NewBoolVar(f"out[{clinician.id.value},{i}]")))
        for i, earlier in enumerate(served, start=1):
            for j, later in enumerate(served, start=1):
                if i == j:
                    continue
                # Ordering constraints are enforced by *not creating the arc*.
                # Acuity: sickest first, so a route may never step up in acuity.
                if acuity_active and acuity.get(later, Acuity.LOW) > acuity.get(
                    earlier, Acuity.LOW
                ):
                    continue
                # Isolation: nothing follows an isolation patient except
                # another isolation patient.
                #
                # These two together are stricter than either alone, and can be
                # jointly unsatisfiable for a *pair* of patients: an isolated
                # HIGH-acuity patient and a non-isolated LOW-acuity one admit
                # no arc in either direction, so no single clinician can see
                # both. That is a faithful reading of the two `Constraint`
                # classes as written, not a modelling shortcut, and the effect
                # is that the solver spreads such pairs across clinicians.
                if (
                    isolation_active
                    and isolated.get(earlier, False)
                    and not isolated.get(later, False)
                ):
                    continue
                lit = model.NewBoolVar(f"arc[{clinician.id.value},{i},{j}]")
                arcs.append((i, j, lit))
                need = travel_slots(inst, earlier, later)
                model.Add(
                    start[(clinician.id.value, later.value)]
                    >= start[(clinician.id.value, earlier.value)] + duration + need
                ).OnlyEnforceIf(lit)
                metres_cm = _motion_centimetres(inst, earlier, later)
                if metres_cm:
                    motion_terms.append((metres_cm, lit))
        model.AddCircuit(arcs)

    # --- objective 1: MDT co-presence ------------------------------------
    #
    # `mdt[p]` is a *lower bound* on whether patient p got an MDT moment: the
    # overlap literals below are implied one way only (ov => the two visits
    # really do overlap), never forced true. That is sound because co-presence
    # is only ever maximised or lower-bounded here, so the solver has every
    # incentive to set them and the optimum is tight; and the schedule that
    # comes back is re-scored by `evaluate` regardless. The reverse implication
    # would double the constraint count for nothing.
    multi = inst.multi_specialty_patients()
    mdt_terms: list[cp_model.IntVar] = []
    for patient in multi:
        required = inst.required.get(patient, frozenset())
        terms: list[cp_model.IntVar] = []
        served = [c for c in candidates[patient]]
        for clinician in served:
            # One clinician holding two of the required specialties is an MDT
            # moment on their own -- `Schedule.specialties_at_bedside` unions
            # over all visits occupying an instant, including a single visit.
            if len(specialties[clinician] & required) >= 2:
                terms.append(presence[(clinician.value, patient.value)])
        for a_index, first in enumerate(served):
            for second in served[a_index + 1 :]:
                joint = (specialties[first] | specialties[second]) & required
                if len(joint) < 2:
                    continue
                ka = (first.value, patient.value)
                kb = (second.value, patient.value)
                ov = model.NewBoolVar(f"ov[{patient.value},{first.value},{second.value}]")
                model.AddImplication(ov, presence[ka])
                model.AddImplication(ov, presence[kb])
                model.Add(start[ka] < start[kb] + duration).OnlyEnforceIf(ov)
                model.Add(start[kb] < start[ka] + duration).OnlyEnforceIf(ov)
                terms.append(ov)
        if not terms:
            continue
        got = model.NewBoolVar(f"mdt[{patient.value}]")
        model.AddMaxEquality(got, terms)
        mdt_terms.append(got)
    copresence = sum(mdt_terms) if mdt_terms else 0

    # --- objective 3: nursing disruption ---------------------------------
    disruption_terms: list[cp_model.IntVar] = []
    if protected:
        for key, legal in starts.items():
            if not legal:
                continue
            bad = [t for t in legal if any(t + k in protected for k in range(duration))]
            good = [t for t in legal if t not in set(bad)]
            if not bad:
                continue
            d = model.NewBoolVar(f"disr[{key[0]},{key[1]}]")
            model.AddImplication(d, presence[key])
            model.AddAllowedAssignments([start[key]], [(t,) for t in bad]).OnlyEnforceIf(
                d
            )
            if good:
                model.AddAllowedAssignments(
                    [start[key]], [(t,) for t in good]
                ).OnlyEnforceIf([presence[key], d.Not()])
            else:
                # Every legal start collides; the visit cannot happen without
                # disrupting, so presence implies disruption.
                model.AddImplication(presence[key], d)
            disruption_terms.append(d)
    disruption = sum(disruption_terms) if disruption_terms else 0

    # --- objective 4: continuity of care ---------------------------------
    continuity_terms: list[cp_model.IntVar] = []
    for patient_id, previous in sorted(
        inst.previous_day.items(), key=lambda kv: kv[0].value
    ):
        key = (previous.value, patient_id.value)
        if key in presence:
            continuity_terms.append(presence[key])
    continuity = sum(continuity_terms) if continuity_terms else 0

    # --- objective 5: makespan -------------------------------------------
    makespan = model.NewIntVar(0, horizon, "makespan")
    for key, y in presence.items():
        model.Add(makespan >= start[key] + duration).OnlyEnforceIf(y)

    motion = sum(cost * lit for cost, lit in motion_terms) if motion_terms else 0

    return _Encoding(
        model=model,
        inst=inst,
        presence=presence,
        start=start,
        native={
            "copresence": copresence,
            "motion_m": motion,
            "disruption": disruption,
            "continuity": continuity,
            "makespan_s": makespan,
        },
        n_multi=len(multi),
        n_previous=len(inst.previous_day),
    )


def quantise(objectives: Objectives) -> Objectives:
    """Round motion to the centimetre before the front is built.

    Not cosmetic. `_motion_metres` sums routed float metres in whatever order a
    schedule's routes happen to fall, so two schedules that walk the same
    corridors in a different order differ by ~1e-14 m — and `dominates`
    compares floats exactly, so that dust reads as a genuine trade-off. On a
    12-bed instance this was observed promoting a schedule with 25% co-presence
    into the front purely because it was three femtometres shorter than a
    schedule that beat it on every objective a human cares about.

    The centimetre is the right place to round because it is the resolution the
    model optimises at anyway (motion is encoded as integer centimetres), so
    this discards no precision the solver ever had. It is applied on the way
    *out* of the exact path rather than inside `evaluate`, which is shared with
    the metaheuristics and whose contract is not this module's to change.
    """
    return Objectives(
        copresence=objectives.copresence,
        motion_m=round(objectives.motion_m, 2),
        disruption=objectives.disruption,
        continuity=objectives.continuity,
        makespan_s=objectives.makespan_s,
    )


def _extract(encoding: _Encoding, solver: cp_model.CpSolver) -> Schedule:
    duration = encoding.inst.visit_slots
    visits: list[PlannedVisit] = []
    for (clinician, patient), y in sorted(encoding.presence.items()):
        if solver.Value(y):
            visits.append(
                PlannedVisit(
                    clinician=ClinicianId(clinician),
                    patient=PatientId(patient),
                    start=int(solver.Value(encoding.start[(clinician, patient)])),
                    duration=duration,
                )
            )
    visits.sort(key=lambda v: (v.start, v.clinician.value, v.patient.value))
    return Schedule(visits=tuple(visits))


# ---------------------------------------------------------------------------
# Solving
# ---------------------------------------------------------------------------


def solve_single(
    inst: Instance,
    objective: str,
    *,
    seconds: float,
    seed: int,
    workers: int = 1,
    epsilon: Mapping[str, int] | None = None,
) -> SolveOutcome:
    """Optimise one objective, optionally subject to epsilon bounds on others.

    `epsilon` maps objective keys to bounds **in native integer units** (see
    the table at the top of this module): a bound on a minimised objective is
    an upper bound, on a maximised one a lower bound. Native units rather than
    `Objectives`' floats because CP-SAT constrains integers, and rounding a
    fraction into a bound at the call site is how an epsilon sweep quietly
    starts excluding feasible points.
    """
    if objective not in OBJECTIVE_KEYS:
        raise ValueError(
            f"unknown objective {objective!r}; expected one of {OBJECTIVE_KEYS}"
        )
    encoding = build_model(inst)
    model = encoding.model
    for key, bound in sorted((epsilon or {}).items()):
        if key == objective:
            continue
        expression = encoding.native[key]
        if key in _MAXIMISE:
            model.Add(expression >= bound)
        else:
            model.Add(expression <= bound)
    target = encoding.native[objective]
    if objective in _MAXIMISE:
        model.Maximize(target)
    else:
        model.Minimize(target)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(seconds)
    solver.parameters.num_workers = int(workers)
    solver.parameters.random_seed = int(seed)
    started = time.perf_counter()
    status = solver.Solve(model)
    elapsed = time.perf_counter() - started
    name = solver.StatusName(status)

    schedule: Schedule | None = None
    objectives: Objectives | None = None
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = _extract(encoding, solver)
        objectives = quantise(evaluate(schedule, inst))
    return SolveOutcome(
        objective=objective,
        schedule=schedule,
        objectives=objectives,
        status=name,
        proven_optimal=status == cp_model.OPTIMAL,
        wall_seconds=elapsed,
        epsilon=tuple(sorted((epsilon or {}).items())),
    )


def _native_values(
    inst: Instance, _schedule: Schedule, objectives: Objectives
) -> dict[str, int]:
    """Read a solved schedule back into the solver's native integer units, so
    the payoff table and the epsilon grid speak one language.

    Derived from `Objectives` (i.e. from `evaluate`), not from the solver's own
    expressions, so that a bound in the sweep is a bound on the number the
    project actually reports.
    """
    n_multi = len(inst.multi_specialty_patients())
    n_previous = len(inst.previous_day)
    return {
        "copresence": round(objectives.copresence * n_multi) if n_multi else 0,
        "motion_m": round(objectives.motion_m * 100),
        "disruption": int(objectives.disruption),
        "continuity": round(objectives.continuity * n_previous) if n_previous else 0,
        "makespan_s": objectives.makespan_s // inst.slots.slot_seconds,
    }


def payoff_table(
    inst: Instance, *, seconds: float, seed: int, workers: int = 1
) -> tuple[dict[str, tuple[int, int]], tuple[SolveOutcome, ...]]:
    """Optimise each objective alone; return per-objective `(ideal, nadir)` in
    native units, plus the five solves.

    The nadir here is the standard payoff-table *estimate* — the worst value
    each objective takes across the five individual optima — not the true
    nadir, which is NP-hard to compute for more than two objectives. The grid
    laid over it can therefore miss parts of the true front. Said once here and
    once in the module docstring because it is the single most-overclaimed step
    in epsilon-constraint methods.
    """
    outcomes: list[SolveOutcome] = []
    columns: dict[str, list[int]] = {k: [] for k in OBJECTIVE_KEYS}
    for key in OBJECTIVE_KEYS:
        outcome = solve_single(inst, key, seconds=seconds, seed=seed, workers=workers)
        outcomes.append(outcome)
        if outcome.schedule is None or outcome.objectives is None:
            continue
        native = _native_values(inst, outcome.schedule, outcome.objectives)
        for name, value in native.items():
            columns[name].append(value)
    ranges: dict[str, tuple[int, int]] = {}
    for key, values in columns.items():
        if not values:
            continue
        if key in _MAXIMISE:
            ranges[key] = (max(values), min(values))  # (ideal, nadir)
        else:
            ranges[key] = (min(values), max(values))
    return ranges, tuple(outcomes)


def _grid_levels(ideal: int, nadir: int, grid: int) -> tuple[int, ...]:
    """`grid` evenly spaced integer bounds spanning ideal..nadir inclusive.

    Deduplicated and sorted so a degenerate range (ideal == nadir, i.e. the
    objective does not vary across the payoff table) contributes one level
    rather than `grid` identical solves.
    """
    if grid <= 1 or ideal == nadir:
        return (nadir,)
    step = (nadir - ideal) / (grid - 1)
    return tuple(sorted({round(ideal + step * i) for i in range(grid)}))


@dataclass(frozen=True)
class CpSatScheduler:
    """SPEC-004's `Scheduler`, solved exactly. Returns a Pareto set, never one
    schedule (ADR-0004).

    `primary` defaults to motion, the objective with the widest continuous
    range and therefore the most informative one to optimise against a grid of
    bounds; the other four take integer values over small ranges and make
    better epsilon axes than optimisation targets.
    """

    grid: int = 2
    primary: str = "motion_m"
    workers: int = 1

    def __post_init__(self) -> None:
        if self.grid < 1:
            raise ValueError(f"grid must be >= 1, got {self.grid!r}")
        if self.primary not in OBJECTIVE_KEYS:
            raise ValueError(
                f"unknown primary objective {self.primary!r}; "
                f"expected one of {OBJECTIVE_KEYS}"
            )
        if self.workers < 1:
            raise ValueError(f"workers must be >= 1, got {self.workers!r}")

    def solve(
        self, inst: Instance, budget: Budget, rng: Random
    ) -> list[tuple[Schedule, Objectives]]:
        return list(self.solve_detailed(inst, budget, rng).front)

    def solve_detailed(self, inst: Instance, budget: Budget, rng: Random) -> FrontResult:
        """Full epsilon-constraint sweep.

        `rng` seeds CP-SAT rather than driving any sampling of our own: the
        search is exact and the only randomness is the solver's internal
        tie-breaking. It is still threaded explicitly because a hidden seed is
        exactly what makes a benchmark irreproducible, and this benchmark is
        the input to the Rule 0 decision.
        """
        seed = rng.randrange(2**31 - 1)
        others = tuple(k for k in OBJECTIVE_KEYS if k != self.primary)
        planned = 5 + self.grid ** len(others)
        per_solve = max(1.0, budget.max_seconds / planned)
        started = time.perf_counter()

        ranges, outcomes = payoff_table(
            inst, seconds=per_solve, seed=seed, workers=self.workers
        )
        collected: list[tuple[Schedule, Objectives]] = [
            (o.schedule, o.objectives)
            for o in outcomes
            if o.schedule is not None and o.objectives is not None
        ]
        all_outcomes = list(outcomes)

        if ranges:
            axes = [
                _grid_levels(*ranges[key], self.grid) for key in others if key in ranges
            ]
            axis_keys = [key for key in others if key in ranges]
            for combination in product(*axes):
                if time.perf_counter() - started > budget.max_seconds:
                    break
                epsilon = dict(zip(axis_keys, combination, strict=True))
                outcome = solve_single(
                    inst,
                    self.primary,
                    seconds=per_solve,
                    seed=seed,
                    workers=self.workers,
                    epsilon=epsilon,
                )
                all_outcomes.append(outcome)
                if outcome.schedule is not None and outcome.objectives is not None:
                    collected.append((outcome.schedule, outcome.objectives))

        elapsed = time.perf_counter() - started
        return FrontResult(
            front=tuple(pareto_front(collected)),
            outcomes=tuple(all_outcomes),
            wall_seconds=elapsed,
            seed=seed,
            primary=self.primary,
            grid=self.grid,
            workers=self.workers,
        )
