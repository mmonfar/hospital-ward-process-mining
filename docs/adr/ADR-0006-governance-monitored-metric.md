# ADR-0006 — MDT coverage is a governance-monitored metric, engineered as one

**Status:** accepted · **Date:** 2026-08-14 · **Supersedes:** nothing

## Context

The Clinical Governance department (quality and patient safety) will monitor MDT
synchronisation as part of this project, against a goal that the majority of
rounds for multi-specialty patients become synchronous MDT rounds.

Analytical output that feeds a governance process is a different artefact from
analytical output that informs a study, even when the arithmetic is identical.
It will be reported upward, compared across periods, and challenged.

Simultaneously, SPEC-001 now makes the `RequiredSpecialty` definition — the
**denominator** of the coverage metric — a runtime choice exposed in the front
end. A selectable denominator on a monitored metric is a gaming surface, and it
was introduced deliberately for good reasons (different definitions genuinely
suit different clinical questions).

Both facts are settled. This ADR records how they are reconciled.

## Decision

**MDT coverage is engineered as a regulated measurement, not as a chart.**

1. **Method versioning.** Every published figure carries a method version. A
   change to episode-derivation parameters, the travel graph, or a strategy
   implementation increments it, and the **back-series is recomputed and
   re-published together with the change**. Silent recomputation is prohibited.

2. **The denominator always travels with the number.** No rendering path — UI,
   export, screenshot, API — emits a coverage figure without its
   `RequiredSpecialty` strategy key and confidence. Enforced by the artefact
   schema (SPEC-001 criterion 10), so omitting it is a load failure rather than
   a review oversight.

3. **All five strategies are always computed and always displayed as a spread.**
   Selection changes emphasis, never availability. `union` and `intersection` are
   shown as bounds so the plausible range is always visible.

4. **Co-presence is derived from observed events only.** Never self-report,
   never manual attestation. This is the single load-bearing anti-gaming
   property: the moment coverage can be asserted rather than observed, the
   metric measures documentation behaviour instead of clinical behaviour.

5. **No clinician-level attribution.** ADR-0005's suppression floor applies with
   no exception for governance use. A governance department asking for
   clinician-level breakdown is asking for a different tool, and the answer is
   no.

6. **Uncertainty is mandatory.** Coverage is reported with its interval
   (SPEC-003). A governance dashboard showing a bare percentage is a defect.

## Rationale

The instinct when a metric becomes monitored is to make it look authoritative —
a single confident number on a dashboard. That instinct is exactly wrong here.
The number's authority comes from the visible chain behind it: which patients
counted, under which definition, derived from which observed events, with what
uncertainty. A confident-looking percentage without that chain will not survive
its first challenge by a consultant who disagrees with it, and losing that first
challenge would end the project's credibility regardless of the underlying work.

Point 4 deserves emphasis because it is the one most likely to be traded away
under delivery pressure. Allowing teams to record "MDT round completed" would
make coverage trivially easy to collect and would make the metric worthless
within two reporting cycles. Observed-events-only is what makes it real.

## Consequences

- The dashboard (node N18) is more complex than a percentage: spread across
  strategies, intervals, method version, and drill-through to evidence.
- Method changes become an announced event with a recompute cost, which is
  friction — accepted, because unannounced method changes are worse.
- Some governance requests will be refused: clinician-level breakdown, bare
  percentages for slide decks, self-reported supplementation. Each refusal
  should offer the nearest defensible alternative rather than simply declining.
- The project acquires a stakeholder with a target, which is a real risk to
  analytical independence. The mitigations above are structural for that reason:
  they work without requiring anyone to resist pressure in the moment.
