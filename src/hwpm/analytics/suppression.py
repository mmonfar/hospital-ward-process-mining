"""The ADR-0005 aggregation floor, as enforced code. SPEC-003 criterion 7.

ADR-0005 rule 4: "no output cell derived from fewer than 5 patients or
attributable to a single named clinician. Enforced in the analytics layer as a
suppression rule with its own test, not left to reviewer vigilance."
ADR-0006 rule 5 removes the governance exemption anyone will eventually ask
for: "A governance department asking for clinician-level breakdown is asking
for a different tool, and the answer is no."

Two design choices worth stating, because both are the strict reading:

1. **Unattributed episodes are a refusal, not a pass.** A `BedsideEpisode`
   whose `patient` is `None` has not been joined to occupancy yet
   (`hwpm.mining.attach_patients`). We cannot count distinct patients we
   cannot see, and "I could not check" is not "it passed" for a disclosure
   control. So an unjoined episode set is refused with an explanation rather
   than waved through on a bed-count proxy.
2. **The floor is a refusal, not a redaction.** This module raises rather than
   returning a censored figure, because the caller here is `analyse`, which
   produces exactly one aggregate cell. A future cell-wise report (N18) can
   catch `SuppressionFloorError` per cell and suppress that cell; it must not
   be able to *lower* the floor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hwpm.domain import ClinicianId, PatientId, Round

#: ADR-0005 rule 4, verbatim: "fewer than 5 patients".
MIN_PATIENTS = 5

#: ADR-0005 rule 4, verbatim: "attributable to a single named clinician". Two
#: is the smallest number that is not one; it is a floor, not a target.
MIN_CLINICIANS = 2


class SuppressionFloorError(RuntimeError):
    """A figure was requested that ADR-0005 forbids publishing.

    Not a `ValueError`: the inputs are perfectly well formed. This is a
    disclosure-control refusal, and it should read like one at the call site.
    """


@dataclass(frozen=True)
class Cohort:
    """Who a figure is derived from. Carried into `MotionReport.params` so the
    floor that was applied travels with the number (ADR-0006 rule 2's habit,
    generalised)."""

    patients: frozenset[PatientId]
    clinicians: frozenset[ClinicianId]

    @property
    def n_patients(self) -> int:
        return len(self.patients)

    @property
    def n_clinicians(self) -> int:
        return len(self.clinicians)


def cohort_of(rounds: Sequence[Round]) -> Cohort:
    """The distinct patients and clinicians a set of rounds covers.

    Raises `SuppressionFloorError` if any episode is unattributed -- see the
    module docstring, choice 1.
    """
    patients: set[PatientId] = set()
    clinicians: set[ClinicianId] = set()
    unattributed = 0
    for round_ in rounds:
        clinicians.add(round_.clinician)
        for episode in round_.episodes:
            if episode.patient is None:
                unattributed += 1
            else:
                patients.add(episode.patient)
    if unattributed:
        raise SuppressionFloorError(
            f"{unattributed} episode(s) carry no patient attribution, so the "
            "ADR-0005 floor cannot be verified. Join occupancy first "
            "(hwpm.mining.attach_patients); an unverifiable floor is a refusal, "
            "not a pass"
        )
    return Cohort(patients=frozenset(patients), clinicians=frozenset(clinicians))


def enforce_suppression_floor(rounds: Sequence[Round]) -> Cohort:
    """Refuse to produce a figure ADR-0005 rule 4 forbids. Returns the cohort
    so the caller can record it in the report's provenance."""
    cohort = cohort_of(rounds)
    if cohort.n_patients < MIN_PATIENTS:
        raise SuppressionFloorError(
            f"derived from {cohort.n_patients} distinct patient(s); the "
            f"ADR-0005 aggregation floor is {MIN_PATIENTS}. Suppressed"
        )
    if cohort.n_clinicians < MIN_CLINICIANS:
        raise SuppressionFloorError(
            f"attributable to {cohort.n_clinicians} named clinician(s); "
            "ADR-0005 forbids any output cell attributable to a single named "
            "clinician, and ADR-0006 rule 5 allows no governance exemption. "
            "Suppressed"
        )
    return cohort


__all__ = [
    "MIN_CLINICIANS",
    "MIN_PATIENTS",
    "Cohort",
    "SuppressionFloorError",
    "cohort_of",
    "enforce_suppression_floor",
]
