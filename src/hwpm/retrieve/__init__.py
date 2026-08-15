"""Agent context retrieval over this repository's own text (SPEC-007 Part A).

Adapter layer: imports only stdlib plus (lazily, when the `retrieve` extra is
installed) `fastembed`/`sqlite-vec` for the vector half. Touches no patient
data — the corpus is `docs/`, `src/hwpm/**/*.py` docstrings and signatures, and
the parsed metaheuristics reference. See ADR-0007 and SPEC-007.

Everything importable from here without an optional dependency: chunking,
the lexical retriever, and the evaluation harness. The vector half only
exists if the measurement gate in ADR-0007 fired; see `docs/AUDIT-LOG.md`
for whether it did.
"""

from __future__ import annotations

from hwpm.retrieve.chunk import Chunk
from hwpm.retrieve.corpus import build_chunks, iter_corpus_files
from hwpm.retrieve.eval import LabelledQuery, RetrievalScore, evaluate, load_queries
from hwpm.retrieve.index import (
    Hit,
    IndexStats,
    MissingOptionalDependencyError,
    build_index,
    search,
)
from hwpm.retrieve.lexical import LexicalIndex, tokenize

__all__ = [
    "Chunk",
    "Hit",
    "IndexStats",
    "LabelledQuery",
    "LexicalIndex",
    "MissingOptionalDependencyError",
    "RetrievalScore",
    "build_chunks",
    "build_index",
    "evaluate",
    "iter_corpus_files",
    "load_queries",
    "search",
    "tokenize",
]
