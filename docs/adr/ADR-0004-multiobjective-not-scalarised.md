# ADR-0004 — MDT scheduling is solved multi-objectively, not scalarised

**Status:** accepted · **Date:** 2026-08-14

## Context

MDT synchronisation trades off five things that genuinely conflict: co-presence,
clinician motion, nursing disruption, continuity of care, and round makespan
(`SELECTION-GUIDE.md`, P1).

The convenient move is to weight them into one score and optimise that. It makes
the code simpler and produces a single answer.

## Decision

Optimise the objectives **jointly** with NSGA-II (Alg 104, p.143) and deliver a
**Pareto front** of non-dominated schedules. Do not scalarise.

Also solve the single-ward instance **exactly with CP-SAT first** (Rule 0 in
`SELECTION-GUIDE.md`). If exact solving is tractable at the scale that matters,
the metaheuristic becomes a cross-check rather than the product.

## Rationale

The choice between "protect nursing time" and "minimise consultant walking" is
a management decision with real stakeholders. Encoding it as a weight vector
does not remove the decision — it hides it inside a constant that no clinical
director will ever see, let alone challenge. When the output is questioned, the
answer becomes "the model said so", which is how analytics projects lose
credibility in hospitals.

A Pareto front changes the conversation to the correct one: *here are seven
schedules that are all defensible; this one costs the surgeons 20 extra minutes
and gives the nurses an uninterrupted drug round; choose.*

The organisational argument and the technical one agree here, which is a good
sign. But the organisational one is decisive.

## Consequences

- Output artefacts are sets of schedules with objective vectors, not one
  schedule. The viewer (SPEC-005) must present a front, which is more UI work.
- We need the crowding-distance machinery (Alg 102, p.142) to keep the front
  usably spread rather than clustered.
- SPEA2 (Alg 107, p.146) is implemented as a QA cross-check only. Materially
  different fronts from the same instance indicate a defect, not a preference.
- We must resist "just give me the best one" requests by instead asking which
  objective the requester wants to prioritise, and returning that point of the
  front. The answer is available; it just has to be chosen consciously.
