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

__all__ = [
    "CSV_FIELDNAMES",
    "DEFAULT_DUPLICATE_WINDOW",
    "QUARANTINE_THRESHOLD",
    "ConsultNote",
    "CsvEventReader",
    "EventReader",
    "IngestionReport",
    "LocationMapper",
    "ProblemListEntry",
    "Referral",
    "RequiredSpecialtyEvidence",
    "XesEventReader",
    "bay_alias_key",
    "build_report",
    "is_quarantined",
    "pseudonymise",
    "pseudonymise_id",
    "quarantined_raw_value",
]
