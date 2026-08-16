"""Benchmark instances for SPEC-004. Node N08.

These live in `src/` rather than in `tests/` deliberately. SPEC-004's Rule 0
decision turns on a measurement — "does CP-SAT solve the realistic single-ward
instance in acceptable time" — and a measurement whose input is defined inside
a test file is one nobody outside the test run can reproduce. `realistic_single_ward`
is the instance named in SPEC-004 ("one 30-bed ward, 8 clinicians, 3-hour round
window at 5-minute resolution") and in `SELECTION-GUIDE.md`'s scale sanity
check, built from an explicit `rng` so the same seed gives the same instance on
any machine.

**Synthetic, and only synthetic.** Nothing here reads or approximates real ward
data (ADR-0005). Distributions are stated modelling assumptions, not fitted
ones, and are listed in `_ASSUMPTIONS` so that a reader of a benchmark result
can see what was assumed without reading the generator.

**A geometry compromise, stated up front.** `hwpm.domain.travel.TravelGraph`
mirrors `web/hospital-ward.html`, which has 6 beds per ward and 3 wards per
floor — 18 beds per floor, 54 in the building. A 30-bed ward therefore does not
fit on one floor of the reference geometry. Rather than invent floor plan
geometry the prototype does not have (which would put made-up metres into the
project's headline motion figure), the 30 beds are laid over five six-bed bays,
two of which are on the floor above. That *overstates* motion relative to a
real single-storey 30-bed ward, because some inter-bed moves become lift rides.
It does not affect the size of the search space, which is what the Rule 0
measurement is about, and overstating a cost is the safe direction for a
benchmark whose purpose is to ask whether the problem is hard enough to need a
metaheuristic.
"""

from __future__ import annotations

import math
from datetime import datetime
from random import Random

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
    Constraint,
    IsolationLast,
    NursingProtectedWindow,
    PatientUnavailable,
)
from hwpm.domain.travel import TravelGraph
from hwpm.optimize.types import Instance, SlotGrid

#: Modelling assumptions baked into `realistic_single_ward`. None is measured.
#: SPEC-004's "Failure modes" warns about optimising a fiction; the least a
#: generator can do is say which fiction.
_ASSUMPTIONS: tuple[str, ...] = (
    "60% of patients require one specialty, 35% two, 5% three",
    "acuity uniform over the four levels",
    "10% of patients are under transmission-based precautions",
    "15% of patients are off the ward for a contiguous 30-minute episode",
    "one 30-minute protected nursing window (the mid-round drug round)",
    "each clinician is available for the whole window bar a 25% chance of a "
    "45-minute commitment elsewhere",
    "a bedside visit takes one slot (5 minutes)",
    "yesterday's clinician is drawn from those holding a required specialty",
)

#: Bed node ids in the reference geometry, in a fixed order. Constructed from
#: `hwpm.domain.travel`'s documented `"<ward>/BED<n>"` node naming rather than
#: read off the graph, which exposes no node listing; `TravelGraph.cost` raises
#: `KeyError` on an unknown id, so a naming drift fails loudly at the first
#: distance query rather than silently scoring zero metres.
_WARD_ORDER: tuple[str, ...] = ("1A", "1B", "1C", "2A", "2B", "2C", "3A", "3B", "3C")
BED_IDS: tuple[str, ...] = tuple(
    f"{ward}/BED{index}" for ward in _WARD_ORDER for index in range(6)
)

#: The specialty mix on the round. Three general physicians because general
#: medicine is the specialty most patients need; two of each of the others so
#: that no single specialty has exactly one holder — a specialty with one
#: holder makes the acuity/isolation ordering rules (which apply per clinician
#: route) trivially able to render the instance infeasible, which would measure
#: the generator rather than the solver.
_REALISTIC_MIX: tuple[tuple[Role, tuple[Specialty, ...]], ...] = (
    (Role.CONSULTANT, (Specialty.GENERAL_MEDICINE,)),
    (Role.CONSULTANT, (Specialty.GENERAL_MEDICINE,)),
    (Role.REGISTRAR, (Specialty.GENERAL_MEDICINE,)),
    (Role.CONSULTANT, (Specialty.CARDIOLOGY,)),
    (Role.REGISTRAR, (Specialty.CARDIOLOGY,)),
    (Role.CONSULTANT, (Specialty.SURGICAL,)),
    (Role.REGISTRAR, (Specialty.SURGICAL,)),
    # One dual-specialty clinician: the case where a single visit satisfies two
    # requirements at once, which `_copresence` scores as an MDT moment and
    # which a formulation that only looked at pairs of visits would miss.
    (Role.CONSULTANT, (Specialty.NEPHROLOGY, Specialty.GENERAL_MEDICINE)),
)

_SPECIALTY_POOL: tuple[Specialty, ...] = (
    Specialty.GENERAL_MEDICINE,
    Specialty.CARDIOLOGY,
    Specialty.SURGICAL,
    Specialty.NEPHROLOGY,
)


def _clinicians(
    mix: tuple[tuple[Role, tuple[Specialty, ...]], ...],
) -> tuple[Clinician, ...]:
    return tuple(
        Clinician(
            id=ClinicianId(f"C{index:02d}"),
            role=role,
            specialties=frozenset(specialties),
        )
        for index, (role, specialties) in enumerate(mix)
    )


def team_mix(
    n_teams: int,
) -> tuple[tuple[Role, tuple[Specialty, ...]], ...]:
    """`_REALISTIC_MIX` repeated `n_teams` times — the roster of a hospital
    running `n_teams` ward teams in parallel. Node N21.

    Whole teams, not individual clinicians, because `_REALISTIC_MIX`'s
    composition is load-bearing: its comment records that no specialty may have
    exactly one holder, or the per-route acuity/isolation ordering rules can
    render the instance infeasible for reasons that measure the generator
    rather than the solver. Adding staff one at a time would break that
    invariant on most counts; adding a whole team preserves it by construction
    and keeps the specialty *proportions* identical to the instance N08 proved,
    which is what makes the two measurements comparable.
    """
    if n_teams < 1:
        raise ValueError(f"n_teams must be >= 1, got {n_teams!r}")
    return _REALISTIC_MIX * n_teams


#: Beds per clinician on the instance N08 proved optimal (30 beds, 8
#: clinicians). `teams_for_beds` holds this ratio as the hospital grows, so
#: that the N21 scaling curve varies *size* rather than *saturation* — see that
#: function's docstring for why the distinction decides what the curve means.
PROVEN_BEDS_PER_CLINICIAN = 30 / 8


def teams_for_beds(n_beds: int) -> int:
    """How many 8-clinician teams a `n_beds` hospital is rostered with, holding
    the proven instance's staffing ratio. Node N21.

    Why a rule rather than a number per row of the sweep: with the roster held
    at 8, the instance stops being *feasible* — not merely hard — somewhere
    between 42 and 48 beds, because a handful of specialties have two holders
    each and a 3-hour window only holds so many visits. A scaling curve run on
    a fixed roster therefore measures the ward filling up, and its late points
    say nothing about the solver at all. Scaling staff with beds is what makes
    "does CP-SAT still prove optimality as the hospital grows" a question about
    CP-SAT.

    Rounded *up*: understaffing reintroduces the saturation this exists to
    remove, so the safe direction is a slightly generous roster.
    """
    if n_beds < 1:
        raise ValueError(f"n_beds must be >= 1, got {n_beds!r}")
    return max(1, math.ceil(n_beds / PROVEN_BEDS_PER_CLINICIAN / len(_REALISTIC_MIX)))


def realistic_single_ward(
    rng: Random,
    *,
    n_beds: int = 30,
    n_slots: int = 36,
    slot_seconds: int = 300,
    start: datetime | None = None,
    n_teams: int = 1,
) -> Instance:
    """SPEC-004's realistic instance: one 30-bed ward, 8 clinicians, a 3-hour
    round window at 5-minute resolution.

    The defaults *are* the spec's instance; the parameters exist so a scaling
    curve can be measured without a second generator drifting away from this
    one. `n_teams` (N21) repeats the 8-clinician roster so that hospital-scale
    bed counts can be staffed at hospital-scale ratios; see `teams_for_beds`.

    Note that `n_teams` changes the instance *only* by enlarging the roster —
    the patients, their beds, their required specialties and their off-ward
    episodes are all drawn before the roster is consulted, so the same seed
    gives the same patients at every team count. Yesterday's clinician is the
    one exception and necessarily so: it is drawn from the holders of a
    required specialty, and there are more of those.
    """
    if n_beds > len(BED_IDS):
        raise ValueError(
            f"reference geometry has {len(BED_IDS)} beds, asked for {n_beds}"
        )
    grid = SlotGrid(
        start=start or datetime(2026, 8, 14, 9, 0),
        slot_seconds=slot_seconds,
        n_slots=n_slots,
    )
    clinicians = _clinicians(team_mix(n_teams))
    holders = {
        specialty: tuple(c.id for c in clinicians if specialty in c.specialties)
        for specialty in _SPECIALTY_POOL
    }

    patients: list[Patient] = []
    beds: dict[PatientId, LocationId] = {}
    required: dict[PatientId, frozenset[Specialty]] = {}
    previous_day: dict[PatientId, ClinicianId] = {}
    constraints: list[Constraint] = []
    acuity_map: dict[PatientId, Acuity] = {}
    isolation_map: dict[PatientId, IsolationStatus] = {}

    for index in range(n_beds):
        pid = PatientId(f"P{index:02d}")
        acuity = rng.choice(list(Acuity))
        roll = rng.random()
        n_required = 1 if roll < 0.60 else (2 if roll < 0.95 else 3)
        specialties = frozenset(rng.sample(_SPECIALTY_POOL, n_required))
        isolated = rng.random() < 0.10
        isolation = (
            rng.choice([IsolationStatus.CONTACT, IsolationStatus.DROPLET])
            if isolated
            else IsolationStatus.NONE
        )
        patients.append(Patient(id=pid, acuity=acuity, isolation_status=isolation))
        beds[pid] = LocationId(BED_IDS[index])
        required[pid] = specialties
        acuity_map[pid] = acuity
        isolation_map[pid] = isolation
        # Continuity: yesterday's clinician is one who could plausibly have
        # seen them, i.e. holds one of today's required specialties. Drawing
        # uniformly from all clinicians would make objective 4 mostly
        # unachievable and therefore uninformative.
        candidates = [
            c for s in sorted(specialties, key=lambda s: s.value) for c in holders[s]
        ]
        previous_day[pid] = rng.choice(candidates)
        # Off-ward episodes: imaging, dialysis, theatre.
        if rng.random() < 0.15:
            off_start = rng.randrange(0, max(1, n_slots - 6))
            constraints.append(
                PatientUnavailable(
                    patient=pid, slots=frozenset(range(off_start, off_start + 6))
                )
            )

    for clinician in clinicians:
        if rng.random() < 0.25:
            busy_start = rng.randrange(0, max(1, n_slots - 9))
            busy = set(range(busy_start, busy_start + 9))
        else:
            busy = set()
        constraints.append(
            ClinicianAvailability(
                clinician=clinician.id,
                available=frozenset(s for s in range(n_slots) if s not in busy),
            )
        )

    # The drug round: 30 minutes in the middle of the window. Soft (see
    # `NursingProtectedWindow`) -- it is objective 3's subject, not a wall.
    protected_start = n_slots // 2 - 3
    constraints.append(
        NursingProtectedWindow(
            slots=frozenset(range(protected_start, protected_start + 6))
        )
    )
    constraints.append(AcuityOrdering(acuity=acuity_map))
    constraints.append(IsolationLast(isolation=isolation_map))

    return Instance(
        patients=tuple(patients),
        clinicians=clinicians,
        constraints=tuple(constraints),
        graph=TravelGraph(),
        slots=grid,
        required=required,
        beds=beds,
        visit_slots=1,
        previous_day=previous_day,
    )


def tiny_instance(
    rng: Random,
    *,
    n_beds: int = 4,
    n_slots: int = 6,
    protected: bool = True,
) -> Instance:
    """A small instance for the brute-force oracle (SPEC-004 criterion 1).

    Small means small: brute force enumerates every legal combination of
    covering clinicians and start slots, which grows as
    `(start slots)**(visits)`. Four patients on a 6-slot grid is already tens
    of thousands of candidate schedules, which is the right side of the line
    for a test that has to run on every commit.

    All beds are in one ward bay so the instance exercises the routing and
    ordering machinery without the payoff table being dominated by lift rides.
    """
    if n_beds > 6:
        raise ValueError("tiny_instance keeps all beds in one 6-bed bay")
    grid = SlotGrid(start=datetime(2026, 8, 14, 9, 0), slot_seconds=300, n_slots=n_slots)
    clinicians = _clinicians(
        (
            (Role.CONSULTANT, (Specialty.GENERAL_MEDICINE,)),
            (Role.CONSULTANT, (Specialty.CARDIOLOGY,)),
            (Role.REGISTRAR, (Specialty.GENERAL_MEDICINE, Specialty.CARDIOLOGY)),
        )
    )
    pool = (Specialty.GENERAL_MEDICINE, Specialty.CARDIOLOGY)

    patients: list[Patient] = []
    beds: dict[PatientId, LocationId] = {}
    required: dict[PatientId, frozenset[Specialty]] = {}
    previous_day: dict[PatientId, ClinicianId] = {}
    acuity_map: dict[PatientId, Acuity] = {}
    isolation_map: dict[PatientId, IsolationStatus] = {}
    constraints: list[Constraint] = []

    for index in range(n_beds):
        pid = PatientId(f"P{index}")
        acuity = rng.choice([Acuity.LOW, Acuity.MODERATE, Acuity.HIGH])
        n_required = 2 if rng.random() < 0.5 else 1
        specialties = frozenset(rng.sample(pool, n_required))
        isolation = (
            IsolationStatus.CONTACT if rng.random() < 0.2 else IsolationStatus.NONE
        )
        patients.append(Patient(id=pid, acuity=acuity, isolation_status=isolation))
        beds[pid] = LocationId(BED_IDS[index])
        required[pid] = specialties
        acuity_map[pid] = acuity
        isolation_map[pid] = isolation
        previous_day[pid] = rng.choice([c.id for c in clinicians])

    constraints.append(AcuityOrdering(acuity=acuity_map))
    constraints.append(IsolationLast(isolation=isolation_map))
    if protected:
        constraints.append(NursingProtectedWindow(slots=frozenset({n_slots - 1})))

    return Instance(
        patients=tuple(patients),
        clinicians=clinicians,
        constraints=tuple(constraints),
        graph=TravelGraph(),
        slots=grid,
        required=required,
        beds=beds,
        visit_slots=1,
        previous_day=previous_day,
    )


def assumptions() -> tuple[str, ...]:
    """The modelling assumptions behind `realistic_single_ward`, for a
    benchmark report to print alongside its numbers."""
    return _ASSUMPTIONS
