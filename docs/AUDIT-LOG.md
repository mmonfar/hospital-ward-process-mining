# Audit log

Append-only record of every change to this repository: what was done, why, which
spec or ADR authorised it, and which model made it. Written by
`src/hwpm/govern/audit.py`. Do not edit past entries — corrections are appended
as new entries referencing the original.

---

## 2026-08-14 16:08:21Z — Established spec-driven foundation: docs, ADRs, orchestration graph, governance tooling

- **Why:** Brief items 1-7. A project intended to run largely autonomously across many sessions needs its intent, decisions and budget recorded outside the conversation, or each session re-derives them from code and drifts.
- **Authority:** ADR-0001; graph node N00-foundation
- **Graph node:** N00-foundation
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `a271524`
- **Artefacts:** `docs/`, `orchestration/graph.yaml`, `src/hwpm/govern/`, `refs/metaheuristics/SELECTION-GUIDE.md`
- **Evidence:**

  ```
  20 passed
  ```

## 2026-08-14 16:08:21Z — Parsed Essentials of Metaheuristics to page-anchored markdown; indexed 139 algorithms

- **Why:** Requested as the basis for optimiser selection. Parsed via a reproducible tool rather than by hand so the reference can be regenerated and audited. Full text is git-ignored: CC BY-ND forbids redistributing a transformed copy.
- **Authority:** SELECTION-GUIDE.md; refs/metaheuristics/README.md
- **Graph node:** N00-foundation
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `a271524`
- **Artefacts:** `tools/parse_reference_pdf.py`, `refs/metaheuristics/ALGORITHM-INDEX.md`
- **Evidence:**

  ```
  pages=264 algorithms=139
  ```

## 2026-08-14 16:08:21Z — Recalibrated session token budget from 400k/700k to 2M/3M

- **Why:** The guessed limits scored a healthy 83-turn session at 184% of the hard limit on the ledger's first real run. A budget that a normal session exceeds is one everyone learns to ignore, which would defeat the monitoring the brief asked for. Reset against the observation; re-calibrate once ~10 sessions of history exist rather than tuning on a sample of one.
- **Authority:** docs/04-AGENT-ORCHESTRATION.md
- **Graph node:** N00-foundation
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `a271524`
- **Artefacts:** `orchestration/graph.yaml`
- **Evidence:**

  ```
  observed: 1,284,873 weighted tokens for a healthy session
  ```

## 2026-08-14 16:20:08Z — Resolved N04 gate: RequiredSpecialty is a runtime choice selectable in the front end

- **Why:** User decision. Changes pipeline shape: the definition moves from an ingestion-time constant to a parameter of every downstream analysis, so all five strategies are always computed and every artefact is keyed by strategy. Introduces a gaming surface on the coverage denominator, mitigated by always displaying the spread and defaulting to the most conservative source.
- **Authority:** SPEC-001; ADR-0006
- **Graph node:** N04-required-specialty
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `e0f7706`
- **Artefacts:** `docs/specs/SPEC-001-ingestion.md`, `orchestration/graph.yaml`
- **Evidence:**

  ```
  N04 gate changed confirm -> autonomous; budget 120k -> 180k
  ```

## 2026-08-14 16:20:08Z — Recorded Clinical Governance as monitoring stakeholder; added ADR-0006 and node N18

- **Why:** User confirmed the goal is a majority of MDT rounds, monitored by Clinical Governance. This makes MDT coverage a governance measurement rather than a study output, inheriting obligations around method versioning, gaming resistance and provenance that a study does not have.
- **Authority:** ADR-0006; 00-VISION.md
- **Graph node:** N18-governance-view
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `e0f7706`
- **Artefacts:** `docs/adr/ADR-0006-governance-monitored-metric.md`, `docs/00-VISION.md`
- **Evidence:**

  ```
  20 nodes, graph valid
  ```

## 2026-08-14 16:20:08Z — Raised the front-end design bar to normative; split out N14a-design-system

- **Why:** User requires front-end quality matching the analysis. In a project whose output will be challenged by senior clinicians the interface is part of the evidence. Design system moved early because retrofitting a visual system onto built screens yields a themed prototype, not a coherent product.
- **Authority:** SPEC-005
- **Graph node:** N14a-design-system
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `e0f7706`
- **Artefacts:** `docs/specs/SPEC-005-visualisation.md`
- **Evidence:**

  ```
  8 normative design requirements, 11 acceptance criteria
  ```

## 2026-08-14 16:20:08Z — CONSOLIDATE: session ended at 89% of token budget

- **Why:** Budget threshold 0.80 reached. Per docs/05-SELF-MANAGED-MODE.md, stop starting new nodes and end at a resumable boundary rather than risk running out mid-node and leaving half-built code. Next session starts fresh at N01/N02/N14a.
- **Authority:** docs/05-SELF-MANAGED-MODE.md; orchestration/graph.yaml budget.thresholds
- **Graph node:** N00-foundation
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `e0f7706`
- **Evidence:**

  ```
  billable 2,676,107 of 3,000,000 (89%) - action CONSOLIDATE
  ```

## 2026-08-14 16:34:01Z — N02: implemented frozen domain core (Location, Event, Trajectory, Patient, Clinician, Visit, etc.)

- **Why:** Substrate every later analysis reads; ADR-0001 requires the spec before the code, and SPEC-001 covers it
- **Authority:** SPEC-001
- **Graph node:** N02-domain-core
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `c8b96f7`
- **Artefacts:** `src/hwpm/domain/model.py`, `src/hwpm/domain/__init__.py`, `tests/test_domain.py`, `docs/01-DOMAIN-MODEL.md`
- **Evidence:**

  ```
  ; ruff check src/hwpm/domain: clean; mypy src/hwpm/domain: clean; lint-imports: 2 kept, 0 broken; coverage domain/: 100%
  ```

## 2026-08-14 16:34:34Z — N01: implemented synthetic ward event-log generator with known ground truth

- **Why:** ADR-0005 forbids agents touching real data; fixtures are the only substrate development can happen on until N03/N17
- **Authority:** SPEC-001
- **Graph node:** N01-synthetic-fixtures
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `c8b96f7`
- **Artefacts:** `src/hwpm/ingest/synthetic.py`, `tests/test_synthetic.py`
- **Evidence:**

  ```
  .............                                                            [100%]; ruff check src/hwpm/ingest: clean; coverage ingest/synthetic.py: 99%; vulture: no findings; determinism: generate(config, Random(42)) equal across two runs, and with noise enabled
  ```
