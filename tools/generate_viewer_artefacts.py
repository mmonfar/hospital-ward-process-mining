"""Demo artefact bundle for the N14 ward viewer. SPEC-005.

NOT part of the governed SPEC-001/003/004 pipeline in the way N01's
`hwpm.ingest.synthetic` is -- this script *orchestrates* that governed code
(`hwpm.ingest`, `hwpm.mining`, `hwpm.analytics`, `hwpm.optimize`) to produce a
demo dataset for `web/viewer.html`, and lives in `tools/` rather than
`src/hwpm/artefact/` for a layering reason, not a laziness one:
`tool.importlinter`'s "Layers point inward" contract places `hwpm.artefact`
*below* `hwpm.ingest`/`hwpm.mining`/`hwpm.optimize`/`hwpm.analytics` (every
stage above it writes an envelope; the envelope module cannot import the
stages that write into it without a cycle). An orchestrator that calls all
four of those layers therefore cannot live inside the `hwpm` package at all
without sitting above `hwpm.cli`, which is more surface than a demo generator
earns. `tools/generate_rfid_demo_log.py` set the precedent: an ungoverned
script, outside the layered package, reusing real `hwpm.domain` types.

**Synthetic, and only synthetic** (ADR-0005, CLAUDE.md rule 2). Nothing here
reads `HWPM_DATA_DIR`; every number is computed by the governed analytics and
optimisation code, run on `hwpm.optimize.instances.tiny_instance` and
`hwpm.ingest.synthetic`'s generators. All randomness comes from a single
seeded `random.Random` (CLAUDE.md rule 3), so `main()` is reproducible.

Deliberate scope cut, stated rather than silent: the same small `Instance`
(and schedules drawn on it) is reused as the substrate for the front, the
motion report and the missed-MDT opportunities, rather than three unrelated
generators. That keeps every artefact in the bundle mutually consistent (the
same beds, the same clinicians, the same patients) at the cost of the bundle
being one small ward rather multiple wards' worth of variety -- the right
trade for a viewer demo, wrong for anything claiming to be a benchmark.

Usage: python tools/generate_viewer_artefacts.py [output_dir]
  (defaults to web/demo/)
"""

from __future__ import annotations

import dataclasses
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any

from hwpm.analytics import MotionParams, analyse
from hwpm.artefact import ArtefactEnvelope
from hwpm.domain import (
    BedsideEpisode,
    Calibration,
    ClinicianId,
    PatientId,
    RequiredSpecialtyStrategyKey,
    Specialty,
    Trajectory,
)
from hwpm.domain.schedule import Schedule
from hwpm.domain.travel import TravelGraph
from hwpm.ingest.specialty import derive_all
from hwpm.ingest.synthetic import EvidenceConfig, GroundTruth, generate_evidence
from hwpm.mining.episodes import reconstruct_rounds
from hwpm.mining.mdt import detect_opportunistic
from hwpm.optimize.baselines import HillClimbingScheduler, RandomSearchScheduler
from hwpm.optimize.evaluate import evaluate, pareto_front
from hwpm.optimize.instances import realistic_single_ward
from hwpm.optimize.nsga2 import Nsga2Scheduler
from hwpm.optimize.types import Budget, Instance, Objectives

#: `realistic_single_ward`'s own construction rng, held fixed rather than
#: drawn from the bundle's seed. Node N10's benchmark (`test_optimize_nsga2.py`
#: `test_front_is_nondominated`) already measured this exact
#: (n_beds=12, n_slots=24) instance at seed 17 as one NSGA-II finds a rich
#: front for within a few seconds; other seeds tried while building this demo
#: (2026, and 2026-derived draws) produced instances NSGA-II could not clear
#: at all within budget. A known-feasible seed beats a "more random" one for
#: a demo dataset that has to render every time this script runs.
_INSTANCE_SEED = 17

METHOD_VERSION = "viewer_demo/1.0.0"
DATE_RANGE = "synthetic demo data (no real dates -- ADR-0005)"
ALL_STRATEGIES: tuple[RequiredSpecialtyStrategyKey, ...] = tuple(
    RequiredSpecialtyStrategyKey
)
DEFAULT_KEY = RequiredSpecialtyStrategyKey.REFERRAL

#: SPEC-004's five objectives, with the metadata `paretoFront()`
#: (web/design/parallel.js) needs to draw an honest axis: unit, and which
#: direction is "better" -- never inferred, always stated (Objectives.SENSES
#: mirrors this on the Python side; kept here as plain data because this
#: script may not import hwpm.optimize.types for JSON purposes beyond typing).
OBJECTIVES_META: tuple[dict[str, Any], ...] = (
    {
        "key": "copresence",
        "label": "MDT co-presence",
        "unit": "fraction",
        "direction": "max",
    },
    {"key": "motion_m", "label": "Motion", "unit": "m", "direction": "min"},
    {
        "key": "disruption",
        "label": "Nursing disruption",
        "unit": "visits",
        "direction": "min",
    },
    {"key": "continuity", "label": "Continuity", "unit": "fraction", "direction": "max"},
    {"key": "makespan_s", "label": "Makespan", "unit": "s", "direction": "min"},
)


def _node_kind(node_id: str) -> str:
    if "/BED" in node_id:
        return "bed"
    if node_id.endswith("/CORRIDOR"):
        return "corridor"
    if node_id.endswith("/LIFT"):
        return "lift"
    if node_id.endswith("/STAIR"):
        return "stair"
    return "other"


def layout_payload(graph: TravelGraph) -> dict[str, Any]:
    """`layout.json`: every node and edge `TravelGraph` knows about (N06),
    walked through its public accessors rather than any literal geometry
    (SPEC-005 acceptance criterion 1)."""
    nodes = []
    for node in graph.nodes():
        x, y, z = graph.position(node)
        nodes.append(
            {"id": node.value, "kind": _node_kind(node.value), "x": x, "y": y, "z": z}
        )
    edges = [
        {"a": a.value, "b": b.value, "metres": round(m, 3), "seconds": round(s, 3)}
        for a, b, m, s in graph.edges()
    ]
    return {"nodes": nodes, "edges": edges}


def _envelope(
    kind: str, strategy: RequiredSpecialtyStrategyKey, payload: dict[str, Any]
) -> ArtefactEnvelope:
    return ArtefactEnvelope(
        kind=kind,
        method_version=METHOD_VERSION,
        strategy=strategy,
        available_strategies=frozenset(ALL_STRATEGIES),
        calibration=Calibration.UNCALIBRATED,
        payload=payload,
    )


def strategy_required_maps(
    base_required: dict[PatientId, frozenset[Specialty]], rng: Random
) -> dict[RequiredSpecialtyStrategyKey, dict[PatientId, frozenset[Specialty]]]:
    """The five strategies' `required` mappings for one patient set.

    Reuses N04's real machinery (`hwpm.ingest.synthetic.generate_evidence`,
    `hwpm.ingest.specialty.derive_all`) rather than hand-rolling a spread, so
    the five strategies here genuinely disagree the way SPEC-001 says they
    do (imperfect recall per source) instead of being a cosmetic fan-out of
    one number. `base_required` stands in for `GroundTruth.required_specialties`
    -- what a clinician would say, on review, each patient actually needed.
    """
    truth = GroundTruth(
        schedules={}, required_specialties=dict(base_required), total_distance_m={}
    )
    evidence = generate_evidence(
        truth,
        EvidenceConfig(
            referral_recall=0.9,
            consult_note_recall=0.6,
            problem_list_recall=0.55,
            # Both left at 0: `tiny_instance`'s roster holds only two
            # specialties (general medicine, cardiology), so any "stale"
            # evidence for a specialty outside that pool would produce a
            # `required` set no rostered clinician can cover, which is an
            # infeasible *instance*, not a strategy disagreement worth
            # demonstrating. SPEC-001's stale-referral/comorbidity bias is
            # real; it just needs a roster that covers the full specialty
            # pool to demo safely, which this small viewer instance does not.
            referral_stale_rate=0.0,
            problem_list_comorbidity_rate=0.0,
        ),
        rng,
    )
    determinations = derive_all(evidence)
    return {
        key: {
            patient: det.for_mdt().included
            for patient, det in determinations.select(key).items()
        }
        for key in ALL_STRATEGIES
    }


def _feasible(schedule: Schedule, inst: Instance) -> bool:
    try:
        schedule.validate(inst.constraints)
        return True
    except Exception:
        return False


def _objectives_dict(o: Objectives) -> dict[str, float | int]:
    return {
        "copresence": round(o.copresence, 4),
        "motion_m": round(o.motion_m, 2),
        "disruption": o.disruption,
        "continuity": round(o.continuity, 4),
        "makespan_s": o.makespan_s,
    }


def _schedule_payload(
    schedule_id: str, schedule: Schedule, objectives: Objectives, inst: Instance
) -> dict[str, Any]:
    return {
        "id": schedule_id,
        "label": schedule_id,
        "objectives": _objectives_dict(objectives),
        "feasible": _feasible(schedule, inst),
        "visits": [
            {
                "clinician": v.clinician.value,
                "patient": v.patient.value,
                "bed": inst.beds[v.patient].value if v.patient in inst.beds else None,
                "start_s": inst.slots.seconds(v.start),
                "duration_s": inst.slots.seconds(v.duration),
            }
            for v in sorted(schedule.visits, key=lambda v: (v.clinician.value, v.start))
        ],
    }


def build_front_artefacts(
    base_inst: Instance,
    required_maps: dict[
        RequiredSpecialtyStrategyKey, dict[PatientId, frozenset[Specialty]]
    ],
    rng: Random,
    *,
    nsga2_budget: Budget | None = None,
    baseline_scheduler_budget: Budget | None = None,
) -> dict[RequiredSpecialtyStrategyKey, ArtefactEnvelope]:
    """`front.{strategy}.json`: the Pareto front browser's data.

    One baseline schedule -- drawn once, uncoordinated (`RandomSearchScheduler`
    with a one-draw budget) -- is re-evaluated under every strategy's
    `required` mapping, per SPEC-005: "always show today's observed baseline
    alongside", anchored to one reality rather than five.

    Per strategy, NSGA-II's own front is topped up with the union of N09's two
    baselines' evaluated candidates, re-filtered through `pareto_front` -- the
    same "merge every scheduler's output into one archive" pattern N09/N10 use
    for their own gate comparisons. A sparser strategy (fewer multi-specialty
    patients, e.g. `intersection`) gives NSGA-II less objective-space to work
    with and its own front alone sometimes lands under the browser's minimum
    of five (SPEC-005 acceptance criterion 6); this is a genuine property of
    a sparse requirement set, not a bug to hide, so it is topped up rather
    than the requirement being loosened.
    """
    nsga2_budget = nsga2_budget or Budget(max_seconds=4.0)
    baseline_scheduler_budget = baseline_scheduler_budget or Budget(
        max_seconds=4.0, max_evaluations=2000
    )
    baseline_inst = dataclasses.replace(base_inst, required=required_maps[DEFAULT_KEY])
    baseline_front = RandomSearchScheduler().solve(
        baseline_inst, Budget(max_seconds=1.0, max_evaluations=1), Random(rng.random())
    )
    if not baseline_front:
        raise RuntimeError(
            "baseline draw produced no feasible schedule; widen the instance"
        )
    baseline_schedule = baseline_front[0][0]

    out: dict[RequiredSpecialtyStrategyKey, ArtefactEnvelope] = {}
    for key in ALL_STRATEGIES:
        strategy_inst = dataclasses.replace(base_inst, required=required_maps[key])
        nsga2_front = Nsga2Scheduler().solve(
            strategy_inst, nsga2_budget, Random(rng.random())
        )
        hc_front = HillClimbingScheduler().solve(
            strategy_inst, baseline_scheduler_budget, Random(rng.random())
        )
        rs_front = RandomSearchScheduler().solve(
            strategy_inst, baseline_scheduler_budget, Random(rng.random())
        )
        front = pareto_front(nsga2_front + hc_front + rs_front)
        baseline_objectives = evaluate(baseline_schedule, strategy_inst)
        payload = {
            "objectives_meta": list(OBJECTIVES_META),
            "baseline": {
                "id": "baseline",
                "label": "Today (observed, uncoordinated)",
                "objectives": _objectives_dict(baseline_objectives),
                "visits": [
                    {
                        "clinician": v.clinician.value,
                        "patient": v.patient.value,
                        "bed": strategy_inst.beds[v.patient].value
                        if v.patient in strategy_inst.beds
                        else None,
                        "start_s": strategy_inst.slots.seconds(v.start),
                        "duration_s": strategy_inst.slots.seconds(v.duration),
                    }
                    for v in sorted(
                        baseline_schedule.visits,
                        key=lambda v: (v.clinician.value, v.start),
                    )
                ],
            },
            "schedules": [
                _schedule_payload(f"S{i:02d}", schedule, objectives, strategy_inst)
                for i, (schedule, objectives) in enumerate(front)
            ],
        }
        out[key] = _envelope("pareto_front", key, payload)
    return out


def _draw_day_episodes(inst: Instance, day: date, rng: Random) -> list[BedsideEpisode]:
    """One ward-day's `BedsideEpisode`s, from one uncoordinated schedule drawn
    on `inst`. Standing in for a real day's mined episodes -- see the module
    docstring's scope note."""
    front = RandomSearchScheduler().solve(
        inst, Budget(max_seconds=1.0, max_evaluations=1), rng
    )
    if not front:
        return []
    schedule = front[0][0]
    day_start = datetime.combine(day, datetime.min.time()).replace(hour=8)
    episodes: list[BedsideEpisode] = []
    for visit in schedule.visits:
        bed = inst.beds.get(visit.patient)
        if bed is None:
            continue
        start = day_start + timedelta(seconds=inst.slots.seconds(visit.start))
        end = start + timedelta(seconds=inst.slots.seconds(visit.duration))
        episodes.append(
            BedsideEpisode(
                clinician=visit.clinician,
                bed=bed,
                start=start,
                end=end,
                confidence=1.0,
                source_events=(),
                patient=visit.patient,
            )
        )
    return episodes


def build_motion_artefact(
    base_inst: Instance, graph: TravelGraph, rng: Random
) -> dict[RequiredSpecialtyStrategyKey, ArtefactEnvelope]:
    """`motion.{strategy}.json`. Motion doesn't depend on `RequiredSpecialty`
    (walking distance is the same however the specialty requirement was
    determined), so one `MotionReport` is computed and re-keyed to all five
    strategies via `ArtefactEnvelope.with_strategy` -- exactly the operation
    it exists for."""
    days = [date(2026, 1, 5) + timedelta(days=offset) for offset in range(4)]
    all_episodes: list[BedsideEpisode] = []
    for day in days:
        all_episodes.extend(_draw_day_episodes(base_inst, day, rng))

    rounds = []
    for day in days:
        rounds.extend(reconstruct_rounds(all_episodes, day))

    report = analyse(rounds, graph, rng, MotionParams(n_replicates=300))
    payload = {
        "observed_m": round(report.observed_m, 2),
        "necessary_m": round(report.necessary_m, 2),
        "attributable_m": round(report.attributable_m, 2),
        "attributable_fraction": round(report.attributable_fraction, 4),
        "ci95": [round(report.ci95[0], 2), round(report.ci95[1], 2)],
        "params": report.params,
    }
    base = _envelope("motion_report", DEFAULT_KEY, payload)
    return {key: base.with_strategy(key) for key in ALL_STRATEGIES}


def build_opportunities_artefacts(
    base_inst: Instance,
    required_maps: dict[
        RequiredSpecialtyStrategyKey, dict[PatientId, frozenset[Specialty]]
    ],
    rng: Random,
) -> dict[RequiredSpecialtyStrategyKey, ArtefactEnvelope]:
    """`opportunities.{strategy}.json`: `detect_opportunistic` candidates.

    Trajectories are built directly from one day's routed schedule (arrive /
    depart at each visited bed, in route order) rather than from a separate
    RFID-style log -- see the module scope note. Clinician specialties come
    straight from `Instance.clinicians`, so `detect_opportunistic` sees
    exactly the roster the front and motion artefacts were built from.
    `detect_opportunistic` builds its own `TravelGraph()` internally
    (`hwpm.mining.mdt`), so this function takes none.
    """
    day = date(2026, 1, 6)
    front = RandomSearchScheduler().solve(
        base_inst, Budget(max_seconds=1.0, max_evaluations=1), rng
    )
    schedule = front[0][0] if front else Schedule(visits=())

    episodes: list[BedsideEpisode] = []
    trajectories_by_clinician: dict[ClinicianId, list] = {}
    day_start = datetime.combine(day, datetime.min.time()).replace(hour=8)
    clinician_specialties = {c.id: c.specialties for c in base_inst.clinicians}

    for clinician, route in schedule.routes().items():
        events = []
        for visit in route:
            bed = base_inst.beds.get(visit.patient)
            if bed is None:
                continue
            start = day_start + timedelta(seconds=base_inst.slots.seconds(visit.start))
            end = start + timedelta(seconds=base_inst.slots.seconds(visit.duration))
            from hwpm.domain import Event

            events.append(
                Event(
                    timestamp=start,
                    subject=clinician,
                    activity="arrive",
                    location=bed,
                    source="demo",
                )
            )
            events.append(
                Event(
                    timestamp=end,
                    subject=clinician,
                    activity="depart",
                    location=bed,
                    source="demo",
                )
            )
            episodes.append(
                BedsideEpisode(
                    clinician=clinician,
                    bed=bed,
                    start=start,
                    end=end,
                    confidence=1.0,
                    source_events=(),
                    patient=visit.patient,
                )
            )
        if events:
            trajectories_by_clinician[clinician] = Trajectory(
                subject=clinician, events=tuple(events)
            )

    # Attach clinician specialties onto the episodes `detect_opportunistic`
    # reads (`episode.clinician_specialties`), via the same helper N05 uses.
    from hwpm.mining.episodes import attach_clinician_specialties

    episodes = attach_clinician_specialties(episodes, clinician_specialties)
    trajectories = list(trajectories_by_clinician.values())

    out: dict[RequiredSpecialtyStrategyKey, ArtefactEnvelope] = {}
    for key in ALL_STRATEGIES:
        candidates = detect_opportunistic(trajectories, episodes, required_maps[key])
        payload = {
            "candidates": [
                {
                    "clinician": c.clinician.value,
                    "patient": c.patient.value,
                    "specialty": c.specialty.value,
                    "bed": c.bed.value,
                    "at": c.at.isoformat(),
                    "proximity_m": c.proximity_m,
                    "confidence": c.confidence,
                    "reason": c.reason,
                }
                for c in candidates
            ],
        }
        out[key] = _envelope("missed_mdt_opportunities", key, payload)
    return out


def build_manifest() -> dict[str, Any]:
    return {
        "method_version": METHOD_VERSION,
        "date_range": DATE_RANGE,
        "strategies": [key.value for key in ALL_STRATEGIES],
        "default_strategy": DEFAULT_KEY.value,
        "artefacts": {
            "layout": "layout.json",
            "front": "front.{strategy}.json",
            "motion": "motion.{strategy}.json",
            "opportunities": "opportunities.{strategy}.json",
        },
        "note": (
            "Synthetic demo data (ADR-0005): every figure is computed by the "
            "governed hwpm.analytics/hwpm.optimize code, run on "
            "hwpm.optimize.instances.tiny_instance rather than a real extract."
        ),
    }


def build_bundle(seed: int = 2026) -> dict[str, Any]:
    rng = Random(seed)
    graph = TravelGraph()
    base_inst = realistic_single_ward(Random(_INSTANCE_SEED), n_beds=12, n_slots=24)
    required_maps = strategy_required_maps(base_inst.required, rng)

    front = build_front_artefacts(base_inst, required_maps, rng)
    motion = build_motion_artefact(base_inst, graph, rng)
    opportunities = build_opportunities_artefacts(base_inst, required_maps, rng)

    return {
        "layout": layout_payload(graph),
        "front": front,
        "motion": motion,
        "opportunities": opportunities,
        "manifest": build_manifest(),
    }


def write_bundle(bundle: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "layout.json").write_text(
        json.dumps(bundle["layout"], indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(bundle["manifest"], indent=2) + "\n", encoding="utf-8"
    )
    for kind in ("front", "motion", "opportunities"):
        for key, envelope in bundle[kind].items():
            path = out_dir / f"{kind}.{key.value}.json"
            path.write_text(
                json.dumps(envelope.to_mapping(), indent=2) + "\n", encoding="utf-8"
            )


def main() -> None:
    out_dir = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parent.parent / "web" / "demo"
    )
    bundle = build_bundle()
    write_bundle(bundle, out_dir)
    n_files = sum(len(bundle[k]) for k in ("front", "motion", "opportunities")) + 2
    print(f"wrote {n_files} artefact files to {out_dir}")


if __name__ == "__main__":
    main()
