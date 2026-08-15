"""Analytics layer — motion waste quantification. SPEC-003, node N07.

The public surface matches SPEC-003's interface block (`MotionReport`,
`analyse`), plus the two refusal paths the spec and ADR-0005 require to be
code rather than convention:

- `SuppressionFloorError` — ADR-0005 rule 4's aggregation floor (`suppression.py`).
- `BoundUnavailableError` — no *proven* lower bound at this instance size, so
  the analysis declines rather than substituting a heuristic tour
  (`bounds.py`).

Split by concern:

- `bounds.py`      — exact optimum and proven lower bounds on `necessary_m`.
- `suppression.py` — the ADR-0005 floor.
- `motion.py`      — `analyse`, `MotionReport`, the uncertainty loop.

Standard library only. `hwpm.analytics` depends on `hwpm.domain` (types and
`TravelGraph`) and `hwpm.mining` (SPEC-002's `min_dwell_s` sweep, which is
uncertainty source 1), and on nothing outward of itself.

One sentence, before anything downstream renders these numbers:
`attributable_m` is an **upper bound on avoidable motion under perfect
foresight** — not a target, not a promise, and not "we could save N% of
walking". Use `MotionReport.render()`, which says so.
"""

from __future__ import annotations

from hwpm.analytics.bounds import (
    BOUND_NODE_LIMIT,
    EXACT_NODE_LIMIT,
    BoundUnavailableError,
    NecessaryMotion,
    exact_open_tour_metres,
    necessary_metres,
    proven_lower_bound_metres,
)
from hwpm.analytics.motion import (
    ATTRIBUTABLE_M_WORDING,
    METHOD_VERSION,
    InsufficientEvidenceError,
    LowerBoundViolationError,
    MotionParams,
    MotionReport,
    analyse,
)
from hwpm.analytics.suppression import (
    MIN_CLINICIANS,
    MIN_PATIENTS,
    Cohort,
    SuppressionFloorError,
    cohort_of,
    enforce_suppression_floor,
)

__all__ = [
    "ATTRIBUTABLE_M_WORDING",
    "BOUND_NODE_LIMIT",
    "EXACT_NODE_LIMIT",
    "METHOD_VERSION",
    "MIN_CLINICIANS",
    "MIN_PATIENTS",
    "BoundUnavailableError",
    "Cohort",
    "InsufficientEvidenceError",
    "LowerBoundViolationError",
    "MotionParams",
    "MotionReport",
    "NecessaryMotion",
    "SuppressionFloorError",
    "analyse",
    "cohort_of",
    "enforce_suppression_floor",
    "exact_open_tour_metres",
    "necessary_metres",
    "proven_lower_bound_metres",
]
