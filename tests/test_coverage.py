"""MDT coverage governance-metric tests. ADR-0006, SPEC-005 node N18.

No I/O and no real data (ADR-0005): episodes are fabricated `BedsideEpisode`s,
same style as `tests/test_motion.py`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from random import Random

import pytest

from hwpm.analytics.coverage import (
    CoverageCell,
    CoverageParams,
    CoverageReport,
    InsufficientCoverageEvidenceError,
    analyse_coverage,
    wilson_interval,
)
from hwpm.analytics.suppression import SuppressionFloorError, check_counts
from hwpm.domain import BedsideEpisode, ClinicianId, LocationId, PatientId, Specialty

_START = datetime(2026, 2, 2, 8, 0, 0)


def _episode(
    clinician: str,
    bed: str,
    patient: str,
    minute_offset: int,
    specialties: frozenset[Specialty],
    *,
    minutes: int = 10,
    day: date = date(2026, 2, 2),
) -> BedsideEpisode:
    start = datetime.combine(day, _START.time()) + timedelta(minutes=minute_offset)
    return BedsideEpisode(
        clinician=ClinicianId(clinician),
        bed=LocationId(f"W1/{bed}"),
        start=start,
        end=start + timedelta(minutes=minutes),
        confidence=1.0,
        source_events=(),
        patient=PatientId(patient),
        clinician_specialties=specialties,
    )


CARDIO = frozenset({Specialty.CARDIOLOGY})
NEPHRO = frozenset({Specialty.NEPHROLOGY})
GEN_MED = frozenset({Specialty.GENERAL_MEDICINE})


def _multi_day_ward(
    n_days: int, n_patients: int, covered_fraction: float
) -> tuple[list[BedsideEpisode], dict[PatientId, frozenset[Specialty]]]:
    """`n_days` ward-days, each with `n_patients` multi-specialty patients.
    `covered_fraction` of them get a genuine two-specialty co-presence; the
    rest get only one specialty (never counted, ADR-0006 rule 4)."""
    required: dict[PatientId, frozenset[Specialty]] = {}
    episodes: list[BedsideEpisode] = []
    n_covered = round(n_patients * covered_fraction)
    for d in range(n_days):
        day = date(2026, 2, 2) + timedelta(days=d)
        for p in range(n_patients):
            patient = f"P{d}-{p}"
            required[PatientId(patient)] = CARDIO | NEPHRO
            bed = f"BED{p}"
            episodes.append(_episode(f"CARD-{d}", bed, patient, p * 20, CARDIO, day=day))
            if p < n_covered:
                episodes.append(
                    _episode(f"NEPH-{d}", bed, patient, p * 20 + 1, NEPHRO, day=day)
                )
    return episodes, required


# ---------------------------------------------------------------------------
# wilson_interval
# ---------------------------------------------------------------------------


def test_wilson_interval_bounds_and_symmetry() -> None:
    lo, hi = wilson_interval(5, 10)
    assert 0.0 <= lo < 0.5 < hi <= 1.0
    lo0, hi0 = wilson_interval(0, 10)
    assert lo0 == 0.0
    assert hi0 > 0.0  # not degenerate at zero successes
    lo1, hi1 = wilson_interval(10, 10)
    assert hi1 == pytest.approx(1.0)
    assert lo1 < 1.0  # not degenerate at all successes


def test_wilson_interval_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        wilson_interval(1, 0)
    with pytest.raises(ValueError):
        wilson_interval(5, 3)


# ---------------------------------------------------------------------------
# check_counts (suppression.py)
# ---------------------------------------------------------------------------


def test_check_counts_floors() -> None:
    check_counts(5, 2)  # exactly at both floors: passes
    with pytest.raises(SuppressionFloorError):
        check_counts(4, 2)
    with pytest.raises(SuppressionFloorError):
        check_counts(5, 1)


# ---------------------------------------------------------------------------
# CoverageCell / CoverageReport construction discipline
# ---------------------------------------------------------------------------


def test_suppressed_cell_carries_no_numbers() -> None:
    with pytest.raises(ValueError):
        CoverageCell(
            day=date(2026, 2, 2),
            ward="W1",
            suppressed=True,
            n_patients=5,  # a suppressed cell must carry nothing
            suppression_reason="below floor",
        )


def test_suppressed_cell_needs_a_reason() -> None:
    with pytest.raises(ValueError):
        CoverageCell(day=date(2026, 2, 2), ward="W1", suppressed=True)


def test_published_cell_needs_its_interval() -> None:
    with pytest.raises(ValueError):
        CoverageCell(
            day=date(2026, 2, 2),
            ward="W1",
            suppressed=False,
            n_patients=6,
            n_covered=3,
            coverage=0.5,
            ci95=None,
        )


def test_coverage_report_rejects_bare_percentage() -> None:
    with pytest.raises(ValueError):
        CoverageReport(
            coverage=0.5,
            ci95=None,  # type: ignore[arg-type]
            n_patients=10,
            n_covered=5,
            cells=(),
            params={"strategy": "referral"},
        )


def test_coverage_report_requires_params() -> None:
    with pytest.raises(ValueError):
        CoverageReport(
            coverage=0.5,
            ci95=(0.2, 0.8),
            n_patients=10,
            n_covered=5,
            cells=(),
            params={},
        )


# ---------------------------------------------------------------------------
# analyse_coverage: the real thing
# ---------------------------------------------------------------------------


def test_analyse_coverage_ground_truth() -> None:
    """A known 6-of-10 coverage over 3 ward-days lands where arithmetic says,
    with an interval that actually brackets it and floors that clear."""
    episodes, required = _multi_day_ward(n_days=3, n_patients=10, covered_fraction=0.6)
    report = analyse_coverage(episodes, required, Random(7), strategy="referral")
    assert report.n_patients == 30
    assert report.n_covered == 18
    assert report.coverage == pytest.approx(0.6)
    lo, hi = report.ci95
    assert lo < report.coverage < hi
    assert report.n_published_cells + report.n_suppressed_cells == len(report.cells)


def test_analyse_coverage_required_not_merely_present() -> None:
    """A patient visited by two specialties who never overlap within the
    window scores 0, not 1 -- 'required', not 'merely present' (ADR-0006
    rule 4, mirroring evaluate._copresence)."""
    day = date(2026, 2, 2)
    required = {PatientId("P0"): CARDIO | NEPHRO}
    # Two visits four hours apart: same bed, both specialties, no genuine
    # co-presence within the default 300s window.
    far_apart = [
        _episode("CARD-0", "BED0", "P0", 0, CARDIO, day=day),
        _episode("NEPH-0", "BED0", "P0", 240, NEPHRO, day=day),
    ]
    # A second, otherwise-identical ward-day so the resampler has >=2 units.
    day2 = date(2026, 2, 3)
    far_apart += [
        _episode("CARD-0", "BED0", "P0", 0, CARDIO, day=day2),
        _episode("NEPH-0", "BED0", "P0", 240, NEPHRO, day=day2),
    ]
    # Pad the denominator to clear the suppression floor without adding any
    # covered patient, so the "never counts a collision" property is visible
    # in a non-degenerate (not 0/N with N<5) proportion.
    for i in range(1, 5):
        required[PatientId(f"P{i}")] = CARDIO | NEPHRO
        far_apart.append(_episode(f"CARD-{i}", f"BED{i}", f"P{i}", 0, CARDIO, day=day))
        far_apart.append(_episode(f"CARD-{i}", f"BED{i}", f"P{i}", 0, CARDIO, day=day2))
    report = analyse_coverage(far_apart, required, Random(3), strategy="referral")
    assert report.n_covered == 0  # P0's two far-apart visits never count


def test_analyse_coverage_below_floor_raises() -> None:
    episodes, required = _multi_day_ward(n_days=2, n_patients=2, covered_fraction=1.0)
    with pytest.raises(SuppressionFloorError):
        analyse_coverage(episodes, required, Random(1), strategy="referral")


def test_analyse_coverage_needs_two_ward_days() -> None:
    episodes, required = _multi_day_ward(n_days=1, n_patients=10, covered_fraction=0.5)
    with pytest.raises(InsufficientCoverageEvidenceError):
        analyse_coverage(episodes, required, Random(1), strategy="referral")


def test_analyse_coverage_empty_denominator_raises() -> None:
    """No multi-specialty patient observed -- the honest output is a refusal,
    not a 0% or 100% invention."""
    day1, day2 = date(2026, 2, 2), date(2026, 2, 3)
    episodes = [
        _episode("CARD-0", "BED0", "P0", 0, CARDIO, day=day1),
        _episode("CARD-1", "BED1", "P1", 0, CARDIO, day=day2),
    ]
    required = {PatientId("P0"): CARDIO, PatientId("P1"): CARDIO}  # single-specialty
    with pytest.raises(InsufficientCoverageEvidenceError):
        analyse_coverage(episodes, required, Random(1), strategy="referral")


def test_analyse_coverage_determinism() -> None:
    episodes, required = _multi_day_ward(n_days=4, n_patients=8, covered_fraction=0.4)
    a = analyse_coverage(episodes, required, Random(42), strategy="referral")
    b = analyse_coverage(episodes, required, Random(42), strategy="referral")
    assert a.coverage == b.coverage
    assert a.ci95 == b.ci95
    assert a.n_patients == b.n_patients
    assert a.n_covered == b.n_covered


def test_analyse_coverage_strategy_travels_with_the_number() -> None:
    """ADR-0006 rule 2: the denominator's strategy key is always in params."""
    episodes, required = _multi_day_ward(n_days=3, n_patients=10, covered_fraction=0.5)
    report = analyse_coverage(episodes, required, Random(9), strategy="union")
    assert report.params["strategy"] == "union"
    assert "coverage was" in report.render() or "MDT coverage" in report.render()


def test_analyse_coverage_small_cells_are_suppressed_but_still_count() -> None:
    """Per-day cells with <5 multi-specialty patients are withheld
    individually (ADR-0005 rule 4) but still contribute to an aggregate that
    itself clears the floor (coverage.py's stated policy, rule 2)."""
    # 6 ward-days of 5 patients each: each cell is exactly at the floor
    # (5 patients, 2 clinicians) and is NOT suppressed here; shrink to 3 to
    # force per-cell suppression while the 6-day aggregate (18 patients)
    # still clears.
    episodes, required = _multi_day_ward(n_days=6, n_patients=3, covered_fraction=0.5)
    report = analyse_coverage(episodes, required, Random(5), strategy="referral")
    assert report.n_suppressed_cells == len(report.cells)
    assert report.n_published_cells == 0
    assert report.n_patients == 18  # every patient still counted in the aggregate


def test_coverage_params_validates() -> None:
    with pytest.raises(ValueError):
        CoverageParams(window_s=-1)
    with pytest.raises(ValueError):
        CoverageParams(n_replicates=1)
