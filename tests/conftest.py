"""Shared pytest configuration.

`bench`-marked tests are deselected unless `--bench` is passed. The marker is
already declared in `pyproject.toml`, but declaring a marker only stops
`--strict-markers` complaining about it; it does not stop the test running. A
benchmark that runs on every commit either makes the suite too slow to run or
gets its budget cut until it stops measuring anything, and both failures end
with the benchmark being ignored.

Deselection lives here rather than in `pyproject.toml`'s `addopts` so that the
project's default pytest invocation stays exactly what `CLAUDE.md` and
`docs/06-QA-AND-DEADCODE.md` say it is.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--bench",
        action="store_true",
        default=False,
        help="run tests marked `bench` (throughput and scale benchmarks)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--bench"):
        return
    skip = pytest.mark.skip(reason="benchmark: pass --bench to run")
    for item in items:
        if "bench" in item.keywords:
            item.add_marker(skip)
