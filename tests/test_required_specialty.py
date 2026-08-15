"""Tests for the five `RequiredSpecialty` strategies. SPEC-001 node N04.

Covers acceptance criteria 8 (`test_required_specialty_confidence`), 9
(`test_all_strategies_computed`), 10 (`test_artefact_strategy_key`) and 11
(`test_strategy_bounds`) — the exact names SPEC-001's acceptance-criteria table
gives them — plus the confidence model SPEC-001 adds in "The confidence model".

The oracle is `hwpm.ingest.synthetic.generate_evidence`, which turns a known
`GroundTruth.required_specialties` into the three imperfect sources. That means
the strategies are checked against what was really required rather than against
a previous run of themselves (SPEC-001, "Test oracle").
"""

from __future__ import annotations

import json
from pathlib import Path
from random import Random

import pytest

from hwpm.artefact import (
    ArtefactEnvelope,
    ArtefactError,
    IncompleteStrategySetError,
    MissingStrategyKeyError,
    UnknownStrategyError,
    read_json,
    write_json,
)
from hwpm.domain import (
    DEFAULT_MDT_CONFIDENCE_THRESHOLD,
    DEFAULT_STRATEGY,
    N_EVIDENCE_SOURCES,
    Calibration,
    Corroboration,
    EvidenceSource,
    PatientId,
    RequiredSpecialtyDetermination,
    RequiredSpecialtyStrategyKey,
    Specialty,
    SpecialtyClaim,
)
from hwpm.ingest.evidence import (
    ConsultNote,
    ProblemListEntry,
    Referral,
    RequiredSpecialtyEvidence,
)
from hwpm.ingest.specialty import (
    ARTEFACT_KIND,
    RECOGNITION_THRESHOLD,
    SpecialtyMapper,
    derive_all,
    from_artefact,
    index_evidence,
    normalise_specialty_text,
    to_artefact,
)
from hwpm.ingest.synthetic import EvidenceConfig, SynthConfig, generate, generate_evidence

SEED = 42
WHEN = EvidenceConfig().recorded_at
PATIENT = PatientId("PAT0001")
METHOD_VERSION = "n04-test"


# ---------------------------------------------------------------------------
# Hand-built evidence, so the arithmetic is checkable by eye
# ---------------------------------------------------------------------------


def _evidence(
    *,
    referral: tuple[str, ...] = (),
    consult: tuple[str, ...] = (),
    problems: tuple[str, ...] = (),
    patient: PatientId = PATIENT,
) -> RequiredSpecialtyEvidence:
    return RequiredSpecialtyEvidence(
        referrals=tuple(
            Referral(patient, text, WHEN, "test:referral") for text in referral
        ),
        consult_notes=tuple(
            ConsultNote(patient, text, WHEN, "test:consult") for text in consult
        ),
        problem_list=tuple(
            ProblemListEntry(patient, text, WHEN, "test:problems") for text in problems
        ),
    )


def _synthetic_evidence(
    evidence_config: EvidenceConfig | None = None,
) -> RequiredSpecialtyEvidence:
    _, truth = generate(SynthConfig(), Random(SEED))
    return generate_evidence(truth, evidence_config or EvidenceConfig(), Random(SEED + 1))


# ---------------------------------------------------------------------------
# Specialty text recognition (SPEC-001, "Specialty text recognition")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "general_medicine",
        "General Medicine",
        "  GENERAL   medicine  ",
        "general-medicine",
    ],
)
def test_recognition_normalises_without_guessing(raw: str) -> None:
    """Case, separators and padding are the same string written differently, so
    they map at full confidence."""
    specialty, recognition = SpecialtyMapper().map(raw)

    assert specialty is Specialty.GENERAL_MEDICINE
    assert recognition == 1.0


def test_recognition_quarantines_unknown_text() -> None:
    """Criterion 5's rule applied to the specialty field: never guessed."""
    specialty, recognition = SpecialtyMapper().map("cardio")

    assert specialty is None
    assert recognition == 0.0


def test_default_alias_table_is_empty() -> None:
    """A shipped synonym list would be this repo guessing one hospital's
    vocabulary on behalf of every other (SPEC-001)."""
    assert SpecialtyMapper().aliases == {}


def test_site_alias_maps_below_full_confidence() -> None:
    mapper = SpecialtyMapper(aliases={"cardio": Specialty.CARDIOLOGY})
    specialty, recognition = mapper.map("Cardio")

    assert specialty is Specialty.CARDIOLOGY
    assert recognition == pytest.approx(0.85)
    assert recognition >= RECOGNITION_THRESHOLD


def test_mapper_rejects_unnormalised_keys() -> None:
    with pytest.raises(ValueError, match="must be normalised"):
        SpecialtyMapper(aliases={"Cardio": Specialty.CARDIOLOGY})


def test_mapper_rejects_fuzzy_confidence_below_threshold() -> None:
    """A tier that is quarantined the moment it fires is a trap, not a tier."""
    with pytest.raises(ValueError, match="fuzzy_confidence"):
        SpecialtyMapper(fuzzy_confidence=0.5)


def test_unrecognised_text_is_counted_never_dropped() -> None:
    index = index_evidence(_evidence(referral=("cardiology", "wibble")))

    assert index.report.total_records == 2
    assert index.report.recognised_count == 1
    assert index.report.unrecognised_count == 1
    assert index.report.unrecognised_texts == ("wibble",)
    assert normalise_specialty_text("wibble") not in SpecialtyMapper().known


# ---------------------------------------------------------------------------
# The confidence model (SPEC-001, "The confidence model")
# ---------------------------------------------------------------------------


def test_confidence_is_corroboration_over_a_fixed_denominator() -> None:
    """One source out of three is 1/3, whichever strategy selected it. The
    denominator is a constant of the model, not the number of sources this
    extract happened to contain."""
    determinations = derive_all(_evidence(referral=("cardiology",)))
    determination = determinations.select(RequiredSpecialtyStrategyKey.REFERRAL)[PATIENT]

    assert determination.confidence_of(Specialty.CARDIOLOGY) == pytest.approx(1 / 3)


def test_confidence_distinguishes_a_lone_referral_from_three_sources() -> None:
    """The honesty requirement: an intersection three sources agree on is not
    the same evidential strength as a lone referral."""
    lone = derive_all(_evidence(referral=("cardiology",)))
    agreed = derive_all(
        _evidence(
            referral=("cardiology",), consult=("cardiology",), problems=("cardiology",)
        )
    )

    lone_confidence = lone.select(RequiredSpecialtyStrategyKey.REFERRAL)[
        PATIENT
    ].confidence_of(Specialty.CARDIOLOGY)
    agreed_confidence = agreed.select(RequiredSpecialtyStrategyKey.INTERSECTION)[
        PATIENT
    ].confidence_of(Specialty.CARDIOLOGY)

    assert lone_confidence == pytest.approx(1 / 3)
    assert agreed_confidence == pytest.approx(1.0)
    assert agreed_confidence > lone_confidence


def test_confidence_counts_all_sources_even_under_a_single_source_strategy() -> None:
    """Membership is the strategy's decision; confidence is computed over all
    the evidence. `referral` selects *which* specialties count, not which
    evidence is looked at."""
    determinations = derive_all(
        _evidence(referral=("cardiology",), consult=("cardiology",))
    )
    determination = determinations.select(RequiredSpecialtyStrategyKey.REFERRAL)[PATIENT]

    assert determination.confidence_of(Specialty.CARDIOLOGY) == pytest.approx(2 / 3)


def test_missing_source_type_caps_confidence_rather_than_inflating_it() -> None:
    """The fixed denominator: an extract containing only referrals can never
    reach 1.0, which is the correct signal that nothing in it is corroborated."""
    determinations = derive_all(
        _evidence(referral=("cardiology", "nephrology", "surgical"))
    )
    union = determinations.select(RequiredSpecialtyStrategyKey.UNION)[PATIENT]

    assert all(claim.confidence == pytest.approx(1 / 3) for claim in union.claims)
    assert determinations.report.bounds_degenerate is True
    assert determinations.report.available_sources == {EvidenceSource.REFERRAL}


def test_alias_recognition_discounts_the_confidence() -> None:
    mapper = SpecialtyMapper(aliases={"cardio": Specialty.CARDIOLOGY})
    determinations = derive_all(_evidence(referral=("cardio",)), mapper)
    determination = determinations.select(RequiredSpecialtyStrategyKey.REFERRAL)[PATIENT]

    assert determination.confidence_of(Specialty.CARDIOLOGY) == pytest.approx(0.85 / 3)


def test_repeated_records_in_one_source_are_one_corroboration() -> None:
    """Otherwise a team that re-referred would outweigh three independent
    sources agreeing."""
    determinations = derive_all(_evidence(referral=("cardiology",) * 5))
    determination = determinations.select(RequiredSpecialtyStrategyKey.REFERRAL)[PATIENT]

    assert determination.confidence_of(Specialty.CARDIOLOGY) == pytest.approx(1 / 3)


def test_confidence_is_declared_uncalibrated() -> None:
    """It is an evidence count, not a probability, until the >=50-patient
    clinician review SPEC-001 requires."""
    determinations = derive_all(_evidence(referral=("cardiology",)))

    assert determinations.calibration is Calibration.UNCALIBRATED
    for by_patient in determinations.by_strategy.values():
        for determination in by_patient.values():
            assert determination.calibration is Calibration.UNCALIBRATED


def test_claim_rejects_duplicate_sources() -> None:
    with pytest.raises(ValueError, match="one entry per source type"):
        SpecialtyClaim(
            specialty=Specialty.CARDIOLOGY,
            corroboration=(
                Corroboration(EvidenceSource.REFERRAL, 1.0),
                Corroboration(EvidenceSource.REFERRAL, 1.0),
            ),
        )


def test_claim_rejects_no_corroboration() -> None:
    """A claim nothing asserts is not a claim."""
    with pytest.raises(ValueError, match="at least one"):
        SpecialtyClaim(specialty=Specialty.CARDIOLOGY, corroboration=())


def test_determination_rejects_duplicate_specialties() -> None:
    claim = SpecialtyClaim.from_recognitions(
        Specialty.CARDIOLOGY, {EvidenceSource.REFERRAL: 1.0}
    )
    with pytest.raises(ValueError, match="one claim per specialty"):
        RequiredSpecialtyDetermination(
            patient=PATIENT,
            strategy=DEFAULT_STRATEGY,
            claims=(claim, claim),
        )


def test_corroboration_rejects_out_of_range_recognition() -> None:
    with pytest.raises(ValueError, match="recognition must be in"):
        Corroboration(EvidenceSource.REFERRAL, 1.5)


# ---------------------------------------------------------------------------
# Criterion 8 — confidence threshold: excluded and counted
# ---------------------------------------------------------------------------


def test_required_specialty_confidence() -> None:
    """Criterion 8: `required_specialties` carries confidence; entries below
    threshold are excluded from MDT analysis and counted."""
    mapper = SpecialtyMapper(aliases={"cardio": Specialty.CARDIOLOGY})
    determinations = derive_all(
        _evidence(referral=("cardio", "nephrology"), consult=("nephrology",)), mapper
    )
    determination = determinations.select(RequiredSpecialtyStrategyKey.REFERRAL)[PATIENT]

    # Both are required under `referral`; only one clears the default threshold.
    assert determination.specialties == {Specialty.CARDIOLOGY, Specialty.NEPHROLOGY}

    selection = determination.for_mdt()

    assert selection.threshold == pytest.approx(DEFAULT_MDT_CONFIDENCE_THRESHOLD)
    assert selection.included == {Specialty.NEPHROLOGY}
    assert selection.excluded == {Specialty.CARDIOLOGY}  # 0.85/3 < 1/3
    assert selection.excluded_count == 1
    # Nothing was dropped: the two sets partition the claims.
    assert selection.included | selection.excluded == determination.specialties


def test_default_threshold_admits_one_exact_source() -> None:
    """A stricter default would silently turn every strategy into
    `intersection` and take the definition away from the analyst."""
    determinations = derive_all(_evidence(referral=("cardiology",)))
    selection = determinations.select(RequiredSpecialtyStrategyKey.REFERRAL)[
        PATIENT
    ].for_mdt()

    assert selection.included == {Specialty.CARDIOLOGY}
    assert selection.excluded_count == 0


def test_raising_the_threshold_excludes_and_counts() -> None:
    determinations = derive_all(
        _evidence(referral=("cardiology", "nephrology"), consult=("nephrology",))
    )
    determination = determinations.select(RequiredSpecialtyStrategyKey.REFERRAL)[PATIENT]

    selection = determination.for_mdt(threshold=2 / 3)

    assert selection.included == {Specialty.NEPHROLOGY}
    assert selection.excluded == {Specialty.CARDIOLOGY}


def test_confidence_of_absent_specialty_is_zero() -> None:
    determinations = derive_all(_evidence(referral=("cardiology",)))
    determination = determinations.select(RequiredSpecialtyStrategyKey.REFERRAL)[PATIENT]

    assert determination.confidence_of(Specialty.ONCOLOGY) == 0.0


# ---------------------------------------------------------------------------
# Criterion 9 — all five strategies computed, none skippable
# ---------------------------------------------------------------------------


def test_all_strategies_computed() -> None:
    """Criterion 9: all five implementations are computed for every run; none
    can be skipped."""
    determinations = derive_all(_synthetic_evidence())

    assert set(determinations.by_strategy) == set(RequiredSpecialtyStrategyKey)
    assert len(determinations.by_strategy) == 5

    patients = determinations.patients()
    assert patients
    for key in RequiredSpecialtyStrategyKey:
        assert set(determinations.by_strategy[key]) == set(patients)


def test_strategy_determinations_refuses_an_incomplete_set() -> None:
    """The check lives on the type, so a caller assembling one by hand cannot
    skip it either."""
    full = derive_all(_synthetic_evidence())
    partial = {
        key: value
        for key, value in full.by_strategy.items()
        if key is not RequiredSpecialtyStrategyKey.INTERSECTION
    }

    with pytest.raises(IncompleteStrategySetError, match="intersection"):
        type(full)(by_strategy=partial, report=full.report)


def test_default_strategy_is_referral() -> None:
    """ "The default is `referral` ... so the flattering choice is an active
    decision, not the path of least resistance" (SPEC-001)."""
    assert DEFAULT_STRATEGY is RequiredSpecialtyStrategyKey.REFERRAL

    determinations = derive_all(_synthetic_evidence())
    assert determinations.select() == determinations.select(
        RequiredSpecialtyStrategyKey.REFERRAL
    )


# ---------------------------------------------------------------------------
# Criterion 11 — bounds
# ---------------------------------------------------------------------------


def test_strategy_bounds() -> None:
    """Criterion 11: `intersection ⊆ any single strategy ⊆ union` for every
    patient. Checked over noisy evidence, where the three sources genuinely
    disagree — on clean evidence all five coincide and the test proves nothing."""
    determinations = derive_all(
        _synthetic_evidence(
            EvidenceConfig(
                referral_recall=0.8,
                consult_note_recall=0.6,
                problem_list_recall=0.9,
                referral_stale_rate=0.05,
                problem_list_comorbidity_rate=0.1,
            )
        )
    )
    single_keys = (
        RequiredSpecialtyStrategyKey.REFERRAL,
        RequiredSpecialtyStrategyKey.CONSULT_NOTE,
        RequiredSpecialtyStrategyKey.PROBLEM_LIST,
    )

    disagreement_seen = False
    for patient in determinations.patients():
        lower = determinations.select(RequiredSpecialtyStrategyKey.INTERSECTION)[
            patient
        ].specialties
        upper = determinations.select(RequiredSpecialtyStrategyKey.UNION)[
            patient
        ].specialties
        assert lower <= upper
        for key in single_keys:
            middle = determinations.select(key)[patient].specialties
            assert lower <= middle, f"{key.value} violates the lower bound"
            assert middle <= upper, f"{key.value} violates the upper bound"
        if lower != upper:
            disagreement_seen = True

    assert disagreement_seen, "noise rates too low to exercise the bounds"


def test_bounds_coincide_on_clean_evidence_and_recover_ground_truth() -> None:
    """The oracle: at perfect recall with nothing spurious, all five strategies
    equal `GroundTruth.required_specialties` exactly."""
    _, truth = generate(SynthConfig(), Random(SEED))
    evidence = generate_evidence(truth, EvidenceConfig(), Random(SEED + 1))
    determinations = derive_all(evidence)

    for key in RequiredSpecialtyStrategyKey:
        recovered = {
            patient: determination.specialties
            for patient, determination in determinations.by_strategy[key].items()
        }
        assert recovered == truth.required_specialties, key.value


def test_bounds_are_flagged_degenerate_when_a_source_is_missing() -> None:
    """SPEC-001's extractability open question, made machine-readable rather
    than worked around."""
    determinations = derive_all(
        _evidence(referral=("cardiology",), consult=("cardiology",))
    )

    assert determinations.report.bounds_degenerate is True
    intersection = determinations.select(RequiredSpecialtyStrategyKey.INTERSECTION)
    assert intersection[PATIENT].specialties == frozenset()


# ---------------------------------------------------------------------------
# Criterion 10 — the artefact strategy key
# ---------------------------------------------------------------------------


def _artefact() -> ArtefactEnvelope:
    return to_artefact(derive_all(_synthetic_evidence()), METHOD_VERSION)


def test_artefact_strategy_key(tmp_path: Path) -> None:
    """Criterion 10: every artefact carries its strategy key; loading one
    without it raises."""
    envelope = _artefact()
    assert envelope.strategy is DEFAULT_STRATEGY

    path = tmp_path / "required_specialty.json"
    write_json(envelope, path)
    assert read_json(path) == envelope

    stripped = envelope.to_mapping()
    del stripped["strategy"]
    path.write_text(json.dumps(stripped), encoding="utf-8")

    with pytest.raises(MissingStrategyKeyError):
        read_json(path)


def test_artefact_rejects_a_null_strategy(tmp_path: Path) -> None:
    data = _artefact().to_mapping()
    data["strategy"] = None
    path = tmp_path / "a.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(MissingStrategyKeyError):
        read_json(path)


def test_artefact_rejects_an_unknown_strategy() -> None:
    data = _artefact().to_mapping()
    data["strategy"] = "whatever_looks_best"

    with pytest.raises(UnknownStrategyError):
        ArtefactEnvelope.from_mapping(data)


def test_artefact_rejects_an_incomplete_strategy_set() -> None:
    """ADR-0006 section 3, enforced at the file boundary where a rendering path
    cannot forget it."""
    data = _artefact().to_mapping()
    data["available_strategies"] = ["referral"]

    with pytest.raises(IncompleteStrategySetError):
        ArtefactEnvelope.from_mapping(data)


def test_artefact_rejects_a_missing_available_strategies_key() -> None:
    data = _artefact().to_mapping()
    del data["available_strategies"]

    with pytest.raises(IncompleteStrategySetError, match="available_strategies"):
        ArtefactEnvelope.from_mapping(data)


def test_artefact_rejects_an_unknown_calibration() -> None:
    data = _artefact().to_mapping()
    data["calibration"] = "definitely_validated_trust_me"

    with pytest.raises(ArtefactError, match="unknown calibration"):
        ArtefactEnvelope.from_mapping(data)


def test_every_strategy_can_be_selected_by_key() -> None:
    """The plumbing N14's selector needs: five keys in, five re-keyed envelopes
    out, all sharing one payload."""
    envelope = _artefact()

    for key in RequiredSpecialtyStrategyKey:
        assert envelope.with_strategy(key).strategy is key


def test_artefact_rejects_a_missing_calibration() -> None:
    data = _artefact().to_mapping()
    del data["calibration"]

    with pytest.raises(ArtefactError, match="calibration"):
        ArtefactEnvelope.from_mapping(data)


def test_artefact_rejects_a_missing_required_field() -> None:
    data = _artefact().to_mapping()
    del data["method_version"]

    with pytest.raises(ArtefactError, match="method_version"):
        ArtefactEnvelope.from_mapping(data)


def test_envelope_cannot_be_built_without_all_five() -> None:
    with pytest.raises(IncompleteStrategySetError):
        ArtefactEnvelope(
            kind=ARTEFACT_KIND,
            method_version=METHOD_VERSION,
            strategy=DEFAULT_STRATEGY,
            available_strategies=frozenset({DEFAULT_STRATEGY}),
            calibration=Calibration.UNCALIBRATED,
            payload={},
        )


def test_artefact_carries_the_method_version_and_calibration() -> None:
    """ADR-0006 sections 1 and 6."""
    envelope = _artefact()

    assert envelope.method_version == METHOD_VERSION
    assert envelope.calibration is Calibration.UNCALIBRATED
    assert envelope.kind == ARTEFACT_KIND


def test_artefact_records_the_mdt_threshold() -> None:
    """A figure produced at a stricter threshold is not comparable with one
    produced at the default, so the threshold travels with it."""
    envelope = to_artefact(
        derive_all(_synthetic_evidence()), METHOD_VERSION, mdt_threshold=2 / 3
    )

    assert envelope.payload["mdt_confidence_threshold"] == pytest.approx(2 / 3)


def test_with_strategy_rekeys_without_recomputing() -> None:
    """What makes N14's selector instant and the spread always available."""
    envelope = _artefact()
    rekeyed = envelope.with_strategy(RequiredSpecialtyStrategyKey.UNION)

    assert rekeyed.strategy is RequiredSpecialtyStrategyKey.UNION
    assert rekeyed.payload is envelope.payload
    assert rekeyed.available_strategies == envelope.available_strategies


def test_artefact_payload_holds_all_five_strategies() -> None:
    envelope = _artefact()
    stored = envelope.payload["determinations"]

    assert set(stored) == {key.value for key in RequiredSpecialtyStrategyKey}


def test_artefact_round_trips_to_typed_determinations() -> None:
    determinations = derive_all(
        _synthetic_evidence(EvidenceConfig(consult_note_recall=0.7))
    )
    restored = from_artefact(to_artefact(determinations, METHOD_VERSION))

    assert restored.by_strategy == determinations.by_strategy
    assert restored.report == determinations.report


def test_from_artefact_recomputes_confidence_rather_than_trusting_the_file() -> None:
    """A hand-edited `confidence` must not be able to raise a number: the
    corroboration is the evidence, the confidence is a view of it."""
    envelope = to_artefact(
        derive_all(_evidence(referral=("cardiology",))), METHOD_VERSION
    )
    data = envelope.to_mapping()
    data["payload"]["determinations"]["referral"][PATIENT.value][0]["confidence"] = 0.99

    restored = from_artefact(ArtefactEnvelope.from_mapping(data))
    determination = restored.select(RequiredSpecialtyStrategyKey.REFERRAL)[PATIENT]

    assert determination.confidence_of(Specialty.CARDIOLOGY) == pytest.approx(1 / 3)


def test_from_artefact_rejects_a_foreign_artefact_kind() -> None:
    envelope = _artefact()
    foreign = ArtefactEnvelope(
        kind="motion",
        method_version=envelope.method_version,
        strategy=envelope.strategy,
        available_strategies=envelope.available_strategies,
        calibration=envelope.calibration,
        payload=envelope.payload,
    )

    with pytest.raises(ArtefactError, match="required_specialty"):
        from_artefact(foreign)


def test_from_artefact_rejects_a_payload_missing_a_strategy() -> None:
    data = _artefact().to_mapping()
    del data["payload"]["determinations"]["union"]

    with pytest.raises(IncompleteStrategySetError, match="union"):
        from_artefact(ArtefactEnvelope.from_mapping(data))


def test_read_json_rejects_a_non_object(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ArtefactError, match="JSON object"):
        read_json(path)


# ---------------------------------------------------------------------------
# Gate 8 — determinism
# ---------------------------------------------------------------------------


def test_evidence_generation_is_deterministic() -> None:
    _, truth = generate(SynthConfig(), Random(SEED))
    config = EvidenceConfig(referral_recall=0.7, unrecognised_text_rate=0.2)

    assert generate_evidence(truth, config, Random(SEED)) == generate_evidence(
        truth, config, Random(SEED)
    )


def test_artefact_serialisation_is_byte_stable() -> None:
    """Two runs over the same evidence produce the same file, or gate 8 is
    checking something weaker than it claims to."""
    evidence = _synthetic_evidence(EvidenceConfig(problem_list_recall=0.8))
    first = to_artefact(derive_all(evidence), METHOD_VERSION).to_mapping()
    second = to_artefact(derive_all(evidence), METHOD_VERSION).to_mapping()

    assert json.dumps(first) == json.dumps(second)


def test_evidence_config_rejects_a_rate_outside_the_unit_interval() -> None:
    with pytest.raises(ValueError, match="referral_recall"):
        EvidenceConfig(referral_recall=1.5)


def test_n_evidence_sources_matches_the_source_enum() -> None:
    """The confidence denominator is the number of source types the model
    defines. If a fourth is added, this is the test that says so."""
    assert N_EVIDENCE_SOURCES == len(EvidenceSource) == 3
