# SPEC-001 — Domain core, synthetic fixtures, and event-log ingestion

**Status:** accepted · **Nodes:** N01, N02, N03, N04 · **Owner role:** builder (N04: architect)
**Last revised:** 2026-08-15 (N04: confidence model, strategy membership rules,
artefact envelope)

## Problem

Turn heterogeneous hospital exports (RTLS badge feeds, EPR audit trails, manual
observation sheets) into a canonical, immutable, pseudonymous `Event` stream over
a modelled ward geometry — the substrate every later analysis reads.

## In scope

- Frozen domain types from `01-DOMAIN-MODEL.md`.
- A synthetic ward event-log generator with configurable, *known* ground truth.
- CSV and XES readers; a location-string → `LocationId` mapper.
- Pseudonymisation at the boundary.
- Derivation of `RequiredSpecialty` with a confidence value (N04). **Corrected
  2026-08-15:** this bullet previously read "`Patient.required_specialties`",
  which contradicts the 2026-08-14 amendment to `01-DOMAIN-MODEL.md` and the
  resolution below — it is not a `Patient` field, it is an analysis-time
  determination over persisted evidence. The code was right and this line was
  stale; the line is fixed rather than the code (ADR-0001: "if code and spec
  disagree, that is a defect — fix one or amend the other").

## Out of scope

- Process discovery and episode derivation → SPEC-002.
- Travel cost → SPEC-003.
- Live feeds. Batch file ingestion only.
- Any connection to a real hospital system. Files in, files out.

## Interface

```python
# hwpm.domain
@dataclass(frozen=True) class LocationId: value: str
@dataclass(frozen=True) class Location: id: LocationId; kind: LocationKind; point: Point
@dataclass(frozen=True) class Event:
    timestamp: datetime; subject: SubjectId; activity: str
    location: LocationId; source: str; confidence: float = 1.0
class Trajectory:  # ordered events for one subject
    def transitions(self) -> Iterator[Transition]: ...

# hwpm.ingest
class EventReader(Protocol):
    def read(self, path: Path) -> Iterator[Event]: ...
class LocationMapper:
    def map(self, raw: str) -> tuple[LocationId, float]:  # id + confidence
        ...
def pseudonymise(events, salt: bytes) -> Iterator[Event]: ...

# hwpm.ingest.synthetic
@dataclass(frozen=True) class GroundTruth:
    schedules: dict[ClinicianId, list[Visit]]
    required_specialties: dict[PatientId, frozenset[Specialty]]
    total_distance_m: dict[ClinicianId, float]
def generate(config: SynthConfig, rng: Random) -> tuple[list[Event], GroundTruth]: ...
def generate_evidence(
    truth: GroundTruth, config: EvidenceConfig, rng: Random
) -> RequiredSpecialtyEvidence: ...

# hwpm.domain — RequiredSpecialty result types (N04). Frozen, no I/O; they are
# the *result* of a strategy, so they live in the domain, while the Protocol and
# the five implementations live beside the evidence they read.
class EvidenceSource(Enum):  # REFERRAL | CONSULT_NOTE | PROBLEM_LIST
class RequiredSpecialtyStrategyKey(Enum):
    # REFERRAL | CONSULT_NOTE | PROBLEM_LIST | UNION | INTERSECTION
class Calibration(Enum):  # UNCALIBRATED | CLINICIAN_VALIDATED
@dataclass(frozen=True) class Corroboration:
    source: EvidenceSource; recognition: float
@dataclass(frozen=True) class SpecialtyClaim:
    specialty: Specialty; corroboration: tuple[Corroboration, ...]
    @property
    def confidence(self) -> float: ...       # see "The confidence model"
@dataclass(frozen=True) class MDTSelection:
    threshold: float; included: frozenset[Specialty]; excluded: frozenset[Specialty]
@dataclass(frozen=True) class RequiredSpecialtyDetermination:
    patient: PatientId; strategy: RequiredSpecialtyStrategyKey
    claims: tuple[SpecialtyClaim, ...]; calibration: Calibration
    def for_mdt(self, threshold: float = ...) -> MDTSelection: ...

# hwpm.ingest.specialty — the five strategies (N04)
class SpecialtyMapper:
    def map(self, raw: str) -> tuple[Specialty | None, float]:  # None => quarantined
        ...
class RequiredSpecialtyStrategy(Protocol):
    @property
    def key(self) -> RequiredSpecialtyStrategyKey: ...
    def derive(
        self, index: EvidenceIndex, patient: PatientId
    ) -> RequiredSpecialtyDetermination: ...
def index_evidence(
    evidence: RequiredSpecialtyEvidence, mapper: SpecialtyMapper | None = None
) -> EvidenceIndex: ...
def derive_all(
    evidence: RequiredSpecialtyEvidence, mapper: SpecialtyMapper | None = None
) -> StrategyDeterminations: ...          # all five, always; criterion 9
def to_artefact(
    determinations: StrategyDeterminations, method_version: str, *,
    strategy: RequiredSpecialtyStrategyKey = DEFAULT_STRATEGY,
    mdt_threshold: float = DEFAULT_MDT_CONFIDENCE_THRESHOLD,
) -> ArtefactEnvelope: ...
def from_artefact(envelope: ArtefactEnvelope) -> StrategyDeterminations: ...

# hwpm.artefact — the strategy-keyed envelope every stage writes (N04)
@dataclass(frozen=True) class ArtefactEnvelope:
    kind: str; method_version: str
    strategy: RequiredSpecialtyStrategyKey
    available_strategies: frozenset[RequiredSpecialtyStrategyKey]
    calibration: Calibration; payload: Mapping[str, Any]; schema_version: int
    def with_strategy(self, key: RequiredSpecialtyStrategyKey) -> ArtefactEnvelope: ...
def write_json(envelope: ArtefactEnvelope, path: Path) -> None: ...
def read_json(path: Path) -> ArtefactEnvelope: ...   # raises MissingStrategyKeyError
```

`generate` returning ground truth alongside events is the design decision that
makes the rest of the project testable. Every downstream analysis can be checked
against what actually happened rather than against a previous run of itself.

## Modelling assumptions

| Assumption | Default | Rationale / risk |
|---|---|---|
| Badge read = clinician present at that location | — | RTLS reads are noisy and can fire through walls. Mitigated by a dwell threshold in SPEC-002. |
| Location strings map 1:1 to modelled locations | — | Real exports have free text ("bay 3", "Bay3", "B3"). Mapper returns a confidence; anything below 0.8 is quarantined, never silently guessed. |
| Timestamps are ward-local and monotonic per subject | — | Clock skew across systems is real. Non-monotonic sequences are flagged, not reordered. |
| Walking speed | 1.2 m/s | Literature value for adult indoor walking; calibrated later (P3). |
| Round-window ordering by acuity | sickest first | Clinician-stated norm; validate against observed order in SPEC-002. |

## `RequiredSpecialty` — the critical field (N04)

**Resolved 2026-08-14: the definition is a runtime choice, selectable in the
front end, not a build-time constant.**

`01-DOMAIN-MODEL.md` flags this as the most consequential derived field: every
MDT result inherits its error. The candidate sources, none clean:

| Strategy | Basis | Bias |
|---|---|---|
| `referral` | Active referral records | Precise, but teams disengage without closing the referral — over-counts |
| `consult_note` | Consult notes authored during the stay | Reflects real involvement; needs NLP; under-counts verbal advice |
| `problem_list` | Problem list mapped to specialty | Broad; over-triggers on historical comorbidity |
| `union` / `intersection` | Combinations | Upper and lower bounds on the true set |

### Architectural consequence

Making this selectable is not a UI feature — it changes the pipeline shape. The
definition moves from a constant fixed at ingestion to a **parameter of every
downstream analysis**, which means:

1. `RequiredSpecialtyStrategy` is a Protocol with the implementations above,
   resolved at analysis time rather than baked into the event store.
2. **All artefacts are keyed by strategy.** `motion.json`, `front.json` and every
   MDT figure carry the strategy that produced them. An artefact without a
   strategy key is invalid and must fail to load.
3. The ingestion stage persists the *evidence* (referrals, notes, problem list)
   rather than the *conclusion*, so switching strategy does not require re-ingest.
4. Analysis must be cheap enough to re-run per strategy, or pre-computed for all
   of them. Pre-computing all is preferred — there are five, and it makes the
   comparison in the next section free.

### The incentive problem this creates

A selectable definition means the definition that produces the most flattering
number can be selected. With Clinical Governance monitoring MDT coverage as a
performance measure (`SPEC-002`), there is now a live incentive to do exactly
that — and it would happen through ordinary optimism, not bad faith.

**Mitigations, all mandatory:**

- **Every strategy is always computed, and the front end always displays the
  spread**, not only the selected one. Selecting a strategy changes which is
  emphasised, never which are available.
- The **`union` and `intersection` strategies are always shown as bounds**, so
  the plausible range is visible whatever is chosen.
- Every exported figure and screenshot carries the strategy in its caption. A
  number that can travel without its definition will.
- The default is **`referral`** — the most conservative widely-available source —
  so the flattering choice is an active decision, not the path of least
  resistance.

### Strategy membership rules (N04, added 2026-08-15)

For a patient `p` and a `Specialty` `s`, let `T(p, s)` be the set of evidence
**source types** — `referral`, `consult_note`, `problem_list` — that assert `s`
for `p` with a *recognised* specialty text (see "Specialty text recognition").

| Strategy key | `s` is a member iff |
|---|---|
| `referral` | `referral ∈ T(p, s)` |
| `consult_note` | `consult_note ∈ T(p, s)` |
| `problem_list` | `problem_list ∈ T(p, s)` |
| `union` | `T(p, s) ≠ ∅` |
| `intersection` | `T(p, s)` = all three source types |

**Membership is decided by the selected strategy; confidence is always computed
over all three sources.** This separation is deliberate and is what makes
`referral` an honest *definition* rather than a weak one: choosing `referral`
selects which patients and specialties count, not which evidence is looked at.
A confidence that only looked at the selected source could not distinguish a
lone referral from a referral that three independent sources agree with, and
that distinction is the whole point of carrying a confidence at all.

Criterion 11 (`intersection ⊆ any single strategy ⊆ union`) holds by
construction from this table, not by a runtime check — but it is asserted as a
test anyway, because "holds by construction" is a claim about code that changes.

### The confidence model (N04, added 2026-08-15)

Every claim carries a confidence. Two things it must not be: a probability
nobody has measured, and a number that makes a lone referral look like three
sources agreeing.

**Definition.**

```
confidence(p, s) = ( Σ  recognition(t, p, s) ) / 3
                    t ∈ T(p, s)
```

where `recognition(t, p, s) ∈ [0.8, 1.0]` is the `SpecialtyMapper`'s confidence
that source `t`'s raw text denotes `s`, and the denominator **3** is the number
of source types this spec defines — a constant of the model, never the number of
source types a particular extract happened to contain.

Read it as: *the number of independent source types that corroborate this
claim, discounted by how confidently each one's free text was recognised,
expressed as a fraction of the three sources the model knows about.*

Both terms are **counted, not assumed**. There is no per-source prior, no
weighting of "referrals are 0.7 reliable", no fitted coefficient. Every number
that enters is either a count or a mapper output that is itself a match/no-match
decision. This is the property that makes the value defensible in front of a
consultant who disputes it: each contribution can be traced to one record.

| Evidence for `s` | confidence |
|---|---|
| One source, exact text | 0.333 |
| One source, matched only by a site alias table (0.85) | 0.283 |
| Two sources, exact text | 0.667 |
| Three sources, exact text | 1.000 |

**The fixed denominator is a design choice with a consequence, and the
consequence is intended.** If an extract contains only referrals, every claim in
it has confidence 0.333 and nothing can ever reach 1.0. That is the correct
signal — nothing in that extract is corroborated — and it is preferable to the
alternative of dividing by "the sources we happened to get", which would award
1.0 to a completely uncorroborated claim and quietly redefine certainty as
"unanimity among a sample of one". It also makes SPEC-001's open question about
extractability visible in the numbers rather than only in a footnote.

**What the number is not.** It is not the probability that the specialty is
genuinely required. No such probability can be stated until the clinician review
below exists. Every `RequiredSpecialtyDetermination` therefore carries a
`Calibration` value, `UNCALIBRATED` until that review has happened, and every
artefact carries it too. A figure derived from `UNCALIBRATED` determinations may
be explored; under ADR-0006 it may not be published as a governance measurement.

**The MDT threshold (criterion 8).** `DEFAULT_MDT_CONFIDENCE_THRESHOLD = 1/3`.
Claims below it are excluded from MDT analysis and counted, never silently
dropped — `for_mdt()` returns the included set, the excluded set and the
threshold together. The default is set at exactly one exact-text source so that
a single-source claim under a single-source strategy passes: a stricter default
would silently convert every strategy into `intersection` and take the
definition away from the analyst who selected it. Raising the threshold is a
legitimate, and recorded, analysis choice — it appears in the artefact payload,
so a figure produced at a stricter threshold cannot be compared with one
produced at the default without the difference being visible.

Comparison is `confidence >= threshold - 1e-12`; the tolerance is there because
both sides are floating-point sums of the same magnitudes and an exact `>=` on
`1/3` is a rounding accident waiting to happen.

### Specialty text recognition (N04, added 2026-08-15)

`ingest/evidence.py` deliberately persists `specialty_text` as raw text. Mapping
it to a `Specialty` is the same problem as mapping a location string, and gets
the same answer (criterion 5's rule, applied to a second field):

- `SpecialtyMapper.map(raw)` returns `(Specialty, confidence)` or
  `(None, 0.0)`.
- Case, surrounding whitespace, and `_`/`-`/space differences are
  **normalisation, not guessing** — they map at confidence 1.0.
- A site-supplied alias table (`"cardio"`, `"renal medicine"`) maps at
  `fuzzy_confidence`, default 0.85. **The default alias table is empty.**
  Inventing clinical synonyms in this repository would be exactly the silent
  mis-mapping the spec names as its worst failure mode; alias tables are site
  configuration and arrive with the site.
- Anything else is unrecognised: `RECOGNITION_THRESHOLD = 0.8` (the same
  threshold as locations), the raw text is preserved and counted in a
  `SpecialtyEvidenceReport`, and it contributes to no claim under any strategy.

### The artefact envelope (N04, added 2026-08-15)

Criterion 10 and ADR-0006 §2 require that the denominator travels with the
number. `hwpm.artefact.ArtefactEnvelope` is that mechanism, and it is a separate
package from `ingest` because every stage — mining, analytics, optimisation, the
viewer — writes one.

An envelope carries `kind`, `schema_version`, `method_version` (ADR-0006 §1),
`strategy`, `available_strategies`, `calibration` and an opaque `payload`.
Loading fails, rather than warning, when:

- `strategy` is absent → `MissingStrategyKeyError` (criterion 10);
- `strategy` is not one of the five → `UnknownStrategyError`;
- `available_strategies` is absent or is not all five →
  `IncompleteStrategySetError` (criterion 9's "none can be skipped", and
  ADR-0006 §3's "always computed, always displayed as a spread", enforced at the
  file boundary where it cannot be forgotten).

`with_strategy(key)` re-keys an envelope without recomputation, which is what
lets the front-end selector (N14) switch definitions instantly: all five
determinations are already in the payload.

**Degenerate bounds.** If a source type is absent from an extract entirely,
`intersection` is empty for every patient and the lower bound conveys nothing.
The payload records `available_sources` and `bounds_degenerate` so a consumer
must show that the bound is uninformative rather than presenting an empty set as
a finding. This is SPEC-001's non-blocking open question about extractability,
made machine-readable instead of worked around.

### Validation requirement (unchanged)

Whatever strategy is chosen, the field carries a **confidence**, and the chosen
strategy is **validated against clinician review of a random sample of ≥50
patients** before any MDT result built on it is reported. Reporting an
unvalidated MDT coverage rate remains the project's most likely serious error.

## Acceptance criteria

1. `Event` and all domain value objects are frozen; mutation raises. — `test_domain_immutable`
2. `hwpm.domain` imports nothing from `ingest`, `mining`, `optimize`, `govern`. — import-linter, gate 6
3. `generate(config, Random(42))` is byte-identical across runs and platforms. — `test_determinism`
4. Ground truth round-trips: events → ingestion → recovered distance within 1% of `GroundTruth.total_distance_m`. — `test_synthetic_roundtrip`
5. Unmappable location strings are quarantined with their raw value, never dropped and never guessed. — `test_location_quarantine`
6. No pseudonymised output contains any source identifier. — `test_pseudonymisation`
7. Out-of-order and duplicate events are flagged and counted in an ingestion report, not silently fixed. — `test_ingest_report`
8. `required_specialties` carries confidence; entries below threshold are excluded from MDT analysis and counted. — `test_required_specialty_confidence`
9. All five `RequiredSpecialtyStrategy` implementations are computed for every run; none can be skipped. — `test_all_strategies_computed`
10. Every artefact carries its strategy key; loading one without it raises. — `test_artefact_strategy_key`
11. `intersection ⊆ any single strategy ⊆ union` holds for every patient. — `test_strategy_bounds`

## Test oracle

The synthetic generator. It knows the schedules, distances, and required
specialties it produced, so ingestion correctness is checked against truth rather
than against itself. Real-data behaviour has no oracle — hence N16 and N17.

## Failure modes

- **Silent location mis-mapping** — the worst case, since it corrupts every
  distance downstream while looking healthy. Hence criterion 5: quarantine, never
  guess.
- **Pseudonymisation salt committed** to the repo. Salt lives in `HWPM_DATA_DIR`,
  git-ignored, and a pre-commit check greps for it.
- **Synthetic data too clean**, so ingestion passes on fixtures and fails on real
  exports. Mitigation: the generator must inject clock skew, duplicate reads,
  missing locations, and free-text location variants at configurable rates.

## Open questions

- ~~**[blocking, user]** Which source defines `RequiredSpecialty`?~~ **Resolved
  2026-08-14:** selectable at runtime in the front end, all strategies always
  computed, `referral` as default. See above.
- **[non-blocking]** Are referral records, consult notes and problem lists all
  extractable? If only one is, the selector degrades to a single option and the
  bounds cannot be shown — which weakens the mitigation above and should be
  reported as a limitation rather than worked around.
- **[non-blocking]** Is RTLS available, or is the event log EPR-derived only?
  Changes the noise model but not the interface.
- **[non-blocking]** Real ward geometry: are floor plans available, or do we stay
  on the prototype's idealised layout?
