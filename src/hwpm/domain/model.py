"""Frozen domain types. SPEC-001, 01-DOMAIN-MODEL.md.

Scope note (N02): SPEC-001's interface block names `LocationId`, `Location`,
`Event` and `Trajectory` explicitly; `Point`, `LocationKind`, `SubjectId`,
`ClinicianId`, `PatientId`, `Role`, `Specialty`, `Acuity`, `IsolationStatus`,
`Clinician`, `Patient`, `Transition` and `Visit` are added because
`hwpm.ingest.synthetic.GroundTruth` (also SPEC-001) needs them. `Schedule` and
`Constraint` are described in 01-DOMAIN-MODEL.md but belong to SPEC-004 nodes
(N08-N11) and are deliberately not built here — implementing them now would be
building ahead of the spec that constrains them.

Scope note (N05): `BedsideEpisode`, `Round` and `MDTMoment` were the other
three deferred by the note above, to SPEC-002 / N05. SPEC-002 is now accepted
and N05 is the node that builds them, so they are added here rather than in
`hwpm.mining` — 01-DOMAIN-MODEL.md's "Entities" section describes them as
domain vocabulary (ubiquitous language table: "Bedside episode", "MDT moment"),
and `hwpm.mining` (SPEC-002's derivation/discovery/conformance functions)
depends on them rather than owning them, the same relationship `hwpm.mining`
has with `Event` and `Trajectory`.

Rule 1 of 01-DOMAIN-MODEL.md: frozen by default. Rule 2: no I/O. This module
imports nothing outside the standard library; import-linter enforces the rest
(gate 6, docs/06-QA-AND-DEADCODE.md).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum, IntEnum
from itertools import pairwise

# ---------------------------------------------------------------------------
# Place
# ---------------------------------------------------------------------------


class LocationKind(Enum):
    """One level of the Site > Building > Floor > Ward > Bay > Bed hierarchy.

    01-DOMAIN-MODEL.md, "Place". An isolation room is modelled as a `Bay` of
    size one, not a separate kind.
    """

    SITE = "site"
    BUILDING = "building"
    FLOOR = "floor"
    WARD = "ward"
    BAY = "bay"
    BED = "bed"
    NURSING_STATION = "nursing_station"
    CORRIDOR = "corridor"


@dataclass(frozen=True)
class Point:
    """A raw 3D coordinate.

    Not a travel cost. 01-DOMAIN-MODEL.md is explicit that "travel cost between
    locations is a graph problem, not a Euclidean one" — `TravelGraph.cost(a, b)`
    (SPEC-003) is the sanctioned way for application code to reason about
    distance. `euclidean_distance_to` exists only for the synthetic generator's
    ground truth, which by design uses straight-line distance as the interim
    assumption 01-DOMAIN-MODEL.md records as "the prototype's" and SPEC-003 will
    replace.
    """

    x: float
    y: float
    z: float

    def euclidean_distance_to(self, other: Point) -> float:
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))


@dataclass(frozen=True)
class LocationId:
    value: str


@dataclass(frozen=True)
class Location:
    id: LocationId
    kind: LocationKind
    point: Point


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectId:
    """Whoever an `Event` is observed about. Never a name (01-DOMAIN-MODEL.md).

    `ClinicianId` and `PatientId` are distinct subtypes rather than both being
    bare `SubjectId`s, so that a clinician id and a patient id are not
    interchangeable by accident — that is exactly the kind of mix-up that
    produces a double-counting bug in a `MDTMoment`.
    """

    value: str


@dataclass(frozen=True)
class ClinicianId(SubjectId):
    pass


@dataclass(frozen=True)
class PatientId(SubjectId):
    pass


class Role(Enum):
    CONSULTANT = "consultant"
    REGISTRAR = "registrar"
    NURSE = "nurse"
    AHP = "ahp"
    OTHER = "other"


class Specialty(Enum):
    """Clinical specialty. Matches the nine wards in `web/hospital-ward.html`'s
    `WARDS` array — that layout is the reference geometry until real floor
    plans are ingested (01-DOMAIN-MODEL.md)."""

    CARDIOLOGY = "cardiology"
    NEPHROLOGY = "nephrology"
    GENERAL_MEDICINE = "general_medicine"
    SURGICAL = "surgical"
    ICU = "icu"
    RECOVERY = "recovery"
    ONCOLOGY = "oncology"
    RENAL = "renal"
    REHABILITATION = "rehabilitation"


class Acuity(IntEnum):
    """Higher is sicker. Backs the default "sickest first" round ordering
    assumption in SPEC-001's modelling-assumptions table."""

    LOW = 1
    MODERATE = 2
    HIGH = 3
    CRITICAL = 4


class IsolationStatus(Enum):
    """Transmission-based precaution category, or none. Any value other than
    `NONE` is what a future `IsolationLast` constraint (01-DOMAIN-MODEL.md)
    keys off."""

    NONE = "none"
    CONTACT = "contact"
    DROPLET = "droplet"
    AIRBORNE = "airborne"


@dataclass(frozen=True)
class Clinician:
    id: ClinicianId
    role: Role
    specialties: frozenset[Specialty]


@dataclass(frozen=True)
class Patient:
    """Patient identity and clinical state.

    Deliberately does **not** carry `required_specialties`. 01-DOMAIN-MODEL.md
    originally described that as a `Patient` field; SPEC-001 later resolved (see
    docs/AUDIT-LOG.md, 2026-08-14, "Resolved N04 gate") that `RequiredSpecialty`
    is a runtime-selectable analysis parameter computed by one of five
    `RequiredSpecialtyStrategy` implementations over persisted evidence
    (referrals, consult notes, problem list), not a fixed value baked in at
    ingestion. Putting it on `Patient` here would encode one strategy into the
    domain layer and contradict "the ingestion stage persists the evidence
    rather than the conclusion" (SPEC-001, "Architectural consequence" #3).
    `01-DOMAIN-MODEL.md` should be read with this amendment; N04 owns the
    strategy types.
    """

    id: PatientId
    acuity: Acuity
    isolation_status: IsolationStatus


# ---------------------------------------------------------------------------
# Time and observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """One observation: `(timestamp, subject, activity, location, source,
    confidence)`. The whole system is a fold over an ordered stream of these
    (01-DOMAIN-MODEL.md). `confidence` < 0.8 is the location-mapper quarantine
    threshold from SPEC-001; it is not enforced here — enforcing it is N03's
    job — but the field exists so the evidence travels with the event."""

    timestamp: datetime
    subject: SubjectId
    activity: str
    location: LocationId
    source: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence!r}")


@dataclass(frozen=True)
class Transition:
    """Movement between two locations (01-DOMAIN-MODEL.md ubiquitous language).

    A pair of consecutive same-location events is not a transition — nobody
    moved — so `Trajectory.transitions()` never yields one where
    `origin == destination`.
    """

    subject: SubjectId
    origin: LocationId
    destination: LocationId
    departed_at: datetime
    arrived_at: datetime

    @property
    def duration(self) -> timedelta:
        return self.arrived_at - self.departed_at


@dataclass(frozen=True)
class Trajectory:
    """The ordered `Event` sequence for one subject over one period.

    Events are taken in the order given, not re-sorted: "non-monotonic
    sequences are flagged, not reordered" (SPEC-001). Flagging is an ingestion
    concern (N03); this class only walks the sequence it is handed.
    """

    subject: SubjectId
    events: tuple[Event, ...]

    def transitions(self) -> Iterator[Transition]:
        for prev, nxt in pairwise(self.events):
            if prev.location != nxt.location:
                yield Transition(
                    subject=self.subject,
                    origin=prev.location,
                    destination=nxt.location,
                    departed_at=prev.timestamp,
                    arrived_at=nxt.timestamp,
                )


# ---------------------------------------------------------------------------
# Schedule (partial — only what SPEC-001's GroundTruth needs; `Schedule` and
# `Constraint` proper are SPEC-004's, see the module docstring)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Visit:
    """A *planned* bedside episode: `(clinicianId, patientId, startSlot,
    duration)`. Distinct from a `BedsideEpisode` (observed, SPEC-002) — planned
    and observed things must never be the same class (01-DOMAIN-MODEL.md), or
    counterfactual analysis silently compares a plan against itself."""

    clinician_id: ClinicianId
    patient_id: PatientId
    start_slot: datetime
    duration: timedelta


# ---------------------------------------------------------------------------
# Derived entities (SPEC-002, N05-mining). See the N05 scope note at the top
# of this module for why these live here rather than in `hwpm.mining`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BedsideEpisode:
    """A contiguous period a clinician spends at one location
    (01-DOMAIN-MODEL.md, "Bedside episode"). Derivation from raw events
    (dwell-time thresholds, sensor noise) is SPEC-002's job and is explicitly
    a modelling assumption with a tunable parameter, not a fact — see
    `hwpm.mining.EpisodeParams`.

    `patient` and `clinician_specialties` are **not** filled in by derivation
    itself: a clinician's raw trajectory names a location, not who occupied
    it, and bed occupancy is a separate feed even in the synthetic generator
    (`hwpm.ingest.synthetic` never emits a patient-location event). They stay
    `None` / empty until an explicit join (`hwpm.mining.attach_patients`,
    `attach_clinician_specialties`) runs, rather than being guessed at, so
    that "not yet known" is never silently indistinguishable from "known and
    empty" — 01-DOMAIN-MODEL.md rule 4: every derived entity carries
    provenance.
    """

    clinician: ClinicianId
    bed: LocationId
    start: datetime
    end: datetime
    confidence: float
    source_events: tuple[Event, ...]
    patient: PatientId | None = None
    clinician_specialties: frozenset[Specialty] = frozenset()

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"episode end {self.end!r} precedes start {self.start!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence!r}")

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True)
class Round:
    """One clinician's ordered sweep of bedside episodes on one day
    (01-DOMAIN-MODEL.md, "Round"). `episodes` is ordered by start time —
    producing that order is `hwpm.mining.reconstruct_rounds`'s job; this type
    only carries the result."""

    clinician: ClinicianId
    day: date
    episodes: tuple[BedsideEpisode, ...]


@dataclass(frozen=True)
class MDTMoment:
    """>=2 required specialties co-present at one bedside (01-DOMAIN-MODEL.md,
    "MDT moment"). `satisfied_specialties` is the subset of
    `present_specialties` that intersects the patient's required specialties.

    01-DOMAIN-MODEL.md is explicit: "A `MDTMoment` that satisfies nothing is
    not an MDT moment; it is two people who happened to collide." Read
    together with the ubiquitous-language table's "≥2 required specialties
    co-present", that means fewer than two satisfied specialties does not
    just make a weak MDT moment — it makes something that is not one at all.
    Constructing one with fewer than two is therefore a `ValueError`, not a
    caller convention to remember.
    """

    bed: LocationId
    patient: PatientId
    start: datetime
    end: datetime
    present_specialties: frozenset[Specialty]
    satisfied_specialties: frozenset[Specialty]

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"moment end {self.end!r} precedes start {self.start!r}")
        if not self.satisfied_specialties <= self.present_specialties:
            raise ValueError(
                "satisfied_specialties must be a subset of present_specialties"
            )
        if len(self.satisfied_specialties) < 2:
            got = sorted(s.value for s in self.satisfied_specialties)
            raise ValueError(
                "an MDTMoment needs >=2 satisfied required specialties co-present "
                f"(01-DOMAIN-MODEL.md); got {got}"
            )
