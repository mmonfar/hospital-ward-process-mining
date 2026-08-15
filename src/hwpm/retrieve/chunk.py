"""Chunking: markdown, Python, the audit log, and the page-anchored reference.

Chunk boundaries are a modelling choice (SPEC-007 "Chunking"), not an
implementation detail: they determine what a citation can point at.

1. Markdown splits on heading boundaries first, then ~400-token windows with
   60-token overlap within an over-long section, never splitting a line.
2. `essentials-of-metaheuristics.md` chunks never cross a `<!-- page N -->`
   anchor — page numbers are the citation currency of SELECTION-GUIDE.md and
   SPEC-004.
3. Python chunks at module/class/function granularity: signature plus
   docstring, never the body.
4. `docs/AUDIT-LOG.md` chunks one-per-entry.

`Chunk.text` is always the verbatim source slice `path:start_line-end_line` —
nothing is invented or paraphrased into it. `Chunk.heading_path` is a separate
field (the "what document and section is this" context) so that a consumer
choosing to prepend it before embedding does not corrupt the verbatim-text
guarantee that `Hit.citation()` depends on (SPEC-007 acceptance criterion 2).
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_TOKENS = 400
DEFAULT_OVERLAP = 60

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_PAGE_ANCHOR_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$")
_AUDIT_ENTRY_RE = re.compile(r"^##\s+(.*)$")


@dataclass(frozen=True)
class Chunk:
    """A retrievable passage with stable, verifiable provenance."""

    id: str  # f"{path}:{start_line}-{end_line}"
    path: str  # repo-relative, forward-slash separated
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    page_anchor: int | None  # set only for the parsed reference
    heading_path: str
    text: str
    sha256: str

    def citation(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_chunk(
    path: str,
    start_line: int,
    end_line: int,
    heading_path: str,
    text: str,
    page_anchor: int | None = None,
) -> Chunk:
    return Chunk(
        id=f"{path}:{start_line}-{end_line}",
        path=path,
        start_line=start_line,
        end_line=end_line,
        page_anchor=page_anchor,
        heading_path=heading_path,
        text=text,
        sha256=_sha256(text),
    )


def _window_lines(
    path: str,
    lines: list[str],
    heading: str,
    start_line: int,
    end_line: int,
    max_tokens: int,
    overlap: int,
    page_anchor: int | None = None,
) -> list[Chunk]:
    """Split ``lines[start_line-1:end_line]`` into token-bounded windows.

    Never splits a line in two: a window's boundary always falls between
    lines, which is what keeps ``Chunk.text`` a verbatim slice of the file.
    """
    seg = lines[start_line - 1 : end_line]
    if not seg:
        return []
    tok_counts = [len(line.split()) for line in seg]
    total = sum(tok_counts)
    if total <= max_tokens or len(seg) == 1:
        text = "\n".join(seg)
        return [_make_chunk(path, start_line, end_line, heading, text, page_anchor)]

    chunks: list[Chunk] = []
    n = len(seg)
    i = 0
    while i < n:
        acc = 0
        j = i
        while j < n and (acc == 0 or acc + tok_counts[j] <= max_tokens):
            acc += tok_counts[j]
            j += 1
        chunk_start = start_line + i
        chunk_end = start_line + j - 1
        text = "\n".join(seg[i:j])
        chunks.append(
            _make_chunk(path, chunk_start, chunk_end, heading, text, page_anchor)
        )
        if j >= n:
            break
        # Walk back up to `overlap` tokens' worth of lines for the next window,
        # always advancing by at least one line so the loop terminates.
        k = j
        back = 0
        while k > i + 1 and back < overlap:
            k -= 1
            back += tok_counts[k]
        i = k
    return chunks


def _heading_sections(lines: list[str], base_heading: str) -> list[tuple[str, int, int]]:
    """Split lines into (heading_path, start_line, end_line) sections."""
    sections: list[tuple[str, int, int]] = []
    stack: list[tuple[int, str]] = []
    section_start = 1
    section_heading = base_heading

    def current_heading() -> str:
        if not stack:
            return base_heading
        titles = [t for _, t in stack]
        return " > ".join([base_heading, *titles]) if base_heading else " > ".join(titles)

    for i, line in enumerate(lines, start=1):
        m = _HEADING_RE.match(line)
        if m:
            if i > section_start:
                sections.append((section_heading, section_start, i - 1))
            level = len(m.group(1))
            title = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            section_heading = current_heading()
            section_start = i
    sections.append((section_heading, section_start, len(lines)))
    return [s for s in sections if s[1] <= s[2]]


def chunk_markdown(
    path: str,
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap: int = DEFAULT_OVERLAP,
    heading_prefix: str = "",
    page_anchor: int | None = None,
) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[Chunk] = []
    for heading, start, end in _heading_sections(lines, heading_prefix):
        chunks.extend(
            _window_lines(
                path, lines, heading, start, end, max_tokens, overlap, page_anchor
            )
        )
    return chunks


def chunk_reference(
    path: str,
    text: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Chunk the parsed metaheuristics reference. Never crosses a page anchor."""
    lines = text.splitlines()
    if not lines:
        return []

    # Locate anchors: (line_index_1based, page_number)
    anchors: list[tuple[int, int]] = []
    for i, line in enumerate(lines, start=1):
        m = _PAGE_ANCHOR_RE.match(line.strip())
        if m:
            anchors.append((i, int(m.group(1))))

    chunks: list[Chunk] = []
    if not anchors:
        # No anchors at all — fall back to ordinary markdown chunking so the
        # function still degrades gracefully rather than silently emitting
        # nothing.
        return chunk_markdown(path, text, max_tokens=max_tokens, overlap=overlap)

    # Preamble before the first anchor (title page, front matter) — no page
    # number applies.
    if anchors[0][0] > 1:
        chunks.extend(
            chunk_markdown(
                path,
                "\n".join(lines[: anchors[0][0] - 1]),
                max_tokens=max_tokens,
                overlap=overlap,
                page_anchor=None,
            )
        )

    for idx, (anchor_line, page_no) in enumerate(anchors):
        seg_start = anchor_line
        seg_end = anchors[idx + 1][0] - 1 if idx + 1 < len(anchors) else len(lines)
        if seg_end < seg_start:
            continue
        seg_lines = lines[seg_start - 1 : seg_end]
        for heading, start_off, end_off in _heading_sections(
            seg_lines, f"page {page_no}"
        ):
            chunks.extend(
                _window_lines(
                    path,
                    lines,
                    heading,
                    seg_start + start_off - 1,
                    seg_start + end_off - 1,
                    max_tokens,
                    overlap,
                    page_anchor=page_no,
                )
            )
    return chunks


def chunk_audit_log(path: str, text: str) -> list[Chunk]:
    """One chunk per `## `-headed audit entry. No further windowing:
    entries are short by construction (SPEC-006 audit 5)."""
    lines = text.splitlines()
    if not lines:
        return []
    entries: list[tuple[str, int, int]] = []
    cur_title = "preamble"
    cur_start = 1
    for i, line in enumerate(lines, start=1):
        m = _AUDIT_ENTRY_RE.match(line)
        if m:
            if i > cur_start:
                entries.append((cur_title, cur_start, i - 1))
            cur_title = m.group(1).strip()
            cur_start = i
    entries.append((cur_title, cur_start, len(lines)))
    chunks = []
    for title, start, end in entries:
        if start > end:
            continue
        seg = lines[start - 1 : end]
        chunks.append(_make_chunk(path, start, end, title, "\n".join(seg)))
    return chunks


def _member_range(node: ast.AST) -> tuple[int, int]:
    start = node.lineno  # type: ignore[attr-defined]
    decorators = getattr(node, "decorator_list", None)
    if decorators:
        start = min(start, min(d.lineno for d in decorators))
    body = getattr(node, "body", None)
    if not body:
        return start, start
    first = body[0]
    is_docstring = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    fallback = max(start, first.lineno - 1)
    end = (first.end_lineno if is_docstring else None) or fallback
    return start, end


def chunk_python(path: str, text: str, module_name: str) -> list[Chunk]:
    """Module/class/function granularity: signature plus docstring, never
    the body (SPEC-007 chunking rule 3). Bodies are lexical search's job."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    chunks: list[Chunk] = []

    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        doc = tree.body[0]
        start = 1
        end = doc.end_lineno or start
        chunks.append(
            _make_chunk(path, start, end, module_name, "\n".join(lines[start - 1 : end]))
        )

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            start, end = _member_range(node)
            heading = f"{module_name}.{node.name}"
            chunks.append(
                _make_chunk(path, start, end, heading, "\n".join(lines[start - 1 : end]))
            )
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    mstart, mend = _member_range(member)
                    mheading = f"{module_name}.{node.name}.{member.name}"
                    chunks.append(
                        _make_chunk(
                            path,
                            mstart,
                            mend,
                            mheading,
                            "\n".join(lines[mstart - 1 : mend]),
                        )
                    )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            start, end = _member_range(node)
            heading = f"{module_name}.{node.name}"
            chunks.append(
                _make_chunk(path, start, end, heading, "\n".join(lines[start - 1 : end]))
            )
    return chunks


def chunk_file(
    path: Path,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap: int = DEFAULT_OVERLAP,
    *,
    repo_relative: str | None = None,
    module_name: str | None = None,
) -> list[Chunk]:
    """Dispatch on filename/extension. `repo_relative` and `module_name` let
    callers control provenance without depending on cwd."""
    rel = repo_relative if repo_relative is not None else str(path)
    rel = rel.replace("\\", "/")
    text = path.read_text(encoding="utf-8")
    if path.name == "AUDIT-LOG.md":
        return chunk_audit_log(rel, text)
    if path.name == "essentials-of-metaheuristics.md":
        return chunk_reference(rel, text, max_tokens=max_tokens, overlap=overlap)
    if path.suffix == ".py":
        mod = module_name or path.stem
        return chunk_python(rel, text, mod)
    if path.suffix == ".md":
        return chunk_markdown(
            rel, text, max_tokens=max_tokens, overlap=overlap, heading_prefix=path.stem
        )
    return []
