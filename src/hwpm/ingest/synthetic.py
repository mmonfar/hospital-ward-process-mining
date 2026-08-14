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

from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
                source=_SOURCE,
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
                        source=_SOURCE,
                        confidence=confidence,
                    )
                )
    return events
