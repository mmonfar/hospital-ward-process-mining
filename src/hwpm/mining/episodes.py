"""Bedside-episode derivation and round reconstruction. SPEC-002, N05-mining.

No pm4py here at all -- dwell-time segmentation is plain Python over
`hwpm.domain` types, per ADR-0008 (only `_pm4py_adapter.py` may import pm4py)
and per SPEC-002's own framing: "our contribution is the ward-motion layer."

`derive_episodes` takes exactly SPEC-002's two-argument signature
(`traj, params`). It cannot know which patient occupies a bed or which
specialties a clinician carries -- neither is knowable from one clinician's
raw event trajectory alone (bed occupancy is a separate feed even in
`hwpm.ingest.synthetic`, which never emits a patient-location event). Those
joins are explicit, separate, composable steps (`attach_patients`,
`attach_clinician_specialties`) rather than hidden lookups inside derivation,
so "not yet joined" is never silently indistinguishable from "joined and
empty" (01-DOMAIN-MODEL.md rule 4).

Confidence handling: events below `params.min_confidence` (default 0.8, the
location-mapper quarantine threshold, SPEC-001) are dropped before
segmentation, not just flagged. A visit whose *every* event is quarantined
therefore produces no episode at all -- under-reporting an unreliable visit
is preferred over fabricating a confident one out of untrustworthy evidence.
This is a real trade-off, not a hidden one: `EpisodeParams.min_confidence` is
the knob, and `test_mining.py` documents the case where it drops a whole visit.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime

from hwpm.domain import BedsideEpisode, ClinicianId, Event, LocationId, Round, Trajectory
from hwpm.mining.types import (
    DEFAULT_SENSITIVITY_DWELL_VALUES,
    BedOccupancy,
    ClinicianSpecialties,
    EpisodeParams,
    EventLog,
    LogEvent,
    ReportHeader,
    SensitivityPoint,
    SensitivityReport,
)

# ---------------------------------------------------------------------------
# Episode derivation
# ---------------------------------------------------------------------------


def _flush_run(
    clinician: ClinicianId, run: list[Event], params: EpisodeParams
) -> BedsideEpisode | None:
    """A run of events at one location becomes an episode only if it dwelled
    at least `min_dwell_s` (SPEC-002: "below this: passing through, not a
    visit"). A single-event run has zero duration and is always dropped."""
    if not run:
        return None
    start, end = run[0].timestamp, run[-1].timestamp
    if (end - start).total_seconds() < params.min_dwell_s:
        return None
    return BedsideEpisode(
        clinician=clinician,
        bed=run[0].location,
        start=start,
        end=end,
        confidence=min(event.confidence for event in run),
        source_events=tuple(run),
    )


def derive_episodes(traj: Trajectory, params: EpisodeParams) -> list[BedsideEpisode]:
    """Dwell-time segmentation of one clinician's trajectory into
    `BedsideEpisode`s (SPEC-002 interface).

    Two passes, deliberately not one, and pass 1 needed a second correction
    during this node's own testing (both failures reproduced and fixed
    before this docstring was written; see the two notes below).

    `hwpm.ingest.synthetic` (this module's test oracle) reports a visit as
    exactly two boundary readings -- an "arrive" and a "depart" a full
    visit-duration apart, not a stream of periodic same-location pings.
    Splitting a run the moment the gap between *any* two consecutive
    same-location readings exceeds `max_gap_s` is wrong for that shape of
    data: it treats every visit's own arrive-to-depart span (minutes) as a
    "gap" against a noise threshold in the tens of seconds, and shreds every
    visit into two zero-duration, sub-`min_dwell_s` fragments that
    `_flush_run` then drops. (First defect caught during this node's own
    testing: that naive one-pass version recovered 0% of the synthetic
    generator's visits, not the required >=95%.)

    Pass 1 -- `blocks`: a block is the maximal run of consecutive
    (time-sorted) events at one location, split on either signal:

    - a location change (unambiguous: they moved), or
    - the *reopening* signal: the event's activity matches the block's own
      first event's activity (i.e. the kind of reading that opened this
      block is recurring -- for "arrive"/"depart" data that means another
      "arrive"), **and** the gap since the block's last event exceeds
      `max_gap_s`.

    The reopening check is deliberately not hardcoded to the literal strings
    "arrive"/"depart": for boundary-style data (open/close reading pairs) it
    correctly leaves a single pair's own internal span untouched (the close
    reading never matches the open reading's activity, so it never
    re-triggers the check) while still splitting a genuine same-bed re-visit
    (a second "opening" reading, far enough later). For homogeneous-activity
    periodic-ping data (every reading labelled the same), it reduces exactly
    to "split on any gap over `max_gap_s`" -- the originally-intended
    behaviour for that shape of data, since every reading trivially matches
    the block's first activity. (Second defect caught during this node's own
    testing, found by the coordinator: without the reopening check, four
    events at one bed -- arrive, depart, [2 hours], arrive, depart -- came
    back as a single 2-hour episode instead of two 5-minute ones.)

    Pass 2 -- `open_runs`: SPEC-002's gap tolerance ("gaps <90s within an
    episode are sensor noise") also applies *between* blocks -- a block that
    reappears at a location it was at before, within `max_gap_s` of when it
    was last seen there, continues that same visit (a brief, separately
    logged detour to a different location and straight back) rather than
    starting a new one.

    Confidence-quarantined events (below `params.min_confidence`) are dropped
    before either pass -- see the module docstring.
    """
    clinician = traj.subject
    if not isinstance(clinician, ClinicianId):
        raise TypeError(
            "derive_episodes needs a clinician's trajectory (01-DOMAIN-MODEL.md, "
            "'Round': one clinician's ordered sweep); got a trajectory for "
            f"{type(clinician).__name__}"
        )

    usable = sorted(
        (event for event in traj.events if event.confidence >= params.min_confidence),
        key=lambda event: event.timestamp,
    )

    blocks: list[list[Event]] = []
    for event in usable:
        if blocks:
            current = blocks[-1]
            same_location = event.location == current[-1].location
            reopening = event.activity == current[0].activity
            gap_s = (event.timestamp - current[-1].timestamp).total_seconds()
            if same_location and not (reopening and gap_s > params.max_gap_s):
                current.append(event)
                continue
        blocks.append([event])

    open_runs: dict[LocationId, list[Event]] = {}
    episodes: list[BedsideEpisode] = []
    for block in blocks:
        location = block[0].location
        existing = open_runs.get(location)
        if (
            existing is not None
            and (block[0].timestamp - existing[-1].timestamp).total_seconds()
            <= params.max_gap_s
        ):
            existing.extend(block)
            continue
        if existing is not None:
            episode = _flush_run(clinician, existing, params)
            if episode is not None:
                episodes.append(episode)
        open_runs[location] = list(block)

    for run in open_runs.values():
        episode = _flush_run(clinician, run, params)
        if episode is not None:
            episodes.append(episode)

    episodes.sort(key=lambda e: e.start)
    return episodes


# ---------------------------------------------------------------------------
# Joins: patient occupancy, clinician specialty
# ---------------------------------------------------------------------------


def attach_patients(
    episodes: Sequence[BedsideEpisode], occupancy: BedOccupancy
) -> list[BedsideEpisode]:
    """Fill in `BedsideEpisode.patient` from a bed-occupancy mapping. Beds not
    in `occupancy` keep whatever `patient` they already had (`None` if this is
    the first join)."""
    return [replace(ep, patient=occupancy.get(ep.bed, ep.patient)) for ep in episodes]


def attach_clinician_specialties(
    episodes: Sequence[BedsideEpisode], clinicians: ClinicianSpecialties
) -> list[BedsideEpisode]:
    """Fill in `BedsideEpisode.clinician_specialties` from a clinician roster.
    Clinicians not in `clinicians` keep whatever they already had."""
    return [
        replace(
            ep,
            clinician_specialties=clinicians.get(ep.clinician, ep.clinician_specialties),
        )
        for ep in episodes
    ]


# ---------------------------------------------------------------------------
# Round reconstruction
# ---------------------------------------------------------------------------


def reconstruct_rounds(episodes: Sequence[BedsideEpisode], day: date) -> list[Round]:
    """Group `day`'s episodes by clinician into ordered `Round`s (SPEC-002
    interface). Episodes on other days are excluded, not raised on -- a caller
    deriving episodes across a whole log and reconstructing one day at a time
    is the expected usage."""
    by_clinician: dict[ClinicianId, list[BedsideEpisode]] = {}
    for episode in episodes:
        if episode.start.date() != day:
            continue
        by_clinician.setdefault(episode.clinician, []).append(episode)

    rounds = [
        Round(
            clinician=clinician,
            day=day,
            episodes=tuple(sorted(clinician_episodes, key=lambda e: e.start)),
        )
        for clinician, clinician_episodes in by_clinician.items()
    ]
    rounds.sort(key=lambda r: r.clinician.value)
    return rounds


# ---------------------------------------------------------------------------
# Log building (feeds hwpm.mining.discovery, not pm4py directly)
# ---------------------------------------------------------------------------


def build_log(trajectories: Iterable[Trajectory]) -> EventLog:
    """Flatten raw event trajectories into our own `EventLog` shape, one case
    per subject. Deliberately upstream of episode derivation: process
    discovery over `Event.activity` ("arrive"/"depart"...) asks "what does the
    round process look like", a different question from "how long did each
    visit last", which is what `derive_episodes` answers."""
    events = tuple(
        LogEvent(
            case_id=traj.subject.value,
            activity=event.activity,
            timestamp=event.timestamp,
        )
        for traj in trajectories
        for event in traj.events
    )
    return EventLog(events=events)


# ---------------------------------------------------------------------------
# Sensitivity analysis (acceptance criterion 2)
# ---------------------------------------------------------------------------


def sensitivity_analysis(
    trajectories: Iterable[Trajectory],
    base_params: EpisodeParams | None = None,
    dwell_values: tuple[int, ...] = DEFAULT_SENSITIVITY_DWELL_VALUES,
    known_visit_count: int | None = None,
) -> SensitivityReport:
    """Episode counts (and recall, when `known_visit_count` -- a ground-truth
    visit count -- is supplied) across the mandated `min_dwell_s` sweep
    (SPEC-002 acceptance criterion 2). Produced unconditionally: nothing about
    calling this is optional in a real report, only the test happens to call
    it directly.

    `base_params` defaults to `None` rather than `EpisodeParams()` directly:
    ruff's B008 flags a function call as a default value on principle (most
    such calls build a fresh mutable object shared across every call site
    that doesn't override it). `EpisodeParams` is frozen, so the shared-object
    hazard the rule is guarding against does not actually apply here -- but
    satisfying the rule the straightforward way costs nothing and keeps this
    module clean under gate 2 (`docs/06-QA-AND-DEADCODE.md`).
    """
    if base_params is None:
        base_params = EpisodeParams()
    trajs = list(trajectories)
    points: list[SensitivityPoint] = []
    for dwell in dwell_values:
        params = replace(base_params, min_dwell_s=dwell)
        count = sum(len(derive_episodes(traj, params)) for traj in trajs)
        recall = count / known_visit_count if known_visit_count else None
        points.append(
            SensitivityPoint(min_dwell_s=dwell, episode_count=count, recall=recall)
        )
    header = ReportHeader(
        parameters={
            "max_gap_s": base_params.max_gap_s,
            "min_confidence": base_params.min_confidence,
            "dwell_values": dwell_values,
            "known_visit_count": known_visit_count,
        },
        generated_at=datetime.now(UTC),
    )
    return SensitivityReport(points=tuple(points), header=header)


__all__ = [
    "attach_clinician_specialties",
    "attach_patients",
    "build_log",
    "derive_episodes",
    "reconstruct_rounds",
    "sensitivity_analysis",
]
