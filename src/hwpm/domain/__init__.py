"""Domain layer — the stable core. Depends on nothing; everything else depends
inward on it (02-ARCHITECTURE.md, enforced by import-linter, gate 6).

Re-exports the flat namespace SPEC-001 writes against (`from hwpm.domain import
Event, Location, ...`); the implementation lives in `model.py` so this file
stays a pure export list.
"""

from __future__ import annotations

from hwpm.domain.model import (
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
    SubjectId,
    Trajectory,
    Transition,
    Visit,
)

__all__ = [
    "Acuity",
    "Clinician",
    "ClinicianId",
    "Event",
    "IsolationStatus",
    "Location",
    "LocationId",
    "LocationKind",
    "Patient",
    "PatientId",
    "Point",
    "Role",
    "Specialty",
    "SubjectId",
    "Trajectory",
    "Transition",
    "Visit",
]
