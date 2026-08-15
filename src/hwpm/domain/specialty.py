"""`RequiredSpecialty` determinations and their confidence. SPEC-001, node N04.

These are the *results* of a `RequiredSpecialtyStrategy`, so they live in the
domain: frozen value objects carrying provenance (01-DOMAIN-MODEL.md rules 1
and 4), no I/O (rule 2), imported by every layer above. The strategy Protocol
and the five implementations live in `hwpm.ingest.specialty`, beside the
evidence records they read — a domain module cannot name `RequiredSpecialtyEvidence`
without inverting the dependency the architecture exists to protect.

01-DOMAIN-MODEL.md rule 3 is the reason `SpecialtyClaim` is a class rather than
a `(specialty, float)` tuple or a dict entry: this is the single most
consequential derived quantity in the model, and a derived quantity whose
provenance is a dict key is a derived quantity nobody can defend in a meeting.

**The confidence is not a probability.** SPEC-001, "The confidence model":

    confidence(p, s) = ( Σ recognition(t) for t in T(p, s) ) / 3

`T(p, s)` is the set of evidence source types that assert `s` for `p` with a
recognised specialty text; `recognition(t)` is the text mapper's confidence for
that source; the denominator is the number of source types SPEC-001 defines, a
constant of the model rather than the number of sources a given extract happened
to contain. Read it as "how many of three independent sources corroborate this,
discounted by how sure we are each one said it". It becomes a probability only
after the clinician-review calibration SPEC-001 requires, which is why every
determination carries a `Calibration` and defaults it to `UNCALIBRATED`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from hwpm.domain.model import PatientId, Specialty

# ---------------------------------------------------------------------------
# Evidence source types and the confidence denominator
# ---------------------------------------------------------------------------


class EvidenceSource(Enum):
    """The three source *types* SPEC-001's strategy table names.

    A source type, not a source system: two hospitals' referral systems are
    both `REFERRAL` here. The count of members is the confidence denominator,
    so adding a member is a method change under ADR-0006 §1 and requires the
    back-series to be recomputed, not just a new enum value.
    """

    REFERRAL = "referral"
    CONSULT_NOTE = "consult_note"
    PROBLEM_LIST = "problem_list"


#: Denominator of every confidence. Deliberately the number of source types the
#: *model* defines, never the number present in a particular extract — see
#: SPEC-001, "The fixed denominator is a design choice with a consequence".
N_EVIDENCE_SOURCES = len(EvidenceSource)

#: Claims below this are excluded from MDT analysis and counted (criterion 8).
#: One exact-text source, so a single-source strategy's own claims survive its
#: own default; anything stricter would silently turn every strategy into
#: `intersection`. SPEC-001, "The MDT threshold".
DEFAULT_MDT_CONFIDENCE_THRESHOLD = 1.0 / N_EVIDENCE_SOURCES

#: Both sides of the threshold comparison are floating-point sums of the same
#: magnitudes; an exact `>=` on 1/3 is a rounding accident waiting to happen.
_THRESHOLD_TOLERANCE = 1e-12


class Calibration(Enum):
    """Whether the confidence has been checked against clinician judgement.

    SPEC-001's validation requirement: the chosen strategy is validated against
    clinician review of a random sample of >=50 patients before any MDT result
    built on it is reported. Until that has happened the confidence is an
    evidence count, not a probability, and saying so in a field beats saying so
    in a docstring nobody reads at rendering time.
    """

    UNCALIBRATED = "uncalibrated"
    CLINICIAN_VALIDATED = "clinician_validated"


class RequiredSpecialtyStrategyKey(Enum):
    """The five definitions, selectable at runtime (SPEC-001, resolved
    2026-08-14). `union` and `intersection` are the upper and lower bounds and
    are always shown alongside whichever is selected (ADR-0006 §3)."""

    REFERRAL = "referral"
    CONSULT_NOTE = "consult_note"
    PROBLEM_LIST = "problem_list"
    UNION = "union"
    INTERSECTION = "intersection"


#: "The default is `referral` -- the most conservative widely-available source --
#: so the flattering choice is an active decision, not the path of least
#: resistance" (SPEC-001).
DEFAULT_STRATEGY = RequiredSpecialtyStrategyKey.REFERRAL

#: The three strategies whose membership comes from exactly one source type.
#: `union` and `intersection` are absent because they are combinations, not
#: sources; callers use `.get()` and treat a miss as "combination strategy".
SINGLE_SOURCE_STRATEGIES: Mapping[RequiredSpecialtyStrategyKey, EvidenceSource] = (
    MappingProxyType(
        {
            RequiredSpecialtyStrategyKey.REFERRAL: EvidenceSource.REFERRAL,
            RequiredSpecialtyStrategyKey.CONSULT_NOTE: EvidenceSource.CONSULT_NOTE,
            RequiredSpecialtyStrategyKey.PROBLEM_LIST: EvidenceSource.PROBLEM_LIST,
        }
    )
)


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Corroboration:
    """One source type asserting one specialty, with the text mapper's
    confidence that it really said so.

    `recognition` is a *measured* match quality, on the same footing as
    `LocationMapper`'s output: 1.0 for an exact or case/separator-normalised
    match, the mapper's `fuzzy_confidence` for a site alias table hit. Text the
    mapper does not recognise never becomes a `Corroboration` at all — it is
    quarantined and counted, per SPEC-001's "Specialty text recognition".
    """

    source: EvidenceSource
    recognition: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.recognition <= 1.0:
            raise ValueError(f"recognition must be in [0, 1], got {self.recognition!r}")


@dataclass(frozen=True)
class SpecialtyClaim:
    """One specialty asserted for one patient, with everything that asserts it.

    `corroboration` holds one entry per source type that asserts the specialty,
    in `EvidenceSource` declaration order — *regardless of which strategy
    produced the claim*. That is the point: membership is the strategy's
    decision, confidence is computed over all the evidence, so a lone referral
    (0.333) and a referral three sources agree with (1.0) are distinguishable
    even though both are members under `referral`.
    """

    specialty: Specialty
    corroboration: tuple[Corroboration, ...]

    def __post_init__(self) -> None:
        if not self.corroboration:
            raise ValueError(
                f"a claim for {self.specialty.value} needs at least one "
                "corroborating source; a claim nothing asserts is not a claim"
            )
        sources = [entry.source for entry in self.corroboration]
        if len(set(sources)) != len(sources):
            raise ValueError(
                "corroboration holds at most one entry per source type, got "
                f"{[s.value for s in sources]}"
            )

    @classmethod
    def from_recognitions(
        cls, specialty: Specialty, recognitions: Mapping[EvidenceSource, float]
    ) -> SpecialtyClaim:
        """Build a claim from `{source: recognition}`, in declaration order.

        Ordering here rather than at every call site is what makes two runs
        over the same evidence compare equal (gate 8) — a `dict` iteration
        order is deterministic within a process but is not a contract callers
        should be relying on for artefact byte-equality.
        """
        return cls(
            specialty=specialty,
            corroboration=tuple(
                Corroboration(source=source, recognition=recognitions[source])
                for source in EvidenceSource
                if source in recognitions
            ),
        )

    @property
    def confidence(self) -> float:
        """SPEC-001's confidence: corroborating sources, discounted by
        recognition, over the three source types the model defines.

        Not a probability. See the module docstring and `Calibration`.
        """
        return sum(entry.recognition for entry in self.corroboration) / N_EVIDENCE_SOURCES


# ---------------------------------------------------------------------------
# Determinations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MDTSelection:
    """Criterion 8's output: what survived the threshold, what did not, and the
    threshold itself — all three, because an exclusion count without the
    threshold that produced it is not auditable."""

    threshold: float
    included: frozenset[Specialty]
    excluded: frozenset[Specialty]

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)


@dataclass(frozen=True)
class RequiredSpecialtyDetermination:
    """What one strategy concluded about one patient.

    Carries the strategy that produced it, because "a number that can travel
    without its definition will" (SPEC-001), and the calibration status,
    because an uncalibrated confidence rendered as a probability is the
    project's most likely serious error.
    """

    patient: PatientId
    strategy: RequiredSpecialtyStrategyKey
    claims: tuple[SpecialtyClaim, ...]
    calibration: Calibration = Calibration.UNCALIBRATED

    def __post_init__(self) -> None:
        specialties = [claim.specialty for claim in self.claims]
        if len(set(specialties)) != len(specialties):
            raise ValueError(
                "one claim per specialty; duplicates would double-count a "
                f"patient in MDT coverage: {[s.value for s in specialties]}"
            )

    @property
    def specialties(self) -> frozenset[Specialty]:
        """Every specialty this strategy considers required, threshold ignored.

        The set criterion 11's bounds are checked over. Use `for_mdt` for the
        set an MDT figure may be built from.
        """
        return frozenset(claim.specialty for claim in self.claims)

    def confidence_of(self, specialty: Specialty) -> float:
        """Confidence for `specialty`, or 0.0 if this strategy does not
        consider it required. Zero rather than `None` because the caller is
        almost always comparing against a threshold, and `None` there turns
        into a silent `or 0` at every call site."""
        for claim in self.claims:
            if claim.specialty is specialty:
                return claim.confidence
        return 0.0

    def for_mdt(
        self, threshold: float = DEFAULT_MDT_CONFIDENCE_THRESHOLD
    ) -> MDTSelection:
        """Split the claims at `threshold` (criterion 8).

        Nothing is dropped: the excluded set comes back with the included one,
        so a caller cannot report a coverage figure without being able to say
        how many determinations it left out.
        """
        included: set[Specialty] = set()
        excluded: set[Specialty] = set()
        for claim in self.claims:
            if claim.confidence >= threshold - _THRESHOLD_TOLERANCE:
                included.add(claim.specialty)
            else:
                excluded.add(claim.specialty)
        return MDTSelection(
            threshold=threshold,
            included=frozenset(included),
            excluded=frozenset(excluded),
        )
