"""NSGA-II multi-objective MDT scheduler. SPEC-004, node N10.

`SELECTION-GUIDE.md` P1 selects **NSGA-II (Alg 104, p.143)** for the core
problem, with its three supporting pieces: **Pareto domination (Alg 98,
p.139)**, **front rank assignment by non-dominated sorting (Alg 101, p.141)**
and **multiobjective sparsity / crowding distance (Alg 102, p.142)**, selected
through **non-dominated sorting lexicographic tournament selection with
sparsity (Alg 103, p.142)**. Domination and front extraction already exist in
`hwpm.optimize.evaluate` (built at N08) and are reused rather than restated;
ranking, sparsity, selection and the archive loop are this module.

**Why this node exists again.** N08 proved the single 30-bed ward exactly and
demoted this node to a cross-check. N21 then measured hospital scale and found
that beds and roster growing *together* stop CP-SAT proving anything at all —
36 beds with 16 clinicians sits 92.3% from its bound after 900s, and the
fixed-8 roster is provably infeasible from 48 beds. Rule 0's step 1 is
therefore unsatisfied above one ward, and SPEC-004's amended criterion 2
requires this scheduler to clear the N09 baseline gate **at hospital scale**
before any result here is reportable. Building it is authorised; believing it
is not. `tests/bench/test_nsga2_vs_baselines.py` is where that is measured.

## Representation

SPEC-004: "A schedule is per-clinician permutations with time offsets, not a
bit string — §4 integer/permutation operators, not one-point crossover."

The genome is a **fixed-length integer vector**, one gene per *requirement* —
per `(patient, specialty)` pair the N04 strategy says must be covered, which
is the same coverage decision `cpsat.build_model`'s `AddExactlyOne` and
`baselines`' `Assignment` make. Each gene is a pair:

    (covering clinician, preferred start slot)

Fixed-length and identically keyed across every individual, which is what
makes uniform crossover meaningful: gene *k* of either parent answers the same
question. Keying offsets by requirement rather than by `(clinician, patient)`
is deliberate — the latter's key set changes when the coverage half of the
genome mutates, so a child would inherit timing genes whose meaning depended
on the other parent's coverage.

**Where the permutation went.** It is *derived*, not stored, and this is the
one representation decision worth arguing with. `AcuityOrdering` and
`IsolationLast` already fix a clinician's route up to ties within an
(isolation group, acuity level) class — the same segmentation `acs` routes
inside. A stored permutation would therefore be mostly redundant with the
constraints and mostly repaired away. Instead the **preferred-start genes
break those ties**: within a class, patients are visited in order of the
clinician's preferred slot for them. One gene family therefore controls both
*when* a visit is wanted and *where in the route* it falls, and both are
heritable. Ordering itself is delegated to `baselines._order_route`, imported
rather than reimplemented so that "a legal route" means exactly one thing
across N09, N10 and N11 (its sort is stable, which is precisely what lets the
offset genes act as the intra-class tie-break).

**Phenotype is a deterministic function of the genome.** Given the genes, the
route order and every start slot follow with no further random draws:
`_assign_starts_near` takes, for each visit in route order, the legal start
nearest the gene's preferred slot among those leaving room to walk from the
previous bed. `baselines._materialize` instead *redraws* start slots at
random every time, which is right for random search and fatal for a genetic
algorithm — a child would not inherit its parents' timing, so crossover would
transmit nothing about four of the five objectives. Making the map
deterministic is what makes this a GA rather than random search wearing a
population.

## Repair, not penalty

SPEC-004: "Invalid offspring are **repaired**, not penalised: penalty terms on
a heavily-constrained space produce populations that are almost entirely
infeasible, and the repair operator is itself a documented modelling choice."

`_repair` runs at most `max_repair_rounds` rounds. Each round materialises the
genome; if a clinician's assigned set has no legal route (the jointly
unsatisfiable acuity/isolation pair `cpsat.build_model` documents) or no legal
timing, one requirement gene belonging to that clinician is moved to a
different holder of that specialty, or — when the specialty has only one
holder — its preferred slot is pulled earlier. Bounded rounds, so it
terminates by construction (SPEC-004 criterion 7); a genome still infeasible
at the end is **discarded**, not scored, and costs no evaluation. Feasibility
is re-checked on the way out via `Schedule.hard_violations` and
`cpsat.respects_travel_time` rather than trusted to follow from construction.

## No scalarisation anywhere

ADR-0004. NSGA-II is the one algorithm in `SELECTION-GUIDE.md` that needs no
scalar fitness at all: ranks come from domination and ties from sparsity, both
computed over the five objectives as five objectives. Unlike `acs`, which had
to pick a single-objective proxy to drive its pheromone update, nothing here
ever combines the objectives into a number.

## Deviation from Alg 104, stated

Algorithm 104 line 19 returns `BestFront`, the front of the *final*
generation's `P` united with `A`. Archive truncation (line 14) keeps the sparsest members
when a rank-1 front is larger than the archive, so a point discovered in
generation 5 can be dropped by generation 6 and the returned front can be
worse than one already seen. For a governance artefact a clinical director
browses (ADR-0004) that is indefensible, so this module additionally keeps an
external monotone archive: every generation's front is merged into it through
`pareto_front`, and that is what `solve` returns. It is never used to breed
from, so the search dynamics are Algorithm 104's unchanged; only the reporting
is monotone.

**Determinism.** Every draw — initial population, crossover mask, mutation,
tournament, repair — goes through the caller's `rng: Random`. Truncation and
selection ties break on population index, never on `set` iteration order, so a
fixed seed and an evaluation-bounded `Budget` give an identical front (gate 8).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from random import Random

from hwpm.domain.model import Acuity, ClinicianId, PatientId, Specialty
from hwpm.domain.schedule import PlannedVisit, Schedule
from hwpm.optimize.baselines import _order_route, _specialty_holders
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
from hwpm.optimize.evaluate import dominates, evaluate, pareto_front
from hwpm.optimize.types import Budget, Instance, Objectives

#: One covering decision the instance demands: this patient needs this
#: specialty. The genome has exactly one gene per requirement.
Requirement = tuple[PatientId, Specialty]

#: A gene: who covers the requirement, and the slot they would prefer to do it
#: in. The preferred slot is a *request*, not a placement -- `_materialise`
#: maps it to the nearest legal slot.
Gene = tuple[ClinicianId, int]

Genome = dict[Requirement, Gene]


@dataclass(frozen=True)
class _Ground:
    """Everything derived from the `Instance` that never changes during a
    solve, computed once. `allowed_starts` in particular is O(patients x
    clinicians x slots) and calling it per candidate — as a naive port of
    `baselines._materialize` would — costs more than the whole search."""

    inst: Instance
    keys: tuple[Requirement, ...]
    holders: dict[Specialty, tuple[ClinicianId, ...]]
    acuity: dict[PatientId, Acuity]
    isolated: dict[PatientId, bool]
    acuity_active: bool
    isolation_active: bool
    starts: dict[tuple[str, str], tuple[int, ...]]
    #: A soft ceiling on how many patients one clinician is given while a
    #: genome is being constructed or repaired: an even share of the
    #: requirements, plus one. Not a constraint — nothing rejects a schedule
    #: for exceeding it, and the search may cross it freely — but without it
    #: the compatibility filter below funnels work onto whichever clinician
    #: happens to hold a low-acuity isolation patient, since that one clinician
    #: is compatible with everybody. Measured at N10 on the 48-bed instance:
    #: one clinician drew 16 of 48 patients and could not fit them in the
    #: window.
    share: int


@dataclass(frozen=True)
class _Individual:
    """A genome with the phenotype it produced. Kept together so that breeding
    can read the genes without re-deriving them from the `Schedule`."""

    genome: Genome
    schedule: Schedule
    objectives: Objectives


def _check_feasible(
    inst: Instance, holders: dict[Specialty, tuple[ClinicianId, ...]]
) -> None:
    """A required specialty no rostered clinician holds is a malformed
    instance, not a hard search. Same contract as `cpsat.build_model` and
    `baselines._check_feasible`; kept local for the same reason `acs` does,
    so the message is tied to `Instance` rather than to another module."""
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


def _ground(inst: Instance) -> _Ground:
    holders = _specialty_holders(inst)
    _check_feasible(inst, holders)
    keys = tuple(
        (patient.id, specialty)
        for patient in sorted(inst.patients, key=lambda p: p.id.value)
        for specialty in sorted(
            inst.required.get(patient.id, frozenset()), key=lambda s: s.value
        )
    )
    acuity_active, isolation_active = ordering_active(inst)
    return _Ground(
        inst=inst,
        keys=keys,
        holders=holders,
        acuity=acuity_of(inst),
        isolated=isolated_of(inst),
        acuity_active=acuity_active,
        isolation_active=isolation_active,
        starts=allowed_starts(inst),
        share=max(1, math.ceil(len(keys) / max(1, len(inst.clinicians))) + 1),
    )


# ---------------------------------------------------------------------------
# Genome -> Schedule
# ---------------------------------------------------------------------------


def _random_genome(ground: _Ground, rng: Random) -> Genome:
    """A uniform draw over holders and slots — the unbiased initial genome.

    Kept, and used by `Nsga2Scheduler(seeded=False)`, because it is what makes
    the seeded constructor below measurable rather than assumed. On the
    single-ward instance it is perfectly serviceable; above it, it is not.
    """
    horizon = ground.inst.slots.n_slots
    return {
        key: (rng.choice(ground.holders[key[1]]), rng.randrange(horizon))
        for key in ground.keys
    }


def _seeded_genome(ground: _Ground, rng: Random) -> Genome:
    """Greedy-randomised initial genome: requirements are covered in a random
    order, each by a random holder among those the assignment keeps
    route-feasible and lightly loaded.

    This is Rule 0's step 2 — "greedy/constructive heuristic" — used as a
    *seeder* for the population, not as the answer, and it is here because the
    unbiased draw above stops working at exactly the scale N21 reinstated this
    node for. Measured on the 48-bed hospital instance: **0 of 50 uniform
    draws materialise into a legal schedule**, and 0 of 20 survive 200 rounds
    of repair, because `_ordering_compatible`'s inequality has to hold for
    every clinician at once. A population that is empty is not a population,
    and NSGA-II with nothing to breed from reports nothing at all.

    The bias this introduces is real and worth naming: the initial population
    is drawn from feasible-leaning coverage rather than from the whole space.
    Diversity is preserved by the random requirement order, the random choice
    among the tolerated holders, and the entirely unbiased preferred slots;
    and the search is free to leave the seeded region, since crossover and
    mutation consult none of this.
    """
    order = list(ground.keys)
    rng.shuffle(order)
    coverage: dict[Requirement, ClinicianId] = {}
    load: dict[ClinicianId, list[PatientId]] = {}
    for key in order:
        patient, specialty = key
        chosen = _pick_holder(ground, specialty, None, patient, load, rng)
        coverage[key] = chosen or ground.holders[specialty][0]
        load.setdefault(coverage[key], []).append(patient)
    offsets = _seed_offsets(ground, load, rng)
    return {key: (coverage[key], offsets[(coverage[key], key[0])]) for key in ground.keys}


def _seed_offsets(
    ground: _Ground, load: dict[ClinicianId, list[PatientId]], rng: Random
) -> dict[tuple[ClinicianId, PatientId], int]:
    """Preferred slots for a seeded genome, laid out along a greedy
    nearest-neighbour route inside each forced (isolation, acuity) class.

    Uniformly random offsets would be the unbiased choice and they do not work:
    the class order is forced, but *within* a class the offsets decide the
    visit order (module docstring), so random offsets mean a random walk around
    the building between every pair of consecutive beds. On the 30-bed instance
    that leaves the nephrology consultant — the only holder of that specialty,
    so every nephrology patient is theirs — unable to finish inside the window
    at all, and 0 of 20 seeded genomes materialised.

    This does not preempt N11: ACS *searches* the routing space, this only
    stops the initial population starting from a route no one would walk. The
    next visit is drawn from the two nearest candidates rather than the single
    nearest, so seeded individuals still differ from one another.
    """
    horizon = ground.inst.slots.n_slots
    out: dict[tuple[ClinicianId, PatientId], int] = {}
    for clinician in sorted(load, key=lambda c: c.value):
        groups: dict[tuple[bool, int], list[PatientId]] = {}
        for patient in dict.fromkeys(load[clinician]):
            key = (
                ground.isolated.get(patient, False),
                -int(ground.acuity.get(patient, Acuity.LOW)),
            )
            groups.setdefault(key, []).append(patient)
        clock = 0
        previous: PatientId | None = None
        for group in sorted(groups):
            remaining = sorted(groups[group], key=lambda p: p.value)
            while remaining:
                if previous is None:
                    chosen = rng.choice(remaining)
                else:
                    near = sorted(
                        remaining,
                        key=lambda p: (travel_slots(ground.inst, previous, p), p.value),  # type: ignore[arg-type]
                    )
                    chosen = rng.choice(near[:2])
                    clock += travel_slots(ground.inst, previous, chosen)
                out[(clinician, chosen)] = min(horizon - 1, clock)
                clock += ground.inst.visit_slots
                previous = chosen
                remaining.remove(chosen)
    return out


def _pick_holder(
    ground: _Ground,
    specialty: Specialty,
    exclude: ClinicianId | None,
    patient: PatientId,
    load: dict[ClinicianId, list[PatientId]],
    rng: Random,
) -> ClinicianId | None:
    """Choose a holder of `specialty` to cover `patient`, preferring one this
    keeps route-feasible for (`_ordering_compatible`), one inside its share of
    the work, and one already working **near** this bed. Shared by construction
    and repair so the two cannot drift apart on what "a sensible holder" means.

    The eligibility order is compatible-and-roomy, then compatible, then roomy,
    then anybody: each fallback gives up strictly less than returning nothing
    would. Among the eligible, candidates are ranked by the travel time from
    their nearest already-held bed and one is drawn from the best two — a
    randomised-greedy choice, not a deterministic one, so that a population
    seeded this way still differs individual to individual.

    **Locality is not a nicety at hospital scale.** The reference geometry
    spans nine wards over three floors and `travel_slots` charges lift rides in
    whole slots; a clinician handed patients uniformly across it cannot fit the
    round into the window at all, whatever the ordering. Measured at N10 on the
    30-bed instance: with compatibility and load but no locality, 0 of 20
    seeded genomes repaired into a legal schedule.
    """
    holders = [c for c in ground.holders[specialty] if c != exclude]
    if not holders:
        return None
    compatible = [
        c for c in holders if _ordering_compatible(ground, load.get(c, []), patient)
    ]
    roomy = [c for c in compatible if len(load.get(c, [])) < ground.share]
    pool = (
        roomy
        or compatible
        or [c for c in holders if len(load.get(c, [])) < ground.share]
        or holders
    )
    ranked = sorted(
        pool, key=lambda c: (_nearest(ground, load.get(c, []), patient), c.value)
    )
    return rng.choice(ranked[:2])


def _nearest(ground: _Ground, held: list[PatientId], patient: PatientId) -> int:
    """Travel slots from the nearest bed a clinician already holds to this one;
    zero for a clinician with no patients yet, who is free to start anywhere."""
    if not held:
        return 0
    return min(travel_slots(ground.inst, other, patient) for other in held)


def _visits_wanted(genome: Genome) -> dict[ClinicianId, dict[PatientId, int]]:
    """Collapse the genome to one wanted visit per `(clinician, patient)`.

    A clinician holding two of a patient's required specialties satisfies both
    in one bedside visit — `evaluate._copresence` scores exactly that case —
    so two genes can name one visit. Their preferred slots are combined by
    **minimum**: the aggregation has to be deterministic (gate 8) and
    order-independent, and taking the earlier request keeps the visit inside
    the round window rather than drifting later as requirements accumulate.
    """
    wanted: dict[ClinicianId, dict[PatientId, int]] = {}
    for (patient, _specialty), (clinician, offset) in genome.items():
        per_patient = wanted.setdefault(clinician, {})
        current = per_patient.get(patient)
        per_patient[patient] = offset if current is None else min(current, offset)
    return wanted


def _ordering_compatible(
    ground: _Ground, held: list[PatientId], newcomer: PatientId
) -> bool:
    """Could one clinician hold `held` plus `newcomer` and still have a legal
    route?

    `AcuityOrdering` (never step up in acuity) and `IsolationLast` (nothing
    follows an isolation patient but isolation) leave a clinician's route with
    no freedom at all on this point: the isolation patients are last, so the
    *most* acute isolation patient must still be no more acute than the
    *least* acute non-isolation one. That single inequality is the whole
    feasibility question for a patient set, and it is the reason a uniform
    random coverage draw is not merely unlikely but effectively impossible
    above one ward — measured at N10, 0 feasible draws in 50 on the 48-bed
    instance. Both `_seeded_genome` and `_repair` consult it.
    """
    if not (ground.acuity_active and ground.isolation_active):
        return True
    group = [*held, newcomer]
    isolated = [p for p in group if ground.isolated.get(p, False)]
    others = [p for p in group if not ground.isolated.get(p, False)]
    if not isolated or not others:
        return True
    worst = max(ground.acuity.get(p, Acuity.LOW) for p in isolated)
    mildest = min(ground.acuity.get(p, Acuity.LOW) for p in others)
    return worst <= mildest


def _ordering_culprits(
    ground: _Ground, patients: tuple[PatientId, ...]
) -> tuple[PatientId, ...]:
    """Which patients to consider moving when a clinician's set has no legal
    route: the two ends of `_ordering_compatible`'s broken inequality.

    Both ends, not just one, because they are rehomed with very different
    ease and which is available depends on the roster. The most acute
    isolation patient is the conceptual culprit; the mildest non-isolation one
    is usually the *practical* one, since it is far more likely to require a
    specialty with several holders. Offering repair both and letting it draw
    is what stops a bottleneck specialty — nephrology has two holders in the
    reference roster at any hospital size — from bouncing the same patient
    between the same two clinicians until the round budget runs out.
    """
    isolated = [p for p in patients if ground.isolated.get(p, False)]
    others = [p for p in patients if not ground.isolated.get(p, False)]
    if not isolated or not others:
        return patients[:1]
    return (
        max(isolated, key=lambda p: (ground.acuity.get(p, Acuity.LOW), p.value)),
        min(others, key=lambda p: (ground.acuity.get(p, Acuity.LOW), p.value)),
    )


def _assign_starts_near(
    ground: _Ground,
    clinician: ClinicianId,
    ordered: tuple[PatientId, ...],
    preferred: dict[PatientId, int],
) -> tuple[list[PlannedVisit] | None, PatientId | None]:
    """Place each visit at the legal start nearest its gene's preferred slot.

    Deterministic — no `rng` — which is the whole point (see the module
    docstring): a genetic algorithm whose phenotype re-randomises on every
    materialisation inherits nothing from its parents. Legal means clearing
    availability and off-ward blocks (`cpsat.allowed_starts`) and leaving room
    to walk from the previous bed (`cpsat.travel_slots`). `allowed_starts`
    returns ascending slots, so an exact tie between an earlier and a later
    candidate resolves to the earlier one.

    Returns `(visits, None)` on success and `(None, patient)` on failure,
    naming the visit that ran out of window — the one `_repair` must move to
    make room, rather than a random one of the clinician's.
    """
    duration = ground.inst.visit_slots
    visits: list[PlannedVisit] = []
    prev_end: int | None = None
    prev_patient: PatientId | None = None
    for patient in ordered:
        legal = ground.starts.get((clinician.value, patient.value), ())
        if prev_patient is None or prev_end is None:
            earliest = 0
        else:
            earliest = prev_end + travel_slots(ground.inst, prev_patient, patient)
        want = preferred[patient]
        chosen: int | None = None
        for slot in legal:
            if slot < earliest:
                continue
            if chosen is None or abs(slot - want) < abs(chosen - want):
                chosen = slot
        if chosen is None:
            return None, patient
        visits.append(
            PlannedVisit(
                clinician=clinician, patient=patient, start=chosen, duration=duration
            )
        )
        prev_end = chosen + duration
        prev_patient = patient
    return visits, None


@dataclass(frozen=True)
class _Blocked:
    """Why a genome has no schedule: this clinician cannot fit this patient.

    Naming the obstruction rather than returning a bare `None` is what lets
    `_repair` act on the actual conflict instead of perturbing the genome at
    random and hoping. On a hospital-scale instance the difference is not a
    refinement — random perturbation repairs essentially nothing (measured at
    N10: 0 of 20 genomes repaired in 200 rounds).
    """

    clinician: ClinicianId
    #: The patients whose requirements repair may move off this clinician.
    #: Plural because an ordering conflict has two ends and they are not
    #: equally easy to rehome -- see `_ordering_culprits`.
    patients: tuple[PatientId, ...]


def _materialise(ground: _Ground, genome: Genome) -> Schedule | _Blocked:
    """The genome's schedule, or the obstruction that stopped it."""
    wanted = _visits_wanted(genome)
    visits: list[PlannedVisit] = []
    for clinician in sorted(wanted, key=lambda c: c.value):
        preferred = wanted[clinician]
        # Sorted by preferred slot first so that `_order_route`'s stable sort
        # keeps that order within an (isolation, acuity) class -- the offset
        # genes are the intra-class tie-break (module docstring).
        by_request = tuple(sorted(preferred, key=lambda p: (preferred[p], p.value)))
        ordered = _order_route(
            by_request,
            ground.acuity,
            ground.isolated,
            ground.acuity_active,
            ground.isolation_active,
        )
        if ordered is None:
            return _Blocked(clinician, _ordering_culprits(ground, by_request))
        placed, stuck = _assign_starts_near(ground, clinician, ordered, preferred)
        if placed is None:
            return _Blocked(clinician, (stuck or ordered[0],))
        visits.extend(placed)

    schedule = Schedule(visits=tuple(visits))
    if schedule.hard_violations(ground.inst.constraints) or respects_travel_time(
        schedule, ground.inst
    ):
        # Belt and braces: the construction above enforces both, and these
        # checks exist so that a construction bug fails as a discarded
        # candidate rather than as an invalid returned schedule. Which visit
        # to blame is then genuinely unknown, so the lowest-sorting gene is
        # named rather than a diagnosis being invented.
        key = min(genome, key=lambda k: (k[0].value, k[1].value))
        return _Blocked(genome[key][0], (key[0],))
    return schedule


def _repair(
    ground: _Ground, genome: Genome, rng: Random, rounds: int
) -> tuple[Genome, Schedule] | None:
    """SPEC-004's repair operator: bounded, feasibility-preserving, and it
    terminates because `rounds` bounds it (criterion 7).

    Each round materialises the genome and, if it is blocked, moves the named
    patient's requirement off the obstructed clinician to another holder of
    that specialty (`_pick_holder`). When the specialty has only one holder
    there is nobody to move to, so the preferred slot is pulled earlier
    instead — halving it toward slot 0, where the window constraint bites
    least. A genome still blocked after `rounds` is discarded, not scored, and
    costs no evaluation.
    """
    working = dict(genome)
    for _ in range(rounds + 1):
        outcome = _materialise(ground, working)
        if isinstance(outcome, Schedule):
            return working, outcome
        blocked = sorted(
            (
                key
                for key, (clinician, _) in working.items()
                if clinician == outcome.clinician and key[0] in outcome.patients
            ),
            key=lambda k: (k[0].value, k[1].value),
        )
        if not blocked:
            return None
        key = rng.choice(blocked)
        clinician, offset = working[key]
        load = {c: list(p) for c, p in _visits_wanted(working).items()}
        moved = _pick_holder(ground, key[1], clinician, key[0], load, rng)
        working[key] = (clinician, offset // 2) if moved is None else (moved, offset)
    return None


# ---------------------------------------------------------------------------
# Alg 101 / 102 / 103 -- ranking, sparsity, selection
# ---------------------------------------------------------------------------


def front_ranks(objectives: list[Objectives]) -> list[list[int]]:
    """Algorithm 101, p.141: partition indices into Pareto front ranks by
    repeatedly peeling off the non-dominated front. Rank 0 is the best.

    Indices, not individuals, so that the ranking is testable against
    hand-built objective vectors without constructing schedules.
    """
    remaining = list(range(len(objectives)))
    ranks: list[list[int]] = []
    while remaining:
        current = [
            i
            for i in remaining
            if not any(dominates(objectives[j], objectives[i]) for j in remaining)
        ]
        ranks.append(current)
        peeled = set(current)
        remaining = [i for i in remaining if i not in peeled]
    return ranks


def sparsities(indices: list[int], objectives: list[Objectives]) -> dict[int, float]:
    """Algorithm 102, p.142: crowding distance as the summed, range-normalised
    Manhattan gap between an individual's neighbours along each objective.
    Front ends get infinite sparsity.

    Algorithm 102 asks for `Range(Oi)`, "the range of possible values a given
    objective can take on". Two of the five have no such range (motion metres
    and makespan seconds are bounded only by the instance), so the **observed**
    range within the front is used, and an objective on which the whole front
    is constant contributes nothing rather than dividing by zero. The book's
    stated fallback — assume a range of 1 — would let raw metres swamp
    co-presence's 0-to-1 scale entirely, which is exactly the failure its own
    footnote 124 warns about for SPEA2.
    """
    if len(indices) <= 2:
        return {i: math.inf for i in indices}
    out: dict[int, float] = {i: 0.0 for i in indices}
    for field in Objectives.SENSES:
        ordered = sorted(
            indices,
            key=lambda i: (getattr(objectives[i], field), i),
        )
        low = getattr(objectives[ordered[0]], field)
        high = getattr(objectives[ordered[-1]], field)
        spread = high - low
        if spread == 0:
            continue
        out[ordered[0]] = math.inf
        out[ordered[-1]] = math.inf
        for position in range(1, len(ordered) - 1):
            after = getattr(objectives[ordered[position + 1]], field)
            before = getattr(objectives[ordered[position - 1]], field)
            out[ordered[position]] += (after - before) / spread
    return out


def _tournament(
    pool: list[int],
    rank_of: dict[int, int],
    sparsity_of: dict[int, float],
    size: int,
    rng: Random,
) -> int:
    """Algorithm 103, p.142: lexicographic tournament — lower front rank wins,
    ties broken by higher sparsity. Picked with replacement, per the
    algorithm."""

    def key(index: int) -> tuple[int, float]:
        # Lower rank first, then *higher* sparsity -- negated so that one
        # ascending comparison expresses Algorithm 103's two lexicographic
        # tests without a branch per test.
        return (rank_of[index], -sparsity_of[index])

    best = rng.choice(pool)
    for _ in range(1, size):
        challenger = rng.choice(pool)
        if key(challenger) < key(best):
            best = challenger
    return best


# ---------------------------------------------------------------------------
# Breeding operators (SPEC-004: section 4 integer operators, not bit strings)
# ---------------------------------------------------------------------------


def _uniform_crossover(
    ground: _Ground, a: Genome, b: Genome, rng: Random
) -> tuple[Genome, Genome]:
    """Uniform crossover over the fixed requirement-keyed gene vector: each
    gene independently goes to one child or the other.

    Uniform rather than one- or two-point because the gene order is an
    arbitrary sort of `(patient, specialty)` — there is no locality along it
    for a positional operator to preserve, so a cut point would only impose a
    linkage the problem does not have.
    """
    first: Genome = {}
    second: Genome = {}
    for key in ground.keys:
        if rng.random() < 0.5:
            first[key], second[key] = a[key], b[key]
        else:
            first[key], second[key] = b[key], a[key]
    return first, second


def _mutate(ground: _Ground, genome: Genome, rate: float, rng: Random) -> Genome:
    """Per-gene mutation at `rate`, choosing with equal probability between the
    two halves of the gene: re-draw the covering clinician among the holders of
    that specialty, or shift the preferred slot.

    The slot shift is local (a uniform step of +/-1..3 slots, clamped to the
    horizon) rather than a fresh uniform draw. A full re-draw is available
    through the clinician half's knock-on effects, and a mutation operator
    whose typical step is the width of the whole search space is a restart, not
    a tweak.
    """
    horizon = ground.inst.slots.n_slots
    mutated = dict(genome)
    for key in ground.keys:
        if rng.random() >= rate:
            continue
        clinician, offset = mutated[key]
        alternatives = [c for c in ground.holders[key[1]] if c != clinician]
        if alternatives and rng.random() < 0.5:
            mutated[key] = (rng.choice(alternatives), offset)
        else:
            step = rng.choice([-3, -2, -1, 1, 2, 3])
            mutated[key] = (clinician, min(horizon - 1, max(0, offset + step)))
    return mutated


# ---------------------------------------------------------------------------
# The scheduler
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Nsga2Scheduler:
    """SPEC-004's `Scheduler`, implemented as NSGA-II (Alg 104, p.143).

    Hyperparameters are fixed here rather than tuned per instance — SPEC-004's
    "Failure modes" names tuning to the answer as a failure mode and requires
    them recorded in the artefact header, which is what these defaults and
    their reasons are.

    `popsize` and `archive_size` follow Algorithm 104's `a = m` note.
    `tournament_size` is the algorithm's own "typically 2" (line 18).
    `crossover_rate` leaves a minority of the population passing through as
    mutated clones, which keeps a foothold when crossover of two distant
    parents materialises to nothing. `mutation_rate` is `None` by default,
    meaning `1 / genome length` — the standard per-gene rate, so that a typical
    child differs in about one gene regardless of ward size, rather than
    degrading into a random restart as the hospital grows.
    `max_repair_rounds` bounds SPEC-004's repair operator (criterion 7) and is
    `None` by default, meaning `max(8, one round per requirement)` — the number
    of things that can be wrong with a genome grows with the instance, so a
    fixed constant would be a single-ward number quietly capping hospital-scale
    repair. `seeded` selects the greedy-randomised initial population
    (`_seeded_genome`) over the uniform one; `seeded=False` exists so that the
    difference is measurable rather than asserted, and is not a supported
    configuration above one ward, where it yields no feasible individual at
    all.
    """

    popsize: int = 40
    archive_size: int | None = None
    tournament_size: int = 2
    crossover_rate: float = 0.9
    mutation_rate: float | None = None
    max_repair_rounds: int | None = None
    seeded: bool = True

    def __post_init__(self) -> None:
        if self.popsize < 2:
            raise ValueError(f"popsize must be >= 2, got {self.popsize!r}")
        if self.archive_size is not None and self.archive_size < 1:
            raise ValueError(f"archive_size must be >= 1, got {self.archive_size!r}")
        if self.tournament_size < 1:
            raise ValueError(
                f"tournament_size must be >= 1, got {self.tournament_size!r}"
            )
        if not 0.0 <= self.crossover_rate <= 1.0:
            raise ValueError(
                f"crossover_rate must be in [0, 1], got {self.crossover_rate!r}"
            )
        if self.mutation_rate is not None and not 0.0 <= self.mutation_rate <= 1.0:
            raise ValueError(
                f"mutation_rate must be in [0, 1], got {self.mutation_rate!r}"
            )
        if self.max_repair_rounds is not None and self.max_repair_rounds < 0:
            raise ValueError(
                f"max_repair_rounds must be >= 0, got {self.max_repair_rounds!r}"
            )

    # -- helpers -----------------------------------------------------------

    def _rounds(self, ground: _Ground) -> int:
        if self.max_repair_rounds is not None:
            return self.max_repair_rounds
        return max(8, len(ground.keys))

    def _born(self, ground: _Ground, genome: Genome, rng: Random) -> _Individual | None:
        repaired = _repair(ground, genome, rng, self._rounds(ground))
        if repaired is None:
            return None
        genes, schedule = repaired
        return _Individual(genes, schedule, quantise(evaluate(schedule, ground.inst)))

    def _initial_population(
        self, ground: _Ground, budget: Budget, started: float, rng: Random
    ) -> tuple[list[_Individual], int]:
        population: list[_Individual] = []
        evaluations = 0
        attempts = 0
        limit = self.popsize * 20
        while len(population) < self.popsize and attempts < limit:
            if not _within_budget(started, evaluations, budget):
                break
            attempts += 1
            draw = _seeded_genome if self.seeded else _random_genome
            individual = self._born(ground, draw(ground, rng), rng)
            if individual is None:
                continue
            population.append(individual)
            evaluations += 1
        return population, evaluations

    def _truncate(self, pool: list[_Individual]) -> list[_Individual]:
        """Algorithm 104 lines 10-17: fill the archive rank by rank, and when a
        rank does not fit, take its sparsest members. Ties break on population
        index rather than "arbitrarily" (line 14) so the result is reproducible
        under a fixed seed (gate 8)."""
        capacity = self.archive_size or self.popsize
        objectives = [individual.objectives for individual in pool]
        archive: list[_Individual] = []
        for rank in front_ranks(objectives):
            if len(archive) + len(rank) <= capacity:
                archive.extend(pool[i] for i in rank)
                continue
            spread = sparsities(rank, objectives)
            room = capacity - len(archive)
            chosen = sorted(rank, key=lambda i: (-spread[i], i))[:room]
            archive.extend(pool[i] for i in sorted(chosen))
            break
        return archive

    # -- Scheduler ---------------------------------------------------------

    def solve(
        self, inst: Instance, budget: Budget, rng: Random
    ) -> list[tuple[Schedule, Objectives]]:
        ground = _ground(inst)
        started = time.perf_counter()
        if not ground.keys:
            # Nothing to cover: no patient requires any specialty. An empty
            # schedule is the honest answer, not an empty front.
            empty = Schedule(visits=())
            return [(empty, quantise(evaluate(empty, inst)))]

        population, evaluations = self._initial_population(ground, budget, started, rng)
        if not population:
            return []

        archive: list[_Individual] = []
        best: list[tuple[Schedule, Objectives]] = []

        while True:
            pool = population + archive
            best = pareto_front(best + [(ind.schedule, ind.objectives) for ind in pool])
            archive = self._truncate(pool)
            if not _within_budget(started, evaluations, budget):
                break

            # Alg 104 line 18: breed the next population from the archive,
            # selecting with Alg 103.
            objectives = [individual.objectives for individual in archive]
            ranks = front_ranks(objectives)
            rank_of = {i: r for r, rank in enumerate(ranks) for i in rank}
            sparsity_of: dict[int, float] = {}
            for rank in ranks:
                sparsity_of.update(sparsities(rank, objectives))
            indices = list(range(len(archive)))
            rate = self.mutation_rate
            if rate is None:
                rate = 1.0 / len(ground.keys)

            children: list[_Individual] = []
            while len(children) < self.popsize:
                if not _within_budget(started, evaluations, budget):
                    break
                mother = archive[
                    _tournament(indices, rank_of, sparsity_of, self.tournament_size, rng)
                ]
                father = archive[
                    _tournament(indices, rank_of, sparsity_of, self.tournament_size, rng)
                ]
                if rng.random() < self.crossover_rate:
                    genomes = _uniform_crossover(
                        ground, mother.genome, father.genome, rng
                    )
                else:
                    genomes = (dict(mother.genome), dict(father.genome))
                for genome in genomes:
                    if len(children) >= self.popsize:
                        break
                    child = self._born(ground, _mutate(ground, genome, rate, rng), rng)
                    if child is None:
                        continue
                    children.append(child)
                    evaluations += 1

            if not children:
                break
            population = children

        return best
