"""The `hwpm context` index — lexical only.

ADR-0007's G2 measurement (`docs/AUDIT-LOG.md`, node N19) came back with
lexical-only recall@5 = 0.75 on the labelled query set — above the 0.70 stop
threshold — so the vector half of SPEC-007 Part A was **not built** and this
module never imports `fastembed` or `sqlite-vec`. `Hit.vector_rank` is always
`None`; `search()` accepts `lexical_only` for interface compatibility with
SPEC-007 but there is currently no other mode to select.

The index is a git-ignored JSON build artefact (`.hwpm/context-index.json`
by default): the chunked corpus plus its `IndexStats`. Rebuilding it is a
sub-second, pure-Python operation at this corpus size (~940 chunks, ~1.2 MB)
— there is no embedding cost to amortise, so unlike the vector design in
SPEC-007 there is no incremental-reindex machinery here. If the vector half
is ever built later (a fresh G2/G3 measurement would be required first,
since this file is the record that G2 passed on 2026-08-15), incremental
reindexing becomes worth the complexity because embedding, not indexing, is
the expensive step.
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
    """Raised for a retrieval mode that needs `fastembed`/`sqlite-vec`.

    Neither is a dependency of this repository yet (SPEC-007's vector half
    was not built — the G2 gate stopped at lexical-only). CLI callers catch
    this and exit 2 with an install hint, never a traceback, matching
    SPEC-007 criterion 10's intent even though today there is nothing to
    install a hint *for* — vector mode simply does not exist yet.
    """


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
    """Ranked passages for `q`. `lexical_only` is accepted for SPEC-007
    interface parity; every result is lexical-only today regardless of its
    value, and `Hit.vector_rank` is always `None` (criterion 8) because the
    vector half was not built."""
    del lexical_only  # no other mode exists yet — see module docstring
    if index_path.exists():
        chunks = load_chunks(index_path)
    else:
        chunks = build_chunks((repo_root or Path.cwd()).resolve())
    idx = LexicalIndex.build(chunks)
    results = idx.search(q, k=k)
    return [
        Hit(chunk=chunk, score=score, lexical_rank=rank, vector_rank=None)
        for rank, (chunk, score) in enumerate(results, start=1)
    ]
