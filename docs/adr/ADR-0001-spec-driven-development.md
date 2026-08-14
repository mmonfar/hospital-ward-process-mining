# ADR-0001 — Spec-driven development with an audit trail

**Status:** accepted · **Date:** 2026-08-14

## Context

The project will be built largely by autonomous agents, across many sessions,
with the user confirming only at gates. Two failure modes follow directly:

- **Drift.** Each session re-derives intent from code and slowly builds something
  nobody asked for.
- **Unauditability.** A result is produced and nobody — including the agents —
  can reconstruct why the method was chosen.

The second is disqualifying. This is health data science; a motion-waste figure
that cannot be traced to a documented method is not usable in a governance
meeting.

## Decision

**No implementation without a spec.** Every capability gets a `docs/specs/SPEC-NNN`
before code is written, containing: problem, scope boundaries, interface,
acceptance criteria, test oracle, and open questions.

**Specs are normative and code is subordinate.** When code and spec disagree,
that is a defect. Either the code is fixed or the spec is amended *first*, with
the amendment logged.

**Every non-obvious decision gets an ADR.** Cheap to write, and it prevents the
same argument being had four times across sessions.

**Every agent action that changes the repository is appended to
`docs/AUDIT-LOG.md`** with what, why, which spec it serves, and which model made
it. Append-only.

## Consequences

- Slower start. The first commits are documents, not features. Accepted: the
  alternative is a prototype nobody can defend.
- Specs can rot. Mitigated by the self-revision loop (`05-SELF-MANAGED-MODE.md`)
  checking spec-vs-code agreement as a standing task, and by the future code
  auditor treating divergence as a finding.
- The audit log grows large. Accepted; it is plain text and greppable.
