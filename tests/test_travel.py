"""TravelGraph tests. SPEC-003 acceptance criteria 1-2, N06-travel-graph.

Pure unit tests, no I/O (docs/06-QA-AND-DEADCODE.md, "Test strategy: Domain").
Nothing here touches HWPM_DATA_DIR or real data — the graph is built purely
from the reference geometry in web/hospital-ward.html (read, not modified,
by src/hwpm/domain/travel.py).
"""

from __future__ import annotations

import pytest

from hwpm.domain.model import LocationId, Point
from hwpm.domain.travel import TravelGraph

# A spread of locations covering: same ward, same floor different ward,
# different floors, and the corridor/lift/stair nodes themselves (which are
# valid LocationIds too — TravelGraph.path can return them).
_SAMPLE_LOCATIONS = [
    LocationId("1A/BED0"),
    LocationId("1A/BED5"),
    LocationId("1B/BED2"),
    LocationId("1C/CORRIDOR"),
    LocationId("2A/BED0"),
    LocationId("2C/BED3"),
    LocationId("3B/BED1"),
    LocationId("FLOOR0/LIFT"),
    LocationId("FLOOR2/STAIR"),
]


# ---------------------------------------------------------------------------
# Criterion 1: inter-floor cost exceeds Euclidean by a factor reflecting the
# lift path. test_interfloor_cost
# ---------------------------------------------------------------------------


def test_interfloor_cost() -> None:
    """Two beds at the same local position on adjacent floors (1A/BED0 and
    2A/BED0 — both index 0 on their floor, so they share x and z) are the
    spec's own "4 m apart on different floors" example almost exactly: the
    graph must report a routed cost far larger than that straight line, in
    both metres and seconds, because getting from one to the other means a
    walk to a corridor, a lift or stairs, and a walk back out — not a
    straight line through the floor slab.
    """
    graph = TravelGraph()
    bed_a = LocationId("1A/BED0")
    bed_b = LocationId("2A/BED0")

    # Same local (x, z) offset within their respective wards -> the only
    # difference is the floor, so Euclidean distance is just the floor
    # height (4.2 m), matching the spec's illustrative "4 m apart" figure.
    euclidean_m = Point(-4.0, 0.0, -2.2).euclidean_distance_to(Point(-4.0, 4.2, -2.2))
    assert euclidean_m == pytest.approx(4.2)

    routed = graph.cost(bed_a, bed_b)

    # Metres: a straight line through the floor slab is not a corridor route
    # -- walking to/from the corridor and lift/stair lobby alone adds tens
    # of metres.
    assert routed.metres > euclidean_m * 3

    # Seconds: this is where the lift path bites hardest. Naive Euclidean
    # distance at walking pace would suggest a few seconds; the routed cost
    # includes corridor walking plus either a lift wait or a slow stair
    # climb, an order of magnitude more.
    naive_seconds = euclidean_m / TravelGraph.WALK_SPEED_M_S
    assert routed.seconds > naive_seconds * 5

    # And the path really does leave the ward via a corridor and cross a
    # lift or stair node -- it is not a fabricated number.
    path = graph.path(bed_a, bed_b)
    assert path[0] == bed_a
    assert path[-1] == bed_b
    crossed_vertically = any(
        loc.value.startswith("FLOOR") and ("/LIFT" in loc.value or "/STAIR" in loc.value)
        for loc in path
    )
    assert crossed_vertically


def test_interfloor_cost_same_floor_is_cheaper() -> None:
    """Sanity check on the same fixture: two beds in the *same* ward (no
    lift or stairs involved at all) must cost less than the cross-floor pair
    above -- if this were false the lift/stair layer would be doing
    nothing."""
    graph = TravelGraph()
    same_ward = graph.cost(LocationId("1A/BED0"), LocationId("1A/BED5"))
    cross_floor = graph.cost(LocationId("1A/BED0"), LocationId("2A/BED0"))
    assert same_ward.seconds < cross_floor.seconds


# ---------------------------------------------------------------------------
# Criterion 2: symmetry and triangle inequality. test_metric_properties
# ---------------------------------------------------------------------------


def test_metric_properties() -> None:
    graph = TravelGraph()

    for a in _SAMPLE_LOCATIONS:
        for b in _SAMPLE_LOCATIONS:
            forward = graph.cost(a, b)
            backward = graph.cost(b, a)
            assert forward.metres == pytest.approx(backward.metres, abs=1e-9)
            assert forward.seconds == pytest.approx(backward.seconds, abs=1e-9)

    for a in _SAMPLE_LOCATIONS:
        for b in _SAMPLE_LOCATIONS:
            for c in _SAMPLE_LOCATIONS:
                ab = graph.cost(a, b)
                bc = graph.cost(b, c)
                ac = graph.cost(a, c)
                assert ac.metres <= ab.metres + bc.metres + 1e-6
                assert ac.seconds <= ab.seconds + bc.seconds + 1e-6


# ---------------------------------------------------------------------------
# Supporting behaviour
# ---------------------------------------------------------------------------


def test_cost_to_self_is_zero() -> None:
    graph = TravelGraph()
    bed = LocationId("1A/BED0")
    zero = graph.cost(bed, bed)
    assert zero.metres == 0.0
    assert zero.seconds == 0.0
    assert graph.path(bed, bed) == [bed]


def test_unknown_location_raises_key_error() -> None:
    graph = TravelGraph()
    with pytest.raises(KeyError):
        graph.cost(LocationId("9Z/BED0"), LocationId("1A/BED0"))


def test_path_endpoints_match_query() -> None:
    graph = TravelGraph()
    a, b = LocationId("1A/BED0"), LocationId("3C/BED5")
    path = graph.path(a, b)
    assert path[0] == a
    assert path[-1] == b
    assert len(path) == len(set(path))  # no revisits


def test_lift_wait_is_a_tunable_parameter() -> None:
    """The spec's open question flags lift waiting time as unmeasured; it
    must be a constructor parameter, not a baked-in constant, so a future
    calibration against real dispatch logs is a one-line change at the call
    site."""
    quick = TravelGraph(lift_wait_s=0.0)
    slow = TravelGraph(lift_wait_s=300.0)

    a, b = LocationId("1A/BED0"), LocationId("3C/BED5")
    assert slow.cost(a, b).seconds >= quick.cost(a, b).seconds


def test_travel_cost_rejects_negative_values() -> None:
    from hwpm.domain.travel import TravelCost

    with pytest.raises(ValueError):
        TravelCost(metres=-1.0, seconds=1.0)
    with pytest.raises(ValueError):
        TravelCost(metres=1.0, seconds=-1.0)
