"""The Part A corpus: this repository's own text, and nothing else.

ADR-0005 / rule 2 in CLAUDE.md: nothing under `HWPM_DATA_DIR` may ever be
indexed. `_guard_not_data_dir` is not a formality — it is what a test asserts
against (`test_corpus_excludes_data_dir`), because "the corpus definition
would pull in patient data" is exactly the kind of bug that looks fine until
someone points `HWPM_DATA_DIR` at something inside the repo tree.
"""

from __future__ import annotations

import os
from pathlib import Path

from hwpm.retrieve.chunk import Chunk, chunk_file

REFERENCE_FILES = (
    "ALGORITHM-INDEX.md",
    "SELECTION-GUIDE.md",
    "essentials-of-metaheuristics.md",
)


class CorpusSafetyError(ValueError):
    """Raised when a would-be corpus file resolves inside HWPM_DATA_DIR."""


def _data_dir() -> Path | None:
    raw = os.environ.get("HWPM_DATA_DIR")
    if not raw:
        return None
    return Path(raw).resolve()


def _guard_not_data_dir(path: Path) -> None:
    data_dir = _data_dir()
    if data_dir is None:
        return
    resolved = path.resolve()
    if resolved == data_dir or data_dir in resolved.parents:
        raise CorpusSafetyError(
            f"refusing to index {resolved}: it is inside HWPM_DATA_DIR "
            f"({data_dir}) — ADR-0005 forbids patient data in the Part A corpus"
        )


def iter_corpus_files(repo_root: Path) -> list[Path]:
    """Every file Part A may index: `docs/**/*.md`, `src/hwpm/**/*.py`, and
    the three files under `refs/metaheuristics/`. Nothing under
    `HWPM_DATA_DIR` is ever included, guarded even if a caller passes a
    misconfigured root."""
    repo_root = repo_root.resolve()
    files: list[Path] = []

    docs_dir = repo_root / "docs"
    if docs_dir.is_dir():
        files.extend(sorted(docs_dir.rglob("*.md")))

    src_dir = repo_root / "src" / "hwpm"
    if src_dir.is_dir():
        files.extend(
            sorted(p for p in src_dir.rglob("*.py") if "__pycache__" not in p.parts)
        )

    ref_dir = repo_root / "refs" / "metaheuristics"
    for name in REFERENCE_FILES:
        candidate = ref_dir / name
        if candidate.is_file():
            files.append(candidate)

    for f in files:
        _guard_not_data_dir(f)
    return files


def _module_name(repo_root: Path, path: Path) -> str:
    rel = path.relative_to(repo_root / "src").with_suffix("")
    return ".".join(rel.parts)


def build_chunks(repo_root: Path) -> list[Chunk]:
    """Chunk the whole Part A corpus. Pure function of the files on disk —
    no network, no model call; safe to run with networking disabled
    (SPEC-007 criterion 20)."""
    repo_root = repo_root.resolve()
    chunks: list[Chunk] = []
    for path in iter_corpus_files(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        module_name = _module_name(repo_root, path) if path.suffix == ".py" else None
        chunks.extend(chunk_file(path, repo_relative=rel, module_name=module_name))
    return chunks
