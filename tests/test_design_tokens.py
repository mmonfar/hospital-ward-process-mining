"""The design system's accessibility and consistency gates (SPEC-005, N14a).

SPEC-005 design bar 6 says accessibility is a correctness property. These tests
are what that sentence costs: contrast, dichromat separation, dual encoding and
single-source discipline are asserted here rather than reviewed by eye, because
a review by eye happens once and an assertion happens on every commit.

Covers SPEC-005 acceptance criterion 8 in full, and the design-system half of
criteria 3, 4 and 10. The behavioural halves -- that a *screen* renders no bare
point estimate and that a running view honours reduced motion -- need a DOM and
land with N14.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from hwpm.design import colour, emit, tokens

REPO_ROOT = Path(__file__).resolve().parents[1]
DESIGN_DIR = REPO_ROOT / "web" / "design"
GENERATED = {"tokens.css", "tokens.js"}


# --------------------------------------------------------------------------
# Contrast and colour vision
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule", tokens.CONTRAST_REQUIREMENTS, ids=lambda r: f"{r.fg}-on-{r.bg}"
)
def test_contrast_ratios(rule: tokens.ContrastRule) -> None:
    """Every pair the system puts on screen clears its WCAG floor."""
    ratio = colour.contrast_ratio(tokens.resolve(rule.fg), tokens.resolve(rule.bg))
    assert ratio >= rule.minimum, (
        f"{rule.fg} on {rule.bg} is {ratio:.2f}:1, needs {rule.minimum}:1 ({rule.use})"
    )


@pytest.mark.parametrize("kind", ["none", *colour.CVD_KINDS])
def test_series_separable_under_colour_vision_deficiency(kind: str) -> None:
    """The five categorical slots stay apart for a dichromat reader.

    A clinical audience of any size includes people with a colour vision
    deficiency; a palette that only separates for trichromats separates for
    most of the room and silently fails the rest.
    """
    shown = [
        c if kind == "none" else colour.simulate_cvd(c, kind)
        for c in tokens.series_colours()
    ]
    worst, pair = colour.min_separation(shown)
    assert worst >= tokens.SERIES_MIN_DELTA_E, (
        f"under {kind}, {pair[0]} and {pair[1]} are dE {worst:.1f} apart, "
        f"below the floor of {tokens.SERIES_MIN_DELTA_E}"
    )


def test_no_colour_only_encoding() -> None:
    """Every series carries a shape and a pattern as well as a colour."""
    every = (*tokens.SERIES, tokens.SERIES_OTHER)
    for series in every:
        assert series.shape, f"series {series.key} has no mark shape"
        assert series.pattern, f"series {series.key} has no fill pattern"
    shapes = [s.shape for s in every]
    assert len(set(shapes)) == len(shapes), f"mark shapes repeat: {shapes}"


def test_strategies_are_distinguished_without_colour() -> None:
    """All five RequiredSpecialty strategies differ by dash pattern.

    They share one hue on purpose (SPEC-001: a definitional axis, not five
    categories), so the dash is the whole of the distinction and cannot repeat.
    """
    dashes = list(tokens.STRATEGY_DASH.values())
    assert len(set(dashes)) == len(dashes), f"strategy dashes repeat: {dashes}"
    assert set(tokens.STRATEGY_DASH) >= {"union", "intersection"}, (
        "union and intersection are the outer bounds and must exist"
    )


def test_no_traffic_light_colour_in_the_semantic_set() -> None:
    """No success-green and no alarm-red anywhere a value can be dressed in.

    SPEC-005's strongest failure mode is the governance view drifting into a KPI
    dashboard. A palette with no "good" colour cannot make that judgement on the
    reader's behalf, which is the point: the interval is what they should be
    reading. Categorical series are exempt -- a specialty is not a verdict.
    """
    for name, value in {**tokens.SEMANTIC, **tokens.DATA}.items():
        if not value.startswith("#"):
            continue
        _, a, b = colour.to_lab(value)
        chroma = math.hypot(a, b)
        hue = math.degrees(math.atan2(b, a)) % 360
        assert not (105 <= hue <= 165 and chroma > 15), f"{name} is a signal green"
        assert not (10 <= hue <= 50 and chroma > 30), f"{name} is a signal red"


# --------------------------------------------------------------------------
# One source of truth
# --------------------------------------------------------------------------


def test_generated_files_match_their_source() -> None:
    """tokens.css and tokens.js are what tokens.py currently produces."""
    problems = emit.drift(DESIGN_DIR)
    assert not problems, (
        f"{problems} -- run `python -m hwpm.cli design build`. "
        "The generated files are not editable by hand."
    )


def _style_text(path: Path) -> str:
    """The parts of a file that can carry a colour, for literal-colour scanning."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".html":
        return text
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", text, re.S)
    blocks += re.findall(r"""\bstyle\s*=\s*["']([^"']*)["']""", text)
    blocks += re.findall(r"\.style\.[A-Za-z]+\s*=\s*(.+)", text)
    return "\n".join(blocks)


LITERAL_COLOUR = re.compile(
    r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b|\brgba?\s*\(|\bhsla?\s*\(", re.I
)


@pytest.mark.parametrize(
    "path",
    sorted(p for p in DESIGN_DIR.iterdir() if p.suffix in {".css", ".js", ".html"}),
    ids=lambda p: p.name,
)
def test_no_literal_colours_outside_the_generated_tokens(path: Path) -> None:
    """Colour appears once, in tokens.py, and reaches everything else by name.

    Design bar 1 -- one visual system -- survives exactly as long as this holds.
    The first hard-coded shade is never the problem; it is the precedent.
    """
    if path.name in GENERATED:
        pytest.skip("generated from tokens.py, which is the one place colour lives")
    offenders = [
        line.strip()
        for line in _style_text(path).splitlines()
        if LITERAL_COLOUR.search(line)
    ]
    assert not offenders, f"{path.name} declares colour directly: {offenders[:3]}"


# --------------------------------------------------------------------------
# Motion
# --------------------------------------------------------------------------

DURATION_LITERAL = re.compile(r"(transition|animation)[^;{}]*?\b\d+(\.\d+)?m?s\b", re.I)


@pytest.mark.parametrize(
    "path",
    sorted(p for p in DESIGN_DIR.iterdir() if p.suffix == ".css"),
    ids=lambda p: p.name,
)
def test_motion_durations_come_from_tokens(path: Path) -> None:
    """No literal duration in a transition or animation.

    Reduced motion is implemented by neutralising the duration tokens, so a
    literal duration is not a style inconsistency -- it is a transition that
    keeps running for a reader who asked it not to.
    """
    if path.name in GENERATED:
        pytest.skip("the generated file is where the duration tokens are defined")
    offenders = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if DURATION_LITERAL.search(line)
    ]
    assert not offenders, f"{path.name} hard-codes a duration: {offenders[:3]}"


def test_reduced_motion_is_honoured_by_the_token_layer() -> None:
    """tokens.css neutralises every duration under prefers-reduced-motion."""
    css = (DESIGN_DIR / "tokens.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    block = css.split("@media (prefers-reduced-motion: reduce)")[1]
    for name in ("--m-state", "--m-transition", "--m-narrative"):
        assert re.search(rf"{name}:\s*0\.01ms", block), f"{name} not neutralised"


def test_looping_animation_is_stopped_not_merely_shortened() -> None:
    """A neutralised duration makes a loop faster, not absent. base.css says so."""
    base = (DESIGN_DIR / "base.css").read_text(encoding="utf-8")
    reduced = base.split("@media (prefers-reduced-motion: reduce)")
    assert len(reduced) == 2, "base.css has no reduced-motion block"
    assert "animation: none" in reduced[1]


# --------------------------------------------------------------------------
# Enforcement that lives in the JavaScript primitives
# --------------------------------------------------------------------------
#
# These are presence checks, not behavioural ones: they assert the guard is
# still in the file, not that it fires. The behavioural test needs a DOM and a
# JS runner, and belongs with N14 where a headless browser is justified by more
# than four assertions. Recorded honestly rather than named to sound stronger.


def test_figure_frame_still_requires_provenance() -> None:
    source = (DESIGN_DIR / "figure.js").read_text(encoding="utf-8")
    assert 'REQUIRED_PROVENANCE = ["strategy", "methodVersion", "dateRange"]' in source
    assert "missing provenance" in source


def test_statistic_still_requires_an_interval() -> None:
    source = (DESIGN_DIR / "figure.js").read_text(encoding="utf-8")
    assert "no interval" in source
    assert "exact" in source


def test_series_chart_still_requires_the_bounding_strategies() -> None:
    source = (DESIGN_DIR / "series-chart.js").read_text(encoding="utf-8")
    assert "requireIntervals" in source
    assert "BOUND_KEYS" in source


def _strip_comments(source: str) -> str:
    """Remove /* block */ and // line comments.

    Assertions about behaviour must look at code, not prose. Grepping raw source
    means a test fails the moment someone documents the very rule it enforces --
    which is exactly how both assertions below first broke: `parallel.js` has no
    sort call, but its comment explaining why sorting is forbidden contains the
    word "sorts".
    """
    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_blocks, flags=re.MULTILINE)


def test_front_browser_still_requires_a_baseline() -> None:
    """ADR-0004: showing a ranked front re-scalarises the decision in the UI."""
    source = (DESIGN_DIR / "parallel.js").read_text(encoding="utf-8")
    assert "a baseline is required" in source

    code = _strip_comments(source)
    # Any actual ordering call, however it is spelled. `.sort(`, `sortBy(`,
    # `orderBy(` and `.reverse()` all reintroduce a ranking.
    offenders = re.findall(r"\b\w*(?:sort|orderBy)\w*\s*\(|\.reverse\s*\(", code)
    assert not offenders, (
        f"the front browser must not order the front (ADR-0004); found {offenders}"
    )


def test_typography_is_tabular_by_default() -> None:
    """Design bar 3: numbers are the product, so they align without opting in."""
    base = (DESIGN_DIR / "base.css").read_text(encoding="utf-8")
    # base.css legitimately declares `body` more than once -- a reset block and
    # a themed block. Checking only the first silently tested the reset.
    # The negative lookbehind keeps `.figure__body {` and friends out; matching
    # on `[^}]*` rather than a preceding `}` means every block is found
    # regardless of what surrounds it.
    body_blocks = re.findall(r"(?<![\w.\-])body\s*\{([^}]*)\}", base)
    assert body_blocks, "no body block found in base.css"
    assert any("font-variant-numeric: tabular-nums" in b for b in body_blocks), (
        "body must set tabular-nums so figures align without per-component opt-in"
    )
