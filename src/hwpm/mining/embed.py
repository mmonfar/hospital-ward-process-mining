"""Ward-day representation, clustering and neighbours. SPEC-007 Part B, N20.

This module is the *other* system in SPEC-007. It shares an embedding runtime
with `hwpm.retrieve` (Part A) and nothing else: no storage, no index file, no
data. Part A indexes this repository's own text and touches no patient data;
everything here is derived from patient data and touches nothing else
(ADR-0007 decision 1). A session that finds itself passing a `Chunk` into
`cluster_ward_days`, or a `WardDayVector` into `hwpm.retrieve.search`, has made
the mistake SPEC-007's opening section predicted.

What is built, and what is not
------------------------------
`WardDayVector.features` is a fixed list of **named, unit-carrying numbers**
(`FEATURE_NAMES`) derived from observed `BedsideEpisode`s. It is always
populated. `WardDayVector.embedding` is `None` unless the Part B gate
(SPEC-007 criterion 16) has fired and a caller has attached one.

The measured outcome of that gate for this node is recorded in
`docs/AUDIT-LOG.md` and in `orchestration/graph.yaml`. Read it there rather
than inferring it from the presence of `attach_embeddings` in this file:
the learned path exists here so the comparison is *runnable*, which is not the
same as it being the recommended representation.

The constraint that outranks the features
-----------------------------------------
**An embedding may never be the basis of a reported governance figure without
an interpretable derivation published alongside** (ADR-0007 decision 5,
SPEC-007 criteria 14-15). A cosine distance in 384 dimensions has no chain
from figure to evidence, and ADR-0006 makes that chain the source of the MDT
coverage figure's authority.

Enforced structurally rather than by review:

* an import-linter contract (`pyproject.toml`, "Governance figures do not
  depend on embeddings") forbids `hwpm.analytics.coverage`,
  `hwpm.analytics.motion`, `hwpm.analytics.bounds` and
  `hwpm.analytics.suppression` from importing this module, directly or
  transitively;
* `hwpm.artefact.envelope` refuses to serialise a payload holding an
  embedding-derived field;
* `features` is not optional and `embedding` is, so any consumer that cannot
  work from `features` alone is a consumer that must not feed a governance
  figure.

Layering note: this module deliberately does **not** import
`hwpm.analytics.suppression`, because `hwpm.analytics` is a layer *above*
`hwpm.mining` and importing it would break gate 6. Cluster output therefore
carries its cohort (`patients`, `clinicians`) and the ADR-0005 aggregation
floor is applied by `hwpm.analytics.cohorts`, which is allowed to see both.
Nothing in this module publishes anything.

Determinism: every draw comes from the `rng` argument (CLAUDE.md rule 3). Two
calls to `cluster_ward_days(..., rng=Random(11))` compare equal.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from itertools import pairwise
from random import Random

from hwpm.domain import (
    BedsideEpisode,
    ClinicianId,
    LocationId,
    PatientId,
    Round,
    Specialty,
)
from hwpm.domain.travel import TravelGraph
from hwpm.mining.mdt import detect_mdt_moments
from hwpm.mining.types import EpisodeParams

#: ADR-0006 rule 1's habit, applied to a representation rather than a figure.
#: A change to `FEATURE_NAMES`, to any feature's definition, or to the
#: standardisation in `cluster_ward_days` increments this. Two vectors carrying
#: different method versions are not comparable and nothing here pretends
#: otherwise.
METHOD_VERSION = "wardday-features/1.0.0"

#: SPEC-007 criterion 17. Below this, clustering finds structure in noise:
#: ADR-0007 puts it plainly -- "clustering 30 days produces clusters whatever
#: the method, and their apparent structure is noise".
MIN_WARD_DAYS = 200

#: The interpretable feature vector, in a fixed order. Every name carries its
#: unit in the name itself (`_s` seconds, `_m` metres, bare names are counts),
#: because a feature vector whose units live in a docstring is a feature vector
#: whose units get lost.
FEATURE_NAMES: tuple[str, ...] = (
    "n_episodes",
    "n_clinicians",
    "n_patients",
    "median_dwell_s",
    "iqr_dwell_s",
    "n_specialties",
    "motion_m",
    "motion_m_per_episode",
    "floor_transitions",
    "bed_revisits",
    "mdt_moments",
    "span_s",
    "median_gap_s",
    "protected_window_episodes",
)

#: Vertical separation (metres) above which two beds are treated as being on
#: different floors. The reference geometry spaces floors 4.2 m apart
#: (`hwpm.ingest.synthetic._FLOOR_Y_SPACING`); anything under a metre is
#: within-floor coordinate noise. Read off `TravelGraph.position` rather than
#: parsed out of a bed id, so a real floor plan changes one number and not this
#: module.
FLOOR_SEPARATION_M = 1.0


class InsufficientWardDaysError(RuntimeError):
    """Fewer than `MIN_WARD_DAYS` ward-days were offered for clustering.

    A refusal, not a failure: SPEC-007 criterion 17 requires clustering to
    "refuse to run on fewer than 200 ward-days and say why", and returning a
    k-means result on 30 days would be returning noise with a confident shape.
    """


class EmbeddingSpaceError(ValueError):
    """A learned-embedding operation was asked for on vectors that carry none,
    or vectors whose embeddings disagree in dimension."""


# ---------------------------------------------------------------------------
# Parameters and the vector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WardDayParams:
    """Every field a modelling assumption, none of them a fact. Same house
    pattern as `EpisodeParams` and `MotionParams`.

    `protected_window` is a *local clock* window, not a slot set: this module
    reads observed episodes, which carry wall-clock times, whereas
    `hwpm.domain.schedule.NursingProtectedWindow` constrains a planned
    schedule on an integer slot grid. They describe the same clinical
    intention -- protected mealtimes -- in the two different coordinate systems
    the observed and planned halves of this project use, and conflating them
    would be the planned/observed mix-up 01-DOMAIN-MODEL.md forbids.
    """

    #: SPEC-002's co-presence window, restated by reference so the ward-day
    #: `mdt_moments` feature and the N18 governance figure count the same thing.
    mdt_window_s: int = 300
    #: Protected mealtime, local clock. Half-open [start, end).
    protected_window: tuple[time, time] = (time(12, 0), time(13, 0))
    #: The episode-derivation parameters the input episodes were produced
    #: under. Stamped into `provenance`, never used to re-derive anything --
    #: SPEC-007 "Failure modes": if `EpisodeParams` change, every ward-day
    #: vector changes, and that is a method-version change (ADR-0006 section 1).
    episode_params: EpisodeParams = field(default_factory=EpisodeParams)

    def __post_init__(self) -> None:
        if self.mdt_window_s < 0:
            raise ValueError(f"mdt_window_s must be >= 0, got {self.mdt_window_s!r}")
        start, end = self.protected_window
        if start >= end:
            raise ValueError(
                f"protected_window must be a non-empty [start, end), got "
                f"{self.protected_window!r}"
            )


#: A ward-day's identity: the calendar day and the ward id (`"1A"`), the same
#: key `hwpm.analytics.coverage` groups its cells by.
WardDayKey = tuple[date, str]


@dataclass(frozen=True)
class WardDayVector:
    """One ward-day as a fixed-length, named vector.

    `features` is never empty. `embedding` may be `None`, and is `None` unless
    a caller has explicitly attached one after the SPEC-007 criterion-16 gate.

    `patients` and `clinicians` are carried, not counted, so that
    `hwpm.analytics.cohorts` can apply the ADR-0005 aggregation floor to a
    *cluster* -- a union across ward-days, which cannot be recovered from
    per-ward-day counts. They make this object derived personal data in the
    plain sense, which it already was: ADR-0007 decision 2 is explicit that a
    ward-day vector is not anonymisation.
    """

    day: date
    ward: str
    features: Mapping[str, float]
    patients: frozenset[PatientId]
    clinicians: frozenset[ClinicianId]
    provenance: Mapping[str, object]
    embedding: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        missing = [name for name in FEATURE_NAMES if name not in self.features]
        if missing:
            raise ValueError(
                f"features must carry every name in FEATURE_NAMES; missing {missing}. "
                "A partially populated interpretable vector is the one thing "
                "SPEC-007 criterion 13 forbids"
            )
        object.__setattr__(self, "features", dict(self.features))
        object.__setattr__(self, "provenance", dict(self.provenance))

    @property
    def key(self) -> WardDayKey:
        return (self.day, self.ward)

    def as_row(self) -> tuple[float, ...]:
        """The interpretable features in `FEATURE_NAMES` order."""
        return tuple(float(self.features[name]) for name in FEATURE_NAMES)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

_DEFAULT_GRAPH: TravelGraph | None = None


def _default_graph() -> TravelGraph:
    """The reference-geometry travel graph, built once.

    A module-level cache rather than a default argument: `TravelGraph()` runs
    Dijkstra over the whole building on construction, and rebuilding it per
    ward-day would dominate the cost of encoding a year of them.
    """
    global _DEFAULT_GRAPH
    if _DEFAULT_GRAPH is None:
        _DEFAULT_GRAPH = TravelGraph()
    return _DEFAULT_GRAPH


def ward_of(bed: LocationId) -> str:
    """Ward component of a bed id. Delegates to `hwpm.analytics.motion.ward_of`
    conceptually but cannot import it (that module is a layer above this one),
    so the parse is restated here and `test_embed.py` asserts the two agree."""
    return bed.value.split("/", 1)[0]


def _percentile(ordered: Sequence[float], pct: float) -> float:
    if not ordered:
        return 0.0
    k = (len(ordered) - 1) * (pct / 100.0)
    low = math.floor(k)
    high = math.ceil(k)
    if low == high:
        return ordered[int(k)]
    return ordered[low] + (ordered[high] - ordered[low]) * (k - low)


def _overlaps_window(episode: BedsideEpisode, window: tuple[time, time]) -> bool:
    start, end = window
    return episode.start.time() < end and episode.end.time() > start


def _routes(
    episodes: Sequence[BedsideEpisode], rounds: Sequence[Round]
) -> list[tuple[BedsideEpisode, ...]]:
    """Per-clinician ordered sweeps restricted to this ward-day's episodes.

    `rounds` is honoured where it is supplied, because reconstructing an order
    from a bag of episodes is exactly the job `reconstruct_rounds` already did
    and re-deriving it here would be a second definition of "a round". Rounds
    covering other wards or other days contribute only their episodes that are
    in `episodes`; a round with nothing in common contributes nothing.
    """
    wanted = set(episodes)
    out: list[tuple[BedsideEpisode, ...]] = []
    covered: set[BedsideEpisode] = set()
    for round_ in sorted(rounds, key=lambda r: (r.day, r.clinician.value)):
        kept = tuple(e for e in round_.episodes if e in wanted)
        if not kept:
            continue
        out.append(kept)
        covered.update(kept)
    leftover = [e for e in episodes if e not in covered]
    if leftover:
        by_clinician: dict[ClinicianId, list[BedsideEpisode]] = {}
        for episode in leftover:
            by_clinician.setdefault(episode.clinician, []).append(episode)
        for clinician in sorted(by_clinician, key=lambda c: c.value):
            out.append(tuple(sorted(by_clinician[clinician], key=lambda e: e.start)))
    return out


def encode_ward_day(
    episodes: Sequence[BedsideEpisode],
    rounds: Sequence[Round],
    *,
    required: Mapping[PatientId, frozenset[Specialty]] | None = None,
    graph: TravelGraph | None = None,
    params: WardDayParams | None = None,
) -> WardDayVector:
    """SPEC-007 Part B's `encode_ward_day`: one ward-day as named features.

    The two positional arguments are the spec's interface verbatim. The three
    keyword-only additions are the inputs the named features actually need and
    that neither episodes nor rounds carry: the `required` map (an N04 strategy
    output, a runtime choice per SPEC-001 -- deriving it here would re-bake the
    decision this project spent a node removing), the travel graph (the only
    sanctioned source of distance, SPEC-003), and the modelling parameters.

    `required=None` is allowed and is **not** silently treated as "nobody needs
    an MDT": `mdt_moments` is set to 0.0 and `provenance["required_joined"]` is
    `False`, so "not yet joined" stays distinguishable from "joined and empty"
    (01-DOMAIN-MODEL.md rule 4). A consumer comparing vectors across that
    boundary is comparing two different things, and the provenance is what lets
    it notice.

    Raises `ValueError` on an empty ward-day: a vector describing no observed
    episode is not a ward-day that was quiet, it is a ward-day that was not
    observed, and the two must not land in the same cluster.
    """
    if not episodes:
        raise ValueError(
            "cannot encode a ward-day with no episodes: an unobserved ward-day "
            "and a quiet one are different things and would cluster together"
        )
    if params is None:
        params = WardDayParams()
    if graph is None:
        graph = _default_graph()

    days = {e.start.date() for e in episodes}
    wards = {ward_of(e.bed) for e in episodes}
    if len(days) != 1 or len(wards) != 1:
        raise ValueError(
            "encode_ward_day takes exactly one ward-day's episodes; got days "
            f"{sorted(days)} and wards {sorted(wards)}. Group with "
            "`group_ward_days` first"
        )
    day = days.pop()
    ward = wards.pop()

    dwells = sorted(e.duration.total_seconds() for e in episodes)
    routes = _routes(episodes, rounds)

    motion_m = 0.0
    floor_transitions = 0
    revisits = 0
    gaps: list[float] = []
    for route in routes:
        seen: set[LocationId] = set()
        for episode in route:
            if episode.bed in seen:
                revisits += 1
            seen.add(episode.bed)
        for earlier, later in pairwise(route):
            motion_m += graph.cost(earlier.bed, later.bed).metres
            if (
                abs(graph.position(earlier.bed)[1] - graph.position(later.bed)[1])
                > FLOOR_SEPARATION_M
            ):
                floor_transitions += 1
            gaps.append(max(0.0, (later.start - earlier.end).total_seconds()))

    if required is None:
        n_moments = 0.0
    else:
        n_moments = float(
            len(detect_mdt_moments(episodes, required, params.mdt_window_s))
        )

    n_episodes = float(len(episodes))
    patients = frozenset(e.patient for e in episodes if e.patient is not None)
    clinicians = frozenset(e.clinician for e in episodes)
    specialties: set[Specialty] = set()
    for episode in episodes:
        specialties |= set(episode.clinician_specialties)

    features: dict[str, float] = {
        "n_episodes": n_episodes,
        "n_clinicians": float(len(clinicians)),
        "n_patients": float(len(patients)),
        "median_dwell_s": _percentile(dwells, 50.0),
        "iqr_dwell_s": _percentile(dwells, 75.0) - _percentile(dwells, 25.0),
        "n_specialties": float(len(specialties)),
        "motion_m": motion_m,
        "motion_m_per_episode": motion_m / n_episodes,
        "floor_transitions": float(floor_transitions),
        "bed_revisits": float(revisits),
        "mdt_moments": n_moments,
        "span_s": (
            max(e.end for e in episodes) - min(e.start for e in episodes)
        ).total_seconds(),
        "median_gap_s": _percentile(sorted(gaps), 50.0),
        "protected_window_episodes": float(
            sum(1 for e in episodes if _overlaps_window(e, params.protected_window))
        ),
    }

    provenance: dict[str, object] = {
        "method_version": METHOD_VERSION,
        "min_dwell_s": params.episode_params.min_dwell_s,
        "max_gap_s": params.episode_params.max_gap_s,
        "min_confidence": params.episode_params.min_confidence,
        "mdt_window_s": params.mdt_window_s,
        "protected_window": (
            params.protected_window[0].isoformat(),
            params.protected_window[1].isoformat(),
        ),
        "required_joined": required is not None,
        "n_routes": len(routes),
    }

    return WardDayVector(
        day=day,
        ward=ward,
        features=features,
        patients=patients,
        clinicians=clinicians,
        provenance=provenance,
    )


def group_ward_days(
    episodes: Iterable[BedsideEpisode],
) -> dict[WardDayKey, list[BedsideEpisode]]:
    """Split a flat episode stream into `(day, ward)` cells, each time-ordered.

    Same grouping `hwpm.analytics.coverage._units` performs, restated here for
    the same layering reason `ward_of` is (that module is a layer above), and
    checked against it in `test_embed.py`.
    """
    cells: dict[WardDayKey, list[BedsideEpisode]] = {}
    for episode in episodes:
        cells.setdefault((episode.start.date(), ward_of(episode.bed)), []).append(episode)
    for cell in cells.values():
        cell.sort(key=lambda e: (e.start, e.bed.value, e.clinician.value))
    return cells


def encode_ward_days(
    episodes: Iterable[BedsideEpisode],
    rounds: Sequence[Round],
    *,
    required: Mapping[PatientId, frozenset[Specialty]] | None = None,
    graph: TravelGraph | None = None,
    params: WardDayParams | None = None,
) -> list[WardDayVector]:
    """`encode_ward_day` over a whole episode stream, ordered by `(day, ward)`.

    The order is fixed rather than incidental because it is the order
    `cluster_ward_days` consumes, and a clustering whose result depended on
    dict insertion order would fail gate 8 for a reason nobody would find.
    """
    cells = group_ward_days(episodes)
    return [
        encode_ward_day(cells[key], rounds, required=required, graph=graph, params=params)
        for key in sorted(cells)
    ]


# ---------------------------------------------------------------------------
# Clustering
#
# k-means, with a stated k. SPEC-007 modelling assumption 7 is explicit that a
# k-selection heuristic is not wanted: "the number of ward-day types is a
# clinical question; silhouette-optimal k is an arithmetic answer to it and
# would be mistaken for a finding".
#
# Rule 0 of refs/metaheuristics/SELECTION-GUIDE.md does not put a metaheuristic
# anywhere near this. The metaheuristics reference itself classifies k-means as
# a degenerate case of Expectation Maximisation under Alternating Optimisation
# and names it "famous but non-metaheuristic" (p.125, footnote 107) -- it sits
# below even the greedy rung of the ladder, so no baseline-gate comparison
# against random search applies.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterAssignment:
    """A clustering, with everything needed to place a new point in it.

    `centre`/`scale` are the standardisation actually applied, carried so that
    `atypicality` and `neighbours` measure in the same space the clustering was
    fitted in. Refitting a scaler at query time is the commonest way a distance
    stops meaning what it meant.
    """

    labels: tuple[int, ...]
    keys: tuple[WardDayKey, ...]
    centroids: tuple[tuple[float, ...], ...]
    k: int
    space: str
    dimension_names: tuple[str, ...]
    centre: tuple[float, ...]
    scale: tuple[float, ...]
    inertia: float
    n_iterations: int
    method_version: str = METHOD_VERSION

    def __post_init__(self) -> None:
        if len(self.labels) != len(self.keys):
            raise ValueError("labels and keys must be the same length")
        if self.space not in {"features", "embedding"}:
            raise ValueError(
                f"space must be 'features' or 'embedding', got {self.space!r}"
            )

    def label_of(self, key: WardDayKey) -> int:
        return self.labels[self.keys.index(key)]

    def members(self, cluster: int) -> tuple[WardDayKey, ...]:
        return tuple(
            key
            for key, label in zip(self.keys, self.labels, strict=True)
            if label == cluster
        )


def _matrix(
    vectors: Sequence[WardDayVector], use_embedding: bool
) -> list[tuple[float, ...]]:
    if not use_embedding:
        return [v.as_row() for v in vectors]
    rows: list[tuple[float, ...]] = []
    dims: set[int] = set()
    for v in vectors:
        if v.embedding is None:
            raise EmbeddingSpaceError(
                f"ward-day {v.key} carries no embedding; `use_embedding=True` "
                "requires every vector to have one (SPEC-007 criterion 13: "
                "`features` is always present, `embedding` is not)"
            )
        rows.append(v.embedding)
        dims.add(len(v.embedding))
    if len(dims) > 1:
        raise EmbeddingSpaceError(f"embeddings disagree in dimension: {sorted(dims)}")
    return rows


def _standardise(
    rows: Sequence[tuple[float, ...]],
) -> tuple[list[tuple[float, ...]], tuple[float, ...], tuple[float, ...]]:
    """Z-score each dimension.

    Not optional for the interpretable space: `motion_m` runs to hundreds and
    `n_clinicians` to single digits, so unstandardised Euclidean k-means would
    be clustering on metres and nothing else. Zero-variance dimensions get a
    scale of 1.0 rather than being dropped, so `FEATURE_NAMES` and a centroid
    stay index-aligned.
    """
    n = len(rows)
    dim = len(rows[0])
    centre = tuple(math.fsum(r[j] for r in rows) / n for j in range(dim))
    scale_list: list[float] = []
    for j in range(dim):
        var = math.fsum((r[j] - centre[j]) ** 2 for r in rows) / n
        sd = math.sqrt(var)
        scale_list.append(sd if sd > 1e-12 else 1.0)
    scale = tuple(scale_list)
    scaled = [tuple((r[j] - centre[j]) / scale[j] for j in range(dim)) for r in rows]
    return scaled, centre, scale


def _sq_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.fsum((x - y) ** 2 for x, y in zip(a, b, strict=True))


def _kmeans_plus_plus(
    rows: Sequence[tuple[float, ...]], k: int, rng: Random
) -> list[tuple[float, ...]]:
    """Arthur & Vassilvitskii seeding, every draw from `rng`.

    Chosen over a uniform draw of k rows because k-means is only as good as its
    start and a uniform draw routinely puts two centres inside one cluster --
    which on this data would report two flavours of "routine" and no skeleton
    days at all.
    """
    centres = [rows[rng.randrange(len(rows))]]
    while len(centres) < k:
        weights = [min(_sq_distance(r, c) for c in centres) for r in rows]
        total = math.fsum(weights)
        if total <= 0.0:
            # Every remaining point coincides with a chosen centre. Fill
            # deterministically rather than looping forever on a degenerate
            # input; the empty-cluster repair below will not help here.
            centres.append(rows[len(centres) % len(rows)])
            continue
        target = rng.random() * total
        cumulative = 0.0
        for row, weight in zip(rows, weights, strict=True):
            cumulative += weight
            if cumulative >= target:
                centres.append(row)
                break
        else:  # pragma: no cover - floating-point tail
            centres.append(rows[-1])
    return centres


def _assign(
    rows: Sequence[tuple[float, ...]], centres: Sequence[Sequence[float]]
) -> list[int]:
    """Nearest centre per row. Ties break to the lowest centre index, so a
    point equidistant from two centres lands in the same one every run."""
    labels: list[int] = []
    for row in rows:
        best = 0
        best_d = _sq_distance(row, centres[0])
        for index in range(1, len(centres)):
            d = _sq_distance(row, centres[index])
            if d < best_d:
                best, best_d = index, d
        labels.append(best)
    return labels


def cluster_ward_days(
    vectors: Sequence[WardDayVector],
    k: int,
    rng: Random,
    use_embedding: bool = False,
    *,
    max_iterations: int = 100,
) -> ClusterAssignment:
    """SPEC-007 Part B's `cluster_ward_days`. k-means with a stated `k`.

    Refuses below `MIN_WARD_DAYS` (criterion 17) and says why.

    `use_embedding=False` clusters the interpretable features and is the
    default in the signature the spec specifies. `use_embedding=True` requires
    every vector to carry an embedding and clusters those instead; it exists so
    the criterion-16 comparison can be *run*, and its use in any pipeline is
    governed by the measured outcome recorded in the audit log, not by this
    parameter's availability.
    """
    if len(vectors) < MIN_WARD_DAYS:
        raise InsufficientWardDaysError(
            f"clustering was asked to run on {len(vectors)} ward-day(s); the "
            f"SPEC-007 criterion 17 floor is {MIN_WARD_DAYS}. Below it, k-means "
            "returns clusters whatever the data does, and their apparent "
            "structure is sampling noise wearing the shape of a finding "
            "(ADR-0007). Refused rather than reported"
        )
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k!r}")
    if k > len(vectors):
        raise ValueError(f"k={k!r} exceeds the {len(vectors)} ward-days offered")

    ordered = sorted(vectors, key=lambda v: v.key)
    raw = _matrix(ordered, use_embedding)
    if use_embedding:
        # Already L2-normalised by `hwpm.retrieve.vector.embed_texts`; cosine
        # over unit vectors is a monotone function of Euclidean distance
        # (SPEC-007 modelling assumption 4), so no rescaling is applied and the
        # recorded scaler is the identity.
        rows = [tuple(r) for r in raw]
        centre = tuple(0.0 for _ in rows[0])
        scale = tuple(1.0 for _ in rows[0])
        names = tuple(f"e{i}" for i in range(len(rows[0])))
    else:
        rows, centre, scale = _standardise(raw)
        names = FEATURE_NAMES

    centres = [tuple(c) for c in _kmeans_plus_plus(rows, k, rng)]
    labels = _assign(rows, centres)
    iterations = 0
    while iterations < max_iterations:
        iterations += 1
        groups: dict[int, list[tuple[float, ...]]] = {i: [] for i in range(k)}
        for row, label in zip(rows, labels, strict=True):
            groups[label].append(row)
        new_centres: list[tuple[float, ...]] = []
        for index in range(k):
            members = groups[index]
            if not members:
                # Empty cluster: re-seed on the point furthest from any current
                # centre. Dropping the cluster instead would silently return
                # fewer than the k the caller asked for, and a caller who chose
                # k for a clinical reason would not be told.
                furthest = max(
                    rows, key=lambda r: min(_sq_distance(r, c) for c in centres)
                )
                new_centres.append(furthest)
                continue
            dim = len(members[0])
            new_centres.append(
                tuple(math.fsum(m[j] for m in members) / len(members) for j in range(dim))
            )
        new_labels = _assign(rows, new_centres)
        centres = new_centres
        if new_labels == labels:
            labels = new_labels
            break
        labels = new_labels

    inertia = math.fsum(
        _sq_distance(row, centres[label]) for row, label in zip(rows, labels, strict=True)
    )
    return ClusterAssignment(
        labels=tuple(labels),
        keys=tuple(v.key for v in ordered),
        centroids=tuple(centres),
        k=k,
        space="embedding" if use_embedding else "features",
        dimension_names=names,
        centre=centre,
        scale=scale,
        inertia=inertia,
        n_iterations=iterations,
    )


def _project(v: WardDayVector, model: ClusterAssignment) -> tuple[float, ...]:
    if model.space == "embedding":
        if v.embedding is None:
            raise EmbeddingSpaceError(
                f"ward-day {v.key} carries no embedding but the model was fitted "
                "in the embedding space"
            )
        return tuple(v.embedding)
    row = v.as_row()
    return tuple((row[j] - model.centre[j]) / model.scale[j] for j in range(len(row)))


def atypicality(v: WardDayVector, model: ClusterAssignment) -> float:
    """Distance from `v` to the nearest cluster centre, in the model's space.

    Named `atypicality` and not `score` on purpose (SPEC-007 "Failure modes").
    **Atypical means unlike other ward-days. It is not a synonym for worse**,
    and it will be read as one the first time it appears on a dashboard. The
    only sanctioned use of this number is to produce a list of ward-days worth
    a human look; there is no oracle for it, because "atypical" is defined by
    the method rather than against truth, and that is a finding rather than an
    omission (SPEC-007 "Test oracle").
    """
    point = _project(v, model)
    return math.sqrt(min(_sq_distance(point, c) for c in model.centroids))


def neighbours(
    v: WardDayVector,
    pool: Sequence[WardDayVector],
    model: ClusterAssignment,
    k: int = 5,
) -> list[tuple[WardDayKey, float]]:
    """The `k` ward-days in `pool` most like `v`, nearest first.

    Two deliberate departures from SPEC-007's interface sketch
    `neighbours(v, k=5) -> list[tuple[date, float]]`, both recorded rather than
    quietly made:

    1. `pool` and `model` are explicit arguments. The sketch implies a module
       global holding a corpus of vectors; a hidden global here would be a
       hidden store of patient-derived data, which is precisely what ADR-0007
       decision 2 says must never be ambient.
    2. The key is `(date, ward)`, not `date`. The unit of analysis is the
       ward-day (modelling assumption 5), and a bare date does not identify
       one.

    `v` itself is excluded if it appears in `pool`: a ward-day is not its own
    neighbour, and a warm start seeded from the instance it is solving would
    make the measurement of warm starts meaningless.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k!r}")
    point = _project(v, model)
    scored = [
        (other.key, math.sqrt(_sq_distance(point, _project(other, model))))
        for other in pool
        if other.key != v.key
    ]
    scored.sort(key=lambda item: (item[1], item[0]))
    return scored[:k]


# ---------------------------------------------------------------------------
# The learned path (SPEC-007 criterion 16's challenger)
# ---------------------------------------------------------------------------

#: Dwell buckets for `trace_text`. SPEC-007 modelling assumption 6: "trace
#: encoding treats activity sequences as ordered symbols with durations.
#: Discarding duration would make a 4-minute and a 40-minute bedside episode
#: identical, which is the distinction SPEC-002's `min_dwell_s` exists to
#: make." Buckets rather than raw seconds because the encoder is a sentence
#: model: "1443" is a token, not a magnitude, to it.
DWELL_BUCKETS: tuple[tuple[float, str], ...] = (
    (300.0, "brief"),
    (600.0, "short"),
    (1200.0, "medium"),
    (2400.0, "long"),
    (math.inf, "extended"),
)


def _bucket(seconds: float) -> str:
    for limit, name in DWELL_BUCKETS:
        if seconds < limit:
            return name
    return DWELL_BUCKETS[-1][1]


def trace_text(episodes: Sequence[BedsideEpisode], rounds: Sequence[Round] = ()) -> str:
    """A ward-day rendered as an ordered symbol sequence for a text encoder.

    Beds are named by their position in the ward (`bed3`) rather than by their
    id, and clinicians by their index within the ward-day rather than by id, so
    the string carries *structure* and not identifiers. This is a data-handling
    property as much as a modelling one: the encoded string must not be a
    re-identification surface, and a bed id plus a date narrows further than a
    shape does.

    The honest limitation, stated because criterion 16's result depends on it:
    `BAAI/bge-small-en-v1.5` is an English *sentence* encoder, and this is a
    symbol sequence. Nothing about its pre-training makes "bed3 medium bed1
    brief" mean what a process-mining trace encoder trained on activity
    sequences would make it mean. The alternative -- training a trace encoder
    on ward data -- is out of scope for SPEC-007 by name ("Fine-tuning or
    training an embedding model on ward data"), because the resulting model is
    itself derived personal data. So this is the best available challenger
    under the spec's own constraints, and if it loses, the reason it lost is
    part of the finding rather than a defect to fix later.
    """
    routes = _routes(episodes, rounds)
    parts: list[str] = []
    for index, route in enumerate(routes):
        steps = " ".join(
            f"{_short_bed(e.bed)} {_bucket(e.duration.total_seconds())}" for e in route
        )
        parts.append(f"round {index}: {steps}")
    return " ; ".join(parts)


def _short_bed(bed: LocationId) -> str:
    tail = bed.value.split("/", 1)[-1]
    return tail.lower()


def attach_embeddings(
    vectors: Sequence[WardDayVector],
    cells: Mapping[WardDayKey, Sequence[BedsideEpisode]],
    rounds: Sequence[Round] = (),
) -> list[WardDayVector]:
    """Attach a learned embedding of each ward-day's trace text.

    Runs the *same local ONNX model* Part A uses, via
    `hwpm.retrieve.vector.embed_texts`. The import is lazy and inside the
    function for two reasons: `fastembed` is an optional dependency (SPEC-007
    criteria 8-9, `hwpm govern` must import without it), and reusing Part A's
    loaded model is the one thing ADR-0007 says the two systems *do* share
    ("they share an embedding runtime and a dependency-weight decision").
    What is not shared is storage: nothing here opens Part A's
    `context-vectors.db`, and no ward-day vector is ever written into it
    (ADR-0007 decision 1).

    Nothing leaves the machine. `embed_texts` runs the cached ONNX weights
    offline; ADR-0007 decision 2 admits no exceptions clause for
    patient-derived data.
    """
    from hwpm.retrieve.vector import embed_texts

    texts = [trace_text(cells[v.key], rounds) for v in vectors]
    embeddings = embed_texts(texts)
    return [
        WardDayVector(
            day=v.day,
            ward=v.ward,
            features=v.features,
            patients=v.patients,
            clinicians=v.clinicians,
            provenance={**v.provenance, "embedding_model": _model_name()},
            embedding=tuple(e),
        )
        for v, e in zip(vectors, embeddings, strict=True)
    ]


def _model_name() -> str:
    from hwpm.retrieve.vector import MODEL_NAME

    return MODEL_NAME


# ---------------------------------------------------------------------------
# The measurement (SPEC-007 criterion 16)
# ---------------------------------------------------------------------------


def adjusted_rand_index(a: Sequence[object], b: Sequence[object]) -> float:
    """Adjusted Rand index between two labellings of the same items.

    Hand-rolled rather than pulled from scikit-learn: the whole scientific
    stack is an optional extra here (`pyproject.toml`), scikit-learn is not in
    it, and adding a dependency of that weight for one contingency-table sum
    would be exactly the dependency creep ADR-0007 refuses elsewhere.

    Returns 1.0 for identical partitions, ~0.0 for the agreement two random
    partitions would show by chance, and can go negative.
    """
    if len(a) != len(b):
        raise ValueError(f"labellings differ in length: {len(a)} vs {len(b)}")
    n = len(a)
    if n < 2:
        raise ValueError("adjusted Rand index needs at least two items")

    table: dict[tuple[object, object], int] = {}
    rows: dict[object, int] = {}
    cols: dict[object, int] = {}
    for x, y in zip(a, b, strict=True):
        table[(x, y)] = table.get((x, y), 0) + 1
        rows[x] = rows.get(x, 0) + 1
        cols[y] = cols.get(y, 0) + 1

    def choose2(value: int) -> float:
        return value * (value - 1) / 2.0

    index = math.fsum(choose2(v) for v in table.values())
    row_sum = math.fsum(choose2(v) for v in rows.values())
    col_sum = math.fsum(choose2(v) for v in cols.values())
    total = choose2(n)
    expected = row_sum * col_sum / total
    maximum = (row_sum + col_sum) / 2.0
    if maximum == expected:
        # Both labellings are trivial (one cluster each, or all singletons).
        # 0.0 is the honest answer: there is no agreement above chance to
        # measure, and 1.0 would report a perfect recovery of nothing.
        return 0.0
    return (index - expected) / (maximum - expected)


__all__ = [
    "DWELL_BUCKETS",
    "FEATURE_NAMES",
    "FLOOR_SEPARATION_M",
    "METHOD_VERSION",
    "MIN_WARD_DAYS",
    "ClusterAssignment",
    "EmbeddingSpaceError",
    "InsufficientWardDaysError",
    "WardDayKey",
    "WardDayParams",
    "WardDayVector",
    "adjusted_rand_index",
    "attach_embeddings",
    "atypicality",
    "cluster_ward_days",
    "encode_ward_day",
    "encode_ward_days",
    "group_ward_days",
    "neighbours",
    "trace_text",
    "ward_of",
]
