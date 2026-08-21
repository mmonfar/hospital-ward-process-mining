# SPEC-005 — Front end: ward visualisation, strategy selection, governance view

**Status:** accepted · **Nodes:** N14a, N14, N18 · **Owner role:** architect (design), builder (implementation)
**Depends on:** SPEC-001, SPEC-003, SPEC-004 · **Last revised:** 2026-08-21
(2026-08-15: design-system section and criteria D1–D6 added at N14a.
 2026-08-21: decisions 11–13 and criteria 12–14 added — register, disclosure and
 tiering. Addition only; nothing above was altered.)

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

## The design system (N14a) — normative

Built at N14a, ahead of any screen. What follows records the decisions the
design bar left open, so that N14 and N18 inherit a system rather than
re-deciding it per view.

### Where it lives

| Path | Status |
|---|---|
| `src/hwpm/design/tokens.py` | **Source of truth.** Palette, type, space, elevation, motion, and the normative contrast table |
| `src/hwpm/design/colour.py` | Contrast, CIELAB, dichromat simulation — the maths the checks run on |
| `web/design/tokens.css`, `tokens.js` | **Generated.** Never hand-edited; `hwpm design check` fails on drift |
| `web/design/base.css` | The only stylesheet. There are no per-component stylesheets |
| `web/design/*.js` | Primitives: `svg`, `scale`, `motion`, `figure`, `states`, `legend`, `strategy`, `series-chart`, `parallel` |
| `web/design/gallery.html` | The review surface: every token, primitive and state on one page |

The palette is defined in Python rather than in CSS because design bar 6 makes
accessibility a correctness property, and a correctness property has to be
testable. `python -m hwpm.cli design check` prints the ratios; `hwpm design
build` regenerates the CSS and JS.

### Decisions

1. **Colour is declared once.** `tokens.py` emits the CSS and JS; components
   reference semantic tokens (`--ink-quiet`, `--data-observed`), never ramp
   steps and never literals. A literal colour anywhere in `web/design/` fails
   `test_no_literal_colours_outside_the_generated_tokens`. The first hard-coded
   shade is never the problem — it is the precedent.

2. **Categorical colour is capped at five, plus an `other` bucket.** Under the
   AA contrast floor the usable lightness range is about thirty L\* units, and
   past five categories colour stops being decodable — particularly for readers
   with a colour vision deficiency. The five were chosen by maximin search over
   muted in-gamut candidates under simulated protanopia, deuteranopia and
   tritanopia, with the prototype's teal held fixed. Floor: ΔE76 ≥ 15 under
   every simulation; achieved 18.7. A view needing more distinctions uses
   position or small multiples, not more hues.

3. **Every series carries a shape and a fill pattern**, not colour alone. This
   is stronger than WCAG requires and is the reason the legend primitive draws
   the mark the chart draws.

4. **The RequiredSpecialty strategies share one hue and differ by dash.** They
   are a definitional axis with union and intersection as outer bounds
   (SPEC-001), not five categories. Five hues would say "five alternatives, pick
   one"; one hue with the selection emphasised says "one quantity, defined five
   ways", which is what is true.

5. **There is no success-green and no alarm-red in the semantic palette.**
   Enforced by `test_no_traffic_light_colour_in_the_semantic_set`. The strongest
   failure mode in this spec is the governance view drifting into a KPI
   dashboard; a palette with no "good" colour cannot make that judgement for the
   reader, which leaves the interval as the thing to read.

6. **Three durations, reduced motion handled at the token layer.**
   `prefers-reduced-motion` neutralises the duration custom properties in
   `tokens.css`, so components written later inherit it without knowing it
   exists; `motion.js` mirrors it for JS-driven animation. Reduced motion always
   arrives at the same final state — never a different result.

7. **The rules are enforced by the primitives, not by review.** `figure()`
   throws without strategy, method version and date range. `statistic()` throws
   on a point estimate unless `exact: true` is passed deliberately.
   `timeSeries()` throws on a series without an interval, and on a spread
   missing its bounding strategies. `paretoFront()` throws without a baseline
   and contains no ordering call. Criteria 3 and 4 hold for a month if they are
   a checklist; they hold indefinitely if the constructor refuses.

8. **D3 is not used.** The spec permits it for scales and shapes; the five chart
   types here need linear and band scales and a tick generator, which is
   `scale.js` at ninety lines, against a CDN dependency with an integrity pin to
   maintain on a machine that may be offline inside a hospital network. Recorded
   as a decision rather than left as an omission.

9. **Criterion 2's boundary.** "The front end computes none" means *statistics*
   — a total, a mean, a distance, an interval. Mapping a value to a pixel is
   rendering. `web/design/scale.js` is the declared exception and the only place
   arithmetic on data values is allowed; the test that lands with N14 exempts it
   by name.

10. **A defect in the prototype was fixed rather than inherited.** The
    prototype's muted label colour `#8a9599` is 2.7:1 on the page and fails AA
    outright. It survives as a decorative-separator token only; label text moved
    to a darkened neutral (`#54666b`, 5.29:1). Anything set in the old tone in
    `web/hospital-ward.html` is a known defect to be corrected at N14.

### Decisions added 2026-08-21 (N14-viewer follow-on)

The first built version of the viewer satisfied every criterion above and was
still not usable by the audience in the Problem statement. Read cold by a
Clinical Governance reader, it presented as an audit trail: subtitles cited
`ADR-0004`, `SPEC-002` and `N06` inline, five figures of equal weight rendered
simultaneously each carrying a paragraph of methodological caveat, and the
withheld-coverage explanation appeared twice on one screen. Nothing was
*wrong* — criteria 1–11 and D1–D6 all held. The gap was that this spec
constrained what a figure must **contain** and never what it must **read
like**, and containment turns out to be satisfiable by documentation.

**11. Internal citations are not user-facing prose.** No ADR number, SPEC
number, graph-node id or artefact filename appears in any text a reader sees
before they open a disclosure — not in a title, subtitle, headline, caption,
axis label, provenance strip or page title. This is not a softening of the
provenance requirement and nothing is deleted: every citation stays on the page,
inside the figure's own method panel, one keystroke away. The audience for this
work has never heard of this project's decision records, and prose that cites
them at a reader reads as a tool describing itself rather than as a finding —
which costs exactly the credibility the design bar exists to buy.

**12. Every figure opens with a plain-language sentence, and method collapses
behind it.** A figure whose first element is a statistic is read as a statistic;
the number is the evidence, not the finding. The sentence is written in clinical
register and is pure substitution of fields already in the artefact — it states
no qualifier (*"just over half"*, *"only"*, *"disappointing"*) that the artefact
does not contain, because a qualifier is a judgement and decision 5 keeps
judgement with the reader. Methodological caveat prose, estimator detail and
citations go in a collapsed panel. **The interval, the denominator and the
strategy key never do**: design bar 5 is explicit that uncertainty behind a
disclosure is read as absent.

Figures also carry an **epistemic status** — how the number came to exist —
drawn from a closed set: measured, upper bound, for review, modelled, reference,
withheld. These are deliberately *not* evaluative. A brief for this work asked
for good / concerning / uncertain encoding using the palette; that is refused
here and the refusal is recorded rather than silently taken, because decision 5
and the KPI-dashboard failure mode both forbid the interface making the
judgement on the reader's behalf. "Measured" and "Upper bound" describe
provenance; "good" would describe a verdict the tool is not entitled to.
Status is carried by a printed word *and* a rule that varies in hue and line
style, never by colour alone.

**13. Figures are tiered, not equal.** A page of *n* equally weighted figures
has *n* entry points and therefore none. The primary tier answers the question
the page exists to answer and must be legible without reading a paragraph: the
ward scene, whether joint review is happening (coverage and its near-miss
candidates are one question and belong in one figure), and the schedule
browser. Supporting measures — currently motion waste, which is a ceiling
rather than a finding — render as a collapsed tile carrying the headline
number, its interval, one sentence and its provenance, expanding to the full
figure on request. A tile is smaller because it carries less prose, never
because it carries less evidence: `kpiTile()` throws without an interval or
provenance exactly as `figure()` and `statistic()` do, since a tile is *more*
likely to be cropped into a slide than a full figure, not less.

### Design-system acceptance criteria

| # | Criterion | Test |
|---|---|---|
| D1 | Every declared foreground/background pair clears its WCAG floor | `test_contrast_ratios` |
| D2 | The five categorical slots stay ≥ ΔE 15 apart under all three dichromat simulations | `test_series_separable_under_colour_vision_deficiency` |
| D3 | No meaning is carried by colour alone — every series has a distinct shape, every strategy a distinct dash | `test_no_colour_only_encoding`, `test_strategies_are_distinguished_without_colour` |
| D4 | The generated CSS and JS match `tokens.py`, and no other file declares a colour | `test_generated_files_match_their_source`, `test_no_literal_colours_outside_the_generated_tokens` |
| D5 | No literal durations; reduced motion neutralises every one and stops loops | `test_motion_durations_come_from_tokens`, `test_reduced_motion_is_honoured_by_the_token_layer`, `test_looping_animation_is_stopped_not_merely_shortened` |
| D6 | The provenance, interval, bounds and baseline guards are still in the primitives | `test_figure_frame_still_requires_provenance` and the three beside it |

D6 is a presence check, not a behavioural one: it asserts the guard is in the
file, not that it fires. The behavioural test needs a DOM and a JS runner and
belongs with N14, where more than four assertions justify a headless browser.
Named this way so the coverage is not read as stronger than it is.

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
12. No ADR number, SPEC number, node id or artefact filename appears in text
    rendered before a disclosure is opened; every one of them remains reachable
    inside one. — manual review (no JS test harness in this repo; see D6's note
    on not naming coverage stronger than it is)
13. Every figure carries a plain-language sentence and an epistemic status from
    the closed set, and no status is evaluative. — `plainSummary()` throws on a
    missing sentence or an unknown status + manual review
14. Supporting measures render collapsed, and render their interval and
    provenance while collapsed. — `kpiTile()` throws without either + manual
    review

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
