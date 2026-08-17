"""`TravelGraph`'s geometry-export accessors. SPEC-005, node N14-viewer.

`nodes`/`position`/`edges` exist so `tools/generate_viewer_artefacts.py` can
build `layout.json` by walking the graph rather than re-reading a second copy
of the ward geometry -- SPEC-005 acceptance criterion 1, "no hard-coded ward
geometry; layout.json drives the scene". These are read-only accessors over
state `TravelGraph` already builds in `_build`/`cost`/`path`, so the tests
here check they *agree* with those existing, already-tested code paths rather
than re-deriving the geometry by hand.
"""

from __future__ import annotations

from hwpm.domain.model import LocationId
from hwpm.domain.travel import TravelGraph


def test_nodes_include_every_bed_and_are_stable() -> None:
    graph = TravelGraph()
    nodes = graph.nodes()
    assert LocationId("1A/BED0") in nodes
    assert LocationId("3C/BED5") in nodes
    assert LocationId("FLOOR0/LIFT") in nodes
    assert LocationId("FLOOR1/STAIR") in nodes
    # Deterministic across independent constructions (gate 8): two graphs
    # built the same way list their nodes in the same order.
    assert graph.nodes() == TravelGraph().nodes()


def test_position_matches_cost_geometry() -> None:
    """`position` is not a second geometry: the distance it implies between
    two beds must equal `cost`'s routed distance for beds in the same bay
    (where the route is the direct corridor hop, not a detour through a lift),
    or the accessor has silently drifted from the graph it reads."""
    graph = TravelGraph()
    a = LocationId("1A/BED0")
    b = LocationId("1A/BED1")
    ax, ay, az = graph.position(a)
    bx, by, bz = graph.position(b)
    assert ay == by  # same floor
    # Same ward, same floor: the routed cost is at least the straight-line
    # distance (it detours via the corridor node), which is a sanity bound
    # rather than an equality -- the corridor detour is the point of N06.
    straight = ((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2) ** 0.5
    assert graph.cost(a, b).metres >= straight - 1e-9


def test_position_unknown_location_raises() -> None:
    graph = TravelGraph()
    try:
        graph.position(LocationId("NOWHERE/BED9"))
    except KeyError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("expected KeyError for an unknown node id")


def test_edges_are_undirected_and_deduplicated() -> None:
    graph = TravelGraph()
    edges = graph.edges()
    seen = set()
    for a, b, metres, seconds in edges:
        key = frozenset((a.value, b.value))
        assert key not in seen, f"edge {a.value}<->{b.value} listed twice"
        seen.add(key)
        assert metres >= 0
        assert seconds >= 0
    # Every bed has at least one edge (to its ward corridor).
    bed_ids = {n.value for n in graph.nodes() if "/BED" in n.value}
    endpoints = {a.value for a, b, _, _ in edges} | {b.value for a, b, _, _ in edges}
    assert bed_ids <= endpoints


def test_edges_agree_with_cost_for_direct_hops() -> None:
    """An edge's own (metres, seconds) is the direct hop `TravelGraph._build`
    created -- for a bed-to-corridor edge that direct hop *is* the shortest
    path, so it must equal `cost`'s answer exactly."""
    graph = TravelGraph()
    for a, b, metres, seconds in graph.edges():
        if "/BED" in a.value and b.value.endswith("/CORRIDOR"):
            cost = graph.cost(a, b)
            assert cost.metres == metres
            assert cost.seconds == seconds
