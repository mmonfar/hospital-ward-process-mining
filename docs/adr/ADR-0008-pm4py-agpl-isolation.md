# ADR-0008 — pm4py is AGPL v3; isolate it behind a Protocol and keep it off the network

**Status:** accepted (mitigation) · **Date:** 2026-08-14
**Open decision for the user:** yes — see "What the user must decide"

## Context

`SPEC-002` specifies process discovery and conformance checking via `pm4py`. On
installing it, the package banner and metadata make its licence explicit:

```
License: AGPL 3.0
Commercial use requires open-sourcing your application.
A commercial license is available.
```

This was not checked before the dependency was chosen, which was a mistake — it
is exactly the kind of constraint that becomes expensive in proportion to how
late it is found.

AGPL v3 matters here for one specific reason. Ordinary internal use is fine:
running AGPL software on your own machines without conveying it to anyone
triggers no obligation. But **AGPL §13** extends copyleft to *network
interaction* — if users interact with a covered work remotely, they must be
offered its Corresponding Source, and courts and the FSF read "the work"
expansively where the AGPL component is combined into a larger program.

Two facts about this project put it directly in that zone:

1. `SPEC-005` carries an open question: *"Does the governance view need to be
   shareable (hosted)?"* Clinical Governance monitoring a metric (ADR-0006) is
   precisely the use case that ends in "can we put this on the intranet?".
2. The whole project is oriented toward an organisational deployment, not a
   personal script.

Note the risk is **not** hypothetical-only in the other direction either: GPL
compliance is a live procurement question for NHS organisations, and "we will
open-source our ward analytics platform" is a decision nobody on this project
has the authority to make casually.

## Decision

Three parts. The first two are engineering and are adopted now; the third is the
user's.

### 1. pm4py is confined to a single adapter module

All pm4py imports live in exactly one file, `src/hwpm/mining/_pm4py_adapter.py`,
behind our own Protocols declared in `hwpm.mining`:

```python
class ProcessDiscovery(Protocol):
    def discover(self, log: EventLog) -> ProcessModel: ...


class ConformanceChecker(Protocol):
    def check(self, log: EventLog, model: ProcessModel) -> ConformanceReport: ...
```

Nothing else in the codebase may `import pm4py`. Enforced by an import-linter
`forbidden` contract, so violation fails a gate rather than relying on review.

This does **not** by itself resolve the AGPL question — a Protocol boundary is
not a legal firewall, and this ADR does not claim it is. What it buys is
*optionality*: the dependency becomes swappable at a known, small cost, so
whichever way the licensing decision goes, we are not re-architecting.

### 2. pm4py stays off the network until the decision is made

While pm4py is in the pipeline, the discovery/conformance step runs **offline as
batch analysis producing static artefacts**. This is already the architecture
(`02-ARCHITECTURE.md`: artefact chain, no web framework), so it costs nothing to
hold — but it is now a constraint with a reason, not merely a default.

`SPEC-005`'s hosting question is therefore **blocked on this ADR**: no hosted or
network-served deployment of anything downstream of pm4py until part 3 is
resolved. The viewer reading static JSON is unaffected and continues.

### 3. Escape routes, costed

If the licence proves unacceptable:

| Option | Cost | Note |
|---|---|---|
| Keep pm4py, offline single-analyst only | zero | Viable if the output is only ever exported reports |
| Commercial pm4py licence | procurement | Vendor offers one |
| Accept AGPL and publish the source | policy decision | Not ours to make |
| Swap to a permissive alternative | moderate | Adapter makes this a one-file change |
| Implement the inductive miner ourselves | high | Well-documented algorithm, but a real project, and re-implementing a mature miner is how subtle conformance bugs enter |

## What the user must decide

**Question:** is AGPL v3 acceptable for this project's process-mining
dependency, given the organisation's software policy and the possibility that
the governance view is later hosted?

This needs the organisation's answer — IT/information-governance or whoever owns
software licensing — not an engineering judgement. Until then, parts 1 and 2
hold and work continues; the discovery step is the only affected node (N05), and
its interface is stable whichever way the answer goes.

## Consequences

- `SPEC-002` gains an explicit adapter requirement and an import-linter contract.
- `SPEC-005`'s hosting open question is now marked blocking, referencing this ADR.
- If the answer is "not acceptable", N05's internals change and nothing else does
  — which is the entire point of part 1.
- **Licence review becomes part of dependency selection.** Every future
  dependency records its licence in the ADR or spec that introduces it. This
  should have happened for pm4py and did not.
