"""The strategy-keyed artefact envelope. SPEC-001 criterion 10, ADR-0006 §2.

Every stage of the pipeline writes a versioned artefact (02-ARCHITECTURE.md,
"Data flow": "nothing downstream recomputes from raw ... the artefact chain is
the evidence"). SPEC-001 adds a requirement that outranks the format: **every
artefact carries the `RequiredSpecialty` strategy that produced it, and loading
one without it raises.** ADR-0006 §2 gives the reason — the denominator of the
MDT coverage metric is now a runtime choice, so a figure that travels without
its definition is a figure that will be compared against a different definition
and reported as a change.

That is why this is a package of its own rather than a helper inside `ingest`.
`mining`, `analytics`, `optimize` and the viewer all write artefacts; a shared
envelope in `ingest` would either be imported upward or, more likely, quietly
reimplemented without the check.

Enforcement is at load time and is a hard failure, never a warning:

- no `strategy` key            -> `MissingStrategyKeyError`  (criterion 10)
- unknown `strategy` value     -> `UnknownStrategyError`
- `available_strategies` is not all five -> `IncompleteStrategySetError`
  (criterion 9's "none can be skipped", ADR-0006 §3's "always computed, always
  displayed as a spread" — enforced at the file boundary, where it cannot be
  forgotten by a rendering path.)

No wall-clock reads: `method_version` is supplied by the caller. An artefact
that stamped itself with `datetime.now()` would not be byte-reproducible and
gate 8 would be checking something weaker than it claims to.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hwpm.domain import Calibration, RequiredSpecialtyStrategyKey

#: Bumped when the envelope's own shape changes, independently of
#: `method_version`, which tracks the analysis method (ADR-0006 §1).
SCHEMA_VERSION = 1

#: The full set criterion 9 requires. Named rather than inlined so the
#: "all five" check and the enum cannot drift apart.
ALL_STRATEGY_KEYS: frozenset[RequiredSpecialtyStrategyKey] = frozenset(
    RequiredSpecialtyStrategyKey
)


class ArtefactError(Exception):
    """Base for every artefact-schema failure. Loading is all-or-nothing."""


class MissingStrategyKeyError(ArtefactError):
    """Criterion 10: "loading one without it raises"."""


class UnknownStrategyError(ArtefactError):
    """A `strategy` value that is not one of the five."""


class IncompleteStrategySetError(ArtefactError):
    """Fewer than all five strategies present (criterion 9, ADR-0006 §3)."""


class EmbeddingDerivedFieldError(ArtefactError):
    """A payload field derived from a learned embedding. SPEC-007 criterion 15.

    ADR-0007 decision 5 prohibits an embedding underpinning a reported figure
    without an interpretable derivation alongside it, and says the mechanism of
    the eventual breach is predictable: nobody proposes basing MDT coverage on
    an embedding, somebody proposes a "similar wards" comparator. This is the
    file boundary saying no to that, in the same place and the same way the
    missing-strategy check does -- at load and at construction, where a
    rendering path cannot route around it.
    """


@dataclass(frozen=True)
class ArtefactEnvelope:
    """A payload plus the provenance a governance-monitored figure needs.

    `payload` is opaque here on purpose: the envelope's job is the contract
    every artefact shares, and a package that also knew the shape of
    `motion.json` would have to change every time an analysis did.
    """

    kind: str
    method_version: str
    strategy: RequiredSpecialtyStrategyKey
    available_strategies: frozenset[RequiredSpecialtyStrategyKey]
    calibration: Calibration
    payload: Mapping[str, Any]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.available_strategies != ALL_STRATEGY_KEYS:
            missing = sorted(
                key.value for key in ALL_STRATEGY_KEYS - self.available_strategies
            )
            raise IncompleteStrategySetError(
                "every artefact must offer all five strategies (SPEC-001 "
                f"criterion 9, ADR-0006 §3); missing {missing}"
            )
        reject_embedding_fields(self.payload)
        # No "is `strategy` available?" check: the line above has already
        # established that `available_strategies` is all five, and `strategy`
        # is an enum member, so it is. A second guard would be unreachable
        # code, and unreachable guards are how a file grows checks nobody can
        # test (docs/06-QA-AND-DEADCODE.md).

    def with_strategy(self, key: RequiredSpecialtyStrategyKey) -> ArtefactEnvelope:
        """Re-key to a different strategy without recomputing anything.

        This is what makes the front-end selector (N14) instant and what makes
        "selecting a strategy changes which is emphasised, never which are
        available" (SPEC-001) true of the data as well as the UI: all five are
        already in the payload. No availability check for the same reason as
        `__post_init__`: `available_strategies` is all five by construction, so
        every enum member is selectable and a guard here would be untestable.
        """
        return ArtefactEnvelope(
            kind=self.kind,
            method_version=self.method_version,
            strategy=key,
            available_strategies=self.available_strategies,
            calibration=self.calibration,
            payload=self.payload,
            schema_version=self.schema_version,
        )

    def to_mapping(self) -> dict[str, Any]:
        """JSON-ready form. Strategy order is the enum's, not the set's, so two
        runs over the same data serialise identically (gate 8)."""
        return {
            "kind": self.kind,
            "schema_version": self.schema_version,
            "method_version": self.method_version,
            "strategy": self.strategy.value,
            "available_strategies": [
                key.value
                for key in RequiredSpecialtyStrategyKey
                if key in self.available_strategies
            ],
            "calibration": self.calibration.value,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ArtefactEnvelope:
        """Parse an envelope, refusing anything that lost its provenance.

        Every branch here raises rather than defaulting. A default strategy
        applied at load time would be indistinguishable, downstream, from an
        artefact that genuinely used that strategy — which is the exact failure
        ADR-0006 §2 exists to prevent.
        """
        if "strategy" not in data or data["strategy"] is None:
            raise MissingStrategyKeyError(
                "artefact has no `strategy` key; a figure without its "
                "RequiredSpecialty definition is not loadable (SPEC-001 "
                "criterion 10, ADR-0006 §2)"
            )
        strategy = _parse_strategy(data["strategy"])

        raw_available = data.get("available_strategies")
        if raw_available is None:
            raise IncompleteStrategySetError(
                "artefact has no `available_strategies` key; all five must be "
                "computed and offered (SPEC-001 criterion 9)"
            )
        available = frozenset(_parse_strategy(value) for value in raw_available)

        raw_calibration = data.get("calibration")
        if raw_calibration is None:
            raise ArtefactError(
                "artefact has no `calibration` key; an uncalibrated confidence "
                "rendered as a probability is SPEC-001's most likely serious error"
            )
        try:
            calibration = Calibration(raw_calibration)
        except ValueError as exc:
            raise ArtefactError(f"unknown calibration {raw_calibration!r}") from exc

        for required in ("kind", "method_version", "schema_version", "payload"):
            if required not in data:
                raise ArtefactError(f"artefact has no `{required}` key")

        return cls(
            kind=str(data["kind"]),
            method_version=str(data["method_version"]),
            strategy=strategy,
            available_strategies=available,
            calibration=calibration,
            payload=dict(data["payload"]),
            schema_version=int(data["schema_version"]),
        )


#: Field-name fragments that mark a value as embedding-derived. Substring
#: matching on a lowercased key, not an exact list, because the field that
#: eventually appears will be called `similar_wards_embedding_distance` or
#: `cohort_vector`, not `embedding`. A name-based check is admittedly a
#: heuristic and cannot see a laundered field called `similarity_index`; it is
#: the *second* line of defence. The first is the import-linter contract
#: "Governance figures do not depend on embeddings" in `pyproject.toml`, which
#: stops the code that would compute such a field from existing at all.
EMBEDDING_FIELD_MARKERS: tuple[str, ...] = (
    "embedding",
    "cosine",
    "atypicality",
    "vector_distance",
    "nearest_neighbour_distance",
    "nearest_neighbor_distance",
)


def reject_embedding_fields(payload: Mapping[str, Any], _path: str = "payload") -> None:
    """Raise `EmbeddingDerivedFieldError` if `payload` holds an
    embedding-derived field, at any depth. SPEC-007 criterion 15.

    Recurses into nested mappings and into lists of mappings, because a
    payload's second level is exactly where a "similar days" comparator would
    be added by someone who had read the top-level check and worked around it.
    """
    for key, value in payload.items():
        lowered = str(key).lower()
        for marker in EMBEDDING_FIELD_MARKERS:
            if marker in lowered:
                raise EmbeddingDerivedFieldError(
                    f"{_path}.{key} looks embedding-derived (matched {marker!r}). "
                    "ADR-0007 decision 5: an embedding may never be the basis of "
                    "a reported governance figure without an interpretable "
                    "derivation published alongside it. Publish the named, "
                    "unit-carrying features (hwpm.mining.embed.FEATURE_NAMES) "
                    "instead, or publish both"
                )
        if isinstance(value, Mapping):
            reject_embedding_fields(value, f"{_path}.{key}")
        elif isinstance(value, list | tuple):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    reject_embedding_fields(item, f"{_path}.{key}[{index}]")


def _parse_strategy(value: Any) -> RequiredSpecialtyStrategyKey:
    try:
        return RequiredSpecialtyStrategyKey(value)
    except ValueError as exc:
        raise UnknownStrategyError(
            f"{value!r} is not one of the five RequiredSpecialty strategies"
        ) from exc


def write_json(envelope: ArtefactEnvelope, path: Path) -> None:
    """Write `envelope` as UTF-8 JSON.

    `sort_keys=False` keeps the declaration order above, which puts the
    provenance fields before the payload — an artefact a human opens should
    show what produced it in the first screenful.
    """
    path.write_text(
        json.dumps(envelope.to_mapping(), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> ArtefactEnvelope:
    """Read an envelope, raising on anything that lost its strategy key."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ArtefactError(f"{path} does not contain a JSON object")
    return ArtefactEnvelope.from_mapping(data)
