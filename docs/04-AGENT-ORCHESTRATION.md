# 04 — Agent orchestration, model selection, token governance

**Status:** accepted · **Last revised:** 2026-08-14

`orchestration/graph.yaml` is the machine-readable authority. This file explains
the reasoning behind it. When they disagree, the YAML wins and this file is a bug.

```bash
python -m hwpm.cli govern graph -v          # what is runnable, what is blocked
python -m hwpm.cli govern graph --mermaid   # regenerate the diagram
python -m hwpm.cli govern budget --by-model # real token spend
```

## The work graph

18 nodes, N00–N17, dependency-ordered. Three properties are doing real work:

**Sequencing that saves money.** `N08-exact-baseline` (CP-SAT) comes *before*
`N10-nsga2`. If exact solving turns out tractable at single-ward scale — and it
plausibly is — then several hundred thousand tokens of metaheuristic work is
never spent. The graph encodes Rule 0 of `SELECTION-GUIDE.md` structurally, so
following the graph enforces it without anyone remembering to.

**Conditional nodes.** `N13-native-kernel` carries a `condition` field and does
not start unless `N12-profile-gate` produces a measurement that fires the
ADR-0003 gate. A conditional node is how "don't over-engineer" becomes something
a planner can check rather than an aspiration.

**Gates.** Every node is `autonomous`, `confirm`, or `hard-stop`:

| Gate | Meaning | Nodes |
|---|---|---|
| `autonomous` | Proceed, log on completion | most |
| `confirm` | Do the analysis, prepare a recommendation, then stop and ask | N04, N13, N16 |
| `hard-stop` | Never proceed. Only the user, in conversation, moves this | N17 |

`Graph.runnable()` filters hard-stops structurally rather than checking at the
call site, so it is not possible to forget the check.

Why those three are gated:
- **N04 (RequiredSpecialty derivation)** — a clinical judgement, not an
  engineering one, and every MDT result inherits its error.
- **N13 (C kernel)** — accepting a permanent build-toolchain and memory-safety
  burden is the user's call even when the measurement supports it.
- **N16 (face validity)** — needs real clinicians. An agent can prepare the
  materials; it cannot perform the review, and must not simulate having done so.
- **N17 (real data)** — information governance. See ADR-0005.

## Model selection

Four roles, resolved to models in `graph.yaml`. The principle: **spend reasoning
depth where a wrong answer is expensive to reverse.**

| Role | Model | Effort | Why |
|---|---|---|---|
| `architect` | Opus 5 | high | Specs, ADRs, domain modelling, optimiser and statistical design. A wrong decision costs days and may not be noticed until an auditor finds it. |
| `builder` | Sonnet 5 | medium | Implementing a written spec. The spec has already collapsed the solution space; extra depth buys little. |
| `mechanic` | Haiku 4.5 | low | Formatting, inventories, index regeneration, dependency bumps — verifiable by inspection. |
| `reviewer` | Opus 5 | high | Adversarial review and the future code auditor. |

Two rules that matter more than the table:

1. **Never review with the model that wrote the code.** A model reviewing its own
   output reliably re-derives the same blind spot. The auditor (N15) is a
   separate `reviewer` node for this reason, not a flag on the builder nodes.
2. **Escalate on evidence, don't start high.** If a `builder` node fails twice on
   the same problem, that is information: the spec is probably underspecified.
   Escalate to `architect` to fix *the spec*, then re-run the builder. Retrying
   the same node at higher effort treats a specification defect as a reasoning
   defect and usually produces confidently wrong code.

## Token governance

The brief's concern — "so we do not just burn in a chunk" — is addressed by
measurement, not by estimation.

### Why the ledger is cost-weighted

Summing raw token counts is misleading. Every turn re-reads the whole
conversation from cache, so `cache_read_input_tokens` summed across a session
counts the same context dozens of times, and a naive total crosses any plausible
limit within a few turns. `src/hwpm/govern/ledger.py` therefore weights by
relative cost (cache reads ~0.1x fresh input, output ~5x) and drives thresholds
off the weighted figure.

### Calibration is empirical

The first thresholds were guessed at 400k/700k. The first real run scored
**1.28M weighted for a healthy 83-turn session** — 184% of a limit that had never
been tested. They were reset to 2M/3M against that observation. This is recorded
because it is the general lesson: **a budget that a normal session exceeds is a
budget everyone learns to ignore**, and a guessed threshold is worse than none.
Re-calibrate once ~10 sessions of history exist, not on a sample of one.

### Thresholds

| Fraction | Action | Behaviour |
|---|---|---|
| 0.60 | `warn` | Report remaining budget in the next turn. |
| 0.80 | `consolidate` | Stop starting new nodes. Finish the in-flight node, log, commit — end at a resumable boundary. |
| 0.95 | `halt` | Audit entry, handover note, stop. |

The `consolidate` step is the important one. The expensive failure is not running
out of budget; it is running out *mid-node*, leaving half-built code no future
session can safely resume. Consolidation converts a hard stop into a clean seam.

### The four spend rules

From `graph.yaml`:

- **`no-bulk-reference-reads`** — `essentials-of-metaheuristics.md` is ~863 KB
  (≈215k tokens). Reading it once would consume a sixth of a session's budget for
  content that `ALGORITHM-INDEX.md` plus a page-anchor grep delivers for a few
  hundred. This is the single largest avoidable spend in the repository.
- **`no-raw-data-reads`** — both governance (ADR-0005) and budget.
- **`subagent-returns-summaries`** — sub-agents return findings and file paths,
  never file contents. Otherwise the parent context accumulates everything the
  child already paid to read, and the delegation saves nothing.
- **`prefer-grep-over-read`** — locate, then read only the identified range.

## Parallelism

Nodes with no dependency relation may run concurrently, but **only when they
touch disjoint files.** `N01` (fixtures) and `N02` (domain core) qualify;
`N10` (NSGA-II) and `N11` (ACS) both touch `optimize/` and must not.

Sub-agents are used for *fan-out search* (locating things across many files) and
*independent review*, where the isolation is the point. They are not used to
parallelise a single spec's implementation — the coordination cost exceeds the
saving, and each cold agent re-derives context this one already has.
