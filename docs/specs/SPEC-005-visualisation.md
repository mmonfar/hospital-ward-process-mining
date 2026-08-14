# SPEC-005 — Ward visualisation

**Status:** draft · **Node:** N14 · **Owner role:** builder
**Depends on:** SPEC-003, SPEC-004 · **Last revised:** 2026-08-14

## Problem

Make the findings legible to people who will never read a Pareto front table:
ward managers, clinical directors, and the clinicians whose day this describes.

The existing three.js prototype (`web/hospital-ward.html`, `web/three-d-stage.js`)
is the starting point and already does the hard part — a navigable 3D ward with
CSV case-log import and motion playback.

## In scope

- Ward layout served as **data** from the Python layer instead of hard-coded JS.
- Trajectory playback driven by ingested events.
- Motion-waste overlay: observed path vs. the `necessary_m` lower bound.
- **Pareto front browser** — the schedule-selection interface ADR-0004 requires.
- Missed-opportunity markers from `detect_opportunistic` (SPEC-002).

## Out of scope

- Authentication, multi-user, persistence. Static artefacts opened locally.
- Any display of real patient or named clinician data. ADR-0005 applies to the
  viewer with no exception.

## The main change to the prototype

Ward geometry currently lives in a `WARDS` array in `hospital-ward.html`, and
distances are straight-line. Both move to the Python layer:

```
artefacts/
  layout.json      # locations, travel graph edges  (SPEC-003)
  trajectories.json
  motion.json      # MotionReport incl. ci95 and params
  front.json       # [{schedule, objectives}, ...]   (SPEC-004)
```

The viewer becomes a renderer of served artefacts and holds **no domain logic** —
`02-ARCHITECTURE.md`'s layering rule applies to JavaScript too. Concretely: the
viewer must not compute a distance, a total, or an average. If a number appears
on screen, it came from an artefact.

## The Pareto front browser

The interface challenge of the project. Users must compare 5–10 schedules across
five objectives and pick one. Design constraints:

- Show the **trade-off**, not a ranking. Any UI that orders the front implies one
  objective matters most, which is the decision we are trying not to make for
  them (ADR-0004).
- Parallel-coordinates plot across the five objectives, brushed to the 3D view.
- Selecting a point animates that schedule in the ward, because the trade-off is
  far more intuitive as motion than as numbers.
- Always show the observed baseline alongside, so "better" is anchored to what
  happens today rather than to the best of the alternatives.

## Acceptance criteria

1. The viewer contains no hard-coded ward geometry; `layout.json` drives it. — manual + `test_no_hardcoded_layout` (greps for coordinate literals)
2. Every number displayed traces to a field in an artefact. — review checklist
3. Motion figures display their CI and parameters, never a bare point estimate. — review checklist (SPEC-003)
4. The front browser shows ≥5 schedules with all five objectives simultaneously.
5. No patient or clinician identifier is rendered. — `test_no_identifiers_in_artefacts`
6. Loads and animates a full ward-day in <3 s on a mid-range laptop.

## Failure modes

- **The viewer recomputes something** and silently disagrees with the report. The
  reason for criterion 2 and the no-logic rule.
- **The front browser implies a best option**, quietly re-scalarising the decision
  in the UI after ADR-0004 removed it from the maths.
- **Prettiness over honesty**: a smooth animation of a schedule that is not
  operationally feasible is persuasive in the wrong direction.

## Open questions

- **[non-blocking]** Keep three.js, or is a 2D floor plan clearer for this
  audience? 3D is more engaging; 2D is often more readable. Decide with users at
  N16 rather than by assertion.
- **[non-blocking]** Does this need to be shareable (a hosted page), which would
  change the data-governance posture entirely?
