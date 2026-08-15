"""`discover` / `conformance`: SPEC-002's process-discovery interface,
orchestrated behind the ADR-0008 Protocol boundary. N05-mining.

Neither function here imports `pm4py`. Both depend on
`hwpm.mining._pm4py_adapter` -- our own module, not pm4py itself -- only as the
*default* engine, reached through `ProcessDiscovery` / `ConformanceChecker`
(the Protocols ADR-0008 requires "declared in `hwpm.mining`", see
`hwpm.mining.types`). Passing a different `engine` swaps the implementation
without touching a caller: ADR-0008's whole point, "optionality... whichever
way the licensing decision goes, we are not re-architecting."
"""

from __future__ import annotations

from hwpm.mining import _pm4py_adapter
from hwpm.mining.types import (
    ConformanceChecker,
    ConformanceReport,
    EventLog,
    ProcessDiscovery,
    ProcessModel,
)


def discover(
    log: EventLog,
    algorithm: str = "inductive",
    engine: ProcessDiscovery | None = None,
) -> ProcessModel:
    """SPEC-002 interface: `discover(log, algorithm="inductive")`.

    `engine` is a seam, not part of the spec's signature -- omitted, this
    behaves exactly as specified, backed by pm4py's inductive miner (or
    heuristics miner, SPEC-002's named comparator). Supplied, it is how a
    test exercises this function's plumbing (case grouping, report shape)
    without pm4py at all, and how ADR-0008's "swap to a permissive
    alternative" escape route would be exercised for real.
    """
    if engine is None:
        engine = _pm4py_adapter.Pm4pyProcessDiscovery(algorithm=algorithm)
    return engine.discover(log)


def conformance(
    log: EventLog,
    model: ProcessModel,
    engine: ConformanceChecker | None = None,
) -> ConformanceReport:
    """SPEC-002 interface: `conformance(log, model)`. See `discover` for what
    `engine` is for."""
    if engine is None:
        engine = _pm4py_adapter.Pm4pyConformanceChecker()
    return engine.check(log, model)


__all__ = ["conformance", "discover"]
