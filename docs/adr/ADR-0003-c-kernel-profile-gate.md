# ADR-0003 — The C fitness kernel is gated on a profile, not assumed

**Status:** accepted · **Date:** 2026-08-14

## Context

`Essentials of Metaheuristics` §5 (Alg 67, p.101) observes that fitness
assessment dominates metaheuristic runtime and is embarrassingly parallel. For
P1 (MDT scheduling) one fitness evaluation replays a day's schedule across the
travel graph and scores five objectives; NSGA-II at realistic population and
generation counts needs order-of-millions of evaluations.

That is a genuine case for C. It is also exactly the kind of reasoning that
produces a premature FFI boundary, a build toolchain, and a class of
memory-safety bugs, in exchange for a speedup on code that turns out not to be
the bottleneck.

## Decision

The C kernel is written **only when a measurement says so**. The escalation
ladder, in order:

| Step | Action | Exit condition |
|---|---|---|
| 1 | Plain Python fitness function | Always built. It is the correctness oracle. |
| 2 | NumPy vectorisation | Stop if a realistic run finishes < 10 min. |
| 2.5 | Numba `@njit` | Stop if it clears the bar. A decorator beats an FFI boundary. |
| 3 | C kernel via CFFI | Only if a realistic run still exceeds **10 minutes** *and* profiling attributes **> 60%** of wall time to the fitness loop. |

"Realistic instance" is defined concretely so the gate cannot be argued: **one
30-bed ward, 8 clinicians, 3-hour round window at 5-minute resolution, NSGA-II
with population 200 for 500 generations.** The benchmark lives in
`tests/bench/test_fitness_throughput.py` and its numbers go in the audit log.

**Non-negotiable conditions on the C kernel if it is built:**

1. A differential test asserts agreement with the Python reference across
   randomised inputs, within a stated float tolerance. **If that test is
   deleted or skipped, the kernel is deleted.**
2. The Python path stays runnable and stays tested. `--no-native` must always
   work.
3. The kernel takes flat arrays and returns flat arrays. No domain objects cross
   the FFI boundary.
4. It is compiled with `-Wall -Wextra -Werror` and exercised under ASan in CI.

## Rationale

This inverts the usual failure. The common pattern is to write C because the
problem *sounds* compute-heavy, then discover the real cost was in pandas
group-bys in the data preparation. A measured gate costs one profiling run and
prevents a permanent maintenance burden.

The 10-minute threshold is chosen against the workflow, not arbitrarily: this is
batch analysis run a few times a week, so a 10-minute run is unremarkable. Were
it interactive, the threshold would be seconds and the gate would fire
immediately.

## Consequences

- `native/` stays empty until the gate fires. That emptiness is intentional and
  documented, so a future reader (or auditor) does not read it as an oversight.
- If the gate fires, we accept a C toolchain on Windows (MSVC or MinGW) as a
  build dependency, and the CI matrix grows.
- The Python reference is permanent, not scaffolding. It is what proves the fast
  path correct.
