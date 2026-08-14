"""Tests for the synthetic ward event-log generator. SPEC-001 node N01.

Criteria covered now: determinism (3). Criteria 4-11 need N03/N04 (ingestion,
RequiredSpecialty strategies) and are not testable until those nodes land —
see docs/AUDIT-LOG.md for the scope note. This file also checks the
noise-injection knobs the spec's "Failure modes" section requires, and that
`GroundTruth.total_distance_m` is internally consistent with the events
generated from it.
"""

from __future__ import annotations

from random import Random

import pytest

from hwpm.domain import Event
from hwpm.ingest.synthetic import SynthConfig, _build_wards, generate

SEED = 42


def _clean_config(**overrides: object) -> SynthConfig:
    return SynthConfig(n_clinicians=6, n_patients=24, visits_per_clinician=6, **overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Criterion 3: determinism
# --------------------------------------------------------------------------


def test_determinism() -> None:
    config = _clean_config()
    events_a, truth_a = generate(config, Random(SEED))
    events_b, truth_b = generate(config, Random(SEED))

    assert events_a == events_b
    assert truth_a == truth_b


def test_determinism_holds_with_noise_enabled() -> None:
    config = _clean_config(
        clock_skew_rate=0.3,
        duplicate_read_rate=0.2,
        low_confidence_rate=0.25,
    )
    events_a, truth_a = generate(config, Random(SEED))
    events_b, truth_b = generate(config, Random(SEED))
    assert events_a == events_b
    assert truth_a == truth_b


def test_different_seeds_produce_different_events() -> None:
    config = _clean_config()
    events_a, _ = generate(config, Random(1))
    events_b, _ = generate(config, Random(2))
    assert events_a != events_b


# --------------------------------------------------------------------------
# Ground truth shape
# --------------------------------------------------------------------------


def test_ground_truth_covers_every_clinician_and_patient() -> None:
    config = _clean_config()
    events, truth = generate(config, Random(SEED))

    assert len(truth.schedules) == config.n_clinicians
    assert all(visits for visits in truth.schedules.values())
    assert len(truth.total_distance_m) == config.n_clinicians
    assert all(d >= 0.0 for d in truth.total_distance_m.values())
    assert len(truth.required_specialties) == config.n_patients
    assert all(specs for specs in truth.required_specialties.values())
    assert events, "generate() must produce events for a non-trivial config"


def test_required_specialty_bounds_are_a_subset_of_the_domain_enum() -> None:
    from hwpm.domain import Specialty

    _, truth = generate(_clean_config(), Random(SEED))
    for specs in truth.required_specialties.values():
        assert specs <= set(Specialty)
        assert 1 <= len(specs) <= 2


def test_ground_truth_distance_matches_reconstructed_bed_path() -> None:
    """The distance the generator claims a clinician walked must equal the
    straight-line path through the beds it actually visited, in visit order,
    starting from the clinician's home nursing station. This is the local
    (pre-ingestion) analogue of acceptance criterion 4's round-trip check.

    Relies on `generate`'s construction order -- the i-th clinician's home ward
    is `wards[i % len(wards)]` -- rather than re-deriving the assignment, since
    that order is exactly what `_make_clinicians` documents it does.
    """
    config = _clean_config()
    wards = _build_wards()
    bed_point_by_id = {bed.id: bed.point for ward in wards for bed in ward.beds}
    patient_bed = {}
    all_beds = [bed for ward in wards for bed in ward.beds]
    for i in range(config.n_patients):
        patient_bed[f"PAT{i:04d}"] = all_beds[i % len(all_beds)].id

    _, truth = generate(config, Random(SEED))

    for i, visits in enumerate(truth.schedules.values()):
        home_ward = wards[i % len(wards)]
        expected = 0.0
        current_point = home_ward.nursing_station.point
        for visit in visits:
            bed_id = patient_bed[visit.patient_id.value]
            bed_point = bed_point_by_id[bed_id]
            expected += current_point.euclidean_distance_to(bed_point)
            current_point = bed_point

        clinician_id = list(truth.schedules.keys())[i]
        assert truth.total_distance_m[clinician_id] == pytest.approx(expected)


def test_zero_noise_stream_is_clean() -> None:
    """With every noise rate at zero, events for each subject are strictly
    monotonic in time and free of exact duplicates."""
    config = _clean_config()
    events, _ = generate(config, Random(SEED))

    by_subject: dict[object, list[Event]] = {}
    for event in events:
        by_subject.setdefault(event.subject, []).append(event)

    for subject_events in by_subject.values():
        timestamps = [e.timestamp for e in subject_events]
        assert timestamps == sorted(timestamps)
        assert len(subject_events) == len(set(subject_events))
        assert all(e.confidence == 1.0 for e in subject_events)


@pytest.mark.parametrize("rate", [0.0, 0.5, 1.0])
def test_clock_skew_rate_is_honoured_at_the_extremes(rate: float) -> None:
    config = _clean_config(clock_skew_rate=rate, clock_skew_seconds=120.0)
    events, _ = generate(config, Random(SEED))

    by_subject: dict[object, list[Event]] = {}
    for event in events:
        by_subject.setdefault(event.subject, []).append(event)

    any_out_of_order = any(
        [e.timestamp for e in evs] != sorted(e.timestamp for e in evs)
        for evs in by_subject.values()
    )
    if rate == 0.0:
        assert not any_out_of_order
    if rate == 1.0:
        # Every event skewed by up to +-120s makes at least one inversion
        # overwhelmingly likely across 6 clinicians x 6 visits x 2 activities.
        assert any_out_of_order


def test_duplicate_read_rate_zero_means_no_duplicates_rate_one_means_many() -> None:
    config_off = _clean_config(duplicate_read_rate=0.0)
    events_off, _ = generate(config_off, Random(SEED))

    config_on = _clean_config(duplicate_read_rate=1.0)
    events_on, _ = generate(config_on, Random(SEED))

    assert len(events_on) == 2 * len(events_off)


def test_low_confidence_rate_one_pushes_every_event_below_quarantine_threshold() -> None:
    config = _clean_config(low_confidence_rate=1.0, low_confidence_floor=0.4)
    events, _ = generate(config, Random(SEED))
    assert all(e.confidence < 0.8 for e in events)
    assert all(e.confidence >= 0.4 for e in events)


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValueError, match="clock_skew_rate"):
        SynthConfig(clock_skew_rate=1.5)
    with pytest.raises(ValueError, match="n_patients"):
        SynthConfig(n_patients=0)
