# SPEC-006 — Automated code and method auditor

**Status:** accepted, built at N15 · **Node:** N15 · **Owner role:** reviewer
**Depends on:** SPEC-002, SPEC-003, SPEC-004 · **Last revised:** 2026-08-16

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

1. Reports every `src/hwpm` module with no authorising spec. — `test_reports_module_with_no_authorising_spec`
2. Reports every acceptance criterion with no corresponding test. — `test_reports_criterion_with_no_test`
3. Reports any code path reading `HWPM_DATA_DIR`. — `test_reports_data_dir_read`
4. Reports any aggregate output lacking a suppression check. — `test_reports_aggregate_without_suppression`
5. Reports any commit absent from the audit log. — `test_reports_unlogged_commit`
6. **Findings are written to the audit log whether or not they are acted on.** — `test_findings_written_to_audit_log`

Criterion 6 is the one that makes it an audit. A tool that records only what got
fixed is a to-do list.

The test names were added at N15, when the tests were written. Nothing in the
criteria themselves changed: they now say how each is verified, as SPEC-001,
-003 and -005 already did. Each test also has a negative control in
`tests/test_auditor.py`, because the failure mode of a static check is a false
clean and a criterion satisfied only by a positive case has been tested for
silence.

## Built at N15

`src/hwpm/govern/auditor.py`, run as `hwpm govern review`. Static and
stdlib-only: it parses source with `ast` and reads markdown and `git log`, so
`hwpm govern` stays installable without the scientific stack and the auditor
cannot be defeated by the code it audits importing cleanly.

**What is implemented:** scope items 1 (spec conformance), 2 in part (method:
explicit `rng`, determinism-test coverage per stochastic module, the gate 9
file), 4 (governance) and 5 (audit-log integrity).

**What is not:** scope item 3, the provenance audit. It has no acceptance
criterion here, and tracing every number back through the artefact chain means
executing the pipeline and following values, not reading source. It is recorded
as an open item rather than approximated with a check that greps for the word
"provenance". The method audit is likewise partial: whether the algorithm in a
file is the one `SELECTION-GUIDE.md` specifies still needs code read against
intent, which is why this node is a reviewer's and not a linter's.

**Acknowledged findings.** `auditor.ACKNOWLEDGED` maps a finding to a recorded
reason. An acknowledged finding is still printed and still written to the log —
acknowledgement changes the exit code, never the visibility, because a list that
hid findings would defeat criterion 6. The `hwpm.govern` package having no
authorising spec is the current entry: a genuine ADR-0001 gap, recorded rather
than closed by writing a retrospective spec.

## Open questions

- **[resolved at N15]** Should the auditor gate CI (blocking) or report
  (advisory)? **Decided: governance findings block, everything else advises.**
  `hwpm govern review` exits non-zero when an unacknowledged governance finding
  exists; `--strict` fails on any finding. Rationale: a governance failure
  exports patient data or publishes a disclosive cell and cannot wait for
  triage, whereas a missing determinism test is a debt with a known owner.
  Severity is assigned per finding, not per audit — a reference that only names
  `HWPM_DATA_DIR` in a guard is advisory, a reference in a function that also
  reads a file is blocking.
- **[non-blocking]** The provenance audit (scope item 3) needs a runtime
  artefact chain to trace. Revisit when N18's governance view exists and there
  are report artefacts to follow.
