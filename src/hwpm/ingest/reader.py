"""Event-log readers. SPEC-001, node N03.

`EventReader` is the seam between "whatever format a hospital export happens
to be in" and the typed `Event` stream the rest of the system reads
(01-DOMAIN-MODEL.md: "The whole system is a fold over an ordered stream of
these"). A reader does no mapping and no repair — it only parses rows into
`Event`s exactly as given, in file order, so that clock skew and duplicate
reads survive to be flagged by `hwpm.ingest.report.build_report` rather than
being silently absorbed on the way in.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Protocol

from hwpm.domain import ClinicianId, Event, LocationId, PatientId, SubjectId

#: Column headers `CsvEventReader` and its (test-only) writer counterpart
#: agree on. `subject_kind` disambiguates `ClinicianId` from `PatientId` —
#: 01-DOMAIN-MODEL.md is explicit that the two must never be interchangeable.
CSV_FIELDNAMES = (
    "timestamp",
    "subject_kind",
    "subject",
    "activity",
    "location",
    "source",
    "confidence",
)

_SUBJECT_KINDS: dict[str, type[SubjectId]] = {
    "clinician": ClinicianId,
    "patient": PatientId,
}


class EventReader(Protocol):
    """SPEC-001 interface: `def read(self, path: Path) -> Iterator[Event]: ...`"""

    def read(self, path: Path) -> Iterator[Event]: ...


def _subject_from_row(row: dict[str, str]) -> SubjectId:
    kind = row["subject_kind"].strip().lower()
    try:
        subject_cls = _SUBJECT_KINDS[kind]
    except KeyError:
        raise ValueError(
            f"unknown subject_kind {row['subject_kind']!r}; expected one of "
            f"{sorted(_SUBJECT_KINDS)}"
        ) from None
    return subject_cls(row["subject"])


class CsvEventReader:
    """Reads `Event`s from a CSV with `CSV_FIELDNAMES` columns.

    Rows are yielded in file order, unsorted and undeduplicated — ordering
    and duplication are ingestion-report concerns (criterion 7), not a
    reader concern. `location` is passed through verbatim as a raw string
    wrapped in `LocationId`; resolving it against modelled geometry is
    `LocationMapper`'s job, applied by the caller after reading.
    """

    def read(self, path: Path) -> Iterator[Event]:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                yield Event(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    subject=_subject_from_row(row),
                    activity=row["activity"],
                    location=LocationId(row["location"]),
                    source=row["source"],
                    confidence=float(row["confidence"]) if row.get("confidence") else 1.0,
                )


class XesEventReader:
    """XES event reader.

    TODO(SPEC-001, N03): SPEC-001 lists XES as in-scope alongside CSV. This
    class satisfies the `EventReader` Protocol shape so callers can select a
    reader without an `isinstance` branch, but `read` raises until it is
    genuinely implemented.

    When implementing it, do NOT reach for `pm4py.read_xes`. ADR-0008 confines
    pm4py — which is AGPL v3 — to `hwpm.mining._pm4py_adapter`, and an
    import-linter contract fails the build if any other module imports it.
    XES is plain XML, so parse it directly (`lxml` is already an indirect
    dependency) and map each `<trace>`'s `<event>` the way `CsvEventReader.read`
    does: `concept:name` -> `activity`, `time:timestamp` -> `timestamp`, plus
    whatever extension the export uses for subject and location. Parsing it
    ourselves keeps the AGPL surface at exactly one module instead of spreading
    it into the ingestion layer, which is the cheaper long-term position
    whichever way the ADR-0008 licensing question is answered.

    The mapping must be confirmed against a real export — never a checked-in
    one (ADR-0005) — before this is written.
    """

    def read(self, path: Path) -> Iterator[Event]:
        del path  # unused: raises; kept for the `EventReader` shape
        raise NotImplementedError(
            "XesEventReader.read is not yet implemented. SPEC-001 lists XES as "
            "in-scope; see the TODO on hwpm.ingest.reader.XesEventReader, and "
            "note ADR-0008 forbids importing pm4py from this layer."
        )
