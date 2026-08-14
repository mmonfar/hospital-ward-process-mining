"""Location-string to `LocationId` mapping. SPEC-001, node N03.

Real exports use free text for the same physical place ("bay 3", "Bay3",
"B3" — SPEC-001's modelling-assumptions table). `LocationMapper` normalises
what it can recognise and, when it cannot, quarantines the raw string rather
than guessing: "anything below 0.8 is quarantined, never silently guessed"
(SPEC-001) is acceptance criterion 5, and it is the single failure mode the
spec calls out as worst-case ("Silent location mis-mapping ... corrupts
every distance downstream while looking healthy").

A quarantined location is *not* dropped. It comes back as a `LocationId`
that carries the original raw string (prefixed so it is unmistakable) and a
confidence of 0.0, so it survives into the event stream and can be counted
by `hwpm.ingest.report.build_report` and reported on rather than vanishing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from hwpm.domain import LocationId

#: SPEC-001's own threshold: confidence strictly below this is quarantined.
QUARANTINE_THRESHOLD = 0.8

#: Prefix marking a `LocationId` as a quarantined raw string rather than a
#: resolved location, so callers (and `report.build_report`) can recognise
#: one without re-running the mapper.
QUARANTINE_PREFIX = "QUARANTINE:"

#: Matches the "bay 3" / "Bay3" / "B3" family SPEC-001 names explicitly: an
#: optional "bay" word (itself optionally abbreviated to a bare "b"),
#: optional whitespace, then a number.
_BAY_ALIAS_RE = re.compile(r"^\s*b(?:ay)?\s*0*([0-9]+)\s*$", re.IGNORECASE)


def bay_alias_key(raw: str) -> str | None:
    """Normalise a "bay 3" / "Bay3" / "B3" style string to a canonical key.

    Returns e.g. ``"bay3"`` for any of those three spellings, or `None` if
    `raw` does not match the pattern at all.
    """
    match = _BAY_ALIAS_RE.match(raw)
    if match is None:
        return None
    return f"bay{int(match.group(1))}"


@dataclass(frozen=True)
class LocationMapper:
    """Maps raw location strings from an export to a `(LocationId, confidence)`.

    Two lookup tiers:

    - `known`: exact raw strings (as they appear in the export) to their
      `LocationId`, confidence 1.0. This is how a clean feed — the
      synthetic generator's own `LocationId.value`, for instance — maps
      straight through.
    - `bay_aliases`: canonical bay keys (see `bay_alias_key`) to the
      `LocationId` they refer to, at `fuzzy_confidence`. This is the free-text
      tier SPEC-001 names.

    Anything matching neither is quarantined (criterion 5): never dropped,
    never guessed, always carrying its raw value forward.
    """

    known: Mapping[str, LocationId] = field(default_factory=dict)
    bay_aliases: Mapping[str, LocationId] = field(default_factory=dict)
    fuzzy_confidence: float = 0.85

    def __post_init__(self) -> None:
        if not 0.0 <= self.fuzzy_confidence <= 1.0:
            raise ValueError(
                f"fuzzy_confidence must be in [0, 1], got {self.fuzzy_confidence!r}"
            )

    def map(self, raw: str) -> tuple[LocationId, float]:
        exact = self.known.get(raw)
        if exact is not None:
            return exact, 1.0

        alias_key = bay_alias_key(raw)
        if alias_key is not None:
            aliased = self.bay_aliases.get(alias_key)
            if aliased is not None:
                return aliased, self.fuzzy_confidence

        return LocationId(f"{QUARANTINE_PREFIX}{raw}"), 0.0


def is_quarantined(location: LocationId) -> bool:
    """True if `location` is a mapper quarantine marker, not a resolved place."""
    return location.value.startswith(QUARANTINE_PREFIX)


def quarantined_raw_value(location: LocationId) -> str:
    """Recover the original raw string from a quarantined `LocationId`.

    Raises if `location` is not actually a quarantine marker — callers
    should check `is_quarantined` first, the same discipline the rest of
    the module uses to avoid silently treating a resolved location as
    unmapped or vice versa.
    """
    if not is_quarantined(location):
        raise ValueError(f"{location!r} is not a quarantined location")
    return location.value[len(QUARANTINE_PREFIX) :]
