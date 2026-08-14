# 06 — QA, dead code, and the future auditor

**Status:** accepted · **Last revised:** 2026-08-14

Covers brief items 5 (a code auditor later) and 6 (iterate for dead code and QA).

## Gates

A graph node is not complete until all of these pass. They run in ascending cost
order so failures surface cheaply.

| # | Gate | Tool | Threshold |
|---|---|---|---|
| 1 | Format | `ruff format --check` | clean |
| 2 | Lint | `ruff check` | clean |
| 3 | Types | `mypy src/` | clean on `src/hwpm/domain`, advisory elsewhere initially |
| 4 | Unit tests | `pytest -q` | all pass |
| 5 | Coverage | `pytest --cov=hwpm` | ≥85% on `domain/`, ≥70% overall |
| 6 | Layering | `import-linter` | `domain/` imports nothing from outer layers |
| 7 | Dead code | `vulture --min-confidence 80` | no findings, or each whitelisted with a reason |
| 8 | Determinism | `tests/test_determinism.py` | same seed ⇒ same output |
| 9 | Baseline gate | `tests/test_baseline_gate.py` | metaheuristics beat Alg 9 / Alg 10 |

Gates 6, 8 and 9 are the project-specific ones and are where the real risk is.

**Gate 6 (layering)** turns `02-ARCHITECTURE.md`'s central rule from an intention
into a test. Architectures decay by exactly one import at a time.

**Gate 8 (determinism)** matters more here than in most projects. Every algorithm
in `SELECTION-GUIDE.md` is stochastic. Without seeded, reproducible runs, no
result can be re-derived by an auditor and no regression can be distinguished
from variance. Every stochastic component takes an explicit `rng` — never
module-level `random`.

**Gate 9 (baseline)** encodes Rule 0: if NSGA-II cannot beat random restarts on
the same evaluation budget, the representation or the fitness function is wrong.
This runs as a test because it is the kind of check that is otherwise done once,
early, on a toy instance, and never again.

## Dead code

Dead code in an agent-built repository accumulates faster than in a
human-built one: a session builds a helper, a later session solves the problem
differently, and nothing removes the first attempt.

**Standing sweep**, run at the start of any session beginning a new node:

```bash
vulture src/ tools/ --min-confidence 80
ruff check --select F401,F841 src/ tools/
git ls-files 'src/**/*.py' | while read -r f; do
  base=$(basename "$f" .py)
  [ "$base" = "__init__" ] && continue
  grep -rqF "$base" --include='*.py' --include='*.md' --include='*.yaml' . || echo "orphan: $f"
done
```

**Deletion policy.** Dead code gets deleted, not commented out and not moved to
`legacy/`. Git holds the history; a commented-out block holds only confusion.
Deletion is logged with the reason, so an auditor can see the removal was
deliberate rather than accidental.

**One explicit exemption.** `native/` is intentionally empty until the ADR-0003
gate fires, and `optimize/` will hold a Python reference implementation that
looks redundant once a faster path exists. That reference is *not* dead code — it
is the correctness oracle, and ADR-0003 makes deleting it grounds for deleting
the C kernel too. Whitelisted with this reason.

## Test strategy

- **Domain**: pure unit tests, no I/O, high coverage. Cheap and fast.
- **Ingestion**: property-based (`hypothesis`) over the synthetic generator.
  Timestamps, out-of-order events, and missing locations are where real logs
  break parsers, and examples chosen by hand will not find those.
- **Mining/analytics**: golden-file tests against synthetic logs with a *known*
  ground truth — the generator knows the process it generated, so conformance can
  be checked against truth rather than against a previous output.
- **Optimisation**: seeded determinism, the baseline gate, and known-optimum
  instances small enough to brute-force.
- **Never**: tests asserting a specific floating-point objective value from a
  stochastic run. Assert invariants and relative improvement instead.

## The auditor (SPEC-006, node N15)

Planned, not yet built. Deliberately deferred — an auditor written before there
is substantial code audits nothing and hard-codes assumptions that will be wrong.

Its scope, when built, is broader than a linter:

1. **Spec conformance** — does each module do what its spec says, and is anything
   in `src/` unauthorised by any spec?
2. **Method audit** — are the statistical and optimisation choices in the code the
   ones `SELECTION-GUIDE.md` specifies? Is the baseline gate genuinely enforced?
3. **Provenance audit** — can every number in every report be traced back through
   the artefact chain to the events that produced it?
4. **Governance audit** — does any code path read raw data, and does every
   aggregate respect the suppression floor in ADR-0005?
5. **Audit-log integrity** — does the log account for every commit?

Run by the `reviewer` role, on Opus, **never by the model that wrote the code**
(`04-AGENT-ORCHESTRATION.md`). Its findings go to the audit log whether or not
they are acted on — an audit that only records what got fixed is not an audit.
