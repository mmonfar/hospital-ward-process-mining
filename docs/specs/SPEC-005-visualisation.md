# SPEC-005 — Front end: ward visualisation, strategy selection, governance view

**Status:** accepted · **Nodes:** N14, N18 · **Owner role:** architect (design), builder (implementation)
**Depends on:** SPEC-001, SPEC-003, SPEC-004 · **Last revised:** 2026-08-14

## Problem

Make the findings legible and credible to people who will never read a Pareto
front table: ward managers, clinical directors, the Clinical Governance
department, and the clinicians whose working day this describes.

The existing three.js prototype (`web/hospital-ward.html`, `web/three-d-stage.js`)
is the starting point and already does the hard part — a navigable 3D ward with
CSV case-log import and motion playback.

## In scope

- Ward layout served as **data** from the Python layer instead of hard-coded JS.
- Trajectory playback driven by ingested events.
- Motion-waste overlay: observed path against the `necessary_m` lower bound.
- **Pareto front browser** — the schedule-selection interface ADR-0004 requires.
- **`RequiredSpecialty` strategy selector** — the runtime choice from SPEC-001.
- **Governance view (N18)** — MDT coverage as ADR-0006 requires it reported.
- Missed-opportunity markers from `detect_opportunistic` (SPEC-002).

## Out of scope

- Authentication, multi-user, server-side persistence. Static artefacts.
- Any display of real patient or named clinician data. ADR-0005 applies to the
  front end with no exception.
- Mobile layouts. This is a desk tool; pretending otherwise costs effort that
  belongs elsewhere.

---

## The design bar (normative)

The brief is explicit: the front end must carry the same quality as the
analysis. That is not decoration — in a project whose output will be challenged
by senior clinicians, **the interface is part of the evidence**. An analysis
presented in a default-styled page reads as a prototype and gets treated as one,
regardless of the rigour behind it.

These are requirements, auditable at review:

**1. One visual system, not a collection of widgets.** A single defined palette,
type scale, spacing scale, and elevation scale, declared once as CSS custom
properties and never overridden ad hoc. The prototype's existing teal
(`#16777a` on `#eef1f0`) is the seed — it is calm, clinical, and not the
saturated blue of every hospital dashboard. Extend it into a full ramp rather
than adding new hues per component.

**2. Data-ink discipline.** Every pixel earns its place. No chrome, no gradients
for their own sake, no decorative iconography, no drop shadows that do not
express elevation. The 3D ward is the visual centrepiece; everything else is
quiet by design so that it can be.

**3. Typography does the hierarchy.** A tight scale (four sizes, two weights),
tabular figures for all numerics so columns align and changing values do not
jitter. Numbers are the product here; they get the best treatment.

**4. Motion is explanatory only.** Animate a clinician walking a route, a
schedule transitioning, a front point being selected — because those show
mechanism. Never animate a panel appearing. Every transition respects
`prefers-reduced-motion`.

**5. Uncertainty is rendered, not annotated.** Intervals appear as visual extent
— bands, ranges, spreads — not as a parenthetical after a number. A figure whose
uncertainty is only in a tooltip will be read as certain and quoted as certain.
This is where most dashboards fail and where this one must not.

**6. Accessibility is a correctness property.** WCAG AA contrast minimum. No
meaning carried by colour alone — specialty encoding needs shape or label too,
and a significant minority of any clinical audience has colour vision deficiency.
Full keyboard operation of the front browser and the selectors. Focus states
visible.

**7. Honest empty and loading states.** "No MDT opportunities found for this
ward-day" is a real result and must look like one, not like a broken panel. A
loading skeleton must not resemble data.

**8. It must survive a screenshot.** Findings travel as screenshots pasted into
emails and slide decks. Any view containing a figure must remain
self-explanatory when cropped out of context — which in practice means the
strategy key, method version, and date range are *in the view*, not in
surrounding chrome. ADR-0006 point 2 makes this a hard requirement.

**Technology.** Stay with vanilla three.js and plain ES modules. No framework.
The prototype already works, the interaction model is direct manipulation rather
than form state, and adding React here would be complexity for its own sake —
`02-ARCHITECTURE.md`'s standing test. Charts are hand-built SVG (D3 for scales
and shapes only, not for DOM management); the chart types needed are few and
specific, and a general charting library would fight the design system.

---

## Artefacts consumed

The viewer renders served data and holds **no domain logic** —
`02-ARCHITECTURE.md`'s layering rule applies to JavaScript. Concretely: the
front end must not compute a distance, a total, or an average. If a number
appears on screen, it came from an artefact.

```
artefacts/
  layout.json                      # locations, travel-graph edges   (SPEC-003)
  trajectories.json
  motion.{strategy}.json           # MotionReport incl. ci95, params (SPEC-003)
  front.{strategy}.json            # [{schedule, objectives}, ...]   (SPEC-004)
  opportunities.{strategy}.json    # missed-MDT candidates           (SPEC-002)
  coverage.{strategy}.json         # MDT coverage series             (ADR-0006)
  manifest.json                    # method version, date range, strategy list
```

The `{strategy}` key is structural, not cosmetic: SPEC-001 makes the
`RequiredSpecialty` definition a runtime choice, so every dependent artefact
exists once per strategy. `manifest.json` is loaded first and is the only place
the front end learns what is available.

## The strategy selector

Selects among `referral` (default), `consult_note`, `problem_list`, `union`,
`intersection`.

**It is not a filter.** Per SPEC-001 and ADR-0006, selection changes emphasis,
never availability:

- The selected strategy is emphasised; **the other four remain visible as a
  spread** in every figure showing coverage or MDT counts.
- `union` and `intersection` are always drawn as the outer bounds, so the
  plausible range is visible whatever is selected.
- Changing selection is instant (all strategies pre-computed) and animates
  between states, so the user *sees how much the answer depends on the
  definition*. That sensitivity is the most important thing this control
  communicates — arguably more important than any single value it shows.

Design intent: a user who changes this control should come away less confident in
any single number and more confident in the range. A selector that made one
number look authoritative would be actively harmful given ADR-0006's incentive
analysis.

## The Pareto front browser

The hardest interface problem here. Users compare 5–10 schedules across five
objectives and choose one.

- **Show the trade-off, not a ranking.** Any UI that sorts the front implies one
  objective dominates — the decision ADR-0004 deliberately removed from the maths
  and must not reintroduce in the interface.
- Parallel-coordinates plot across the five objectives, brushed to the 3D view.
- Selecting a point animates that schedule in the ward. The trade-off is far more
  intuitive as motion than as numbers, and this is the payoff for having a 3D
  view at all.
- **Always show today's observed baseline alongside**, so "better" is anchored to
  current reality rather than to the best of the alternatives.

## The governance view (N18)

Serves the Clinical Governance department per ADR-0006. Requirements follow
directly from that ADR and are not negotiable at implementation time:

1. MDT coverage over time, **with interval bands**, not a line of point estimates.
2. Strategy spread visible on every figure; selected strategy emphasised.
3. **Method version and date range rendered in-view** (design bar 8).
4. Drill-through from any figure to the contributing ward-days and, from there,
   to the observed events — the provenance chain SPEC-006 audit 3 verifies.
5. **No clinician-level breakdown.** Not a hidden feature, not permission-gated:
   absent. The UI should make clear this is a deliberate boundary rather than a
   missing feature, because it will be asked for.
6. Suppression: no cell below 5 patients (ADR-0005).

## Acceptance criteria

1. No hard-coded ward geometry; `layout.json` drives the scene. — `test_no_hardcoded_layout` (greps for coordinate literals)
2. Every displayed number traces to an artefact field; the front end computes none. — review checklist + `test_no_arithmetic_in_view_layer`
3. No motion or coverage figure renders without its interval and parameters. — `test_no_bare_point_estimates`
4. No figure renders without its strategy key and method version in-view. — `test_screenshot_self_sufficiency`
5. All five strategies are reachable and the spread renders for each. — `test_strategy_spread`
6. The front browser shows ≥5 schedules across all five objectives simultaneously, unsorted. — manual review
7. No patient or clinician identifier appears in any artefact or view. — `test_no_identifiers_in_artefacts`
8. WCAG AA contrast across the palette; no colour-only encoding. — `test_contrast_ratios` + review
9. Full keyboard operation of selectors and front browser. — manual review
10. `prefers-reduced-motion` honoured throughout. — `test_reduced_motion`
11. Loads and animates a full ward-day in <3 s on a mid-range laptop. — `bench`

## Failure modes

- **The front end recomputes something** and quietly disagrees with the report.
  The reason for criterion 2 and the no-logic rule.
- **The front browser implies a best option**, re-scalarising in the UI the
  decision ADR-0004 removed from the maths.
- **The strategy selector makes one number look authoritative** instead of
  exposing definitional sensitivity — inverting its purpose.
- **Prettiness over honesty.** A smooth animation of an operationally infeasible
  schedule is persuasive in the wrong direction. Feasibility marking is required
  on every rendered schedule.
- **The governance view drifts toward a KPI dashboard** — big number, green
  arrow, no interval. This is the strongest gravitational pull on the project and
  criteria 3 and 4 exist specifically to resist it.

## Open questions

- **[non-blocking]** Keep 3D, or is a 2D floor plan clearer for the governance
  audience? Likely answer: 3D for the ward/motion view where geometry is the
  point, 2D for the governance view where trend and interval are the point.
  Decide with users at N16 rather than by assertion.
- **[BLOCKING]** Does the governance view need to be shareable (hosted)? This is
  now blocked on **ADR-0008**: `pm4py` is AGPL v3, and AGPL §13 extends copyleft
  to network interaction, so hosting anything downstream of the discovery step
  may oblige the organisation to publish the source of this application. It also
  changes the data-governance posture entirely. No hosted deployment until both
  questions are answered.
