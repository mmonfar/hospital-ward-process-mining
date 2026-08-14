# SPEC-002 — Process discovery, conformance, and episode derivation

**Status:** accepted · **Nodes:** N05, N16 · **Owner role:** builder (N16: architect)
**Depends on:** SPEC-001 · **Last revised:** 2026-08-14

## Problem

Recover what ward rounds *actually* look like from the event stream: derive
`BedsideEpisode`s from raw location events, discover the round process, and
measure how far reality departs from the intended process.

## In scope

- `BedsideEpisode` derivation (dwell-time segmentation).
- `Round` reconstruction per clinician per day.
- Process discovery via `pm4py` (inductive miner; heuristics miner as comparator).
- Conformance checking against a stated intended process.
- `MDTMoment` detection, including the **opportunistic** case.
- Materials for clinician face-validity review (N16).

## Out of scope

- Distance and motion waste → SPEC-003.
- Counterfactual scheduling → SPEC-004.
- Writing our own discovery algorithm. We use `pm4py`; our contribution is the
  ward-motion layer.

## Licence constraint (ADR-0008)

**`pm4py` is AGPL v3.** All pm4py imports must live in exactly one adapter
module, `src/hwpm/mining/_pm4py_adapter.py`, behind the `ProcessDiscovery` and
`ConformanceChecker` Protocols. Nothing else may import it; an import-linter
`forbidden` contract enforces this.

Until the licensing question in ADR-0008 is answered by the organisation,
discovery and conformance run **offline as batch analysis producing static
artefacts** — no network-served deployment of anything downstream of pm4py.

## Interface

```python
# hwpm.mining
@dataclass(frozen=True) class EpisodeParams:
    min_dwell_s: int = 120        # below this: passing through, not a visit
    max_gap_s: int = 90           # gap tolerated within one episode
    min_confidence: float = 0.8

def derive_episodes(traj: Trajectory, params: EpisodeParams) -> list[BedsideEpisode]: ...
def reconstruct_rounds(episodes, day: date) -> list[Round]: ...
def discover(log: EventLog, algorithm: str = "inductive") -> ProcessModel: ...
def conformance(log: EventLog, model: ProcessModel) -> ConformanceReport: ...

def detect_mdt_moments(
    episodes: list[BedsideEpisode],
    required: dict[PatientId, frozenset[Specialty]],
    window_s: int = 300,
) -> list[MDTMoment]: ...

def detect_opportunistic(
    trajectories, episodes, required, proximity_m: float = 15.0, window_s: int = 300,
) -> list[MissedMDTOpportunity]: ...
```

## `detect_opportunistic` — the surgeon who walked past

This is the concrete case in the brief: a surgical registrar physically passes
through a ward containing one of their patients, and the joint review does not
happen because nobody knew they were there.

Definition: a `MissedMDTOpportunity` exists when, within `window_s`, a clinician
of specialty *S* was within `proximity_m` of a bed whose patient required *S*,
and no `BedsideEpisode` for that pair occurred.

This is the highest-value output of the whole project, because unlike a proposed
schedule it requires **no organisational change to act on** — it is a
notification. It is also the easiest to get wrong: proximity is not intent, and a
registrar walking to theatre is not an available reviewer. The output must
therefore be reported as *candidate* opportunities with an explicit false-positive
rate measured against clinician review, never as "missed reviews".

## Modelling assumptions

| Assumption | Default | Risk |
|---|---|---|
| Dwell ≥120 s at a bed = a clinical visit | 120 s | Under-counts brief reviews, over-counts a clinician standing near a bed writing notes. **Sensitivity analysis across 60–300 s is mandatory**, and any headline figure must be shown to be stable across that range or reported with the range. |
| Gaps <90 s within an episode are sensor noise | 90 s | Merges genuinely separate short visits. |
| Co-presence within 300 s = joint review | 300 s | Two teams at one bed 4 minutes apart are not an MDT. Validated in N16. |
| Proximity 15 m = "passing by" | 15 m | Floor-plan dependent; must use the travel graph (SPEC-003), not Euclidean distance. |

Every one of these is a **tunable parameter with a defensible default, not a
fact**. Results are reported with the parameter values that produced them.

## Acceptance criteria

1. Episode derivation on synthetic data recovers ≥95% of generated visits at default params. — `test_episode_recall`
2. Sensitivity analysis over `min_dwell_s` ∈ {60,120,180,300} is produced automatically for every report. — `test_sensitivity_emitted`
3. Discovered model on synthetic data matches the generating process (fitness ≥0.9, precision ≥0.8). — `test_discovery_ground_truth`
4. `detect_mdt_moments` finds exactly the co-presences the generator created; no false positives on ground truth. — `test_mdt_exact`
5. `detect_opportunistic` reports candidates with a confidence and never emits a candidate for a clinician whose trajectory shows continuous transit. — `test_opportunistic_transit_excluded`
6. Conformance report distinguishes *deviation* from *missing data*. — `test_conformance_missing_vs_deviation`
7. All parameters appear in the report header. — `test_report_provenance`

## Test oracle

The synthetic generator's ground truth for 1–6. For face validity (N16), review
by clinicians who work the ward — which no agent can perform or approximate.

## Failure modes

- **The discovered model is technically good and clinically unrecognisable.**
  The most likely outcome, and success criterion 1 in `00-VISION.md` exists to
  catch it. A model with excellent fitness that clinicians do not recognise is a
  failure, not a communication problem.
- **Opportunistic detection over-reports**, the tool cries wolf, and clinicians
  stop trusting it. Mitigated by measuring the false-positive rate before
  reporting anything, and by reporting candidates rather than misses.
- **Parameter tuning to a desired answer.** Prevented by criterion 2: the
  sensitivity analysis is emitted whether or not anyone asked for it.

## Open questions

- **[blocking, user]** What *is* the intended round process here? Conformance
  requires a stated norm, and there may not be a documented one — in which case
  the honest output is discovery only, with no conformance claim.
- **[non-blocking]** Is nursing task timing available? Needed for the disruption
  objective in SPEC-004.
