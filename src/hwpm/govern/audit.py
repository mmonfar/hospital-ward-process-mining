"""Append-only audit log.

Requirement 4 of the brief: keep a log of what was done and why, for audit
purposes. The value of such a log is entirely in its being trustworthy, so the
writer only ever appends — there is no update or delete path, and adding one
would defeat the purpose.

Entries are markdown so the log stays readable without tooling, with a stable
field order so `git diff` on it is meaningful.
"""

from __future__ import annotations

import getpass
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

HEADER = """# Audit log

Append-only record of every change to this repository: what was done, why, which
spec or ADR authorised it, and which model made it. Written by
`src/hwpm/govern/audit.py`. Do not edit past entries — corrections are appended
as new entries referencing the original.

---
"""


@dataclass(frozen=True)
class AuditEntry:
    action: str
    why: str
    authority: str  # SPEC / ADR / graph node id that authorises this
    node: str | None = None
    model: str | None = None
    artefacts: tuple[str, ...] = ()
    evidence: str | None = None  # command output, test result, measurement

    def render(self, *, when: datetime, actor: str, commit: str | None) -> str:
        lines = [
            f"## {when.strftime('%Y-%m-%d %H:%M:%SZ')} — {self.action}",
            "",
            f"- **Why:** {self.why}",
            f"- **Authority:** {self.authority}",
        ]
        if self.node:
            lines.append(f"- **Graph node:** {self.node}")
        if self.model:
            lines.append(f"- **Model:** {self.model}")
        lines.append(f"- **Actor:** {actor}")
        if commit:
            lines.append(f"- **Commit:** `{commit}`")
        if self.artefacts:
            lines.append(
                f"- **Artefacts:** {', '.join(f'`{a}`' for a in self.artefacts)}"
            )
        if self.evidence:
            lines.append("- **Evidence:**")
            lines.append("")
            lines.append("  ```")
            for line in self.evidence.strip().splitlines():
                lines.append(f"  {line}")
            lines.append("  ```")
        lines.append("")
        return "\n".join(lines)


def _current_commit(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def append(log_path: Path, entry: AuditEntry, *, repo: Path | None = None) -> None:
    """Append one entry. Creates the log with its header if absent."""
    repo = repo or log_path.parent.parent
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text(HEADER, encoding="utf-8")

    try:
        actor = getpass.getuser()
    except Exception:  # pragma: no cover - getuser can fail on odd environments
        actor = "unknown"

    rendered = entry.render(
        when=datetime.now(UTC),
        actor=actor,
        commit=_current_commit(repo),
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + rendered)
