"""Data-shaping tests for `tools/generate_viewer_artefacts.py`. SPEC-005, N14.

`tools/` sits outside the layered `hwpm` package (see that module's own
docstring for why: an orchestrator over `hwpm.ingest`/`hwpm.mining`/
`hwpm.optimize`/`hwpm.analytics` cannot live inside `hwpm.artefact`, which
`tool.importlinter`'s "Layers point inward" contract places *below* all four).
It is not swept by `testpaths` or installed as a package, so this file adds it
to `sys.path` directly -- the same access pattern the module itself uses to
reach `hwpm`.

Scope, stated rather than silent: these tests exercise the *shaping* functions
-- envelope construction, JSON-payload structure, the geometry walk -- fast
and without a solver. `build_bundle()` (the full orchestration: NSGA-II per
strategy plus two baseline schedulers, ~40-60s) is exercised by running
`python tools/generate_viewer_artefacts.py` by hand, not by this suite --
a test that takes a minute would either make the suite too slow to run on
every commit or get its budget cut until nobody trusts it
(docs/06-QA-AND-DEADCODE.md's own words about `bench`-marked tests). That is
the one thing about the viewer bundle this file does **not** verify by
automated test; the audit entry says so explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path
from random import Random

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import generate_viewer_artefacts as gva  # noqa: E402

from hwpm.artefact.envelope import ArtefactEnvelope  # noqa: E402
from hwpm.domain import Calibration, RequiredSpecialtyStrategyKey  # noqa: E402
from hwpm.domain.model import PatientId, Specialty  # noqa: E402
from hwpm.domain.schedule import PlannedVisit, Schedule  # noqa: E402
from hwpm.domain.travel import TravelGraph  # noqa: E402
from hwpm.optimize.instances import tiny_instance  # noqa: E402
from hwpm.optimize.types import Objectives  # noqa: E402

# ---------------------------------------------------------------------------
# layout.json
# ---------------------------------------------------------------------------


def test_layout_payload_has_every_bed_and_no_hardcoded_geometry() -> None:
    """`layout_payload` walks `TravelGraph`'s own accessors -- it defines no
    coordinate literal of its own (SPEC-005 acceptance criterion 1)."""
    payload = gva.layout_payload(TravelGraph())
    ids = {n["id"] for n in payload["nodes"]}
    assert "1A/BED0" in ids
    assert "FLOOR0/LIFT" in ids
    kinds = {n["id"]: n["kind"] for n in payload["nodes"]}
    assert kinds["1A/BED0"] == "bed"
    assert kinds["1A/CORRIDOR"] == "corridor"
    assert kinds["FLOOR0/LIFT"] == "lift"
    assert kinds["FLOOR0/STAIR"] == "stair"
    assert payload["edges"], "no edges -- the ward would render as disconnected dots"
    for edge in payload["edges"]:
        assert edge["a"] in ids and edge["b"] in ids
        assert edge["metres"] >= 0


def test_node_kind_classifies_every_reference_node() -> None:
    for node in TravelGraph().nodes():
        assert gva._node_kind(node.value) in {"bed", "corridor", "lift", "stair"}


# ---------------------------------------------------------------------------
# Envelope shaping
# ---------------------------------------------------------------------------


def test_envelope_offers_all_five_strategies() -> None:
    envelope = gva._envelope("demo_kind", RequiredSpecialtyStrategyKey.UNION, {"x": 1})
    assert envelope.strategy is RequiredSpecialtyStrategyKey.UNION
    assert envelope.available_strategies == frozenset(RequiredSpecialtyStrategyKey)
    assert envelope.calibration is Calibration.UNCALIBRATED
    # Round-trips through the real envelope contract (criteria 9/10): a
    # mapping produced by `to_mapping` loads back losslessly.
    reloaded = ArtefactEnvelope.from_mapping(envelope.to_mapping())
    assert reloaded.strategy is envelope.strategy
    assert reloaded.payload == envelope.payload


def test_objectives_dict_carries_every_spec004_field() -> None:
    o = Objectives(
        copresence=0.5, motion_m=12.3456, disruption=2, continuity=0.75, makespan_s=900
    )
    d = gva._objectives_dict(o)
    assert set(d) == {"copresence", "motion_m", "disruption", "continuity", "makespan_s"}
    assert d["motion_m"] == pytest.approx(12.35, abs=0.01)
    assert d["disruption"] == 2
    assert d["makespan_s"] == 900


def test_schedule_payload_maps_visits_to_beds_and_seconds() -> None:
    inst = tiny_instance(Random(3), n_beds=4, n_slots=6)
    patient = inst.patients[0].id
    clinician = inst.clinicians[0].id
    visit = PlannedVisit(clinician=clinician, patient=patient, start=1, duration=1)
    schedule = Schedule(visits=(visit,))
    objectives = Objectives(
        copresence=1.0, motion_m=5.0, disruption=0, continuity=0.0, makespan_s=600
    )

    payload = gva._schedule_payload("S00", schedule, objectives, inst)

    assert payload["id"] == "S00"
    assert payload["objectives"]["motion_m"] == 5.0
    assert payload["feasible"] is True  # a single legal visit violates nothing
    assert len(payload["visits"]) == 1
    v = payload["visits"][0]
    assert v["clinician"] == clinician.value
    assert v["patient"] == patient.value
    assert v["bed"] == inst.beds[patient].value
    assert v["start_s"] == inst.slots.seconds(1)
    assert v["duration_s"] == inst.slots.seconds(1)


def test_schedule_payload_marks_an_infeasible_schedule() -> None:
    """A double-booked clinician is a hard violation (`Schedule.hard_violations`
    via `double_bookings`) -- the payload must say so rather than silently
    treating every schedule as feasible (SPEC-005 failure mode: 'a smooth
    animation of an operationally infeasible schedule is persuasive in the
    wrong direction')."""
    inst = tiny_instance(Random(4), n_beds=4, n_slots=6)
    p0, p1 = inst.patients[0].id, inst.patients[1].id
    clinician = inst.clinicians[0].id
    overlapping = (
        PlannedVisit(clinician=clinician, patient=p0, start=0, duration=2),
        PlannedVisit(clinician=clinician, patient=p1, start=1, duration=2),
    )
    schedule = Schedule(visits=overlapping)
    objectives = Objectives(
        copresence=0.0, motion_m=1.0, disruption=0, continuity=0.0, makespan_s=100
    )

    payload = gva._schedule_payload("S01", schedule, objectives, inst)
    assert payload["feasible"] is False


# ---------------------------------------------------------------------------
# Strategy spread (criterion 9: all five, always)
# ---------------------------------------------------------------------------


def test_strategy_required_maps_covers_all_five_and_bounds_hold() -> None:
    """`union` must be a superset of every single-source strategy and
    `intersection` a subset, for every patient -- SPEC-001's bounds, checked
    on the maps this script actually feeds into an `Instance`."""
    base_required = {
        PatientId("P0"): frozenset({Specialty.GENERAL_MEDICINE, Specialty.CARDIOLOGY}),
        PatientId("P1"): frozenset({Specialty.CARDIOLOGY}),
    }
    rng = Random(7)
    maps = gva.strategy_required_maps(base_required, rng)

    assert set(maps) == set(RequiredSpecialtyStrategyKey)
    union = maps[RequiredSpecialtyStrategyKey.UNION]
    intersection = maps[RequiredSpecialtyStrategyKey.INTERSECTION]
    for key, required in maps.items():
        if key in (
            RequiredSpecialtyStrategyKey.UNION,
            RequiredSpecialtyStrategyKey.INTERSECTION,
        ):
            continue
        for patient in base_required:
            assert required.get(patient, frozenset()) <= union.get(patient, frozenset())
            assert intersection.get(patient, frozenset()) <= required.get(
                patient, frozenset()
            )


def test_strategy_required_maps_is_deterministic() -> None:
    base_required = {PatientId("P0"): frozenset({Specialty.GENERAL_MEDICINE})}
    first = gva.strategy_required_maps(base_required, Random(11))
    second = gva.strategy_required_maps(base_required, Random(11))
    assert first == second


# ---------------------------------------------------------------------------
# manifest.json
# ---------------------------------------------------------------------------


def test_manifest_names_all_five_strategies_and_a_default() -> None:
    manifest = gva.build_manifest()
    assert set(manifest["strategies"]) == {k.value for k in RequiredSpecialtyStrategyKey}
    assert manifest["default_strategy"] == gva.DEFAULT_KEY.value
    assert manifest["method_version"] == gva.METHOD_VERSION
    assert manifest.get("date_range")
