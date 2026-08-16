"""Ant Colony System visit routing. SPEC-004, node N11.

`SELECTION-GUIDE.md` P2 selects ACS (Alg 112, p.159, an elitist refinement of
the abstract ACO of Alg 109, p.154) specifically for **visit routing**:
"Given one clinician's patient set, order the visits to minimise distance
travelled across the ward geometry ... a TSP over bed coordinates with a few
precedence constraints (isolation rooms last, critically ill first)." That is
narrower than the full SPEC-004 `Scheduler` contract, which also decides
*coverage* (who sees whom) and *timing* (when). This module supplies all
three, but only one of them is what ACS itself is doing:

- **Coverage** -- the same random `{patient -> {specialty -> clinician}}` draw
  `hwpm.optimize.baselines` uses. Deciding coverage well is N10's problem
  (NSGA-II); P2's problem statement starts from a patient set already handed
  to a clinician, so re-optimising coverage here would make N11 quietly
  re-decide something N10 owns.
- **Routing** -- ACS proper, described below.
- **Timing** -- the earliest slot that clears availability/off-ward
  constraints and leaves room to walk from the previous visit. Once an order
  is fixed there is no motion left to trade off against timing, so drawing a
  random start (as `baselines._assign_starts` does, to explore objectives 3
  and 5 too) would only add noise to the one thing ACS is supposed to be
  measured on.

**Precedence as route segmentation, not a filter on ties.** `AcuityOrdering`
and `IsolationLast` do not merely forbid certain edges; together they induce a
fixed sequence of *classes* a clinician's patients fall into -- isolation
group, then acuity level within it -- that must be visited in class order
(`cpsat.build_model`'s note that the two constraints can be jointly
unsatisfiable for a single pair applies here unchanged: `_route_classes`
returns `None` and the candidate is discarded exactly when that pair exists).
Within one class, however, any order is legal, and that free choice is the
actual TSP instance ACS is being asked to solve. A clinician with two patients
tied on acuity and isolation has a real routing decision;
`baselines._order_route` breaks such a tie by patient id -- the arbitrary
choice ACS's pheromone-guided construction exists to replace with a
distance-aware one.

**Components are directed edges between beds** (including a depot edge for
the first visit of each route), and **pheromones are one table shared across
every clinician and every generation** -- not per-clinician tables. This is
not a simplification made for convenience: `SELECTION-GUIDE.md`'s stated
reason for choosing ACO over a GA for this slot is that "the pheromone matrix
is directly interpretable ... a heat map over bed-to-bed transitions saying
'this movement is habitually worth making'. That is a plot you can put in
front of a clinical director." A plot like that is only one artefact, of the
whole ward's geometry, if there is one shared table to draw it from.

**Fitness is motion alone, not a scalarisation.** ADR-0004 forbids collapsing
the five SPEC-004 objectives into one weighted sum. ACS's own algorithm (Alg
112 lines 24-26 and 29-31) needs *some* scalar fitness to rank trails and
drive pheromone deposits, so the question is not whether to have one but which
single-objective proxy is honest here: `motion_m`, the one objective
`SELECTION-GUIDE.md` P2 assigns to routing, used alone rather than blended
with the other four. This is the same move `cpsat.solve_single` makes in the
epsilon-constraint sweep -- optimise one named objective at a time rather than
a weighted combination -- not an unrelated shortcut. The `Scheduler.solve`
contract is still honoured in full: every schedule this module ever evaluates
is kept, and the return value is `pareto_front(everything)` across all five
objectives (ADR-0004). ACS's internal exploitation direction is
single-objective; the reported result never is.

**Elitist Component Selection** (p.156): with probability `q0` pick the
candidate edge of highest desirability outright; otherwise pick by
desirability-proportionate roulette. **Desirability** combines pheromone and a
distance heuristic, `pheromone**alpha * (1 / (1 + metres))**beta`, with `beta`
defaulting to 1.0 per the reference's "usually beta = 1" (ACS "gets rid of
beta" relative to the Ant System's more general form). **Local decay** (p.156,
"Elitism"): every edge used in *any* trail this generation moves toward
`tau0` by rate `xi`. **Elitist global update** (Alg 112 lines 29-31): every
edge used in the generation's best-by-motion trail is reinforced toward
`1 / (1 + motion_m)` by rate `rho`.

**Determinism.** All draws -- coverage, and every roulette tie-break in
component selection -- go through the caller's `rng: Random`
(`04-AGENT-ORCHESTRATION.md`'s stochastic-component rule); no module-level
`random`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from itertools import pairwise
from random import Random

from hwpm.domain.model import Acuity, ClinicianId, PatientId, Specialty
from hwpm.domain.schedule import PlannedVisit, Schedule
from hwpm.optimize.baselines import Assignment, _random_assignment, _specialty_holders
from hwpm.optimize.cpsat import (
    InfeasibleInstanceError,
    acuity_of,
    allowed_starts,
    isolated_of,
    ordering_active,
    quantise,
    respects_travel_time,
    travel_slots,
)
from hwpm.optimize.evaluate import evaluate, pareto_front
from hwpm.optimize.types import Budget, Instance, Objectives

#: A routing component: a directed move from `frm` to `to`. `frm is None`
#: means "depot", i.e. this is the first visit of a clinician's route -- ACS's
#: components are edges, and the start of a route is as much a selected edge
#: as any other (Alg 112 line 17 draws no exception for it).
Edge = tuple[PatientId | None, PatientId]


def _check_feasible(
    inst: Instance, holders: dict[Specialty, tuple[ClinicianId, ...]]
) -> None:
    """Same up-front check as `baselines._check_feasible`, kept local rather
    than imported: it is small, and importing a second module's private name
    would tie this module's error message to that module's internals rather
    than to `Instance` itself."""
    for patient in inst.patients:
        for specialty in inst.required.get(patient.id, frozenset()):
            if not holders.get(specialty):
                raise InfeasibleInstanceError(
                    f"patient {patient.id.value} requires {specialty.value} "
                    f"and no rostered clinician holds it"
                )


def _within_budget(started: float, evaluations: int, budget: Budget) -> bool:
    if time.perf_counter() - started >= budget.max_seconds:
        return False
    return budget.max_evaluations is None or evaluations < budget.max_evaluations


def _route_classes(
    patients: tuple[PatientId, ...],
    acuity: dict[PatientId, Acuity],
    isolated: dict[PatientId, bool],
    acuity_active: bool,
    isolation_active: bool,
) -> list[tuple[PatientId, ...]] | None:
    """The ordered sequence of classes one clinician's route must pass through
    -- non-isolated patients by descending acuity level, then isolated
    patients by descending acuity level -- each class returned as its own
    group so the caller can order *within* it freely. `None` if the class
    sequence itself violates `AcuityOrdering` at the isolation boundary: an
    isolated HIGH-acuity patient and a non-isolated LOW-acuity patient cannot
    both be assigned to one clinician under both constraints at once
    (`cpsat.build_model`'s note on jointly infeasible pairs), and this check
    is the same one `baselines._order_route` makes on the flattened sequence,
    just made before the within-class freedom is used rather than after."""
    if isolation_active:
        buckets = (
            [p for p in patients if not isolated.get(p, False)],
            [p for p in patients if isolated.get(p, False)],
        )
    else:
        buckets = (list(patients), [])

    groups: list[tuple[PatientId, ...]] = []
    for bucket in buckets:
        if not bucket:
            continue
        if acuity_active:
            by_level: dict[Acuity, list[PatientId]] = {}
            for patient in bucket:
                by_level.setdefault(acuity.get(patient, Acuity.LOW), []).append(patient)
            for level in sorted(by_level, reverse=True):
                groups.append(tuple(by_level[level]))
        else:
            groups.append(tuple(bucket))

    flat = [p for group in groups for p in group]
    for earlier, later in pairwise(flat):
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
    return groups


def _desirability(
    inst: Instance,
    pheromone: dict[Edge, float],
    tau0: float,
    alpha: float,
    beta: float,
    frm: PatientId | None,
    to: PatientId,
) -> float:
    tau = pheromone.get((frm, to), tau0)
    metres = 0.0
    if frm is not None:
        a = inst.beds.get(frm)
        b = inst.beds.get(to)
        if a is not None and b is not None:
            metres = inst.graph.cost(a, b).metres
    heuristic = 1.0 / (1.0 + metres)
    return (tau**alpha) * (heuristic**beta)


def _construct_route(
    inst: Instance,
    groups: list[tuple[PatientId, ...]],
    pheromone: dict[Edge, float],
    rng: Random,
    tau0: float,
    alpha: float,
    beta: float,
    q0: float,
) -> tuple[tuple[PatientId, ...], set[Edge]]:
    """Alg 112 lines 16-22 (component-by-component trail construction) plus
    the elitist selection rule (p.156), restricted at each step to the
    patients remaining in the current class -- see the module docstring on
    precedence as route segmentation."""
    ordered: list[PatientId] = []
    edges: set[Edge] = set()
    current: PatientId | None = None
    for group in groups:
        remaining = list(group)
        while remaining:
            scored = [
                (_desirability(inst, pheromone, tau0, alpha, beta, current, p), p)
                for p in remaining
            ]
            if rng.random() < q0:
                chosen = max(scored, key=lambda pair: pair[0])[1]
            else:
                total = sum(d for d, _ in scored)
                if total <= 0.0:
                    chosen = rng.choice(remaining)
                else:
                    threshold = rng.random() * total
                    cumulative = 0.0
                    chosen = scored[-1][1]
                    for desirability, patient in scored:
                        cumulative += desirability
                        if cumulative >= threshold:
                            chosen = patient
                            break
            edges.add((current, chosen))
            ordered.append(chosen)
            remaining.remove(chosen)
            current = chosen
    return tuple(ordered), edges


def _assign_starts_greedy(
    inst: Instance,
    clinician: ClinicianId,
    ordered_patients: tuple[PatientId, ...],
    starts_table: dict[tuple[str, str], tuple[int, ...]],
) -> list[PlannedVisit] | None:
    """The earliest legal start for each visit in route order -- see the
    module docstring on why timing is greedy rather than randomised here."""
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
        start = min(feasible)
        visits.append(
            PlannedVisit(
                clinician=clinician, patient=patient, start=start, duration=duration
            )
        )
        prev_end = start + duration
        prev_patient = patient
    return visits


def _construct(
    inst: Instance,
    assignment: Assignment,
    pheromone: dict[Edge, float],
    rng: Random,
    tau0: float,
    alpha: float,
    beta: float,
    q0: float,
) -> tuple[Schedule, set[Edge]] | None:
    """Build one complete trail (a whole schedule, every clinician routed) for
    a fixed coverage `assignment`, or `None` if any clinician's precedence
    classes or timing are infeasible. Every hard constraint is re-checked on
    the way out, matching `baselines._materialize`'s discipline: a bug in
    construction fails a discard, never returns an invalid schedule."""
    per_clinician: dict[ClinicianId, list[PatientId]] = {}
    for patient, cov in assignment.items():
        for clinician in set(cov.values()):
            per_clinician.setdefault(clinician, []).append(patient)

    acuity = acuity_of(inst)
    isolated = isolated_of(inst)
    acuity_active, isolation_active = ordering_active(inst)
    starts_table = allowed_starts(inst)

    visits: list[PlannedVisit] = []
    edges_used: set[Edge] = set()
    for clinician in sorted(per_clinician, key=lambda c: c.value):
        patients = tuple(sorted(per_clinician[clinician], key=lambda p: p.value))
        groups = _route_classes(
            patients, acuity, isolated, acuity_active, isolation_active
        )
        if groups is None:
            return None
        ordered, edges = _construct_route(
            inst, groups, pheromone, rng, tau0, alpha, beta, q0
        )
        clinician_visits = _assign_starts_greedy(inst, clinician, ordered, starts_table)
        if clinician_visits is None:
            return None
        visits.extend(clinician_visits)
        edges_used |= edges

    schedule = Schedule(visits=tuple(visits))
    if schedule.hard_violations(inst.constraints):
        return None
    if respects_travel_time(schedule, inst):
        return None
    return schedule, edges_used


@dataclass(frozen=True)
class AcsRoutingScheduler:
    """SPEC-004's `Scheduler`, routed by Ant Colony System (Alg 112, p.159).
    See the module docstring for what ACS does and does not decide here, and
    for the meaning of each parameter below.

    `popsize` is Alg 112's line 2 (trails built per generation); `alpha`/`beta`
    weight pheromone against the distance heuristic in component selection;
    `q0` is the elitist-selection coin (line 9); `rho` is the elitist learning
    rate (line 3) applied to the generation's best-by-motion trail; `xi` is
    the evaporation/decay rate (p.156, "Elitism") applied to every edge used
    in any trail this generation; `tau0` is the initial/floor pheromone value
    (line 5).
    """

    popsize: int = 10
    alpha: float = 1.0
    beta: float = 1.0
    q0: float = 0.9
    rho: float = 0.1
    xi: float = 0.1
    tau0: float = 1.0

    def __post_init__(self) -> None:
        if self.popsize < 1:
            raise ValueError(f"popsize must be >= 1, got {self.popsize!r}")
        if self.alpha < 0:
            raise ValueError(f"alpha must be >= 0, got {self.alpha!r}")
        if self.beta < 0:
            raise ValueError(f"beta must be >= 0, got {self.beta!r}")
        if not 0.0 <= self.q0 <= 1.0:
            raise ValueError(f"q0 must be in [0, 1], got {self.q0!r}")
        if not 0.0 < self.rho <= 1.0:
            raise ValueError(f"rho must be in (0, 1], got {self.rho!r}")
        if not 0.0 < self.xi <= 1.0:
            raise ValueError(f"xi must be in (0, 1], got {self.xi!r}")
        if self.tau0 <= 0:
            raise ValueError(f"tau0 must be > 0, got {self.tau0!r}")

    def solve(
        self, inst: Instance, budget: Budget, rng: Random
    ) -> list[tuple[Schedule, Objectives]]:
        holders = _specialty_holders(inst)
        _check_feasible(inst, holders)
        started = time.perf_counter()
        evaluations = 0
        scored: list[tuple[Schedule, Objectives]] = []
        pheromone: dict[Edge, float] = {}
        best_motion: float | None = None
        best_edges: frozenset[Edge] = frozenset()

        while _within_budget(started, evaluations, budget):
            generation: list[tuple[frozenset[Edge], Objectives]] = []
            for _ in range(self.popsize):
                if not _within_budget(started, evaluations, budget):
                    break
                assignment = _random_assignment(inst, holders, rng)
                built = _construct(
                    inst,
                    assignment,
                    pheromone,
                    rng,
                    self.tau0,
                    self.alpha,
                    self.beta,
                    self.q0,
                )
                if built is None:
                    continue
                schedule, trail_edges = built
                evaluations += 1
                objectives = quantise(evaluate(schedule, inst))
                scored.append((schedule, objectives))
                generation.append((frozenset(trail_edges), objectives))

            if not generation:
                continue

            # Local decay (p.156): every edge used by any trail this
            # generation relaxes toward tau0.
            used = frozenset[Edge]().union(*(e for e, _ in generation))
            for edge in used:
                pheromone[edge] = (1 - self.xi) * pheromone.get(
                    edge, self.tau0
                ) + self.xi * self.tau0

            # Track the generation's best trail by motion alone (see module
            # docstring: this is ACS's internal single-objective fitness, not
            # the multi-objective result the scheduler returns).
            for candidate_edges, objectives in generation:
                if best_motion is None or objectives.motion_m < best_motion:
                    best_motion = objectives.motion_m
                    best_edges = candidate_edges

            # Elitist global update (Alg 112 lines 29-31): reinforce only the
            # best-so-far trail's edges.
            if best_edges and best_motion is not None:
                fitness = 1.0 / (1.0 + best_motion)
                for edge in best_edges:
                    pheromone[edge] = (1 - self.rho) * pheromone.get(
                        edge, self.tau0
                    ) + self.rho * fitness

        return pareto_front(scored)
