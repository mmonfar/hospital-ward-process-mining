"""Synthetic ward event-log generator with configurable, known ground truth.

SPEC-001, node N01. `generate` returning `GroundTruth` alongside events is the
design decision that makes the rest of the project testable: every downstream
analysis can be checked against what actually happened rather than against a
previous run of itself (SPEC-001, "Test oracle").

Geometry: nine wards, six beds each, laid out exactly as the prototype's
`WARDS` array in `web/hospital-ward.html` (`01-DOMAIN-MODEL.md`: "that layout
is the reference geometry until real floor plans are ingested"). Distance is
straight-line, matching the modelling-assumptions table in SPEC-001 — SPEC-003
replaces this with a routed `TravelGraph`.

Noise injection (SPEC-001, "Failure modes": "the generator must inject clock
skew, duplicate reads, missing locations, and free-text location variants at
configurable rates"). Three of the four apply directly to a typed `Event`
stream and are implemented here: clock skew (a timestamp perturbed out of
order), duplicate reads (a repeated badge scan a few seconds later), and
low-confidence reads (`Event.confidence` pushed below the 0.8 quarantine
threshold — the typed equivalent of "this location string was ambiguous").
Free-text location *variants* are a property of a raw export string, and
`generate` returns typed `Event`s with an already-resolved `LocationId` per the
SPEC-001 interface, so there is nothing to vary yet; that noise dimension
belongs to whatever raw CSV/XES writer N03's fixtures need, and is deliberately
not faked here — see docs/AUDIT-LOG.md for this scope decision.

Determinism (acceptance criterion 3): every random choice in this module comes
from the `rng` argument. No module-level `random`, no wall-clock reads. Two
calls to `generate(config, Random(42))` compare equal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from random import Random

from hwpm.domain import (
    Acuity,
    Clinician,
    ClinicianId,
    Event,
    IsolationStatus,
    Location,
    LocationId,
    LocationKind,
    Patient,
    PatientId,
    Point,
    Role,
    Specialty,
    Visit,
)
from hwpm.ingest.evidence import (
    ConsultNote,
    ProblemListEntry,
    Referral,
    RequiredSpecialtyEvidence,
)

# ---------------------------------------------------------------------------
# Reference geometry — mirrors web/hospital-ward.html's WARDS / BED_LOCAL.
# ---------------------------------------------------------------------------

_WARD_LAYOUT: tuple[tuple[str, Specialty, int], ...] = (
    ("1A", Specialty.CARDIOLOGY, 0),
    ("1B", Specialty.NEPHROLOGY, 0),
    ("1C", Specialty.GENERAL_MEDICINE, 0),
    ("2A", Specialty.SURGICAL, 1),
    ("2B", Specialty.ICU, 1),
    ("2C", Specialty.RECOVERY, 1),
    ("3A", Specialty.ONCOLOGY, 2),
    ("3B", Specialty.RENAL, 2),
    ("3C", Specialty.REHABILITATION, 2),
)
_BED_LOCAL_XZ: tuple[tuple[float, float], ...] = (
    (-4.0, -2.2),
    (0.0, -2.2),
    (4.0, -2.2),
    (-4.0, 2.2),
    (0.0, 2.2),
    (4.0, 2.2),
)
_WARD_X_SPACING = 32.0
_FLOOR_Y_SPACING = 4.2
_NURSING_STATION_LOCAL_XZ = (-4.6, 0.9)


@dataclass(frozen=True)
class Ward:
    ward_id: str
    specialty: Specialty
    nursing_station: Location
    beds: tuple[Location, ...]


def _build_wards() -> tuple[Ward, ...]:
    floor_counters = [0, 0, 0]
    wards: list[Ward] = []
    for ward_id, specialty, floor in _WARD_LAYOUT:
        index_on_floor = floor_counters[floor]
        floor_counters[floor] += 1
        x = index_on_floor * _WARD_X_SPACING
        y = floor * _FLOOR_Y_SPACING
        ns_x, ns_z = _NURSING_STATION_LOCAL_XZ
        nursing_station = Location(
            id=LocationId(f"{ward_id}/NS"),
            kind=LocationKind.NURSING_STATION,
            point=Point(x + ns_x, y, ns_z),
        )
        beds = tuple(
            Location(
                id=LocationId(f"{ward_id}/BED{i}"),
                kind=LocationKind.BED,
                point=Point(x + bx, y, bz),
            )
            for i, (bx, bz) in enumerate(_BED_LOCAL_XZ)
        )
        wards.append(Ward(ward_id, specialty, nursing_station, beds))
    return tuple(wards)


# ---------------------------------------------------------------------------
# Config and ground truth
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SynthConfig:
    """Every field a rate or count, never a wall-clock or file-system value —
    that is what keeps `generate` deterministic and file-free."""

    n_clinicians: int = 6
    n_patients: int = 24
    shift_start: datetime = field(default=datetime(2026, 1, 5, 8, 0, 0))
    visits_per_clinician: int = 6
    visit_duration_minutes: int = 12
    gap_minutes: int = 3
    walking_speed_mps: float = 1.2

    # Noise, each a probability in [0, 1] applied independently per event.
    clock_skew_rate: float = 0.0
    clock_skew_seconds: float = 45.0
    duplicate_read_rate: float = 0.0
    low_confidence_rate: float = 0.0
    low_confidence_floor: float = 0.4

    def __post_init__(self) -> None:
        if self.n_patients < 1:
            raise ValueError("n_patients must be >= 1")
        if self.n_clinicians < 1:
            raise ValueError("n_clinicians must be >= 1")
        for name in (
            "clock_skew_rate",
            "duplicate_read_rate",
            "low_confidence_rate",
        ):
            rate = getattr(self, name)
            if not 0.0 <= rate <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {rate!r}")


@dataclass(frozen=True)
class GroundTruth:
    schedules: dict[ClinicianId, list[Visit]]
    required_specialties: dict[PatientId, frozenset[Specialty]]
    total_distance_m: dict[ClinicianId, float]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

_SOURCE = "synthetic"

#: Ward-day generator source tag. Distinct from `_SOURCE` so an event stream
#: can be told apart from the single-shift generator's at a glance.
_WARD_DAY_SOURCE = "synthetic:ward-day"


def generate(config: SynthConfig, rng: Random) -> tuple[list[Event], GroundTruth]:
    """Generate a synthetic event log and its known ground truth.

    All randomness is drawn from `rng`, in a fixed order of operations, so
    `generate(config, Random(seed))` is reproducible across runs and platforms
    (acceptance criterion 3).
    """
    wards = _build_wards()
    all_beds = [bed for ward in wards for bed in ward.beds]

    clinicians = _make_clinicians(config, rng, wards)
    patients, bed_of, required_of = _make_patients(config, rng, all_beds)

    events: list[Event] = []
    schedules: dict[ClinicianId, list[Visit]] = {}
    total_distance: dict[ClinicianId, float] = {}

    for clinician, home_ward in clinicians:
        visits, distance_m = _plan_round(
            clinician, home_ward, patients, bed_of, config, rng
        )
        schedules[clinician.id] = visits
        total_distance[clinician.id] = distance_m
        events.extend(_events_for_round(clinician, visits, bed_of, config, rng))

    ground_truth = GroundTruth(
        schedules=schedules,
        required_specialties=required_of,
        total_distance_m=total_distance,
    )
    return events, ground_truth


def _make_clinicians(
    config: SynthConfig, rng: Random, wards: tuple[Ward, ...]
) -> list[tuple[Clinician, Ward]]:
    roles = (Role.CONSULTANT, Role.REGISTRAR, Role.NURSE, Role.AHP)
    out: list[tuple[Clinician, Ward]] = []
    for i in range(config.n_clinicians):
        home_ward = wards[i % len(wards)]
        role = roles[rng.randrange(len(roles))]
        clinician = Clinician(
            id=ClinicianId(f"CLIN{i:03d}"),
            role=role,
            specialties=frozenset({home_ward.specialty}),
        )
        out.append((clinician, home_ward))
    return out


def _make_patients(
    config: SynthConfig, rng: Random, all_beds: list[Location]
) -> tuple[
    list[Patient], dict[PatientId, Location], dict[PatientId, frozenset[Specialty]]
]:
    acuities = list(Acuity)
    isolations = list(IsolationStatus)
    specialties = list(Specialty)

    patients: list[Patient] = []
    bed_of: dict[PatientId, Location] = {}
    required_of: dict[PatientId, frozenset[Specialty]] = {}

    for i in range(config.n_patients):
        patient_id = PatientId(f"PAT{i:04d}")
        patient = Patient(
            id=patient_id,
            acuity=acuities[rng.randrange(len(acuities))],
            isolation_status=isolations[rng.randrange(len(isolations))],
        )
        patients.append(patient)
        bed_of[patient_id] = all_beds[i % len(all_beds)]

        n_required = 1 if rng.random() < 0.7 else 2
        chosen = rng.sample(specialties, n_required)
        required_of[patient_id] = frozenset(chosen)

    return patients, bed_of, required_of


def _plan_round(
    clinician: Clinician,
    home_ward: Ward,
    patients: list[Patient],
    bed_of: dict[PatientId, Location],
    config: SynthConfig,
    rng: Random,
) -> tuple[list[Visit], float]:
    """Pick a round for `clinician`: sickest-first order, per SPEC-001's default
    "round-window ordering by acuity" modelling assumption."""
    k = min(config.visits_per_clinician, len(patients))
    chosen = rng.sample(patients, k)
    chosen.sort(key=lambda p: (-int(p.acuity), p.id.value))

    visits: list[Visit] = []
    current_time = config.shift_start
    current_point = home_ward.nursing_station.point
    distance_m = 0.0

    for patient in chosen:
        bed = bed_of[patient.id]
        distance_m += current_point.euclidean_distance_to(bed.point)
        visits.append(
            Visit(
                clinician_id=clinician.id,
                patient_id=patient.id,
                start_slot=current_time,
                duration=timedelta(minutes=config.visit_duration_minutes),
            )
        )
        current_time += timedelta(
            minutes=config.visit_duration_minutes + config.gap_minutes
        )
        current_point = bed.point

    return visits, distance_m


def _events_for_round(
    clinician: Clinician,
    visits: list[Visit],
    bed_of: dict[PatientId, Location],
    config: SynthConfig,
    rng: Random,
    source: str = _SOURCE,
) -> list[Event]:
    events: list[Event] = []
    for visit in visits:
        bed = bed_of[visit.patient_id]
        arrival = visit.start_slot
        departure = visit.start_slot + visit.duration
        for activity, when in (("arrive", arrival), ("depart", departure)):
            confidence = 1.0
            if rng.random() < config.low_confidence_rate:
                confidence = rng.uniform(config.low_confidence_floor, 0.79)

            timestamp = when
            if rng.random() < config.clock_skew_rate:
                skew = timedelta(
                    seconds=rng.uniform(
                        -config.clock_skew_seconds, config.clock_skew_seconds
                    )
                )
                timestamp = timestamp + skew

            event = Event(
                timestamp=timestamp,
                subject=clinician.id,
                activity=activity,
                location=bed.id,
                source=source,
                confidence=confidence,
            )
            events.append(event)

            if rng.random() < config.duplicate_read_rate:
                dup_offset = timedelta(seconds=rng.uniform(1.0, 5.0))
                events.append(
                    Event(
                        timestamp=timestamp + dup_offset,
                        subject=clinician.id,
                        activity=activity,
                        location=bed.id,
                        source=source,
                        confidence=confidence,
                    )
                )
    return events


# ---------------------------------------------------------------------------
# RequiredSpecialty evidence (SPEC-001 node N04)
#
# The oracle for the five strategies. `GroundTruth.required_specialties` is what
# is really required; this turns that into the three imperfect source types a
# hospital actually records, with each source's SPEC-001 bias as an explicit,
# tunable rate. At the defaults (perfect recall, no spurious entries, no
# unrecognisable text) all five strategies must recover the ground truth
# exactly, which is what makes the noisy cases interpretable.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceConfig:
    """Rates, not counts, for the same reason `SynthConfig` is: a generator
    parameterised by wall-clock or file-system values is not an oracle.

    The defaults are the *clean* case (everything recorded, nothing spurious).
    Each non-default rate corresponds to one row of SPEC-001's bias column:

    - `referral_stale_rate` — "teams disengage without closing the referral";
    - `consult_note_recall` below 1.0 — "under-counts verbal advice";
    - `problem_list_comorbidity_rate` — "over-triggers on historical
      comorbidity".
    """

    recorded_at: datetime = field(default=datetime(2026, 1, 5, 8, 0, 0))
    referral_recall: float = 1.0
    consult_note_recall: float = 1.0
    problem_list_recall: float = 1.0
    referral_stale_rate: float = 0.0
    problem_list_comorbidity_rate: float = 0.0
    unrecognised_text_rate: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "referral_recall",
            "consult_note_recall",
            "problem_list_recall",
            "referral_stale_rate",
            "problem_list_comorbidity_rate",
            "unrecognised_text_rate",
        ):
            rate = getattr(self, name)
            if not 0.0 <= rate <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {rate!r}")


_EVIDENCE_SOURCE_NAMES = {
    "referral": "synthetic:referral",
    "consult_note": "synthetic:consult_note",
    "problem_list": "synthetic:problem_list",
}


def generate_evidence(
    truth: GroundTruth, config: EvidenceConfig, rng: Random
) -> RequiredSpecialtyEvidence:
    """Turn known required specialties into the three imperfect evidence sources.

    Deterministic: patients are visited in id order and specialties in
    `Specialty` declaration order, so `generate_evidence(t, c, Random(42))` is
    reproducible (acceptance criterion 3, gate 8). Every draw comes from `rng`.
    """
    referrals: list[Referral] = []
    consult_notes: list[ConsultNote] = []
    problem_list: list[ProblemListEntry] = []
    all_specialties = list(Specialty)
    unrecognised_counter = 0

    def text_for(specialty: Specialty) -> str:
        nonlocal unrecognised_counter
        if rng.random() < config.unrecognised_text_rate:
            unrecognised_counter += 1
            # Free text no mapper should ever resolve — the point is that it is
            # quarantined and counted, not guessed at (criterion 5's rule).
            return f"?? unmapped clinical text {unrecognised_counter}"
        return specialty.value

    for patient in sorted(truth.required_specialties, key=lambda pid: pid.value):
        required = truth.required_specialties[patient]
        for specialty in all_specialties:
            if specialty not in required:
                continue
            if rng.random() < config.referral_recall:
                referrals.append(
                    Referral(
                        patient=patient,
                        specialty_text=text_for(specialty),
                        recorded_at=config.recorded_at,
                        source=_EVIDENCE_SOURCE_NAMES["referral"],
                    )
                )
            if rng.random() < config.consult_note_recall:
                consult_notes.append(
                    ConsultNote(
                        patient=patient,
                        specialty_text=text_for(specialty),
                        recorded_at=config.recorded_at,
                        source=_EVIDENCE_SOURCE_NAMES["consult_note"],
                    )
                )
            if rng.random() < config.problem_list_recall:
                problem_list.append(
                    ProblemListEntry(
                        patient=patient,
                        specialty_text=text_for(specialty),
                        recorded_at=config.recorded_at,
                        source=_EVIDENCE_SOURCE_NAMES["problem_list"],
                    )
                )

        for specialty in all_specialties:
            if specialty in required:
                continue
            if rng.random() < config.referral_stale_rate:
                referrals.append(
                    Referral(
                        patient=patient,
                        specialty_text=text_for(specialty),
                        recorded_at=config.recorded_at,
                        source=_EVIDENCE_SOURCE_NAMES["referral"],
                    )
                )
            if rng.random() < config.problem_list_comorbidity_rate:
                problem_list.append(
                    ProblemListEntry(
                        patient=patient,
                        specialty_text=text_for(specialty),
                        recorded_at=config.recorded_at,
                        source=_EVIDENCE_SOURCE_NAMES["problem_list"],
                    )
                )

    return RequiredSpecialtyEvidence(
        referrals=tuple(referrals),
        consult_notes=tuple(consult_notes),
        problem_list=tuple(problem_list),
    )


# ---------------------------------------------------------------------------
# Ward-days with a known day-type (SPEC-007 Part B oracle, node N20)
#
# SPEC-007 criterion 16 gates the learned trace embedding on beating the
# interpretable feature baseline "by >= 0.10 adjusted Rand index against
# `GroundTruth` day-types", and its "Test oracle" section says
# `hwpm.ingest.synthetic.GroundTruth` "knows which day-type it generated".
# It did not: `GroundTruth` carries schedules, required specialties and
# distances, and `generate` produces exactly one shift with no notion of a day
# at all, let alone a type. Criterion 17's 200-ward-day floor was likewise
# unreachable -- one call produced one day.
#
# That is a spec/code disagreement of the kind CLAUDE.md rule 1 says must be
# fixed rather than worked around, and it is fixed on the code side (the
# spec's requirement is the sound one; the oracle simply had not been built).
# The addition is deliberately a *separate* entry point rather than a change
# to `generate`: `generate`'s output is the oracle for N01, N03, N05 and N06,
# and altering it to grow a day dimension would silently move every one of
# those nodes' fixtures.
#
# The unit is the **ward-day** (SPEC-007 modelling assumption 5), so a
# day-type is drawn per `(date, ward)` rather than per date. Wards are
# independent draws and each ward's round stays inside its own beds, which is
# what makes a ward-day label an honest clustering target: with one label per
# date shared by nine wards, the "clusters" would largely be recovering the
# date.
# ---------------------------------------------------------------------------


class DayType(Enum):
    """The four generated ward-day shapes. Named after what a ward would call
    them, because the whole point of the oracle is that a recovered cluster
    can be checked against something a clinician would recognise.

    These are *generated* structure, not a claim about real wards. Nothing
    downstream may treat a recovered cluster as one of these types; the
    mapping exists only inside this module's tests and the criterion-16
    measurement.
    """

    ROUTINE = "routine"
    """A normal consultant-led round: moderate visit counts, moderate dwells."""

    POST_TAKE = "post_take"
    """Post-take: more clinicians, more visits, much shorter at each bedside."""

    MDT_DAY = "mdt_day"
    """A board/MDT day: clinicians converge on the same beds at the same
    times, so co-presence -- and therefore `MDTMoment`s -- actually happens."""

    SKELETON = "skeleton"
    """Weekend/night cover: few clinicians, few patients seen, long dwells."""


@dataclass(frozen=True)
class DayTypeProfile:
    """The generative parameters of one `DayType`.

    `convergent` is the only non-numeric knob and it is the one that produces
    a structurally different *sequence* rather than a differently-scaled one:
    convergent clinicians walk the same bed order at the same times, which is
    what an MDT day is. It is included because a trace encoder that only ever
    saw rescaled versions of one shape would be tested against nothing.
    """

    n_clinicians: int
    visits_per_clinician: int
    visit_duration_minutes: int
    gap_minutes: int
    convergent: bool


#: Deliberately overlapping: the jitter in `_jittered` is wide enough that
#: ROUTINE and MDT_DAY share visit counts and dwell ranges, and POST_TAKE and
#: SKELETON differ mainly in scale. A generator whose classes were linearly
#: separable on one feature would make criterion 16 a formality.
DAY_TYPE_PROFILES: Mapping[DayType, DayTypeProfile] = {
    DayType.ROUTINE: DayTypeProfile(3, 5, 12, 4, False),
    DayType.POST_TAKE: DayTypeProfile(4, 6, 5, 2, False),
    DayType.MDT_DAY: DayTypeProfile(3, 5, 13, 4, True),
    DayType.SKELETON: DayTypeProfile(2, 3, 24, 9, False),
}

#: Beds per ward in the reference geometry, hence patients per ward.
_BEDS_PER_WARD = len(_BED_LOCAL_XZ)

#: Roster size per ward. Ordered so that taking the first `n` for any `n >= 2`
#: always yields at least one holder of the ward specialty and one of the
#: visiting specialty -- otherwise a SKELETON day could draw two clinicians of
#: one specialty and no MDT would be possible on any day of that ward.
_ROSTER_PER_WARD = 5


@dataclass(frozen=True)
class WardDayGroundTruth:
    """What the ward-day generator knows and the analysis must recover.

    `day_types` is the criterion-16 oracle. The remaining fields are the joins
    `hwpm.mining.attach_patients` / `attach_clinician_specialties` need, which
    are not derivable from a clinician's event trajectory (see
    `hwpm.mining.episodes`) and would otherwise have to be guessed at.
    """

    day_types: dict[tuple[date, str], DayType]
    occupancy: dict[LocationId, PatientId]
    required_specialties: dict[PatientId, frozenset[Specialty]]
    clinician_specialties: dict[ClinicianId, frozenset[Specialty]]
    patients: tuple[Patient, ...]
    visits: dict[tuple[date, str], list[Visit]]

    @property
    def n_ward_days(self) -> int:
        return len(self.day_types)


@dataclass(frozen=True)
class WardDayConfig:
    """Ward-day generation parameters. `noise` reuses `SynthConfig` rather
    than restating four rates, so there is one definition of what "clock skew"
    means in this module."""

    n_days: int = 24
    first_day: date = field(default=date(2026, 1, 5))
    day_start_hour: int = 8
    noise: SynthConfig = field(default_factory=SynthConfig)
    #: Restrict generation to these ward ids. `None` means all nine.
    ward_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.n_days < 1:
            raise ValueError(f"n_days must be >= 1, got {self.n_days!r}")
        if not 0 <= self.day_start_hour <= 23:
            raise ValueError(
                f"day_start_hour must be in [0, 23], got {self.day_start_hour!r}"
            )


def _ward_roster(ward: Ward, wards: tuple[Ward, ...]) -> list[Clinician]:
    """This ward's clinicians, alternating the ward specialty and a visiting
    one so that a multi-specialty patient can actually be covered."""
    index = wards.index(ward)
    visiting = wards[(index + 1) % len(wards)].specialty
    roles = (Role.CONSULTANT, Role.REGISTRAR, Role.NURSE, Role.AHP, Role.REGISTRAR)
    out: list[Clinician] = []
    for i in range(_ROSTER_PER_WARD):
        specialty = ward.specialty if i % 2 == 0 else visiting
        out.append(
            Clinician(
                id=ClinicianId(f"CLIN-{ward.ward_id}-{i}"),
                role=roles[i % len(roles)],
                specialties=frozenset({specialty}),
            )
        )
    return out


def _ward_patients(
    ward: Ward, wards: tuple[Ward, ...], rng: Random
) -> tuple[
    list[Patient],
    dict[LocationId, PatientId],
    dict[PatientId, frozenset[Specialty]],
]:
    """One patient per bed, occupying it for the whole run.

    Same simplification `SynthConfig` already makes ("assigns each patient one
    bed for the whole run", `hwpm.mining.types.BedOccupancy`); modelling ADT
    movement is a separate piece of work and faking it here would put motion
    into the ward-day features that no event supports.
    """
    index = wards.index(ward)
    visiting = wards[(index + 1) % len(wards)].specialty
    acuities = list(Acuity)
    patients: list[Patient] = []
    occupancy: dict[LocationId, PatientId] = {}
    required: dict[PatientId, frozenset[Specialty]] = {}
    for bed_index, bed in enumerate(ward.beds):
        pid = PatientId(f"PAT-{ward.ward_id}-{bed_index}")
        patients.append(
            Patient(
                id=pid,
                acuity=acuities[rng.randrange(len(acuities))],
                isolation_status=IsolationStatus.NONE,
            )
        )
        occupancy[bed.id] = pid
        # Two thirds multi-specialty: the coverage denominator (ADR-0006)
        # needs to be non-trivial on every ward-day, and a ward where nobody
        # needs an MDT makes both the metric and the MDT_DAY day-type vacuous.
        required[pid] = (
            frozenset({ward.specialty, visiting})
            if bed_index < 4
            else frozenset({ward.specialty})
        )
    return patients, occupancy, required


def _jittered(profile: DayTypeProfile, rng: Random) -> DayTypeProfile:
    """One ward-day's realised parameters. Wide enough that the day-types
    overlap -- see the note on `DAY_TYPE_PROFILES`."""
    return DayTypeProfile(
        n_clinicians=min(
            _ROSTER_PER_WARD, max(2, profile.n_clinicians + rng.choice((-1, 0, 1)))
        ),
        visits_per_clinician=min(
            _BEDS_PER_WARD,
            max(2, profile.visits_per_clinician + rng.choice((-1, 0, 1))),
        ),
        visit_duration_minutes=max(
            2, round(profile.visit_duration_minutes * rng.uniform(0.7, 1.3))
        ),
        gap_minutes=max(1, round(profile.gap_minutes * rng.uniform(0.7, 1.3))),
        convergent=profile.convergent,
    )


def _ward_day_visits(
    roster: list[Clinician],
    patients: list[Patient],
    day: date,
    profile: DayTypeProfile,
    config: WardDayConfig,
    rng: Random,
) -> list[tuple[Clinician, list[Visit]]]:
    """Plan one ward-day's rounds.

    Convergent days give every clinician the *same* bed order at the *same*
    times, so `hwpm.mining.detect_mdt_moments` finds real co-presence.

    Non-convergent days need more care than "stagger the start times", which
    was the first attempt here and did not work, and a rotate-and-stagger
    scheme was the second attempt and did not work either. Both are recorded
    because the failure is instructive: SPEC-002's co-presence rule clusters
    bedside episodes separated by up to `window_s` (300 s by default), so two
    clinicians rounding one behind the other collide at every bed inside the
    window. Measured on 216 generated ward-days, the staggered version gave
    POST_TAKE *more* MDT moments per day (3.98) than MDT_DAY (3.41), and the
    rotated version made it worse (4.95 against 3.29), because rotating by
    `2i` modulo `k` collides for `i >= k/2`. Either way the day-type would
    have been recoverable only from scale, and criterion 16's oracle would
    have been testing something much weaker than it claims.

    What is used instead: on a non-convergent day the teams round
    **sequentially** -- clinician `i` starts only after clinician `i-1` has
    finished -- so no two clinicians are on the ward at once and co-presence
    is structurally impossible. That is also the more realistic model of a
    ward where the consultant round, the therapy round and the pharmacy round
    happen at different times of day, and it makes co-presence the one thing
    that genuinely distinguishes MDT_DAY. The scale features (episode counts,
    dwell, span) still overlap heavily across all four types after jitter, so
    the clustering problem stays a real one.
    """
    start = datetime.combine(day, time(config.day_start_hour, 0))
    working = roster[: profile.n_clinicians]
    step = profile.visit_duration_minutes + profile.gap_minutes

    chosen = rng.sample(patients, profile.visits_per_clinician)
    chosen.sort(key=lambda p: (-int(p.acuity), p.id.value))
    shared_order = [p.id for p in chosen]
    k = len(shared_order)

    out: list[tuple[Clinician, list[Visit]]] = []
    for offset_index, clinician in enumerate(working):
        if profile.convergent:
            order = list(shared_order)
            clock = start
        else:
            rotation = (2 * offset_index) % k
            order = shared_order[rotation:] + shared_order[:rotation]
            clock = start + timedelta(minutes=offset_index * k * step)
        visits: list[Visit] = []
        for pid in order:
            visits.append(
                Visit(
                    clinician_id=clinician.id,
                    patient_id=pid,
                    start_slot=clock,
                    duration=timedelta(minutes=profile.visit_duration_minutes),
                )
            )
            clock += timedelta(
                minutes=profile.visit_duration_minutes + profile.gap_minutes
            )
        out.append((clinician, visits))
    return out


def generate_ward_days(
    config: WardDayConfig, rng: Random
) -> tuple[list[Event], WardDayGroundTruth]:
    """Generate `n_days` x `len(wards)` ward-days, each with a known `DayType`.

    Every draw comes from `rng` in a fixed order (wards outer, days inner,
    then clinicians), so `generate_ward_days(config, Random(7))` is
    reproducible -- the same guarantee `generate` carries, and the one
    criterion 19 rests on.

    Returns the flat `Event` stream (which is what `hwpm.mining` consumes)
    plus the ground truth. Deriving episodes from it is the caller's job and
    is deliberately not done here: episode derivation has its own tunable
    `EpisodeParams`, and a generator that pre-derived them would hide the
    parameter sensitivity SPEC-002 exists to expose.
    """
    all_wards = _build_wards()
    wards = all_wards
    if config.ward_ids is not None:
        wanted = set(config.ward_ids)
        wards = tuple(w for w in all_wards if w.ward_id in wanted)
        if not wards:
            raise ValueError(f"no ward matches ward_ids={config.ward_ids!r}")

    day_types: dict[tuple[date, str], DayType] = {}
    occupancy: dict[LocationId, PatientId] = {}
    required: dict[PatientId, frozenset[Specialty]] = {}
    clinician_specialties: dict[ClinicianId, frozenset[Specialty]] = {}
    all_patients: list[Patient] = []
    visits_by_ward_day: dict[tuple[date, str], list[Visit]] = {}
    events: list[Event] = []

    types = list(DayType)
    for ward in wards:
        roster = _ward_roster(ward, all_wards)
        for clinician in roster:
            clinician_specialties[clinician.id] = clinician.specialties
        patients, ward_occupancy, ward_required = _ward_patients(ward, all_wards, rng)
        all_patients.extend(patients)
        occupancy.update(ward_occupancy)
        required.update(ward_required)
        location_of = {
            pid: bed
            for bed_location in ward.beds
            for bed, pid in [(bed_location, ward_occupancy[bed_location.id])]
        }

        for offset in range(config.n_days):
            day = config.first_day + timedelta(days=offset)
            day_type = types[rng.randrange(len(types))]
            key = (day, ward.ward_id)
            day_types[key] = day_type
            profile = _jittered(DAY_TYPE_PROFILES[day_type], rng)
            planned = _ward_day_visits(roster, patients, day, profile, config, rng)
            day_visits: list[Visit] = []
            for clinician, visits in planned:
                day_visits.extend(visits)
                events.extend(
                    _events_for_round(
                        clinician,
                        visits,
                        location_of,
                        config.noise,
                        rng,
                        source=_WARD_DAY_SOURCE,
                    )
                )
            visits_by_ward_day[key] = day_visits

    truth = WardDayGroundTruth(
        day_types=day_types,
        occupancy=occupancy,
        required_specialties=required,
        clinician_specialties=clinician_specialties,
        patients=tuple(all_patients),
        visits=visits_by_ward_day,
    )
    return events, truth
