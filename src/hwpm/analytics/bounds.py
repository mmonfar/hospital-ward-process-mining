"""Proven lower bounds on the motion a visit set required. SPEC-003, N07.

This module answers one question and refuses to answer it approximately:
*given the exact set of beds a clinician visited, what is the least distance
any ordering of those visits could have cost?*

SPEC-003 makes that a bound, not an estimate, and the distinction is
load-bearing. `necessary_m` feeds `attributable_m = observed_m - necessary_m`,
which is published as an **upper bound on avoidable motion**. That claim is
only true if `necessary_m` is a genuine lower bound on the optimal tour: any
`necessary_m` above the true optimum makes `attributable_m` smaller than the
real avoidable figure and quietly turns a bound into an under-estimate, while
any *unproven* `necessary_m` makes the published number an assertion rather
than a bound. SPEC-003's "Failure modes" names this explicitly -- "on
instances too large to brute-force, use a *proven* lower bound (e.g. LP
relaxation), never a heuristic tour". Nothing in this module ever returns a
constructed tour's length. A nearest-neighbour tour *is* computed here, and is
used only to size a subgradient step; it never reaches the returned figure.

What "the optimal tour" means here
----------------------------------
The shortest **Hamiltonian path with free endpoints** over the distinct beds
of the round. Free endpoints rather than a fixed start because "perfect
foresight" (SPEC-003) includes foreseeing where to begin, and because a looser
relaxation is the safe direction: a smaller `necessary_m` keeps
`attributable_m` a valid *upper* bound on avoidable motion. Note also that
`TravelGraph.cost` is a shortest-path metric and so satisfies the triangle
inequality (`test_metric_properties`); that is what makes the shortest
Hamiltonian path a lower bound on the observed *walk*, which may revisit beds
and which shortcuts down to a Hamiltonian path without ever getting longer.

Three regimes, by instance size
-------------------------------
- ``n <= EXACT_NODE_LIMIT`` (12): exact optimum by Held-Karp subset dynamic
  programming, O(2^n n^2). Exact, so nothing is given away. `test_motion.py`
  cross-checks it against exhaustive permutation search on the <=10-bed
  instances SPEC-003's test oracle names.
- ``EXACT_NODE_LIMIT < n <= BOUND_NODE_LIMIT``: the maximum of two proven
  bounds --
    * the minimum spanning tree over the beds (a Hamiltonian path *is* a
      spanning tree, so no path is lighter than the minimum one), and
    * the Held-Karp 1-tree Lagrangian bound, which is the Lagrangian dual of
      the degree-constrained LP relaxation of the tour, evaluated on the
      instance augmented with a zero-cost dummy node (that augmentation turns
      "shortest Hamiltonian path, free endpoints" into "shortest Hamiltonian
      cycle", which is what the 1-tree bound is defined for). Every iterate is
      a valid bound for every choice of node potentials, so stopping early
      loses tightness and never validity.
  The maximum of two lower bounds on the same quantity is a lower bound on it.
- ``n > BOUND_NODE_LIMIT``: `BoundUnavailableError`. There is no heuristic
  fallback on purpose. SPEC-003 would rather this module refuse to report than
  substitute a tour, and a refusal that propagates out of `analyse` is how
  that preference is enforced rather than merely documented.

Pure standard library: no numpy, no scipy, no ortools. `hwpm.analytics` must
stay importable wherever `hwpm.domain` is, without the optional `analysis` or
`optimize` extras.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from hwpm.domain import LocationId
from hwpm.domain.travel import TravelGraph

#: Above this many distinct beds in one round, the exact subset DP is dropped
#: for a proven bound. 2^12 * 12^2 is under a million operations, which is
#: comfortable inside a bootstrap loop; the ward layout only has 54 beds in
#: total, so most real rounds land well inside this.
EXACT_NODE_LIMIT = 12

#: Above this many distinct beds, no bound is offered at all. The bound
#: algorithms themselves are O(n^2) per iteration and would keep running; the
#: limit exists because the pairwise cost matrix costs n^2 `TravelGraph`
#: queries to build, and because an honest module states the size at which it
#: stops being able to answer instead of degrading silently.
BOUND_NODE_LIMIT = 256

#: Subgradient iterations for the Lagrangian bound. Every iterate is valid, so
#: this trades tightness against time and never correctness.
LAGRANGIAN_ITERATIONS = 120


class BoundUnavailableError(RuntimeError):
    """No *proven* lower bound can be computed for an instance this large.

    Deliberately not recoverable by falling back to a heuristic tour: SPEC-003
    requires refusing to report over reporting an unproven number.
    """


@dataclass(frozen=True)
class NecessaryMotion:
    """The least distance a visit set could have required, plus how that was
    established -- `method` and `exact` travel with the number because a
    bound and an optimum are different claims and a report header has to be
    able to say which one it is carrying (01-DOMAIN-MODEL.md rule 4)."""

    metres: float
    method: str
    exact: bool
    n_nodes: int

    def __post_init__(self) -> None:
        if self.metres < 0:
            raise ValueError(f"metres must be >= 0, got {self.metres!r}")


# ---------------------------------------------------------------------------
# Cost matrix
# ---------------------------------------------------------------------------


def _distinct(beds: Sequence[LocationId]) -> list[LocationId]:
    """Distinct beds, first-occurrence order. `dict.fromkeys` rather than a
    set so the matrix (and therefore the DP's tie-breaking) is deterministic
    -- `LocationId` is frozen and hashable, but set iteration order is not a
    guarantee we want a reported figure to depend on."""
    return list(dict.fromkeys(beds))


def _cost_matrix(beds: Sequence[LocationId], graph: TravelGraph) -> list[list[float]]:
    n = len(beds)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            metres = graph.cost(beds[i], beds[j]).metres
            matrix[i][j] = metres
            matrix[j][i] = metres
    return matrix


# ---------------------------------------------------------------------------
# Exact: shortest Hamiltonian path, free endpoints
# ---------------------------------------------------------------------------


def _exact_open_path(matrix: list[list[float]]) -> float:
    """Held-Karp subset DP. `dp[mask][j]` is the cheapest path that visits
    exactly `mask` and ends at `j`; seeding `dp[1 << j][j] = 0` for *every* j
    is what makes the start free as well as the end, at no extra cost over the
    fixed-start version."""
    n = len(matrix)
    if n <= 1:
        return 0.0
    full = (1 << n) - 1
    inf = math.inf
    dp = [[inf] * n for _ in range(1 << n)]
    for j in range(n):
        dp[1 << j][j] = 0.0
    for mask in range(1 << n):
        row = dp[mask]
        for j in range(n):
            cost = row[j]
            if cost == inf or not mask & (1 << j):
                continue
            matrix_j = matrix[j]
            for k in range(n):
                bit = 1 << k
                if mask & bit:
                    continue
                candidate = cost + matrix_j[k]
                target = dp[mask | bit]
                if candidate < target[k]:
                    target[k] = candidate
    return min(dp[full])


# ---------------------------------------------------------------------------
# Proven bounds
# ---------------------------------------------------------------------------


def _mst_weight(matrix: list[list[float]], nodes: Sequence[int]) -> float:
    """Prim's algorithm over `nodes` (indices into `matrix`)."""
    if len(nodes) <= 1:
        return 0.0
    first, rest = nodes[0], list(nodes[1:])
    cheapest = {i: matrix[first][i] for i in rest}
    total = 0.0
    remaining = set(rest)
    while remaining:
        i = min(remaining, key=lambda k: (cheapest[k], k))
        total += cheapest[i]
        remaining.discard(i)
        for j in remaining:
            weight = matrix[i][j]
            if weight < cheapest[j]:
                cheapest[j] = weight
    return total


def _augmented(matrix: list[list[float]]) -> list[list[float]]:
    """Add a dummy node joined to every real node at zero cost.

    A Hamiltonian cycle through the dummy is exactly a Hamiltonian path over
    the real nodes plus two zero-cost edges, so the optimal cycle on the
    augmented instance equals the optimal free-endpoint path on the original.
    That is what lets a cycle-shaped relaxation (the 1-tree) bound a
    path-shaped quantity.
    """
    n = len(matrix)
    out = [[*row, 0.0] for row in matrix]
    out.append([0.0] * (n + 1))
    return out


def _min_one_tree(matrix: list[list[float]], pi: list[float]) -> tuple[float, list[int]]:
    """Minimum 1-tree under node potentials `pi`: an MST over nodes 1..n-1
    plus the two cheapest edges at node 0. Returns its weight in the modified
    costs `c'(i,j) = c(i,j) + pi[i] + pi[j]`, and every node's degree in it."""
    n = len(matrix)
    modified = [[matrix[i][j] + pi[i] + pi[j] for j in range(n)] for i in range(n)]

    degrees = [0] * n
    total = 0.0

    # MST over 1..n-1 (Prim, tracking the chosen edges so degrees are exact).
    remaining = set(range(2, n))
    parent = {i: 1 for i in remaining}
    cheapest = {i: modified[1][i] for i in remaining}
    while remaining:
        i = min(remaining, key=lambda k: (cheapest[k], k))
        total += cheapest[i]
        degrees[i] += 1
        degrees[parent[i]] += 1
        remaining.discard(i)
        for j in remaining:
            weight = modified[i][j]
            if weight < cheapest[j]:
                cheapest[j] = weight
                parent[j] = i

    # The two cheapest edges out of node 0.
    candidates = sorted((modified[0][j], j) for j in range(1, n))
    for weight, j in candidates[:2]:
        total += weight
        degrees[0] += 1
        degrees[j] += 1

    return total, degrees


def _nearest_neighbour_cycle(matrix: list[list[float]]) -> float:
    """An actual tour's length, used **only** as the `UB` in the subgradient
    step size. It never contributes to a returned bound -- see the module
    docstring. Step-size heuristics may be as rough as they like; bounds may
    not."""
    n = len(matrix)
    unvisited = set(range(1, n))
    current = 0
    total = 0.0
    while unvisited:
        nxt = min(unvisited, key=lambda k: (matrix[current][k], k))
        total += matrix[current][nxt]
        unvisited.discard(nxt)
        current = nxt
    return total + matrix[current][0]


def _lagrangian_bound(matrix: list[list[float]], iterations: int) -> float:
    """Held-Karp 1-tree bound on the minimum Hamiltonian cycle of `matrix`,
    improved by subgradient ascent on the node potentials.

    For *any* potential vector pi, every Hamiltonian cycle H satisfies
    `w'(H) = w(H) + 2*sum(pi)` (each node has degree 2), and the minimum
    1-tree is no heavier than H under `w'`. Hence
    `w(H) >= w_1tree(pi) - 2*sum(pi)` for every pi -- so each iterate below is
    a valid bound in its own right, and taking the best seen is valid too.
    Ascent only sharpens it.
    """
    n = len(matrix)
    if n < 3:
        return 0.0
    upper = _nearest_neighbour_cycle(matrix)
    pi = [0.0] * n
    best = 0.0
    for t in range(iterations):
        weight, degrees = _min_one_tree(matrix, pi)
        bound = weight - 2.0 * math.fsum(pi)
        best = max(best, bound)
        subgradient = [d - 2 for d in degrees]
        norm = sum(g * g for g in subgradient)
        if norm == 0:
            # The 1-tree is already a tour: this bound is the optimum.
            break
        alpha = 2.0 * (0.95**t)
        step = alpha * max(upper - bound, 0.0) / norm
        if step == 0.0:
            break
        pi = [p + step * g for p, g in zip(pi, subgradient, strict=True)]
    return best


def proven_lower_bound_metres(
    beds: Sequence[LocationId],
    graph: TravelGraph,
    *,
    iterations: int = LAGRANGIAN_ITERATIONS,
) -> float:
    """The best of this module's proven lower bounds on the shortest
    free-endpoint Hamiltonian path over the distinct `beds`. Never a tour."""
    unique = _distinct(beds)
    if len(unique) <= 1:
        return 0.0
    matrix = _cost_matrix(unique, graph)
    if len(unique) == 2:
        return matrix[0][1]
    mst = _mst_weight(matrix, range(len(unique)))
    lagrangian = _lagrangian_bound(_augmented(matrix), iterations)
    return max(mst, lagrangian, 0.0)


def exact_open_tour_metres(beds: Sequence[LocationId], graph: TravelGraph) -> float:
    """The exact shortest free-endpoint Hamiltonian path over the distinct
    `beds`. Raises `BoundUnavailableError` above `EXACT_NODE_LIMIT` rather
    than silently degrading -- callers wanting the automatic choice should
    call `necessary_metres`."""
    unique = _distinct(beds)
    if len(unique) > EXACT_NODE_LIMIT:
        raise BoundUnavailableError(
            f"exact optimum requested for {len(unique)} beds; the subset DP is "
            f"capped at {EXACT_NODE_LIMIT} (SPEC-003 test oracle: brute-forced "
            "optimal tours on <=10-bed instances)"
        )
    if len(unique) <= 1:
        return 0.0
    return _exact_open_path(_cost_matrix(unique, graph))


def necessary_metres(
    beds: Sequence[LocationId],
    graph: TravelGraph,
    *,
    iterations: int = LAGRANGIAN_ITERATIONS,
) -> NecessaryMotion:
    """SPEC-003's `necessary_m` for one visit set: exact where the instance is
    small enough, a proven bound where it is not, and a refusal where neither
    is available.

    The returned figure is always <= the true optimum, and therefore always
    <= the distance any actual round over these beds covered.
    """
    unique = _distinct(beds)
    n = len(unique)
    if n <= 1:
        return NecessaryMotion(
            0.0, method="trivial (fewer than two beds)", exact=True, n_nodes=n
        )
    if n > BOUND_NODE_LIMIT:
        raise BoundUnavailableError(
            f"{n} distinct beds in one round exceeds BOUND_NODE_LIMIT="
            f"{BOUND_NODE_LIMIT}; no proven lower bound is available at this "
            "instance size and SPEC-003 forbids substituting a heuristic tour, "
            "so this instance cannot be reported on"
        )
    if n <= EXACT_NODE_LIMIT:
        return NecessaryMotion(
            _exact_open_path(_cost_matrix(unique, graph)),
            method="exact: Held-Karp subset DP over free-endpoint Hamiltonian paths",
            exact=True,
            n_nodes=n,
        )
    return NecessaryMotion(
        proven_lower_bound_metres(unique, graph, iterations=iterations),
        method=(
            "proven lower bound: max(minimum spanning tree, Held-Karp 1-tree "
            "Lagrangian bound on the dummy-augmented instance)"
        ),
        exact=False,
        n_nodes=n,
    )


__all__ = [
    "BOUND_NODE_LIMIT",
    "EXACT_NODE_LIMIT",
    "LAGRANGIAN_ITERATIONS",
    "BoundUnavailableError",
    "NecessaryMotion",
    "exact_open_tour_metres",
    "necessary_metres",
    "proven_lower_bound_metres",
]
