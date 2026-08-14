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


try:
    import pm4py  # type: ignore[import-untyped]
except ImportError:
    pm4py = None  # type: ignore[assignment]


class XesEventReader:
    """XES event reader.

    TODO(SPEC-001, N03): SPEC-001 lists XES as in-scope alongside CSV, but
    `pm4py` (the dependency that would parse it) is not installed in this
    environment, and SPEC-001's "Failure modes" section warns against
    "synthetic data too clean" pipelines that quietly grow a heavy
    dependency nobody asked for. This class satisfies the `EventReader`
    Protocol shape so callers can select a reader without an `isinstance`
    branch, but `read` raises until `pm4py` is genuinely available.

    When it is: use `pm4py.read_xes(str(path))` to get a pandas-like log,
    then map each `<trace>`'s `<event>` to `hwpm.domain.Event` the same way
    `CsvEventReader.read` does — `concept:name` -> `activity`,
    `time:timestamp` -> `timestamp`, plus whatever XES extension the export
    uses for subject and location, which needs confirming against a real
    (never a checked-in) export before it is written. Do not import
    `pm4py` at module scope until this is done; the module-level try/except
    above exists only to make availability checkable without forcing the
    dependency on every install.
    """

    def read(self, path: Path) -> Iterator[Event]:
        del path  # unused: both branches raise; kept for the `EventReader` shape
        if pm4py is None:
            raise NotImplementedError(
                "XES ingestion needs the optional 'pm4py' dependency, which is "
                "not installed (see hwpm[analysis] in pyproject.toml). SPEC-001 "
                "lists XES as in-scope; see the TODO on "
                "hwpm.ingest.reader.XesEventReader for what is left to wire up."
            )
        raise NotImplementedError(
            "pm4py is installed but XesEventReader.read is not yet implemented; "
            "see the TODO on hwpm.ingest.reader.XesEventReader."
        )
