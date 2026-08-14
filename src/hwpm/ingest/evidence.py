"""Persisted `RequiredSpecialty` evidence. SPEC-001 (2026-08-14 amendment), N03/N04.

SPEC-001's "Architectural consequence" #3: "the ingestion stage persists the
*evidence* (referrals, notes, problem list) rather than the *conclusion*, so
switching strategy does not require re-ingest." The five
`RequiredSpecialtyStrategy` implementations and their confidence scoring are
node N04's, owned by the architect role — not built here. This module exists
only so N03's output has somewhere to keep the raw material those strategies
will read, and so N04 does not have to re-open the ingestion pipeline to get
at it.

Each record type mirrors one row of SPEC-001's strategy table (`referral`,
`consult_note`, `problem_list`); `union` and `intersection` are combinations
computed over these at analysis time, not stored separately. Every record
carries `patient`, a raw `specialty_text` (deliberately *not* a `Specialty`
enum member — mapping free text to the domain enum is exactly the kind of
strategy-specific judgement call N04 owns, not N03), a `recorded_at`
timestamp, and `source`, following 01-DOMAIN-MODEL.md rule 4: "every derived
entity carries provenance."

Frozen, per 01-DOMAIN-MODEL.md rule 1 and CLAUDE.md's house style — these are
observed facts about a stay, not something later code should be able to
mutate in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hwpm.domain import PatientId


@dataclass(frozen=True)
class Referral:
    """One active-referral record. Basis for the `referral` strategy.

    SPEC-001: "Precise, but teams disengage without closing the
    referral — over-counts."
    """

    patient: PatientId
    specialty_text: str
    recorded_at: datetime
    source: str


@dataclass(frozen=True)
class ConsultNote:
    """One consult note authored during the stay. Basis for `consult_note`.

    SPEC-001: "Reflects real involvement; needs NLP; under-counts verbal
    advice." The NLP extraction itself is N04's problem — this only records
    that a note tied to a specialty was authored.
    """

    patient: PatientId
    specialty_text: str
    recorded_at: datetime
    source: str


@dataclass(frozen=True)
class ProblemListEntry:
    """One problem-list entry mapped to a specialty. Basis for `problem_list`.

    SPEC-001: "Broad; over-triggers on historical comorbidity."
    """

    patient: PatientId
    specialty_text: str
    recorded_at: datetime
    source: str


@dataclass(frozen=True)
class RequiredSpecialtyEvidence:
    """All persisted evidence for one ingested batch, keyed by patient.

    A plain container, not a strategy: it holds what was observed, in the
    form it was observed in, so that every `RequiredSpecialtyStrategy` (N04)
    reads from the same evidence and none of them can be run without the
    others being computable from it too (criterion 9's "none can be
    skipped" needs a single shared evidence set to compute all five from).
    """

    referrals: tuple[Referral, ...] = ()
    consult_notes: tuple[ConsultNote, ...] = ()
    problem_list: tuple[ProblemListEntry, ...] = ()

    def patients(self) -> frozenset[PatientId]:
        """Every patient with at least one evidence record of any kind."""
        return frozenset(
            record.patient
            for records in (self.referrals, self.consult_notes, self.problem_list)
            for record in records
        )
