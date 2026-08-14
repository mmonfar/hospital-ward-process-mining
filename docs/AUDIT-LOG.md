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
