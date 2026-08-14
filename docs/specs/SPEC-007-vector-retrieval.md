# SPEC-007 — Vector retrieval: agent context search, and trace embeddings

**Status:** draft · **Nodes:** proposed N19-context-retrieval, N20-trace-embeddings
(neither exists in `orchestration/graph.yaml` yet — adding them is the user's call,
see Open questions) · **Owner role:** architect
**Depends on:** SPEC-002 (episodes), SPEC-004 (optimiser warm starts)
**Normative references:** ADR-0005, ADR-0006, ADR-0007 · **Last revised:** 2026-08-14

## Problem

The requirement as stated was: *"we need vectors for efficiency on understanding
context as well."* That sentence has two readings, both of which are real needs
in this project, and they are **different systems that happen to share one
dependency (an embedding model)**. Conflating them is the main risk this spec
exists to prevent, because they have opposite governance profiles: one touches
no patient data at all, the other touches nothing else.

- **Part A — agent context retrieval.** Semantic search over *this repository's
  own text* (specs, ADRs, the audit log, the parsed metaheuristics reference,
  source docstrings) so that an agent retrieves the relevant passage instead of
  reading whole files. This is the mechanism that makes the `budget.rules` in
  `orchestration/graph.yaml` — `no-bulk-reference-reads` and
  `prefer-grep-over-read` — *cheap to obey* rather than merely mandated. No
  patient-derived data is involved at any point.

- **Part B — trace and activity embeddings.** Vector encodings of event
  sequences, `BedsideEpisode` streams and ward-days, so that similar ward-days,
  round patterns and patient journeys can be compared, clustered and used for
  anomaly triage and optimiser warm starts. Trace clustering is established
  process-mining practice. Everything here is derived from patient data and is
  therefore governed by ADR-0005 and constrained by ADR-0006.

They are specified in one document because they share an embedding runtime and a
dependency-weight decision. They share **nothing else**: separate packages,
separate index files, separate storage locations, separate gates. A later session
that finds itself passing a ward-day vector into the context searcher, or a
corpus chunk into the trace clusterer, has made a mistake this paragraph
predicted.

## In scope

**Part A**
- Chunking of markdown and Python source into retrievable passages with stable
  `file:line` provenance, respecting the `<!-- page N -->` anchors that carry the
  page citations `SELECTION-GUIDE.md` and SPEC-004 depend on.
- A local embedding index over that corpus.
- A **hybrid** retriever: lexical (BM25/ripgrep-class) *and* vector, fused. Not
  vector alone.
- `hwpm context index | search | eval` CLI, returning ranked passages with
  `path:start-end` citations.
- A labelled query set and the retrieval-quality measurement that gates whether
  the index is built at all (ADR-0007).

**Part B**
- A ward-day / trace encoder producing a fixed-length vector from an episode
  sequence.
- Clustering of ward-days into cohorts with similar round structure.
- Distance-based anomaly triage: atypical ward-days surfaced for clinical review.
- Nearest-neighbour warm starts for the SPEC-004 optimiser.
- The interpretable-feature baseline that Part B must beat before it is built.

## Out of scope

- **Any hosted embedding API, for either part.** ADR-0005 rule 3. See Modelling
  assumptions.
- **Any generative model in the retrieval loop.** `hwpm context search` returns
  passages, not summaries. A summariser would put a paraphrase between the agent
  and the source, which is precisely the traceability property SPEC-006 audit 3
  requires we keep.
- **Fine-tuning or training an embedding model** on ward data. Off-the-shelf
  weights, used as-is. Training on patient data creates a model artefact that is
  itself derived personal data, with no offsetting benefit at this corpus size.
- **Replacing grep.** Lexical search stays the primary tool for exact identifiers
  and symbol lookup, and stays available as `--lexical-only`.
- **Embedding-derived governance figures.** Prohibited outright — ADR-0007
  decision 4, and Acceptance criterion 14 below.
- Retrieval-augmented generation as a product feature for clinicians. Not asked
  for, not needed, and would inherit every explainability problem in Part B.

---

# Part A — Agent context retrieval

## Why this is not just grep

Grep answers *"where does this exact string appear"*. It is exact, fast, free of
dependencies, and it is the right tool whenever the caller knows the token they
are looking for — a class name, a spec id, a page anchor.

It fails on the question an agent actually asks. An agent wondering *"how do we
handle noisy fitness during calibration"* will not guess that the repository
words it as "elite fitness is re-evaluated each generation, never cached"
(SPEC-004 criterion 8). Nor will it guess that the metaheuristics reference
discusses this under sampling and re-evaluation of noisy objectives on a
particular page. The failure mode is not a slow search; it is an agent
concluding the repository is silent on a topic it has already settled, and then
re-deciding it differently — which is exactly the drift ADR-0001 exists to stop.

Grep also has a specific budget failure here: a near-miss grep produces either
zero hits (agent then reads whole files) or hundreds of hits (agent then reads
whole files). Both paths end at `no-bulk-reference-reads` being violated by a
well-intentioned agent.

Where grep remains strictly better, and must not be displaced:

| Query kind | Better tool | Why |
|---|---|---|
| `derive_episodes` — exact symbol | lexical | Embeddings blur identifiers; a symbol's neighbours in vector space are other symbols. |
| `<!-- page 143 -->` — anchor | lexical | Literal, unique, already the documented workflow. |
| `ADR-0005` — identifier | lexical | Ditto. |
| "who decides the denominator" | vector | Wording in ADR-0006 differs from the query entirely. |
| "why did we reject PHP" | vector | Answer is in ADR-0002 under different words. |
| "noisy fitness in calibration" | vector | See above. |

Therefore the retriever is **hybrid**: both retrievers always run, results are
fused by Reciprocal Rank Fusion, and each `Hit` records which retriever found it
so the agent can see whether it got a lexical match or a semantic one. Vector-only
retrieval is not offered as a mode. RRF is chosen over score normalisation
because it needs no calibration between two incomparable score scales, which is
one fewer parameter to tune and defend.

## The gate — is the index worth building at all?

Following the ADR-0003 pattern: measure, then decide. Full statement of the gate
is ADR-0007; restated here as the normative order of work.

| Step | Action | Exit condition |
|---|---|---|
| 1 | `ALGORITHM-INDEX.md` + page-anchor grep + ripgrep over `docs/` and `src/` | Always available. Costs nothing, ships today. |
| 2 | Build the labelled query set (≥ 30 realistic agent questions, each with hand-marked relevant passages) and measure the **lexical baseline** on it | **Stop here if lexical recall@5 > 0.70.** Grep is adequate; no index. |
| 3 | Prototype hybrid index, measure on the same query set | Ship only if hybrid recall@5 exceeds the lexical baseline by **≥ 0.15 absolute**, index build ≤ 60 s, warm query ≤ 300 ms. |

All three of the following must hold for step 3 to proceed:

- **G1 — corpus size.** Indexable corpus ≥ 1.0 MB of text. *Measured 2026-08-14:
  `docs/` 102 KB + `src/` 142 KB + `refs/metaheuristics/` 871 KB ≈ 1.12 MB
  ≈ 280k tokens. G1 is already met, and the metaheuristics reference alone is
  77% of it.*
- **G2 — lexical inadequacy.** Lexical-only recall@5 ≤ 0.70 on the labelled set.
  **Not yet measured. This is the gate that decides the work.**
- **G3 — measured improvement.** Hybrid recall@5 ≥ lexical + 0.15 absolute, with
  MRR also improved, at the latency and build-time budgets above.

If G2 fails, this half of the spec is closed as *not needed*, the labelled query
set is kept as a regression asset, and that is a **good** outcome — it deletes
planned work, in the same sense as SPEC-004's note about CP-SAT possibly deleting
NSGA-II. Step 2 is roughly an hour of work and is the entire cost of finding out.

## Chunking

Chunk boundaries are a modelling choice, not an implementation detail, because
they determine what a citation can point at.

1. **Markdown** splits on heading boundaries first, then on ~400-token windows
   with 60-token overlap within an over-long section. Every chunk carries the
   heading path (`SPEC-004 > Acceptance criteria`) as a prefix in the embedded
   text, so a chunk retrieved out of context still says what document it is from.
2. **`essentials-of-metaheuristics.md` chunks never cross a `<!-- page N -->`
   anchor.** Page numbers are the citation currency of `SELECTION-GUIDE.md` and
   SPEC-004 ("Alg 104, p.143"); a chunk spanning pages 142–143 cannot be cited
   correctly, and a wrong page citation in this repository is worse than no
   retrieval at all. 263 anchors exist; they are the natural boundary.
3. **Python** chunks at module/class/function granularity, embedding the
   docstring plus the signature, not the body. Bodies are noise for semantic
   retrieval and are exactly what lexical search is good at.
4. **`docs/AUDIT-LOG.md`** chunks per entry.
5. Chunks record `sha256` of source text; a changed hash invalidates the chunk on
   re-index. Indexing is incremental on file mtime + hash, so a routine re-index
   after editing one spec costs one file, not the corpus.

## Interface (Part A)

```python
# hwpm.retrieve  — adapter layer, optional dependency, imports only from domain
@dataclass(frozen=True)
class Chunk:
    id: str                 # stable: f"{path}:{start_line}-{end_line}"
    path: Path              # repo-relative
    start_line: int
    end_line: int
    page_anchor: int | None # set only for the parsed reference
    heading_path: str
    text: str
    sha256: str

@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float            # fused (RRF)
    lexical_rank: int | None
    vector_rank: int | None
    def citation(self) -> str: ...     # "docs/specs/SPEC-004-optimisation.md:101-112"

@dataclass(frozen=True)
class IndexStats:
    n_files: int; n_chunks: int; bytes_indexed: int
    build_seconds: float; model: str; model_sha: str; built_at: datetime

def chunk_file(path: Path, max_tokens: int = 400, overlap: int = 60) -> list[Chunk]: ...
def build_index(roots: list[Path], out: Path, model: str) -> IndexStats: ...
def search(q: str, k: int = 8, lexical_only: bool = False) -> list[Hit]: ...

@dataclass(frozen=True)
class RetrievalScore:
    recall_at_5: float; mrr: float; n_queries: int
def evaluate(queries: Path) -> dict[str, RetrievalScore]:   # {"lexical":…, "hybrid":…}
    ...
```

CLI:

```bash
hwpm context index                       # build/refresh; prints IndexStats
hwpm context search "<query>" -k 8       # ranked passages + file:line citations
hwpm context search "<query>" --lexical-only
hwpm context eval                        # the G2/G3 measurement, JSON out
```

`search` prints the citation and the chunk text, never a paraphrase, and never
more than `k` chunks. Default `k=8` at ~400 tokens is ~3.2k tokens — an order of
magnitude below a single whole-file read of the reference.

## Tooling decision

**Chosen: `fastembed` for embeddings, `sqlite-vec` for the index.**

| Candidate | Weight | Verdict |
|---|---|---|
| `sentence-transformers` | Pulls PyTorch (~2.5 GB installed on Windows) | Rejected. The dependency dwarfs the entire project for a task that runs a few times a week. |
| **`fastembed`** | ONNX Runtime + tokenizers, ~120 MB; `bge-small-en-v1.5` int8 ≈ 130 MB cached | **Chosen.** CPU-only, no PyTorch, no CUDA, downloads weights once then works offline, runs on a single Windows analyst machine. |
| `chromadb` | Bundles its own embedding stack, server-ish process model, its own on-disk format | Rejected. Duplicates the embedding dependency and contradicts `02-ARCHITECTURE.md` "no database server". |
| `faiss-cpu` | Fast, mature, but the index is an opaque sidecar file needing a separate metadata store | Rejected. We would end up writing SQLite alongside it anyway. |
| **`sqlite-vec`** | Single loadable extension, ~1 MB | **Chosen.** The repository already committed to SQLite (`02-ARCHITECTURE.md`, "Parquet + SQLite"). Vectors and chunk metadata live in one file, one transaction, one backup. |

**Model:** `BAAI/bge-small-en-v1.5`, 384 dimensions, 512-token window. Small
enough that indexing 1.1 MB is well inside the 60 s budget on CPU; English-only
is sufficient for this corpus. The model id and its hash are recorded in
`IndexStats` and printed by `hwpm context index`, because a silently changed
embedding model produces silently different retrieval — the same class of problem
as an unversioned method change in ADR-0006.

**Weights are cached under `HWPM_MODEL_CACHE`** (default: the user cache dir),
never committed. The index file is a build artefact and is git-ignored.

**Dependency split — `hwpm govern` must stay installable without any of this.**
Mirroring the existing `pyproject.toml` split:

```toml
retrieve = [
    "fastembed>=0.3",
    "sqlite-vec>=0.1",
]
```

`hwpm.retrieve` is imported lazily inside the `context` subcommand only. Absent
dependencies produce exit code 2 and the install hint `pip install -e .[retrieve]`,
never a traceback, and never an import error anywhere in `hwpm govern`.

---

# Part B — Trace and activity embeddings

## What it is for

Three uses, in decreasing order of confidence that they are worth it:

1. **Cohorts of similar ward-days.** "Show me the days that look structurally
   like this one" is a question the current tooling cannot answer at all. It is
   the natural unit for comparing an intervention week against matched controls.
2. **Anomaly triage.** Ward-days far from every cluster centroid are candidates
   for clinical review. The output is *a list of days worth a human look*, never
   a judgement about those days.
3. **Warm starts for SPEC-004.** Seeding the NSGA-II initial population with
   schedules that worked on the nearest historical ward-days, rather than at
   random. Cheap to try, and it fails safely: a bad seed costs generations, not
   correctness, and the fixed-seed determinism criterion (SPEC-004 criterion 5)
   still has to hold with seeding enabled.

## The constraint that matters more than the features

**Embeddings may be used for retrieval, clustering, anomaly triage and warm
starting. They may NEVER be the basis of a reported governance figure without an
interpretable derivation published alongside it.**

This is not a stylistic preference. `00-VISION.md` commits this project to "a
defensible chain from figure to evidence", and ADR-0006 engineers MDT coverage as
a regulated measurement whose authority comes from the visible chain behind it —
which patients counted, under which definition, from which observed events, with
what uncertainty. A cosine distance in a 384-dimensional space has no such chain.
It cannot be explained to a consultant who disagrees with it, which per ADR-0006's
rationale is the challenge the project must survive.

Concretely prohibited:

- MDT coverage, motion waste, or any published figure computed from, weighted by,
  filtered by, or thresholded on an embedding distance.
- "Similar wards" or "similar days" used as a comparator group in a governance
  report without the interpretable feature differences between the groups being
  published alongside.
- An anomaly score presented as a quality indicator. Anomaly means *unlike other
  days*, which is not a synonym for *worse*, and will be read as one the first
  time it reaches a slide.

Enforced structurally, not by review: an import-linter contract forbids
`hwpm.analytics.governance` from importing `hwpm.mining.embed` (Acceptance
criterion 14), in the same spirit as the existing domain-isolation contract in
`pyproject.toml`. An architecture that is only documented decays one import at a
time.

## The interpretable baseline, and Part B's gate

Before any trace embedding is built, the same measured-gate discipline applies:

| Step | Action | Exit condition |
|---|---|---|
| 1 | **Interpretable feature vector** per ward-day: episode count, median/IQR dwell, distinct specialties, motion metres, floor transitions, revisit count, protected-window collisions, MDT moments. ~12 named, unit-carrying numbers. Cluster with k-means. | **Stop if this is adequate.** It is explainable to governance as-is. |
| 2 | Learned trace embedding (activity-sequence encoding) | Build only if it beats step 1 by ≥ 0.10 adjusted Rand index against the synthetic generator's known day-types, **and** ≥ 200 ward-days of derived episodes exist. |

The 200-ward-day floor exists because clustering 30 days produces clusters
whatever the method, and their apparent structure is noise. The ARI comparison is
possible because `hwpm.ingest.synthetic.GroundTruth` knows which day-type it
generated — this is the one place in the project where cluster quality has a true
oracle, and it should be used before real data is ever considered.

If step 1 wins, Part B ships as *named clinical features*, which is the better
outcome for every downstream audience.

## Data handling (ADR-0005, hard requirement)

1. **No hosted embedding API, ever, for anything derived from patient data.**
   Encoding runs locally through the same ONNX runtime as Part A. This is not a
   preference expressed as a default; sending a ward-day sequence to a remote
   endpoint is an export of patient data outside the organisation's control, and
   deleting it afterwards does not undo that.
2. **Trace vectors are derived personal data.** Embeddings are not anonymisation:
   inversion attacks recover meaningful content from embedding vectors, and a
   ward-day vector plus a date narrows to a small population regardless.
   Therefore trace vectors live under `HWPM_DATA_DIR`, are git-ignored, and are
   never read into an agent context — exactly as the raw log is not.
3. **Agents work against synthetic-fixture vectors only** (`tests/fixtures/`).
4. **The aggregation floor applies to cluster output.** A cluster containing
   fewer than 5 patients, or resolvable to a single named clinician, is
   suppressed. Tested, not left to vigilance.
5. **The Part A repo index and the Part B trace index are separate files in
   separate directories** — repo build artefact vs `HWPM_DATA_DIR`. One process
   never opens both. This is what makes it impossible for a corpus query to
   return patient-derived content.

## Interface (Part B)

```python
# hwpm.mining.embed
@dataclass(frozen=True)
class WardDayVector:
    day: date; ward: LocationId
    features: dict[str, float]     # interpretable, named, unit-carrying — always present
    embedding: tuple[float, ...] | None   # populated only if the Part B gate fired

def encode_ward_day(episodes: list[BedsideEpisode], rounds: list[Round]) -> WardDayVector: ...
def cluster_ward_days(
    vectors: list[WardDayVector], k: int, rng: Random, use_embedding: bool = False
) -> ClusterAssignment: ...
def atypicality(v: WardDayVector, model: ClusterAssignment) -> float: ...
def neighbours(v: WardDayVector, k: int = 5) -> list[tuple[date, float]]: ...

# hwpm.optimize
def seed_population(inst: Instance, neighbours: list[Schedule], rng: Random) -> list[Schedule]: ...
```

`features` is never `None`. `embedding` may be. Any consumer that cannot work
from `features` alone is a consumer that must not feed a governance figure.
`cluster_ward_days` and `seed_population` take an explicit `rng`; module-level
`random` is prohibited project-wide (CLAUDE.md).

---

## Modelling assumptions

Every item here could reasonably have been decided differently.

1. **English-only embedding model.** The corpus is English. If clinical free-text
   in other languages ever enters scope, the model choice is void.
2. **400-token chunks with 60-token overlap.** Chosen so that one heading-scoped
   answer usually fits in one chunk. Too small fragments an argument across
   chunks; too large reintroduces the bulk-read problem it exists to solve.
   Controlled by `chunk_file(max_tokens, overlap)` and recorded in `IndexStats`.
3. **RRF fusion with equal weight to lexical and vector.** No tuned α. Tuning it
   on 30 labelled queries would overfit to those queries.
4. **Cosine similarity** over normalised vectors. Standard for this model family;
   stated because it is a choice.
5. **Ward-day is the unit of Part B analysis**, not patient-stay. Ward-day matches
   how rounds are organised and how governance reports. Patient-stay would be the
   right unit for a continuity-of-care question, and is a different spec.
6. **Trace encoding treats activity sequences as ordered symbols with durations.**
   Discarding duration would make a 4-minute and a 40-minute bedside episode
   identical, which is the distinction SPEC-002's `min_dwell_s` exists to make.
7. **k-means with a stated k** rather than a k-selection heuristic. The number of
   ward-day types is a clinical question; silhouette-optimal k is an arithmetic
   answer to it and would be mistaken for a finding.
8. **Vectors are float32.** Sufficient at this scale; halves storage against
   float64 and changes no reported number, because no reported number depends on
   a vector (ADR-0007 decision 4).

## Acceptance criteria

Part A:

1. `chunk_file` never emits a chunk of `essentials-of-metaheuristics.md` that
   spans a `<!-- page N -->` anchor, across the whole file. — `test_chunks_respect_page_anchors`
2. Every `Hit.citation()` resolves to a line range whose text on disk matches the
   chunk's `sha256`. — `test_citations_resolve`
3. `hwpm context search` returns at most `k` chunks and emits no text that is not
   present verbatim in a source file. — `test_search_returns_verbatim_passages`
4. `hwpm context eval` reports lexical and hybrid `recall@5` and MRR over the
   committed labelled query set. — `test_eval_reports_both_baselines`
5. Hybrid recall@5 exceeds lexical recall@5 by ≥ 0.15 absolute on the labelled
   set; below that the index is not shipped (gate G3, ADR-0007). — `test_hybrid_beats_lexical_gate` **(gate)**
6. Every one of ≥ 30 labelled queries has ≥ 1 marked-relevant passage, and no
   query's answer lies in a file excluded from the index. — `test_query_set_wellformed`
7. Index build over the full corpus completes in ≤ 60 s and a warm query in
   ≤ 300 ms on the reference machine. — `tests/bench/test_retrieval_latency.py`
8. `--lexical-only` produces results with `vector_rank is None` for every hit and
   requires no optional dependency. — `test_lexical_only_needs_no_optional_deps`
9. `import hwpm.govern` and every `hwpm govern` subcommand succeed in an
   environment with neither `fastembed` nor `sqlite-vec` installed. — `test_govern_imports_without_retrieve`
10. `hwpm context search` with the optional dependencies absent exits 2 with the
    install hint and no traceback. — `test_context_missing_dep_message`
11. Re-indexing after editing one file re-embeds only that file's chunks. — `test_incremental_reindex`
12. The index file path is git-ignored and no `.db`/vector artefact is tracked. — `test_index_artefacts_untracked`

Part B:

13. `encode_ward_day` always populates `features`; with the Part B gate unfired,
    `embedding is None` and every downstream function still works. — `test_features_always_present`
14. `hwpm.analytics.governance` does not import `hwpm.mining.embed`, transitively
    or directly. — import-linter contract `Governance figures do not depend on embeddings` **(gate)**
15. No published governance artefact schema admits a field derived from an
    embedding; a fixture attempting one fails schema validation. — `test_governance_schema_rejects_embedding_fields`
16. Learned embeddings beat the interpretable feature baseline by ≥ 0.10 adjusted
    Rand index against `GroundTruth` day-types, or the embedding path is not
    built. — `test_embedding_beats_feature_baseline` **(gate)**
17. Clustering refuses to run on fewer than 200 ward-days and says why. — `test_clustering_minimum_days`
18. A cluster resolving to fewer than 5 patients, or to one named clinician, is
    suppressed from all output. — `test_cluster_suppression_floor`
19. Fixed `rng` seed ⇒ identical cluster assignment and identical seeded
    population; SPEC-004 criterion 5 still holds with warm starts enabled. — `test_warm_start_determinism`
20. No code path sends chunk text, trace vectors or event data to a network host;
    the retrieval tests pass with networking disabled after the model cache is
    populated. — `test_no_network_egress` **(gate — ADR-0005)**

## Test oracle

- **Part A:** a hand-labelled query set, committed as `tests/fixtures/retrieval_queries.yaml`
  — ≥ 30 questions an agent would plausibly ask, each with the passage(s) that
  answer it marked by file and line range. It is a small, human-made oracle and
  its subjectivity is a known limitation, mitigated by being written *before* any
  index exists and by being versioned so a later session cannot quietly relabel
  it to make a number move. Criteria 1–3 and 7–12 have exact mechanical oracles
  and do not depend on it.
- **Part B:** `hwpm.ingest.synthetic.GroundTruth` knows which day-type it
  generated, giving a true clustering oracle via adjusted Rand index (criterion
  16). For anomaly triage there is **no oracle** — "atypical" is defined by the
  method, not against truth. That is a finding, not an omission, and it is the
  direct reason anomaly output is confined to *candidates for human review*.

## Failure modes

- **Retrieval confidently returns the wrong passage and the agent trusts it.**
  Worse than returning nothing, because it looks like an answer. Detected by
  criterion 2 (citations resolve) and mitigated by always printing the citation
  so the agent can cheaply verify, and by never paraphrasing.
- **A stale index silently answers from deleted or superseded text.** An ADR
  amended by addition (the house rule) leaves the superseded passage embedded and
  still retrievable. Mitigated by hash-based invalidation (criterion 11) and by
  including the `Status:` header line in every chunk's heading path, so a
  superseded document announces itself in the retrieved text.
- **The index becomes a second source of truth.** An agent cites the index rather
  than the file. Mitigated by returning verbatim text with `file:line` only — the
  index has no content of its own to cite.
- **Dependency creep.** `fastembed` pulls ONNX Runtime; a future contributor
  "simplifies" by switching to `sentence-transformers` and lands PyTorch on the
  analyst's machine. Detected by criterion 9 plus a dependency-weight note in
  `pyproject.toml`.
- **Model drift.** A version bump changes the embedding, changing retrieval,
  changing which passage an agent reads, changing a decision — with nothing in
  the audit log. Mitigated by recording model id and hash in `IndexStats` and
  printing them on every index build.
- **Embeddings leak into a governance number anyway**, via an innocuous-looking
  "similar days" comparator. This is the failure this spec is most concerned
  with. Detected structurally by criteria 14 and 15, because prose prohibition
  alone would not survive a delivery deadline.
- **Anomaly scores are read as quality scores.** Near-certain if the output is
  ever labelled "outlier ward-days" on a dashboard. Mitigated by naming
  (`atypicality`, not `score`) and by ADR-0006's existing prohibition on bare
  figures — and it remains a real residual risk that lives with the user.
- **Part B clusters the artefacts of episode derivation rather than clinical
  reality.** If `EpisodeParams` (SPEC-002) change, every ward-day vector changes.
  Mitigated by stamping the `EpisodeParams` used into `WardDayVector` provenance,
  and by treating a parameter change as a method-version change (ADR-0006 §1).
- **Trace vectors are treated as anonymous** and consequently emailed, committed,
  or read into an agent context. Addressed by placing them under `HWPM_DATA_DIR`
  from the outset, so the safe path is also the default path.

## Open questions

- **[blocking] Should nodes for this work be added to `orchestration/graph.yaml`
  at all, and at what gate?** This spec deliberately does not touch the graph.
  Recommendation: add `N19-context-retrieval` as `autonomous` (it touches no
  patient data and its gate is self-limiting) and `N20-trace-embeddings` as
  `confirm`, depending on `N05-mining`, because accepting an opaque representation
  anywhere near a governance metric is the user's call in the same way the C
  kernel is (N13). **User decides.**
- **[blocking] Is a one-time download of embedding model weights acceptable on
  the target machine?** `fastembed` fetches ~130 MB from Hugging Face on first
  use. Nothing leaves the machine, but something enters it, and on a managed NHS
  device that may need approval or an offline weights transfer. If not
  acceptable, Part A ships lexical-only and Part B is void. **User decides.**
- **[non-blocking] Does the labelled query set get written by the architect role
  or by the user?** Architect-written queries risk encoding what the architect
  already knows the repository says. User-written queries are a better oracle.
  Recommendation: architect drafts, user amends before it is committed.
- **[non-blocking] Should the audit log be indexed?** It is the record of what
  happened, so retrieval over it is valuable; it also contains model names,
  costs, and node ids that lexical search already handles well. Recommendation:
  index it, chunk per entry, and revisit if it pollutes results.
- **[non-blocking] Warm starts and reproducibility.** Seeding NSGA-II from
  historical neighbours makes a result depend on the historical corpus as well as
  the instance. Determinism (criterion 19) is preserved, but the artefact header
  must record the neighbour set. Decide when N10 is implemented, not now.
- **[non-blocking] Cluster count `k` for ward-day types.** A clinical question.
  Ask at N16 face validity rather than choosing it analytically.
