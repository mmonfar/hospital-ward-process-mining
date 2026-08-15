"""The G2/G3 measurement harness (ADR-0007) — retrieval quality, not a demo.

`evaluate()` is the normative order of work in SPEC-007: build the labelled
query set once, measure lexical, and only measure hybrid if a vector index
exists. If it does not (the G2 gate stopped the work), the returned dict
carries only `"lexical"` — there is no hybrid score to report, and inventing
one would misrepresent an unbuilt system.
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
    queries_path: Path, repo_root: Path, *, chunks: list[Chunk] | None = None
) -> dict[str, RetrievalScore]:
    """The measurement SPEC-007 / ADR-0007 gate on. Always includes
    `"lexical"`. Includes `"hybrid"` only if a vector-capable retriever can
    be constructed (i.e. only once/if the Part A vector half is built)."""
    from hwpm.retrieve.corpus import build_chunks

    queries = load_queries(queries_path)
    corpus_chunks = chunks if chunks is not None else build_chunks(repo_root)
    lexical_index = LexicalIndex.build(corpus_chunks)
    result: dict[str, RetrievalScore] = {
        "lexical": score_retriever(lexical_index, queries)
    }

    try:
        import importlib

        hybrid_mod = importlib.import_module(
            "hwpm.retrieve.hybrid"
        )  # optional, may not exist
    except ImportError:
        return result

    hybrid_retriever = hybrid_mod.build(corpus_chunks, repo_root)  # pragma: no cover
    result["hybrid"] = score_retriever(hybrid_retriever, queries)  # pragma: no cover
    return result
