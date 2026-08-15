"""The five `RequiredSpecialty` strategies and their confidence. SPEC-001, N04.

**These run at analysis time, not at ingestion time.** Nothing in the ingestion
pipeline calls them; `hwpm.ingest.reader` and `hwpm.ingest.report` do not import
this module. They live in `hwpm.ingest` because they read
`hwpm.ingest.evidence`'s records and a module cannot sensibly sit below the data
it consumes — SPEC-001's "Architectural consequence" #3 is about *when* the
conclusion is drawn (per analysis, from persisted evidence) rather than about
which package the code is filed under. The result types they produce are domain
objects (`hwpm.domain.specialty`), so every layer above can consume a
determination without depending on ingestion.

Three things this module is careful about, each because getting it wrong is a
known failure mode rather than a hypothetical one:

1. **Membership is the strategy's decision; confidence is computed over all
   three sources.** Choosing `referral` selects which specialties count, not
   which evidence is examined. A confidence that only looked at the selected
   source could not tell a lone referral from one that three sources agree
   with, and SPEC-001 requires exactly that distinction.

2. **Unrecognised specialty text is quarantined and counted, never guessed.**
   The same rule as `LocationMapper` (criterion 5), for the same reason: a
   silent mis-mapping corrupts everything downstream while looking healthy.
   The default alias table is empty — inventing clinical synonyms here would be
   that failure mode with extra steps.

3. **All five strategies are always computed.** `derive_all` is the only public
   route to a determination set, it returns all five, and `StrategyDeterminations`
   refuses to exist with fewer (criterion 9, ADR-0006 §3).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from hwpm.artefact import ArtefactEnvelope, ArtefactError, IncompleteStrategySetError
from hwpm.domain import (
    DEFAULT_MDT_CONFIDENCE_THRESHOLD,
    DEFAULT_STRATEGY,
    N_EVIDENCE_SOURCES,
    SINGLE_SOURCE_STRATEGIES,
    Calibration,
    EvidenceSource,
    PatientId,
    RequiredSpecialtyDetermination,
    RequiredSpecialtyStrategyKey,
    Specialty,
    SpecialtyClaim,
)
from hwpm.ingest.evidence import RequiredSpecialtyEvidence

#: Below this a specialty text is unrecognised: quarantined, counted, and
#: contributing to no claim. The same 0.8 SPEC-001 sets for locations — the two
#: fields have the same failure mode and there is no reason to hold free-text
#: specialty to a laxer standard than free-text location.
RECOGNITION_THRESHOLD = 0.8

#: `kind` of the artefact `to_artefact` produces.
ARTEFACT_KIND = "required_specialty"

_SEPARATORS = re.compile(r"[\s_\-]+")


# ---------------------------------------------------------------------------
# Specialty text recognition
# ---------------------------------------------------------------------------


def normalise_specialty_text(raw: str) -> str:
    """Casefold, trim, and collapse `_`/`-`/whitespace runs to single spaces.

    Normalisation, not guessing: "General_Medicine", "general medicine" and
    " GENERAL  MEDICINE " are the same string written three ways, so matching
    them carries no risk of the silent mis-mapping criterion 5 is about. Any
    transformation that could change *which* specialty is meant belongs in an
    alias table with a confidence below 1.0, not here.
    """
    return _SEPARATORS.sub(" ", raw.strip().casefold())


def _default_known() -> dict[str, Specialty]:
    return {normalise_specialty_text(member.value): member for member in Specialty}


@dataclass(frozen=True)
class SpecialtyMapper:
    """Maps a raw `specialty_text` to a `(Specialty, confidence)`.

    Two tiers, mirroring `LocationMapper`:

    - `known`: normalised text to `Specialty`, confidence 1.0. Defaults to the
      `Specialty` enum's own values, which is what a clean feed (the synthetic
      generator, or an export already coded to the enum) matches on.
    - `aliases`: normalised site synonyms at `fuzzy_confidence`. **Empty by
      default.** A shipped alias table would be this repository guessing at one
      hospital's vocabulary on behalf of every other; alias tables are site
      configuration and arrive with the site.

    Both mappings are keyed by `normalise_specialty_text` output, and that is
    checked at construction rather than trusted — a table keyed by raw strings
    would silently never match, which is worse than failing loudly.
    """

    known: Mapping[str, Specialty] = field(default_factory=_default_known)
    aliases: Mapping[str, Specialty] = field(default_factory=dict)
    fuzzy_confidence: float = 0.85

    def __post_init__(self) -> None:
        if not RECOGNITION_THRESHOLD <= self.fuzzy_confidence <= 1.0:
            raise ValueError(
                "fuzzy_confidence must be in "
                f"[{RECOGNITION_THRESHOLD}, 1.0] — anything below the "
                "recognition threshold would be quarantined on arrival — got "
                f"{self.fuzzy_confidence!r}"
            )
        for table_name in ("known", "aliases"):
            table: Mapping[str, Specialty] = getattr(self, table_name)
            for key in table:
                if key != normalise_specialty_text(key):
                    raise ValueError(
                        f"{table_name} keys must be normalised "
                        f"(`normalise_specialty_text`); {key!r} is not"
                    )

    def map(self, raw: str) -> tuple[Specialty | None, float]:
        """Return `(specialty, recognition)`, or `(None, 0.0)` if unrecognised.

        `None` rather than a best guess: criterion 5's rule applied to the
        specialty field. The caller counts the raw string in a
        `SpecialtyEvidenceReport` and drops it from every strategy.
        """
        key = normalise_specialty_text(raw)
        exact = self.known.get(key)
        if exact is not None:
            return exact, 1.0
        aliased = self.aliases.get(key)
        if aliased is not None:
            return aliased, self.fuzzy_confidence
        return None, 0.0


# ---------------------------------------------------------------------------
# Evidence index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecialtyEvidenceReport:
    """What the recognition pass saw. The specialty-text analogue of
    `IngestionReport`: counts, never repairs."""

    total_records: int
    recognised_count: int
    unrecognised_count: int
    unrecognised_texts: tuple[str, ...]
    available_sources: frozenset[EvidenceSource]

    @property
    def bounds_degenerate(self) -> bool:
        """True when a source type is missing from the extract entirely.

        `intersection` is then empty for every patient and the lower bound
        conveys nothing. SPEC-001's non-blocking open question about
        extractability, made machine-readable: a consumer showing bounds must
        say they are uninformative rather than present an empty lower bound as
        a finding.
        """
        return len(self.available_sources) < N_EVIDENCE_SOURCES


#: patient -> specialty -> source -> best recognition seen for that combination.
RecognitionTable = Mapping[PatientId, Mapping[Specialty, Mapping[EvidenceSource, float]]]


@dataclass(frozen=True)
class EvidenceIndex:
    """Recognised evidence, folded to one recognition per
    (patient, specialty, source).

    "Best recognition wins" when a source records the same specialty twice: two
    referrals to cardiology are one corroboration, not two, or a team that
    re-referred would outweigh three independent sources agreeing.
    """

    recognitions: RecognitionTable
    report: SpecialtyEvidenceReport

    def patients(self) -> tuple[PatientId, ...]:
        """Patients with at least one recognised record, in id order.

        Sorted rather than insertion-ordered so the artefact is byte-stable
        across runs (gate 8).
        """
        return tuple(sorted(self.recognitions, key=lambda pid: pid.value))


def index_evidence(
    evidence: RequiredSpecialtyEvidence, mapper: SpecialtyMapper | None = None
) -> EvidenceIndex:
    """Recognise every evidence record's specialty text and fold it into an index.

    Runs once per analysis; the five strategies then read it, which is what
    keeps "pre-computing all five" (SPEC-001 architectural consequence #4)
    cheap enough to be the default.
    """
    active = SpecialtyMapper() if mapper is None else mapper

    groups: Sequence[tuple[EvidenceSource, Sequence[Any]]] = (
        (EvidenceSource.REFERRAL, evidence.referrals),
        (EvidenceSource.CONSULT_NOTE, evidence.consult_notes),
        (EvidenceSource.PROBLEM_LIST, evidence.problem_list),
    )

    table: dict[PatientId, dict[Specialty, dict[EvidenceSource, float]]] = {}
    total = 0
    recognised = 0
    unrecognised: list[str] = []
    available: set[EvidenceSource] = set()

    for source, records in groups:
        if records:
            available.add(source)
        for record in records:
            total += 1
            specialty, recognition = active.map(record.specialty_text)
            if specialty is None or recognition < RECOGNITION_THRESHOLD:
                unrecognised.append(record.specialty_text)
                continue
            recognised += 1
            per_specialty = table.setdefault(record.patient, {})
            per_source = per_specialty.setdefault(specialty, {})
            per_source[source] = max(per_source.get(source, 0.0), recognition)

    report = SpecialtyEvidenceReport(
        total_records=total,
        recognised_count=recognised,
        unrecognised_count=len(unrecognised),
        unrecognised_texts=tuple(unrecognised),
        available_sources=frozenset(available),
    )
    return EvidenceIndex(recognitions=table, report=report)


# ---------------------------------------------------------------------------
# The five strategies
# ---------------------------------------------------------------------------


class RequiredSpecialtyStrategy(Protocol):
    """One definition of `RequiredSpecialty` (01-DOMAIN-MODEL.md rule 5:
    interfaces are Protocols, so downstream layers test against fakes)."""

    @property
    def key(self) -> RequiredSpecialtyStrategyKey: ...

    def derive(
        self, index: EvidenceIndex, patient: PatientId
    ) -> RequiredSpecialtyDetermination: ...


@dataclass(frozen=True)
class _MembershipStrategy:
    """Every strategy differs only in which source sets make a specialty a member.

    One implementation with five predicates rather than five near-identical
    classes: the confidence arithmetic is shared by construction, so it cannot
    drift between strategies, which is the property criterion 11's bounds
    depend on.
    """

    key: RequiredSpecialtyStrategyKey
    is_member: Callable[[frozenset[EvidenceSource]], bool]

    def derive(
        self, index: EvidenceIndex, patient: PatientId
    ) -> RequiredSpecialtyDetermination:
        per_specialty = index.recognitions.get(patient, {})
        claims = tuple(
            SpecialtyClaim.from_recognitions(specialty, per_specialty[specialty])
            # `Specialty` declaration order, not dict order: byte-stable output.
            for specialty in Specialty
            if specialty in per_specialty
            and self.is_member(frozenset(per_specialty[specialty]))
        )
        return RequiredSpecialtyDetermination(
            patient=patient,
            strategy=self.key,
            claims=claims,
            calibration=Calibration.UNCALIBRATED,
        )


def _single_source(source: EvidenceSource) -> Callable[[frozenset[EvidenceSource]], bool]:
    return lambda sources: source in sources


def _any_source(sources: frozenset[EvidenceSource]) -> bool:
    return bool(sources)


def _all_sources(sources: frozenset[EvidenceSource]) -> bool:
    return len(sources) == N_EVIDENCE_SOURCES


def _member_test(
    key: RequiredSpecialtyStrategyKey,
) -> Callable[[frozenset[EvidenceSource]], bool]:
    """SPEC-001's membership table, as code. `union` and `intersection` are the
    bounds; everything else is "this one source asserts it"."""
    if key is RequiredSpecialtyStrategyKey.UNION:
        return _any_source
    if key is RequiredSpecialtyStrategyKey.INTERSECTION:
        return _all_sources
    return _single_source(SINGLE_SOURCE_STRATEGIES[key])


#: All five, in enum declaration order. Built by iterating the enum, so a sixth
#: strategy cannot be added without this picking it up. Criterion 9 is enforced
#: by `StrategyDeterminations`, not by trusting callers to iterate this.
ALL_STRATEGIES: Mapping[RequiredSpecialtyStrategyKey, RequiredSpecialtyStrategy] = {
    key: _MembershipStrategy(key=key, is_member=_member_test(key))
    for key in RequiredSpecialtyStrategyKey
}


@dataclass(frozen=True)
class StrategyDeterminations:
    """All five strategies' conclusions for every patient, plus the report.

    Refuses to exist with fewer than five (criterion 9: "computed for every
    run; none can be skipped"). The check is here, on the type, rather than in
    `derive_all`, so a future caller assembling one by hand cannot skip it.
    """

    by_strategy: Mapping[
        RequiredSpecialtyStrategyKey,
        Mapping[PatientId, RequiredSpecialtyDetermination],
    ]
    report: SpecialtyEvidenceReport
    calibration: Calibration = Calibration.UNCALIBRATED

    def __post_init__(self) -> None:
        missing = set(RequiredSpecialtyStrategyKey) - set(self.by_strategy)
        if missing:
            raise IncompleteStrategySetError(
                "all five strategies must be computed (SPEC-001 criterion 9); "
                f"missing {sorted(key.value for key in missing)}"
            )

    def select(
        self, key: RequiredSpecialtyStrategyKey = DEFAULT_STRATEGY
    ) -> Mapping[PatientId, RequiredSpecialtyDetermination]:
        """The determinations for one strategy. Selection changes emphasis,
        never availability — the other four are still here."""
        return self.by_strategy[key]

    def patients(self) -> tuple[PatientId, ...]:
        return tuple(sorted(self.by_strategy[DEFAULT_STRATEGY], key=lambda p: p.value))


def derive_all(
    evidence: RequiredSpecialtyEvidence, mapper: SpecialtyMapper | None = None
) -> StrategyDeterminations:
    """Compute all five strategies for every patient with recognised evidence.

    The only public route to a determination set. There is deliberately no
    `derive_one(strategy)`: SPEC-001's mitigation for the incentive problem is
    that "every strategy is always computed and the front end always displays
    the spread", and an API that made computing one cheaper than computing five
    would erode that within a release or two.
    """
    index = index_evidence(evidence, mapper)
    patients = index.patients()
    by_strategy = {
        key: {patient: strategy.derive(index, patient) for patient in patients}
        for key, strategy in ALL_STRATEGIES.items()
    }
    return StrategyDeterminations(by_strategy=by_strategy, report=index.report)


# ---------------------------------------------------------------------------
# Artefact plumbing (criterion 10) — what N14's selector reads
# ---------------------------------------------------------------------------


def _claim_to_mapping(claim: SpecialtyClaim) -> dict[str, Any]:
    return {
        "specialty": claim.specialty.value,
        # Derived from `corroboration`; emitted anyway so a consumer that is not
        # this library (the three.js viewer) does not have to reimplement the
        # arithmetic. `from_artefact` recomputes rather than trusting it.
        "confidence": claim.confidence,
        "corroboration": [
            {"source": entry.source.value, "recognition": entry.recognition}
            for entry in claim.corroboration
        ],
    }


def _claim_from_mapping(data: Mapping[str, Any]) -> SpecialtyClaim:
    return SpecialtyClaim.from_recognitions(
        Specialty(data["specialty"]),
        {
            EvidenceSource(entry["source"]): float(entry["recognition"])
            for entry in data["corroboration"]
        },
    )


def to_artefact(
    determinations: StrategyDeterminations,
    method_version: str,
    *,
    strategy: RequiredSpecialtyStrategyKey = DEFAULT_STRATEGY,
    mdt_threshold: float = DEFAULT_MDT_CONFIDENCE_THRESHOLD,
) -> ArtefactEnvelope:
    """Pack all five strategies into one strategy-keyed envelope.

    All five go in the payload, and `strategy` names only which is emphasised,
    so the selector (N14) is a re-key rather than a recompute and the spread is
    always available to display alongside it (ADR-0006 §3).

    `mdt_threshold` is recorded in the payload because a coverage figure
    produced at a stricter threshold is not comparable with one produced at the
    default, and that difference has to be visible in the artefact rather than
    remembered by whoever ran it.
    """
    report = determinations.report
    payload: dict[str, Any] = {
        "mdt_confidence_threshold": mdt_threshold,
        "available_sources": [
            source.value
            for source in EvidenceSource
            if source in report.available_sources
        ],
        "bounds_degenerate": report.bounds_degenerate,
        "recognition": {
            "total_records": report.total_records,
            "recognised_count": report.recognised_count,
            "unrecognised_count": report.unrecognised_count,
            "unrecognised_texts": list(report.unrecognised_texts),
        },
        "determinations": {
            key.value: {
                patient.value: [
                    _claim_to_mapping(claim)
                    for claim in determinations.by_strategy[key][patient].claims
                ]
                for patient in determinations.patients()
            }
            for key in RequiredSpecialtyStrategyKey
        },
    }
    return ArtefactEnvelope(
        kind=ARTEFACT_KIND,
        method_version=method_version,
        strategy=strategy,
        available_strategies=frozenset(RequiredSpecialtyStrategyKey),
        calibration=determinations.calibration,
        payload=payload,
    )


def from_artefact(envelope: ArtefactEnvelope) -> StrategyDeterminations:
    """Rebuild typed determinations from an envelope.

    Confidences are recomputed from the stored corroboration rather than read
    from the `confidence` field: a file that has been hand-edited to raise a
    number should not be able to raise it, and the corroboration is the
    evidence while the confidence is a view of it.
    """
    if envelope.kind != ARTEFACT_KIND:
        raise ArtefactError(
            f"expected a {ARTEFACT_KIND!r} artefact, got {envelope.kind!r}"
        )
    payload = envelope.payload
    raw_determinations = payload["determinations"]
    by_strategy: dict[
        RequiredSpecialtyStrategyKey, dict[PatientId, RequiredSpecialtyDetermination]
    ] = {}
    for key in RequiredSpecialtyStrategyKey:
        if key.value not in raw_determinations:
            raise IncompleteStrategySetError(
                f"artefact payload has no determinations for {key.value!r} "
                "(SPEC-001 criterion 9)"
            )
        by_strategy[key] = {
            PatientId(patient_id): RequiredSpecialtyDetermination(
                patient=PatientId(patient_id),
                strategy=key,
                claims=tuple(_claim_from_mapping(claim) for claim in claims),
                calibration=envelope.calibration,
            )
            for patient_id, claims in raw_determinations[key.value].items()
        }

    recognition = payload["recognition"]
    report = SpecialtyEvidenceReport(
        total_records=int(recognition["total_records"]),
        recognised_count=int(recognition["recognised_count"]),
        unrecognised_count=int(recognition["unrecognised_count"]),
        unrecognised_texts=tuple(recognition["unrecognised_texts"]),
        available_sources=frozenset(
            EvidenceSource(value) for value in payload["available_sources"]
        ),
    )
    return StrategyDeterminations(
        by_strategy=by_strategy, report=report, calibration=envelope.calibration
    )
