# 01 — Domain model

**Status:** accepted · **Last revised:** 2026-08-14

The domain layer is the stable core. Everything else (ingestion, mining,
optimisation, visualisation) depends *inward* on it; it depends on nothing.
If a change to a CSV format or a solver library forces a change in this file,
the layering has been violated.

## Ubiquitous language

We use the vocabulary clinicians actually use. Where process-mining literature
and clinical practice disagree on a word, clinical practice wins in the domain
layer and the translation happens at the ingestion boundary.

| Term | Meaning here | Process-mining equivalent |
|---|---|---|
| **Encounter** | One patient's continuous stay | Case / trace |
| **Event** | Something observed at a time and place | Event |
| **Bedside episode** | A contiguous period a clinician spends at one bed | Activity instance |
| **Round** | One clinician's ordered sweep of bedside episodes | — |
| **MDT moment** | ≥2 required specialties co-present at one bedside | — |
| **Transition** | Movement between two locations | Directly-follows edge |

## Entities

Identity semantics matter and are stated explicitly, because getting them wrong
is how double-counting bugs enter analytics.

### Place — value objects, identified by coordinates

```
Site ─┬─ Building ─┬─ Floor ─┬─ Ward ─┬─ Bay ─┬─ Bed
      │            │         │        │       └─ (isolation room = Bay of size 1)
      │            │         │        ├─ NursingStation
      │            │         │        └─ Corridor node
      │            │         └─ (inter-ward corridor, lift, stair)
```

- `Location` is an **abstract value object** with a stable `LocationId` and a
  3D `Point`. Equality is by id, never by coordinates (coordinates get corrected;
  identity must not shift under them).
- `Bed` is where a patient is. `Bay` groups beds and is the unit of
  cohorting. `Ward` matches the existing prototype's `WARDS` array in
  `web/hospital-ward.html` — that layout is the reference geometry until real
  floor plans are ingested.
- **Travel cost between locations is a graph problem, not a Euclidean one.**
  Two beds 4 m apart on different floors are 90 s apart. The domain exposes
  `TravelGraph.cost(a, b)` and never lets callers compute distance themselves.
  The prototype currently uses straight-line distance; replacing that is
  SPEC-003's first task.

### People

- `Clinician` — **entity**, identified by a pseudonymous `ClinicianId`. Carries
  `Role` (consultant, registrar, nurse, AHP…) and one or more `Specialty`
  values. Never carries a name in this repository.
- `Patient` — **entity**, pseudonymous `PatientId`. Carries `acuity` and
  `isolationStatus`. **Amended 2026-08-14 (N02):** `RequiredSpecialty` is *not*
  a `Patient` field. SPEC-001's later resolution (docs/AUDIT-LOG.md, "Resolved
  N04 gate") made it a runtime-selectable analysis parameter — one of five
  `RequiredSpecialtyStrategy` implementations computed over persisted evidence
  (referrals, consult notes, problem list), not a value baked in at ingestion.
  Putting a single derived set on `Patient` would silently pick one strategy at
  the domain layer, contradicting "the ingestion stage persists the evidence
  rather than the conclusion" (SPEC-001). It remains the single most important
  *derived* quantity in the model — see N04 — and the imperfection in any one
  strategy must be surfaced as a confidence value, not hidden.

### Time and observation

- `Event` — **immutable value object**: `(timestamp, subjectId, activity,
  locationId, source, confidence)`. The whole system is a fold over an ordered
  stream of these. Immutability is enforced (frozen dataclasses), because
  every reproducibility bug in analytics of this kind traces back to something
  mutating an event in place.
- `Trajectory` — an ordered `Event` sequence for one subject over one period.
  Knows how to yield `Transition`s and total travel cost.
- `BedsideEpisode` — derived: clinician, bed, patient, interval. Derivation from
  raw events (dwell-time thresholds, sensor noise) is SPEC-002's responsibility
  and is **explicitly a modelling assumption with a tunable parameter**, not a
  fact.
- `MDTMoment` — derived: bed, interval, set of co-present specialties, and the
  set of *required* specialties it satisfies. A `MDTMoment` that satisfies
  nothing is not an MDT moment; it is two people who happened to collide.

### Schedule (the optimisation subject)

- `Visit` — a *planned* bedside episode: `(clinicianId, patientId, startSlot,
  duration)`. Distinct type from `BedsideEpisode` — observed and planned things
  must never be the same class, or counterfactual analysis silently compares a
  plan against itself.
- `Schedule` — a set of `Visit`s. Knows how to validate itself against
  `Constraint`s but does **not** know how to score itself; scoring lives in the
  optimisation layer so the domain stays free of objective-function politics.
- `Constraint` — abstract, with concrete `ClinicianAvailability`,
  `PatientUnavailable` (theatre, imaging, dialysis), `NursingProtectedWindow`,
  `AcuityOrdering`, `IsolationLast`.

## Class design rules

1. **Frozen by default.** Events, locations, and transitions are immutable value
   objects. Mutability is opt-in and justified in a comment.
2. **No I/O in the domain.** No file reads, no database, no HTTP, no logging
   config. A domain class that imports `pandas` is a design smell; one that
   imports `requests` is a bug.
3. **Derived quantities are explicit types, not dict keys.** `MDTMoment` is a
   class. If it were a dict it would grow keys nobody can find the origin of.
4. **Every derived entity carries provenance.** `source` and `confidence` are
   not optional. When a clinician disputes a number — and they will — we must be
   able to trace it to the events that produced it.
5. **Interfaces are Protocols**, so the optimisation and mining layers can be
   tested against fakes without a database.

## The one thing most likely to be got wrong

`RequiredSpecialty` on `Patient`. Everything about MDT synchronisation depends
on knowing which patients genuinely need which specialties, and that is not
recorded cleanly in any hospital system. If it is derived badly, the optimiser
will produce beautiful schedules for the wrong patients.

SPEC-001 must therefore treat this as a **measured quantity with error bars**,
validated against clinician review of a sample, before any optimisation result
built on it is reported. This is flagged here, in the domain model, so that it
cannot be quietly forgotten in implementation.
