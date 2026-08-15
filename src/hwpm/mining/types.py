"""Types and Protocols for `hwpm.mining`. SPEC-002, ADR-0008, node N05-mining.

This module is the ADR-0008 boundary's inward face: `ProcessDiscovery` and
`ConformanceChecker` are "our own Protocols declared in `hwpm.mining`" (ADR-0008
§1), and every other type here exists so the rest of the codebase can talk
about a discovered process, a conformance result, or an event log without ever
importing `pm4py` -- only `hwpm/mining/_pm4py_adapter.py` may do that.

`ProcessModel.engine_payload` is the one deliberately loose seam: it is typed
`object`, not any pm4py class, so the discovery engine that produced a model
has somewhere to stash whatever it needs to re-run conformance later (for the
pm4py adapter: a Petri net plus its initial/final markings), without that
shape leaking into this module's public signature. Nothing outside
`_pm4py_adapter.py` is expected to interpret it -- ADR-0008: "pm4py types must
not leak through the Protocol into the rest of the codebase."

Frozen throughout, per 01-DOMAIN-MODEL.md rule 1 and CLAUDE.md's house style.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hwpm.domain import ClinicianId, LocationId, PatientId, Specialty

# ---------------------------------------------------------------------------
# Episode derivation parameters (SPEC-002 interface block, verbatim)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeParams:
    """Every field a tunable modelling assumption, not a fact (SPEC-002,
    "Modelling assumptions"). Defaults match the spec's table."""

    min_dwell_s: int = 120
    max_gap_s: int = 90
    min_confidence: float = 0.8

    def __post_init__(self) -> None:
        if self.min_dwell_s < 0:
            raise ValueError(f"min_dwell_s must be >= 0, got {self.min_dwell_s!r}")
        if self.max_gap_s < 0:
            raise ValueError(f"max_gap_s must be >= 0, got {self.max_gap_s!r}")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence must be in [0, 1], got {self.min_confidence!r}"
            )


#: Bed occupancy at derivation time: which patient a bed belongs to. A plain
#: mapping rather than an interval index -- real ADT-fed occupancy that moves
#: over time is future scope (it is not modelled by `hwpm.ingest.synthetic`
#: today either: SynthConfig assigns each patient one bed for the whole run).
#: See `hwpm.mining.episodes.attach_patients`.
BedOccupancy = Mapping[LocationId, PatientId]

#: Which specialties a clinician carries, for `attach_clinician_specialties`.
ClinicianSpecialties = Mapping[ClinicianId, frozenset[Specialty]]


# ---------------------------------------------------------------------------
# Report provenance (acceptance criterion 7: "All parameters appear in the
# report header").
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportHeader:
    """Provenance for a mining report: every tunable parameter that produced
    it (SPEC-002 acceptance criterion 7; 01-DOMAIN-MODEL.md rule 4, "every
    derived entity carries provenance"). `parameters` holds primitives only
    (str/int/float/bool), so a header can always be serialised as-is."""

    parameters: Mapping[str, object]
    generated_at: datetime


# ---------------------------------------------------------------------------
# Event log -- our own shape, never pm4py's `EventLog`/`DataFrame`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogEvent:
    """One row of a process-mining log: which case, what happened, when."""

    case_id: str
    activity: str
    timestamp: datetime
    resource: str | None = None


@dataclass(frozen=True)
class EventLog:
    """A flat, case-tagged event stream -- the ward-motion layer's own
    representation of "a log fed to a discovery/conformance engine"
    (SPEC-002: "our contribution is the ward-motion layer"). Translating this
    into and out of whatever `pm4py` wants is entirely `_pm4py_adapter.py`'s
    job; nothing else needs to know pm4py exists.
    """

    events: tuple[LogEvent, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.events, key=lambda e: (e.case_id, e.timestamp)))
        object.__setattr__(self, "events", ordered)

    def cases(self) -> dict[str, tuple[LogEvent, ...]]:
        """Events grouped by case, each case's events time-ordered (guaranteed
        by `__post_init__`'s sort)."""
        out: dict[str, list[LogEvent]] = {}
        for event in self.events:
            out.setdefault(event.case_id, []).append(event)
        return {case_id: tuple(events) for case_id, events in out.items()}

    def case_ids(self) -> frozenset[str]:
        return frozenset(event.case_id for event in self.events)


# ---------------------------------------------------------------------------
# Discovered model and conformance result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessModel:
    """A discovered process model, described structurally (a directly-follows
    graph over activity labels, plus start/end activities) so that reporting
    and the ward-motion layer can reason about "what does the process look
    like" without ever touching an engine-specific representation.

    `engine_payload` is the ADR-0008 seam -- see the module docstring.
    """

    algorithm: str
    activities: frozenset[str]
    directly_follows: frozenset[tuple[str, str]]
    start_activities: frozenset[str]
    end_activities: frozenset[str]
    engine_payload: object = None


@dataclass(frozen=True)
class ConformanceReport:
    """Fitness/precision plus, per acceptance criterion 6, an explicit split
    between *deviation* (the model was violated: something happened that the
    process does not allow, in some order it does not allow) and *missing
    data* (nothing violated the model; the case simply stopped before
    reaching a valid end -- truncated ingestion, not a process breach).

    `header` carries the parameters that produced this report (criterion 7).
    """

    fitness: float
    precision: float
    deviating_cases: frozenset[str]
    missing_data_cases: frozenset[str]
    header: ReportHeader

    def __post_init__(self) -> None:
        if not 0.0 <= self.fitness <= 1.0:
            raise ValueError(f"fitness must be in [0, 1], got {self.fitness!r}")
        if not 0.0 <= self.precision <= 1.0:
            raise ValueError(f"precision must be in [0, 1], got {self.precision!r}")
        overlap = self.deviating_cases & self.missing_data_cases
        if overlap:
            raise ValueError(
                "a case cannot be both a deviation and missing data at once: "
                f"{sorted(overlap)}"
            )


# ---------------------------------------------------------------------------
# Sensitivity analysis (acceptance criterion 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SensitivityPoint:
    min_dwell_s: int
    episode_count: int
    recall: float | None  # against a known visit count, when one is supplied


@dataclass(frozen=True)
class SensitivityReport:
    """Episode counts (and recall, where a ground truth is available) across
    the mandated `min_dwell_s` sweep -- criterion 2: emitted automatically,
    not only when asked for."""

    points: tuple[SensitivityPoint, ...]
    header: ReportHeader

    def stable_within(self, tolerance: float) -> bool:
        """Whether recall stays within `tolerance` of its own mean across the
        sweep -- SPEC-002: "any headline figure must be shown to be stable
        across that range or reported with the range." Returns True (vacuously
        stable) if no point carries a recall figure to compare."""
        recalls = [p.recall for p in self.points if p.recall is not None]
        if not recalls:
            return True
        mean = sum(recalls) / len(recalls)
        return all(abs(r - mean) <= tolerance for r in recalls)


#: The sweep acceptance criterion 2 mandates, verbatim.
DEFAULT_SENSITIVITY_DWELL_VALUES: tuple[int, ...] = (60, 120, 180, 300)


# ---------------------------------------------------------------------------
# Opportunistic MDT detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MissedMDTOpportunity:
    """A *candidate*, never a claimed miss (SPEC-002: "reported as candidate
    opportunities with an explicit false-positive rate... never as 'missed
    reviews'"). `reason` is a short human-readable justification -- the
    distance and window that produced the candidate -- because a clinician
    disputing this number needs to see what produced it (01-DOMAIN-MODEL.md
    rule 4)."""

    clinician: ClinicianId
    patient: PatientId
    specialty: Specialty
    bed: LocationId
    at: datetime
    proximity_m: float
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence!r}")
        if self.proximity_m < 0:
            raise ValueError(f"proximity_m must be >= 0, got {self.proximity_m!r}")


# ---------------------------------------------------------------------------
# ADR-0008 Protocols
# ---------------------------------------------------------------------------


class ProcessDiscovery(Protocol):
    def discover(self, log: EventLog) -> ProcessModel: ...


class ConformanceChecker(Protocol):
    def check(self, log: EventLog, model: ProcessModel) -> ConformanceReport: ...


__all__ = [
    "DEFAULT_SENSITIVITY_DWELL_VALUES",
    "BedOccupancy",
    "ClinicianSpecialties",
    "ConformanceChecker",
    "ConformanceReport",
    "EpisodeParams",
    "EventLog",
    "LogEvent",
    "MissedMDTOpportunity",
    "ProcessDiscovery",
    "ProcessModel",
    "ReportHeader",
    "SensitivityPoint",
    "SensitivityReport",
]
