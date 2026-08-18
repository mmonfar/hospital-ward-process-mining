"""SPEC-007 criterion 7 / ADR-0007 G3: index build <= 60s, warm query <= 300ms.

Not run by default (`bench` marker, see pyproject.toml) -- like the other
files in this directory, it measures wall-clock on the reference machine
rather than asserting on every CI run. Requires the `retrieve` extra
(`fastembed`/`sqlite-vec`); skipped otherwise, same convention as
`tests/test_retrieve.py`'s vector-half tests.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastembed")
pytest.importorskip("sqlite_vec")

from hwpm.retrieve.corpus import build_chunks
from hwpm.retrieve.vector import build_vector_index, search_vectors

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.bench
def test_vector_index_build_under_60s(tmp_path: Path) -> None:
    chunks = build_chunks(REPO_ROOT)
    out = tmp_path / "context-vectors.db"
    stats = build_vector_index(chunks, out)
    assert stats.build_seconds <= 60.0, (
        f"vector index build took {stats.build_seconds:.1f}s, over the ADR-0007 "
        f"G3 60s budget"
    )


@pytest.mark.bench
def test_warm_vector_query_under_300ms(tmp_path: Path) -> None:
    chunks = build_chunks(REPO_ROOT)
    out = tmp_path / "context-vectors.db"
    build_vector_index(chunks, out)

    # Warm: pay the model-load cost once, outside the timed section.
    search_vectors("why does the project reject PHP", k=8, index_path=out)

    started = time.monotonic()
    search_vectors(
        "how do we handle noisy fitness during calibration", k=8, index_path=out
    )
    elapsed = time.monotonic() - started
    assert elapsed <= 0.3, (
        f"warm query took {elapsed * 1000:.0f}ms, over the 300ms budget"
    )
