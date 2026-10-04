# Hospital ward process mining model

Measuring motion waste and finding **MDT synchronisation opportunities** from
hospital ward event logs.

## The problem

Ward rounds here are asynchronous. Each subspecialty rounds on its own schedule,
so a patient under cardiology, renal and surgery is visited three times, hours
apart, by three teams who never meet. The consequences are concrete: nursing
workflow is interrupted unpredictably, information is scattered across three
partial notes, continuity of care is not guaranteed, and clinicians walk the
building in an order driven by their own list rather than by geography.

The sharpest case: a surgical registrar physically walks past a ward containing
one of their patients, and the joint review does not happen because nobody knew
they were there. Detecting exactly that is `detect_opportunistic` in SPEC-002 —
the highest-value output here, because acting on it needs a notification rather
than an organisational change.

Full problem statement: [`docs/00-VISION.md`](docs/00-VISION.md).

## What it does

| Capability | Spec |
|---|---|
| Ingest event logs into a canonical, pseudonymous stream | SPEC-001 |
| Discover the real round process; detect MDT moments and missed opportunities | SPEC-002 |
| Quantify motion waste over a routed travel graph, with uncertainty | SPEC-003 |
| Produce a Pareto set of synchronised-MDT schedules (exact CP-SAT baseline, random/hill-climbing baselines, ACS routing, NSGA-II) | SPEC-004 |
| Visualise it — ward scene, Pareto browser, governance coverage view | SPEC-005 |
| Vector/hybrid context retrieval over the project's own docs (measured, lexical-only shipped) | SPEC-007 |
| Audit the code and the method | SPEC-006 |

## Status

**20 of 24 planned nodes are complete.** The optimiser stack, motion-waste
analytics, MDT-coverage governance view, ward viewer, and automated
code/method auditor are all built, tested and committed — see
[`docs/AUDIT-LOG.md`](docs/AUDIT-LOG.md) for the full, honestly-reported
history, including negative results (a learned trace embedding and a hybrid
vector retriever were both built, measured, and rejected in favour of the
simpler interpretable/lexical alternative — the numbers are in the log).

Two things remain, both requiring a human, not an agent:

- **`N16-face-validity`** — clinician review of the discovered process model.
  Nothing here has been checked against a real clinician's judgement yet.
- **`N17-real-data`** — first run against real data. A hard stop by design
  (`orchestration/graph.yaml`): no agent may proceed past it under any
  circumstances.

Everything in this repository, including the demo bundle in `web/demo/`, is
synthetic. Run `hwpm govern graph -v` for the live state.

## Quick start

```bash
pip install -e ".[dev,analysis,optimize,retrieve]"
python -m hwpm.cli govern graph -v
python -m hwpm.cli govern budget --by-model
python -m pytest -q
```

### Viewer demo

```bash
python tools/generate_viewer_artefacts.py   # regenerate web/demo/ (optional -- already committed)
python -m http.server 8799 --directory web
```

Then open `http://localhost:8799/viewer.html`.

## How this project is run

Built largely by autonomous agents under a spec-driven protocol:

- **`orchestration/graph.yaml`** is the work graph — 24 nodes with dependencies,
  an assigned model role, a token budget, and a gate. It is data: `hwpm govern
  graph` reads it to decide what is runnable.
- **`docs/specs/`** is normative. No implementation without a spec (ADR-0001).
- **`docs/AUDIT-LOG.md`** is append-only: what was done, why, under what
  authority, by which model.
- **`docs/05-SELF-MANAGED-MODE.md`** defines the autonomous loop and the four
  conditions under which an agent stops and asks.

Agents read `CLAUDE.md` first.

## Method

Optimisation choices follow `refs/metaheuristics/SELECTION-GUIDE.md`, which maps
each sub-problem to a specific algorithm from Sean Luke's *Essentials of
Metaheuristics* and records what was rejected. Its Rule 0 governs everything:
**metaheuristics are a last resort** — exact methods first, then greedy, and
nothing is reportable until it beats random search. That gate has fired twice
so far: NSGA-II was demoted to a cross-check when CP-SAT proved optimal at
single-ward scale, then reinstated once measurement showed hospital scale
changes the answer (`docs/AUDIT-LOG.md`, N08/N21).

MDT scheduling is solved multi-objectively and delivered as a Pareto front, not
a single answer. The trade-off between protecting nursing time and minimising
clinician walking is a management decision, and scalarising it into a weight
vector would hide that decision rather than make it (ADR-0004).

## Data protection

No identifiable data enters this repository or any model context. Raw logs live
outside the repo under `HWPM_DATA_DIR`; development runs on synthetic fixtures;
aggregates are suppressed below 5 patients and never attributed to a named
clinician. See **ADR-0005**, which is a hard stop, not a guideline.

## Project structure

| Path | What |
|---|---|
| `docs/01-DOMAIN-MODEL.md` | Ubiquitous language, entities, design rules |
| `docs/02-ARCHITECTURE.md` | Layering, language choice |
| `docs/adr/` | Architecture decisions, amended by addition only |
| `src/hwpm/` | The governed Python package (domain, ingest, mining, analytics, optimize, retrieve, govern) |
| `web/` | The ward viewer (three.js), served statically against `web/demo/` |
| `tests/`, `tests/bench/` | Unit tests and benchmark/gate measurements |

## Licence

Code is licensed under **AGPL-3.0-or-later** (see [`LICENSE`](LICENSE)); a commercial licence is available on request from the author via [LinkedIn](https://www.linkedin.com/in/martin-monteagudo-farina/). Non-code content is under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Details in [`LICENSING.md`](LICENSING.md).

## Disclaimer

Research and demonstration software. Not a medical device and not intended for clinical decision-making, diagnosis or treatment. Provided "as is", without warranty of any kind; the author accepts no liability for any use. Uses synthetic data only.

Built with AI assistance (Claude); all code and claims reviewed by the author. See [`LICENSE`](LICENSE) for the full warranty terms.

## Independence and data notice

**Independence and data notice.** This is a personal project, developed independently in my own time and on my own equipment. It is not affiliated with, endorsed by, or representative of my employer or any other organisation. It contains no employer data, systems, code or confidential information. All data in this repository is synthetic or fictitious, and any resemblance to real patients, staff or events is coincidental. Views are my own.
