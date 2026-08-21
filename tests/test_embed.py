"""SPEC-007 Part B acceptance criteria 13-20. Node N20-trace-embeddings.

The criterion-16 gate itself (does a learned embedding beat the interpretable
baseline by >= 0.10 ARI?) needs `fastembed` and about 70 s of CPU, so it lives
in `tests/bench/test_embedding_gate.py` and runs under `--bench`, exactly as
N19b's retrieval-quality gate does. Everything here runs in the default suite.
"""

from __future__ import annotations

import socket
from datetime import date, datetime, time, timedelta
from random import Random

import pytest

from hwpm.analytics.cohorts import summarise_cohorts
from hwpm.analytics.motion import ward_of as analytics_ward_of
from hwpm.artefact.envelope import (
    ArtefactEnvelope,
    EmbeddingDerivedFieldError,
    reject_embedding_fields,
)
from hwpm.domain import (
    BedsideEpisode,
    Calibration,
    ClinicianId,
    Event,
    LocationId,
    PatientId,
    RequiredSpecialtyStrategyKey,
    Round,
    Specialty,
    Trajectory,
)
from hwpm.domain.schedule import PlannedVisit, Schedule
from hwpm.ingest.synthetic import (
    DAY_TYPE_PROFILES,
    DayType,
    WardDayConfig,
    generate_ward_days,
)
from hwpm.mining import (
    EpisodeParams,
    attach_clinician_specialties,
    attach_patients,
    derive_episodes,
    reconstruct_rounds,
)
from hwpm.mining.embed import (
    DWELL_BUCKETS,
    FEATURE_NAMES,
    MIN_WARD_DAYS,
    EmbeddingSpaceError,
    InsufficientWardDaysError,
    WardDayParams,
    WardDayVector,
    adjusted_rand_index,
    atypicality,
    cluster_ward_days,
    encode_ward_day,
    encode_ward_days,
    group_ward_days,
    neighbours,
    trace_text,
)
from hwpm.mining.embed import ward_of as embed_ward_of
from hwpm.optimize.instances import tiny_instance
from hwpm.optimize.warmstart import bed_priority, seed_population

# ---------------------------------------------------------------------------
# The corpus. Module-scoped: deriving episodes over 216 ward-days costs about
# ten seconds, and criterion 17's floor means it cannot honestly be made small.
# ---------------------------------------------------------------------------

GENERATOR_SEED = 7
N_DAYS = 24


def _pipeline(seed: int = GENERATOR_SEED, n_days: int = N_DAYS):
    events, truth = generate_ward_days(WardDayConfig(n_days=n_days), Random(seed))
    by_subject: dict[object, list[Event]] = {}
    for event in events:
        by_subject.setdefault(event.subject, []).append(event)
    episodes = []
    for subject, subject_events in by_subject.items():
        traj = Trajectory(
            subject=subject,
            events=tuple(sorted(subject_events, key=lambda e: e.timestamp)),
        )
        episodes.extend(derive_episodes(traj, EpisodeParams()))
    episodes = attach_clinician_specialties(
        attach_patients(episodes, truth.occupancy), truth.clinician_specialties
    )
    days = sorted({e.start.date() for e in episodes})
    rounds = []
    for day in days:
        rounds.extend(reconstruct_rounds(episodes, day))
    vectors = encode_ward_days(episodes, rounds, required=truth.required_specialties)
    return episodes, rounds, vectors, truth


@pytest.fixture(scope="module")
def corpus():
    return _pipeline()


# ---------------------------------------------------------------------------
# The generator's own oracle
# ---------------------------------------------------------------------------


def test_ward_day_generator_is_deterministic():
    a_events, a_truth = generate_ward_days(WardDayConfig(n_days=3), Random(42))
    b_events, b_truth = generate_ward_days(WardDayConfig(n_days=3), Random(42))
    assert a_events == b_events
    assert a_truth.day_types == b_truth.day_types
    assert a_truth.visits == b_truth.visits


def test_ward_day_generator_clears_the_criterion_17_floor(corpus):
    _episodes, _rounds, vectors, truth = corpus
    assert truth.n_ward_days >= MIN_WARD_DAYS
    assert len(vectors) >= MIN_WARD_DAYS
    # Every generated day-type is actually present, or the ARI oracle would be
    # scoring against a class nothing can be assigned to.
    assert set(truth.day_types.values()) == set(DayType)
    assert set(DAY_TYPE_PROFILES) == set(DayType)


def test_only_mdt_days_are_built_to_produce_copresence(corpus):
    """The generator's discriminating structure, asserted rather than assumed.

    `_ward_day_visits` rounds non-convergent teams sequentially so co-presence
    is structurally impossible on them; two earlier layouts did not, and the
    day-type became recoverable from scale alone. If this ever goes green for a
    non-MDT day, the criterion-16 oracle has silently weakened.
    """
    _episodes, _rounds, vectors, truth = corpus
    moments = {v.key: v.features["mdt_moments"] for v in vectors}
    for key, day_type in truth.day_types.items():
        if key not in moments:
            continue
        if day_type is DayType.MDT_DAY:
            continue
        assert moments[key] == 0.0, f"{key} is {day_type} but has co-presence"
    mdt_days = [
        moments[k]
        for k, t in truth.day_types.items()
        if t is DayType.MDT_DAY and k in moments
    ]
    assert sum(mdt_days) / len(mdt_days) > 1.0


# ---------------------------------------------------------------------------
# Criterion 13: features are always present, embedding is not
# ---------------------------------------------------------------------------


def test_features_always_present(corpus):
    _episodes, _rounds, vectors, _truth = corpus
    for vector in vectors:
        assert set(vector.features) >= set(FEATURE_NAMES)
        assert vector.embedding is None
        assert len(vector.as_row()) == len(FEATURE_NAMES)
        assert all(isinstance(value, float) for value in vector.as_row())


def test_a_vector_missing_a_feature_cannot_be_constructed():
    with pytest.raises(ValueError, match="FEATURE_NAMES"):
        WardDayVector(
            day=date(2026, 1, 5),
            ward="1A",
            features={"n_episodes": 3.0},
            patients=frozenset(),
            clinicians=frozenset(),
            provenance={},
        )


def test_every_downstream_function_works_with_embedding_none(corpus):
    _episodes, _rounds, vectors, _truth = corpus
    model = cluster_ward_days(vectors, 4, Random(11))
    assert model.space == "features"
    assert atypicality(vectors[0], model) >= 0.0
    assert len(neighbours(vectors[0], vectors, model, k=3)) == 3
    assert summarise_cohorts(vectors, model).k == 4


def test_use_embedding_refuses_vectors_that_carry_none(corpus):
    _episodes, _rounds, vectors, _truth = corpus
    with pytest.raises(EmbeddingSpaceError, match="carries no embedding"):
        cluster_ward_days(vectors, 4, Random(11), use_embedding=True)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def test_ward_parse_agrees_with_the_analytics_layer():
    """`hwpm.mining.embed` cannot import `hwpm.analytics.motion` (wrong layer),
    so the ward parse is restated. This is the check that keeps the two
    definitions from drifting."""
    for value in ("1A/BED3", "3C/NS", "unstructured"):
        bed = LocationId(value)
        assert embed_ward_of(bed) == analytics_ward_of(bed)


def test_group_ward_days_agrees_with_the_coverage_cell_grouping(corpus):
    from hwpm.analytics.coverage import _units

    episodes, _rounds, _vectors, truth = corpus
    cells = group_ward_days(episodes)
    units = _units(episodes, truth.required_specialties, 300)
    assert {(u.day, u.ward) for u in units} == set(cells)


def test_encode_refuses_an_empty_or_mixed_ward_day(corpus):
    episodes, rounds, _vectors, _truth = corpus
    with pytest.raises(ValueError, match="no episodes"):
        encode_ward_day([], rounds)
    cells = group_ward_days(episodes)
    keys = sorted(cells)
    mixed = cells[keys[0]] + cells[keys[-1]]
    with pytest.raises(ValueError, match="exactly one ward-day"):
        encode_ward_day(mixed, rounds)


def test_required_not_joined_is_recorded_not_guessed(corpus):
    episodes, rounds, _vectors, truth = corpus
    cell = group_ward_days(episodes)[sorted(group_ward_days(episodes))[0]]
    joined = encode_ward_day(cell, rounds, required=truth.required_specialties)
    unjoined = encode_ward_day(cell, rounds)
    assert joined.provenance["required_joined"] is True
    assert unjoined.provenance["required_joined"] is False
    assert unjoined.features["mdt_moments"] == 0.0


def test_episode_params_are_stamped_into_provenance(corpus):
    episodes, rounds, _vectors, _truth = corpus
    cell = group_ward_days(episodes)[sorted(group_ward_days(episodes))[0]]
    params = WardDayParams(episode_params=EpisodeParams(min_dwell_s=180))
    vector = encode_ward_day(cell, rounds, params=params)
    assert vector.provenance["min_dwell_s"] == 180
    assert vector.provenance["method_version"].startswith("wardday-features/")


def test_protected_window_feature_counts_overlapping_episodes():
    """A hand-built ward-day, because the generator's rounds start at 08:00 and
    rarely reach the default midday window."""
    clinician = ClinicianId("C1")
    bed = LocationId("1A/BED0")
    day = date(2026, 3, 2)

    def episode(hour: int, minute: int, minutes: int) -> BedsideEpisode:
        start = datetime.combine(day, time(hour, minute))
        return BedsideEpisode(
            clinician=clinician,
            bed=bed,
            start=start,
            end=start + timedelta(minutes=minutes),
            confidence=1.0,
            source_events=(),
            patient=PatientId("P1"),
            clinician_specialties=frozenset({Specialty.CARDIOLOGY}),
        )

    inside = episode(12, 30, 10)
    straddling = episode(11, 55, 20)
    outside = episode(9, 0, 10)
    episodes = [outside, straddling, inside]
    rounds = [Round(clinician=clinician, day=day, episodes=tuple(episodes))]
    vector = encode_ward_day(episodes, rounds)
    assert vector.features["protected_window_episodes"] == 2.0
    assert vector.features["n_episodes"] == 3.0
    assert vector.features["bed_revisits"] == 2.0


# ---------------------------------------------------------------------------
# Criterion 17: the 200-ward-day floor
# ---------------------------------------------------------------------------


def test_clustering_minimum_days(corpus):
    _episodes, _rounds, vectors, _truth = corpus
    too_few = vectors[: MIN_WARD_DAYS - 1]
    with pytest.raises(InsufficientWardDaysError) as excinfo:
        cluster_ward_days(too_few, 4, Random(11))
    message = str(excinfo.value)
    assert str(MIN_WARD_DAYS) in message
    assert "noise" in message
    # It says why, not just that it refused (criterion 17's "and says why").
    assert len(message) > 120


# ---------------------------------------------------------------------------
# Criterion 18: the ADR-0005 aggregation floor on cluster output
# ---------------------------------------------------------------------------


def test_cluster_suppression_floor(corpus):
    """A cohort resolving to too few patients, or to one clinician, carries no
    numbers at all."""
    _episodes, _rounds, vectors, _truth = corpus
    model = cluster_ward_days(vectors, 4, Random(11))

    # Force a floor breach: rewrite one cluster's members to name a single
    # patient and a single clinician. Done on the vectors rather than by
    # shrinking the corpus, because criterion 17's floor forbids clustering a
    # corpus small enough to breach criterion 18 naturally.
    target = model.labels[0]
    thin = []
    for vector, label in zip(vectors, model.labels, strict=True):
        if label == target:
            thin.append(
                WardDayVector(
                    day=vector.day,
                    ward=vector.ward,
                    features=vector.features,
                    patients=frozenset({PatientId("PAT-ONLY")}),
                    clinicians=frozenset({ClinicianId("CLIN-ONLY")}),
                    provenance=vector.provenance,
                )
            )
        else:
            thin.append(vector)

    report = summarise_cohorts(thin, model)
    suppressed = [c for c in report.cohorts if c.suppressed]
    assert suppressed, "a single-patient, single-clinician cohort must be withheld"
    for cohort in suppressed:
        assert cohort.n_ward_days is None
        assert cohort.n_patients is None
        assert cohort.feature_means is None
        assert cohort.atypical is None
        assert cohort.suppression_reason
    assert "WITHHELD" in report.render()
    assert report.n_suppressed + report.n_published == report.k


def test_a_suppressed_cohort_cannot_smuggle_a_number():
    from hwpm.analytics.cohorts import CohortSummary

    with pytest.raises(ValueError, match="no values"):
        CohortSummary(
            cluster=0,
            suppressed=True,
            suppression_reason="too few",
            n_ward_days=12,
        )


def test_cohort_report_always_carries_the_atypicality_wording(corpus):
    from hwpm.analytics.cohorts import ATYPICALITY_WORDING

    _episodes, _rounds, vectors, _truth = corpus
    model = cluster_ward_days(vectors, 4, Random(11))
    rendered = summarise_cohorts(vectors, model).render()
    assert ATYPICALITY_WORDING in rendered
    assert "not a quality score" in rendered


def test_cohort_means_are_named_features_even_in_embedding_space(corpus):
    """ADR-0007 decision 5's "interpretable derivation published alongside":
    a cohort found in an opaque space is still described in named units."""
    _episodes, _rounds, vectors, _truth = corpus
    model = cluster_ward_days(vectors, 3, Random(5))
    faked = type(model)(
        labels=model.labels,
        keys=model.keys,
        centroids=model.centroids,
        k=model.k,
        space="embedding",
        dimension_names=model.dimension_names,
        centre=model.centre,
        scale=model.scale,
        inertia=model.inertia,
        n_iterations=model.n_iterations,
    )
    # `atypicality` needs the embedding it says it needs.
    with pytest.raises(EmbeddingSpaceError):
        summarise_cohorts(vectors, faked)


# ---------------------------------------------------------------------------
# Criterion 19: determinism, for clustering and for the seeded population
# ---------------------------------------------------------------------------


def test_cluster_determinism(corpus):
    _episodes, _rounds, vectors, _truth = corpus
    a = cluster_ward_days(vectors, 4, Random(11))
    b = cluster_ward_days(vectors, 4, Random(11))
    assert a.labels == b.labels
    assert a.centroids == b.centroids
    assert a.inertia == b.inertia
    c = cluster_ward_days(vectors, 4, Random(12))
    assert c.labels != a.labels or c.inertia == pytest.approx(a.inertia)


def test_cluster_input_order_does_not_change_the_result(corpus):
    _episodes, _rounds, vectors, _truth = corpus
    shuffled = list(vectors)
    Random(99).shuffle(shuffled)
    a = cluster_ward_days(vectors, 4, Random(11))
    b = cluster_ward_days(shuffled, 4, Random(11))
    assert a.keys == b.keys
    assert a.labels == b.labels


def test_neighbours_are_deterministic_and_exclude_self(corpus):
    _episodes, _rounds, vectors, _truth = corpus
    model = cluster_ward_days(vectors, 4, Random(11))
    first = neighbours(vectors[0], vectors, model, k=5)
    again = neighbours(vectors[0], vectors, model, k=5)
    assert first == again
    assert all(key != vectors[0].key for key, _ in first)
    assert [d for _, d in first] == sorted(d for _, d in first)


def _neighbour_schedules(instance) -> list[Schedule]:
    patients = [p.id for p in instance.patients]
    clinician = instance.clinicians[0].id
    forward = Schedule(
        visits=tuple(
            PlannedVisit(clinician=clinician, patient=p, start=i, duration=1)
            for i, p in enumerate(patients)
        )
    )
    backward = Schedule(
        visits=tuple(
            PlannedVisit(clinician=clinician, patient=p, start=i, duration=1)
            for i, p in enumerate(reversed(patients))
        )
    )
    return [forward, backward]


def test_warm_start_determinism():
    instance = tiny_instance(Random(3))
    historical = _neighbour_schedules(instance)
    a = seed_population(instance, historical, Random(5), size=6)
    b = seed_population(instance, historical, Random(5), size=6)
    assert a == b
    assert len(a) == 6


def test_warm_start_returns_only_legal_schedules():
    """SPEC-004 criterion 3 still holds with warm starts enabled: every seed
    has been through `Schedule.validate`."""
    instance = tiny_instance(Random(3))
    for schedule in seed_population(
        instance, _neighbour_schedules(instance), Random(5), size=6
    ):
        schedule.validate(instance.constraints)
        assert schedule.visits


def test_warm_start_with_no_neighbours_returns_nothing():
    """Not a random population. Silently substituting a fallback would make a
    measurement of warm starts a measurement of the fallback."""
    instance = tiny_instance(Random(3))
    assert seed_population(instance, [], Random(5)) == []


def test_bed_priority_follows_first_visit_order():
    instance = tiny_instance(Random(3))
    forward, backward = _neighbour_schedules(instance)
    assert bed_priority(forward, dict(instance.beds)) == list(
        reversed(bed_priority(backward, dict(instance.beds)))
    )


# ---------------------------------------------------------------------------
# Criterion 15: the governance artefact schema rejects embedding-derived fields
# ---------------------------------------------------------------------------


def _envelope(payload: dict) -> ArtefactEnvelope:
    return ArtefactEnvelope(
        kind="coverage",
        method_version="coverage/1.0.0",
        strategy=RequiredSpecialtyStrategyKey.REFERRAL,
        available_strategies=frozenset(RequiredSpecialtyStrategyKey),
        calibration=Calibration.UNCALIBRATED,
        payload=payload,
    )


def test_governance_schema_rejects_embedding_fields():
    assert _envelope({"coverage": 0.72}).payload["coverage"] == 0.72

    for bad in (
        {"embedding": [0.1, 0.2]},
        {"similar_wards_embedding_distance": 0.3},
        {"cohort": {"cosine_similarity": 0.9}},
        {"cells": [{"ward": "1A"}, {"ward": "1B", "atypicality": 2.1}]},
    ):
        with pytest.raises(EmbeddingDerivedFieldError):
            _envelope(bad)


def test_embedding_field_check_survives_a_round_trip(tmp_path):
    from hwpm.artefact.envelope import read_json, write_json

    path = tmp_path / "coverage.json"
    write_json(_envelope({"coverage": 0.72}), path)
    assert read_json(path).payload["coverage"] == 0.72

    text = path.read_text(encoding="utf-8").replace(
        '"coverage": 0.72', '"coverage": 0.72, "cosine_to_cohort": 0.4'
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(EmbeddingDerivedFieldError):
        read_json(path)


def test_reject_embedding_fields_is_callable_on_its_own():
    reject_embedding_fields({"a": 1, "b": {"c": [1, 2]}})
    with pytest.raises(EmbeddingDerivedFieldError, match="nested"):
        reject_embedding_fields({"nested": {"embedding_mean": 1.0}}, "nested")


# ---------------------------------------------------------------------------
# Criterion 20: nothing leaves the machine
# ---------------------------------------------------------------------------


def test_no_network_egress(monkeypatch, corpus):
    """The whole interpretable path runs with sockets disabled.

    Covers the path that is actually recommended for downstream use. The
    learned path's offline behaviour is asserted in
    `tests/bench/test_embedding_gate.py`, because it needs the model cache
    populated first and that is the one point at which a download can happen
    (`hwpm.retrieve.vector.model_sha256`).
    """
    episodes, rounds, vectors, truth = corpus

    def refuse(*_args, **_kwargs):  # pragma: no cover - the point is it never runs
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    rebuilt = encode_ward_days(episodes, rounds, required=truth.required_specialties)
    assert [v.key for v in rebuilt] == [v.key for v in vectors]
    model = cluster_ward_days(rebuilt, 4, Random(11))
    summarise_cohorts(rebuilt, model)
    trace_text(group_ward_days(episodes)[rebuilt[0].key], rounds)


# ---------------------------------------------------------------------------
# The measurement machinery itself
# ---------------------------------------------------------------------------


def test_adjusted_rand_index_known_values():
    assert adjusted_rand_index([0, 0, 1, 1], [0, 0, 1, 1]) == pytest.approx(1.0)
    # Label permutation is not a difference.
    assert adjusted_rand_index([0, 0, 1, 1], [1, 1, 0, 0]) == pytest.approx(1.0)
    # A partition that splits every true class in half agrees above chance but
    # not perfectly.
    partial = adjusted_rand_index([0, 0, 0, 1, 1, 1], [0, 0, 1, 2, 2, 3])
    assert 0.0 < partial < 1.0
    # Both labellings trivial: no agreement above chance exists to measure.
    assert adjusted_rand_index([0, 0, 0], [0, 0, 0]) == 0.0
    with pytest.raises(ValueError, match="differ in length"):
        adjusted_rand_index([0, 1], [0])


def test_feature_baseline_recovers_real_structure(corpus):
    """The interpretable baseline is well above chance on the generator's own
    day-types. This is the number the criterion-16 gate has to beat, and it is
    asserted here so a regression in the features shows up in the default suite
    rather than only under `--bench`."""
    _episodes, _rounds, vectors, truth = corpus
    ordered = sorted(vectors, key=lambda v: v.key)
    labels = [truth.day_types[v.key].value for v in ordered]
    k = len(set(labels))
    scores = [
        adjusted_rand_index(labels, cluster_ward_days(vectors, k, Random(seed)).labels)
        for seed in (11, 23, 37, 53, 71)
    ]
    assert min(scores) > 0.30, scores
    assert max(scores) > 0.50, scores


def test_trace_text_carries_shape_not_identifiers(corpus):
    episodes, rounds, vectors, _truth = corpus
    cells = group_ward_days(episodes)
    text = trace_text(cells[vectors[0].key], rounds)
    assert "round 0:" in text
    assert any(bucket in text for _limit, bucket in DWELL_BUCKETS)
    # No ward id, no clinician id, no patient id.
    assert vectors[0].ward not in text
    for clinician in vectors[0].clinicians:
        assert clinician.value not in text
    for patient in vectors[0].patients:
        assert patient.value not in text
