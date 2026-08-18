"""The G2/G3 measurement harness (ADR-0007) — retrieval quality, not a demo.

`evaluate()` is the normative order of work in SPEC-007: build the labelled
query set once, always measure lexical, and additionally measure vector-only
and hybrid if the optional `fastembed`/`sqlite-vec` dependencies are present
(N19b, ADR-0007 G2 reopened). If they are not, the returned dict carries only
`"lexical"` — there is no vector or hybrid score to report, and inventing one
would misrepresent an unbuilt system.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from hwpm.retrieve.chunk import Chunk
from hwpm.retrieve.lexical import LexicalIndex


@dataclass(frozen=True)
class RelevantSpan:
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class LabelledQuery:
    id: str
    query: str
    relevant: tuple[RelevantSpan, ...]
    note: str = ""


@dataclass(frozen=True)
class RetrievalScore:
    recall_at_5: float
    mrr: float
    n_queries: int


class _Retriever(Protocol):
    def search(self, query: str, k: int = 8) -> list[tuple[Chunk, float]]: ...


def load_queries(path: Path) -> list[LabelledQuery]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    queries_raw = raw["queries"] if isinstance(raw, dict) else raw
    queries: list[LabelledQuery] = []
    for item in queries_raw:
        spans = tuple(
            RelevantSpan(
                path=str(r["path"]).replace("\\", "/"),
                start_line=int(r["start_line"]),
                end_line=int(r["end_line"]),
            )
            for r in item["relevant"]
        )
        queries.append(
            LabelledQuery(
                id=str(item["id"]),
                query=str(item["query"]),
                relevant=spans,
                note=str(item.get("note", "")),
            )
        )
    return queries


def _overlaps(chunk: Chunk, span: RelevantSpan) -> bool:
    if chunk.path != span.path:
        return False
    return chunk.start_line <= span.end_line and chunk.end_line >= span.start_line


def _is_relevant(chunk: Chunk, query: LabelledQuery) -> bool:
    return any(_overlaps(chunk, span) for span in query.relevant)


def score_retriever(
    retriever: _Retriever, queries: list[LabelledQuery], k: int = 5, search_k: int = 20
) -> RetrievalScore:
    """recall@k: fraction of queries with >=1 relevant chunk in the top k.
    MRR: mean reciprocal rank of the first relevant chunk, searched to
    `search_k` so MRR is not artificially capped at 1/k."""
    if not queries:
        return RetrievalScore(recall_at_5=0.0, mrr=0.0, n_queries=0)
    hits = 0
    reciprocal_ranks: list[float] = []
    for q in queries:
        results = retriever.search(q.query, k=search_k)
        rank_found: int | None = None
        for rank, (chunk, _score) in enumerate(results, start=1):
            if _is_relevant(chunk, q):
                rank_found = rank
                break
        if rank_found is not None and rank_found <= k:
            hits += 1
        reciprocal_ranks.append(1.0 / rank_found if rank_found else 0.0)
    n = len(queries)
    return RetrievalScore(
        recall_at_5=hits / n, mrr=sum(reciprocal_ranks) / n, n_queries=n
    )


def evaluate(
    queries_path: Path,
    repo_root: Path,
    *,
    chunks: list[Chunk] | None = None,
    include_vector: bool = True,
    reuse_vector_index: bool = False,
) -> dict[str, RetrievalScore]:
    """The measurement SPEC-007 / ADR-0007 gates on. Always includes
    `"lexical"`. Includes `"vector"` and `"hybrid"` only if a vector index
    could actually be built -- i.e. only if `fastembed`/`sqlite-vec` are
    installed (the `retrieve` extra). If they are not, the dict carries only
    `"lexical"`, honestly reflecting what was measured rather than inventing
    a score for a system that was not built.

    `include_vector=False` skips the vector/hybrid measurement entirely and
    returns immediately after lexical scoring -- for callers that only want
    the cheap lexical baseline. This matters in practice: embedding the full
    corpus (`refs/metaheuristics/essentials-of-metaheuristics.md` alone is
    ~1300 chunks) takes on the order of minutes on CPU, not the sub-second
    cost of BM25, so a caller that does not need the vector/hybrid score
    should not pay for it. `reuse_vector_index=True` skips rebuilding the
    vector index if one already exists at the default path under
    `repo_root`, for repeat calls within the same measurement session."""
    from hwpm.retrieve.corpus import build_chunks
    from hwpm.retrieve.index import MissingOptionalDependencyError
    from hwpm.retrieve.vector import DEFAULT_VECTOR_DB_PATH, VectorRetriever

    queries = load_queries(queries_path)
    corpus_chunks = chunks if chunks is not None else build_chunks(repo_root)
    lexical_index = LexicalIndex.build(corpus_chunks)
    result: dict[str, RetrievalScore] = {
        "lexical": score_retriever(lexical_index, queries)
    }
    if not include_vector:
        return result

    vector_path = repo_root / DEFAULT_VECTOR_DB_PATH
    try:
        from hwpm.retrieve.vector import build_vector_index

        if not reuse_vector_index or not vector_path.exists():
            build_vector_index(corpus_chunks, vector_path)
    except MissingOptionalDependencyError:
        return result

    result["vector"] = score_retriever(VectorRetriever(vector_path), queries)

    try:
        import importlib

        hybrid_mod = importlib.import_module(
            "hwpm.retrieve.hybrid"
        )  # optional, may not exist
    except ImportError:
        return result

    try:
        hybrid_retriever = hybrid_mod.build(
            corpus_chunks,
            repo_root,
            vector_index_path=vector_path,
            rebuild_vectors=False,  # already built just above -- reuse it
        )
    except MissingOptionalDependencyError:
        return result
    result["hybrid"] = score_retriever(hybrid_retriever, queries)
    return result
