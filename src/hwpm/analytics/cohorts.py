"""Publishable cohort summaries over ward-day clusters. SPEC-007 Part B, N20.

`hwpm.mining.embed` clusters ward-days and publishes nothing. This module is
where a cluster becomes something a person can be shown, and it exists as a
separate module in a separate layer for one reason: the ADR-0005 aggregation
floor lives in `hwpm.analytics.suppression`, `hwpm.analytics` sits *above*
`hwpm.mining` in the import-linter layering, and `hwpm.mining.embed` therefore
cannot apply it. Rather than restate the floor constants down in `mining` --
where a second definition could drift below the first -- the vectors carry
their cohort and the floor is applied here, once, by the same `check_counts`
the N18 coverage cells use.

What this module is not
-----------------------
It is **not** a governance figure and it must never become the input to one.
ADR-0007 decision 5 permits embeddings to drive retrieval, clustering, anomaly
triage and warm starts; the import-linter contract "Governance figures do not
depend on embeddings" (`pyproject.toml`) forbids `hwpm.analytics.coverage`,
`hwpm.analytics.motion`, `hwpm.analytics.bounds` and
`hwpm.analytics.suppression` from importing `hwpm.mining.embed` or this
module, transitively or directly. This module is downstream of all of them and
imports the floor from `suppression`, never the other way round.

`CohortSummary.atypical` is a **list of ward-days worth a human look**. It is
not a quality ranking, and `render()` says so in the same breath as printing
it, on the same principle as `MotionReport.ATTRIBUTABLE_M_WORDING`: the
qualification is the part that gets dropped, so the correct sentence has to be
the easy one to produce.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hwpm.analytics.suppression import SuppressionFloorError, check_counts
from hwpm.mining.embed import (
    ClusterAssignment,
    WardDayKey,
    WardDayVector,
    atypicality,
)

#: The sentence a cohort listing has to travel with. ADR-0007: "anomaly means
#: *unlike other days*, which is not a synonym for *worse*, and will be read as
#: one the first time it reaches a slide."
ATYPICALITY_WORDING = (
    "Atypicality is distance from the nearest cohort centre in the ward-day "
    "feature space. It means UNLIKE OTHER WARD-DAYS. It is not a quality "
    "score, not a ranking, and not a judgement about the care given on those "
    "days. The only sanctioned use is to produce candidates for human review "
    "(SPEC-007 Part B, ADR-0007 decision 5). There is no oracle for it: "
    "'atypical' is defined by the method, not against truth."
)


@dataclass(frozen=True)
class CohortSummary:
    """One cluster, either publishable or withheld.

    A suppressed cohort carries no numbers at all -- not the size, not the
    feature means, not the atypical list. Same strictness N18's `CoverageCell`
    applies, and for the same reason: a rendering path cannot display a number
    that is not there.
    """

    cluster: int
    suppressed: bool
    suppression_reason: str | None = None
    n_ward_days: int | None = None
    n_patients: int | None = None
    n_clinicians: int | None = None
    feature_means: dict[str, float] | None = None
    atypical: tuple[tuple[WardDayKey, float], ...] | None = None

    def __post_init__(self) -> None:
        if self.suppressed:
            populated = [
                name
                for name in (
                    "n_ward_days",
                    "n_patients",
                    "n_clinicians",
                    "feature_means",
                    "atypical",
                )
                if getattr(self, name) is not None
            ]
            if populated:
                raise ValueError(
                    f"a suppressed cohort must carry no values; got {populated}. "
                    "ADR-0005 rule 4 suppresses the cell, not just its headline"
                )
            if not self.suppression_reason:
                raise ValueError("a suppressed cohort must say why it was suppressed")
        elif self.n_ward_days is None or self.feature_means is None:
            raise ValueError("a published cohort must carry its size and its means")


@dataclass(frozen=True)
class CohortReport:
    """Every cluster in an assignment, floor-checked."""

    cohorts: tuple[CohortSummary, ...]
    space: str
    method_version: str
    k: int

    @property
    def n_published(self) -> int:
        return sum(1 for c in self.cohorts if not c.suppressed)

    @property
    def n_suppressed(self) -> int:
        return sum(1 for c in self.cohorts if c.suppressed)

    def render(self) -> str:
        lines = [
            f"Ward-day cohorts ({self.method_version}, space={self.space}, k={self.k})",
            "",
        ]
        for cohort in self.cohorts:
            if cohort.suppressed:
                lines.append(
                    f"  cohort {cohort.cluster}: WITHHELD -- {cohort.suppression_reason}"
                )
                continue
            assert cohort.feature_means is not None
            head = ", ".join(
                f"{name}={value:.1f}"
                for name, value in sorted(cohort.feature_means.items())[:4]
            )
            lines.append(
                f"  cohort {cohort.cluster}: {cohort.n_ward_days} ward-days, "
                f"{cohort.n_patients} patients, {cohort.n_clinicians} clinicians"
            )
            lines.append(f"    {head}")
        lines.extend(["", ATYPICALITY_WORDING])
        if self.space == "embedding":
            lines.append("")
            lines.append(
                "This report was produced in a LEARNED EMBEDDING space. "
                "ADR-0007 decision 5: it may not be the basis of a reported "
                "governance figure without an interpretable derivation "
                "published alongside it."
            )
        return "\n".join(lines)


def summarise_cohorts(
    vectors: Sequence[WardDayVector],
    model: ClusterAssignment,
    *,
    n_atypical: int = 5,
) -> CohortReport:
    """Cluster summaries with the ADR-0005 aggregation floor applied.

    A cohort resolving to fewer than `MIN_PATIENTS` distinct patients, or to
    one named clinician, is suppressed entirely (SPEC-007 criterion 18). The
    floor is checked with `hwpm.analytics.suppression.check_counts` -- the same
    function the N18 coverage cells use -- so there is one definition of the
    floor in the codebase and this module cannot weaken it.

    Feature means are always reported from `WardDayVector.features`, never from
    an embedding, even when `model.space == "embedding"`. That is the
    "interpretable derivation published alongside" ADR-0007 decision 5
    requires: a cohort found in an opaque space is still described in named,
    unit-carrying numbers, or it is not described at all.
    """
    if n_atypical < 0:
        raise ValueError(f"n_atypical must be >= 0, got {n_atypical!r}")
    by_key = {v.key: v for v in vectors}
    missing = [key for key in model.keys if key not in by_key]
    if missing:
        raise ValueError(
            f"the assignment names {len(missing)} ward-day(s) absent from "
            f"`vectors`, e.g. {missing[0]}; summarising a cluster from a "
            "different population than it was fitted on is not a report"
        )

    cohorts: list[CohortSummary] = []
    for cluster in range(model.k):
        members = [by_key[key] for key in model.members(cluster)]
        patients: set[object] = set()
        clinicians: set[object] = set()
        for member in members:
            patients |= set(member.patients)
            clinicians |= set(member.clinicians)
        try:
            check_counts(len(patients), len(clinicians))
        except SuppressionFloorError as exc:
            cohorts.append(
                CohortSummary(
                    cluster=cluster, suppressed=True, suppression_reason=str(exc)
                )
            )
            continue
        names = sorted(members[0].features)
        means = {
            name: sum(float(m.features[name]) for m in members) / len(members)
            for name in names
        }
        scored = sorted(
            ((m.key, atypicality(m, model)) for m in members),
            key=lambda item: (-item[1], item[0]),
        )
        cohorts.append(
            CohortSummary(
                cluster=cluster,
                suppressed=False,
                n_ward_days=len(members),
                n_patients=len(patients),
                n_clinicians=len(clinicians),
                feature_means=means,
                atypical=tuple(scored[:n_atypical]),
            )
        )

    return CohortReport(
        cohorts=tuple(cohorts),
        space=model.space,
        method_version=model.method_version,
        k=model.k,
    )


__all__ = [
    "ATYPICALITY_WORDING",
    "CohortReport",
    "CohortSummary",
    "summarise_cohorts",
]
