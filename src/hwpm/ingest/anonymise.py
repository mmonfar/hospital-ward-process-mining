"""Pseudonymisation at the ingestion boundary. SPEC-001, node N03.

"Pseudonymisation salt committed to the repo" is SPEC-001's second named
failure mode. The salt is never a module constant and never a function
default — it always comes in as an explicit `bytes` argument from the
caller, sourced from `HWPM_DATA_DIR` (git-ignored, `.salt` / `*.salt` in
`.gitignore`), so there is nothing here for a pre-commit grep to catch
because there is nothing here to commit.

`pseudonymise` replaces `Event.subject` with a salted HMAC digest, keeping
the `ClinicianId` / `PatientId` subtype (so downstream code that branches on
type still works) but discarding the original value entirely — the digest
cannot be reversed without the salt, and the salt is never stored alongside
the data it protects.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Iterator
from dataclasses import replace

from hwpm.domain import Event, SubjectId

#: Hex digest characters kept from the HMAC. 16 hex chars = 64 bits, ample
#: to keep collisions negligible at the scale of one hospital's roster while
#: keeping ids short enough to stay readable in logs and CSV output.
_DIGEST_LENGTH = 16


def pseudonymise_id(subject: SubjectId, salt: bytes) -> SubjectId:
    """Salted HMAC-SHA256 of one subject id, keyed by `salt`.

    Deterministic for a given `salt`: the same source identifier always
    pseudonymises to the same output, which is what lets `Trajectory` and
    `MDTMoment` still group events by subject after this runs. Not
    deterministic across different salts, which is the point — two exports
    salted differently must not be joinable by id.
    """
    digest = hmac.new(salt, subject.value.encode("utf-8"), hashlib.sha256).hexdigest()
    return type(subject)(digest[:_DIGEST_LENGTH])


def pseudonymise(events: Iterable[Event], salt: bytes) -> Iterator[Event]:
    """Replace every event's subject with its salted pseudonym.

    Criterion 6: no pseudonymised output contains any source identifier.
    Only `subject` is touched — `source` names the *system* an event came
    from (e.g. "synthetic", "RTLS"), not a person, and `activity` /
    `location` carry no identifying value by construction (01-DOMAIN-MODEL.md:
    `Event` never carries a name).
    """
    for event in events:
        yield replace(event, subject=pseudonymise_id(event.subject, salt))
