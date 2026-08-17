"""The automated auditor. SPEC-006 acceptance criteria 1-6, node N15-auditor.

Every test builds a small synthetic repository under `tmp_path` rather than
asserting against this one. Two reasons, and the second is the important one:

1. Findings about the real tree change with every commit, so a test asserting
   them would fail for reasons unrelated to the auditor.
2. An auditor tested only against a tree it reports clean has been tested for
   silence. Each criterion here is checked with a *positive* case that must be
   found and a negative control that must not be, because the failure mode of a
   static check is a false clean, and a false clean is what an auditor exists to
   prevent.

Nothing here reads `HWPM_DATA_DIR` (ADR-0005). The strings that name it are
fixtures written by these tests, and the variable is never resolved.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hwpm.govern import auditor

# --- fixtures ---------------------------------------------------------------


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal, conforming repository: one module with an authorising spec,
    one spec whose single criterion names a test, and that test."""
    _write(
        tmp_path / "src" / "hwpm" / "widget.py",
        '"""Widgets. SPEC-001 criterion 1."""\n\n\ndef widget() -> int:\n    return 1\n',
    )
    _write(
        tmp_path / "docs" / "specs" / "SPEC-001-widgets.md",
        "# SPEC-001\n\n## Acceptance criteria\n\n1. Widgets widget. — `test_widget`\n",
    )
    _write(
        tmp_path / "tests" / "test_widget.py",
        "def test_widget() -> None:\n    assert True\n",
    )
    _write(
        tmp_path / "tests" / "bench" / "test_nsga2_vs_baselines.py",
        "def test_nsga2_beats_the_baselines() -> None:\n    assert True\n",
    )
    _write(tmp_path / "docs" / "AUDIT-LOG.md", "# Audit log\n")
    return tmp_path


def _subjects(findings: list[auditor.Finding]) -> str:
    return "\n".join(f"{f.subject}: {f.detail}" for f in findings)


# --- criterion 1: modules with no authorising spec --------------------------


def test_reports_module_with_no_authorising_spec(repo: Path) -> None:
    """SPEC-006 criterion 1."""
    _write(
        repo / "src" / "hwpm" / "orphan.py",
        '"""No spec names this."""\n\n\ndef orphan() -> int:\n    return 0\n',
    )
    findings, examined = auditor.audit_spec_conformance(repo)
    assert examined == 2
    assert [f.subject for f in findings] == ["src/hwpm/orphan.py"]


def test_a_body_reference_is_not_authorisation(repo: Path) -> None:
    """A spec id in a function body is a cross-reference. If it authorised the
    module, any module could authorise itself by citing someone else's spec."""
    _write(
        repo / "src" / "hwpm" / "sneaky.py",
        '"""Nothing here."""\n\n\ndef sneaky() -> str:\n    return "see SPEC-001"\n',
    )
    findings, _ = auditor.audit_spec_conformance(repo)
    assert [f.subject for f in findings] == ["src/hwpm/sneaky.py"]
    assert "cites SPEC-001 in the body" in findings[0].detail


def test_a_module_naming_a_spec_that_does_not_exist_is_blocking(repo: Path) -> None:
    _write(
        repo / "src" / "hwpm" / "ghost.py",
        '"""Authorised by SPEC-099."""\n\n\ndef ghost() -> int:\n    return 0\n',
    )
    findings, _ = auditor.audit_spec_conformance(repo)
    assert [(f.subject, f.severity) for f in findings] == [
        ("src/hwpm/ghost.py", auditor.BLOCKING)
    ]


def test_a_pure_reexport_module_needs_no_spec(repo: Path) -> None:
    """`__init__.py` that only re-exports declares no behaviour to authorise;
    demanding a spec of it produces noise, not accountability."""
    _write(
        repo / "src" / "hwpm" / "__init__.py",
        '"""Package."""\n\nfrom hwpm.widget import widget\n\n__all__ = ["widget"]\n',
    )
    findings, _ = auditor.audit_spec_conformance(repo)
    assert findings == []


# --- criterion 2: acceptance criteria with no test --------------------------


def test_reports_criterion_with_no_test(repo: Path) -> None:
    """SPEC-006 criterion 2, in its three distinct forms: a criterion naming no
    verification at all, one naming a test that does not exist, and one whose
    named test has drifted from the suite's actual name."""
    _write(
        repo / "docs" / "specs" / "SPEC-002-mining.md",
        "# SPEC-002\n\n"
        "## Acceptance criteria\n\n"
        "1. Mining mines.\n"
        "2. Mining stops. — `test_absent`\n"
        "3. Mining resumes. — `test_widget`\n"
        "4. Mining is layered. — import-linter, gate 6\n"
        "5. Mining drifts. — `test_drift`\n",
    )
    _write(
        repo / "tests" / "test_drifted.py",
        "def test_drift_but_renamed() -> None:\n    assert True\n",
    )
    findings, examined = auditor.audit_criteria_coverage(repo)
    assert examined == 6  # one from SPEC-001, five from SPEC-002
    by_subject = {f.subject: f.detail for f in findings}
    assert "SPEC-002 Acceptance criteria 1" in by_subject
    assert "names no test" in by_subject["SPEC-002 Acceptance criteria 1"]
    assert "exists in no test module" in by_subject["SPEC-002 Acceptance criteria 2"]
    assert "SPEC-002 Acceptance criteria 3" not in by_subject  # test_widget exists
    assert "import-linter" in by_subject["SPEC-002 Acceptance criteria 4"]
    assert "drifted apart" in by_subject["SPEC-002 Acceptance criteria 5"]


def test_two_criteria_sections_are_kept_apart(repo: Path) -> None:
    """SPEC-004 carries an original and an amended set. Merging them by number
    would let an amended criterion inherit the original's test."""
    _write(
        repo / "docs" / "specs" / "SPEC-004-optimisation.md",
        "# SPEC-004\n\n"
        "## Acceptance criteria\n\n"
        "1. The original. — `test_widget`\n\n"
        "### Amended acceptance criteria\n\n"
        "1. The amendment, unverified.\n",
    )
    findings, _ = auditor.audit_criteria_coverage(repo)
    subjects = [f.subject for f in findings]
    assert "SPEC-004 Amended acceptance criteria 1" in subjects
    assert "SPEC-004 Acceptance criteria 1" not in subjects


def test_a_wrapped_criterion_keeps_its_test(repo: Path) -> None:
    """The named test often sits on the continuation line of a wrapped list
    item; losing it would report a covered criterion as uncovered."""
    _write(
        repo / "docs" / "specs" / "SPEC-003-motion.md",
        "# SPEC-003\n\n## Acceptance criteria\n\n"
        "1. A criterion long enough that its author wrapped it across\n"
        "   two lines. — `test_widget`\n",
    )
    findings, _ = auditor.audit_criteria_coverage(repo)
    assert [f for f in findings if f.subject.startswith("SPEC-003")] == []


# --- criterion 3: code paths reading HWPM_DATA_DIR --------------------------


def test_reports_data_dir_read(repo: Path) -> None:
    """SPEC-006 criterion 3. Blocking: ADR-0005 findings do not wait for
    triage."""
    _write(
        repo / "src" / "hwpm" / "leak.py",
        '"""Leaky. SPEC-001."""\n\n'
        "import os\n"
        "from pathlib import Path\n\n\n"
        "def load() -> str:\n"
        '    root = Path(os.environ["HWPM_DATA_DIR"])\n'
        '    return (root / "events.csv").read_text()\n',
    )
    findings, examined = auditor.audit_governance(repo)
    leaks = [f for f in findings if f.subject.startswith("src/hwpm/leak.py")]
    assert examined == 1
    assert len(leaks) == 1
    assert leaks[0].severity == auditor.BLOCKING
    assert "read_text" in leaks[0].detail


def test_a_guard_is_reported_but_does_not_block(repo: Path) -> None:
    """`hwpm.retrieve.corpus` resolves the variable in order to *refuse*
    anything inside it. Reporting that as a data read would train a reader to
    ignore this audit; not reporting it at all would hide the reference."""
    _write(
        repo / "src" / "hwpm" / "guard.py",
        '"""Guard. SPEC-001."""\n\n'
        "import os\n"
        "from pathlib import Path\n\n\n"
        "def refuse(candidate: Path) -> None:\n"
        '    raw = os.environ.get("HWPM_DATA_DIR")\n'
        "    if raw and str(candidate).startswith(raw):\n"
        '        raise RuntimeError("refusing")\n',
    )
    findings, _ = auditor.audit_governance(repo)
    guards = [f for f in findings if f.subject.startswith("src/hwpm/guard.py")]
    assert len(guards) == 1
    assert guards[0].severity == auditor.ADVISORY
    assert not guards[0].counts


def test_naming_the_variable_without_resolving_it_is_not_a_reference(
    repo: Path,
) -> None:
    """This module's own source names `HWPM_DATA_DIR` a dozen times and reads
    nothing. A text rule would have the auditor report itself."""
    _write(
        repo / "src" / "hwpm" / "prose.py",
        '"""Prose. SPEC-001."""\n\n\n'
        "def explain() -> str:\n"
        '    return "never read HWPM_DATA_DIR"\n',
    )
    findings, examined = auditor.audit_governance(repo)
    assert examined == 0
    assert not [f for f in findings if f.subject.startswith("src/hwpm/prose.py")]


# --- criterion 4: aggregates with no suppression check ----------------------


def test_reports_aggregate_without_suppression(repo: Path) -> None:
    """SPEC-006 criterion 4."""
    _write(
        repo / "src" / "hwpm" / "analytics.py",
        '"""Analytics. SPEC-001."""\n\n\n'
        "class MotionReport:\n"
        "    pass\n\n\n"
        "def analyse(rounds: list[int]) -> MotionReport:\n"
        "    return MotionReport()\n",
    )
    findings, _ = auditor.audit_governance(repo)
    aggregates = [f for f in findings if "analytics.py" in f.subject]
    assert len(aggregates) == 1
    assert aggregates[0].severity == auditor.BLOCKING
    assert "suppression floor" in aggregates[0].detail


def test_suppression_reached_through_a_helper_satisfies_the_check(
    repo: Path,
) -> None:
    """The floor is usually applied one call down. A direct-call-only rule would
    report every correctly guarded aggregate."""
    _write(
        repo / "src" / "hwpm" / "analytics.py",
        '"""Analytics. SPEC-001."""\n\n'
        "from hwpm.analytics.suppression import enforce_suppression_floor\n\n\n"
        "class MotionReport:\n"
        "    pass\n\n\n"
        "def _prepare(rounds: list[int]) -> None:\n"
        "    enforce_suppression_floor(rounds)\n\n\n"
        "def analyse(rounds: list[int]) -> MotionReport:\n"
        "    _prepare(rounds)\n"
        "    return MotionReport()\n",
    )
    findings, _ = auditor.audit_governance(repo)
    assert not [f for f in findings if "analytics.py" in f.subject]


def test_a_protocol_stub_is_not_an_aggregate(repo: Path) -> None:
    """A `...` body publishes nothing; the implementation behind it is what the
    audit needs to reach."""
    _write(
        repo / "src" / "hwpm" / "types.py",
        '"""Types. SPEC-001."""\n\n'
        "from typing import Protocol\n\n\n"
        "class Report:\n"
        "    pass\n\n\n"
        "class Checker(Protocol):\n"
        "    def check(self) -> Report: ...\n",
    )
    findings, _ = auditor.audit_governance(repo)
    assert not [f for f in findings if "types.py" in f.subject]


def test_a_committed_salt_is_blocking(repo: Path) -> None:
    """SPEC-001's named failure mode: a committed salt undoes pseudonymisation."""
    _write(
        repo / "src" / "hwpm" / "anon.py",
        '"""Anon. SPEC-001."""\n\nPSEUDONYM_SALT = "hunter2"\n',
    )
    findings, _ = auditor.audit_governance(repo)
    salt = [f for f in findings if "anon.py" in f.subject]
    assert len(salt) == 1
    assert salt[0].severity == auditor.BLOCKING


# --- method audit (SPEC-006 scope item 2) -----------------------------------


def test_reports_a_stochastic_module_with_no_determinism_test(repo: Path) -> None:
    """The gap docs/06-QA-AND-DEADCODE.md leaves open against gate 8: a new
    stochastic module can be added with no determinism test and nothing fails.
    Checked directly here, which is what docs/06 defers to N15."""
    _write(
        repo / "src" / "hwpm" / "sampler.py",
        '"""Sampler. SPEC-001."""\n\n'
        "from random import Random\n\n\n"
        "def draw(rng: Random) -> int:\n"
        "    return rng.randrange(10)\n",
    )
    findings, examined = auditor.audit_method(repo)
    assert examined == 1
    assert [f.subject for f in findings if "sampler" in f.subject] == [
        "src/hwpm/sampler.py"
    ]


def test_a_determinism_test_beside_the_module_satisfies_gate_8(repo: Path) -> None:
    _write(
        repo / "src" / "hwpm" / "sampler.py",
        '"""Sampler. SPEC-001."""\n\n'
        "from random import Random\n\n\n"
        "def draw(rng: Random) -> int:\n"
        "    return rng.randrange(10)\n",
    )
    _write(
        repo / "tests" / "test_sampler.py",
        "from hwpm.sampler import draw\n\n\n"
        "def test_determinism() -> None:\n"
        "    assert draw is draw\n",
    )
    findings, _ = auditor.audit_method(repo)
    assert not [f for f in findings if "sampler" in f.subject]


def test_module_level_random_is_reported(repo: Path) -> None:
    """CLAUDE.md: everything stochastic takes an explicit `rng`. A bare
    `random.foo()` is a result nobody can re-derive."""
    _write(
        repo / "src" / "hwpm" / "sloppy.py",
        '"""Sloppy. SPEC-001."""\n\n'
        "import random\n\n\n"
        "def draw() -> int:\n"
        "    return random.randrange(10)\n",
    )
    findings, _ = auditor.audit_method(repo)
    assert any("module-level" in f.detail for f in findings)


def test_prose_about_rng_is_not_a_stochastic_module(repo: Path) -> None:
    """A text rule would have this auditor demand a determinism test of itself:
    its docstring says `rng` repeatedly and it draws nothing."""
    _write(
        repo / "src" / "hwpm" / "essay.py",
        '"""An essay about rng and Random(). SPEC-001."""\n\n\n'
        "def essay() -> str:\n"
        '    return "everything stochastic takes an explicit rng"\n',
    )
    _, examined = auditor.audit_method(repo)
    assert examined == 0


# --- criterion 5: commits absent from the audit log -------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(repo: Path) -> Path:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "auditor@example.invalid")
    _git(repo, "config", "user.name", "auditor")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    return repo


def test_reports_unlogged_commit(git_repo: Path) -> None:
    """SPEC-006 criterion 5. Unlogged commits are the signal that the
    self-managed loop has decayed."""
    findings, examined = auditor.audit_log_integrity(git_repo)
    assert examined == 1
    assert len(findings) == 1
    assert "not accounted for" in findings[0].detail


def test_a_logged_commit_is_accounted_for(git_repo: Path) -> None:
    head = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    log = git_repo / "docs" / "AUDIT-LOG.md"
    log.write_text(f"# Audit log\n\n- **Commit:** `{head[:7]}`\n", encoding="utf-8")
    findings, _ = auditor.audit_log_integrity(git_repo)
    assert findings == []


def test_a_commit_whose_parent_is_logged_is_accounted_for(git_repo: Path) -> None:
    """`hwpm govern audit` stamps the then-current HEAD, so an entry written
    immediately before its own commit necessarily records the parent. Without
    this rule every correctly logged commit would be reported."""
    head = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    log = git_repo / "docs" / "AUDIT-LOG.md"
    log.write_text(f"# Audit log\n\n- **Commit:** `{head[:7]}`\n", encoding="utf-8")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "second, logged against its parent")
    findings, _ = auditor.audit_log_integrity(git_repo)
    assert findings == []


def test_a_missing_audit_log_is_blocking(repo: Path) -> None:
    (repo / "docs" / "AUDIT-LOG.md").unlink()
    findings, _ = auditor.audit_log_integrity(repo)
    assert [f.severity for f in findings] == [auditor.BLOCKING]


# --- criterion 6: findings reach the audit log ------------------------------


def test_findings_written_to_audit_log(repo: Path, tmp_path: Path) -> None:
    """SPEC-006 criterion 6 — the one that makes it an audit. A tool that
    records only what got fixed is a to-do list."""
    from hwpm.govern import audit

    _write(
        repo / "src" / "hwpm" / "orphan.py",
        '"""No spec names this."""\n\n\ndef orphan() -> int:\n    return 0\n',
    )
    result = auditor.run(repo)
    entry = auditor.to_entry(result, model="claude-opus-5")
    log = tmp_path / "out" / "AUDIT-LOG.md"
    audit.append(log, entry, repo=repo)

    written = log.read_text(encoding="utf-8")
    assert "SPEC-006" in written
    assert "src/hwpm/orphan.py" in written, "the finding itself must be in the log"
    assert entry.node == "N15-auditor"
    assert "finding(s)" in entry.action


def test_the_report_says_what_was_examined(repo: Path) -> None:
    """A clean run and a run that read nothing print identically without the
    counts, and the second is the failure an auditor is least able to notice
    about itself."""
    result = auditor.run(repo)
    rendered = auditor.render(result)
    assert "modules examined" in rendered
    assert "acceptance criteria examined" in rendered
    assert result.checked["spec-conformance"] == 1


def test_an_acknowledged_finding_is_still_reported() -> None:
    """Acknowledgement changes the exit code, never the visibility. A list that
    hid findings would turn the audit back into the to-do list criterion 6
    exists to prevent."""
    finding = auditor.Finding(
        "spec-conformance",
        "src/hwpm/govern/audit.py",
        "no authorising spec named in the module docstring",
        auditor.BLOCKING,
    )
    assert finding.acknowledgement is not None
    assert not finding.counts
    assert "acknowledged:" in finding.render()
    result = auditor.AuditResult((finding,), {"spec-conformance": 1})
    assert finding.subject in auditor.render(result)
    assert result.blocking == ()


def test_only_runs_the_named_audits(repo: Path) -> None:
    result = auditor.run(repo, only=["governance"])
    assert set(result.checked) == {"governance"}


def test_clean_repository_produces_no_findings(repo: Path) -> None:
    """The negative control for the whole suite: the conforming fixture must
    come back clean, or every positive above proves nothing."""
    result = auditor.run(repo)
    assert result.findings == (), _subjects(list(result.findings))
