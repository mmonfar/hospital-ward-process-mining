"""Routed travel graph over ward geometry. SPEC-003 ("The first task: replace
Euclidean distance"), N06-travel-graph.

Why this exists: the prototype in `web/hospital-ward.html` computes
straight-line distance between bed coordinates (see `globalPos`, around line
166). That is wrong in a way that biases the project's headline finding —
two beds 4 m apart on different floors are a lift ride and ~90 s apart, so
Euclidean distance systematically *understates* inter-floor movement, which
is precisely the movement asynchronous rounds generate. `TravelGraph` is the
single place in the codebase permitted to compute distance
(01-DOMAIN-MODEL.md, "Place"): "Nothing outside the domain computes
distance."

Geometry is read from, but does not modify, `web/hospital-ward.html`'s
`WARDS` array (9 wards, 3 floors, 3 wards/floor) and `BED_LOCAL` (6 beds per
ward). Coordinates there are in three.js scene units which the prototype
already treats as metres — a ward-to-ward spacing of 32 and a floor height of
4.2 reproduce the spec's own "4 m apart on different floors" example almost
exactly (see `test_interfloor_cost`), so this module keeps that scale rather
than inventing a new one. The corridor/lift/stair layer that connects wards
is *not* in the prototype (it only ever draws one ward at a time) and is this
module's own addition, built from the geometry that is there: wards have no
west wall (`buildWardArchitecture` only builds north/south/east walls), so
the corridor is modelled as attaching on the open west side.

Performance note (ADR-0003-c-kernel-profile-gate): this is called in the
optimiser's hot loop. Dijkstra from the query's source node, memoised per
source for the lifetime of the graph, is used rather than all-pairs
Floyd-Warshall: with ~9 wards * 6 beds plus a handful of corridor/lift/stair
nodes (under 70 nodes total), computing full all-pairs distances up front
would do a lot of work for pairs nothing ever queries, and per-source
Dijkstra is O((V+E) log V) — fast enough here without reaching for C, which
ADR-0003 says not to do pre-emptively. Pure Python plus stdlib only.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from hwpm.domain.model import LocationId

# ---------------------------------------------------------------------------
# Reference geometry, mirrored from web/hospital-ward.html (read, not
# modified). Keep these two literals in visual correspondence with the
# WARDS / BED_LOCAL arrays there so a diff in the prototype is easy to spot.
# ---------------------------------------------------------------------------

_WARD_SPECS: tuple[tuple[str, int], ...] = (
    ("1A", 0),
    ("1B", 0),
    ("1C", 0),
    ("2A", 1),
    ("2B", 1),
    ("2C", 1),
    ("3A", 2),
    ("3B", 2),
    ("3C", 2),
)

_BED_LOCAL: tuple[tuple[float, float], ...] = (
    (-4.0, -2.2),
    (0.0, -2.2),
    (4.0, -2.2),
    (-4.0, 2.2),
    (0.0, 2.2),
    (4.0, 2.2),
)

_ROOM_W = 12.0  # matches buildWardArchitecture's ROOM_W
_WARD_SPACING_M = 32.0  # matches globalPos: x = indexOnFloor * 32
_FLOOR_HEIGHT_M = 4.2  # matches globalPos: y = floor * 4.2

# The prototype never places a corridor, lift or stair — it renders one ward
# at a time. These lobby positions are this module's addition: a single lift
# shaft and stairwell per floor, west of ward "*A" (index 0 on each floor),
# where the open (wall-less) side of every ward already faces.
_LIFT_LOBBY_X = -(_ROOM_W / 2 + 10.0)
_STAIR_LOBBY_X = -(_ROOM_W / 2 + 14.0)


@dataclass(frozen=True)
class _Point3:
    """Local coordinate helper. Not `hwpm.domain.model.Point` — that type's
    `euclidean_distance_to` is deliberately the *wrong* answer for anything
    but the synthetic generator's interim ground truth (see its docstring);
    keeping this as a private type stops anyone reaching for it as a graph
    distance by mistake."""

    x: float
    y: float
    z: float

    def distance_to(self, other: _Point3) -> float:
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))


@dataclass(frozen=True)
class TravelCost:
    """A routed cost between two locations: metres *and* seconds.

    The two are not proportional — a lift ride adds seconds (waiting) far
    out of proportion to the metres it covers, which is exactly the effect
    this graph exists to capture. Callers needing "how far" or "how long"
    must pick the field explicitly rather than assume one derives from the
    other at a constant speed.
    """

    metres: float
    seconds: float

    def __post_init__(self) -> None:
        if self.metres < 0:
            raise ValueError(f"metres must be >= 0, got {self.metres!r}")
        if self.seconds < 0:
            raise ValueError(f"seconds must be >= 0, got {self.seconds!r}")


@dataclass(frozen=True)
class _Edge:
    to: str
    metres: float
    seconds: float


class TravelGraph:
    """Routed graph over the ward layout: beds, ward corridors, one lift
    shaft and one stairwell per floor.

    `cost` and `path` are the only sanctioned way to reason about distance
    outside this module (01-DOMAIN-MODEL.md, "Place").
    """

    # --- modelling assumptions, all tunable, none yet measured ------------
    #
    # Walking pace along corridors and wards. A brisk-but-not-running
    # clinical pace; not measured against real staff movement.
    WALK_SPEED_M_S = 1.2

    # Vertical stair-climbing pace. Slower than level walking; not measured.
    STAIR_SPEED_M_S = 0.5

    # Vertical lift car speed while moving.
    LIFT_SPEED_M_S = 1.0

    # MODELLING ASSUMPTION (SPEC-003 open question: "Lift waiting time —
    # measured, or assumed constant?" — not yet measured). Mean time spent
    # waiting for the car to arrive, added once per lift traversal regardless
    # of how many floors it crosses. Tunable via the constructor so a future
    # calibration against real dispatch logs does not require touching this
    # module's structure, only the argument passed to it.
    DEFAULT_LIFT_WAIT_S = 45.0

    def __init__(self, lift_wait_s: float = DEFAULT_LIFT_WAIT_S) -> None:
        if lift_wait_s < 0:
            raise ValueError(f"lift_wait_s must be >= 0, got {lift_wait_s!r}")
        self._lift_wait_s = lift_wait_s
        self._points: dict[str, _Point3] = {}
        self._adj: dict[str, list[_Edge]] = {}
        # Per-source Dijkstra trees, memoised for the graph's lifetime. The
        # graph is static once built (frozen ward layout), so this cache is
        # never invalidated. A plain dict rather than functools.lru_cache:
        # the graph is a mutable-by-construction, non-frozen object (its
        # adjacency is built incrementally in _build), so it is not hashable
        # in a way lru_cache could key on safely.
        self._dijkstra_cache: dict[str, dict[str, tuple[float, float, str | None]]] = {}
        self._build()

    # -- construction --------------------------------------------------

    def _add_node(self, node_id: str, point: _Point3) -> None:
        self._points[node_id] = point
        self._adj.setdefault(node_id, [])

    def _add_edge(self, a: str, b: str, metres: float, seconds: float) -> None:
        self._adj[a].append(_Edge(b, metres, seconds))
        self._adj[b].append(_Edge(a, metres, seconds))

    def _build(self) -> None:
        floor_counts = [0, 0, 0]
        for ward_id, floor in _WARD_SPECS:
            index_on_floor = floor_counts[floor]
            floor_counts[floor] += 1
            ward_x = index_on_floor * _WARD_SPACING_M
            floor_y = floor * _FLOOR_HEIGHT_M

            corridor_id = f"{ward_id}/CORRIDOR"
            corridor_point = _Point3(ward_x - _ROOM_W / 2, floor_y, 0.0)
            self._add_node(corridor_id, corridor_point)

            for bed_index, (bx, bz) in enumerate(_BED_LOCAL):
                bed_id = f"{ward_id}/BED{bed_index}"
                bed_point = _Point3(ward_x + bx, floor_y, bz)
                self._add_node(bed_id, bed_point)
                d = bed_point.distance_to(corridor_point)
                self._add_edge(bed_id, corridor_id, d, d / self.WALK_SPEED_M_S)

            lift_id = f"FLOOR{floor}/LIFT"
            stair_id = f"FLOOR{floor}/STAIR"
            if lift_id not in self._points:
                self._add_node(lift_id, _Point3(_LIFT_LOBBY_X, floor_y, 0.0))
            if stair_id not in self._points:
                self._add_node(stair_id, _Point3(_STAIR_LOBBY_X, floor_y, 0.0))

            d_lift = corridor_point.distance_to(self._points[lift_id])
            self._add_edge(corridor_id, lift_id, d_lift, d_lift / self.WALK_SPEED_M_S)
            d_stair = corridor_point.distance_to(self._points[stair_id])
            self._add_edge(corridor_id, stair_id, d_stair, d_stair / self.WALK_SPEED_M_S)

        # Vertical shafts: every pair of floors gets a direct lift edge and a
        # direct stair edge (rather than chaining through intermediate-floor
        # nodes), so a lift traversal incurs the wait exactly once regardless
        # of how many floors it spans.
        floors = sorted({floor for _, floor in _WARD_SPECS})
        for i, f1 in enumerate(floors):
            for f2 in floors[i + 1 :]:
                vertical = abs(f2 - f1) * _FLOOR_HEIGHT_M
                lift_seconds = vertical / self.LIFT_SPEED_M_S + self._lift_wait_s
                self._add_edge(
                    f"FLOOR{f1}/LIFT", f"FLOOR{f2}/LIFT", vertical, lift_seconds
                )
                stair_seconds = vertical / self.STAIR_SPEED_M_S
                self._add_edge(
                    f"FLOOR{f1}/STAIR", f"FLOOR{f2}/STAIR", vertical, stair_seconds
                )

    # -- queries ----------------------------------------------------------

    def _resolve(self, location: LocationId) -> str:
        if location.value not in self._points:
            raise KeyError(f"unknown location: {location.value!r}")
        return location.value

    def _dijkstra(self, source: str) -> dict[str, tuple[float, float, str | None]]:
        """Shortest-path tree from `source`, weighted by seconds (the
        criterion a clinician actually optimises for when choosing lift vs.
        stairs), tracking accumulated metres and predecessor alongside.

        Non-negative edge weights throughout (walking, stair climbing, lift
        travel plus a non-negative wait are all >= 0), so plain Dijkstra
        applies and the result is a genuine shortest-path metric — which is
        what makes the triangle inequality in `test_metric_properties` a
        theorem rather than a hope.
        """
        cached = self._dijkstra_cache.get(source)
        if cached is not None:
            return cached

        best_seconds: dict[str, float] = {source: 0.0}
        best_metres: dict[str, float] = {source: 0.0}
        predecessor: dict[str, str | None] = {source: None}
        visited: set[str] = set()
        heap: list[tuple[float, str]] = [(0.0, source)]

        while heap:
            seconds_so_far, node = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            for edge in self._adj[node]:
                candidate = seconds_so_far + edge.seconds
                if edge.to not in best_seconds or candidate < best_seconds[edge.to]:
                    best_seconds[edge.to] = candidate
                    best_metres[edge.to] = best_metres[node] + edge.metres
                    predecessor[edge.to] = node
                    heapq.heappush(heap, (candidate, edge.to))

        tree = {
            node: (best_seconds[node], best_metres[node], predecessor[node])
            for node in best_seconds
        }
        self._dijkstra_cache[source] = tree
        return tree

    def cost(self, a: LocationId, b: LocationId) -> TravelCost:
        """Routed cost between `a` and `b`: metres and seconds, both via the
        same shortest (by time) path. The single source of truth for
        distance — nothing outside `hwpm.domain` computes it independently.
        """
        a_id, b_id = self._resolve(a), self._resolve(b)
        if a_id == b_id:
            return TravelCost(metres=0.0, seconds=0.0)
        tree = self._dijkstra(a_id)
        if b_id not in tree:
            raise ValueError(f"no route between {a.value!r} and {b.value!r}")
        seconds, metres, _ = tree[b_id]
        return TravelCost(metres=metres, seconds=seconds)

    def path(self, a: LocationId, b: LocationId) -> list[LocationId]:
        """The sequence of graph nodes (beds, corridors, lift/stair lobbies)
        on the shortest (by time) route from `a` to `b`, inclusive of both
        endpoints."""
        a_id, b_id = self._resolve(a), self._resolve(b)
        tree = self._dijkstra(a_id)
        if b_id not in tree:
            raise ValueError(f"no route between {a.value!r} and {b.value!r}")
        nodes: list[str] = []
        current: str | None = b_id
        while current is not None:
            nodes.append(current)
            current = tree[current][2]
        nodes.reverse()
        return [LocationId(node) for node in nodes]

    # -- geometry export (N14-viewer) --------------------------------------
    #
    # `layout.json` (SPEC-005) must be served data, not literals baked into the
    # viewer -- acceptance criterion 1. This graph is the single place ward
    # geometry lives (01-DOMAIN-MODEL.md, "Place"), so the export walks it
    # rather than the front end re-reading `_WARD_SPECS`/`_BED_LOCAL` itself,
    # which would put a second, driftable copy of the geometry outside the
    # domain layer.

    def nodes(self) -> tuple[LocationId, ...]:
        """Every node id in the graph: beds, ward corridors, lift and stair
        lobbies. Insertion order, which is construction order in `_build` --
        deterministic for a given `TravelGraph()` (gate 8)."""
        return tuple(LocationId(node_id) for node_id in self._points)

    def position(self, location: LocationId) -> tuple[float, float, float]:
        """`(x, y, z)` scene-unit coordinates of `location`, y being the
        vertical (floor) axis -- the same convention `web/hospital-ward.html`
        and `_Point3` use."""
        point = self._points[self._resolve(location)]
        return (point.x, point.y, point.z)

    def edges(self) -> tuple[tuple[LocationId, LocationId, float, float], ...]:
        """Every undirected edge once, as `(a, b, metres, seconds)`. This is
        the "travel-graph edges" `layout.json` is asked to carry (SPEC-005,
        'Artefacts consumed')."""
        seen: set[frozenset[str]] = set()
        out: list[tuple[LocationId, LocationId, float, float]] = []
        for a_id, edges in self._adj.items():
            for edge in edges:
                key = frozenset((a_id, edge.to))
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    (LocationId(a_id), LocationId(edge.to), edge.metres, edge.seconds)
                )
        return tuple(out)
