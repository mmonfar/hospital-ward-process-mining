# SPEC-001 — Domain core, synthetic fixtures, and event-log ingestion

**Status:** accepted · **Nodes:** N01, N02, N03, N04 · **Owner role:** builder (N04: architect)
**Last revised:** 2026-08-14

## Problem

Turn heterogeneous hospital exports (RTLS badge feeds, EPR audit trails, manual
observation sheets) into a canonical, immutable, pseudonymous `Event` stream over
a modelled ward geometry — the substrate every later analysis reads.

## In scope

- Frozen domain types from `01-DOMAIN-MODEL.md`.
- A synthetic ward event-log generator with configurable, *known* ground truth.
- CSV and XES readers; a location-string → `LocationId` mapper.
- Pseudonymisation at the boundary.
- Derivation of `Patient.required_specialties` with a confidence value (N04).

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
