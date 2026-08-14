# ADR-0007 — Vector retrieval is gated on a measurement, runs locally, and never underpins a governance figure

**Status:** accepted · **Date:** 2026-08-14 · **Supersedes:** nothing
**Specified by:** SPEC-007 · **Constrained by:** ADR-0005, ADR-0006

## Context

The requirement was stated as: *"we need vectors for efficiency on understanding
context as well."*

It has two readings, and both are genuinely valuable, so both are being taken:

**(A) Agent context retrieval.** Semantic search over the repository's own text —
specs, ADRs, the audit log, the parsed metaheuristics reference, source
docstrings — so an agent retrieves the relevant passage rather than reading whole
files. `orchestration/graph.yaml` already mandates `no-bulk-reference-reads` and
`prefer-grep-over-read`; those rules currently rely on an agent successfully
guessing the right grep term. Retrieval is the mechanism that makes the rules
cheap to obey rather than merely stated. The largest single spend in the
repository — `essentials-of-metaheuristics.md`, ~863 KB ≈ 215k tokens — is also
the corpus that benefits most, because its content is indexed by page number and
algorithm number, neither of which an agent asking a conceptual question knows.

**(B) Trace embeddings.** Vector encodings of event sequences and ward-days, so
that similar ward-days can be compared, clustered, triaged for anomalies, and
used to warm-start the SPEC-004 optimiser. Trace clustering is established
process-mining practice and answers questions the current tooling cannot pose at
all.

These are **different systems that share one dependency**. Their governance
profiles are opposite: (A) touches no patient data whatsoever; (B) touches
nothing else. Treating them as one capability — one index, one module, one
mental model — is the failure this ADR is written to prevent, because it would
put a patient-derived vector one function call away from a corpus search.

Two further pressures apply. First, embeddings are the archetypal thing that
*sounds* obviously worth adding: the same reasoning shape ADR-0003 caught for the
C kernel, where a plausible efficiency argument produces a permanent dependency
against an unmeasured benefit. Second, this project's outputs are monitored by
Clinical Governance (ADR-0006, `00-VISION.md`) and must trace from figure to
evidence. A cosine distance in 384 dimensions has no such trace.

## Decision

**1. Both systems are built, separately, and neither shares storage or a module
with the other.** Part A indexes repository text into a git-ignored build
artefact. Part B indexes patient-derived vectors under `HWPM_DATA_DIR`. No
process opens both. Enforced by package boundaries (`hwpm.retrieve` vs
`hwpm.mining.embed`) and by the tests in SPEC-007 criteria 14 and 20.

**2. Nothing leaves the machine. Hard requirement, no exceptions clause.**
ADR-0005 rule 3. Local embedding models only. **No hosted embedding API for
anything derived from patient data** — sending a ward-day sequence to a remote
endpoint is an export outside the organisation's control, and deleting it
afterwards does not undo that. Trace vectors are treated as derived personal
data, not as anonymisation: embedding inversion recovers meaningful content, and
a ward-day vector plus a date narrows to a small population. They are therefore
git-ignored, live outside the repository, and are never read into an agent
context — exactly as the raw log is not.

**3. The index is gated on a measurement, not assumed.** Following ADR-0003:

| Step | Action | Exit condition |
|---|---|---|
| 1 | `ALGORITHM-INDEX.md` + page-anchor grep + ripgrep | Always available, costs nothing. |
| 2 | Build a labelled query set (≥ 30 realistic agent questions with hand-marked relevant passages) and measure the lexical baseline | **Stop if lexical recall@5 > 0.70.** No index is built. |
| 3 | Prototype hybrid index and measure on the same query set | Ship only if all of G1–G3 hold. |

- **G1 — corpus size ≥ 1.0 MB of indexable text.** *Measured 2026-08-14:
  `docs/` 102 KB + `src/` 142 KB + `refs/metaheuristics/` 871 KB ≈ 1.12 MB
  ≈ 280k tokens. Already met.*
- **G2 — lexical-only recall@5 ≤ 0.70** on the labelled set. **Not yet measured;
  this is the gate that decides the work.**
- **G3 — hybrid recall@5 ≥ lexical + 0.15 absolute**, MRR also improved, index
  build ≤ 60 s, warm query ≤ 300 ms on the reference machine.

Step 2 costs about an hour. If G2 fails, this work is closed as *not needed*, the
query set is retained as a regression asset, and that is a good outcome in the
same sense that a tractable CP-SAT model deleting NSGA-II would be (SPEC-004).

Part B carries its own gate: an interpretable ~12-feature ward-day vector with
k-means is built first, and a learned embedding is built only if it beats that
baseline by ≥ 0.10 adjusted Rand index against the synthetic generator's known
day-types, with ≥ 200 ward-days available. Below 200 days, clustering finds
structure in noise.

**4. Retrieval is hybrid — lexical *and* vector, never vector alone.** Grep is
strictly better for exact identifiers, symbol lookup, spec ids and page anchors,
and stays available as `--lexical-only` with no optional dependency. Vector
search earns its place only on the questions grep cannot express: an agent asking
*"how do we handle noisy fitness in calibration"* will not guess that the answer
is worded "elite fitness is re-evaluated each generation, never cached". Results
are fused by Reciprocal Rank Fusion — no tuned weight, because tuning α on 30
labelled queries would fit the queries rather than the corpus. Every hit records
which retriever found it.

**5. Embeddings may be used for retrieval, clustering, anomaly triage and
optimiser warm starts. They may NEVER be the basis of a reported governance
figure without an interpretable derivation published alongside.** This is the
most important sentence in the decision. `00-VISION.md` commits to a defensible
chain from figure to evidence; ADR-0006 engineers MDT coverage as a regulated
measurement whose authority is that visible chain. An embedding distance cannot
be explained to a consultant who disagrees with it, and per ADR-0006's rationale
that first challenge is the one the project cannot afford to lose.

Enforced structurally rather than by review: an import-linter contract forbids
`hwpm.analytics.governance` from importing `hwpm.mining.embed`, the governance
artefact schema rejects embedding-derived fields, and `WardDayVector.features`
(named, unit-carrying, interpretable) is always populated while
`WardDayVector.embedding` may be `None`. Any consumer that cannot work from
`features` alone is a consumer that must not feed a governance figure.

Anomaly output is confined to *candidates for human review*. "Atypical" means
unlike other days; it is not a synonym for worse, and it will be read as one the
first time it appears on a dashboard unless the naming prevents it.

**6. Tooling: `fastembed` + `sqlite-vec`, with `BAAI/bge-small-en-v1.5`.**
`sentence-transformers` is rejected because it pulls PyTorch (~2.5 GB installed)
for a job that runs a few times a week. `chromadb` is rejected because it
duplicates the embedding stack and contradicts `02-ARCHITECTURE.md`'s "no
database server". `faiss-cpu` is rejected because its index is an opaque sidecar
needing a separate metadata store — which would be SQLite, which we already have.
`fastembed` is ONNX Runtime plus tokenizers (~120 MB, CPU-only, no CUDA);
`sqlite-vec` is a ~1 MB loadable extension that keeps vectors and chunk metadata
in one file, one transaction, one backup, consistent with the existing
Parquet + SQLite decision. Model id and hash are recorded in `IndexStats` and
printed on every build, because a silently changed embedding model produces
silently different retrieval — the same class of problem as an unversioned method
change under ADR-0006.

**7. `hwpm govern` stays installable without any of it.** A new
`retrieve = ["fastembed>=0.3", "sqlite-vec>=0.1"]` extra, mirroring the existing
`analysis`/`optimize` split in `pyproject.toml`. `hwpm.retrieve` is imported
lazily inside the `context` subcommand. Absent dependencies produce exit code 2
and an install hint, never a traceback, and never an import failure anywhere in
`hwpm govern`.

**8. Chunk boundaries never cross a `<!-- page N -->` anchor** in the parsed
metaheuristics reference. Page numbers are the citation currency of
`SELECTION-GUIDE.md` and SPEC-004 ("Alg 104, p.143"); a chunk spanning pages
142–143 cannot be cited correctly, and a wrong page citation is worse than no
retrieval. 263 anchors exist and are the natural boundary.

## Rationale

The honest case for retrieval is not that semantic search is fashionable. It is
that this repository has already identified its largest avoidable spend, written
a rule against it, and left the rule dependent on an agent guessing the right
literal string. That is a control that works until it matters. Retrieval converts
it into a control that works by default — which, as ADR-0005 put it about
synthetic fixtures, is the only kind that survives contact with a deadline.

The gate exists because the same argument would justify a vector index over a
50 KB repository, where it would be pure overhead. The measurement in step 2 is
cheap and can only produce useful outcomes: either grep is adequate and we have
proof, or it is not and we have a baseline to beat and a regression set to keep.
Note that this gate is *harder to pass* than ADR-0003's, deliberately: a C kernel
adds a build burden, whereas an unnecessary retrieval layer adds a burden *and*
an authoritative-looking wrong answer.

Decision 5 is the one that would be traded away under pressure, so it is written
as a prohibition with structural enforcement rather than as guidance. The
mechanism of that trade is predictable: nobody proposes "let us base MDT coverage
on an embedding". Somebody proposes a "similar wards" comparator, or an
"atypical days" panel, and six months later a figure is being defended in a
governance meeting whose provenance ends at a cosine distance. The import-linter
contract stops that at the point it happens rather than at the point it is
noticed.

Separating the two systems physically, rather than by convention, follows the
same reasoning as ADR-0005's separation of raw data from the repository. Once a
corpus index and a patient-derived index are two tables in one file, the only
thing preventing a query from crossing between them is that nobody wrote that
query yet.

## Consequences

- Part A adds ~120 MB of optional dependency and a ~130 MB one-time model
  download to any machine that wants `hwpm context`. `hwpm govern` is unaffected.
  On a managed device the download may need approval — flagged as a blocking open
  question in SPEC-007.
- A labelled query set becomes a maintained asset. It is small, subjective, and
  written before any index exists so it cannot be relabelled to make a number
  move.
- Agents gain a fourth retrieval habit — `ALGORITHM-INDEX.md`, grep, page anchor,
  and now `hwpm context search` — and CLAUDE.md will need a line about it once
  the gate fires. Not before: documenting a capability that does not exist is how
  agents end up inventing commands.
- If G2 fails, none of this is built, and the ADR stands as the record of why —
  which is the point of writing it before rather than after.
- Part B acquires a permanent interpretable baseline that is not scaffolding. It
  is what the governance path uses regardless of whether the embedding path is
  ever built, in the same way ADR-0003's Python fitness reference is permanent.
- Some future requests will be refused on the strength of decision 5, including
  reasonable-sounding ones. Each refusal should offer the interpretable
  alternative rather than simply declining — the same posture ADR-0006 takes
  toward governance requests.
