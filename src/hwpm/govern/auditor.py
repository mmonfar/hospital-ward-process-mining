"""The automated code and method auditor. SPEC-006, node N15.

It answers SPEC-006's question -- *"is this project doing what it says it does,
and can every claim be traced?"* -- by reading the repository as text, never by
running the analysis. Five audits, matching SPEC-006's scope list:

1. **Spec conformance** (`audit_spec_conformance`) -- every `src/hwpm` module
   names an authorising spec in its module docstring, and that spec exists.
2. **Criteria coverage** (`audit_criteria_coverage`) -- every acceptance
   criterion in every spec names a test, and that test function exists.
3. **Method** (`audit_method`) -- stochastic modules take an explicit `rng` and
   are constrained by a determinism test; the baseline gate exists.
4. **Governance** (`audit_governance`) -- no code path reads `HWPM_DATA_DIR`,
   every aggregate output reaches the ADR-0005 suppression floor, no salt in
   the repository. Blocking.
5. **Audit-log integrity** (`audit_log_integrity`) -- `docs/AUDIT-LOG.md`
   accounts for every commit.

Three design choices worth stating, because each is the strict reading:

**Static, stdlib-only.** The auditor parses source with `ast` and reads markdown
and `git log`. It imports nothing from `hwpm.analytics`, `hwpm.mining` or
`hwpm.optimize`, so `hwpm govern` stays installable without the scientific
stack (the split in `pyproject.toml`), and so an auditor cannot be defeated by
the code it audits importing cleanly.

**Findings are reported, not silenced.** SPEC-006 criterion 6: findings go to
the audit log whether or not they are acted on. `ACKNOWLEDGED` exists so that a
finding whose answer is "yes, known, here is why" does not set the exit code --
but an acknowledged finding is still printed and still written to the log,
carrying its reason. A suppression list that hides findings would turn the audit
back into the to-do list criterion 6 exists to prevent.

**Reported ≠ defect.** A finding is a question for a reviewer, not a verdict.
`audit_governance` flagging a report type that never reaches the suppression
floor may be a genuine disclosure risk or may be a figure derived from no
patient at all; the auditor cannot tell, and says so rather than guessing.

SPEC-006 scope item 3, the provenance audit -- tracing every number in every
report back through the artefact chain to the events that produced it -- is
**not implemented here**. It has no acceptance criterion in SPEC-006, and doing
it properly means executing the pipeline and following values, not reading
source. Saying so is better than shipping a check that greps for the word
"provenance" and reports a coverage it does not have.

Not to be confused with `hwpm govern audit`, which appends one hand-written
entry about a change. This is the machine audit *of* the project's outputs and
method; its results are appended through the same log.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from hwpm.govern.audit import AuditEntry

# --- severities -------------------------------------------------------------

BLOCKING = "blocking"
ADVISORY = "advisory"

#: SPEC-006's open question, decided at N15: governance findings block, method
#: and conformance findings advise. Rationale: a governance failure exports
#: patient data or publishes a disclosive cell and cannot wait for triage; a
#: missing determinism test is a debt with a known owner. Recorded here and in
#: SPEC-006 rather than left open.
#:
#: Severity is assigned by each audit at the point of the finding, not applied
#: to a whole audit afterwards: `audit_governance` reports both a data *read*
#: (blocking) and a guard that merely names the variable (advisory), and
#: flattening the two would make the blocking count meaningless.
BLOCKING_AUDITS = frozenset({"governance"})

#: Findings whose answer is already known and recorded. Keyed by
#: `(audit, subject)`; the value is the reason, which is printed with the
#: finding and written to the audit log. Acknowledgement changes the exit code,
#: never the visibility.
ACKNOWLEDGED: Mapping[tuple[str, str], str] = {
    (
        "spec-conformance",
        "src/hwpm/govern/audit.py",
    ): "Governance tooling, authorised by brief item 4 and docs/04-AGENT-"
    "ORCHESTRATION.md rather than by a SPEC. A real ADR-0001 gap, recorded at "
    "N15 rather than closed by writing a retrospective spec.",
    (
        "spec-conformance",
        "src/hwpm/govern/graph.py",
    ): "As above: orchestration tooling, authorised by orchestration/graph.yaml "
    "and docs/04, not by a SPEC.",
    (
        "spec-conformance",
        "src/hwpm/govern/ledger.py",
    ): "As above: token governance, authorised by docs/04 section Token "
    "governance, not by a SPEC.",
    (
        "spec-conformance",
        "src/hwpm/govern/auditor.py",
    ): "This module. Authorised by SPEC-006, which it names in its docstring; "
    "listed here only if the docstring reference is ever removed.",
}

_SPEC_ID = re.compile(r"SPEC-\d{3}")
_TEST_NAME = re.compile(r"`(test_[A-Za-z0-9_]+)`")
_SHORT_HASH = re.compile(r"\b([0-9a-f]{7,40})\b")
_NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$")
_ACCEPTANCE_HEADING = re.compile(r"^(#+)\s+.*acceptance criteria\s*$", re.IGNORECASE)

#: Verification routes a spec may name instead of a test. Each is legitimate and
#: each means the criterion is not machine-checked, which is itself a finding --
#: it tells a reviewer exactly how much of the acceptance bar the test suite
#: actually holds.
_NON_TEST_ROUTES = (
    "manual review",
    "review checklist",
    "import-linter",
    "bench",
    "review",
)

#: Calls that move bytes off disk. Used to tell a path that is *read* from a
#: path that is only compared against, which is how the ADR-0005 guard in
#: `hwpm.retrieve.corpus` works. Deliberately excludes generic names like
#: `walk`, `load` and `loads`: `ast.walk` and `yaml.load` appear all over a
#: static analyser, and a check that fires on its own machinery is a check
#: whose output gets skimmed.
_READ_CALLS = frozenset(
    {
        "open",
        "read_text",
        "read_bytes",
        "read_csv",
        "read_parquet",
        "read_xes",
        "read_json",
        "glob",
        "rglob",
        "iterdir",
        "listdir",
        "scandir",
    }
)

#: How an environment variable is resolved. A `"HWPM_DATA_DIR"` string that is
#: not passed to one of these is a comparison or a message, not a lookup --
#: this module's own source is full of them.
_ENV_LOOKUPS = frozenset({"get", "getenv", "environ", "pop", "setdefault"})

#: A function returning one of these is an aggregate: it collapses many
#: patient-derived records into a figure that could be published.
_AGGREGATE_RETURN = re.compile(r"\b\w*(Report|Summary)\b")

#: The ADR-0005 floor, as called.
_SUPPRESSION_CALLS = frozenset({"enforce_suppression_floor", "cohort_of"})

_DETERMINISM_TEST = re.compile(r"determinis|reproducib|same_seed", re.IGNORECASE)


def _is_stochastic(tree: ast.Module) -> bool:
    """Whether a module has a random component, judged from the syntax tree.

    Not from the text: this module's own prose says `rng` repeatedly and draws
    nothing, and a text rule would have it demand a determinism test of the
    auditor. What counts is an actual `random` import, a `Random` reference, or
    an identifier named `rng` used as a value or a parameter.
    """
    names = {"rng", "Random"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "random" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "random":
            return True
        identifier = (
            node.id
            if isinstance(node, ast.Name)
            else node.attr
            if isinstance(node, ast.Attribute)
            else node.arg
            if isinstance(node, ast.arg)
            else None
        )
        if identifier in names:
            return True
    return False


@dataclass(frozen=True)
class Finding:
    """One question for a reviewer.

    `subject` is a repo-relative path or a spec/criterion id -- stable across
    runs, so the same finding is recognisable in successive audit-log entries.
    """

    audit: str
    subject: str
    detail: str
    severity: str = ADVISORY

    @property
    def acknowledgement(self) -> str | None:
        return ACKNOWLEDGED.get((self.audit, self.subject))

    @property
    def counts(self) -> bool:
        """Whether this finding sets the exit code."""
        return self.severity == BLOCKING and self.acknowledgement is None

    def render(self) -> str:
        line = f"  [{self.severity}] {self.subject}: {self.detail}"
        if self.acknowledgement:
            line += f"\n      acknowledged: {self.acknowledgement}"
        return line


@dataclass(frozen=True)
class AuditResult:
    """Findings plus what was examined.

    `checked` is not decoration. "No findings" and "the audit did not run
    because it found no files to read" print identically without it, and the
    second is the failure mode an auditor is least able to notice about itself.
    """

    findings: tuple[Finding, ...] = ()
    checked: Mapping[str, int] = field(default_factory=dict)

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.counts)

    def by_audit(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for finding in self.findings:
            out.setdefault(finding.audit, []).append(finding)
        return out


# --- shared helpers ---------------------------------------------------------


def _rel(path: Path, repo: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:  # pragma: no cover - only if called with a foreign path
        return path.as_posix()


def _python_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _is_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Protocol / abstract declaration: body is `...` or a docstring plus `...`."""
    body = [
        s
        for s in node.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    return not body


# --- audit 1: spec conformance ---------------------------------------------


def _has_definitions(tree: ast.Module) -> bool:
    """Whether a module declares anything a spec could authorise.

    A package `__init__.py` that only re-exports has no behaviour of its own;
    demanding a spec reference from it produces noise, not accountability.
    """
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            return True
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if not (isinstance(target, ast.Name) and target.id == "__all__"):
                return True
    return False


def audit_spec_conformance(repo: Path) -> tuple[list[Finding], int]:
    """SPEC-006 criterion 1: every `src/hwpm` module with no authorising spec.

    The authorising spec is the one named in the *module docstring*, not
    anywhere in the file: a `SPEC-003` mentioned inside a function body is a
    cross-reference, and treating it as authorisation would let any module
    authorise itself by citing someone else's spec in a comment.
    """
    findings: list[Finding] = []
    src = repo / "src" / "hwpm"
    specs_dir = repo / "docs" / "specs"
    files = _python_files(src)
    for path in files:
        tree = _parse(path)
        subject = _rel(path, repo)
        if tree is None:
            findings.append(
                Finding("spec-conformance", subject, "could not be parsed", BLOCKING)
            )
            continue
        if not _has_definitions(tree):
            continue
        doc = ast.get_docstring(tree) or ""
        named = sorted(set(_SPEC_ID.findall(doc)))
        if not named:
            elsewhere = sorted(set(_SPEC_ID.findall(path.read_text(encoding="utf-8"))))
            hint = (
                f" (cites {', '.join(elsewhere)} in the body, but not in the module "
                "docstring, so nothing states which spec authorises the module)"
                if elsewhere
                else ""
            )
            findings.append(
                Finding(
                    "spec-conformance",
                    subject,
                    f"no authorising spec named in the module docstring{hint}",
                )
            )
            continue
        for spec_id in named:
            if not list(specs_dir.glob(f"{spec_id}-*.md")):
                findings.append(
                    Finding(
                        "spec-conformance",
                        subject,
                        f"names {spec_id}, which does not exist in docs/specs/",
                        BLOCKING,
                    )
                )
    return findings, len(files)


# --- audit 2: acceptance criteria coverage ----------------------------------


@dataclass(frozen=True)
class Criterion:
    spec: str
    section: str
    number: int
    text: str

    @property
    def subject(self) -> str:
        return f"{self.spec} {self.section} {self.number}"


def parse_criteria(spec_path: Path) -> list[Criterion]:
    """Numbered items under any 'Acceptance criteria' heading.

    Multiple such sections are kept apart by their heading text -- SPEC-004 has
    both an original and an amended set, and merging them by number would let an
    amended criterion inherit the original's test.
    """
    spec_id_match = _SPEC_ID.search(spec_path.name)
    if spec_id_match is None:
        return []
    spec_id = spec_id_match.group(0)
    criteria: list[Criterion] = []
    section: str | None = None
    section_depth = 0
    pending: Criterion | None = None
    for raw in spec_path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^(#+)\s+(.*)$", raw)
        if heading:
            pending = None
            if _ACCEPTANCE_HEADING.match(raw):
                section = heading.group(2).strip()
                section_depth = len(heading.group(1))
            elif section is not None and len(heading.group(1)) <= section_depth:
                section = None
            continue
        if section is None:
            continue
        numbered = _NUMBERED.match(raw.strip())
        if numbered:
            pending = Criterion(
                spec_id, section, int(numbered.group(1)), numbered.group(2).strip()
            )
            criteria.append(pending)
        elif pending is not None and raw.strip():
            # Continuation line of a wrapped list item; the named test may sit
            # on it, so it belongs to the same criterion.
            criteria[-1] = Criterion(
                pending.spec,
                pending.section,
                pending.number,
                f"{pending.text} {raw.strip()}",
            )
            pending = criteria[-1]
        elif not raw.strip():
            pending = None
    return criteria


def _test_function_names(tests_root: Path) -> set[str]:
    names: set[str] = set()
    for path in _python_files(tests_root):
        tree = _parse(path)
        if tree is None:
            continue
        for func in _functions(tree):
            names.add(func.name)
    return names


def audit_criteria_coverage(repo: Path) -> tuple[list[Finding], int]:
    """SPEC-006 criterion 2: every acceptance criterion with no test.

    Three distinct findings, because they mean different things: a criterion
    that names no verification route at all; one that names a test which does
    not exist (the spec and the suite have drifted); and one verified by
    something other than a test, which is legitimate but means the suite does
    not hold that part of the bar.
    """
    findings: list[Finding] = []
    specs_dir = repo / "docs" / "specs"
    if not specs_dir.is_dir():
        return findings, 0
    known = _test_function_names(repo / "tests")
    total = 0
    for spec_path in sorted(specs_dir.glob("SPEC-*.md")):
        if "TEMPLATE" in spec_path.name:
            continue
        for criterion in parse_criteria(spec_path):
            total += 1
            named = _TEST_NAME.findall(criterion.text)
            lowered = criterion.text.lower()
            routes = [r for r in _NON_TEST_ROUTES if r in lowered]
            if not named and not routes:
                findings.append(
                    Finding(
                        "criteria-coverage",
                        criterion.subject,
                        "names no test and no other verification route: "
                        f"{_clip(criterion.text)}",
                    )
                )
                continue
            for test_name in named:
                if test_name in known:
                    continue
                near = sorted(n for n in known if n.startswith(test_name + "_"))
                if near:
                    findings.append(
                        Finding(
                            "criteria-coverage",
                            criterion.subject,
                            f"names `{test_name}`, which does not exist; the suite "
                            f"has {', '.join(f'`{n}`' for n in near[:3])} -- spec "
                            "wording and suite have drifted apart",
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            "criteria-coverage",
                            criterion.subject,
                            f"names `{test_name}`, which exists in no test module",
                        )
                    )
            if not named and routes:
                findings.append(
                    Finding(
                        "criteria-coverage",
                        criterion.subject,
                        f"verified by {routes[0]}, not by a test: the suite does "
                        "not hold this criterion",
                    )
                )
    return findings, total


def _clip(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


# --- audit 3: method --------------------------------------------------------


def _modules_imported_by(path: Path) -> set[str]:
    """Dotted `hwpm.*` names a test file mentions, imports included.

    Text, not imports alone: a test that reaches a module through a fixture or
    names it in a docstring is still evidence about that module, and missing
    that link would produce a false 'no determinism test' finding.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover
        return set()
    return set(re.findall(r"hwpm\.[A-Za-z_][A-Za-z0-9_.]*", text))


def _references_module(mentions: Iterable[str], text: str, dotted: str) -> bool:
    """Whether a test file is about the module `dotted`.

    Two routes, because `from hwpm.analytics import analyse` never spells
    `hwpm.analytics.motion` and a strict dotted match would report
    `tests/test_motion.py` as not existing:

    1. the file names the module (or something inside it) directly; or
    2. it names the module's package *and* uses the module's own stem as a
       word -- `hwpm.analytics` plus `motion`, `hwpm.optimize` plus `acs`.

    Route 2 is the loose one and is deliberately conjunctive: naming the
    package alone would let one determinism test vouch for every module beside
    it.
    """
    stem = dotted.rsplit(".", 1)[-1]
    package = dotted.rsplit(".", 1)[0]
    for mention in mentions:
        if mention == dotted or mention.startswith(dotted + "."):
            return True
    if any(m == package or m.startswith(package + ".") for m in mentions):
        return re.search(rf"\b{re.escape(stem)}\b", text) is not None
    return False


def audit_method(repo: Path) -> tuple[list[Finding], int]:
    """SPEC-006 scope item 2, the machine-checkable part.

    Closes the gap docs/06-QA-AND-DEADCODE.md names against gate 8: "a new
    stochastic module can be added with no determinism test at all and nothing
    will fail... Until the auditor (N15) can check that directly, this is a
    review obligation rather than an enforced one." It is checked directly here.

    What is *not* checked: whether the algorithm in the file is the one
    SELECTION-GUIDE.md specifies. That needs code read against intent and stays
    a reviewer's job, as SPEC-006 says.
    """
    findings: list[Finding] = []
    src = repo / "src" / "hwpm"
    tests_root = repo / "tests"
    coverage: list[tuple[set[str], str, bool]] = []
    for path in _python_files(tests_root):
        tree = _parse(path)
        has_determinism = tree is not None and any(
            _DETERMINISM_TEST.search(func.name) for func in _functions(tree)
        )
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            text = ""
        coverage.append((_modules_imported_by(path), text, has_determinism))

    stochastic = 0
    for path in _python_files(src):
        tree = _parse(path)
        if tree is None or not _is_stochastic(tree):
            continue
        stochastic += 1
        subject = _rel(path, repo)
        dotted = "hwpm." + subject[len("src/hwpm/") :].removesuffix(".py").replace(
            "/", "."
        )
        dotted = dotted.removesuffix(".__init__")

        # Module-level `random.*` use: the rule in CLAUDE.md is that everything
        # stochastic takes an explicit rng, so a bare `random.foo()` call is a
        # result nobody can re-derive.
        for func in _functions(tree):
            for child in ast.walk(func):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "random"
                ):
                    findings.append(
                        Finding(
                            "method",
                            f"{subject}:{child.lineno}",
                            f"`{func.name}` calls module-level "
                            f"`random.{child.func.attr}`; every stochastic "
                            "component takes an explicit rng",
                        )
                    )

        covered = any(
            has_determinism and _references_module(mentions, text, dotted)
            for mentions, text, has_determinism in coverage
        )
        if not covered:
            findings.append(
                Finding(
                    "method",
                    subject,
                    "is stochastic but no test module that references it holds a "
                    "determinism test (gate 8 has no single entry point; this is "
                    "the check docs/06 defers to N15)",
                )
            )

    gate = repo / "tests" / "bench" / "test_nsga2_vs_baselines.py"
    if not gate.is_file():
        findings.append(
            Finding(
                "method",
                "tests/bench/test_nsga2_vs_baselines.py",
                "gate 9 (baseline gate) has no test file; Rule 0 is unenforced",
            )
        )
    else:
        gate_tree = _parse(gate)
        if gate_tree is not None and not any(
            f.name.startswith("test_") for f in _functions(gate_tree)
        ):
            findings.append(
                Finding(
                    "method",
                    "tests/bench/test_nsga2_vs_baselines.py",
                    "gate 9 file exists but declares no test function",
                )
            )
    return findings, stochastic


# --- audit 4: governance ----------------------------------------------------


def _resolves_data_dir(node: ast.AST) -> int | None:
    """Line at which `node` looks up `HWPM_DATA_DIR` in the environment.

    A lookup, not a mention: `os.environ.get("HWPM_DATA_DIR")` and
    `os.environ["HWPM_DATA_DIR"]` count; a comparison against the literal, or
    the name inside an error message, does not. The narrower rule is what lets
    this module audit itself -- it names the variable a dozen times and reads
    nothing.

    The limitation is worth stating: a code path that hard-codes a data
    directory instead of resolving the variable is invisible here. SPEC-006
    criterion 3 is written in terms of `HWPM_DATA_DIR`, and inventing a wider
    rule would mean inventing what counts as a data path.
    """
    for child in ast.walk(node):
        if not (isinstance(child, ast.Constant) and child.value == "HWPM_DATA_DIR"):
            continue
        parents = _enclosing(node, child)
        for parent in parents:
            if isinstance(parent, ast.Call):
                func = parent.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else func.id
                    if isinstance(func, ast.Name)
                    else ""
                )
                if name in _ENV_LOOKUPS:
                    return child.lineno
            if isinstance(parent, ast.Subscript):
                return child.lineno
    return None


def _enclosing(root: ast.AST, target: ast.AST) -> list[ast.AST]:
    """The ancestors of `target` within `root`, innermost first."""
    stack: list[ast.AST] = []

    def walk(node: ast.AST) -> bool:
        if node is target:
            return True
        for child in ast.iter_child_nodes(node):
            if walk(child):
                stack.append(node)
                return True
        return False

    walk(root)
    return stack


def _data_dir_functions(
    tree: ast.Module,
) -> Iterator[tuple[ast.FunctionDef | ast.AsyncFunctionDef, int]]:
    for func in _functions(tree):
        lineno = _resolves_data_dir(func)
        if lineno is not None:
            yield func, lineno


def audit_governance(repo: Path) -> tuple[list[Finding], int]:
    """SPEC-006 criteria 3 and 4, plus the salt check from SPEC-001's failure
    modes. Blocking, per the open question decided at N15.

    The `HWPM_DATA_DIR` check distinguishes a *read* from a *guard*. The only
    sanctioned reference in the tree, `hwpm.retrieve.corpus`, resolves the
    variable in order to refuse anything inside it; reporting that as a data
    read would train a reader to ignore this audit. A reference whose function
    also opens, globs or parses a file is reported blocking; a reference that
    only compares paths is reported as an advisory note, so the reference count
    is never silently zero.
    """
    findings: list[Finding] = []
    examined = 0
    for root in (repo / "src", repo / "tools"):
        for path in _python_files(root):
            tree = _parse(path)
            if tree is None:
                continue
            subject = _rel(path, repo)
            for func, lineno in _data_dir_functions(tree):
                examined += 1
                calls = _called_names(func)
                reads = sorted(calls & _READ_CALLS)
                if reads:
                    findings.append(
                        Finding(
                            "governance",
                            f"{subject}:{lineno}",
                            f"`{func.name}` resolves HWPM_DATA_DIR and calls "
                            f"{', '.join(f'`{c}`' for c in reads)} -- a code path "
                            "that can read patient-identifiable data (ADR-0005)",
                            BLOCKING,
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            "governance",
                            f"{subject}:{lineno}",
                            f"`{func.name}` references HWPM_DATA_DIR but performs "
                            "no read call; reads as a guard, reported so the "
                            "reference is visible rather than assumed benign",
                        )
                    )

    findings.extend(_audit_suppression(repo))
    findings.extend(_audit_salt(repo))
    return findings, examined


def _audit_suppression(repo: Path) -> list[Finding]:
    """SPEC-006 criterion 4: aggregate outputs with no suppression check.

    An aggregate is a function whose return annotation names a `*Report` or
    `*Summary` type -- the shapes this project publishes. It is satisfied if the
    function, or any function it calls within its own module, reaches
    `enforce_suppression_floor` or `cohort_of`. Cross-module call graphs are not
    followed: an aggregate that delegates its disclosure control to another
    package is exactly the arrangement worth a reviewer's eyes.
    """
    findings: list[Finding] = []
    for path in _python_files(repo / "src" / "hwpm"):
        if path.name == "suppression.py":
            continue  # the floor itself
        tree = _parse(path)
        if tree is None:
            continue
        local: dict[str, set[str]] = {}
        for func in _functions(tree):
            local[func.name] = _called_names(func)
        for func in _functions(tree):
            if func.returns is None or _is_stub(func):
                continue
            returns = ast.unparse(func.returns)
            if not _AGGREGATE_RETURN.search(returns):
                continue
            if _reaches_suppression(func.name, local):
                continue
            findings.append(
                Finding(
                    "governance",
                    f"{_rel(path, repo)}:{func.lineno}",
                    f"`{func.name}` returns `{returns}` -- an aggregate output -- "
                    "and neither it nor any function it calls in this module "
                    "applies the ADR-0005 suppression floor",
                    BLOCKING,
                )
            )
    return findings


def _reaches_suppression(
    name: str, local: Mapping[str, set[str]], seen: frozenset[str] = frozenset()
) -> bool:
    if name in seen:
        return False
    calls = local.get(name, set())
    if calls & _SUPPRESSION_CALLS:
        return True
    return any(
        _reaches_suppression(callee, local, seen | {name})
        for callee in calls
        if callee in local
    )


def _audit_salt(repo: Path) -> list[Finding]:
    """SPEC-001's named failure mode: the pseudonymisation salt in the repo."""
    findings: list[Finding] = []
    tracked = _git(repo, "ls-files")
    for line in tracked:
        name = line.strip()
        if name.endswith(".salt") or Path(name).name in {".salt", "salt.txt"}:
            findings.append(
                Finding(
                    "governance",
                    name,
                    "a salt file is tracked in the repository; ADR-0005 requires "
                    "it to live under HWPM_DATA_DIR",
                    BLOCKING,
                )
            )
    for path in _python_files(repo / "src" / "hwpm"):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and "SALT" in target.id.upper()
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str | bytes)
                    and node.value.value
                ):
                    findings.append(
                        Finding(
                            "governance",
                            f"{_rel(path, repo)}:{node.lineno}",
                            f"`{target.id}` is assigned a literal value in source; "
                            "a committed salt undoes pseudonymisation",
                            BLOCKING,
                        )
                    )
    return findings


# --- audit 5: audit-log integrity -------------------------------------------


def _git(repo: Path, *args: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return []
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def audit_log_integrity(repo: Path) -> tuple[list[Finding], int]:
    """SPEC-006 criterion 5: commits absent from the audit log.

    A commit is accounted for if its hash appears anywhere in
    `docs/AUDIT-LOG.md`, **or** if the log records its parent. The second rule
    is not a loophole: `hwpm govern audit` stamps the then-current HEAD, so an
    entry written immediately before its own commit necessarily records the
    parent. Without that rule every correctly logged commit would be reported,
    and an audit that cries wolf on all 28 commits is one nobody reads.
    """
    findings: list[Finding] = []
    log_path = repo / "docs" / "AUDIT-LOG.md"
    if not log_path.is_file():
        return [
            Finding(
                "audit-log",
                "docs/AUDIT-LOG.md",
                "the audit log does not exist",
                BLOCKING,
            )
        ], 0

    logged = set(_SHORT_HASH.findall(log_path.read_text(encoding="utf-8")))
    commits = [
        line.split(" ", 2)
        for line in _git(repo, "log", "--format=%H %P %s")
        if line.strip()
    ]
    for entry in commits:
        full = entry[0]
        parents = entry[1].split() if len(entry) > 1 else []
        subject = entry[2] if len(entry) > 2 else ""
        if _mentioned(full, logged) or any(_mentioned(p, logged) for p in parents):
            continue
        findings.append(
            Finding(
                "audit-log",
                full[:7],
                f"not accounted for in docs/AUDIT-LOG.md: {_clip(subject, 70)}",
            )
        )
    return findings, len(commits)


def _mentioned(full_hash: str, logged: Iterable[str]) -> bool:
    return any(full_hash.startswith(h) for h in logged if len(h) >= 7)


# --- orchestration ----------------------------------------------------------

_AUDITS = (
    ("spec-conformance", audit_spec_conformance),
    ("criteria-coverage", audit_criteria_coverage),
    ("method", audit_method),
    ("governance", audit_governance),
    ("audit-log", audit_log_integrity),
)


#: The audit names, in run order. Public so the CLI can offer them as choices
#: without reaching into a private tuple.
AUDIT_NAMES = tuple(name for name, _ in _AUDITS)


def run(repo: Path, *, only: Sequence[str] | None = None) -> AuditResult:
    """Run every audit (or the named subset) and collect the findings."""
    findings: list[Finding] = []
    checked: dict[str, int] = {}
    for name, fn in _AUDITS:
        if only and name not in only:
            continue
        found, n = fn(repo)
        findings.extend(found)
        checked[name] = n
    return AuditResult(tuple(findings), checked)


_HEADLINE = {
    "spec-conformance": "modules examined",
    "criteria-coverage": "acceptance criteria examined",
    "method": "stochastic modules examined",
    "governance": "HWPM_DATA_DIR references examined",
    "audit-log": "commits examined",
}


def render(result: AuditResult) -> str:
    """Human-readable report. What was examined comes first, so a run that
    examined nothing cannot be mistaken for a clean one."""
    lines = ["Automated audit (SPEC-006, node N15)", ""]
    for name, count in result.checked.items():
        lines.append(f"  {count:>4}  {_HEADLINE.get(name, name)}")
    lines.append("")
    grouped = result.by_audit()
    for name, _ in _AUDITS:
        if name not in result.checked:
            continue
        found = grouped.get(name, [])
        if not found:
            lines.append(f"{name}: no findings")
            continue
        lines.append(f"{name}: {len(found)} finding(s)")
        for finding in found:
            lines.append(finding.render())
        lines.append("")
    blocking = result.blocking
    lines.append(
        f"{len(result.findings)} finding(s), {len(blocking)} blocking."
        if result.findings
        else "No findings."
    )
    lines.append(
        "Provenance audit (SPEC-006 scope item 3) is not mechanised: it has no "
        "acceptance criterion and needs the pipeline run, not the source read."
    )
    return "\n".join(lines)


def to_entry(result: AuditResult, *, model: str | None = None) -> AuditEntry:
    """SPEC-006 criterion 6: the findings, as an audit-log entry.

    Written whether or not anything is acted on. The evidence field carries the
    full finding list rather than a count, because a count is not traceable and
    tracing is the point.
    """
    blocking = len(result.blocking)
    summary = (
        f"Automated audit: {len(result.findings)} finding(s), {blocking} blocking"
        if result.findings
        else "Automated audit: no findings"
    )
    return AuditEntry(
        action=summary,
        why=(
            "SPEC-006 criterion 6: findings are written to the audit log whether "
            "or not they are acted on. A tool that records only what got fixed "
            "is a to-do list."
        ),
        authority="SPEC-006",
        node="N15-auditor",
        model=model,
        artefacts=("src/hwpm/govern/auditor.py",),
        evidence=render(result),
    )


__all__ = [
    "ACKNOWLEDGED",
    "ADVISORY",
    "AUDIT_NAMES",
    "BLOCKING",
    "AuditResult",
    "Criterion",
    "Finding",
    "audit_criteria_coverage",
    "audit_governance",
    "audit_log_integrity",
    "audit_method",
    "audit_spec_conformance",
    "parse_criteria",
    "render",
    "run",
    "to_entry",
]
