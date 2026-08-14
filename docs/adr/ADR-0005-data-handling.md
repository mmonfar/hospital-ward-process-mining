# ADR-0005 — Identifiable data never enters the repository or a model context

**Status:** accepted · **Date:** 2026-08-14

## Context

The input to this project is a ward event log: timestamp, staff identifier,
patient identifier, location, activity. That combination is patient-identifiable
even without names — a bed and a timestamp identify a person to anyone with ward
access. It is also staff-identifiable, which raises a separate employment-relations
problem (`00-VISION.md`, non-negotiable constraints).

Agents in this project read files and may send their contents to a model API.
That is an export of data outside the organisation's control, and it is not
undone by deleting the file afterwards.

## Decision

1. **Raw data lives outside the repository**, in a path configured via
   `HWPM_DATA_DIR`, and `data/` plus all `*.csv`, `*.xes`, `*.parquet` are
   git-ignored.
2. **Pseudonymisation happens at the ingestion boundary**, before anything is
   persisted as a project artefact. The identifier mapping is written to the
   external data directory and never to the repo.
3. **No agent reads raw event data into its context.** Agents work against
   *synthetic fixtures* (`tests/fixtures/`) that share the real schema and
   plausible distributions but contain no real people. Analysis over real data
   runs as executed code whose *aggregate* output the agent may read.
4. **Aggregation floor:** no output cell derived from fewer than 5 patients or
   attributable to a single named clinician. Enforced in the analytics layer as
   a suppression rule with its own test, not left to reviewer vigilance.
5. **Information-governance approval precedes real data.** This repository is
   built and validated on synthetic data. Whether IG/Caldicott sign-off and an
   appropriate legal basis are in place before any real extract is used is a
   decision for the user, not for an agent, and is a hard stop in
   `05-SELF-MANAGED-MODE.md`.

## Rationale

Rule 3 is the one specific to agentic development and the one most easily
violated by accident — an agent that helpfully runs `head data/log.csv` to
"understand the schema" has exported patient data. Synthetic fixtures make the
safe path also the convenient path, which is the only kind of control that
survives contact with a deadline.

Rule 4 exists because the technical capability to rank clinicians will exist
whether or not we intend it, and intent is not a control.

## Consequences

- Building good synthetic fixtures is real work, scheduled in SPEC-001 rather
  than improvised.
- Some debugging is harder: an agent cannot look at the failing row. It must add
  a reproducing synthetic case instead — which is better practice anyway, since
  it leaves a regression test behind.
- Statements in this repository about real-data results must cite the executed
  artefact chain (`02-ARCHITECTURE.md`), because no agent will have seen the
  underlying rows.
