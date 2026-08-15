"""Demo-only RFID real-time movement log with stochastic operational disruptions.

NOT part of the governed SPEC-001 pipeline (`hwpm.ingest.synthetic`). That
module is deliberately scoped to clean arrive/depart events plus a few
typed-Event noise dimensions (SPEC-001, "Failure modes") — operational
disruptions like theatre overruns, emergency admissions, and equipment
outages are out of its scope by design (see its module docstring). This
script is a throwaway generator for an external demo: it reuses the real
`hwpm.domain` types and the reference ward geometry from `synthetic.py` so
the CSV shape is legitimate, but it is not audited, not wired into
`orchestration/graph.yaml`, and produces no ground truth. Do not treat its
output as a fixture for tests.

All randomness comes from a single seeded `Random` — deterministic, no
wall-clock reads, no file-system inputs (mirrors the determinism discipline
of the real generator even though this script itself isn't governed).

Usage: python tools/generate_rfid_demo_log.py
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from random import Random

from hwpm.domain import (
    Location,
    LocationId,
    LocationKind,
    Point,
)
from hwpm.ingest.synthetic import _build_wards

SEED = 7
SHIFT_START = datetime(2026, 1, 5, 8, 0, 0)
SHIFT_HOURS = 10
N_CLINICIANS = 12
STOPS_PER_CLINICIAN = 7
WALKING_SPEED_MPS = 1.2
PING_INTERVAL_S = 30
SOURCE = "rfid_synthetic_demo"

OT_PROBABILITY = 0.25
OT_PROLONG_PROBABILITY = 0.35
OT_NOMINAL_MINUTES = (60, 120)
OT_OVERRUN_MINUTES = (20, 75)

EMERGENCY_PROBABILITY_PER_CLINICIAN = 0.20
EMERGENCY_DURATION_MINUTES = (25, 55)

RADIOLOGY_VISIT_PROBABILITY = 0.35
N_RADIOLOGY_OUTAGES = 2
RADIOLOGY_OUTAGE_MINUTES = (20, 70)

VISIT_DURATION_MINUTES = 12
GAP_MINUTES = 3


@dataclass(frozen=True)
class Row:
    timestamp: datetime
    subject: str
    activity: str
    location: str
    source: str
    confidence: float
    event_category: str
    disruption_type: str
    delay_minutes: float
    note: str


def _special_locations() -> dict[str, Location]:
    return {
        "OT/1": Location(LocationId("OT/1"), LocationKind.BAY, Point(-40.0, 0.0, 0.0)),
        "OT/2": Location(LocationId("OT/2"), LocationKind.BAY, Point(-40.0, 0.0, 6.0)),
        "RAD/1": Location(LocationId("RAD/1"), LocationKind.BAY, Point(-60.0, 0.0, 3.0)),
        "ED/1": Location(LocationId("ED/1"), LocationKind.BAY, Point(-80.0, 0.0, 3.0)),
    }


def _transit_pings(
    rng: Random,
    subject: str,
    start_time: datetime,
    origin: Location,
    destination: Location,
) -> tuple[list[Row], datetime]:
    """Interpolated RFID heartbeat events while walking origin -> destination."""
    distance_m = origin.point.euclidean_distance_to(destination.point)
    travel_s = distance_m / WALKING_SPEED_MPS
    n_pings = max(0, int(travel_s // PING_INTERVAL_S))

    rows: list[Row] = []
    for i in range(1, n_pings + 1):
        frac = (i * PING_INTERVAL_S) / travel_s if travel_s > 0 else 1.0
        frac = min(frac, 1.0)
        t = start_time + timedelta(seconds=i * PING_INTERVAL_S)
        rows.append(
            Row(
                timestamp=t,
                subject=subject,
                activity="rfid_ping",
                location=f"transit:{origin.id.value}->{destination.id.value}@{frac:.2f}",
                source=SOURCE,
                confidence=round(rng.uniform(0.85, 1.0), 3),
                event_category="transit",
                disruption_type="none",
                delay_minutes=0.0,
                note=f"{distance_m:.1f}m walk in progress",
            )
        )
    arrival_time = start_time + timedelta(seconds=travel_s)
    return rows, arrival_time


def generate() -> list[Row]:
    rng = Random(SEED)
    wards = _build_wards()
    all_beds = [bed for ward in wards for bed in ward.beds]
    specials = _special_locations()

    radiology_outages = sorted(
        (SHIFT_START + timedelta(minutes=rng.uniform(0, SHIFT_HOURS * 60 - 60)),)
        for _ in range(N_RADIOLOGY_OUTAGES)
    )
    outage_windows: list[tuple[datetime, datetime]] = []
    for (start,) in radiology_outages:
        dur = timedelta(minutes=rng.uniform(*RADIOLOGY_OUTAGE_MINUTES))
        outage_windows.append((start, start + dur))

    rows: list[Row] = []

    for i in range(N_CLINICIANS):
        subject = f"CLIN{i:03d}"
        home_ward = wards[i % len(wards)]
        current_loc = home_ward.nursing_station
        current_time = SHIFT_START
        cumulative_delay = timedelta(0)

        rows.append(
            Row(
                current_time,
                subject,
                "shift_start",
                current_loc.id.value,
                SOURCE,
                1.0,
                "shift_marker",
                "none",
                0.0,
                f"role home ward {home_ward.ward_id}",
            )
        )

        # Optional theatre case at the start of the shift.
        if rng.random() < OT_PROBABILITY:
            ot = specials[rng.choice(["OT/1", "OT/2"])]
            ping_rows, current_time = _transit_pings(
                rng, subject, current_time, current_loc, ot
            )
            rows.extend(ping_rows)
            current_loc = ot

            rows.append(
                Row(
                    current_time,
                    subject,
                    "ot_case_arrive",
                    ot.id.value,
                    SOURCE,
                    1.0,
                    "ot_case",
                    "none",
                    0.0,
                    "scheduled theatre case",
                )
            )
            nominal = timedelta(minutes=rng.uniform(*OT_NOMINAL_MINUTES))
            overrun = timedelta(0)
            disruption = "none"
            if rng.random() < OT_PROLONG_PROBABILITY:
                overrun = timedelta(minutes=rng.uniform(*OT_OVERRUN_MINUTES))
                disruption = "ot_prolonged"
                cumulative_delay += overrun
                rows.append(
                    Row(
                        current_time + nominal,
                        subject,
                        "ot_prolonged",
                        ot.id.value,
                        SOURCE,
                        1.0,
                        "ot_case",
                        "ot_prolonged",
                        overrun.total_seconds() / 60,
                        "case running over scheduled time",
                    )
                )
            current_time += nominal + overrun
            rows.append(
                Row(
                    current_time,
                    subject,
                    "ot_case_depart",
                    ot.id.value,
                    SOURCE,
                    1.0,
                    "ot_case",
                    disruption,
                    overrun.total_seconds() / 60,
                    "theatre case complete",
                )
            )

        # Round of patient visits, sickest/acuity ordering approximated by shuffle.
        patient_beds = rng.sample(all_beds, min(STOPS_PER_CLINICIAN, len(all_beds)))

        for bed in patient_beds:
            patient_id = f"PAT{all_beds.index(bed):04d}"

            # Stochastic emergency diversion before this stop.
            if rng.random() < EMERGENCY_PROBABILITY_PER_CLINICIAN / STOPS_PER_CLINICIAN:
                ed = specials["ED/1"]
                ping_rows, current_time = _transit_pings(
                    rng, subject, current_time, current_loc, ed
                )
                rows.extend(ping_rows)
                current_loc = ed
                rows.append(
                    Row(
                        current_time,
                        subject,
                        "emergency_admission_arrive",
                        ed.id.value,
                        SOURCE,
                        1.0,
                        "emergency",
                        "emergency_admission",
                        0.0,
                        "diverted to emergency admission",
                    )
                )
                duration = timedelta(minutes=rng.uniform(*EMERGENCY_DURATION_MINUTES))
                cumulative_delay += duration
                current_time += duration
                rows.append(
                    Row(
                        current_time,
                        subject,
                        "emergency_admission_depart",
                        ed.id.value,
                        SOURCE,
                        1.0,
                        "emergency",
                        "emergency_admission",
                        duration.total_seconds() / 60,
                        "returning to round, schedule delayed",
                    )
                )

            # Optional radiology stop before the patient visit.
            needs_radiology = rng.random() < RADIOLOGY_VISIT_PROBABILITY
            if needs_radiology:
                rad = specials["RAD/1"]
                ping_rows, current_time = _transit_pings(
                    rng, subject, current_time, current_loc, rad
                )
                rows.extend(ping_rows)
                current_loc = rad
                rows.append(
                    Row(
                        current_time,
                        subject,
                        "radiology_arrive",
                        rad.id.value,
                        SOURCE,
                        1.0,
                        "radiology_wait",
                        "none",
                        0.0,
                        f"escorting {patient_id} for imaging",
                    )
                )
                active_outage = next(
                    ((s, e) for s, e in outage_windows if s <= current_time <= e), None
                )
                if active_outage is not None:
                    wait = active_outage[1] - current_time
                    wait_minutes = max(wait.total_seconds() / 60, 5.0)
                    cumulative_delay += timedelta(minutes=wait_minutes)
                    rows.append(
                        Row(
                            current_time,
                            subject,
                            "radiology_outage_wait",
                            rad.id.value,
                            SOURCE,
                            1.0,
                            "radiology_wait",
                            "radiology_outage",
                            wait_minutes,
                            "radiology equipment down, waiting",
                        )
                    )
                    current_time += timedelta(minutes=wait_minutes)
                rows.append(
                    Row(
                        current_time,
                        subject,
                        "radiology_depart",
                        rad.id.value,
                        SOURCE,
                        1.0,
                        "radiology_wait",
                        "none",
                        0.0,
                        "imaging complete",
                    )
                )

            ping_rows, current_time = _transit_pings(
                rng, subject, current_time, current_loc, bed
            )
            rows.extend(ping_rows)
            current_loc = bed

            confidence = 1.0
            if rng.random() < 0.06:
                confidence = round(rng.uniform(0.4, 0.79), 3)

            rows.append(
                Row(
                    current_time,
                    subject,
                    "arrive",
                    bed.id.value,
                    SOURCE,
                    confidence,
                    "routine_visit",
                    "none",
                    0.0,
                    f"visiting {patient_id}",
                )
            )
            visit_duration = timedelta(minutes=VISIT_DURATION_MINUTES)
            current_time += visit_duration
            rows.append(
                Row(
                    current_time,
                    subject,
                    "depart",
                    bed.id.value,
                    SOURCE,
                    1.0,
                    "routine_visit",
                    "none",
                    0.0,
                    f"visit {patient_id} complete",
                )
            )
            current_time += timedelta(minutes=GAP_MINUTES)

    return rows


def main() -> None:
    rows = generate()
    rows.sort(key=lambda r: (r.subject, r.timestamp))

    out_dir = Path(__file__).resolve().parent.parent / "demo_data"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "rfid_realtime_demo.csv"

    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp",
                "subject",
                "activity",
                "location",
                "source",
                "confidence",
                "event_category",
                "disruption_type",
                "delay_minutes",
                "note",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.timestamp.isoformat(),
                    r.subject,
                    r.activity,
                    r.location,
                    r.source,
                    r.confidence,
                    r.event_category,
                    r.disruption_type,
                    round(r.delay_minutes, 1),
                    r.note,
                ]
            )

    disruptions = sum(1 for r in rows if r.disruption_type != "none")
    print(f"rows: {len(rows)}  disruption events: {disruptions}")
    print(out_path)


if __name__ == "__main__":
    main()
