"""Token ledger — measures what agent sessions actually cost.

The brief asked to monitor remaining tokens "so we do not just burn in a chunk".
This reads the Claude Code session transcripts and reports real spend against the
budget declared in `orchestration/graph.yaml`.

Transcript format (verified against a live session on 2026-08-14): each line is a
JSON object; assistant lines carry `message.usage` with `input_tokens`,
`output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`.

A note on what "tokens used" means here, because the naive sum is misleading.
Cache reads are billed at a fraction of fresh input, and every turn re-reads the
whole conversation from cache, so summing `cache_read_input_tokens` across turns
counts the same context dozens of times. We therefore report:

  billable  - a cost-weighted figure, the honest "what did this cost" number
  fresh     - input + cache_creation, i.e. context that was genuinely new
  output    - generated tokens

and drive budget thresholds off `billable`. Summing raw totals would trip a
"budget exhausted" warning within a few turns of any long session and train
everyone to ignore it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Cost weights relative to a fresh input token. These mirror Anthropic's public
# multipliers: cache writes cost more than fresh input, cache reads much less.
# They are ratios, not prices — the ledger reports weighted tokens, not currency,
# so it stays correct when prices change.
WEIGHT_FRESH_INPUT = 1.0
WEIGHT_CACHE_WRITE = 1.25
WEIGHT_CACHE_READ = 0.1
WEIGHT_OUTPUT = 5.0


@dataclass
class ModelUsage:
    """Accumulated usage for one model within one session."""

    model: str
    turns: int = 0
    fresh_input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0

    @property
    def raw_total(self) -> int:
        return self.fresh_input + self.cache_write + self.cache_read + self.output

    @property
    def billable(self) -> float:
        """Cost-weighted token count. This is what budgets are measured against."""
        return (
            self.fresh_input * WEIGHT_FRESH_INPUT
            + self.cache_write * WEIGHT_CACHE_WRITE
            + self.cache_read * WEIGHT_CACHE_READ
            + self.output * WEIGHT_OUTPUT
        )

    def add(self, usage: dict) -> None:
        self.turns += 1
        self.fresh_input += usage.get("input_tokens", 0) or 0
        self.cache_write += usage.get("cache_creation_input_tokens", 0) or 0
        self.cache_read += usage.get("cache_read_input_tokens", 0) or 0
        self.output += usage.get("output_tokens", 0) or 0


@dataclass
class SessionUsage:
    session_id: str
    path: Path
    by_model: dict[str, ModelUsage] = field(default_factory=dict)

    @property
    def billable(self) -> float:
        return sum(m.billable for m in self.by_model.values())

    @property
    def output(self) -> int:
        return sum(m.output for m in self.by_model.values())

    @property
    def turns(self) -> int:
        return sum(m.turns for m in self.by_model.values())


def read_session(path: Path) -> SessionUsage:
    """Parse one transcript. Malformed lines are skipped, not fatal.

    A transcript being written by a live session can have a torn final line, and
    a ledger that crashes on the session it is reporting on is useless.
    """
    session = SessionUsage(session_id=path.stem, path=path)
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            model = message.get("model") or "unknown"
            session.by_model.setdefault(model, ModelUsage(model)).add(usage)
    return session


def read_project(transcript_dir: Path) -> list[SessionUsage]:
    """All sessions for a project, newest transcript last."""
    paths = sorted(transcript_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return [read_session(p) for p in paths]


def default_transcript_dir(project_dir: Path) -> Path:
    """Locate Claude Code's transcript directory for a project.

    Claude Code slugifies the absolute project path: separators and spaces become
    hyphens. `C:\\Users\\x\\My Proj` -> `C--Users-x-My-Proj`.
    """
    slug = str(project_dir.resolve())
    for char in ("\\", "/", " ", ":", "_", "."):
        slug = slug.replace(char, "-")
    return Path.home() / ".claude" / "projects" / slug


@dataclass
class BudgetStatus:
    billable: float
    soft_limit: int
    hard_limit: int
    action: str
    note: str

    @property
    def fraction(self) -> float:
        return self.billable / self.hard_limit if self.hard_limit else 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.hard_limit - self.billable)


def assess(billable: float, budget: dict) -> BudgetStatus:
    """Compare spend against the thresholds declared in graph.yaml."""
    soft = int(budget.get("session_soft_limit_tokens", 0))
    hard = int(budget.get("session_hard_limit_tokens", 0)) or 1
    fraction = billable / hard

    action, note = "ok", "Within budget."
    # Thresholds are ascending; the last one crossed wins.
    for threshold in sorted(
        budget.get("thresholds", []), key=lambda t: t.get("at_fraction", 0)
    ):
        if fraction >= float(threshold.get("at_fraction", 1.0)):
            action = threshold.get("action", "warn")
            note = threshold.get("note", "").strip()
    return BudgetStatus(billable, soft, hard, action, note)
