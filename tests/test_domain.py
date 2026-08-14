"""Domain-layer tests. SPEC-001 acceptance criteria 1-2. N02-domain-core.

Pure unit tests, no I/O (docs/06-QA-AND-DEADCODE.md, "Test strategy: Domain").
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta

import pytest

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
    Trajectory,
    Visit,
)

T0 = datetime(2026, 1, 5, 8, 0, 0)


def _event(
    activity: str, location: str, when: datetime, confidence: float = 1.0
) -> Event:
    return Event(
        timestamp=when,
        subject=ClinicianId("CLIN000"),
        activity=activity,
        location=LocationId(location),
        source="synthetic",
        confidence=confidence,
    )


# --------------------------------------------------------------------------
# Criterion 1: frozen, mutation raises
# --------------------------------------------------------------------------


def test_domain_immutable() -> None:
    event = _event("arrive", "1A/BED0", T0)
    with pytest.raises(FrozenInstanceError):
        event.activity = "depart"  # type: ignore[misc]

    location = Location(
        id=LocationId("1A/BED0"), kind=LocationKind.BED, point=Point(0, 0, 0)
    )
    with pytest.raises(FrozenInstanceError):
        location.kind = LocationKind.CORRIDOR  # type: ignore[misc]

    patient = Patient(
        id=PatientId("PAT0001"), acuity=Acuity.HIGH, isolation_status=IsolationStatus.NONE
    )
    with pytest.raises(FrozenInstanceError):
        patient.acuity = Acuity.LOW  # type: ignore[misc]

    clinician = Clinician(
        id=ClinicianId("CLIN000"), role=Role.NURSE, specialties=frozenset()
    )
    with pytest.raises(FrozenInstanceError):
        clinician.role = Role.CONSULTANT  # type: ignore[misc]

    visit = Visit(
        clinician_id=ClinicianId("CLIN000"),
        patient_id=PatientId("PAT0001"),
        start_slot=T0,
        duration=timedelta(minutes=10),
    )
    with pytest.raises(FrozenInstanceError):
        visit.start_slot = T0 + timedelta(hours=1)  # type: ignore[misc]


def test_event_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        _event("arrive", "1A/BED0", T0, confidence=1.5)
    with pytest.raises(ValueError, match="confidence"):
        _event("arrive", "1A/BED0", T0, confidence=-0.1)


def test_clinician_id_and_patient_id_are_distinct_types() -> None:
    """A ClinicianId and a PatientId with the same string are not the same
    subject — that mix-up is exactly what SubjectId subtyping exists to
    prevent (01-DOMAIN-MODEL.md)."""
    clinician_id = ClinicianId("X001")
    patient_id = PatientId("X001")
    assert clinician_id != patient_id
    assert type(clinician_id) is not type(patient_id)


def test_patient_has_no_required_specialties_field() -> None:
    """RequiredSpecialty is a runtime-selectable analysis parameter (N04), not
    a fixed Patient attribute. See the amendment note in domain/model.py."""
    patient = Patient(
        id=PatientId("PAT0001"), acuity=Acuity.HIGH, isolation_status=IsolationStatus.NONE
    )
    assert not hasattr(patient, "required_specialties")


# --------------------------------------------------------------------------
# Point / geometry
# --------------------------------------------------------------------------


def test_point_euclidean_distance() -> None:
    a = Point(0, 0, 0)
    b = Point(3, 4, 0)
    assert a.euclidean_distance_to(b) == pytest.approx(5.0)
    assert a.euclidean_distance_to(a) == 0.0


# --------------------------------------------------------------------------
# Trajectory / Transition
# --------------------------------------------------------------------------


def test_trajectory_transitions_skip_same_location_pairs() -> None:
    events = (
        _event("arrive", "1A/BED0", T0),
        _event("depart", "1A/BED0", T0 + timedelta(minutes=10)),
        _event("arrive", "1A/BED1", T0 + timedelta(minutes=13)),
        _event("depart", "1A/BED1", T0 + timedelta(minutes=20)),
    )
    trajectory = Trajectory(subject=ClinicianId("CLIN000"), events=events)
    transitions = list(trajectory.transitions())

    assert len(transitions) == 1
    (t,) = transitions
    assert t.origin == LocationId("1A/BED0")
    assert t.destination == LocationId("1A/BED1")
    assert t.departed_at == T0 + timedelta(minutes=10)
    assert t.arrived_at == T0 + timedelta(minutes=13)
    assert t.duration == timedelta(minutes=3)


def test_trajectory_no_transitions_for_single_event() -> None:
    trajectory = Trajectory(
        subject=ClinicianId("CLIN000"), events=(_event("arrive", "1A/BED0", T0),)
    )
    assert list(trajectory.transitions()) == []


def test_specialty_enum_matches_reference_wards() -> None:
    """Sanity check against the nine wards in web/hospital-ward.html: this
    enum must have exactly one member per ward specialty."""
    assert len(Specialty) == 9
