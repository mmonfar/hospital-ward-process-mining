"""Domain layer — the stable core. Depends on nothing; everything else depends
inward on it (02-ARCHITECTURE.md, enforced by import-linter, gate 6).

Re-exports the flat namespace SPEC-001 writes against (`from hwpm.domain import
Event, Location, ...`); the implementation lives in `model.py` so this file
stays a pure export list.
"""

from __future__ import annotations

from hwpm.domain.model import (
    Acuity,
    BedsideEpisode,
    Clinician,
    ClinicianId,
    Event,
    IsolationStatus,
    Location,
    LocationId,
    LocationKind,
    MDTMoment,
    Patient,
    PatientId,
    Point,
    Role,
    Round,
    Specialty,
    SubjectId,
    Trajectory,
    Transition,
    Visit,
)
from hwpm.domain.specialty import (
    DEFAULT_MDT_CONFIDENCE_THRESHOLD,
    DEFAULT_STRATEGY,
    N_EVIDENCE_SOURCES,
    SINGLE_SOURCE_STRATEGIES,
    Calibration,
    Corroboration,
    EvidenceSource,
    MDTSelection,
    RequiredSpecialtyDetermination,
    RequiredSpecialtyStrategyKey,
    SpecialtyClaim,
)

__all__ = [
    "DEFAULT_MDT_CONFIDENCE_THRESHOLD",
    "DEFAULT_STRATEGY",
    "N_EVIDENCE_SOURCES",
    "SINGLE_SOURCE_STRATEGIES",
    "Acuity",
    "BedsideEpisode",
    "Calibration",
    "Clinician",
    "ClinicianId",
    "Corroboration",
    "Event",
    "EvidenceSource",
    "IsolationStatus",
    "Location",
    "LocationId",
    "LocationKind",
    "MDTMoment",
    "MDTSelection",
    "Patient",
    "PatientId",
    "Point",
    "RequiredSpecialtyDetermination",
    "RequiredSpecialtyStrategyKey",
    "Role",
    "Round",
    "Specialty",
    "SpecialtyClaim",
    "SubjectId",
    "Trajectory",
    "Transition",
    "Visit",
]
