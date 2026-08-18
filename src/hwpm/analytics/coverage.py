"""MDT coverage as a governance-monitored measurement. ADR-0006, SPEC-005 N18.

This is the module behind the Clinical Governance view. It answers one
question -- *for what fraction of the patients who needed a multi-specialty
review did that review actually happen at the bedside* -- and it is built to
survive being reported upward, compared across periods and challenged, which
is what ADR-0006 says makes it a different artefact from the same arithmetic
in a study.

What the figure is
------------------
For a ward-day, the denominator is the distinct **multi-specialty patients**
observed on that ward that day: patients whose `required` set holds >=2
specialties under the selected `RequiredSpecialty` strategy. Patients needing
one specialty cannot have an MDT moment by definition, and including them
would make coverage improve as a ward admits more single-specialty patients
(`hwpm.optimize.types.Instance.multi_specialty_patients` says the same thing
for objective 1, and this module deliberately keeps the same denominator so
the governance figure and the optimiser's objective are the same quantity).

The numerator is the subset of that denominator with at least one
`MDTMoment` -- >=2 of *that patient's required* specialties simultaneously at
their bedside within `window_s` (`hwpm.mining.mdt.detect_mdt_moments`).
**Required, not merely present**, exactly as `hwpm.optimize.evaluate._copresence`
defines it: 01-DOMAIN-MODEL.md calls a gathering that satisfies no requirement
"two people who happened to collide", and counting collisions would make the
governance figure improve when clinicians bump into each other in a corridor.

Coverage is derived from observed `BedsideEpisode`s only -- ADR-0006 rule 4.
There is no parameter, no argument and no code path in this module through
which an attested or self-reported MDT can enter. That is the single
load-bearing anti-gaming property of the metric, and it is protected here by
the function simply not accepting such an input.

Uncertainty (ADR-0006 rule 6: "a governance dashboard showing a bare
percentage is a defect")
------------------------------------------------------------------------
Two interval methods, because the two figures are different shapes:

* **Per ward-day cell: Wilson score interval.** A cell is a proportion of a
  small, countable denominator (five to a dozen patients). Wilson is the right
  tool there and is chosen over a bootstrap for a specific reason: at 0/6 or
  6/6 -- which real ward-days produce constantly -- a bootstrap returns
  [0, 0] or [1, 1], a point estimate wearing a costume, whereas Wilson still
  carries the uncertainty that six observations genuinely leave. It is also
  deterministic, so a published cell does not move when the seed does.

* **Headline aggregate: the envelope of a two-stage ward-day cluster bootstrap
  and the pooled Wilson interval.** Patients on the same ward-day are not
  independent -- one consultant's ward round covers many of them at once -- so
  a pooled Wilson interval alone would treat correlated observations as
  independent and report a narrower interval than the evidence supports.
  Motion (SPEC-003) resamples ward-days for the same reason and this module
  uses the same resampling unit. The bootstrap is two-stage (ward-days with
  replacement, then patients within each drawn ward-day) so it carries the
  binomial component as well as the between-day component. The reported
  interval is `(min(lo), max(hi))` of the two methods: never narrower than the
  binomial interval, and never narrower than the observed between-day spread.
  Both component intervals are recorded in `params` so an auditor can see
  which bound is doing the work.

Neither method is a *calibrated* statement about a future period. Coverage is
a measurement of what was observed; the interval describes sampling and
clustering variation in that measurement, not a forecast of next month.
`CoverageReport.render()` says so, and is the supported way to put this figure
into words.

Suppression (ADR-0005 rule 4, ADR-0006 rule 5)
---------------------------------------------
`suppression.py`'s docstring anticipated this module: "A future cell-wise
report (N18) can catch `SuppressionFloorError` per cell and suppress that
cell; it must not be able to *lower* the floor." That is what happens here --
the floor constants and the check are imported, never restated, so there is
one definition of the floor in the codebase and this module cannot weaken it.

Two decisions worth stating because both go the strict way:

1. **A suppressed cell carries no numbers at all** -- not the coverage, not
   the interval, not the patient count. `CoverageCell`'s constructor refuses a
   cell that is marked suppressed and still holds a value, so a rendering path
   cannot find one to display.
2. **Suppressed cells still count toward the aggregate.** Dropping them would
   bias the headline toward whatever the larger ward-days happened to show.
   ADR-0005 forbids publishing a cell derived from fewer than five patients;
   it does not forbid those patients contributing to a figure that clears the
   floor itself -- and the aggregate is checked against the floor too, so the
   published headline never rests on fewer than five patients either.

Determinism: every draw comes from the `rng` argument (CLAUDE.md rule 3). Two
calls to `analyse_coverage(..., Random(7))` compare equal.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from random import Random

from hwpm.analytics.suppression import (
    MIN_CLINICIANS,
    MIN_PATIENTS,
    SuppressionFloorError,
    check_counts,
)
from hwpm.domain import BedsideEpisode, ClinicianId, PatientId, Specialty
from hwpm.mining.mdt import detect_mdt_moments

#: ADR-0006 rule 1. A change to the co-presence window, the denominator
#: definition or either interval method increments this, and the back-series
#: is recomputed and re-published with it. Silent recomputation is prohibited.
METHOD_VERSION = "coverage/1.0.0"

#: Two-sided normal quantile at 95%. Named rather than inlined so the Wilson
#: interval and the bootstrap percentiles cannot drift to different levels.
Z_95 = 1.959963984540054

#: The sentence this figure has to be reported in. Carried into `params` and
#: into `render()` so it travels with the number, the same habit
#: `hwpm.analytics.motion.ATTRIBUTABLE_M_WORDING` establishes.
COVERAGE_WORDING = (
    "MDT coverage is the fraction of observed multi-specialty patients for whom "
    ">=2 of their REQUIRED specialties were simultaneously at the bedside, "
    "derived from observed events only (ADR-0006 rule 4). It is measured, never "
    "attested. The interval is sampling and ward-day clustering variation in "
    "what was observed -- not a forecast, not a target, and not a tolerance. "
    "Quote it as 'coverage was X% (95% CI A to B) under the {strategy} "
    "definition', never as a bare percentage (ADR-0006 rule 6)."
)


class InsufficientCoverageEvidenceError(RuntimeError):
    """The inputs cannot support an interval, so they cannot support a figure.

    Raised rather than returning a bare proportion. ADR-0006 rule 6 makes a
    percentage without its interval a defect, and the only honest response to
    evidence too thin to form one is to decline.
    """


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion, clamped to [0, 1].

    Chosen over the normal approximation because the normal interval is
    degenerate at 0 and n successes and can leave [0, 1] entirely -- both of
    which happen on ordinary ward-days, which is exactly when a governance
    reader is most likely to quote the number.
    """
    if n <= 0:
        raise ValueError("a proportion needs at least one observation")
    if not 0 <= successes <= n:
        raise ValueError(f"successes {successes!r} not in [0, {n}]")
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = (z / denominator) * math.sqrt(p * (1.0 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class CoverageParams:
    """Every field a modelling assumption, none of them a fact."""

    #: SPEC-002's co-presence window, verbatim ("Co-presence within 300s =
    #: joint review"). Passed to `detect_mdt_moments` rather than re-derived.
    window_s: int = 300
    #: Bootstrap replicates. Percentile intervals at 2.5/97.5 want a few
    #: hundred at minimum before each tail stops being one observation wide.
    n_replicates: int = 1000

    def __post_init__(self) -> None:
        if self.window_s < 0:
            raise ValueError(f"window_s must be >= 0, got {self.window_s!r}")
        if self.n_replicates < 2:
            raise ValueError(
                f"n_replicates must be >= 2 to form an interval, got "
                f"{self.n_replicates!r}"
            )


@dataclass(frozen=True)
class CoverageCell:
    """One ward-day's coverage, or the statement that it is withheld.

    The constructor enforces the two states and permits nothing between them:
    either every field is present with a non-degenerate interval, or the cell
    is suppressed and carries no number at all. There is no path to a cell
    holding a proportion without its interval, and none to a suppressed cell
    whose value a rendering path could still read.
    """

    day: date
    ward: str
    suppressed: bool
    n_patients: int | None = None
    n_covered: int | None = None
    coverage: float | None = None
    ci95: tuple[float, float] | None = None
    suppression_reason: str | None = None

    def __post_init__(self) -> None:
        if self.suppressed:
            leaked = [
                name
                for name in ("n_patients", "n_covered", "coverage", "ci95")
                if getattr(self, name) is not None
            ]
            if leaked:
                raise ValueError(
                    f"suppressed cell {self.ward}/{self.day} still carries "
                    f"{leaked}; a withheld cell carries no numbers, not even a "
                    "denominator (ADR-0005 rule 4)"
                )
            if not self.suppression_reason:
                raise ValueError(
                    "a suppressed cell must say why it was suppressed; an "
                    "unexplained gap reads as missing data, not as a rule"
                )
            return

        if self.suppression_reason is not None:
            raise ValueError("a published cell must not carry a suppression reason")
        if self.n_patients is None or self.n_covered is None or self.coverage is None:
            raise ValueError("a published cell must carry its counts and proportion")
        if not 0 <= self.n_covered <= self.n_patients:
            raise ValueError(
                f"n_covered {self.n_covered!r} not in [0, {self.n_patients!r}]"
            )
        if self.ci95 is None or len(self.ci95) != 2:
            raise ValueError(
                f"cell {self.ward}/{self.day} has no interval. A coverage figure "
                "without one is a defect (ADR-0006 rule 6)"
            )
        lo, hi = self.ci95
        if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
            raise ValueError(f"ci95 must be finite and non-degenerate, got {self.ci95!r}")


@dataclass(frozen=True)
class CoverageReport:
    """The headline governance figure, and the cells behind it.

    Mirrors `MotionReport`'s contract: no field has a default, `ci95` is
    required and validated, and `params` must be populated. There is no
    constructor here that produces the bare percentage ADR-0006 rule 6 calls a
    defect.
    """

    coverage: float
    ci95: tuple[float, float]
    n_patients: int
    n_covered: int
    cells: tuple[CoverageCell, ...]
    params: dict[str, object]

    def __post_init__(self) -> None:
        if not 0 <= self.n_covered <= self.n_patients:
            raise ValueError(
                f"n_covered {self.n_covered!r} not in [0, {self.n_patients!r}]"
            )
        if not math.isfinite(self.coverage) or not 0.0 <= self.coverage <= 1.0:
            raise ValueError(
                f"coverage must be a fraction in [0, 1], got {self.coverage!r}"
            )
        if not isinstance(self.ci95, tuple) or len(self.ci95) != 2:
            raise ValueError(
                f"ci95 must be a (lo, hi) tuple, got {self.ci95!r}. A coverage "
                "figure without its interval is a defect (ADR-0006 rule 6)"
            )
        lo, hi = self.ci95
        if not (math.isfinite(lo) and math.isfinite(hi)):
            raise ValueError(f"ci95 bounds must be finite, got {self.ci95!r}")
        if hi - lo <= 0:
            raise ValueError(
                f"ci95 must be non-degenerate (hi > lo), got {self.ci95!r}; a "
                "zero-width interval is a point estimate wearing a costume"
            )
        if not 0.0 <= lo <= hi <= 1.0:
            raise ValueError(f"ci95 must lie inside [0, 1], got {self.ci95!r}")
        if not self.params:
            raise ValueError(
                "params must be populated: a governance figure without its "
                "provenance is not publishable (ADR-0006 rules 1 and 2)"
            )
        object.__setattr__(self, "params", dict(self.params))

    @property
    def n_published_cells(self) -> int:
        return sum(1 for cell in self.cells if not cell.suppressed)

    @property
    def n_suppressed_cells(self) -> int:
        return sum(1 for cell in self.cells if cell.suppressed)

    def render(self) -> str:
        """The figure as text, with the qualification attached.

        Call this rather than formatting `coverage` yourself: the interval is
        the part that gets dropped on the way into a slide, and the only
        reliable defence is for the correct sentence to be the easy one.
        """
        lo, hi = self.ci95
        strategy = self.params.get("strategy", "unstated")
        return (
            f"MDT coverage ({self.params.get('method_version', METHOD_VERSION)}, "
            f"definition: {strategy})\n"
            f"  {100 * self.coverage:.1f}%   95% CI "
            f"[{100 * lo:.1f}%, {100 * hi:.1f}%]\n"
            f"  {self.n_covered} of {self.n_patients} multi-specialty patients, "
            f"over {len(self.cells)} ward-days "
            f"({self.n_suppressed_cells} withheld under the ADR-0005 floor)\n"
            "\nMeasured from observed bedside co-presence of required "
            "specialties.\nNever attested, never self-reported. The interval is "
            "sampling and\nward-day clustering variation, not a forecast."
        )


# ---------------------------------------------------------------------------
# Cell construction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Unit:
    """One ward-day reduced to what the bootstrap needs: an outcome per
    multi-specialty patient, 1 if their required review happened at the
    bedside. Replicates never touch a `BedsideEpisode` again."""

    day: date
    ward: str
    outcomes: tuple[int, ...]
    n_clinicians: int


def _units(
    episodes: Sequence[BedsideEpisode],
    required: Mapping[PatientId, frozenset[Specialty]],
    window_s: int,
) -> list[_Unit]:
    from hwpm.analytics.motion import ward_of

    grouped: dict[tuple[date, str], list[BedsideEpisode]] = {}
    for episode in episodes:
        grouped.setdefault((episode.start.date(), ward_of(episode.bed)), []).append(
            episode
        )

    units: list[_Unit] = []
    for (day, ward), cell_episodes in sorted(grouped.items()):
        denominator: set[PatientId] = set()
        clinicians: set[ClinicianId] = set()
        for episode in cell_episodes:
            clinicians.add(episode.clinician)
            if episode.patient is None:
                continue
            if len(required.get(episode.patient, frozenset())) >= 2:
                denominator.add(episode.patient)
        covered = {
            moment.patient
            for moment in detect_mdt_moments(cell_episodes, required, window_s)
        }
        units.append(
            _Unit(
                day=day,
                ward=ward,
                outcomes=tuple(
                    1 if patient in covered else 0
                    for patient in sorted(denominator, key=lambda p: p.value)
                ),
                n_clinicians=len(clinicians),
            )
        )
    return units


def _cell_of(unit: _Unit) -> CoverageCell:
    """One unit as a publishable cell, or as a withheld one.

    The floor check is `suppression.check_counts` -- the same function
    `enforce_suppression_floor` calls -- so a cell here and an aggregate there
    are held to one definition of the floor.
    """
    n = len(unit.outcomes)
    try:
        check_counts(n, unit.n_clinicians)
    except SuppressionFloorError as exc:
        return CoverageCell(
            day=unit.day, ward=unit.ward, suppressed=True, suppression_reason=str(exc)
        )
    covered = sum(unit.outcomes)
    return CoverageCell(
        day=unit.day,
        ward=unit.ward,
        suppressed=False,
        n_patients=n,
        n_covered=covered,
        coverage=covered / n,
        ci95=wilson_interval(covered, n),
    )


def _percentile(ordered: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted sequence."""
    if not ordered:
        raise ValueError("cannot take a percentile of an empty sample")
    k = (len(ordered) - 1) * (pct / 100.0)
    low = math.floor(k)
    high = math.ceil(k)
    if low == high:
        return ordered[int(k)]
    return ordered[low] + (ordered[high] - ordered[low]) * (k - low)


def _cluster_bootstrap(
    units: Sequence[_Unit], rng: Random, n_replicates: int
) -> tuple[float, float]:
    """Two-stage percentile interval: ward-days with replacement, then patients
    within each drawn ward-day. Replicates that draw no patient at all are
    skipped rather than scored as zero -- an empty draw is an artefact of
    resampling, not a ward-day on which nobody was covered."""
    samples: list[float] = []
    for _ in range(n_replicates):
        covered = 0
        total = 0
        for _ in range(len(units)):
            unit = units[rng.randrange(len(units))]
            if not unit.outcomes:
                continue
            for _ in range(len(unit.outcomes)):
                covered += unit.outcomes[rng.randrange(len(unit.outcomes))]
                total += 1
        if total:
            samples.append(covered / total)
    if len(samples) < 2:
        raise InsufficientCoverageEvidenceError(
            "the ward-day resampling produced fewer than two usable replicates, "
            "so no interval exists for these inputs"
        )
    samples.sort()
    return (_percentile(samples, 2.5), _percentile(samples, 97.5))


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def analyse_coverage(
    episodes: Sequence[BedsideEpisode],
    required: Mapping[PatientId, frozenset[Specialty]],
    rng: Random,
    params: CoverageParams | None = None,
    *,
    strategy: str = "unstated",
) -> CoverageReport:
    """MDT coverage over ward-days, with the interval ADR-0006 requires.

    `episodes` must already have been joined to occupancy and to clinician
    specialties (`hwpm.mining.attach_patients`, `attach_clinician_specialties`)
    -- an episode with no patient contributes its clinician to the floor check
    and nothing else, because there is no requirement to check it against.

    `strategy` is the `RequiredSpecialty` strategy key that produced
    `required`. It is recorded in `params` and reproduced by `render()`:
    ADR-0006 rule 2 says the denominator always travels with the number, and
    an unlabelled coverage figure is one that will be compared against a
    different definition and reported as a change.

    Raises
    ------
    SuppressionFloorError
        The aggregate itself would breach the ADR-0005 floor. Individual
        ward-days below the floor are withheld as cells, not raised.
    InsufficientCoverageEvidenceError
        Fewer than two ward-days, no multi-specialty patients at all, or a
        resampling distribution that cannot form an interval.
    """
    if params is None:
        params = CoverageParams()

    units = _units(episodes, required, params.window_s)
    if len(units) < 2:
        raise InsufficientCoverageEvidenceError(
            f"{len(units)} ward-day(s) of data; a cluster bootstrap over "
            "ward-days needs at least two resampling units, and ADR-0006 rule 6 "
            "does not permit a coverage figure without an interval"
        )

    n_patients = sum(len(unit.outcomes) for unit in units)
    n_covered = sum(sum(unit.outcomes) for unit in units)
    if n_patients == 0:
        raise InsufficientCoverageEvidenceError(
            "no multi-specialty patients were observed, so MDT coverage has an "
            "empty denominator. Reporting 0% or 100% here would both be "
            "inventions; the honest output is that the question does not apply "
            "to this period"
        )

    n_clinicians = len({episode.clinician for episode in episodes})
    check_counts(n_patients, n_clinicians)

    cells = tuple(_cell_of(unit) for unit in units)
    coverage = n_covered / n_patients
    wilson = wilson_interval(n_covered, n_patients)
    bootstrap = _cluster_bootstrap(units, rng, params.n_replicates)
    ci95 = (min(wilson[0], bootstrap[0]), max(wilson[1], bootstrap[1]))

    report_params: dict[str, object] = {
        "method_version": METHOD_VERSION,
        "strategy": strategy,
        "interpretation": COVERAGE_WORDING,
        # what was measured
        "numerator": (
            "multi-specialty patients with >=1 MDTMoment: >=2 of their REQUIRED "
            "specialties simultaneously at the bedside (never merely present)"
        ),
        "denominator": (
            "distinct observed patients whose required set holds >=2 specialties "
            "under this strategy"
        ),
        "evidence": "observed BedsideEpisodes only (ADR-0006 rule 4)",
        "n_patients": n_patients,
        "n_covered": n_covered,
        "n_clinicians": n_clinicians,
        "n_ward_days": len(units),
        "n_ward_days_suppressed": sum(1 for cell in cells if cell.suppressed),
        "window_s": params.window_s,
        # how the intervals were formed
        "ci_measures": "coverage",
        "ci_method": (
            "envelope of a two-stage ward-day cluster bootstrap (2.5/97.5) and "
            "the pooled Wilson score interval; the wider bound on each side is "
            "reported, so the figure is never narrower than either component"
        ),
        "ci_wilson_pooled": wilson,
        "ci_cluster_bootstrap": bootstrap,
        "cell_ci_method": "Wilson score interval, per ward-day",
        "n_replicates": params.n_replicates,
        "z": Z_95,
        # what was refused
        "suppression_floor_min_patients": MIN_PATIENTS,
        "suppression_floor_min_clinicians": MIN_CLINICIANS,
        "clinician_level_breakdown": (
            "absent by design, not permission-gated (ADR-0006 rule 5)"
        ),
    }

    return CoverageReport(
        coverage=coverage,
        ci95=ci95,
        n_patients=n_patients,
        n_covered=n_covered,
        cells=cells,
        params=report_params,
    )


__all__ = [
    "COVERAGE_WORDING",
    "METHOD_VERSION",
    "Z_95",
    "CoverageCell",
    "CoverageParams",
    "CoverageReport",
    "InsufficientCoverageEvidenceError",
    "analyse_coverage",
    "wilson_interval",
]
