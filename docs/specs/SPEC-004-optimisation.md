# SPEC-004 — MDT synchronisation and route optimisation

**Status:** accepted · **Nodes:** N08–N13 · **Owner role:** architect
**Depends on:** SPEC-001, SPEC-002, SPEC-003 · **Normative reference:** `refs/metaheuristics/SELECTION-GUIDE.md`
**Last revised:** 2026-08-14

## Problem

Given a ward-day's patients, their required specialties, clinician availability
and ward geometry, produce schedules where the required specialties meet at the
bedside — and quantify what that costs.

## In scope

- Exact CP-SAT formulation for the single-ward instance (N08).
- Random-search and hill-climbing baselines (N09).
- NSGA-II multi-objective scheduler (N10).
- Ant Colony System visit routing (N11).
- Fitness throughput benchmark and the ADR-0003 decision (N12–N13).

## Out of scope

- Staff rostering. We schedule *rounds*, given who is available.
- Real-time re-planning.
- Nested metaheuristics (P5 in the selection guide: random search + racing only).

## Rule 0 outcome — measured 2026-08-15 (N08)

CP-SAT solves the realistic single-ward instance to **proven optimality on all
five objectives**, and the epsilon-constraint sweep returns a **proven-optimal
6-point Pareto front in 441s**. Full numbers in `SELECTION-GUIDE.md`.

**N10 (NSGA-II) is therefore demoted to a cross-check** at this scale, its
budget cut and its gate raised to `confirm`. **N21** measures the multi-ward,
multi-day instance before any assumption is made about hospital scale, where
the 30→40 bed cliff says CP-SAT does not obviously reach.

One finding worth stating separately, because it is the project's actual
question and it will be quoted: on the Pareto front, the point achieving
**89% MDT co-presence sits at exactly the same routed distance (1398.87 m) as
the motion-minimising point**, which achieves only 11% co-presence. On this
instance, synchronising nearly all multi-specialty patients cost **no additional
walking at all** — it is a coordination gain, not a trade-off purchased with
clinician time.

**This is synthetic data.** It demonstrates the method answers the question and
that the answer is not foreclosed by geometry. It says nothing yet about any
real ward, and must not be reported as if it did (N16, N17).

## Order of work — non-negotiable

Rule 0 of the selection guide, enforced by the graph's dependency edges:

1. **N08 exact first.** A 30-bed ward, 8 clinicians, 3-hour window at 5-minute
   resolution is plausibly inside CP-SAT's reach. **If it solves within the
   interaction budget, CP-SAT is the product** and NSGA-II is demoted to a
   cross-check. This would delete a large amount of planned work, which is a good
   outcome, not a disappointing one.
2. **N09 baselines next.** Random Search (Alg 9, p.22) and Hill-Climbing with
   Random Restarts (Alg 10, p.23).
3. **N10 NSGA-II only after** it is shown to beat both on equal evaluation budget.

## Objectives (ADR-0004: no scalarisation)

| # | Objective | Direction | Measure |
|---|---|---|---|
| 1 | MDT co-presence | max | % of multi-specialty patients with ≥2 required specialties in one bedside episode |
| 2 | Clinician motion | min | total `TravelGraph` metres |
| 3 | Nursing disruption | min | round events colliding with protected windows |
| 4 | Continuity of care | max | % of patients seen by the same clinician as the previous day |
| 5 | Makespan | min | last visit completion time |

Output is a **Pareto front**, not one schedule. See ADR-0004 for why this is a
governance requirement rather than a technical preference.

## Interface

```python
# hwpm.optimize
@dataclass(frozen=True) class Instance:
    patients: list[Patient]; clinicians: list[Clinician]
    constraints: list[Constraint]; graph: TravelGraph; slots: SlotGrid

@dataclass(frozen=True) class Objectives:
    copresence: float; motion_m: float; disruption: int
    continuity: float; makespan_s: int

class Scheduler(Protocol):
    def solve(self, inst: Instance, budget: Budget, rng: Random) -> list[Schedule]: ...

def evaluate(schedule: Schedule, inst: Instance) -> Objectives: ...   # the hot loop
def pareto_front(scored: list[tuple[Schedule, Objectives]]) -> list[...]: ...  # Alg 98/100
```

`evaluate` is the single function the ADR-0003 profile gate is about. It has a
plain-Python reference implementation that is **permanent**, not scaffolding.

## Algorithm bindings

Normative; changing any row requires amending `SELECTION-GUIDE.md` first.

| Component | Algorithm | Ref |
|---|---|---|
| Multi-objective scheduler | NSGA-II | Alg 104, p.143 |
| Domination test | Pareto Domination | Alg 98, p.139 |
| Front ranking | Non-Dominated Sorting | Alg 101, p.141 |
| Diversity | Sparsity / crowding distance | Alg 102, p.142 |
| Local refinement | Feature-based Tabu Search | Alg 15, p.29 |
| Routing | Ant Colony System | Alg 112, p.159 |
| Interactive routing | GRASP | Alg 108, p.153 |
| Calibration (P3) | Differential Evolution | Alg 38, p.57 |
| Baselines | Random Search / HC+restarts | Alg 9 p.22 / Alg 10 p.23 |
| QA cross-check | SPEA2 | Alg 107, p.146 |

**Representation.** A schedule is per-clinician permutations with time offsets,
not a bit string — §4 integer/permutation operators, not one-point crossover.
Invalid offspring are **repaired**, not penalised: penalty terms on a
heavily-constrained space produce populations that are almost entirely infeasible,
and the repair operator is itself a documented modelling choice.

**Tabu tenure** is on `(clinician, patient)` features, not whole schedules — the
plain variant's memory of entire solutions is useless at this scale.

## Acceptance criteria

1. CP-SAT returns a proven optimum for a ≤10-bed instance matching brute force. — `test_cpsat_optimal`
2. NSGA-II beats random search and HC+restarts on hypervolume at equal evaluation budget. — `test_baseline_gate` **(gate 9)**
3. All returned schedules satisfy every hard constraint; a violation raises. — `test_constraint_satisfaction`
4. The returned front is genuinely non-dominated. — `test_front_is_nondominated`
5. Fixed seed ⇒ identical front. — `test_determinism` **(gate 8)**
6. SPEA2 cross-check produces a front within tolerance of NSGA-II's; divergence fails the build. — `test_spea2_crosscheck`
7. Repair preserves feasibility and terminates. — `test_repair_terminates`
8. In calibration (P3), elite fitness is re-evaluated each generation, never cached. — `test_noisy_elites_reevaluated`
9. `evaluate` throughput is benchmarked and recorded. — `tests/bench/test_fitness_throughput.py`
10. If the C kernel exists, it agrees with the Python reference across randomised inputs within tolerance. — `test_native_differential` *(deleting this test deletes the kernel — ADR-0003)*

Criterion 8 guards the classic noisy-fitness trap: a lucky simulation
replication enshrines a bad parameter set as an elite permanently.

## Test oracle

Brute force on small instances; CP-SAT's proven optima as ground truth for the
metaheuristics on medium instances; mathematical invariants for the front.

## Failure modes

- **Optimising a fiction.** If `RequiredSpecialty` (SPEC-001 N04) is wrong, this
  module produces beautiful schedules for the wrong patients. The graph makes
  N08 depend on N04 for exactly this reason.
- **A schedule that is optimal and unimplementable.** Mitigated by keeping
  clinicians in the loop at N16, and by treating the output as options rather
  than instructions.
- **Premature C.** Addressed by ADR-0003's measured gate.
- **Tuning to the answer.** Hyperparameters are fixed before the final run and
  recorded in the artefact header.

## Open questions

- **[non-blocking]** Are protected nursing windows documented, or must they be
  inferred from the event log?
- **[non-blocking]** Is continuity measured against a named clinician or a team?
  Materially changes objective 4; team is the more achievable target.
