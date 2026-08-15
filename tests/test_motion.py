"""Motion analytics tests. SPEC-003 criteria 3-7, node N07-motion-analytics.

No I/O and no real data (ADR-0005): rounds are either fabricated from typed
`BedsideEpisode`s or derived from `hwpm.ingest.synthetic`, whose `GroundTruth`
is SPEC-003's named oracle for criterion 3.

Test names follow SPEC-003's acceptance-criteria table where it gives one:
`test_motion_ground_truth`, `test_necessary_is_lower_bound`,
`test_report_completeness`, `test_determinism`, `test_suppression_floor`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import pairwise, permutations
from random import Random

import pytest

from hwpm.analytics import (
    BOUND_NODE_LIMIT,
    EXACT_NODE_LIMIT,
    BoundUnavailableError,
    InsufficientEvidenceError,
    LowerBoundViolationError,
    MotionParams,
    MotionReport,
    SuppressionFloorError,
    analyse,
    exact_open_tour_metres,
    necessary_metres,
    proven_lower_bound_metres,
)
from hwpm.domain import (
    BedsideEpisode,
    ClinicianId,
    Event,
    LocationId,
    PatientId,
    Round,
    Trajectory,
)
from hwpm.domain.travel import TravelGraph
from hwpm.ingest.synthetic import SynthConfig, generate
from hwpm.mining import (
    EpisodeParams,
    attach_patients,
    derive_episodes,
    reconstruct_rounds,
)

_DAY = date(2026, 1, 5)
_START = datetime(2026, 1, 5, 8, 0, 0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _episode(
    clinician: str,
    bed: str,
    patient: str,
    minute_offset: int,
    *,
    minutes: int = 10,
    confidence: float = 1.0,
    day: date = _DAY,
) -> BedsideEpisode:
    start = datetime.combine(day, _START.time()) + timedelta(minutes=minute_offset)
    return BedsideEpisode(
        clinician=ClinicianId(clinician),
        bed=LocationId(bed),
        start=start,
        end=start + timedelta(minutes=minutes),
        confidence=confidence,
        source_events=(),
        patient=PatientId(patient),
    )


def _round(
    clinician: str,
    beds_and_patients: list[tuple[str, str]],
    *,
    day: date = _DAY,
    confidence: float = 1.0,
    minutes: int = 10,
) -> Round:
    episodes = tuple(
        _episode(
            clinician,
            bed,
            patient,
            i * 20,
            minutes=minutes,
            confidence=confidence,
            day=day,
        )
        for i, (bed, patient) in enumerate(beds_and_patients)
    )
    return Round(clinician=ClinicianId(clinician), day=day, episodes=episodes)


def _sample_rounds() -> list[Round]:
    """Four ward-days, six patients, three clinicians: clears the ADR-0005
    floor and gives the ward-day bootstrap something to resample."""
    return [
        _round(
            "CLIN000",
            [("1A/BED0", "PAT0001"), ("2B/BED2", "PAT0002"), ("1A/BED4", "PAT0003")],
        ),
        _round(
            "CLIN001",
            [("1B/BED0", "PAT0004"), ("3C/BED3", "PAT0005")],
            confidence=0.9,
        ),
        _round(
            "CLIN002",
            [("2A/BED1", "PAT0006"), ("2A/BED5", "PAT0002"), ("1C/BED0", "PAT0001")],
        ),
        _round(
            "CLIN000",
            [("3B/BED0", "PAT0003"), ("3B/BED5", "PAT0005")],
            day=date(2026, 1, 6),
        ),
    ]


def _beds(*ids: str) -> list[LocationId]:
    return [LocationId(i) for i in ids]


def _path_metres(beds: list[LocationId], graph: TravelGraph) -> float:
    return sum(graph.cost(a, b).metres for a, b in pairwise(beds))


def _brute_force_open_path(beds: list[LocationId], graph: TravelGraph) -> float:
    """The oracle SPEC-003's "Test oracle" section names: exhaustive search
    over orderings. Free endpoints, matching `necessary_m`'s definition; the
    reversal of every permutation costs the same, so this double-counts but
    never mis-minimises. Costs are matrixed first so the O(n!) loop is
    arithmetic rather than repeated graph queries."""
    n = len(beds)
    matrix = [[graph.cost(beds[i], beds[j]).metres for j in range(n)] for i in range(n)]
    return min(
        sum(matrix[a][b] for a, b in pairwise(order)) for order in permutations(range(n))
    )


@pytest.fixture(scope="module")
def graph() -> TravelGraph:
    return TravelGraph()


# ---------------------------------------------------------------------------
# Criterion 3: observed_m against the synthetic generator's ground truth.
# ---------------------------------------------------------------------------


def _synthetic_rounds(
    graph: TravelGraph, seed: int, n_days: int = 3
) -> tuple[list[Round], float, float]:
    """Generate `n_days` synthetic ward-days, mine them into `Round`s, and
    return them alongside two ground-truth distances computed from
    `GroundTruth.schedules` -- the *routed* one (the comparand) and the
    generator's own straight-line `total_distance_m` (the contrast).
    """
    rounds: list[Round] = []
    routed_truth_m = 0.0
    euclidean_truth_m = 0.0

    for offset in range(n_days):
        day = date(2026, 1, 5) + timedelta(days=offset)
        config = SynthConfig(
            shift_start=datetime.combine(day, datetime.min.time()).replace(hour=8),
        )
        events, truth = generate(config, Random(seed + offset))

        # Bed occupancy is recoverable from the log without touching the
        # generator's internals: each visit's start_slot is the timestamp of
        # its own "arrive" event, and that event names the bed.
        arrivals = {
            (e.subject, e.timestamp): e.location for e in events if e.activity == "arrive"
        }
        occupancy: dict[LocationId, PatientId] = {}
        for clinician, visits in truth.schedules.items():
            previous: LocationId | None = None
            for visit in visits:
                bed = arrivals[(clinician, visit.start_slot)]
                occupancy[bed] = visit.patient_id
                if previous is not None:
                    routed_truth_m += graph.cost(previous, bed).metres
                previous = bed
        euclidean_truth_m += sum(truth.total_distance_m.values())

        by_subject: dict[object, list[Event]] = {}
        for event in events:
            by_subject.setdefault(event.subject, []).append(event)
        episodes = [
            episode
            for subject, subject_events in by_subject.items()
            for episode in derive_episodes(
                Trajectory(subject=subject, events=tuple(subject_events)),
                EpisodeParams(),
            )
        ]
        rounds.extend(reconstruct_rounds(attach_patients(episodes, occupancy), day))

    return rounds, routed_truth_m, euclidean_truth_m


def test_motion_ground_truth(graph: TravelGraph) -> None:
    """SPEC-003 criterion 3: `observed_m` matches the synthetic generator's
    ground truth within 1%.

    Deviation from the criterion's literal wording, recorded here because it
    is a spec/implementation disagreement and CLAUDE.md rule 1 says to name
    those rather than paper over them. The criterion says "matches
    `GroundTruth.total_distance_m` within 1%", but that field cannot be the
    comparand for two reasons, both of them by design elsewhere in the specs:

    1. `total_distance_m` is **straight-line** distance (SPEC-001's modelling
       assumption), and the entire point of SPEC-003's "The first task:
       replace Euclidean distance" is that a routed graph must *not* agree
       with it -- Euclidean "systematically understates inter-floor movement".
       Agreement within 1% would mean N06 had failed.
    2. `total_distance_m` includes the nursing-station-to-first-bed leg. The
       nursing station is not a `TravelGraph` node and not a `BedsideEpisode`
       location, so no analysis over `Round`s can see that leg.

    What criterion 3 is actually testing -- that episode derivation, round
    reconstruction and leg summation recover the *right visit sequence*, with
    no dropped, duplicated or reordered visits -- is tested exactly, by
    walking `GroundTruth.schedules` (the known visit order) through the same
    routed metric. The Euclidean figure is asserted against too, in the only
    way that is meaningful: as the strict under-estimate SPEC-003 predicts.
    """
    rounds, routed_truth_m, euclidean_truth_m = _synthetic_rounds(graph, seed=2026)
    report = analyse(rounds, graph, Random(11))

    assert report.observed_m == pytest.approx(routed_truth_m, rel=0.01)
    # SPEC-003's premise, asserted rather than assumed: the routed distance is
    # materially larger than the straight-line one the prototype used.
    assert routed_truth_m > euclidean_truth_m


# ---------------------------------------------------------------------------
# Criterion 4: necessary_m <= observed_m always; violation raises.
# ---------------------------------------------------------------------------


def test_necessary_is_lower_bound(graph: TravelGraph) -> None:
    """Criterion 4, in all three of the senses it has to hold in: on real
    reports, against the exact optimum, and at construction time."""
    report = analyse(_sample_rounds(), graph, Random(1))
    assert report.necessary_m <= report.observed_m

    # The bound holds per round, not just in aggregate.
    for round_ in _sample_rounds():
        beds = [e.bed for e in round_.episodes]
        assert necessary_metres(beds, graph).metres <= _path_metres(beds, graph) + 1e-9

    # Violation raises rather than being clamped or reported.
    with pytest.raises(LowerBoundViolationError):
        MotionReport(
            observed_m=10.0,
            necessary_m=20.0,
            attributable_m=-10.0,
            ci95=(0.0, 1.0),
            params={"method_version": "test"},
        )


@pytest.mark.parametrize("n", [3, 5, 7, 9])
def test_exact_optimum_matches_brute_force(graph: TravelGraph, n: int) -> None:
    """SPEC-003's test oracle for `necessary_m`: brute-forced optimal tours on
    <=10-bed instances. The subset DP must agree exactly."""
    pool = _beds(
        "1A/BED0",
        "1A/BED3",
        "1B/BED1",
        "1C/BED5",
        "2A/BED0",
        "2B/BED2",
        "2C/BED4",
        "3A/BED1",
        "3B/BED3",
        "3C/BED5",
    )
    beds = pool[:n]
    assert exact_open_tour_metres(beds, graph) == pytest.approx(
        _brute_force_open_path(beds, graph), rel=1e-9
    )


@pytest.mark.parametrize("n", [4, 6, 8])
def test_proven_bound_never_exceeds_the_optimum(graph: TravelGraph, n: int) -> None:
    """The property that makes `attributable_m` an upper bound rather than an
    assertion: the large-instance bound is genuinely below the optimum, so a
    heuristic tour has not crept in anywhere (SPEC-003 "Failure modes")."""
    pool = _beds(
        "1A/BED0",
        "2B/BED2",
        "3C/BED5",
        "1C/BED1",
        "2A/BED4",
        "3B/BED0",
        "1B/BED3",
        "2C/BED1",
    )
    beds = pool[:n]
    optimum = _brute_force_open_path(beds, graph)
    assert proven_lower_bound_metres(beds, graph) <= optimum + 1e-9


def test_bound_is_used_and_refused_at_the_documented_sizes(graph: TravelGraph) -> None:
    """Exact below `EXACT_NODE_LIMIT`, proven bound above it, refusal above
    `BOUND_NODE_LIMIT` -- and never a heuristic tour at any size."""
    all_beds = [
        LocationId(f"{ward}/BED{i}")
        for ward in ("1A", "1B", "1C", "2A", "2B", "2C", "3A", "3B", "3C")
        for i in range(6)
    ]
    assert len(all_beds) > EXACT_NODE_LIMIT

    small = all_beds[:EXACT_NODE_LIMIT]
    assert necessary_metres(small, graph).exact is True

    large = all_beds[: EXACT_NODE_LIMIT + 4]
    result = necessary_metres(large, graph)
    assert result.exact is False
    assert "proven lower bound" in result.method
    assert result.metres <= _path_metres(large, graph) + 1e-9

    with pytest.raises(BoundUnavailableError, match="BOUND_NODE_LIMIT"):
        necessary_metres(
            [LocationId(f"synthetic/{i}") for i in range(BOUND_NODE_LIMIT + 1)],
            graph,
        )


# ---------------------------------------------------------------------------
# Criterion 5: non-degenerate ci95 and populated params, with no way round it.
# ---------------------------------------------------------------------------


def test_report_completeness(graph: TravelGraph) -> None:
    report = analyse(_sample_rounds(), graph, Random(7))

    lo, hi = report.ci95
    assert hi > lo
    assert report.params
    for key in (
        "method_version",
        "interpretation",
        "n_ward_days",
        "n_patients",
        "n_clinicians",
        "necessary_m_methods",
        "ci_method",
        "n_replicates",
        "min_dwell_s_sweep",
        "uncertainty_sources",
    ):
        assert key in report.params, key

    # All three SPEC-003 uncertainty sources are named in the provenance.
    sources = " ".join(report.params["uncertainty_sources"])
    assert "min_dwell_s" in sources
    assert "mapping confidence" in sources
    assert "ward-day" in sources

    # The wording SPEC-003 mandates is carried, not assumed.
    assert "upper bound" in str(report.params["interpretation"]).lower()
    rendered = report.render()
    assert "upper bound on avoidable motion" in rendered
    assert "at most" in rendered.lower()
    assert "could save" not in rendered.lower()


def test_report_has_no_constructor_without_an_interval() -> None:
    """SPEC-003 criterion 5: "MotionReport has no constructor that permits"
    a point estimate without an interval."""
    with pytest.raises(TypeError):
        MotionReport(observed_m=10.0, necessary_m=5.0, attributable_m=5.0)  # type: ignore[call-arg]

    with pytest.raises(ValueError, match="non-degenerate"):
        MotionReport(
            observed_m=10.0,
            necessary_m=5.0,
            attributable_m=5.0,
            ci95=(3.0, 3.0),
            params={"method_version": "test"},
        )

    with pytest.raises(ValueError, match="ci95 must be a"):
        MotionReport(
            observed_m=10.0,
            necessary_m=5.0,
            attributable_m=5.0,
            ci95=None,  # type: ignore[arg-type]
            params={"method_version": "test"},
        )

    with pytest.raises(ValueError, match="params must be populated"):
        MotionReport(
            observed_m=10.0,
            necessary_m=5.0,
            attributable_m=5.0,
            ci95=(3.0, 4.0),
            params={},
        )


def test_degenerate_evidence_is_refused_not_padded(graph: TravelGraph) -> None:
    """Rather than manufacture an interval to satisfy its own constructor,
    `analyse` refuses. One ward-day cannot support a bootstrap over
    ward-days."""
    rounds = [
        _round(
            "CLIN000",
            [("1A/BED0", "PAT0001"), ("1A/BED1", "PAT0002"), ("1A/BED2", "PAT0003")],
        ),
        _round("CLIN001", [("1A/BED3", "PAT0004"), ("1A/BED4", "PAT0005")]),
    ]
    with pytest.raises(InsufficientEvidenceError, match="ward-day"):
        analyse(rounds, graph, Random(0))


# ---------------------------------------------------------------------------
# Criterion 6: reproducible under a fixed seed.
# ---------------------------------------------------------------------------


def test_determinism(graph: TravelGraph) -> None:
    rounds = _sample_rounds()
    first = analyse(rounds, graph, Random(99))
    second = analyse(rounds, graph, Random(99))

    assert first == second
    assert first.ci95 == second.ci95
    assert first.attributable_m == second.attributable_m

    # A different seed exercises a different resampling path; the point
    # estimate, which draws no randomness, must not move with it.
    third = analyse(rounds, graph, Random(12345))
    assert third.observed_m == first.observed_m
    assert third.necessary_m == first.necessary_m


def test_replicate_count_changes_only_the_interval(graph: TravelGraph) -> None:
    rounds = _sample_rounds()
    few = analyse(rounds, graph, Random(4), MotionParams(n_replicates=50))
    many = analyse(rounds, graph, Random(4), MotionParams(n_replicates=400))
    assert few.observed_m == pytest.approx(many.observed_m)
    assert few.necessary_m == pytest.approx(many.necessary_m)


# ---------------------------------------------------------------------------
# Criterion 7: the ADR-0005 suppression floor.
# ---------------------------------------------------------------------------


def test_suppression_floor(graph: TravelGraph) -> None:
    """ADR-0005 rule 4, enforced in code: no output cell derived from fewer
    than 5 patients, and none attributable to a single named clinician.
    ADR-0006 rule 5 allows no governance exemption from either."""
    # Fewer than five patients.
    too_few_patients = [
        _round("CLIN000", [("1A/BED0", "PAT0001"), ("2B/BED2", "PAT0002")]),
        _round("CLIN001", [("3C/BED0", "PAT0003"), ("1B/BED1", "PAT0004")]),
    ]
    with pytest.raises(SuppressionFloorError, match="distinct patient"):
        analyse(too_few_patients, graph, Random(0))

    # Five patients, but one clinician: still refused.
    single_clinician = [
        _round(
            "CLIN000",
            [("1A/BED0", "PAT0001"), ("2B/BED2", "PAT0002"), ("3C/BED4", "PAT0003")],
        ),
        _round(
            "CLIN000",
            [("1B/BED0", "PAT0004"), ("2A/BED3", "PAT0005")],
            day=date(2026, 1, 6),
        ),
    ]
    with pytest.raises(SuppressionFloorError, match="single named clinician"):
        analyse(single_clinician, graph, Random(0))

    # Episodes with no patient join: the floor is unverifiable, so refused
    # rather than assumed to pass.
    unjoined = [
        Round(
            clinician=ClinicianId("CLIN000"),
            day=_DAY,
            episodes=tuple(
                BedsideEpisode(
                    clinician=ClinicianId("CLIN000"),
                    bed=LocationId(f"1A/BED{i}"),
                    start=_START + timedelta(minutes=20 * i),
                    end=_START + timedelta(minutes=20 * i + 10),
                    confidence=1.0,
                    source_events=(),
                )
                for i in range(6)
            ),
        ),
        _round("CLIN001", [("2A/BED0", "PAT0009")], day=date(2026, 1, 6)),
    ]
    with pytest.raises(SuppressionFloorError, match="no patient attribution"):
        analyse(unjoined, graph, Random(0))

    # And the floor lets a legitimate cohort through.
    assert analyse(_sample_rounds(), graph, Random(0)).observed_m > 0


def test_suppression_floor_precedes_any_computation(graph: TravelGraph) -> None:
    """The refusal must not depend on the analysis succeeding: a cohort that
    breaches the floor is refused even when the motion figures themselves
    would have been fine."""
    rounds = [_round("CLIN000", [("1A/BED0", "PAT0001")])]
    with pytest.raises(SuppressionFloorError):
        analyse(rounds, graph, Random(0))


# ---------------------------------------------------------------------------
# Honesty of the headline number.
# ---------------------------------------------------------------------------


def test_attributable_is_never_reported_as_a_saving(graph: TravelGraph) -> None:
    report = analyse(_sample_rounds(), graph, Random(21))
    assert report.attributable_m == pytest.approx(report.observed_m - report.necessary_m)
    assert 0.0 <= report.attributable_fraction <= 1.0
    text = report.render().lower()
    for forbidden in ("we could save", "savings", "will save", "we can save"):
        assert forbidden not in text
    # "target" may only ever appear as a disclaimer, never as a claim.
    assert "not a\ntarget" in text or "not a target" in text


def test_necessary_depends_on_the_visit_set_not_the_order(graph: TravelGraph) -> None:
    """`necessary_m` is the optimum over the same visit *set*, so a bad order
    and a good order over identical beds must yield the same bound -- and the
    bad order must show more attributable motion."""
    beds = _beds("1A/BED0", "3C/BED5", "1A/BED1", "3C/BED0")
    reordered = [beds[0], beds[2], beds[3], beds[1]]

    assert necessary_metres(beds, graph).metres == pytest.approx(
        necessary_metres(reordered, graph).metres
    )
    assert _path_metres(beds, graph) > _path_metres(reordered, graph)
