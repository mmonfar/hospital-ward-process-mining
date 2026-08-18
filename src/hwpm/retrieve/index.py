"""The `hwpm context` index — lexical build artefact, and the `search()`
entry point that layers hybrid ranking on top when a vector index exists.

ADR-0007's G2 measurement (`docs/AUDIT-LOG.md`, node N19) originally came back
with lexical-only recall@5 = 0.75, above the 0.70 stop threshold, so the
vector half was not built at first. The corpus grew and a later remeasurement
(N19b) reopened G2; `hwpm.retrieve.vector` and `hwpm.retrieve.hybrid` are the
vector half and RRF fusion built in response. This module itself still never
imports `fastembed` or `sqlite-vec` at module scope — those live in
`hwpm.retrieve.vector`/`hwpm.retrieve.hybrid` and are imported lazily, inside
`search()`, only when `lexical_only` is False. `Hit.vector_rank` is `None`
whenever the vector half was not consulted (deps absent, no vector index
built yet, or `lexical_only=True`); it is set when a hybrid hit came from the
vector retriever.

The lexical index is a git-ignored JSON build artefact (`.hwpm/context-index.json`
by default): the chunked corpus plus its `IndexStats`. Rebuilding it is a
sub-second, pure-Python operation at this corpus size — there is no embedding
cost to amortise, so unlike the vector index there is no incremental-reindex
machinery here. The vector index (`.hwpm/context-vectors.db`, also
git-ignored) is built separately by `hwpm.retrieve.vector.build_vector_index`
and consulted here only if it is present on disk.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from hwpm.retrieve.chunk import Chunk
from hwpm.retrieve.corpus import build_chunks
from hwpm.retrieve.lexical import LexicalIndex

DEFAULT_INDEX_PATH = Path(".hwpm") / "context-index.json"
LEXICAL_MODEL_NAME = "lexical-bm25"


@dataclass(frozen=True)
class IndexStats:
    n_files: int
    n_chunks: int
    bytes_indexed: int
    build_seconds: float
    model: str
    model_sha: str
    built_at: datetime


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    lexical_rank: int | None
    vector_rank: int | None

    def citation(self) -> str:
        return self.chunk.citation()


class MissingOptionalDependencyError(RuntimeError):
    """Raised by `hwpm.retrieve.vector`/`hwpm.retrieve.hybrid` when a
    function that needs `fastembed`/`sqlite-vec` is called without them
    installed (the `retrieve` extra; N19b, ADR-0007 G2 reopened). Defined
    here rather than in `vector.py` so `hwpm.retrieve.index` — and every
    caller that only wants the lexical path — can reference it without
    importing anything optional-dependent. `hwpm context index`/`search`
    catch it and degrade or exit 2 with an install hint, never a traceback
    (SPEC-007 criteria 9-10)."""


def _chunk_to_dict(c: Chunk) -> dict:
    return {
        "id": c.id,
        "path": c.path,
        "start_line": c.start_line,
        "end_line": c.end_line,
        "page_anchor": c.page_anchor,
        "heading_path": c.heading_path,
        "text": c.text,
        "sha256": c.sha256,
    }


def _dict_to_chunk(d: dict) -> Chunk:
    return Chunk(
        id=d["id"],
        path=d["path"],
        start_line=d["start_line"],
        end_line=d["end_line"],
        page_anchor=d["page_anchor"],
        heading_path=d["heading_path"],
        text=d["text"],
        sha256=d["sha256"],
    )


def build_index(
    roots: list[Path], out: Path, model: str = LEXICAL_MODEL_NAME
) -> IndexStats:
    """Build/refresh the lexical index. `roots` is accepted for interface
    parity with SPEC-007 but the corpus definition itself lives in
    `hwpm.retrieve.corpus` — every root under `docs/`, `src/hwpm/`, and the
    three reference files is always included, since a partial corpus would
    silently change what `hwpm context search` can find."""
    started = time.monotonic()
    repo_root = roots[0].resolve() if roots else Path.cwd().resolve()
    chunks = build_chunks(repo_root)
    build_seconds = time.monotonic() - started

    stats = IndexStats(
        n_files=len({c.path for c in chunks}),
        n_chunks=len(chunks),
        bytes_indexed=sum(len(c.text.encode("utf-8")) for c in chunks),
        build_seconds=build_seconds,
        model=model,
        model_sha="",
        built_at=datetime.now(UTC),
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stats": {
            "n_files": stats.n_files,
            "n_chunks": stats.n_chunks,
            "bytes_indexed": stats.bytes_indexed,
            "build_seconds": stats.build_seconds,
            "model": stats.model,
            "model_sha": stats.model_sha,
            "built_at": stats.built_at.isoformat(),
        },
        "chunks": [_chunk_to_dict(c) for c in chunks],
    }
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return stats


def load_chunks(index_path: Path) -> list[Chunk]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    return [_dict_to_chunk(d) for d in payload["chunks"]]


def load_stats(index_path: Path) -> IndexStats:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    s = payload["stats"]
    return IndexStats(
        n_files=s["n_files"],
        n_chunks=s["n_chunks"],
        bytes_indexed=s["bytes_indexed"],
        build_seconds=s["build_seconds"],
        model=s["model"],
        model_sha=s["model_sha"],
        built_at=datetime.fromisoformat(s["built_at"]),
    )


def search(
    q: str,
    k: int = 8,
    lexical_only: bool = False,
    *,
    index_path: Path = DEFAULT_INDEX_PATH,
    repo_root: Path | None = None,
) -> list[Hit]:
    """Ranked passages for `q`.

    `lexical_only=True` runs BM25 alone and needs no optional dependency
    (SPEC-007 criterion 8): every `Hit.vector_rank` is `None`.

    Otherwise this tries RRF-fused hybrid search (`hwpm.retrieve.hybrid`)
    against whatever vector index exists at `<repo_root>/.hwpm/context-vectors.db`.
    If `fastembed`/`sqlite-vec` are not installed, or no vector index has been
    built yet, it degrades to the same lexical-only result rather than raising
    — vector search is additive, never a hard requirement to get an answer.

    N19b measured (2026-08-18, docs/AUDIT-LOG.md) that hybrid recall@5 (0.639)
    is currently *below* lexical-only (0.750) on this corpus — ADR-0007 G3
    (hybrid >= lexical + 0.15) did not clear. `lexical_only=True` is the
    honestly-recommended mode today; hybrid is shipped per this node's
    instruction to build and measure it, not because it was shown to help.
    """
    if index_path.exists():
        chunks = load_chunks(index_path)
    else:
        chunks = build_chunks((repo_root or Path.cwd()).resolve())

    root = (repo_root or Path.cwd()).resolve()

    if not lexical_only:
        try:
            from hwpm.retrieve.hybrid import HybridRetriever
            from hwpm.retrieve.vector import DEFAULT_VECTOR_DB_PATH

            vector_path = root / DEFAULT_VECTOR_DB_PATH
            lexical_index = LexicalIndex.build(chunks)
            hybrid = HybridRetriever(lexical_index, vector_path)
            ranked = hybrid.search_ranked(q, k=k)
            if ranked:
                return [
                    Hit(
                        chunk=h.chunk,
                        score=h.score,
                        lexical_rank=h.lexical_rank,
                        vector_rank=h.vector_rank,
                    )
                    for h in ranked
                ]
        except MissingOptionalDependencyError:
            pass

    idx = LexicalIndex.build(chunks)
    results = idx.search(q, k=k)
    return [
        Hit(chunk=chunk, score=score, lexical_rank=rank, vector_rank=None)
        for rank, (chunk, score) in enumerate(results, start=1)
    ]
