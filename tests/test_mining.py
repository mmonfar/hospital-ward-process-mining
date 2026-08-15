"""Tests for `hwpm.mining`. SPEC-002 acceptance criteria 1-7, N05-mining.

Test strategy (docs/06-QA-AND-DEADCODE.md, "Mining/analytics"): golden-file
tests against synthetic logs with a *known* ground truth, checked against
truth rather than against a previous run's output.

Two kinds of ground truth are used here, and which one is used where is
deliberate:

- `hwpm.ingest.synthetic.generate()` for episode recall (criterion 1),
  sensitivity (criterion 2) and process discovery (criterion 3). The
  generator's own event stream -- an "arrive" then a "depart" per visit,
  repeated -- *is* the process being discovered; no hand-waving is needed to
  say what the "true" process is.

- Hand-built `BedsideEpisode`/`Trajectory` fixtures for MDT-moment detection
  (criterion 4) and opportunistic detection (criterion 5). The generator does
  not (yet) produce coordinated multi-clinician co-presence, nor any
  "walked past without stopping" trajectory data (it only ever emits
  arrive/depart pairs at beds a clinician actually visited) -- see this
  node's audit entry / handover notes for that gap. These fixtures are still
  synthetic, deterministic, and built by hand rather than "looking at a
  failing row" (CLAUDE.md rule 2); they are the reproducing case for a
  scenario the generator cannot yet produce, in the same spirit as
  `tests/test_travel.py`'s and `tests/test_domain.py`'s hand-built fixtures.

Nothing here reads `HWPM_DATA_DIR` (ADR-0005) or imports `pm4py` directly --
`hwpm.mining.discover`/`conformance` reach it through the adapter.
"""

from __future__ import annotations

import datetime as dt
from random import Random

import pytest

from hwpm.domain import (
    BedsideEpisode,
    ClinicianId,
    Event,
    LocationId,
    MDTMoment,
    PatientId,
    Specialty,
    Trajectory,
)
from hwpm.ingest.synthetic import SynthConfig, generate
from hwpm.mining import (
    ConformanceReport,
    EpisodeParams,
    EventLog,
    LogEvent,
    MissedMDTOpportunity,
    build_log,
    conformance,
    derive_episodes,
    detect_mdt_moments,
    detect_opportunistic,
    discover,
    reconstruct_rounds,
    sensitivity_analysis,
)

T0 = dt.datetime(2026, 1, 5, 8, 0, 0)


def _event(
    clinician: str,
    activity: str,
    location: str,
    when: dt.datetime,
    confidence: float = 1.0,
) -> Event:
    return Event(
        timestamp=when,
        subject=ClinicianId(clinician),
        activity=activity,
        location=LocationId(location),
        source="test",
        confidence=confidence,
    )


def _synthetic_trajectories(config: SynthConfig, seed: int = 42):
    events, truth = generate(config, Random(seed))
    by_subject: dict[object, list[Event]] = {}
    for event in events:
        by_subject.setdefault(event.subject, []).append(event)
    trajectories = [
        Trajectory(subject=subject, events=tuple(evs))
        for subject, evs in by_subject.items()
    ]
    return trajectories, truth


# ---------------------------------------------------------------------------
# Criterion 1: episode recall. test_episode_recall
# ---------------------------------------------------------------------------


def test_episode_recall() -> None:
    """>=95% of the generator's own visits are recovered as episodes at
    `EpisodeParams()` defaults, generated at `SynthConfig()` defaults (no
    noise) -- the generator's ground truth for "how many visits happened"."""
    config = SynthConfig(n_clinicians=6, n_patients=24, visits_per_clinician=6)
    trajectories, truth = _synthetic_trajectories(config)
    total_visits = sum(len(visits) for visits in truth.schedules.values())

    total_episodes = sum(
        len(derive_episodes(traj, EpisodeParams())) for traj in trajectories
    )

    assert total_visits > 0
    assert total_episodes / total_visits >= 0.95


def test_episode_recall_degrades_honestly_when_both_boundary_reads_are_quarantined() -> (
    None
):
    """Documented trade-off, not a hidden one (see `episodes.py`'s module
    docstring): confidence-filtering happens *before* segmentation, so a
    visit whose arrive *and* depart are both quarantined loses its episode
    entirely, rather than being reported with fabricated confidence. This is
    not criterion 1 (which is at zero noise) -- it demonstrates why criterion
    1 is read that way."""
    config = SynthConfig(
        n_clinicians=6,
        n_patients=24,
        visits_per_clinician=6,
        low_confidence_rate=1.0,
        low_confidence_floor=0.4,
    )
    trajectories, truth = _synthetic_trajectories(config)
    total_visits = sum(len(visits) for visits in truth.schedules.values())

    total_episodes = sum(
        len(derive_episodes(traj, EpisodeParams())) for traj in trajectories
    )

    # Every event is quarantined (confidence in [0.4, 0.79)) -> nothing
    # clears `min_confidence=0.8` -> zero episodes, not a fabricated guess.
    assert total_episodes == 0
    assert total_visits > 0


def test_derive_episodes_rejects_a_patient_trajectory() -> None:
    """SPEC-002's `derive_episodes(traj, params)` is a clinician's round
    (01-DOMAIN-MODEL.md, "Round"); a `PatientId`-subject trajectory is a
    caller mistake this module can actually detect, so it raises rather than
    silently producing nonsense episodes attributed to a patient."""
    traj = Trajectory(
        subject=PatientId("PAT0001"),
        events=(_event("PAT0001", "arrive", "1A/BED0", T0),),
    )
    with pytest.raises(TypeError, match="clinician"):
        derive_episodes(traj, EpisodeParams())  # type: ignore[arg-type]


def test_derive_episodes_merges_a_brief_detour_within_max_gap_s() -> None:
    """A short, separately logged ping elsewhere and straight back counts as
    one visit (SPEC-002: "gaps <90s within an episode are sensor noise")."""
    events = (
        _event("C1", "arrive", "1A/BED0", T0),
        _event("C1", "ping", "1A/BED0", T0 + dt.timedelta(seconds=60)),
        _event("C1", "ping", "1A/NS", T0 + dt.timedelta(seconds=70)),
        _event("C1", "ping", "1A/BED0", T0 + dt.timedelta(seconds=110)),
        _event("C1", "depart", "1A/BED0", T0 + dt.timedelta(minutes=5)),
    )
    traj = Trajectory(subject=ClinicianId("C1"), events=events)
    episodes = derive_episodes(traj, EpisodeParams())

    assert len(episodes) == 1
    (episode,) = episodes
    assert episode.start == T0
    assert episode.end == T0 + dt.timedelta(minutes=5)
    assert len(episode.source_events) == 4  # the NS ping is its own dropped block


def test_derive_episodes_splits_a_genuine_revisit_beyond_max_gap_s() -> None:
    events = (
        _event("C1", "arrive", "1A/BED0", T0),
        _event("C1", "depart", "1A/BED0", T0 + dt.timedelta(minutes=5)),
        _event("C1", "arrive", "1A/BED0", T0 + dt.timedelta(hours=2)),
        _event("C1", "depart", "1A/BED0", T0 + dt.timedelta(hours=2, minutes=5)),
    )
    traj = Trajectory(subject=ClinicianId("C1"), events=events)
    episodes = derive_episodes(traj, EpisodeParams(max_gap_s=90))

    assert len(episodes) == 2
    assert episodes[0].end < episodes[1].start


def test_short_dwell_is_passing_through_not_a_visit() -> None:
    events = (
        _event("C1", "arrive", "1A/BED0", T0),
        _event("C1", "depart", "1A/BED0", T0 + dt.timedelta(seconds=30)),
    )
    traj = Trajectory(subject=ClinicianId("C1"), events=events)
    assert derive_episodes(traj, EpisodeParams(min_dwell_s=120)) == []


# ---------------------------------------------------------------------------
# Criterion 2: sensitivity. test_sensitivity_emitted
# ---------------------------------------------------------------------------


def test_sensitivity_emitted() -> None:
    """The mandated {60, 120, 180, 300}s sweep is produced without being
    asked for a specific set of values, and stays stable across it for
    generator-default (12-minute) visits -- SPEC-002: "any headline figure
    must be shown to be stable across that range... or reported with the
    range."."""
    config = SynthConfig(n_clinicians=6, n_patients=24, visits_per_clinician=6)
    trajectories, truth = _synthetic_trajectories(config)
    total_visits = sum(len(visits) for visits in truth.schedules.values())

    report = sensitivity_analysis(trajectories, known_visit_count=total_visits)

    assert [p.min_dwell_s for p in report.points] == [60, 120, 180, 300]
    assert all(p.recall is not None and p.recall >= 0.95 for p in report.points)
    assert report.stable_within(tolerance=0.05)
    assert report.header.parameters["dwell_values"] == (60, 120, 180, 300)


def test_sensitivity_report_flags_instability_when_it_exists() -> None:
    """`stable_within` is a real check, not a tautology: a dwell threshold
    that exceeds the visit duration must recover fewer visits, and the
    report must be able to say so."""
    config = SynthConfig(
        n_clinicians=6, n_patients=24, visits_per_clinician=6, visit_duration_minutes=2
    )
    trajectories, truth = _synthetic_trajectories(config)
    total_visits = sum(len(visits) for visits in truth.schedules.values())

    # 2-minute visits: the 180s and 300s points must recover fewer than the
    # 60s/120s points, since min_dwell_s now exceeds the actual visit length.
    report = sensitivity_analysis(
        trajectories, known_visit_count=total_visits, dwell_values=(60, 300)
    )
    recalls = [p.recall for p in report.points]
    assert recalls[0] is not None and recalls[1] is not None
    assert recalls[1] < recalls[0]
    assert not report.stable_within(tolerance=0.01)


# ---------------------------------------------------------------------------
# Criterion 3: discovery matches the generating process. test_discovery_ground_truth
# ---------------------------------------------------------------------------


def test_discovery_ground_truth() -> None:
    """The generator's own event stream *is* the ground-truth process: every
    visit is exactly an "arrive" followed by a "depart" (`synthetic.py`'s
    `_events_for_round`). A model discovered from that stream, checked
    against the same stream, must show fitness >=0.9 and precision >=0.8
    (SPEC-002 criterion 3)."""
    config = SynthConfig(n_clinicians=6, n_patients=24, visits_per_clinician=6)
    trajectories, _truth = _synthetic_trajectories(config)
    log = build_log(trajectories)

    model = discover(log, algorithm="inductive")
    assert model.activities == {"arrive", "depart"}

    report = conformance(log, model)
    assert report.fitness >= 0.9
    assert report.precision >= 0.8


def test_discovery_heuristics_comparator_diverges_from_inductive() -> None:
    """SPEC-002: "inductive miner; heuristics miner as comparator". Criterion
    3's fitness/precision bar (>=0.9 / >=0.8) is stated for *the* discovered
    model, and `discover`'s own spec-given default is "inductive" --
    `test_discovery_ground_truth` covers that bar. The heuristics miner runs
    as a comparator, not as a second algorithm independently required to
    clear the same threshold.

    That distinction was investigated, not assumed, after this exact test
    first asserted `fitness >= 0.9` for heuristics and got 0.22. Checked
    before relaxing anything: `pm4py.discover_petri_net_heuristics` was run
    directly (bypassing this project's adapter) across `dependency_threshold`
    in {0.0, 0.3, 0.5} and across log sizes from 6 to 40 cases; fitness stayed
    in [0.14, 0.62] throughout, never close to inductive's 1.0 on the
    identical DataFrame through the identical code path. So this is a real
    property of pm4py's heuristics miner on a short, tightly-looping,
    two-activity alphabet (this log is exactly "arrive, depart" repeated per
    case) -- not a defect in `build_log` or in this adapter, which the
    inductive miner's perfect fitness on the same input rules out directly.

    What a comparator is actually for, then, is tested here: it must run
    through the same `discover`/`conformance` plumbing without special-casing,
    agree on *what happened* (the activity alphabet), and its divergence from
    the primary algorithm is itself the reportable comparison -- asserted
    explicitly below, not silently dropped.
    """
    config = SynthConfig(n_clinicians=6, n_patients=24, visits_per_clinician=6)
    trajectories, _truth = _synthetic_trajectories(config)
    log = build_log(trajectories)

    inductive_model = discover(log, algorithm="inductive")
    heuristics_model = discover(log, algorithm="heuristics")
    assert (
        heuristics_model.activities == inductive_model.activities == {"arrive", "depart"}
    )

    inductive_report = conformance(log, inductive_model)
    heuristics_report = conformance(log, heuristics_model)

    assert inductive_report.fitness >= 0.9  # criterion 3's actual bar, restated
    assert 0.0 <= heuristics_report.fitness <= 1.0
    assert 0.0 <= heuristics_report.precision <= 1.0
    # The verified divergence: materially worse, not a rounding difference.
    assert heuristics_report.fitness < inductive_report.fitness - 0.3


# ---------------------------------------------------------------------------
# Criterion 4: exact MDT moments, no false positives. test_mdt_exact
# ---------------------------------------------------------------------------


def _episode(
    clinician: str,
    bed: str,
    patient: str,
    start_min: float,
    end_min: float,
    specialties: frozenset[Specialty],
) -> BedsideEpisode:
    return BedsideEpisode(
        clinician=ClinicianId(clinician),
        bed=LocationId(bed),
        start=T0 + dt.timedelta(minutes=start_min),
        end=T0 + dt.timedelta(minutes=end_min),
        confidence=1.0,
        source_events=(),
        patient=PatientId(patient),
        clinician_specialties=specialties,
    )


def test_mdt_exact() -> None:
    """Exactly the co-presence this fixture creates, and nothing else: not
    the same patient visited alone hours later, and not two same-specialty
    clinicians at a different bed (present_specialties too narrow to satisfy
    >=2 required specialties -- 01-DOMAIN-MODEL.md's "two people who happened
    to collide")."""
    required = {
        PatientId("PAT001"): frozenset({Specialty.CARDIOLOGY, Specialty.SURGICAL}),
        PatientId("PAT002"): frozenset({Specialty.CARDIOLOGY, Specialty.RENAL}),
    }
    episodes = [
        # The genuine MDT moment: two different specialties, 7 minutes apart
        # at the same bedside, well inside the default 300s window.
        _episode("C1", "1A/BED0", "PAT001", 0, 10, frozenset({Specialty.CARDIOLOGY})),
        _episode("C2", "1A/BED0", "PAT001", 7, 15, frozenset({Specialty.SURGICAL})),
        # Same bed/patient, hours later, alone -> only one specialty present.
        _episode("C3", "1A/BED0", "PAT001", 200, 210, frozenset({Specialty.CARDIOLOGY})),
        # Two clinicians of the *same* specialty co-present -- not multidisciplinary.
        _episode("C4", "1B/BED0", "PAT002", 0, 10, frozenset({Specialty.CARDIOLOGY})),
        _episode("C5", "1B/BED0", "PAT002", 5, 12, frozenset({Specialty.CARDIOLOGY})),
    ]

    moments = detect_mdt_moments(episodes, required, window_s=300)

    assert len(moments) == 1
    (moment,) = moments
    assert moment.bed == LocationId("1A/BED0")
    assert moment.patient == PatientId("PAT001")
    assert moment.present_specialties == frozenset(
        {Specialty.CARDIOLOGY, Specialty.SURGICAL}
    )
    assert moment.satisfied_specialties == frozenset(
        {Specialty.CARDIOLOGY, Specialty.SURGICAL}
    )


def test_mdt_exact_respects_the_window() -> None:
    """Two visits at the same bedside further apart than `window_s` are not
    a joint review (SPEC-002 modelling-assumptions table: "Two teams at one
    bed 4 minutes apart are not an MDT" -- generalised here to "further apart
    than the window is not co-presence")."""
    required = {
        PatientId("PAT001"): frozenset({Specialty.CARDIOLOGY, Specialty.SURGICAL})
    }
    episodes = [
        _episode("C1", "1A/BED0", "PAT001", 0, 5, frozenset({Specialty.CARDIOLOGY})),
        _episode("C2", "1A/BED0", "PAT001", 20, 25, frozenset({Specialty.SURGICAL})),
    ]
    moments = detect_mdt_moments(episodes, required, window_s=300)
    assert moments == []


def test_mdt_moment_domain_object_rejects_fewer_than_two_satisfied() -> None:
    """The invariant is enforced at the domain layer too, not only by the
    detector's own pre-check (01-DOMAIN-MODEL.md)."""
    with pytest.raises(ValueError, match="MDTMoment"):
        MDTMoment(
            bed=LocationId("1A/BED0"),
            patient=PatientId("PAT001"),
            start=T0,
            end=T0 + dt.timedelta(minutes=5),
            present_specialties=frozenset({Specialty.CARDIOLOGY}),
            satisfied_specialties=frozenset({Specialty.CARDIOLOGY}),
        )


# ---------------------------------------------------------------------------
# Criterion 5: opportunistic detection excludes continuous transit.
# test_opportunistic_transit_excluded
# ---------------------------------------------------------------------------


def _clinician_events(
    clinician: str, stops: list[tuple[str, dt.datetime, dt.datetime]]
) -> tuple[Event, ...]:
    """`stops`: `[(bed, arrive_at, depart_at), ...]` in order."""
    events: list[Event] = []
    for bed, arrive, depart in stops:
        events.append(_event(clinician, "arrive", bed, arrive))
        events.append(_event(clinician, "depart", bed, depart))
    return tuple(events)


def test_opportunistic_transit_excluded() -> None:
    """The SPEC-002 example itself: a clinician of the needed specialty
    passes within `proximity_m` of a bed whose patient needs it, and no
    episode exists for that pair -> one candidate. A second clinician,
    geometrically identical but with **no** `BedsideEpisode` anywhere in the
    log (continuous transit -- "walking to theatre"), must never yield a
    candidate at all, however close their route passes."""
    # CLIN_A stops at 1B/BED0 (establishing they are a real, available
    # SURGICAL clinician, not just passing through), then transits from
    # 1B/BED0 to 1C/BED0 -- a route that passes within a few metres of
    # 1B/BED3 via the 1B ward corridor (verified against TravelGraph while
    # building this fixture: ~3m, well under the 15m default).
    traj_a = Trajectory(
        subject=ClinicianId("CLIN_A"),
        events=_clinician_events(
            "CLIN_A",
            [
                ("1B/BED0", T0, T0 + dt.timedelta(minutes=10)),
                ("1C/BED0", T0 + dt.timedelta(minutes=15), T0 + dt.timedelta(minutes=20)),
            ],
        ),
    )
    # CLIN_B: the same geometry (1B/BED0 -> 1C/BED0), but contributes *no*
    # BedsideEpisode below -- standing in for a trajectory that never
    # dwelled anywhere, e.g. walking straight through to theatre.
    traj_b = Trajectory(
        subject=ClinicianId("CLIN_B"),
        events=_clinician_events(
            "CLIN_B",
            [
                ("1B/BED0", T0, T0 + dt.timedelta(minutes=10)),
                ("1C/BED0", T0 + dt.timedelta(minutes=15), T0 + dt.timedelta(minutes=20)),
            ],
        ),
    )
    # A nurse's episode is what establishes PAT_NEED occupies 1B/BED3 around
    # this time -- opportunistic detection reads bed occupancy from `episodes`
    # (see mdt.py's module docstring), the same as MDT-moment detection does.
    traj_nurse = Trajectory(
        subject=ClinicianId("NURSE1"),
        events=_clinician_events(
            "NURSE1",
            [("1B/BED3", T0 + dt.timedelta(minutes=5), T0 + dt.timedelta(minutes=12))],
        ),
    )

    episodes = [
        _episode(
            "CLIN_A", "1B/BED0", "PAT_OTHER", 0, 10, frozenset({Specialty.SURGICAL})
        ),
        _episode("NURSE1", "1B/BED3", "PAT_NEED", 5, 12, frozenset({Specialty.RENAL})),
        # Deliberately no episode for CLIN_B anywhere.
    ]
    required = {
        PatientId("PAT_NEED"): frozenset({Specialty.SURGICAL}),
        PatientId("PAT_OTHER"): frozenset(),
    }

    candidates = detect_opportunistic(
        [traj_a, traj_b, traj_nurse], episodes, required, proximity_m=15.0, window_s=300
    )

    clinicians_flagged = {c.clinician for c in candidates}
    assert ClinicianId("CLIN_A") in clinicians_flagged
    assert ClinicianId("CLIN_B") not in clinicians_flagged

    (candidate,) = [c for c in candidates if c.clinician == ClinicianId("CLIN_A")]
    assert candidate.patient == PatientId("PAT_NEED")
    assert candidate.specialty == Specialty.SURGICAL
    assert candidate.bed == LocationId("1B/BED3")
    assert 0.0 < candidate.proximity_m <= 15.0
    assert 0.0 < candidate.confidence <= 1.0


def test_opportunistic_never_flags_a_pair_that_already_had_a_real_episode() -> None:
    """If the clinician actually visited this patient already, it is not a
    *missed* opportunity."""
    traj_a = Trajectory(
        subject=ClinicianId("CLIN_A"),
        events=_clinician_events(
            "CLIN_A",
            [
                ("1B/BED0", T0, T0 + dt.timedelta(minutes=10)),
                ("1B/BED3", T0 + dt.timedelta(minutes=12), T0 + dt.timedelta(minutes=18)),
                ("1C/BED0", T0 + dt.timedelta(minutes=20), T0 + dt.timedelta(minutes=25)),
            ],
        ),
    )
    episodes = [
        _episode(
            "CLIN_A", "1B/BED0", "PAT_OTHER", 0, 10, frozenset({Specialty.SURGICAL})
        ),
        # CLIN_A already visited PAT_NEED directly -- a real episode, not a
        # miss.
        _episode(
            "CLIN_A", "1B/BED3", "PAT_NEED", 12, 18, frozenset({Specialty.SURGICAL})
        ),
    ]
    required = {
        PatientId("PAT_NEED"): frozenset({Specialty.SURGICAL}),
        PatientId("PAT_OTHER"): frozenset(),
    }

    candidates = detect_opportunistic([traj_a], episodes, required)
    assert candidates == []


def test_missed_mdt_opportunity_validates_its_own_fields() -> None:
    with pytest.raises(ValueError, match="confidence"):
        MissedMDTOpportunity(
            clinician=ClinicianId("C1"),
            patient=PatientId("P1"),
            specialty=Specialty.SURGICAL,
            bed=LocationId("1A/BED0"),
            at=T0,
            proximity_m=1.0,
            confidence=1.5,
            reason="test",
        )
    with pytest.raises(ValueError, match="proximity_m"):
        MissedMDTOpportunity(
            clinician=ClinicianId("C1"),
            patient=PatientId("P1"),
            specialty=Specialty.SURGICAL,
            bed=LocationId("1A/BED0"),
            at=T0,
            proximity_m=-1.0,
            confidence=0.5,
            reason="test",
        )


# ---------------------------------------------------------------------------
# Criterion 6: conformance distinguishes deviation from missing data.
# test_conformance_missing_vs_deviation
# ---------------------------------------------------------------------------


def _log_from_cases(cases: dict[str, list[str]]) -> EventLog:
    events = []
    for case_id, activities in cases.items():
        when = T0
        for activity in activities:
            events.append(LogEvent(case_id=case_id, activity=activity, timestamp=when))
            when += dt.timedelta(minutes=1)
    return EventLog(events=tuple(events))


def test_conformance_missing_vs_deviation() -> None:
    """Trained on a clean `(arrive, depart) x 3` process. A truncated trace
    (stops after one activity -- nothing it did was wrong, it just never
    finished: **missing data**) and a reordered trace (**deviation**: it did
    something the model disallows) must land in different buckets, and
    neither in both (`ConformanceReport.__post_init__` enforces that)."""
    train_log = _log_from_cases(
        {
            "train0": ["arrive", "depart"] * 3,
            "train1": ["arrive", "depart"] * 3,
            "train2": ["arrive", "depart"] * 3,
        }
    )
    model = discover(train_log)

    test_log = _log_from_cases(
        {
            "truncated": ["arrive"],
            "deviant": ["depart", "arrive"],
            "clean": ["arrive", "depart"],
        }
    )
    report = conformance(test_log, model)

    assert "truncated" in report.missing_data_cases
    assert "deviant" in report.deviating_cases
    assert "truncated" not in report.deviating_cases
    assert "deviant" not in report.missing_data_cases
    assert "clean" not in report.deviating_cases
    assert "clean" not in report.missing_data_cases


def test_conformance_report_rejects_fitness_out_of_range() -> None:
    from hwpm.mining.types import ReportHeader

    with pytest.raises(ValueError, match="fitness"):
        ConformanceReport(
            fitness=1.5,
            precision=1.0,
            deviating_cases=frozenset(),
            missing_data_cases=frozenset(),
            header=ReportHeader(parameters={}, generated_at=T0),
        )


def test_conformance_report_rejects_a_case_in_both_buckets() -> None:
    from hwpm.mining.types import ReportHeader

    with pytest.raises(ValueError, match="both"):
        ConformanceReport(
            fitness=0.5,
            precision=0.5,
            deviating_cases=frozenset({"c1"}),
            missing_data_cases=frozenset({"c1"}),
            header=ReportHeader(parameters={}, generated_at=T0),
        )


# ---------------------------------------------------------------------------
# Criterion 7: every parameter appears in the report header.
# test_report_provenance
# ---------------------------------------------------------------------------


def test_report_provenance() -> None:
    train_log = _log_from_cases({"c0": ["arrive", "depart"], "c1": ["arrive", "depart"]})
    model = discover(train_log, algorithm="inductive")
    report = conformance(train_log, model)

    assert report.header.parameters["algorithm"] == "inductive"
    assert "conformance_method" in report.header.parameters
    assert report.header.generated_at is not None


def test_sensitivity_report_header_carries_every_parameter() -> None:
    config = SynthConfig(n_clinicians=2, n_patients=4, visits_per_clinician=2)
    trajectories, truth = _synthetic_trajectories(config)
    total_visits = sum(len(v) for v in truth.schedules.values())
    params = EpisodeParams(min_dwell_s=999, max_gap_s=45, min_confidence=0.9)

    report = sensitivity_analysis(
        trajectories,
        base_params=params,
        dwell_values=(60, 120),
        known_visit_count=total_visits,
    )

    assert report.header.parameters["max_gap_s"] == 45
    assert report.header.parameters["min_confidence"] == 0.9
    assert report.header.parameters["dwell_values"] == (60, 120)
    assert report.header.parameters["known_visit_count"] == total_visits


# ---------------------------------------------------------------------------
# Supporting behaviour: joins, round reconstruction, log building
# ---------------------------------------------------------------------------


def test_attach_patients_and_clinician_specialties() -> None:
    from hwpm.mining import attach_clinician_specialties, attach_patients

    episode = BedsideEpisode(
        clinician=ClinicianId("C1"),
        bed=LocationId("1A/BED0"),
        start=T0,
        end=T0 + dt.timedelta(minutes=10),
        confidence=1.0,
        source_events=(),
    )
    assert episode.patient is None
    assert episode.clinician_specialties == frozenset()

    (with_patient,) = attach_patients(
        [episode], {LocationId("1A/BED0"): PatientId("PAT001")}
    )
    assert with_patient.patient == PatientId("PAT001")

    (with_specialty,) = attach_clinician_specialties(
        [with_patient], {ClinicianId("C1"): frozenset({Specialty.CARDIOLOGY})}
    )
    assert with_specialty.clinician_specialties == frozenset({Specialty.CARDIOLOGY})
    # The patient join from the previous step survives the second join.
    assert with_specialty.patient == PatientId("PAT001")


def test_reconstruct_rounds_groups_by_clinician_and_day_ordered_by_start() -> None:
    day = T0.date()
    other_day = day + dt.timedelta(days=1)
    episodes = [
        _episode("C1", "1A/BED1", "P1", 30, 40, frozenset()),
        _episode("C1", "1A/BED0", "P0", 0, 10, frozenset()),
        _episode("C2", "1B/BED0", "P2", 0, 10, frozenset()),
        BedsideEpisode(
            clinician=ClinicianId("C1"),
            bed=LocationId("1A/BED2"),
            start=dt.datetime.combine(other_day, dt.time(8, 0)),
            end=dt.datetime.combine(other_day, dt.time(8, 10)),
            confidence=1.0,
            source_events=(),
        ),
    ]
    rounds = reconstruct_rounds(episodes, day)

    assert {r.clinician for r in rounds} == {ClinicianId("C1"), ClinicianId("C2")}
    (c1_round,) = [r for r in rounds if r.clinician == ClinicianId("C1")]
    assert [e.bed for e in c1_round.episodes] == [
        LocationId("1A/BED0"),
        LocationId("1A/BED1"),
    ]
    assert all(r.day == day for r in rounds)


def test_build_log_flattens_trajectories_by_subject_ordered_by_time() -> None:
    traj = Trajectory(
        subject=ClinicianId("C1"),
        events=(
            _event("C1", "depart", "1A/BED0", T0 + dt.timedelta(minutes=1)),
            _event("C1", "arrive", "1A/BED0", T0),
        ),
    )
    log = build_log([traj])
    assert [e.activity for e in log.events] == ["arrive", "depart"]
    assert log.case_ids() == {"C1"}


def test_episode_params_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="min_dwell_s"):
        EpisodeParams(min_dwell_s=-1)
    with pytest.raises(ValueError, match="max_gap_s"):
        EpisodeParams(max_gap_s=-1)
    with pytest.raises(ValueError, match="min_confidence"):
        EpisodeParams(min_confidence=1.5)


def test_bedside_episode_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="precedes"):
        BedsideEpisode(
            clinician=ClinicianId("C1"),
            bed=LocationId("1A/BED0"),
            start=T0,
            end=T0 - dt.timedelta(minutes=1),
            confidence=1.0,
            source_events=(),
        )


def test_bedside_episode_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        BedsideEpisode(
            clinician=ClinicianId("C1"),
            bed=LocationId("1A/BED0"),
            start=T0,
            end=T0 + dt.timedelta(minutes=1),
            confidence=1.5,
            source_events=(),
        )


def test_bedside_episode_duration_property() -> None:
    episode = BedsideEpisode(
        clinician=ClinicianId("C1"),
        bed=LocationId("1A/BED0"),
        start=T0,
        end=T0 + dt.timedelta(minutes=12),
        confidence=1.0,
        source_events=(),
    )
    assert episode.duration == dt.timedelta(minutes=12)


def test_mdt_moment_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="precedes"):
        MDTMoment(
            bed=LocationId("1A/BED0"),
            patient=PatientId("PAT001"),
            start=T0,
            end=T0 - dt.timedelta(minutes=1),
            present_specialties=frozenset({Specialty.CARDIOLOGY, Specialty.SURGICAL}),
            satisfied_specialties=frozenset({Specialty.CARDIOLOGY, Specialty.SURGICAL}),
        )


def test_mdt_moment_rejects_satisfied_not_a_subset_of_present() -> None:
    with pytest.raises(ValueError, match="subset"):
        MDTMoment(
            bed=LocationId("1A/BED0"),
            patient=PatientId("PAT001"),
            start=T0,
            end=T0 + dt.timedelta(minutes=5),
            present_specialties=frozenset({Specialty.CARDIOLOGY}),
            satisfied_specialties=frozenset({Specialty.CARDIOLOGY, Specialty.SURGICAL}),
        )
