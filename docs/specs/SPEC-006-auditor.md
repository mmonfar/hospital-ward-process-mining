# SPEC-006 — Automated code and method auditor

**Status:** draft (deliberately deferred) · **Node:** N15 · **Owner role:** reviewer
**Depends on:** SPEC-002, SPEC-003, SPEC-004 · **Last revised:** 2026-08-14

## Problem

Brief item 5: an auditor of the code, built at a later stage. Its purpose is
narrower and harder than linting — it answers *"is this project doing what it
says it does, and can every claim be traced?"*

## Why it is deferred

An auditor written before there is substantial code audits nothing and bakes in
assumptions that will be wrong. It is scheduled after N05, N07 and N10 so that
there is real code, real specs, and a real artefact chain to audit. This
deferral is itself a decision and is logged rather than left as a gap.

## Scope when built

Five audits, in ascending order of value and difficulty:

**1. Spec conformance.** Every module in `src/hwpm` maps to a spec; every spec's
acceptance criteria map to a named, passing test. Reports code with no
authorising spec and criteria with no test. Mechanical, high value.

**2. Method audit.** Are the algorithms in the code the ones
`SELECTION-GUIDE.md` specifies? Is the baseline gate genuinely enforced, or has
it been skipped? Are stochastic components seeded? Are parameters recorded in
outputs? Requires reading code against intent — a `reviewer` job, not a linter's.

**3. Provenance audit.** Can every number in every report be traced back through
the artefact chain to the events that produced it? This is what makes a finding
defensible in a governance meeting six months later, and it is the audit most
likely to fail.

**4. Governance audit.** Does any code path read from `HWPM_DATA_DIR` into a
model context? Does every aggregate respect the ADR-0005 suppression floor? Is
the pseudonymisation salt absent from the repository? Findings here are blocking.

**5. Audit-log integrity.** Does `docs/AUDIT-LOG.md` account for every commit?
Unlogged commits are the signal that the self-managed loop has decayed.

## Independence requirement

Run by the `reviewer` role on Opus, and **never by the model that wrote the
code**. A model reviewing its own output re-derives the same blind spot; this is
the whole reason the auditor is a separate graph node rather than a flag.

## Acceptance criteria

1. Reports every `src/hwpm` module with no authorising spec.
2. Reports every acceptance criterion with no corresponding test.
3. Reports any code path reading `HWPM_DATA_DIR`.
4. Reports any aggregate output lacking a suppression check.
5. Reports any commit absent from the audit log.
6. **Findings are written to the audit log whether or not they are acted on.**

Criterion 6 is the one that makes it an audit. A tool that records only what got
fixed is a to-do list.

## Open questions

- **[non-blocking]** Should the auditor gate CI (blocking) or report (advisory)?
  Suggest: governance findings block; method findings advise. Revisit at N15.
