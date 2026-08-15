"""Motion waste quantification with uncertainty. SPEC-003, node N07.

Takes reconstructed `Round`s (SPEC-002 / `hwpm.mining.reconstruct_rounds`) and
a routed `TravelGraph` (SPEC-003 / N06) and answers: how far did clinicians
walk, how little could the same clinical work have been walked in, and how
confident are we in the gap between the two.

Read this before quoting the number
-----------------------------------
`attributable_m = observed_m - necessary_m` is an **upper bound on avoidable
motion under perfect foresight**. It is not a target, not a forecast and not a
promise. `necessary_m` is the optimal tour of the *exact same visit set* --
achievable only by a clinician who knew at 08:00 everything they learned by
14:00 -- so the gap is the most that visit ordering could possibly have cost,
not the amount any real schedule could recover.

The correct sentence is: *"at most N% of observed walking was attributable to
visit ordering, under perfect foresight."* The incorrect sentence is *"we
could save N% of walking"*, and SPEC-003 says so in those words. The field is
named `attributable_m` rather than `wasteful_m` for this reason; every
rendering path in this module repeats the qualification rather than assuming
the reader carries it over from the field name. `MotionReport.render()` exists
so that the wording is code rather than a convention someone re-types into a
slide.

`necessary_m` is always a proven lower bound -- exact where the instance is
small, an LP-dual/spanning-tree bound where it is not, and a refusal
(`BoundUnavailableError`) where neither is available. See `bounds.py`. A
heuristic tour is never substituted, at any size.

Uncertainty (SPEC-003 "Uncertainty")
------------------------------------
`ci95` is a percentile interval on `attributable_m`, from one Monte Carlo loop
that resamples all three of SPEC-003's sources jointly (they are not
independent, so combining them by convolution of separate marginals would be
wrong):

1. **Episode-derivation parameter sensitivity** (SPEC-002 `min_dwell_s`).
   Each replicate draws a `min_dwell_s` from the SPEC-002 sweep
   (`DEFAULT_SENSITIVITY_DWELL_VALUES`) and keeps only episodes that dwelled
   at least that long. *One-sided, and named as such:* raising the threshold
   removes episodes we can see, but lowering it cannot recreate episodes that
   a looser threshold would have found and this module never received. The
   interval therefore reflects the sensitivity that is visible from `Round`s
   alone; recovering the other side needs the trajectories, and belongs to
   whatever pipeline node holds them (`hwpm.mining.sensitivity_analysis`).
2. **Location-mapping confidence** (SPEC-001). Each episode is retained with
   probability `episode.confidence` -- the same quantity SPEC-001 quarantines
   below 0.8, used here as "this bed attribution could have gone the other
   way" rather than as a hard cut.
3. **Sampling variation across days** -- a bootstrap over **ward-days**. The
   resampling unit is `(day, modal ward of the round)`: SPEC-003 names
   ward-days, and a round's modal ward (by bedside time) is the ward it
   belongs to. Resampling rounds individually would understate correlation
   between clinicians working the same ward the same day.

If that loop produces a zero-width interval, `analyse` raises
`InsufficientEvidenceError` rather than widening the interval by an invented
epsilon. SPEC-003: "A point estimate without an interval is not a valid output
of this module, and `MotionReport` has no constructor that permits one." An
interval manufactured to satisfy the constructor would satisfy the letter of
that and defeat its purpose.

Determinism (criterion 6): every draw comes from the `rng` argument, in a
fixed order. No module-level `random`, no wall-clock in `params`. Two calls to
`analyse(rounds, graph, Random(99))` compare equal.

Pure standard library, so `hwpm.analytics` imports cleanly without the
optional `analysis` / `optimize` extras.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from random import Random

from hwpm.analytics.bounds import (
    BOUND_NODE_LIMIT,
    EXACT_NODE_LIMIT,
    BoundUnavailableError,
    necessary_metres,
)
from hwpm.analytics.suppression import (
    MIN_CLINICIANS,
    MIN_PATIENTS,
    SuppressionFloorError,
    enforce_suppression_floor,
)
from hwpm.domain import LocationId, Round
from hwpm.domain.travel import TravelGraph
from hwpm.mining import DEFAULT_SENSITIVITY_DWELL_VALUES, EpisodeParams

#: ADR-0006 rule 1: "Every published figure carries a method version. A change
#: to episode-derivation parameters, the travel graph, or a strategy
#: implementation increments it, and the back-series is recomputed and
#: re-published together with the change."
METHOD_VERSION = "motion/1.0.0"

#: The one sentence this module exists to protect. Reproduced into every
#: report's `params` and into `render()` so it cannot be dropped in transit.
ATTRIBUTABLE_M_WORDING = (
    "attributable_m is an UPPER BOUND on avoidable motion under perfect "
    "foresight (observed minus the optimal tour of the same visit set). It is "
    "not a target, not a forecast and not a savings estimate. Report it as "
    "'at most N% of observed walking was attributable to visit ordering, under "
    "perfect foresight', never as 'we could save N% of walking' (SPEC-003, "
    "'Defining waste honestly')."
)


class InsufficientEvidenceError(RuntimeError):
    """The inputs cannot support an interval, so they cannot support a report.

    Raised rather than returning a point estimate, or widening a degenerate
    interval by a fabricated epsilon (SPEC-003, "Uncertainty").
    """


class LowerBoundViolationError(ValueError):
    """`necessary_m` came out above `observed_m`.

    SPEC-003 criterion 4 ("`necessary_m <= observed_m` always; violation
    raises"). This is a bug signal, not a data condition: the bound is a
    theorem given a metric travel graph, so seeing it means either the graph
    stopped satisfying the triangle inequality or something substituted a
    tour for a bound.
    """


@dataclass(frozen=True)
class MotionParams:
    """Every field a modelling assumption, none of them a fact. Mirrors the
    house pattern of `hwpm.mining.EpisodeParams`."""

    #: The SPEC-002 sweep, verbatim. Uncertainty source 1.
    dwell_values: tuple[int, ...] = DEFAULT_SENSITIVITY_DWELL_VALUES
    #: The threshold the point estimate itself is computed at. Defaults to
    #: `EpisodeParams.min_dwell_s` so the headline figure matches the settings
    #: the episodes were most likely derived under.
    base_min_dwell_s: int = EpisodeParams().min_dwell_s
    #: Monte Carlo replicates. Percentile intervals at 2.5/97.5 want a few
    #: hundred at minimum before the tails stop being one observation wide.
    n_replicates: int = 500
    #: Uncertainty source 2. Switchable only so its contribution can be
    #: isolated in tests; production reports leave it on.
    model_mapping_confidence: bool = True

    def __post_init__(self) -> None:
        if not self.dwell_values:
            raise ValueError("dwell_values must not be empty")
        if any(v < 0 for v in self.dwell_values):
            raise ValueError(f"dwell_values must all be >= 0, got {self.dwell_values!r}")
        if self.base_min_dwell_s < 0:
            raise ValueError(
                f"base_min_dwell_s must be >= 0, got {self.base_min_dwell_s!r}"
            )
        if self.n_replicates < 2:
            raise ValueError(
                f"n_replicates must be >= 2 to form an interval, got "
                f"{self.n_replicates!r}"
            )


@dataclass(frozen=True)
class MotionReport:
    """SPEC-003's headline output.

    No field has a default. That is the whole of SPEC-003 criterion 5's "no
    constructor that permits a point estimate without an interval": `ci95` is
    positional-or-keyword and required, `__post_init__` rejects a degenerate
    or malformed one, and `params` must be non-empty, so there is no path --
    not `MotionReport(1.0, 0.5, 0.5)`, not a `replace()` that drops the
    interval -- to a report that does not carry its uncertainty. The
    invariants of criterion 4 (`necessary_m <= observed_m`) are checked here
    too, so a violated bound raises at construction rather than being
    discovered downstream.
    """

    observed_m: float
    """Metres actually walked between consecutive bedside episodes, routed
    through `TravelGraph`, summed over all rounds."""

    necessary_m: float
    """A **proven lower bound** on the metres the same visit set required: the
    optimal tour over exactly those beds, achievable only with perfect
    foresight. Never a heuristic tour (`hwpm.analytics.bounds`)."""

    attributable_m: float
    """`observed_m - necessary_m`: an **upper bound on avoidable motion**, not
    a target and not a promise. See `ATTRIBUTABLE_M_WORDING`, and use
    `render()` rather than formatting this field yourself."""

    ci95: tuple[float, float]
    """95% percentile interval on `attributable_m`, combining episode-parameter
    sensitivity, location-mapping confidence and a ward-day bootstrap. Never
    absent, never zero-width."""

    params: dict[str, object]
    """Provenance: method version, every tunable that produced the figure, the
    suppression floor applied, how `necessary_m` was bounded, and the wording
    the headline must be reported in (01-DOMAIN-MODEL.md rule 4)."""

    _TOLERANCE_M = 1e-6

    def __post_init__(self) -> None:
        if not math.isfinite(self.observed_m) or self.observed_m < 0:
            raise ValueError(
                f"observed_m must be finite and >= 0, got {self.observed_m!r}"
            )
        if not math.isfinite(self.necessary_m) or self.necessary_m < 0:
            raise ValueError(
                f"necessary_m must be finite and >= 0, got {self.necessary_m!r}"
            )
        if self.necessary_m > self.observed_m + self._TOLERANCE_M:
            raise LowerBoundViolationError(
                f"necessary_m ({self.necessary_m!r}) exceeds observed_m "
                f"({self.observed_m!r}); necessary_m must be a lower bound on the "
                "distance the same visit set required (SPEC-003 criterion 4)"
            )
        expected = self.observed_m - self.necessary_m
        if abs(self.attributable_m - expected) > self._TOLERANCE_M:
            raise ValueError(
                f"attributable_m ({self.attributable_m!r}) must equal observed_m - "
                f"necessary_m ({expected!r})"
            )

        if not isinstance(self.ci95, tuple) or len(self.ci95) != 2:
            raise ValueError(
                f"ci95 must be a (lo, hi) tuple, got {self.ci95!r}. A point estimate "
                "without an interval is not a valid output of this module (SPEC-003)"
            )
        lo, hi = self.ci95
        if not math.isfinite(lo) or not math.isfinite(hi):
            raise ValueError(f"ci95 bounds must be finite, got {self.ci95!r}")
        if hi - lo <= 0:
            raise ValueError(
                f"ci95 must be non-degenerate (hi > lo), got {self.ci95!r}; a "
                "zero-width interval is a point estimate wearing a costume "
                "(SPEC-003 criterion 5)"
            )

        if not self.params:
            raise ValueError(
                "params must be populated: a figure without its provenance is not "
                "publishable (SPEC-003 criterion 5, 01-DOMAIN-MODEL.md rule 4)"
            )
        # Defensive copy: the report is frozen, and a caller holding the dict
        # they passed in should not be able to edit a published figure's
        # provenance afterwards.
        object.__setattr__(self, "params", dict(self.params))

    @property
    def attributable_fraction(self) -> float:
        """`attributable_m / observed_m`, or 0.0 when nobody walked anywhere.
        A *bound* on a fraction, with all the caveats of `attributable_m`."""
        if self.observed_m <= 0:
            return 0.0
        return self.attributable_m / self.observed_m

    def render(self) -> str:
        """The report as text, with the SPEC-003 qualification attached.

        Call this rather than formatting the fields ad hoc: the qualification
        is the part that gets dropped, and the only reliable defence is for
        the correct sentence to be the easy one to produce.
        """
        lo, hi = self.ci95
        pct = 100.0 * self.attributable_fraction
        return (
            f"Motion report ({self.params.get('method_version', METHOD_VERSION)})\n"
            f"  observed_m     {self.observed_m:12.1f}\n"
            f"  necessary_m    {self.necessary_m:12.1f}   "
            "(proven lower bound: optimal tour of the same visit set)\n"
            f"  attributable_m {self.attributable_m:12.1f}   "
            f"95% CI [{lo:.1f}, {hi:.1f}]\n"
            f"\n"
            f"At most {pct:.1f}% of observed walking was attributable to visit "
            "ordering,\nunder perfect foresight. This is an upper bound on "
            "avoidable motion, not a\ntarget and not a promise: it is not a "
            f"claim that {self.attributable_m:.0f} m could be saved."
        )


# ---------------------------------------------------------------------------
# Internal per-round working state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RoundView:
    """One round reduced to what the Monte Carlo loop needs, so replicates
    never touch `BedsideEpisode` again."""

    beds: tuple[LocationId, ...]
    dwell_s: tuple[float, ...]
    confidences: tuple[float, ...]
    day: date
    ward: str


def _ward_of(bed: LocationId) -> str:
    """Ward component of a bed id (`"1A/BED3"` -> `"1A"`). The graph's node
    ids are structured, so this is a parse rather than a lookup; ids without a
    separator are their own ward."""
    return bed.value.split("/", 1)[0]


def _view_of(round_: Round) -> _RoundView:
    beds = tuple(e.bed for e in round_.episodes)
    dwell = tuple(e.duration.total_seconds() for e in round_.episodes)
    confidences = tuple(e.confidence for e in round_.episodes)
    time_by_ward: dict[str, float] = {}
    for episode in round_.episodes:
        ward = _ward_of(episode.bed)
        seconds = episode.duration.total_seconds()
        time_by_ward[ward] = time_by_ward.get(ward, 0.0) + seconds
    # Modal ward by bedside time; ties broken by ward id so the unit key is
    # deterministic and a reported interval never depends on dict order.
    ward = max(time_by_ward, key=lambda w: (time_by_ward[w], w), default="")
    return _RoundView(beds, dwell, confidences, round_.day, ward)


class _Totals:
    """Observed and necessary metres for a bed sequence, memoised.

    The Monte Carlo loop evaluates the same bed sequences thousands of times
    (with all confidences at 1.0 and a dwell threshold that changes nothing,
    every replicate sees each round's full sequence). Without this, the exact
    subset DP would be re-run per replicate for no new information.
    """

    def __init__(self, graph: TravelGraph) -> None:
        self._graph = graph
        self._cache: dict[tuple[str, ...], tuple[float, float]] = {}
        self.max_nodes = 0
        self.all_exact = True
        self.methods: set[str] = set()

    def of(self, beds: Sequence[LocationId]) -> tuple[float, float]:
        key = tuple(b.value for b in beds)
        hit = self._cache.get(key)
        if hit is not None:
            return hit

        observed = math.fsum(self._graph.cost(a, b).metres for a, b in pairwise(beds))
        necessary = necessary_metres(beds, self._graph)
        if necessary.metres > observed + MotionReport._TOLERANCE_M:
            raise LowerBoundViolationError(
                f"necessary_m ({necessary.metres!r}) exceeds observed_m "
                f"({observed!r}) for bed sequence {key!r} -- the travel graph "
                "must be metric for the bound to hold (SPEC-003 criterion 4)"
            )
        self.max_nodes = max(self.max_nodes, necessary.n_nodes)
        self.all_exact = self.all_exact and necessary.exact
        self.methods.add(necessary.method)

        result = (observed, necessary.metres)
        self._cache[key] = result
        return result


def _kept_beds(
    view: _RoundView, min_dwell_s: float, rng: Random, model_confidence: bool
) -> tuple[LocationId, ...]:
    """The bed sequence surviving one replicate's episode-parameter draw and
    location-mapping draw. Order is preserved -- it is the observed order."""
    kept: list[LocationId] = []
    for bed, dwell, confidence in zip(
        view.beds, view.dwell_s, view.confidences, strict=True
    ):
        if dwell < min_dwell_s:
            continue
        if model_confidence and rng.random() >= confidence:
            continue
        kept.append(bed)
    return tuple(kept)


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


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def analyse(
    rounds: Sequence[Round],
    graph: TravelGraph,
    rng: Random,
    params: MotionParams | None = None,
) -> MotionReport:
    """SPEC-003's `analyse`: observed, necessary and attributable motion with
    a 95% interval.

    `params` is an optional fourth argument, so the three-argument signature
    in SPEC-003's interface block continues to work verbatim.

    Raises
    ------
    SuppressionFloorError
        The figure would breach the ADR-0005 aggregation floor (<5 patients,
        or attributable to one named clinician), or the floor could not be
        verified because episodes are unjoined to occupancy.
    InsufficientEvidenceError
        Fewer than two ward-days, or a resampling distribution with no spread
        -- either way the inputs cannot support an interval, and SPEC-003
        forbids reporting without one.
    BoundUnavailableError
        A round visits more distinct beds than any proven lower bound in
        `hwpm.analytics.bounds` covers. No heuristic tour is substituted;
        the analysis refuses instead.
    LowerBoundViolationError
        `necessary_m` came out above `observed_m` (criterion 4).
    """
    if params is None:
        params = MotionParams()

    cohort = enforce_suppression_floor(rounds)

    views = [_view_of(r) for r in rounds]
    units: dict[tuple[date, str], list[_RoundView]] = {}
    for view in views:
        units.setdefault((view.day, view.ward), []).append(view)
    if len(units) < 2:
        raise InsufficientEvidenceError(
            f"{len(units)} ward-day(s) of data; a bootstrap over ward-days needs "
            "at least two resampling units to estimate sampling variation, and "
            "SPEC-003 does not permit a point estimate without an interval"
        )

    totals = _Totals(graph)

    # -- point estimate: no resampling, no confidence draws, base threshold --
    observed_m = 0.0
    necessary_m = 0.0
    for view in views:
        beds = _kept_beds(view, params.base_min_dwell_s, rng, model_confidence=False)
        round_observed, round_necessary = totals.of(beds)
        observed_m += round_observed
        necessary_m += round_necessary
    attributable_m = observed_m - necessary_m

    # -- uncertainty: one loop over all three SPEC-003 sources jointly -------
    unit_keys = list(units)
    samples: list[float] = []
    observed_samples: list[float] = []
    necessary_samples: list[float] = []
    for _ in range(params.n_replicates):
        min_dwell_s = params.dwell_values[rng.randrange(len(params.dwell_values))]
        drawn = [unit_keys[rng.randrange(len(unit_keys))] for _ in unit_keys]
        replicate_observed = 0.0
        replicate_necessary = 0.0
        for key in drawn:
            for view in units[key]:
                beds = _kept_beds(view, min_dwell_s, rng, params.model_mapping_confidence)
                round_observed, round_necessary = totals.of(beds)
                replicate_observed += round_observed
                replicate_necessary += round_necessary
        observed_samples.append(replicate_observed)
        necessary_samples.append(replicate_necessary)
        samples.append(replicate_observed - replicate_necessary)

    samples.sort()
    observed_samples.sort()
    necessary_samples.sort()
    ci95 = (_percentile(samples, 2.5), _percentile(samples, 97.5))
    if ci95[1] - ci95[0] <= 0:
        raise InsufficientEvidenceError(
            "the resampling distribution has zero width, so no non-degenerate "
            "95% interval exists for these inputs (identical ward-days, fully "
            "confident episodes and a dwell sweep that changes nothing will do "
            "this). Widening it by an invented epsilon would satisfy "
            "MotionReport's constructor and defeat its purpose, so this analysis "
            "refuses instead (SPEC-003, 'Uncertainty')"
        )

    report_params: dict[str, object] = {
        "method_version": METHOD_VERSION,
        "interpretation": ATTRIBUTABLE_M_WORDING,
        # what was measured
        "n_rounds": len(rounds),
        "n_ward_days": len(units),
        "n_patients": cohort.n_patients,
        "n_clinicians": cohort.n_clinicians,
        # how necessary_m was established
        "necessary_m_methods": tuple(sorted(totals.methods)),
        "necessary_m_exact": totals.all_exact,
        "necessary_m_max_instance_nodes": totals.max_nodes,
        "exact_node_limit": EXACT_NODE_LIMIT,
        "bound_node_limit": BOUND_NODE_LIMIT,
        # how the interval was formed
        "ci_measures": "attributable_m",
        "ci_method": "percentile bootstrap over ward-days, 2.5/97.5",
        "n_replicates": params.n_replicates,
        "min_dwell_s_base": params.base_min_dwell_s,
        "min_dwell_s_sweep": tuple(params.dwell_values),
        "min_dwell_s_sweep_is_one_sided": True,
        "models_mapping_confidence": params.model_mapping_confidence,
        "uncertainty_sources": (
            "episode-derivation parameter sensitivity (SPEC-002 min_dwell_s sweep, "
            "one-sided: it can drop episodes a stricter threshold would have lost, "
            "not recover ones a looser threshold would have found)",
            "location-mapping confidence (SPEC-001, per-episode retention probability)",
            "sampling variation across ward-days (percentile bootstrap)",
        ),
        "observed_m_ci95": (
            _percentile(observed_samples, 2.5),
            _percentile(observed_samples, 97.5),
        ),
        "necessary_m_ci95": (
            _percentile(necessary_samples, 2.5),
            _percentile(necessary_samples, 97.5),
        ),
        # what was refused
        "suppression_floor_min_patients": MIN_PATIENTS,
        "suppression_floor_min_clinicians": MIN_CLINICIANS,
    }

    return MotionReport(
        observed_m=observed_m,
        necessary_m=necessary_m,
        attributable_m=attributable_m,
        ci95=ci95,
        params=report_params,
    )


__all__ = [
    "ATTRIBUTABLE_M_WORDING",
    "METHOD_VERSION",
    "BoundUnavailableError",
    "InsufficientEvidenceError",
    "LowerBoundViolationError",
    "MotionParams",
    "MotionReport",
    "SuppressionFloorError",
    "analyse",
]
