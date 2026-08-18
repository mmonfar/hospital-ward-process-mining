"""The vector half of SPEC-007 Part A (N19b, ADR-0007 G2 reopened).

Embeds the same corpus the lexical retriever indexes with a local ONNX model
(`fastembed`, CPU-only, no PyTorch/CUDA) and stores the vectors locally in a
`sqlite-vec` file. Nothing leaves the machine once the model weights are
cached: `fastembed` fetches them once from Hugging Face (the ~130 MB download
the user approved 2026-08-14), then every embed call runs offline
(SPEC-007 criterion 20). This module never touches `HWPM_DATA_DIR` — it is
still Part A, corpus text only, no patient data, same as `hwpm.retrieve.lexical`.

Optional dependency, deliberately not imported anywhere eagerly: `hwpm.retrieve`
must still import cleanly with neither `fastembed` nor `sqlite-vec` installed
(SPEC-007 criteria 8-9). Every public function here raises
`MissingOptionalDependencyError` (re-exported from `hwpm.retrieve.index`) if
called without the deps present, rather than a bare `ImportError`.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from hwpm.retrieve.chunk import Chunk
from hwpm.retrieve.index import MissingOptionalDependencyError

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384
DEFAULT_VECTOR_DB_PATH = Path(".hwpm") / "context-vectors.db"

_INSTALL_HINT = "pip install -e '.[retrieve]'"

# One embedding model per process is enough; re-loading the ONNX session per
# call would dominate runtime (loading costs ~10s, embedding ~900 short
# passages costs a few seconds once loaded).
_MODEL_CACHE: dict[str, object] = {}


@dataclass(frozen=True)
class VectorStats:
    n_chunks: int
    build_seconds: float
    model: str
    model_sha: str
    dim: int


def _require_fastembed():
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise MissingOptionalDependencyError(
            f"fastembed is not installed; {_INSTALL_HINT}"
        ) from exc
    return TextEmbedding


def _require_sqlite_vec():
    try:
        import sqlite_vec
    except ImportError as exc:
        raise MissingOptionalDependencyError(
            f"sqlite-vec is not installed; {_INSTALL_HINT}"
        ) from exc
    return sqlite_vec


def model_cache_dir() -> Path:
    """Where model weights are cached. SPEC-007: 'Weights are cached under
    `HWPM_MODEL_CACHE` (default: the user cache dir)', never committed."""
    raw = os.environ.get("HWPM_MODEL_CACHE")
    if raw:
        return Path(raw)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if not base:
        base = str(Path.home() / ".cache")
    return Path(base) / "hwpm" / "fastembed"


def _get_model(model_name: str = MODEL_NAME):
    text_embedding_cls = _require_fastembed()
    if model_name not in _MODEL_CACHE:
        cache_dir = model_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        _MODEL_CACHE[model_name] = text_embedding_cls(
            model_name, cache_dir=str(cache_dir)
        )
    return _MODEL_CACHE[model_name]


def model_sha256(model_name: str = MODEL_NAME) -> str:
    """sha256 of the primary ONNX weights file. Recorded in every build's
    stats so a silently changed embedding model produces a visibly different
    `model_sha` (SPEC-007 'Tooling decision' — 'the same class of problem as
    an unversioned method change under ADR-0006'). Loads the model first if
    the weights are not yet cached, which is the one point a download can
    happen."""
    cache_dir = model_cache_dir()
    candidates = sorted(cache_dir.rglob("model_optimized.onnx"))
    if not candidates:
        _get_model(model_name)
        candidates = sorted(cache_dir.rglob("model_optimized.onnx"))
    if not candidates:
        return ""
    h = hashlib.sha256()
    with candidates[-1].open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _normalize(vec: list[float]) -> list[float]:
    total = sum(x * x for x in vec) ** 0.5
    if total <= 0.0:
        return list(vec)
    return [x / total for x in vec]


def embed_texts(texts: list[str], model_name: str = MODEL_NAME) -> list[list[float]]:
    """L2-normalised embeddings, one per input text, order-preserving. Cosine
    similarity between normalised vectors is then a linear function of L2
    distance, which is what `sqlite-vec`'s `vec0` distance metric computes
    (SPEC-007 modelling assumption 4: cosine similarity over normalised
    vectors)."""
    if not texts:
        return []
    model = _get_model(model_name)
    return [_normalize(list(v)) for v in model.embed(texts)]


def _embed_text_for_chunk(chunk: Chunk) -> str:
    # Same signal shape as the lexical index (hwpm.retrieve.lexical):
    # heading_path + text, so a chunk retrieved out of context still says
    # what document it is from (SPEC-007 chunking rule 1).
    return f"{chunk.heading_path}\n{chunk.text}"


def _connect(db_path: Path) -> sqlite3.Connection:
    sqlite_vec_mod = _require_sqlite_vec()
    db = sqlite3.connect(str(db_path))
    db.enable_load_extension(True)
    sqlite_vec_mod.load(db)
    db.enable_load_extension(False)
    return db


def build_vector_index(
    chunks: list[Chunk], out: Path, model_name: str = MODEL_NAME
) -> VectorStats:
    """Embed `chunks` and persist them to a `sqlite-vec` file at `out`.
    Rebuilds from scratch every call — no incremental re-embed (SPEC-007
    modelling note, `hwpm.retrieve.index` docstring).

    Measured cost, not assumed: over the full corpus (~1,300+ chunks, most of
    them from `refs/metaheuristics/essentials-of-metaheuristics.md`) this took
    several minutes on this reference machine's CPU, well over the ADR-0007
    G3 60s build-time budget -- see `docs/AUDIT-LOG.md` node N19b for the
    exact figure. `tests/bench/test_retrieval_latency.py` records this
    honestly rather than asserting a budget the measurement did not clear;
    warm single-query search is a different, much cheaper operation (one
    short text embedded, not the whole corpus) and is what criterion 7's
    300ms figure actually governs day-to-day."""
    sqlite_vec_mod = _require_sqlite_vec()
    started = time.monotonic()
    texts = [_embed_text_for_chunk(c) for c in chunks]
    vectors = embed_texts(texts, model_name)
    build_seconds = time.monotonic() - started

    dim = len(vectors[0]) if vectors else EMBEDDING_DIM

    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    db = _connect(out)
    try:
        db.execute(f"create virtual table vec_chunks using vec0(embedding float[{dim}])")
        db.execute(
            "create table chunk_meta ("
            "rowid integer primary key, id text, path text, start_line integer, "
            "end_line integer, page_anchor integer, heading_path text, text text, "
            "sha256 text)"
        )
        for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True)):
            db.execute(
                "insert into vec_chunks(rowid, embedding) values (?, ?)",
                (i, sqlite_vec_mod.serialize_float32(vec)),
            )
            db.execute(
                "insert into chunk_meta values (?,?,?,?,?,?,?,?,?)",
                (
                    i,
                    chunk.id,
                    chunk.path,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.page_anchor,
                    chunk.heading_path,
                    chunk.text,
                    chunk.sha256,
                ),
            )
        stats = VectorStats(
            n_chunks=len(chunks),
            build_seconds=build_seconds,
            model=model_name,
            model_sha=model_sha256(model_name),
            dim=dim,
        )
        db.execute(
            "create table if not exists index_meta (key text primary key, value text)"
        )
        for k, v in {
            "model": stats.model,
            "model_sha": stats.model_sha,
            "dim": str(stats.dim),
            "n_chunks": str(stats.n_chunks),
            "build_seconds": repr(stats.build_seconds),
        }.items():
            db.execute("insert or replace into index_meta values (?,?)", (k, v))
        db.commit()
    finally:
        db.close()
    return stats


def load_vector_stats(index_path: Path) -> VectorStats:
    db = _connect(index_path)
    try:
        rows = dict(db.execute("select key, value from index_meta").fetchall())
        return VectorStats(
            n_chunks=int(rows["n_chunks"]),
            build_seconds=float(rows["build_seconds"]),
            model=rows["model"],
            model_sha=rows["model_sha"],
            dim=int(rows["dim"]),
        )
    finally:
        db.close()


def _row_to_chunk(row: tuple) -> Chunk:
    (_rowid, cid, path, start_line, end_line, page_anchor, heading_path, text, sha256) = (
        row
    )
    return Chunk(
        id=cid,
        path=path,
        start_line=start_line,
        end_line=end_line,
        page_anchor=page_anchor,
        heading_path=heading_path,
        text=text,
        sha256=sha256,
    )


def search_vectors(
    query: str, k: int, index_path: Path, model_name: str = MODEL_NAME
) -> list[tuple[Chunk, float]]:
    """Ranked `(Chunk, similarity)` pairs, `similarity` in `[-1, 1]`
    (approximately `[0, 1]` for this model family), highest first. Empty list
    if the index has not been built."""
    if not index_path.exists():
        return []
    qvec = embed_texts([query], model_name)[0]
    sqlite_vec_mod = _require_sqlite_vec()
    db = _connect(index_path)
    try:
        rows = db.execute(
            "select rowid, distance from vec_chunks where embedding match ? and k = ? "
            "order by distance",
            (sqlite_vec_mod.serialize_float32(qvec), k),
        ).fetchall()
        results: list[tuple[Chunk, float]] = []
        for rowid, distance in rows:
            meta_row = db.execute(
                "select * from chunk_meta where rowid=?", (rowid,)
            ).fetchone()
            if meta_row is None:
                continue
            # Both vectors are unit-norm, so L2 distance^2 = 2 - 2*cos.
            similarity = 1.0 - (distance**2) / 2.0
            results.append((_row_to_chunk(meta_row), similarity))
        return results
    finally:
        db.close()


class VectorRetriever:
    """Adapter matching the `_Retriever` protocol in `hwpm.retrieve.eval`:
    `.search(query, k) -> list[(Chunk, score)]`. Thin wrapper so the vector
    half can be scored on its own, the same way the lexical half is, for
    honest three-way reporting (lexical / vector / hybrid)."""

    def __init__(self, index_path: Path, model_name: str = MODEL_NAME) -> None:
        self.index_path = index_path
        self.model_name = model_name

    def search(self, query: str, k: int = 8) -> list[tuple[Chunk, float]]:
        return search_vectors(query, k, self.index_path, self.model_name)
