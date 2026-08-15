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

## 2026-08-14 16:53:18Z — CONSOLIDATE: session ended at 84% of token budget, no node started

- **Why:** Budget threshold 0.80 reached immediately after N01/N02 landed, before any new node could be selected. Per docs/05-SELF-MANAGED-MODE.md, do not start a new node past this threshold -- end at a resumable boundary instead of burning the remaining ~490k mid-node.
- **Authority:** docs/05-SELF-MANAGED-MODE.md; orchestration/graph.yaml budget.thresholds
- **Graph node:** N00-foundation
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `7ff8d59`
- **Evidence:**

  ```
  billable 2,510,903 of 3,000,000 (84%) - action CONSOLIDATE. Runnable and untouched: N03-ingestion, N06-travel-graph, N14a-design-system. Next session should start with N03-ingestion (unblocks the most downstream work: N04, N05).
  ```

## 2026-08-14 16:55:56Z — N03-ingestion complete: readers, location mapper, pseudonymisation, ingestion report, evidence store

- **Why:** SPEC-001 acceptance criteria 4-7. Location quarantine rather than guessing implements criterion 5, which guards the worst failure mode: silent mis-mapping corrupts every downstream distance while looking healthy. Evidence containers hold raw specialty_text rather than resolved Specialty so N04's five strategies share raw material and nothing about strategy selection is pre-decided.
- **Authority:** SPEC-001
- **Graph node:** N03-ingestion
- **Model:** claude-sonnet-5
- **Actor:** marti
- **Commit:** `9e9699b`
- **Artefacts:** `src/hwpm/ingest/`, `tests/test_ingest.py`
- **Evidence:**

  ```
  56 tests passing; ruff clean; layering PASS
  ```

## 2026-08-14 16:55:57Z — N06-travel-graph complete: routed TravelGraph replaces Euclidean distance

- **Why:** SPEC-003. Straight-line distance systematically understates inter-floor movement, which is exactly the movement asynchronous rounds generate, and would have biased the headline finding toward 'there is no problem'. Dijkstra weighted by seconds because that is what a clinician optimises when choosing lift versus stairs. Lift wait is a constructor parameter, not a constant, so calibration is a call-site change.
- **Authority:** SPEC-003
- **Graph node:** N06-travel-graph
- **Model:** claude-sonnet-5
- **Actor:** marti
- **Commit:** `9e9699b`
- **Artefacts:** `src/hwpm/domain/travel.py`, `tests/test_travel.py`
- **Evidence:**

  ```
  test_interfloor_cost, test_metric_properties passing; 45s default lift wait flagged as unmeasured assumption
  ```

## 2026-08-14 16:55:57Z — SPEC-007 and ADR-0007: vector retrieval specced as two separate systems; nodes N19 and N20 added

- **Why:** User asked for vectors for context efficiency. Two valid readings, both specced: repo retrieval (touches no patient data) and trace embeddings (touches nothing else). Kept physically separate with opposite governance profiles. fastembed + sqlite-vec chosen over sentence-transformers which pulls ~2.5GB of PyTorch for weekly work. Gated on measured retrieval quality per the ADR-0003 pattern: if grep already scores above 0.70 recall@5, nothing is built.
- **Authority:** SPEC-007; ADR-0007
- **Graph node:** N19-context-retrieval
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `9e9699b`
- **Artefacts:** `docs/specs/SPEC-007-vector-retrieval.md`, `docs/adr/ADR-0007-vector-context-retrieval.md`
- **Evidence:**

  ```
  corpus measured at 1.12MB; size gate already met, quality gate unmeasured
  ```

## 2026-08-14 16:55:57Z — Environment: created project venv; global install of pm4py/ortools broke and then repaired scikit-learn

- **Why:** Installing pm4py and ortools into global Python upgraded numpy 1.26->2.4 and pandas 2.2->3.0, breaking scikit-learn by binary incompatibility. Repaired by upgrading scikit-learn to 1.9.0. This should have been an isolated environment from the start; a project venv now exists so no future install can affect the user's other projects. Recorded because it is a real side effect on the user's machine, not only on this repository.
- **Authority:** user decision 2026-08-14
- **Graph node:** N00-foundation
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `9e9699b`
- **Evidence:**

  ```
  pandas 3.0.5 remains a major-version change affecting the user's other projects
  ```

## 2026-08-14 17:03:10Z — ADR-0008: pm4py is AGPL v3; confined to one adapter and kept off the network

- **Why:** Discovered on install, after the dependency was already chosen and recommended to the user - a licence check should have preceded selection. AGPL section 13 extends copyleft to network interaction, and SPEC-005 contemplates hosting the governance view, so this sits directly on the project's likely deployment path. Mitigation is optionality, not a legal firewall: an import-linter contract confines pm4py to hwpm.mining._pm4py_adapter so swapping it is a one-file change. Hosting anything downstream of discovery is blocked until the organisation answers.
- **Authority:** ADR-0008
- **Graph node:** N05-mining
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `d573a81`
- **Artefacts:** `docs/adr/ADR-0008-pm4py-agpl-isolation.md`, `pyproject.toml`
- **Evidence:**

  ```
  contract caught a real violation on first run: hwpm.ingest.reader imported pm4py; removed. 3 contracts kept, 0 broken
  ```

## 2026-08-14 17:03:10Z — Project venv created and verified; dependency licences now recorded at selection time

- **Why:** The venv build succeeded; the reported failure was an error in the verification command, which checked for scikit-learn - not a dependency of this project and correctly absent. 56 tests pass inside the venv. Licence review is now a required part of dependency selection, per ADR-0008 consequences.
- **Authority:** ADR-0008; user decision 2026-08-14
- **Graph node:** N00-foundation
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `d573a81`
- **Evidence:**

  ```
  pm4py AGPL-3.0; ortools Apache-2.0; 56 tests pass in .venv
  ```

## 2026-08-15 07:51:55Z — Fixed two failing design-system tests that grepped prose rather than code

- **Why:** Both failures were test defects, not code defects. test_typography asserted against split('body {')[1], which reads the CSS reset block and never sees the themed one that does set tabular-nums. test_front_browser grepped the raw source for 'sort' and failed on the comment explaining why sorting is forbidden. Tests that grep source text including comments break precisely when someone documents the rule they enforce. Rewrote both to strip comments and match code, and verified with negative checks that they still catch real violations - which found a bug in the first replacement regex before it was committed.
- **Authority:** SPEC-005; docs/06-QA-AND-DEADCODE.md
- **Graph node:** N14a-design-system
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `65c7854`
- **Artefacts:** `tests/test_design_tokens.py`
- **Evidence:**

  ```
  105 passed, 3 skipped; negative checks confirm .sort(/sortBy(/orderBy(/.reverse() still caught and comment text ignored
  ```

## 2026-08-15 07:54:52Z — N14a: front-end design system -- tokens, accessibility gates, chart primitives

- **Why:** SPEC-005's design bar is normative and retrofitting a visual system onto built screens yields a themed prototype rather than a coherent product. Palette derived numerically rather than by eye: five categorical slots chosen by maximin separation under simulated protanopia/deuteranopia/tritanopia subject to a contrast floor, worst pair dE76 18.7. Accessibility is asserted, not reviewed. The provenance, interval, spread-bounds and baseline rules are enforced by the primitives themselves -- a constructor that throws holds indefinitely where a review checklist holds for a month.
- **Authority:** SPEC-005
- **Graph node:** N14a-design-system
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `8e67512`
- **Artefacts:** `src/hwpm/design/`, `web/design/`, `tests/test_design_tokens.py`, `docs/specs/SPEC-005-visualisation.md`
- **Evidence:**

  ```
  gates: ruff format/check clean; mypy clean (22 files); vulture clean; import-linter 3 contracts kept; pytest 108 passed 3 skipped; coverage domain 99% overall 83%; hwpm design check 21 contrast rules ok, dE floor 15 met at 18.7 worst. Gates 8-9 (determinism, baseline) have no test files yet -- they arrive with the stochastic nodes; nothing in N14a is stochastic. D6 in SPEC-005 is a presence check, not behavioural: the JS guards are asserted to exist, not asserted to fire. Gallery verified in a browser (DOM, keyboard, strategy transition, no console errors); no visual screenshot was taken.
  ```

## 2026-08-15 07:55:27Z — HANDOVER: N14a complete; session B at budget HALT

- **Why:** Ledger reports 131% of the 3,000,000 hard limit for this session, so per docs/05-SELF-MANAGED-MODE.md work stops at a node boundary rather than starting anything new. N14a is finished and committed, so nothing is in flight.
- **Authority:** docs/05-SELF-MANAGED-MODE.md; orchestration/graph.yaml budget.thresholds
- **Graph node:** N14a-design-system
- **Model:** claude-opus-5
- **Actor:** marti
- **Commit:** `4b37e6a`
- **Evidence:**

  ```
  DONE: design system complete -- src/hwpm/design (tokens, colour maths, emitters), web/design (base.css, 9 primitives, gallery.html), tests/test_design_tokens.py, SPEC-005 design-system section with criteria D1-D6, hwpm design build/check CLI, graph status completed. NOT DONE, and deliberately deferred to N14: behavioural JS tests for the primitive guards (needs a DOM and a JS runner); the prototype web/hospital-ward.html still uses the pre-system palette including the #8a9599 label tone that fails AA -- it is a known defect recorded in SPEC-005 decision 10, not yet corrected. NEXT CONCRETE ACTION: N14-viewer remains blocked on N07 and N10; runnable now are N04, N05, N19. OPEN QUESTION: the design system caps categorical colour at five specialties plus an aggregate bucket -- if a real ward round routinely involves more than five distinguished specialties, that cap needs a decision at N16 about encoding by position or small multiples instead.
  ```
