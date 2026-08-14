# SPEC-003 — Travel graph and motion waste quantification

**Status:** accepted · **Nodes:** N06, N07 · **Owner role:** builder (N07: architect)
**Depends on:** SPEC-001, SPEC-002 · **Last revised:** 2026-08-14

## Problem

Measure how far clinicians actually travel, and how much of that travel is
avoidable — separating waste attributable to scheduling from movement that is
clinically necessary.

## In scope

- A routed `TravelGraph` over ward geometry (corridors, lifts, stairs).
- Distance and time attribution per clinician, per round, per day.
- Decomposition of travel into necessary vs. schedule-attributable.
- Uncertainty quantification on every reported figure.

## Out of scope

- Optimising the routes → SPEC-004.
- Ergonomic or physiological interpretation. We report metres and minutes, not
  fatigue.

## Interface

```python
# hwpm.domain.travel
class TravelGraph:
    def cost(self, a: LocationId, b: LocationId) -> TravelCost: ...   # metres + seconds
    def path(self, a: LocationId, b: LocationId) -> list[LocationId]: ...

# hwpm.analytics.motion
@dataclass(frozen=True) class MotionReport:
    observed_m: float
    necessary_m: float          # lower bound: optimal tour of the same visit set
    attributable_m: float       # observed - necessary
    ci95: tuple[float, float]
    params: dict[str, object]

def analyse(rounds: list[Round], graph: TravelGraph, rng: Random) -> MotionReport: ...
```

## The first task: replace Euclidean distance

The prototype in `web/hospital-ward.html` computes straight-line distance between
bed coordinates. That is wrong in a way that matters: two beds 4 m apart on
different floors are a lift ride and ~90 s apart. Straight-line distance
systematically **understates** inter-floor movement — which is precisely the
movement asynchronous rounds generate — and would therefore bias the headline
finding toward "there is no problem".

`TravelGraph` replaces it. Nothing outside the domain computes distance.

## Defining "waste" honestly

The most consequential definition in the project. A clinician walking is not
waste; walking *further than the same clinical work required* is.

`necessary_m` is the **optimal tour of the exact same visit set under the same
constraints** — a lower bound achievable only with perfect foresight. So:

- `attributable_m = observed_m − necessary_m` is an **upper bound on avoidable
  motion**, not a target and not a promise.
- It must be reported as such, every time, in those words. "We could save 40% of
  walking" is a claim this method does not support; "at most 40% of observed
  walking was attributable to visit ordering, under perfect foresight" is.

Overstating this is the fastest way to lose clinical credibility, and the number
is attractive enough that it will be quoted out of context if we let it.

## Uncertainty

Every figure carries a 95% interval reflecting:

1. Episode-derivation parameter sensitivity (SPEC-002, `min_dwell_s` range).
2. Location mapping confidence (SPEC-001).
3. Sampling variation across days — bootstrap over ward-days.

A point estimate without an interval is not a valid output of this module, and
`MotionReport` has no constructor that permits one.

## Acceptance criteria

1. `TravelGraph.cost` between beds on different floors exceeds Euclidean by a factor reflecting the lift path. — `test_interfloor_cost`
2. `cost` is symmetric and satisfies the triangle inequality. — `test_metric_properties`
3. On synthetic data, `observed_m` matches `GroundTruth.total_distance_m` within 1%. — `test_motion_ground_truth`
4. `necessary_m ≤ observed_m` always; violation raises. — `test_necessary_is_lower_bound`
5. Every `MotionReport` has a non-degenerate `ci95` and populated `params`. — `test_report_completeness`
6. Bootstrap CI is reproducible under a fixed seed. — `test_determinism`
7. No output cell derives from <5 patients, and none is attributable to a single clinician. — `test_suppression_floor` (ADR-0005)

## Test oracle

Synthetic ground truth for 3; mathematical invariants for 1, 2, 4; brute-forced
optimal tours on ≤10-bed instances for `necessary_m`.

## Failure modes

- **`necessary_m` computed with a heuristic rather than an optimum**, making the
  lower bound not a bound. On instances too large to brute-force, use a *proven*
  lower bound (e.g. LP relaxation), never a heuristic tour — a heuristic tour
  would inflate apparent waste.
- **Reporting the upper bound as an estimate.** Addressed by wording, and by the
  field name `attributable_m` rather than `wasteful_m`.
- **Floor plan inaccuracy** dominating the error budget. If real geometry is
  unavailable, say so, and report distances as relative rather than absolute.

## Open questions

- **[non-blocking]** Are real floor plans available? Absolute metres depend on it;
  relative comparisons do not.
- **[non-blocking]** Lift waiting time — measured, or assumed constant? Affects
  time-based figures, not distance.
