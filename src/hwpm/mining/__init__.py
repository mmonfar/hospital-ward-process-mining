"""Process discovery, conformance, bedside-episode derivation. SPEC-002, N05.

The public surface matches SPEC-002's interface block: `EpisodeParams`,
`derive_episodes`, `reconstruct_rounds`, `discover`, `conformance`,
`detect_mdt_moments`, `detect_opportunistic`, plus the `ProcessDiscovery` /
`ConformanceChecker` Protocols ADR-0008 requires to be "declared in
`hwpm.mining`". Implementation is split by concern:

- `types.py`       -- dataclasses and Protocols, no logic.
- `episodes.py`     -- dwell-time segmentation, round reconstruction, the
                       patient/clinician joins, the sensitivity sweep.
- `mdt.py`          -- MDT-moment and opportunistic-MDT detection.
- `discovery.py`    -- `discover` / `conformance`, delegating to an engine.
- `_pm4py_adapter.py` -- the **only** file that may `import pm4py` (ADR-0008
                       §1; import-linter contract "pm4py confined to its
                       adapter (AGPL -- ADR-0008)" in `pyproject.toml`).

Nothing in this file, or in any sibling above `_pm4py_adapter.py`, imports
pm4py -- the discovery/conformance functions reach it only through
`discovery.py`'s default `engine` construction.
"""

from __future__ import annotations

from hwpm.mining.discovery import conformance, discover
from hwpm.mining.episodes import (
    attach_clinician_specialties,
    attach_patients,
    build_log,
    derive_episodes,
    reconstruct_rounds,
    sensitivity_analysis,
)
from hwpm.mining.mdt import detect_mdt_moments, detect_opportunistic
from hwpm.mining.types import (
    DEFAULT_SENSITIVITY_DWELL_VALUES,
    BedOccupancy,
    ClinicianSpecialties,
    ConformanceChecker,
    ConformanceReport,
    EpisodeParams,
    EventLog,
    LogEvent,
    MissedMDTOpportunity,
    ProcessDiscovery,
    ProcessModel,
    ReportHeader,
    SensitivityPoint,
    SensitivityReport,
)

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
    "attach_clinician_specialties",
    "attach_patients",
    "build_log",
    "conformance",
    "derive_episodes",
    "detect_mdt_moments",
    "detect_opportunistic",
    "discover",
    "reconstruct_rounds",
    "sensitivity_analysis",
]
