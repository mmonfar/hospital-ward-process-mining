"""Tests for SPEC-007 Part A: lexical (N19) and vector/hybrid (N19b).

N19 shipped lexical-only: ADR-0007's G2 measurement came back with
lexical-only recall@5 = 0.75 on the labelled query set in
`tests/fixtures/retrieval_queries.yaml`, above the 0.70 stop threshold.

N19b reopened G2 after the corpus grew: `hwpm.retrieve.vector` (fastembed +
sqlite-vec, `BAAI/bge-small-en-v1.5`, local-only) and `hwpm.retrieve.hybrid`
(Reciprocal Rank Fusion of lexical + vector rankings) are the response.
Vector/hybrid tests are gated on the `retrieve` extra being installed
(`HAS_VECTOR_DEPS` below) and skip cleanly otherwise, the same way
`hwpm govern`/`hwpm.retrieve` itself keeps working without it.

Acceptance criteria covered (numbering from SPEC-007): 1 (page-anchor
chunking), 2 (citations resolve), 3 (verbatim, bounded by k), 4 (eval reports
lexical/vector/hybrid), 5 (G3 hybrid-vs-lexical gate), 6 (query set
well-formed), 8 (lexical-only needs no optional dep), 9 (govern imports
without retrieve deps), 12 (index artefact git-ignored). Criteria 11 and
13-20 are Part A incremental-reindex or Part B and do not apply to what was
built here; noted rather than silently skipped.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hwpm.retrieve.chunk import Chunk, chunk_file, chunk_python, chunk_reference
from hwpm.retrieve.corpus import CorpusSafetyError, build_chunks, iter_corpus_files
from hwpm.retrieve.eval import evaluate, load_queries
from hwpm.retrieve.index import DEFAULT_INDEX_PATH
from hwpm.retrieve.lexical import LexicalIndex

REPO_ROOT = Path(__file__).resolve().parents[1]
QUERIES_PATH = REPO_ROOT / "tests" / "fixtures" / "retrieval_queries.yaml"
REFERENCE_PATH = REPO_ROOT / "refs" / "metaheuristics" / "essentials-of-metaheuristics.md"

try:
    import fastembed  # noqa: F401
    import sqlite_vec  # noqa: F401

    HAS_VECTOR_DEPS = True
except ImportError:
    HAS_VECTOR_DEPS = False

needs_vector_deps = pytest.mark.skipif(
    not HAS_VECTOR_DEPS, reason="requires the 'retrieve' extra (fastembed, sqlite-vec)"
)


@pytest.fixture(scope="module")
def corpus_chunks() -> list[Chunk]:
    return build_chunks(REPO_ROOT)


# --------------------------------------------------------------------------
# Criterion 1 -- page-anchor chunking
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not REFERENCE_PATH.exists(), reason="reference is git-ignored, parsed locally"
)
def test_chunks_respect_page_anchors() -> None:
    """No chunk of the parsed reference spans a `<!-- page N -->` anchor."""
    text = REFERENCE_PATH.read_text(encoding="utf-8")
    chunks = chunk_reference("refs/metaheuristics/essentials-of-metaheuristics.md", text)
    assert chunks, "expected the reference to produce chunks"

    lines = text.splitlines()
    anchor_lines = {
        i
        for i, line in enumerate(lines, start=1)
        if re.match(r"^<!--\s*page\s+\d+\s*-->\s*$", line.strip())
    }
    for chunk in chunks:
        crossed = {n for n in anchor_lines if chunk.start_line < n <= chunk.end_line}
        assert not crossed, f"chunk {chunk.id} crosses page anchor(s) at {crossed}"


# --------------------------------------------------------------------------
# Criterion 2 -- citations resolve
# --------------------------------------------------------------------------


def test_citations_resolve(corpus_chunks: list[Chunk]) -> None:
    """Every chunk's line range, read back off disk, hashes to `chunk.sha256`."""
    import hashlib

    # Sampling the full corpus would re-read every file once per chunk; check
    # a deterministic, evenly-spaced sample plus the first/last chunk of each
    # file so this stays fast without losing whole-corpus coverage over time.
    by_path: dict[str, list[Chunk]] = {}
    for c in corpus_chunks:
        by_path.setdefault(c.path, []).append(c)

    checked = 0
    for path, chunks in by_path.items():
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        lines = text.splitlines()
        for chunk in chunks:
            slice_text = "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
            assert slice_text == chunk.text, f"{chunk.id}: text does not match disk"
            assert hashlib.sha256(slice_text.encode("utf-8")).hexdigest() == chunk.sha256
            checked += 1
    assert checked == len(corpus_chunks)


# --------------------------------------------------------------------------
# Criterion 3 -- search returns at most k, verbatim, chunks
# --------------------------------------------------------------------------


def test_search_returns_verbatim_passages(corpus_chunks: list[Chunk]) -> None:
    idx = LexicalIndex.build(corpus_chunks)
    results = idx.search("why does the project reject PHP", k=5)
    assert 0 < len(results) <= 5
    for chunk, _score in results:
        on_disk = (REPO_ROOT / chunk.path).read_text(encoding="utf-8").splitlines()
        assert "\n".join(on_disk[chunk.start_line - 1 : chunk.end_line]) == chunk.text


# --------------------------------------------------------------------------
# Criterion 4 -- eval reports lexical always; vector/hybrid only on request
# --------------------------------------------------------------------------
#
# `evaluate(..., include_vector=True)` (the default) embeds the full corpus,
# which takes minutes on CPU (measured for this node, see docs/AUDIT-LOG.md
# node N19b) -- too slow for a test that runs on every `pytest -q`. These
# tests all pass `include_vector=False` or otherwise avoid a real embed, so
# they stay fast regardless of whether the `retrieve` extra is installed.
# The full three-way measurement (lexical/vector/hybrid recall+MRR, and the
# hybrid gate) lives in `tests/bench/test_retrieval_quality.py`, run with
# `pytest --bench`.


def test_eval_reports_lexical_baseline(corpus_chunks: list[Chunk]) -> None:
    scores = evaluate(QUERIES_PATH, REPO_ROOT, chunks=corpus_chunks, include_vector=False)
    assert "lexical" in scores
    assert "vector" not in scores
    assert "hybrid" not in scores
    assert 0.0 <= scores["lexical"].recall_at_5 <= 1.0
    assert scores["lexical"].n_queries == len(load_queries(QUERIES_PATH))


def test_eval_omits_vector_and_hybrid_without_deps(
    corpus_chunks: list[Chunk], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without fastembed/sqlite-vec, `evaluate()` (even with
    `include_vector=True`) reports lexical only -- no invented score for a
    system that could not be built here. Blocking the import makes
    `build_vector_index` fail fast, before any real embedding is attempted,
    so this stays cheap."""
    import builtins

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object):
        if name in ("fastembed", "sqlite_vec"):
            raise ImportError(f"{name} blocked for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    scores = evaluate(QUERIES_PATH, REPO_ROOT, chunks=corpus_chunks)
    assert "lexical" in scores
    assert "vector" not in scores
    assert "hybrid" not in scores


# --------------------------------------------------------------------------
# Criterion 5 / G2 -- the lexical-only gate, a regression floor
# --------------------------------------------------------------------------


def test_lexical_recall_gate(corpus_chunks: list[Chunk]) -> None:
    """ADR-0007 G2 floor for the `--lexical-only` path, which stays available
    regardless of whether the vector half is built (SPEC-007 'Where grep
    remains strictly better'). This is a regression floor, not a target --
    a drift below 0.70 is the signal that reopened N19b in the first place,
    not a test to raise the bar on by relabelling queries. The hybrid-path
    gate (G3) is `tests/bench/test_retrieval_quality.py::test_hybrid_recall_gate`,
    run with `pytest --bench` since it needs a full vector-index build."""
    scores = evaluate(QUERIES_PATH, REPO_ROOT, chunks=corpus_chunks, include_vector=False)
    assert scores["lexical"].recall_at_5 > 0.70


# --------------------------------------------------------------------------
# Criterion 6 -- query set well-formed
# --------------------------------------------------------------------------


def test_query_set_wellformed() -> None:
    queries = load_queries(QUERIES_PATH)
    assert len(queries) >= 30
    corpus_paths = {c.path for c in build_chunks(REPO_ROOT)}
    for q in queries:
        assert q.relevant, f"{q.id} has no marked-relevant passage"
        for span in q.relevant:
            assert span.path in corpus_paths, (
                f"{q.id} marks {span.path}, which is not in the indexed corpus"
            )
            assert span.start_line <= span.end_line


# --------------------------------------------------------------------------
# Criterion 8 -- lexical-only needs no optional dependency
# --------------------------------------------------------------------------


def test_lexical_only_needs_no_optional_deps() -> None:
    """`import hwpm.retrieve` alone never pulls in `fastembed`/`sqlite-vec`
    (N19b added `hwpm.retrieve.vector`/`hwpm.retrieve.hybrid` to the package,
    but both are lazy about the optional deps -- see their module
    docstrings). Run in a fresh subprocess rather than in-process: this test
    module itself imports `fastembed`/`sqlite_vec` at collection time (to set
    `HAS_VECTOR_DEPS`), which would otherwise make `mod not in sys.modules`
    fail for a reason unrelated to what this test checks."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import hwpm.retrieve; "
            "assert 'fastembed' not in sys.modules; "
            "assert 'sqlite_vec' not in sys.modules; "
            "print('ok')",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


# --------------------------------------------------------------------------
# Criterion 9 -- hwpm govern is unaffected
# --------------------------------------------------------------------------


def test_govern_imports_without_retrieve_deps() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from hwpm.govern import audit, ledger, graph"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# Criterion 12 -- index artefact untracked
# --------------------------------------------------------------------------


def test_index_artefact_is_gitignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert str(DEFAULT_INDEX_PATH.parts[0]) + "/" in gitignore or ".hwpm" in gitignore


def test_index_artefact_not_tracked_by_git() -> None:
    result = subprocess.run(
        ["git", "ls-files", str(DEFAULT_INDEX_PATH.parts[0])],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == ""


# --------------------------------------------------------------------------
# Corpus safety -- ADR-0005 / rule 2, this node touches no patient data
# --------------------------------------------------------------------------


def test_corpus_excludes_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `HWPM_DATA_DIR` were ever pointed inside a would-be corpus root,
    building the corpus must refuse rather than silently index it."""
    data_dir = tmp_path / "patient_data"
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    data_dir.mkdir()
    (data_dir / "leaked.md").write_text("# should never be indexed\n", encoding="utf-8")
    monkeypatch.setenv("HWPM_DATA_DIR", str(data_dir))

    from hwpm.retrieve.corpus import _guard_not_data_dir

    with pytest.raises(CorpusSafetyError):
        _guard_not_data_dir(data_dir / "leaked.md")


def test_iter_corpus_files_never_under_data_dir() -> None:
    """The real corpus, indexed against the real `HWPM_DATA_DIR` if one is
    configured for this machine, never yields a file under it."""
    import os

    data_dir = os.environ.get("HWPM_DATA_DIR")
    if not data_dir:
        pytest.skip("HWPM_DATA_DIR not configured on this machine")
    resolved = Path(data_dir).resolve()
    for f in iter_corpus_files(REPO_ROOT):
        assert resolved not in f.resolve().parents


# --------------------------------------------------------------------------
# Determinism -- gate 8
# --------------------------------------------------------------------------


def test_chunking_is_deterministic(tmp_path: Path) -> None:
    """Same corpus in, same chunks out.

    Deliberately built against a private `tmp_path` corpus rather than
    re-scanning the live repo twice: two other agents (N04, N05) are writing
    files under `docs/` and `src/hwpm/` in this same working tree during this
    session, so two `build_chunks(REPO_ROOT)` calls a few lines apart are not
    guaranteed to see the same filesystem -- that is a fact about concurrent
    editing, not non-determinism in the chunker being tested here.
    """
    docs_dir = tmp_path / "docs"
    src_dir = tmp_path / "src" / "hwpm" / "pkg"
    docs_dir.mkdir(parents=True)
    src_dir.mkdir(parents=True)
    (docs_dir / "NOTE.md").write_text(
        "# Title\n\n## Section one\n\nSome text.\n\n## Section two\n\nMore text.\n",
        encoding="utf-8",
    )
    (src_dir / "mod.py").write_text(
        '"""Module doc."""\n\n\ndef fn(x: int) -> int:\n    """Doc."""\n    return x\n',
        encoding="utf-8",
    )

    first = build_chunks(tmp_path)
    second = build_chunks(tmp_path)
    assert first, "expected the fixture corpus to produce chunks"
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.sha256 for c in first] == [c.sha256 for c in second]


def test_search_is_deterministic(corpus_chunks: list[Chunk]) -> None:
    idx = LexicalIndex.build(corpus_chunks)
    first = idx.search("why does the project reject PHP", k=5)
    second = idx.search("why does the project reject PHP", k=5)
    assert [c.id for c, _ in first] == [c.id for c, _ in second]
    assert [round(s, 9) for _, s in first] == [round(s, 9) for _, s in second]


# --------------------------------------------------------------------------
# Python chunking: signature + docstring, never the body
# --------------------------------------------------------------------------


def test_python_chunk_excludes_function_body() -> None:
    source = '''"""Module doc."""


def add(a: int, b: int) -> int:
    """Add two numbers."""
    total = a + b
    return total
'''
    chunks = chunk_python("example.py", source, "example")
    fn_chunks = [c for c in chunks if c.heading_path == "example.add"]
    assert len(fn_chunks) == 1
    assert "total = a + b" not in fn_chunks[0].text
    assert "def add" in fn_chunks[0].text
    assert "Add two numbers." in fn_chunks[0].text


def test_audit_log_chunks_one_per_entry() -> None:
    from hwpm.retrieve.chunk import chunk_audit_log

    text = (
        "# Audit log\n\npreamble text\n\n"
        "## 2026-01-01T00:00:00Z — First entry\n\nbody one\n\n"
        "## 2026-01-02T00:00:00Z — Second entry\n\nbody two\n"
    )
    chunks = chunk_audit_log("docs/AUDIT-LOG.md", text)
    headings = [c.heading_path for c in chunks]
    assert "2026-01-01T00:00:00Z — First entry" in headings
    assert "2026-01-02T00:00:00Z — Second entry" in headings
    for c in chunks:
        # No entry chunk should contain another entry's heading text.
        others = [h for h in headings if h != c.heading_path and h != "preamble"]
        for other in others:
            assert other not in c.text


def test_chunk_file_dispatches_by_name(tmp_path: Path) -> None:
    md = tmp_path / "NOTE.md"
    md.write_text("# Title\n\nSome content here that is short.\n", encoding="utf-8")
    chunks = chunk_file(md, repo_relative="NOTE.md")
    assert chunks and all(c.path == "NOTE.md" for c in chunks)


def test_load_queries_roundtrip() -> None:
    raw = yaml.safe_load(QUERIES_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert len(raw["queries"]) == len(load_queries(QUERIES_PATH))


# --------------------------------------------------------------------------
# The persisted index: build, load, search (index.py)
# --------------------------------------------------------------------------


def test_build_index_persists_stats_and_chunks(tmp_path: Path) -> None:
    from hwpm.retrieve.index import build_index, load_chunks, load_stats

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "NOTE.md").write_text("# Title\n\nSome content.\n", encoding="utf-8")
    out = tmp_path / ".hwpm" / "context-index.json"

    stats = build_index([tmp_path], out)
    assert out.exists()
    assert stats.model == "lexical-bm25"
    assert stats.n_chunks > 0
    assert stats.build_seconds >= 0.0

    reloaded_stats = load_stats(out)
    assert reloaded_stats == stats

    chunks = load_chunks(out)
    assert len(chunks) == stats.n_chunks
    assert chunks[0].path == "docs/NOTE.md"


def test_search_uses_persisted_index_when_present(tmp_path: Path) -> None:
    from hwpm.retrieve.index import DEFAULT_INDEX_PATH, build_index, search

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "NOTE.md").write_text(
        "# Title\n\nWhy was PHP rejected? Because of runtime duplication.\n",
        encoding="utf-8",
    )
    out = tmp_path / DEFAULT_INDEX_PATH
    build_index([tmp_path], out)

    hits = search("why was PHP rejected", k=3, index_path=out, repo_root=tmp_path)
    assert hits
    assert hits[0].chunk.path == "docs/NOTE.md"
    assert hits[0].lexical_rank == 1
    assert hits[0].vector_rank is None
    assert hits[0].citation() == hits[0].chunk.citation()


def test_search_falls_back_to_live_corpus_when_no_index(tmp_path: Path) -> None:
    from hwpm.retrieve.index import search

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "NOTE.md").write_text(
        "# Title\n\nSome unique zzyzx content.\n", encoding="utf-8"
    )
    missing_index = tmp_path / ".hwpm" / "does-not-exist.json"

    hits = search("zzyzx", k=3, index_path=missing_index, repo_root=tmp_path)
    assert hits
    assert hits[0].chunk.path == "docs/NOTE.md"


# --------------------------------------------------------------------------
# CLI smoke test
# --------------------------------------------------------------------------


def test_cli_context_eval_reports_lexical() -> None:
    """`--lexical-only` keeps this smoke test fast (no embedding) regardless
    of whether the `retrieve` extra is installed. The full hybrid path via
    the CLI is exercised for real, once, as part of closing this node (see
    docs/AUDIT-LOG.md); it is not re-run on every `pytest -q` for the same
    reason `tests/bench/test_retrieval_quality.py` is `bench`-marked."""
    result = subprocess.run(
        [sys.executable, "-m", "hwpm.cli", "context", "eval", "--lexical-only"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    import json as _json

    payload = _json.loads(result.stdout)
    assert "lexical" in payload
    assert "vector" not in payload
    assert "hybrid" not in payload
    assert payload["lexical"]["n_queries"] >= 30


# --------------------------------------------------------------------------
# N19b -- RRF fusion, pure function (no embedding calls needed)
# --------------------------------------------------------------------------


def _mk_chunk(cid: str) -> Chunk:
    return Chunk(
        id=cid,
        path="docs/FAKE.md",
        start_line=1,
        end_line=1,
        page_anchor=None,
        heading_path="fake",
        text=f"text for {cid}",
        sha256="0" * 64,
    )


def test_rrf_fuse_prefers_chunk_ranked_by_both_retrievers() -> None:
    from hwpm.retrieve.hybrid import rrf_fuse

    a, b, c = _mk_chunk("a"), _mk_chunk("b"), _mk_chunk("c")
    # b is #2 lexically and #1 by vector -- it should outrank a (which only
    # the lexical retriever found) once fused, since it accumulates score
    # from both retrievers.
    lexical = [(a, 10.0), (b, 5.0)]
    vector = [(b, 0.9), (c, 0.5)]

    ranked = rrf_fuse(lexical, vector, k=3)
    ids = [h.chunk.id for h in ranked]
    assert ids[0] == "b"
    hit_b = next(h for h in ranked if h.chunk.id == "b")
    assert hit_b.lexical_rank == 2
    assert hit_b.vector_rank == 1
    hit_a = next(h for h in ranked if h.chunk.id == "a")
    assert hit_a.lexical_rank == 1
    assert hit_a.vector_rank is None


def test_rrf_fuse_matches_hand_computed_scores() -> None:
    from hwpm.retrieve.hybrid import RRF_K, rrf_fuse

    a, b = _mk_chunk("a"), _mk_chunk("b")
    lexical = [(a, 1.0), (b, 2.0)]  # a rank 1, b rank 2
    vector = [(b, 1.0)]  # b rank 1

    ranked = rrf_fuse(lexical, vector, k=2)
    scores = {h.chunk.id: h.score for h in ranked}
    assert scores["a"] == pytest.approx(1.0 / (RRF_K + 1))
    assert scores["b"] == pytest.approx(1.0 / (RRF_K + 2) + 1.0 / (RRF_K + 1))


def test_rrf_fuse_respects_k() -> None:
    from hwpm.retrieve.hybrid import rrf_fuse

    chunks = [_mk_chunk(str(i)) for i in range(5)]
    lexical = [(c, float(5 - i)) for i, c in enumerate(chunks)]
    ranked = rrf_fuse(lexical, [], k=2)
    assert len(ranked) == 2


# --------------------------------------------------------------------------
# N19b -- vector index: build, persist, search (requires the retrieve extra)
# --------------------------------------------------------------------------


@needs_vector_deps
def test_vector_index_build_records_model_and_sha(tmp_path: Path) -> None:
    """Closes the gap N19's audit entry flagged: 'model=lexical-bm25,
    model_sha empty'. The vector build's stats carry a real model id and a
    non-empty sha256 of the embedding weights, so a silently changed model
    is visible (SPEC-007 'Tooling decision')."""
    from hwpm.retrieve.vector import MODEL_NAME, build_vector_index

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "NOTE.md").write_text(
        "# Title\n\nWhy was PHP rejected? Because of runtime duplication.\n",
        encoding="utf-8",
    )
    chunks = build_chunks(tmp_path)
    out = tmp_path / ".hwpm" / "context-vectors.db"

    stats = build_vector_index(chunks, out)
    assert out.exists()
    assert stats.model == MODEL_NAME
    assert stats.model_sha, "model_sha must not be empty -- this is the gap N19b closes"
    assert len(stats.model_sha) == 64  # sha256 hex digest
    assert stats.dim == 384
    assert stats.n_chunks == len(chunks)


@needs_vector_deps
def test_vector_search_finds_semantically_related_chunk(tmp_path: Path) -> None:
    """The point of the vector half: find a passage worded completely
    differently from the query (SPEC-007's own example)."""
    from hwpm.retrieve.vector import build_vector_index, search_vectors

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "NOTE.md").write_text(
        "# Calibration\n\n"
        "Elite fitness is re-evaluated each generation, never cached, to "
        "avoid a lucky noisy sample locking in an undeserved elite.\n\n"
        "## Unrelated\n\nThe cafeteria menu rotates weekly on Tuesdays.\n",
        encoding="utf-8",
    )
    chunks = build_chunks(tmp_path)
    out = tmp_path / ".hwpm" / "context-vectors.db"
    build_vector_index(chunks, out)

    results = search_vectors(
        "how do we handle noisy fitness during calibration", k=3, index_path=out
    )
    assert results
    assert "Elite fitness" in results[0][0].text


@needs_vector_deps
def test_vector_search_returns_empty_without_built_index(tmp_path: Path) -> None:
    from hwpm.retrieve.vector import search_vectors

    missing = tmp_path / ".hwpm" / "does-not-exist.db"
    assert search_vectors("anything", k=3, index_path=missing) == []


@needs_vector_deps
def test_hybrid_build_returns_working_retriever(tmp_path: Path) -> None:
    from hwpm.retrieve.hybrid import build

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "NOTE.md").write_text(
        "# Title\n\nWhy was PHP rejected? Because of runtime duplication.\n",
        encoding="utf-8",
    )
    chunks = build_chunks(tmp_path)
    retriever = build(chunks, tmp_path)
    results = retriever.search("why was PHP rejected", k=3)
    assert results
    assert results[0][0].path == "docs/NOTE.md"


def test_search_without_vector_index_falls_back_to_lexical(tmp_path: Path) -> None:
    """`hwpm.retrieve.index.search()` with `lexical_only=False` but no
    vector index on disk (deps present or not) must degrade to lexical
    ranking rather than raising -- vector search is additive."""
    from hwpm.retrieve.index import DEFAULT_INDEX_PATH, build_index, search

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "NOTE.md").write_text(
        "# Title\n\nWhy was PHP rejected? Because of runtime duplication.\n",
        encoding="utf-8",
    )
    out = tmp_path / DEFAULT_INDEX_PATH
    build_index([tmp_path], out)

    hits = search("why was PHP rejected", k=3, index_path=out, repo_root=tmp_path)
    assert hits
    assert hits[0].chunk.path == "docs/NOTE.md"
    assert hits[0].vector_rank is None
