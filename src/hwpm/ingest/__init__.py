"""Ingestion layer. SPEC-001, node N03.

Turns heterogeneous exports into the canonical, pseudonymous `Event` stream
`hwpm.domain` defines. Depends inward on `hwpm.domain` only (02-ARCHITECTURE.md,
import-linter gate 6); never the other way round.

Re-exports the flat namespace SPEC-001 writes against.
"""

from __future__ import annotations

from hwpm.ingest.anonymise import pseudonymise, pseudonymise_id
from hwpm.ingest.evidence import (
    ConsultNote,
    ProblemListEntry,
    Referral,
    RequiredSpecialtyEvidence,
)
from hwpm.ingest.location import (
    QUARANTINE_THRESHOLD,
    LocationMapper,
    bay_alias_key,
    is_quarantined,
    quarantined_raw_value,
)
from hwpm.ingest.reader import (
    CSV_FIELDNAMES,
    CsvEventReader,
    EventReader,
    XesEventReader,
)
from hwpm.ingest.report import DEFAULT_DUPLICATE_WINDOW, IngestionReport, build_report
from hwpm.ingest.specialty import (
    ALL_STRATEGIES,
    ARTEFACT_KIND,
    RECOGNITION_THRESHOLD,
    EvidenceIndex,
    RequiredSpecialtyStrategy,
    SpecialtyEvidenceReport,
    SpecialtyMapper,
    StrategyDeterminations,
    derive_all,
    from_artefact,
    index_evidence,
    normalise_specialty_text,
    to_artefact,
)

__all__ = [
    "ALL_STRATEGIES",
    "ARTEFACT_KIND",
    "CSV_FIELDNAMES",
    "DEFAULT_DUPLICATE_WINDOW",
    "QUARANTINE_THRESHOLD",
    "RECOGNITION_THRESHOLD",
    "ConsultNote",
    "CsvEventReader",
    "EventReader",
    "EvidenceIndex",
    "IngestionReport",
    "LocationMapper",
    "ProblemListEntry",
    "Referral",
    "RequiredSpecialtyEvidence",
    "RequiredSpecialtyStrategy",
    "SpecialtyEvidenceReport",
    "SpecialtyMapper",
    "StrategyDeterminations",
    "XesEventReader",
    "bay_alias_key",
    "build_report",
    "derive_all",
    "from_artefact",
    "index_evidence",
    "is_quarantined",
    "normalise_specialty_text",
    "pseudonymise",
    "pseudonymise_id",
    "quarantined_raw_value",
    "to_artefact",
]
