"""MDT-moment and opportunistic-MDT detection. SPEC-002, N05-mining.

No pm4py here either -- both detectors are plain analytics over
`BedsideEpisode`s, `Trajectory`s and `hwpm.domain.travel.TravelGraph`
(SPEC-003's routed graph, already built by N06). ADR-0008 only names pm4py's
adapter as the boundary; nothing in this module goes near it.

Both functions take `episodes` (not raw trajectories, not a separate
occupancy/roster argument) as their source of "who is who": a `BedsideEpisode`
that has been through `hwpm.mining.episodes.attach_patients` and
`attach_clinician_specialties` already carries which patient occupied the bed
and which specialties the visiting clinician held. This keeps
`detect_mdt_moments` and `detect_opportunistic` to SPEC-002's literal
signatures -- no extra "and also pass me a roster" parameter -- because the
one collection they are already given carries everything both functions need.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from hwpm.domain import (
    BedsideEpisode,
    ClinicianId,
    LocationId,
    MDTMoment,
    PatientId,
    Specialty,
    Trajectory,
)
from hwpm.domain.travel import TravelGraph
from hwpm.mining.types import MissedMDTOpportunity

# ---------------------------------------------------------------------------
# MDT moments
# ---------------------------------------------------------------------------


def _emit_moment(
    moments: list[MDTMoment],
    bed: LocationId,
    patient: PatientId,
    cluster: list[BedsideEpisode],
    required: Mapping[PatientId, frozenset[Specialty]],
) -> None:
    if not cluster:
        return
    present: frozenset[Specialty] = frozenset()
    for episode in cluster:
        present |= episode.clinician_specialties
    satisfied = present & required.get(patient, frozenset())
    if len(satisfied) < 2:
        return
    moments.append(
        MDTMoment(
            bed=bed,
            patient=patient,
            start=min(episode.start for episode in cluster),
            end=max(episode.end for episode in cluster),
            present_specialties=present,
            satisfied_specialties=satisfied,
        )
    )


def detect_mdt_moments(
    episodes: Sequence[BedsideEpisode],
    required: Mapping[PatientId, frozenset[Specialty]],
    window_s: int = 300,
) -> list[MDTMoment]:
    """Every genuine co-presence the episodes contain (SPEC-002 interface).

    Episodes are grouped by `(bed, patient)`, sorted by start, and clustered:
    a gap of more than `window_s` between one episode's end and the next
    episode's start at the same bedside starts a new cluster (SPEC-002,
    "Co-presence within 300s = joint review"). Each cluster becomes an
    `MDTMoment` only if the union of its episodes' `clinician_specialties`
    satisfies >=2 of the patient's `required` specialties -- `MDTMoment`'s own
    constructor enforces this too, so this is a pre-check to avoid raising,
    not the only place the rule is applied.

    Episodes with `patient is None` (never joined via `attach_patients`) are
    silently excluded -- there is nothing to check `required` against.
    """
    grouped: dict[tuple[LocationId, PatientId], list[BedsideEpisode]] = {}
    for episode in episodes:
        if episode.patient is None:
            continue
        grouped.setdefault((episode.bed, episode.patient), []).append(episode)

    moments: list[MDTMoment] = []
    for (bed, patient), group in grouped.items():
        group.sort(key=lambda e: e.start)
        cluster: list[BedsideEpisode] = []
        for episode in group:
            if cluster and (episode.start - cluster[-1].end).total_seconds() > window_s:
                _emit_moment(moments, bed, patient, cluster, required)
                cluster = []
            cluster.append(episode)
        _emit_moment(moments, bed, patient, cluster, required)

    moments.sort(key=lambda m: (m.bed.value, m.start))
    return moments


# ---------------------------------------------------------------------------
# Opportunistic MDT detection
# ---------------------------------------------------------------------------


def _clinician_specialty_index(
    episodes: Iterable[BedsideEpisode],
) -> dict[ClinicianId, frozenset[Specialty]]:
    out: dict[ClinicianId, frozenset[Specialty]] = {}
    for episode in episodes:
        if not episode.clinician_specialties:
            continue
        out[episode.clinician] = (
            out.get(episode.clinician, frozenset()) | episode.clinician_specialties
        )
    return out


def _interval_gap_seconds(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> float:
    """0.0 if the two intervals overlap; otherwise the gap between them."""
    if a_end < b_start:
        return (b_start - a_end).total_seconds()
    if b_end < a_start:
        return (a_start - b_end).total_seconds()
    return 0.0


def detect_opportunistic(
    trajectories: Iterable[Trajectory],
    episodes: Sequence[BedsideEpisode],
    required: Mapping[PatientId, frozenset[Specialty]],
    proximity_m: float = 15.0,
    window_s: int = 300,
) -> list[MissedMDTOpportunity]:
    """The "surgeon who walked past" case (SPEC-002 interface).

    For every `Transition` in every clinician's trajectory, the shortest
    routed path (`TravelGraph.path`, SPEC-003 -- never Euclidean, per the
    modelling-assumptions table) between the transition's endpoints is
    checked for proximity to every bed `episodes` says holds a patient who
    needs one of that clinician's specialties, within `proximity_m` (routed
    metres) and `window_s` of a confirmed presence there. A candidate is
    dropped if the (clinician, patient) pair already has a real episode
    anywhere in the log -- they already saw them.

    Criterion 5: a clinician with **no** `BedsideEpisode` anywhere in
    `episodes` never yields a candidate, however close their trajectory
    passes to a needed bed. Their trajectory shows continuous transit --
    nothing establishes they were ever an available reviewer, only that they
    moved through (SPEC-002's own example: "a registrar walking to theatre is
    not an available reviewer").

    Every candidate carries a `confidence` (proximity and timing scaled onto
    [0, 1], not a measured probability -- like N04's specialty-determination
    confidence, this needs clinician-review calibration before it is
    reported as anything stronger than a ranking) and a human-readable
    `reason`, per SPEC-002: "reported as candidate opportunities... never as
    'missed reviews'."
    """
    travel = TravelGraph()
    clinicians_with_episodes = {episode.clinician for episode in episodes}
    specialty_index = _clinician_specialty_index(episodes)
    seen_pairs = {
        (episode.clinician, episode.patient)
        for episode in episodes
        if episode.patient is not None
    }
    known_beds = {
        (episode.bed, episode.patient)
        for episode in episodes
        if episode.patient is not None
    }

    candidates: list[MissedMDTOpportunity] = []
    for traj in trajectories:
        clinician = traj.subject
        if not isinstance(clinician, ClinicianId):
            continue
        if clinician not in clinicians_with_episodes:
            continue
        specialties = specialty_index.get(clinician, frozenset())
        if not specialties:
            continue

        for transition in traj.transitions():
            try:
                path = travel.path(transition.origin, transition.destination)
            except (KeyError, ValueError):
                continue

            for bed, patient in known_beds:
                if (clinician, patient) in seen_pairs:
                    continue
                needed = required.get(patient, frozenset()) & specialties
                if not needed:
                    continue

                nearest_metres = min(travel.cost(node, bed).metres for node in path)
                if nearest_metres > proximity_m:
                    continue

                bedside_episodes = [
                    ep for ep in episodes if ep.bed == bed and ep.patient == patient
                ]
                gap_s = min(
                    (
                        _interval_gap_seconds(
                            transition.departed_at,
                            transition.arrived_at,
                            ep.start,
                            ep.end,
                        )
                        for ep in bedside_episodes
                    ),
                    default=float(window_s) + 1.0,
                )
                if gap_s > window_s:
                    continue

                proximity_score = (
                    max(0.0, 1.0 - nearest_metres / proximity_m)
                    if proximity_m > 0
                    else 1.0
                )
                timing_score = max(0.0, 1.0 - gap_s / window_s) if window_s > 0 else 1.0
                confidence = round(proximity_score * timing_score, 4)

                for specialty in sorted(needed, key=lambda s: s.value):
                    candidates.append(
                        MissedMDTOpportunity(
                            clinician=clinician,
                            patient=patient,
                            specialty=specialty,
                            bed=bed,
                            at=transition.arrived_at,
                            proximity_m=round(nearest_metres, 2),
                            confidence=confidence,
                            reason=(
                                f"{clinician.value} (specialty {specialty.value}) passed "
                                f"within {nearest_metres:.1f}m of {bed.value} "
                                f"(patient {patient.value} requires {specialty.value}) "
                                f"between {transition.departed_at:%H:%M} and "
                                f"{transition.arrived_at:%H:%M}, {gap_s:.0f}s from a "
                                "confirmed presence there"
                            ),
                        )
                    )

    candidates.sort(key=lambda c: (c.clinician.value, c.at))
    return candidates


__all__ = ["detect_mdt_moments", "detect_opportunistic"]
