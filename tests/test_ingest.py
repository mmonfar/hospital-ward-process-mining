"""Tests for `hwpm.ingest`. SPEC-001 node N03.

Covers acceptance criteria 4 (`test_synthetic_roundtrip`), 5
(`test_location_quarantine`), 6 (`test_pseudonymisation`) and 7
(`test_ingest_report`) — the exact names SPEC-001's acceptance-criteria
table gives them. Criteria 8-11 (`RequiredSpecialty` confidence and the five
strategies) are node N04's, owned by the architect role, and are not tested
here.
"""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from random import Random

import pytest

from hwpm.domain import ClinicianId, Event, PatientId
from hwpm.ingest import (
    CSV_FIELDNAMES,
    QUARANTINE_THRESHOLD,
    CsvEventReader,
    LocationMapper,
    build_report,
    is_quarantined,
    pseudonymise,
    pseudonymise_id,
    quarantined_raw_value,
)
from hwpm.ingest.synthetic import SynthConfig, _build_wards, generate

SEED = 42


def _clean_config(**overrides: object) -> SynthConfig:
    # SynthConfig's own defaults already match `n_clinicians=6, n_patients=24,
    # visits_per_clinician=6`; passing overrides straight through (rather than
    # also hard-coding those three) avoids a duplicate-kwarg clash when a test
    # wants to override one of them.
    return SynthConfig(**overrides)  # type: ignore[arg-type]


def _subject_kind(subject: object) -> str:
    if isinstance(subject, ClinicianId):
        return "clinician"
    if isinstance(subject, PatientId):
        return "patient"
    raise TypeError(f"unexpected subject type {type(subject)!r}")


def _write_csv(events: list[Event], path: Path) -> None:
    """Test-only writer: the mirror image of `CsvEventReader`, using
    `CSV_FIELDNAMES` so the two never drift apart silently."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for event in events:
            writer.writerow(
                {
                    "timestamp": event.timestamp.isoformat(),
                    "subject_kind": _subject_kind(event.subject),
                    "subject": event.subject.value,
                    "activity": event.activity,
                    "location": event.location.value,
                    "source": event.source,
                    "confidence": event.confidence,
                }
            )


# --------------------------------------------------------------------------
# Criterion 4: synthetic ground truth round-trips through ingestion
# --------------------------------------------------------------------------


def test_synthetic_roundtrip(tmp_path: Path) -> None:
    """events -> CSV -> CsvEventReader -> LocationMapper -> pseudonymise
    recovers each clinician's walked distance within 1% of what the
    generator's own `GroundTruth.total_distance_m` claims."""
    config = _clean_config()  # zero noise: the oracle check, not the noise check
    events, truth = generate(config, Random(SEED))

    csv_path = tmp_path / "events.csv"
    _write_csv(events, csv_path)

    read_events = list(CsvEventReader().read(csv_path))
    assert read_events == events

    # A clean feed: every raw location string is already a canonical id, so
    # the mapper should resolve everything at full confidence.
    mapper = LocationMapper(known={e.location.value: e.location for e in read_events})
    mapped_events = []
    for event in read_events:
        location, confidence = mapper.map(event.location.value)
        assert confidence == 1.0
        mapped_events.append(replace(event, location=location))

    salt = b"unit-test-salt-not-real"
    pseudo_events = list(pseudonymise(mapped_events, salt))

    wards = _build_wards()
    point_by_location = {
        loc.id: loc.point for ward in wards for loc in (ward.nursing_station, *ward.beds)
    }

    arrivals_by_subject: dict[object, list[Event]] = {}
    for event in pseudo_events:
        if event.activity == "arrive":
            arrivals_by_subject.setdefault(event.subject, []).append(event)

    for i, (clinician_id, _visits) in enumerate(truth.schedules.items()):
        pseudo_id = pseudonymise_id(clinician_id, salt)
        home_ward = wards[i % len(wards)]
        arrivals = arrivals_by_subject[pseudo_id]

        current_point = home_ward.nursing_station.point
        reconstructed_m = 0.0
        for arrival in arrivals:
            bed_point = point_by_location[arrival.location]
            reconstructed_m += current_point.euclidean_distance_to(bed_point)
            current_point = bed_point

        expected_m = truth.total_distance_m[clinician_id]
        assert reconstructed_m == pytest.approx(expected_m, rel=0.01)


# --------------------------------------------------------------------------
# Criterion 5: unmappable locations are quarantined, never dropped or guessed
# --------------------------------------------------------------------------


def test_location_quarantine() -> None:
    known_bed = next(iter(_build_wards())).beds[3]
    mapper = LocationMapper(
        known={known_bed.id.value: known_bed.id},
        bay_aliases={"bay3": known_bed.id},
    )

    # The free-text variants SPEC-001 names explicitly.
    for raw in ("bay 3", "Bay3", "B3"):
        location, confidence = mapper.map(raw)
        assert location == known_bed.id
        assert confidence >= QUARANTINE_THRESHOLD
        assert not is_quarantined(location)

    # An exact, clean match still resolves at full confidence.
    location, confidence = mapper.map(known_bed.id.value)
    assert (location, confidence) == (known_bed.id, 1.0)

    # Genuinely unrecognisable input is quarantined: never dropped, never
    # guessed, and the raw value survives unchanged.
    raw_unknown = "some scrawled handover note, not a location at all"
    location, confidence = mapper.map(raw_unknown)
    assert confidence < QUARANTINE_THRESHOLD
    assert is_quarantined(location)
    assert quarantined_raw_value(location) == raw_unknown


def test_location_quarantine_is_not_dropped_when_carried_into_an_event() -> None:
    """A quarantined location must still produce a usable, retained `Event` —
    criterion 5's "never dropped" applies to the event stream, not just the
    mapper's return value in isolation."""
    mapper = LocationMapper()
    location, confidence = mapper.map("bay unknown-9")
    event = Event(
        timestamp=datetime(2026, 1, 5, 8, 0, 0),
        subject=ClinicianId("CLIN000"),
        activity="arrive",
        location=location,
        source="csv",
        confidence=confidence,
    )
    assert is_quarantined(event.location)
    assert quarantined_raw_value(event.location) == "bay unknown-9"


# --------------------------------------------------------------------------
# Criterion 6: pseudonymised output contains no source identifier
# --------------------------------------------------------------------------


def test_pseudonymisation() -> None:
    config = _clean_config(n_clinicians=3, n_patients=6, visits_per_clinician=3)
    events, _ = generate(config, Random(SEED))
    raw_ids = {event.subject.value for event in events}

    salt = b"unit-test-salt-not-real"
    pseudo_events = list(pseudonymise(events, salt))
    pseudo_ids = {event.subject.value for event in pseudo_events}

    assert len(pseudo_events) == len(events)
    assert pseudo_ids.isdisjoint(raw_ids), "no source identifier may survive"

    # Deterministic under a fixed salt (so trajectories still group).
    pseudo_events_again = list(pseudonymise(events, salt))
    assert pseudo_events == pseudo_events_again

    # Not deterministic across different salts, or two differently-salted
    # exports would be joinable by id.
    pseudo_other_salt = {
        event.subject.value for event in pseudonymise(events, b"a-different-salt")
    }
    assert pseudo_other_salt.isdisjoint(pseudo_ids)

    # ClinicianId / PatientId subtyping survives pseudonymisation.
    for original, pseudonymised in zip(events, pseudo_events, strict=True):
        assert type(original.subject) is type(pseudonymised.subject)
        assert original.activity == pseudonymised.activity
        assert original.location == pseudonymised.location


# --------------------------------------------------------------------------
# Criterion 7: out-of-order and duplicate events are flagged, not repaired
# --------------------------------------------------------------------------


def test_ingest_report() -> None:
    config = _clean_config(
        n_clinicians=4,
        n_patients=12,
        visits_per_clinician=4,
        clock_skew_rate=1.0,
        clock_skew_seconds=120.0,
        duplicate_read_rate=1.0,
    )
    events, _ = generate(config, Random(SEED))
    events_before = list(events)

    report = build_report(events)

    assert report.total_events == len(events)
    assert report.out_of_order_count > 0
    assert report.duplicate_count > 0
    assert report.quarantined_count == 0
    assert report.quarantined_locations == ()

    # Not silently fixed: the report does not reorder or deduplicate.
    assert events == events_before


def test_ingest_report_clean_stream_flags_nothing() -> None:
    events, _ = generate(_clean_config(), Random(SEED))
    report = build_report(events)
    assert report.out_of_order_count == 0
    assert report.duplicate_count == 0
    assert report.quarantined_count == 0


def test_ingest_report_counts_quarantined_locations() -> None:
    events, _ = generate(
        _clean_config(n_clinicians=2, n_patients=4, visits_per_clinician=2), Random(SEED)
    )
    mapper = LocationMapper()  # empty registry: everything is unmappable
    quarantined_events = [
        replace(event, location=mapper.map(event.location.value)[0]) for event in events
    ]

    report = build_report(quarantined_events)

    assert report.quarantined_count == len(quarantined_events)
    assert len(report.quarantined_locations) == len(quarantined_events)
    # Raw values preserved, not dropped.
    assert set(report.quarantined_locations) == {e.location.value for e in events}
