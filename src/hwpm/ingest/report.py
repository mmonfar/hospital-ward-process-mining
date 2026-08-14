"""Ingestion report: counts anomalies, never repairs them. SPEC-001, node N03.

Criterion 7 is explicit that out-of-order and duplicate events are "flagged
and counted ... not silently fixed", and the modelling-assumptions table
gives the reason: "clock skew across systems is real. Non-monotonic
sequences are flagged, not reordered." `build_report` therefore never sorts,
deduplicates, or drops anything — it walks events in the order it is given
them and produces a count, leaving the caller's event list untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from hwpm.domain import Event
from hwpm.ingest.location import is_quarantined, quarantined_raw_value

#: Two reads of the same subject/activity/location within this window count
#: as a duplicate badge scan rather than two genuinely separate visits.
#: Matches the noise the synthetic generator injects for `duplicate_read_rate`
#: (`hwpm.ingest.synthetic._events_for_round`: a repeat 1-5 seconds later).
#: A modelling assumption, like the rest of SPEC-001's table — tunable, not a
#: fact, and callers needing a different window pass one explicitly.
DEFAULT_DUPLICATE_WINDOW = timedelta(seconds=10)


@dataclass(frozen=True)
class IngestionReport:
    """Anomaly counts for one ingested batch. Nothing here is a repair."""

    total_events: int
    out_of_order_count: int
    duplicate_count: int
    quarantined_count: int
    quarantined_locations: tuple[str, ...]


def build_report(
    events: Sequence[Event],
    duplicate_window: timedelta = DEFAULT_DUPLICATE_WINDOW,
) -> IngestionReport:
    """Count out-of-order events, duplicate reads and quarantined locations.

    Events are grouped by subject and walked in the order given (not
    resorted, per criterion 7's "not silently fixed"). Within a subject's
    sequence:

    - out-of-order: a timestamp earlier than the previous event's.
    - duplicate: same activity and location as the immediately preceding
      event, within `duplicate_window`.

    Quarantined locations (`hwpm.ingest.location.LocationMapper` output
    below the confidence threshold) are counted globally, with every raw
    string preserved — criterion 5's "never dropped".
    """
    by_subject: dict[object, list[Event]] = {}
    for event in events:
        by_subject.setdefault(event.subject, []).append(event)

    out_of_order = 0
    duplicates = 0
    for subject_events in by_subject.values():
        previous: Event | None = None
        for event in subject_events:
            if previous is not None:
                if event.timestamp < previous.timestamp:
                    out_of_order += 1
                elif (
                    event.activity == previous.activity
                    and event.location == previous.location
                    and (event.timestamp - previous.timestamp) <= duplicate_window
                ):
                    duplicates += 1
            previous = event

    quarantined_locations = tuple(
        quarantined_raw_value(event.location)
        for event in events
        if is_quarantined(event.location)
    )

    return IngestionReport(
        total_events=len(events),
        out_of_order_count=out_of_order,
        duplicate_count=duplicates,
        quarantined_count=len(quarantined_locations),
        quarantined_locations=quarantined_locations,
    )
