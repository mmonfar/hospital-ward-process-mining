# Algorithm selection guide

Which metaheuristic we apply to which sub-problem in this project, why, and what
we deliberately rejected. Citations are `Alg N, p.P` into
`ALGORITHM-INDEX.md` / `essentials-of-metaheuristics.md`.

This document is **normative**: `docs/specs/SPEC-004` and the optimiser code must
match it, or this document must be amended first (see `docs/05-SELF-MANAGED-MODE.md`).

---

## Rule 0 — metaheuristics are a last resort

Luke's own framing (§0.1, p.9) is that a metaheuristic is what you reach for when
you know almost nothing about the problem: no gradient, no structure to exploit,
no exact algorithm that finishes in time. That is a *constraint on us*, not a
licence.

**Every sub-problem below must first be attempted with, and shown to fail, the
cheapest sufficient method**, in this order:

1. Closed-form or exact (ILP/CP-SAT via OR-Tools) — if it solves at real ward
   scale within the interaction budget, we ship that and no metaheuristic.
2. Greedy / constructive heuristic with a proven bound.
3. Metaheuristic.

**Baseline gate.** No metaheuristic result is reportable unless it beats
**Random Search (Alg 9, p.22)** and **Hill-Climbing with Random Restarts
(Alg 10, p.23)** on the same evaluation budget. If a genetic algorithm cannot
beat random restarts, the representation or the fitness function is wrong, and
tuning the GA will only hide that. This gate is a test, not a guideline —
see `tests/test_baseline_gate.py` in SPEC-004.

Scale sanity check: a 30-bed ward with 8 clinicians over a 3-hour round window
discretised to 5-minute slots is well inside exact-solver range. **We expect
CP-SAT to win outright for a single ward**, and metaheuristics to become
necessary only at hospital scale (9 wards, multi-day, stochastic durations) or
when we need a *Pareto set* rather than one optimum. Do not skip step 1.

### MEASURED 2026-08-15 (node N08) — Rule 0 fired

The expectation above was correct, and it was measured rather than assumed.
Instance seed 20260814, solver seed 2026, `workers=1`, reproducible via
`pytest tests/bench/test_cpsat_scale.py --bench`.

Realistic instance — 30 patients, 8 clinicians (9 multi-specialty), 36 slots
of 300s. Single-objective payoff table, **all five proven optimal**:

| Objective | Result | Wall |
|---|---|---|
| copresence | OPTIMAL | 3.91s |
| motion_m | OPTIMAL | 5.31s |
| disruption | OPTIMAL | 3.17s |
| continuity | OPTIMAL | 3.33s |
| makespan_s | OPTIMAL | 122.38s |

Epsilon-constraint sweep: **21 solves, 441s wall, every solve proven optimal**,
6 provably-empty boxes, front size 6. That is inside the 10-minute batch
threshold with room to spare.

**Consequence: NSGA-II (N10) is demoted to a cross-check** for the single-ward
problem. It is no longer the product; its value is agreeing with a known
optimum. This is the outcome Rule 0 exists to produce, and it removes a large
block of planned work.

**Scaling cliff — the result does not generalise.** Makespan, the hardest
objective, scaled 0.88s (10 beds) → 2.21s (20) → 124.6s proven (30) → *not
proven in 300s* (40). So exactness is established for one ward and nothing
larger. The genuine case for a metaheuristic reappears at hospital scale
(9 wards, multi-day, stochastic durations) and is measured at **N21** before
anyone assumes it either way. Do not read "CP-SAT won" as "CP-SAT wins".

Caveat on the 54-bed row, which returned INFEASIBLE in 3.75s: that instance is
over-subscribed by construction, not a solver limit. The generator can emit
infeasible instances on some seeds — a known defect, tracked separately — and
until it is fixed an INFEASIBLE result must never be read as a timing.

---

## P1 — MDT synchronisation scheduling *(the core problem)*

**Problem.** Assign each clinician a time-ordered sequence of bedside visits such
that, for patients under multiple subspecialties, the relevant clinicians are
co-present at the bedside. Constraints: theatre lists, clinic commitments,
nursing medication rounds, patient off-ward episodes (imaging, dialysis), and
acuity-driven ordering (sickest first).

**This is irreducibly multi-objective.** The objectives genuinely conflict and
the trade-off is a clinical management decision, not a mathematical one:

| Objective | Direction | Proxy measure |
|---|---|---|
| MDT co-presence | maximise | % of multi-specialty patients seen by ≥2 required specialties within one bedside episode |
| Clinician motion | minimise | metres travelled / inter-bed transitions per clinician |
| Nursing disruption | minimise | count of round-events colliding with med rounds and handover windows |
| Continuity of care | maximise | % of patients seen by the same named clinician as the previous day |
| Round makespan | minimise | last-visit-completed time |

Scalarising these into one weighted sum would bury the decision inside a weight
vector nobody can defend in a governance meeting. We produce a **Pareto front**
and let clinical leadership pick a point.

**Selected: NSGA-II (Alg 104, p.143).**

Supporting machinery, all from §7:
- Pareto domination test — Alg 98, p.139
- Front rank assignment by non-dominated sorting — Alg 101, p.141
- Sparsity (crowding-distance) assignment — Alg 102, p.142

*Why NSGA-II over SPEA2 (Alg 107, p.146):* both are defensible; NSGA-II's
crowding distance gives an even spread along the front, which is what we want
when a human is going to browse the options. SPEA2 is retained as a
**cross-check only** — if the two produce materially different fronts on the same
instance, that is evidence of a bug or a badly conditioned fitness function, not
a reason to prefer one. Wire it as a QA harness, not a runtime option.

**Local refinement: Tabu Search (Alg 14, p.28), feature-based variant (Alg 15, p.29).**

Used as the local-search step of a memetic/hybrid algorithm (§3.3.4). The natural
neighbourhood move here is *swap two visits between clinicians* or *shift one
visit by a slot*, and the characteristic failure mode of plain hill-climbing on
that neighbourhood is oscillating between two near-equal schedules forever. A
tabu list on recently-touched **(clinician, patient) features** — hence the
feature-based variant, since the plain variant's memory of whole schedules is
useless when the space is this large — kills that cycle directly.

Simulated Annealing (Alg 13, p.27) was considered for this slot and is the
fallback if tabu tenure tuning proves fragile; SA has one temperature schedule to
tune versus tabu's tenure plus aspiration criteria.

**Representation.** Not a bit string. A schedule is a set of per-clinician
permutations with time offsets, so we need §4 integer/permutation operators, not
§3.2's one-point crossover. Naive crossover on permutations produces invalid
schedules; the repair-vs-penalty decision is specified in SPEC-004.

---

## P2 — Motion waste and visit routing

**Problem.** Given one clinician's patient set, order the visits to minimise
distance travelled across the ward geometry already modelled in `web/`. This is a
TSP over bed coordinates with a few precedence constraints (isolation rooms last,
critically ill first).

**Selected: Ant Colony System (Alg 112, p.159)**, the ACS refinement of the
abstract ACO of Alg 109, p.154.

*Why ACO rather than a GA:* two reasons, one technical and one organisational.
Technically, ACO is constructive and native to edge-selection problems, which is
exactly the shape of routing. Organisationally, the **pheromone matrix is
directly interpretable** — it is a heat map over bed-to-bed transitions saying
"this movement is habitually worth making". That is a plot you can put in front
of a clinical director. A GA's population gives you no comparable artefact, and
interpretability is what gets an optimisation result adopted in a hospital rather
than politely ignored.

**Fast path: GRASP (Alg 108, p.153)** for the interactive browser what-if mode.
Greedy randomised construction plus local search is cheap enough to run per
keystroke; ACS runs in the batch analysis path.

Guided Local Search (Alg 113, p.162) is noted as a future option if we find the
routing landscape has deep local optima that ACS plateaus on — its penalty-based
escape is well suited to "this corridor keeps getting over-used".

---

## P3 — Simulation calibration

**Problem.** The agent-based ward simulation has real-valued parameters (walking
speed, per-activity service-time distributions, interruption rates). Fit them so
simulated motion reproduces the observed event log.

**Selected: Differential Evolution (Alg 38, p.57).**

Real-valued, low-dimensional (≈10–20 parameters), rugged, and — decisively — DE
has essentially nothing to tune beyond population size and two constants, which
matters because nobody on this project will have time to tune a tuner.

**Fallback: (μ + λ) Evolution Strategy (Alg 19, p.36)** with Gaussian convolution
(Alg 11, p.25), if self-adaptive mutation turns out to be needed.

**Noise handling is mandatory here.** Each fitness evaluation is a stochastic
simulation replication, so a lucky replication can enshrine a bad parameter set
as an elite forever. Elites must be **re-evaluated each generation** rather than
having their fitness cached — this is a known trap and is called out explicitly
in SPEC-004's acceptance criteria.

---

## P4 — Bed allocation / specialty cohorting

**Problem.** Assign patients to beds so that specialty cohorting reduces
*tomorrow's* motion, subject to isolation, gender, and acuity constraints.

**Selected: Genetic Algorithm with elitism (Alg 33, p.48)** over a subset/grouping
representation (Alg 49, p.72).

Lower priority than P1–P3: bed allocation is politically hard to change in a live
organisation, so this is a "show the prize" analysis rather than a deployed
optimiser. Sequenced last deliberately.

---

## P5 — Meta-optimisation (tuning the optimisers)

**Selected: Random Search (Alg 9, p.22) over hyperparameters, plus racing.**

Explicitly **do not nest metaheuristics**. A GA tuning a GA multiplies runtime by
the population size for a benefit that is nearly always inside the noise band.
Random search over a sensible box, with early termination of clearly-losing
configurations, is the honest cost/benefit choice.

---

## Deliberately rejected

| Candidate | Why not |
|---|---|
| **XCS / XCSF learning classifier systems** (Alg 137, p.213) | Tempting for "learn interpretable rules about when async rounds cause harm", but this is a supervised-learning and process-mining question with abundant labelled data. An LCS would be a research project delivering worse accuracy and worse interpretability than a fitted decision tree over conformance features. Revisit only if we move to online control. |
| **Estimation of Distribution Algorithms** (Alg 118, p.167) | Model-building overhead is not repaid at our problem sizes, and the learned distribution is harder to explain to a governance committee than a pheromone matrix or a Pareto front. |
| **Coevolution** (§6) | No adversary and no natural decomposition into interdependent populations. Would be complexity for its own sake. |
| **Genetic Programming** (§4.3) | We are not evolving programs. Scheduling policies are better expressed as parameterised rules and tuned with P5. |
| **Weighted-sum scalarisation for P1** | Hides the clinical trade-off in a weight vector. See P1. |

---

## Where the compute actually goes

§5 (Parallel Methods) makes the point that matters most for our architecture:
**fitness assessment dominates runtime and is embarrassingly parallel** —
Simple Parallel Fitness Assessment, Alg 67, p.101.

For P1, one fitness evaluation means replaying a full day's schedule across ward
geometry and scoring five objectives. At NSGA-II's typical population × generation
counts this is millions of evaluations. That single fact — not a general
preference for fast languages — is the entire justification for the gated C
kernel in `ADR-0003`. Everything outside the fitness loop stays in Python, where
it is readable and testable.
