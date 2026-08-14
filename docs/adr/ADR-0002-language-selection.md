# ADR-0002 — Python as the primary language; PHP rejected

**Status:** accepted · **Date:** 2026-08-14

## Context

The brief asked for the most efficient appropriate language, escalating to C or
PHP where indicated, using the machine well without over-engineering.

## Decision

**Python 3.11+ is the primary language** for the domain model, ingestion,
mining, analytics, and optimiser control flow.

**PHP is rejected outright.**

**C is admitted only through the gate in ADR-0003.**

**JavaScript is retained** for the existing three.js visualisation only.

## Rationale

For Python: the process-mining and scientific ecosystem (`pm4py`, `pandas`,
`numpy`, `scipy`, OR-Tools) has no equivalent in C or PHP, and re-implementing
conformance checking would be a multi-month project with worse correctness. The
project's correctness also depends on an auditor being able to read the method,
which favours the language the method is normally expressed in.

Against PHP: its strength is server-rendered request/response web applications.
Our front end is a static three.js page requiring no server rendering, and any
future API is better served by FastAPI in the same language as the domain.
Introducing PHP would add a second runtime and a second place for the domain
model to be duplicated and drift out of sync — the exact failure ADR-0001 exists
to prevent. "Efficient use of the machine" here means *not* running two
interpreters to do one job.

## Consequences

- Single toolchain: `uv`/`pip`, `ruff`, `pytest`, `mypy`.
- If a genuine server-rendered multi-user web application is later required,
  this ADR is superseded — but by FastAPI + a JS front end, not by PHP.
- Python's speed limits are real and are addressed narrowly in ADR-0003 rather
  than by language choice.
