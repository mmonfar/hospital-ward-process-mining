"""The one file in this codebase allowed to `import pm4py`. ADR-0008, SPEC-002.

`pm4py` is AGPL v3. ADR-0008 §1: "All pm4py imports live in exactly one file,
`src/hwpm/mining/_pm4py_adapter.py`, behind our own Protocols declared in
`hwpm.mining`. Nothing else in the codebase may `import pm4py`." Enforced by
the import-linter contract "pm4py confined to its adapter (AGPL -- ADR-0008)"
in `pyproject.toml` -- which, as part of this node, was extended to also cover
sibling files within `hwpm.mining` itself (see that contract's comment for
what a stray `import pm4py` next to this file used to get away with).

`Pm4pyProcessDiscovery` and `Pm4pyConformanceChecker` implement
`hwpm.mining.types.ProcessDiscovery` / `ConformanceChecker`. Every pm4py type
(`PetriNet`, `Marking`, the `pm4py.objects.log.obj.EventLog` class, the
DataFrame column-name conventions) stays inside this file's functions. The one
place a pm4py-shaped object survives past this module's boundary is
`ProcessModel.engine_payload` -- typed `object` in `hwpm.mining.types`, so
nothing outside this file is able to depend on its shape even though it is,
in fact, a `(PetriNet, Marking, Marking)` triple.

ADR-0008 §2: pm4py stays offline / batch, producing static artefacts -- this
module does no network I/O and nothing here is served.

Conformance method: token-based replay (`pm4py.fitness_token_based_replay`,
`precision_token_based_replay`, `conformance_diagnostics_token_based_replay`),
not alignments -- cheaper, and its per-trace diagnostics give a direct,
well-documented way to tell deviation from missing data (acceptance
criterion 6), which is the point:

- `transitions_with_problems` non-empty on a trace -> a transition had to be
  forced without the tokens the model says it needs, i.e. the trace did
  something the model disallows in some order it disallows. **Deviation.**
- Otherwise, if the trace does not reach the net's final marking -> nothing
  it did was wrong, it just never got there -- e.g. the case log stopped
  before the process completed. **Missing data.**
- Otherwise -> fully conformant.

(Verified empirically while building this adapter: a trace truncated after
one activity and a trace with a genuinely reordered activity both show
`missing_tokens == 1` and are both `trace_is_fit == False` -- those two raw
counts alone do not distinguish the two failure modes. `transitions_with_problems`
does: empty for the truncated trace, non-empty for the reordered one.)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pm4py
from pm4py.objects.petri_net.obj import Marking, PetriNet

from hwpm.mining.types import ConformanceReport, EventLog, ProcessModel, ReportHeader

#: `discover`'s `algorithm` parameter, SPEC-002: "process discovery via pm4py
#: (inductive miner; heuristics miner as comparator)".
SUPPORTED_ALGORITHMS = frozenset({"inductive", "heuristics"})

_CASE_COL = "case:concept:name"
_ACTIVITY_COL = "concept:name"
_TIME_COL = "time:timestamp"


def _to_dataframe(log: EventLog) -> pd.DataFrame:
    """`EventLog` -> the column convention every pm4py entry point in use
    here accepts directly, without going via pm4py's own `EventLog` class."""
    if not log.events:
        raise ValueError("cannot discover or check conformance against an empty EventLog")
    frame = pd.DataFrame(
        {
            _CASE_COL: [event.case_id for event in log.events],
            _ACTIVITY_COL: [event.activity for event in log.events],
            _TIME_COL: [event.timestamp for event in log.events],
        }
    )
    frame[_TIME_COL] = pd.to_datetime(frame[_TIME_COL])
    return frame


def _discover_net(
    algorithm: str, frame: pd.DataFrame
) -> tuple[PetriNet, Marking, Marking]:
    if algorithm == "inductive":
        return pm4py.discover_petri_net_inductive(frame)
    if algorithm == "heuristics":
        return pm4py.discover_petri_net_heuristics(frame)
    raise ValueError(
        f"unknown discovery algorithm {algorithm!r}; expected one of "
        f"{sorted(SUPPORTED_ALGORITHMS)}"
    )


class Pm4pyProcessDiscovery:
    """`hwpm.mining.types.ProcessDiscovery`, backed by pm4py."""

    def __init__(self, algorithm: str = "inductive") -> None:
        if algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"unknown discovery algorithm {algorithm!r}; expected one of "
                f"{sorted(SUPPORTED_ALGORITHMS)}"
            )
        self._algorithm = algorithm

    def discover(self, log: EventLog) -> ProcessModel:
        frame = _to_dataframe(log)
        net, initial_marking, final_marking = _discover_net(self._algorithm, frame)
        dfg, start_activities, end_activities = pm4py.discover_directly_follows_graph(
            frame
        )
        activities = frozenset(frame[_ACTIVITY_COL].unique())
        return ProcessModel(
            algorithm=self._algorithm,
            activities=activities,
            directly_follows=frozenset(dfg.keys()),
            start_activities=frozenset(start_activities.keys()),
            end_activities=frozenset(end_activities.keys()),
            engine_payload=(net, initial_marking, final_marking),
        )


class Pm4pyConformanceChecker:
    """`hwpm.mining.types.ConformanceChecker`, backed by pm4py token-based
    replay. See the module docstring for the deviation/missing-data split."""

    def check(self, log: EventLog, model: ProcessModel) -> ConformanceReport:
        payload = model.engine_payload
        if not (isinstance(payload, tuple) and len(payload) == 3):
            raise ValueError(
                "ProcessModel.engine_payload is not a pm4py (net, im, fm) triple -- "
                "was this model produced by Pm4pyProcessDiscovery?"
            )
        net, initial_marking, final_marking = payload
        frame = _to_dataframe(log)

        fitness = pm4py.fitness_token_based_replay(
            frame, net, initial_marking, final_marking
        )
        precision = pm4py.precision_token_based_replay(
            frame, net, initial_marking, final_marking
        )

        # Converting to pm4py's own EventLog here (rather than passing the
        # DataFrame straight to the diagnostics call) is deliberate: the
        # diagnostics list pm4py returns is positional, parallel to the log
        # it was given, and only the EventLog's traces carry the case id
        # (`trace.attributes['concept:name']`) needed to attribute each
        # diagnosis back to a case for `deviating_cases` / `missing_data_cases`.
        pm4py_log = pm4py.convert_to_event_log(frame)
        diagnostics = pm4py.conformance_diagnostics_token_based_replay(
            pm4py_log, net, initial_marking, final_marking
        )

        deviating: set[str] = set()
        missing_data: set[str] = set()
        for trace, diagnosis in zip(pm4py_log, diagnostics, strict=True):
            case_id = str(trace.attributes["concept:name"])
            if diagnosis["transitions_with_problems"]:
                deviating.add(case_id)
            elif not diagnosis["trace_is_fit"]:
                missing_data.add(case_id)

        header = ReportHeader(
            parameters={
                "algorithm": model.algorithm,
                "conformance_method": "token_based_replay",
                "case_count": len(log.case_ids()),
            },
            generated_at=datetime.now(UTC),
        )
        return ConformanceReport(
            fitness=float(fitness["log_fitness"]),
            precision=float(precision),
            deviating_cases=frozenset(deviating),
            missing_data_cases=frozenset(missing_data),
            header=header,
        )


__all__ = ["SUPPORTED_ALGORITHMS", "Pm4pyConformanceChecker", "Pm4pyProcessDiscovery"]
