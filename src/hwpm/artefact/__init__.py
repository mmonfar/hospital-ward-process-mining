"""Artefact envelope. SPEC-001 criterion 10, ADR-0006 §2, node N04.

Every pipeline stage writes a versioned artefact carrying the
`RequiredSpecialty` strategy that produced it. Depends inward on `hwpm.domain`
only; `ingest`, `mining` and `optimize` all sit above it (import-linter, gate 6).
"""

from __future__ import annotations

from hwpm.artefact.envelope import (
    ALL_STRATEGY_KEYS,
    SCHEMA_VERSION,
    ArtefactEnvelope,
    ArtefactError,
    IncompleteStrategySetError,
    MissingStrategyKeyError,
    UnknownStrategyError,
    read_json,
    write_json,
)

__all__ = [
    "ALL_STRATEGY_KEYS",
    "SCHEMA_VERSION",
    "ArtefactEnvelope",
    "ArtefactError",
    "IncompleteStrategySetError",
    "MissingStrategyKeyError",
    "UnknownStrategyError",
    "read_json",
    "write_json",
]
