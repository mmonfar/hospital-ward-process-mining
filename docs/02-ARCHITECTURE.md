# 02 — Architecture

**Status:** accepted · **Last revised:** 2026-08-14

## Shape

Ports-and-adapters (hexagonal), with dependencies pointing inward. Chosen
because the domain (`01-DOMAIN-MODEL.md`) must outlive the specific event-log
format, the specific process-mining library, and the specific solver — all three
of which are likely to change.

```
┌──────────────────────────────────────────────────────────────┐
│ Adapters (outer)                                             │
│   web/           three.js viewer, HTTP client                │
│   ingest/        CSV/XES/HL7 readers, location mappers       │
│   persist/       parquet + sqlite artefact store             │
│   govern/        token ledger, audit log writer              │
├──────────────────────────────────────────────────────────────┤
│ Application services                                          │
│   mining/        discovery, conformance, episode derivation  │
│   analytics/     motion waste, MDT opportunity detection     │
│   optimize/      NSGA-II, ACS, DE  (SELECTION-GUIDE.md)      │
│      └── native/ C fitness kernel  (gated — ADR-0003)        │
├──────────────────────────────────────────────────────────────┤
│ Domain (inner, dependency-free)                              │
│   domain/        Event, Trajectory, Schedule, TravelGraph…   │
└──────────────────────────────────────────────────────────────┘
```

Rule: `domain/` imports nothing from the layers above it. Enforced by an
import-linter test, not by discipline (`06-QA-AND-DEADCODE.md`).

## Language choice

The brief asked for the most efficient appropriate language, escalating to C or
PHP where indicated, without over-engineering. The honest answer:

### Python — everything except one loop

Python owns ingestion, the domain model, mining, analytics, orchestration, and
the optimiser control flow. Reasons: the process-mining and scientific stack
(`pm4py`, `pandas`, `numpy`, `scipy`, OR-Tools) has no equivalent elsewhere, and
this is a project whose correctness depends on being *readable by an auditor*.
Rewriting readable analytics into a fast language to save seconds on code that
runs weekly is the definition of over-engineering.

### C — one kernel, behind a profile gate

The fitness evaluation inside NSGA-II is the only place where language speed
plausibly changes what the project can do. One evaluation replays a day's
schedule over the travel graph and scores five objectives; NSGA-II needs
millions of them. This is the point `Essentials` §5 (Alg 67, p.101) makes about
fitness assessment dominating runtime.

**But we do not write C first.** ADR-0003 sets the gate:

1. Implement the fitness function in plain Python. It is the executable
   specification and the correctness oracle.
2. Vectorise with NumPy. Profile.
3. If a full NSGA-II run on a realistic instance still exceeds **10 minutes**,
   and profiling attributes **>60%** to the fitness loop, write the C kernel.
4. The C kernel ships with a differential test proving bit-comparable agreement
   with the Python reference across randomised inputs. If that test is deleted,
   the kernel is deleted.

Numba is evaluated at step 2.5 and, if it clears the bar, we stop there — a JIT
decorator is cheaper to maintain than an FFI boundary.

### PHP — rejected

There is no role for it. PHP's strength is server-rendered request/response web
applications; our front end is already a static three.js page that needs no
server rendering, and the API surface is better served by FastAPI in the same
language as the domain. Adding PHP would mean a second runtime, a second
dependency manager, and a second place for the domain model to be duplicated and
drift. Recorded in ADR-0002 so the question is settled rather than relitigated.

### JavaScript — already present, kept

`web/` is the existing three.js prototype. It stays as the visualisation adapter.
It must not accumulate domain logic: the ward layout it currently hard-codes
becomes data served by the Python layer (SPEC-005).

## Data flow

```
raw export ──► ingest ──► canonical event store (parquet)
                              │
                              ├──► mining ──► discovered model + conformance
                              │                    │
                              ├──► analytics ──────┼──► motion waste report
                              │                    │
                              └──► optimize ◄──────┘
                                      │
                                      └──► Pareto set of schedules ──► web/
```

Every stage writes a versioned artefact with a content hash. Nothing downstream
recomputes from raw. This is what makes a result auditable six months later: the
artefact chain is the evidence.

## Concurrency

Fitness assessment is embarrassingly parallel (Alg 67, p.101). Use
`multiprocessing` over generations, not threads — the Python GIL makes threads
useless for CPU-bound scoring, and if the C kernel arrives it releases the GIL
and threads become viable. Deferred until the gate in ADR-0003 fires.

## What is deliberately absent

- **No database server.** Parquet + SQLite. A single-analyst research tool does
  not need Postgres, and adding it would mean managing patient-adjacent data in
  a service with its own access control.
- **No message queue, no microservices, no container orchestration.** The
  workload is batch analysis on one machine.
- **No web framework yet.** The viewer reads static JSON artefacts. FastAPI
  enters only when there is an interactive what-if flow that needs it (SPEC-005).

These absences are choices, re-evaluated when a spec produces a requirement that
needs them — not before.
