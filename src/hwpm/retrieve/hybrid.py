"""Reciprocal Rank Fusion of lexical and vector retrieval (SPEC-007 Part A).

RRF, not a tuned weighted sum: each retriever contributes `1 / (RRF_K + rank)`
for every chunk it places in its own top `depth`, and a chunk's fused score is
the sum across whichever retriever(s) surfaced it. This needs no calibration
between BM25 and cosine-similarity score scales, and there is no alpha to fit
to the 36-query labelled set (SPEC-007 modelling assumption 3: "No tuned
alpha. Tuning it on 30 labelled queries would overfit to those queries.").

`RRF_K = 60` is the standard smoothing constant from the original RRF paper
(Cormack, Clarke & Buettcher, 2009) — a structural constant, not tuned on this
corpus, in the same spirit as BM25's k1/b in `hwpm.retrieve.lexical`.

Optional dependency: this module's *imports* need nothing beyond the lexical
half (`hwpm.retrieve.vector` is itself lazy about `fastembed`/`sqlite-vec`),
so `import hwpm.retrieve.hybrid` always succeeds. Calling `build()` or
`HybridRetriever.search()` without the optional deps installed and without a
vector index on disk degrades to lexical-only ranking rather than raising —
`fastembed`/`sqlite-vec` are only required to *build* a vector index, not to
query an absent one.

**Measured outcome (N19b, 2026-08-18, see docs/AUDIT-LOG.md): ADR-0007 G3 did
not clear.** Lexical-only recall@5 = 0.750; vector-only = 0.611; hybrid =
0.639 (n=36) — hybrid scores *below* lexical alone, not the required
lexical + 0.15. This module is built and shipped per the node's own
instruction (build it, measure honestly, do not paper over a bad result), but
the honest recommendation today is `hwpm context search --lexical-only`, not
this module's default fusion. Plausible causes recorded in the audit entry:
embedding `heading_path + text` may dilute short technical queries the vector
half is supposed to help with; `RRF_K`/fusion depth are structural constants,
not tuned for this corpus; and outlier chunks near the 512-token window may
distort the embedding space. Investigating and improving this is future work.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hwpm.retrieve.chunk import Chunk
from hwpm.retrieve.lexical import LexicalIndex
from hwpm.retrieve.vector import (
    DEFAULT_VECTOR_DB_PATH,
    MODEL_NAME,
    VectorRetriever,
    build_vector_index,
)

RRF_K = 60
DEFAULT_FUSION_DEPTH = 50


@dataclass(frozen=True)
class RankedHit:
    chunk: Chunk
    score: float
    lexical_rank: int | None
    vector_rank: int | None


class HybridRetriever:
    """`.search(query, k)` matches the `_Retriever` protocol used by
    `hwpm.retrieve.eval.score_retriever`. `.search_ranked` additionally
    reports which retriever(s) found each chunk, matching
    `Hit.lexical_rank`/`Hit.vector_rank` in `hwpm.retrieve.index`."""

    def __init__(
        self,
        lexical_index: LexicalIndex,
        vector_index_path: Path,
        model_name: str = MODEL_NAME,
        depth: int = DEFAULT_FUSION_DEPTH,
    ) -> None:
        self._lexical = lexical_index
        self._vector = VectorRetriever(vector_index_path, model_name)
        self._depth = depth

    def search_ranked(self, query: str, k: int = 8) -> list[RankedHit]:
        lexical_results = self._lexical.search(query, k=self._depth)
        vector_results = self._vector.search(query, k=self._depth)
        return rrf_fuse(lexical_results, vector_results, k=k)

    def search(self, query: str, k: int = 8) -> list[tuple[Chunk, float]]:
        return [(h.chunk, h.score) for h in self.search_ranked(query, k=k)]


def rrf_fuse(
    lexical_results: list[tuple[Chunk, float]],
    vector_results: list[tuple[Chunk, float]],
    k: int = 8,
    rrf_k: int = RRF_K,
) -> list[RankedHit]:
    """Pure Reciprocal Rank Fusion, independent of how the two ranked lists
    were produced -- unit-testable without any embedding calls. `rank` is
    1-based position within each input list (already truncated to whatever
    depth the caller searched to); a chunk absent from a list contributes 0
    to the fused score for that retriever, per the standard RRF formula."""
    lexical_rank = {c.id: r for r, (c, _s) in enumerate(lexical_results, start=1)}
    vector_rank = {c.id: r for r, (c, _s) in enumerate(vector_results, start=1)}
    chunks_by_id: dict[str, Chunk] = {}
    for c, _s in (*lexical_results, *vector_results):
        chunks_by_id.setdefault(c.id, c)

    fused: dict[str, float] = {}
    for cid, rank in lexical_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (rrf_k + rank)
    for cid, rank in vector_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (rrf_k + rank)

    ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return [
        RankedHit(
            chunk=chunks_by_id[cid],
            score=score,
            lexical_rank=lexical_rank.get(cid),
            vector_rank=vector_rank.get(cid),
        )
        for cid, score in ordered
    ]


def build(
    chunks: list[Chunk],
    repo_root: Path,
    *,
    vector_index_path: Path | None = None,
    model_name: str = MODEL_NAME,
    rebuild_vectors: bool = True,
) -> HybridRetriever:
    """Build (or reuse) the vector index and return a ready `HybridRetriever`.

    This is the dynamic-import entry point `hwpm.retrieve.eval.evaluate` looks
    for via `importlib.import_module("hwpm.retrieve.hybrid")` -- the mechanism
    that lets `evaluate()` report a hybrid score only when this module is
    importable, and a real one only when the vector index actually builds.
    `rebuild_vectors=False` reuses an existing index at `vector_index_path`
    (or the default) instead of re-embedding the whole corpus, since
    `evaluate()` typically builds the vector index once and scores both the
    vector-only and hybrid retrievers against it.

    Raises `hwpm.retrieve.index.MissingOptionalDependencyError` if
    `rebuild_vectors` is True (or no index exists yet) and `fastembed`/
    `sqlite-vec` are not installed. Callers wanting a silent lexical-only
    fallback should catch that -- `evaluate()` does.
    """
    lexical_index = LexicalIndex.build(chunks)
    out = vector_index_path or (repo_root / DEFAULT_VECTOR_DB_PATH)
    if rebuild_vectors or not out.exists():
        build_vector_index(chunks, out, model_name)
    return HybridRetriever(lexical_index, out, model_name)
