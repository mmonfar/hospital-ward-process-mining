# 00 — Vision

**Status:** accepted · **Owner:** Martin · **Last revised:** 2026-08-14

## The problem, stated as the organisation experiences it

Ward rounds in this organisation are **asynchronous**. Each subspecialty team
rounds on its own schedule. A patient under cardiology, renal, and surgery is
visited three times, hours apart, by three teams who never meet. The observable
consequences:

1. **Nursing workflow is shredded.** A nurse cannot plan the shift around three
   unpredictable round times. Every round arrival is an interrupt, and the
   interrupts land on top of medication rounds and handover.
2. **Information is scattered.** Three teams write three notes at three times
   against three partial pictures. The plan that emerges is nobody's plan.
3. **Continuity of care is not guaranteed.** Decisions get deferred to "when the
   other team comes", which may be tomorrow.
4. **Motion is wasted.** Clinicians walk the hospital in an order driven by their
   own list, not by geography, crossing floors repeatedly.

The surgical case is the sharpest instance: a surgical registrar physically
*passes through* a medical ward where one of their patients lies, but the joint
review does not happen because nobody knew they were there.

## What this project is

A **process-mining and optimisation model of ward-round behaviour**, built on
real event-log data, that can:

- **Measure** what actually happens — discover the real round process from
  timestamped location/activity events, rather than the process people believe
  happens.
- **Quantify motion waste** — metres walked, floor transitions, revisits, and
  the share of them attributable to scheduling rather than clinical necessity.
- **Find synchronisation opportunities** — the concrete question *"for these
  patients on this day, was there a feasible schedule where the required
  specialties met at the bedside?"* and *"what would it have cost?"*
- **Show the trade-off honestly** — as a Pareto set of viable schedules, not a
  single number, because the choice between clinician convenience, nursing
  protection, and MDT co-presence is a management decision.
- **Visualise it** so a non-analyst can see it — the existing three.js ward model
  in `web/` is the front end for exactly this.

## The stated goal and its owner

**Goal:** the *majority* of ward rounds for multi-specialty patients happen as
synchronous MDT rounds.

**Owner:** the **Clinical Governance department** (quality and patient safety)
monitors this as part of the project.

This is a significant commitment and it changes the project's nature. A tool that
merely *analyses* rounds is an internal study. A tool whose output a governance
department *monitors* is a measurement instrument in a quality-assurance system,
and it inherits three obligations a study does not have:

1. **Stability over time.** A metric that moves because we changed a parameter is
   indistinguishable, to its audience, from one that moved because care changed.
   Every published figure is versioned by method, and method changes are
   announced with the back-series recomputed — never silently applied.
2. **Resistance to gaming.** Any monitored number creates an incentive to move
   the number rather than the thing it measures. The specific exposure here is
   the selectable `RequiredSpecialty` definition (SPEC-001) — the denominator of
   the coverage metric is a choice. Mitigations are specified there and are
   mandatory, not advisory.
3. **A defensible chain from figure to evidence.** Governance figures get
   challenged, sometimes months later and sometimes by the clinicians they
   describe. Every number must trace to the events that produced it (SPEC-006,
   audit 3).

**The headline metric** is *MDT coverage*: the proportion of multi-specialty
patient-days on which the required specialties were co-present at the bedside.
Defined in SPEC-002, reported with its uncertainty and its strategy key, never
as a bare percentage.

**One caution, stated once and on the record.** "Majority of MDT rounds" is a
target, and targets applied to clinical process measures reliably produce
gaming — usually by redefining the denominator, occasionally by recording
co-presence that did not clinically occur. The defence is not exhortation; it is
that the denominator definition is displayed alongside every figure, that all
five definitions are always computed, and that co-presence is derived from
observed events rather than self-report. If the metric is ever allowed to be
self-reported, this defence is gone and the number becomes worthless. That is a
design constraint, not a preference.

## What this project is not

- **Not a rostering system.** We do not schedule staff. We analyse and propose.
- **Not a live clinical system.** No real-time patient data, no clinical
  decision support, no regulatory device pathway. This is a management-analytics
  tool. If that changes, everything about the governance changes with it.
- **Not a general-purpose process-mining suite.** We use existing libraries for
  discovery and conformance; our contribution is the ward-motion and
  MDT-synchronisation layer on top.

## Why it generalises

The user's framing is that this is a platform, not a single study. The same
event-log → geometry → optimisation spine serves:

| Application | Uses |
|---|---|
| Motion waste analysis | Ingestion, geometry, routing (P2) |
| MDT synchronisation | + multi-objective scheduling (P1) |
| Opportunistic co-review (the passing surgeon) | + proximity detection over trajectories |
| Bed allocation / cohorting | + grouping optimisation (P4) |
| Nursing interruption load | + collision analysis against nursing task calendar |
| Round timing vs. discharge-by-noon | + outcome linkage |

The architecture is therefore built around a **stable domain core** with
pluggable analyses, not around one question. See `02-ARCHITECTURE.md`.

## Success criteria

This project has succeeded when:

1. A real ward's event log can be ingested and its round process discovered
   without hand-editing, and the discovered model is recognised as accurate by
   the clinicians who work there. *(Face validity is a hard gate; a model
   clinicians do not recognise will not be acted on regardless of its statistics.)*
2. Motion waste is reported with a defensible confidence interval, separating
   avoidable from clinically necessary movement.
3. For at least one real ward-day, we can show a feasible synchronised-MDT
   schedule and state its cost in clinician time and its benefit in co-presence.
4. The results survive an independent audit of the code and the method
   (see `06-QA-AND-DEADCODE.md`).

## Non-negotiable constraints

- **Data protection.** Event logs are patient-identifiable by construction
  (location + time + clinician). No identifiable data enters this repository, any
  model context, or any external service. See `docs/adr/ADR-0005-data-handling.md`.
- **No inference of individual clinician performance.** The unit of analysis is
  the *system*. A tool that ranks named clinicians by metres walked will be
  correctly rejected by every clinician in the building and would be an ethical
  failure besides. This constraint is enforced in the aggregation layer, not left
  to good intentions.
