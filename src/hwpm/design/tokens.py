"""The design system's single source of truth (SPEC-005, node N14a).

`web/design/tokens.css` and `web/design/tokens.js` are **generated** from this
module and must never be hand-edited; `hwpm design check` fails if they drift.
One source rather than three because SPEC-005 design bar 1 requires one visual
system, and three hand-maintained copies of a palette is how a visual system
stops being one.

Why the palette is here rather than in CSS: design bar 6 makes accessibility a
correctness property, so the palette needs to be *tested* — contrast ratios,
dichromat separation, dual encoding. That requires the values in a language with
a test runner, and Python is the project's language (ADR-0002).

Three rules in this file are normative and are enforced by
`tests/test_design_tokens.py`:

1. `CONTRAST_REQUIREMENTS` — every foreground/background pair the system
   actually puts on screen, with its WCAG floor.
2. `SERIES_MIN_DELTA_E` — the categorical series stay separable under simulated
   protanopia, deuteranopia and tritanopia.
3. Every series carries a non-colour encoding (`shape` for marks, `pattern` for
   areas). No meaning is ever carried by colour alone.
"""

from __future__ import annotations

from typing import Final, NamedTuple

# --------------------------------------------------------------------------
# Primitive ramps
# --------------------------------------------------------------------------
# Seeded from the existing prototype (`web/hospital-ward.html`): teal #16777a on
# #eef1f0. Extended into ramps rather than joined by new hues -- design bar 1.
#
# The neutral is a slate-green, not a pure grey: it sits under a teal accent and
# a 3D scene lit with warm-neutral light, and a true grey reads cold beside both.

NEUTRAL: Final[dict[int, str]] = {
    0: "#ffffff",
    50: "#f7f9f8",
    100: "#eef1f0",  # page, from the prototype
    200: "#e2e8e6",
    300: "#ccd5d3",
    400: "#a4b1b0",
    500: "#8a9599",  # decorative separators only: 2.7:1 on the page, fails AA
    600: "#54666b",  # the prototype's #5b6d72, darkened to clear AA on a well
    700: "#33454b",
    800: "#26333a",
    900: "#1c2b30",
}

TEAL: Final[dict[int, str]] = {
    50: "#f0f6f5",
    100: "#dcebea",
    200: "#b6d5d5",
    300: "#82b6b7",
    400: "#4d9698",
    500: "#16777a",  # the seed
    600: "#106064",
    700: "#0d4d50",
    800: "#0a3c3f",
    900: "#06272a",
}

# --------------------------------------------------------------------------
# Semantic surface / ink / line tokens
# --------------------------------------------------------------------------
# Components reference only these. A component that reaches for a ramp step
# directly is reintroducing per-component colour decisions, which is the thing
# design bar 1 forbids.

SEMANTIC: Final[dict[str, str]] = {
    "surface-page": NEUTRAL[100],
    "surface-panel": NEUTRAL[0],
    "surface-sunken": NEUTRAL[200],
    "surface-scene": NEUTRAL[100],  # three.js clear colour; matches the page
    "ink-strong": NEUTRAL[900],
    "ink": NEUTRAL[700],
    "ink-quiet": NEUTRAL[600],  # the quietest tone that still passes AA at 13px
    "ink-inverse": NEUTRAL[0],
    "line-hairline": NEUTRAL[300],
    "line-strong": NEUTRAL[600],
    "accent": TEAL[500],
    "accent-strong": TEAL[600],
    "accent-quiet": TEAL[100],
    "focus": TEAL[600],
}

# --------------------------------------------------------------------------
# Categorical series
# --------------------------------------------------------------------------
# Five slots, and five is the cap. Beyond five, categorical colour stops being
# decodable -- more so under the AA contrast floor, which bars the light end of
# the space and leaves roughly 30 L* units to divide. The sixth bucket is
# `other`: neutral, hatched, and explicitly an aggregate. A view needing more
# than five distinguished specialties encodes them by position or small
# multiples, not by hue.
#
# Chosen by maximin search over muted (C* 26-38) in-gamut candidates under
# simulated protanopia/deuteranopia/tritanopia, subject to a 3:1 contrast floor
# against both page and panel, with the seed teal held fixed. Worst pair over
# all three simulations: dE76 = 18.7 (teal/rose, deuteranopia).


class Series(NamedTuple):
    """One categorical series: a colour plus its mandatory non-colour encoding.

    `shape` is for point marks, `pattern` for filled areas. Every series has
    both defined, so no figure can carry meaning in colour alone (design bar 6).
    """

    key: str
    colour: str
    shape: str
    pattern: str
    label: str


SERIES: Final[tuple[Series, ...]] = (
    Series("s1", "#16777a", "circle", "solid", "Series 1"),
    Series("s2", "#2f3f86", "square", "diagonal", "Series 2"),
    Series("s3", "#78264a", "triangle", "cross", "Series 3"),
    Series("s4", "#a06a0c", "diamond", "dots", "Series 4"),
    Series("s5", "#43663a", "cross", "vertical", "Series 5"),
)

# `other` is deliberately outside the separation requirement: it is neutral by
# design and always drawn hatched and labelled as an aggregate, so it is never
# distinguished from the five by hue in the first place.
SERIES_OTHER: Final[Series] = Series("other", NEUTRAL[500], "dot", "hatch", "Other")

SERIES_MIN_DELTA_E: Final[float] = 15.0

# --------------------------------------------------------------------------
# Data-role tokens
# --------------------------------------------------------------------------
# Roles that recur across every figure in SPEC-005 and must look the same
# everywhere: an observed path is one colour in the ward view, the motion chart
# and the front browser, or the reader has to relearn the encoding per screen.
#
# There is no success-green and no alarm-red anywhere in this system, and that
# is deliberate. SPEC-005's strongest failure mode is the governance view
# drifting into a KPI dashboard -- big number, green arrow, no interval. A
# palette with no "good" colour cannot express that judgement, and the reader
# has to look at the interval instead.

DATA: Final[dict[str, str]] = {
    "data-observed": TEAL[600],  # what actually happened
    "data-necessary": NEUTRAL[700],  # the necessary_m lower bound (SPEC-003)
    "data-waste": "#a06a0c",  # observed minus necessary
    "data-waste-fill": "rgba(160, 106, 12, 0.16)",
    "data-baseline": NEUTRAL[700],  # today's schedule, in the front browser
    "data-infeasible": "#78264a",  # feasibility marking (SPEC-005 failure modes)
    "data-band": "rgba(22, 119, 122, 0.18)",  # interval extent, not a tooltip
    "data-band-edge": TEAL[300],
    # Cells below the ADR-0005 floor of 5. Drawn as a fine hatch: it is quiet
    # because the strokes are sparse and thin, not because they are pale. A
    # suppressed cell is a real state and has to be readable as one.
    "data-suppressed": NEUTRAL[600],
    "data-grid": NEUTRAL[300],
    "data-axis": NEUTRAL[600],
}

# Dash patterns carry the RequiredSpecialty strategy identity. The strategy is
# not categorical -- it is a definitional axis with union and intersection as its
# outer bounds (SPEC-001) -- so it gets one hue with the selection emphasised,
# never five competing colours. Rendering five strategies in five hues would say
# "five alternatives, pick one"; this says "one quantity, defined five ways".

STRATEGY_DASH: Final[dict[str, str]] = {
    "referral": "none",
    "consult_note": "5 3",
    "problem_list": "1 3",
    "union": "9 4",
    "intersection": "2 2 7 2",
}

# --------------------------------------------------------------------------
# Type, space, elevation, motion
# --------------------------------------------------------------------------
# Four sizes and two weights, per design bar 3. A fifth size is a request to
# reconsider the hierarchy, not to add a token.

TYPE: Final[dict[str, str]] = {
    "font-sans": (
        "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif"
    ),
    "t-micro": "11px",  # labels, axis ticks, legend
    "t-body": "13px",  # everything ordinary
    "t-title": "17px",  # panel and figure titles
    "t-figure": "30px",  # the one number a figure is about
    "lh-micro": "1.35",
    "lh-body": "1.5",
    "lh-title": "1.3",
    "lh-figure": "1.1",
    "w-regular": "400",
    "w-strong": "600",
    "track-micro": "0.05em",  # uppercase micro labels only
    "track-title": "-0.01em",
}

SPACE: Final[dict[str, str]] = {
    "s-0": "2px",
    "s-1": "4px",
    "s-2": "8px",
    "s-3": "12px",
    "s-4": "16px",
    "s-5": "24px",
    "s-6": "32px",
    "s-7": "48px",
}

RADIUS: Final[dict[str, str]] = {
    "r-sm": "6px",
    "r-md": "8px",
    "r-lg": "10px",
}

# Two elevations, because there are exactly two things that are physically above
# something else: a panel over the 3D scene, and a transient overlay over a
# panel. Design bar 2: a shadow that does not express elevation is decoration.
ELEVATION: Final[dict[str, str]] = {
    "e-flat": "none",
    "e-panel": "0 1px 2px rgba(20, 40, 40, 0.06), 0 2px 12px rgba(20, 40, 40, 0.10)",
    "e-overlay": "0 6px 24px rgba(20, 40, 40, 0.18)",
}

# Three durations, named for what they explain rather than how long they take
# (design bar 4). `narrative` is the only one long enough to watch: it is for a
# clinician walking a route or a schedule morphing, where the motion *is* the
# information. Nothing else may use it.
MOTION: Final[dict[str, str]] = {
    "m-state": "90ms",  # hover, focus, selection feedback
    "m-transition": "240ms",  # one state of a figure becoming another
    "m-narrative": "900ms",  # a route or schedule playing out
    "ease-standard": "cubic-bezier(0.2, 0, 0, 1)",
    "ease-emphasis": "cubic-bezier(0.3, 0, 0.2, 1)",
}

# --------------------------------------------------------------------------
# Normative contrast table
# --------------------------------------------------------------------------


class ContrastRule(NamedTuple):
    fg: str
    bg: str
    minimum: float
    use: str


# WCAG 2.1 floors: 4.5 for text below 18.66px/bold-14px, 3.0 for large text and
# for graphical objects and UI component boundaries (1.4.11). Where a token is
# used for both, the stricter number is the one recorded.
CONTRAST_REQUIREMENTS: Final[tuple[ContrastRule, ...]] = (
    ContrastRule("ink-strong", "surface-page", 7.0, "titles and figure numerals"),
    ContrastRule("ink-strong", "surface-panel", 7.0, "titles inside panels"),
    ContrastRule("ink", "surface-page", 4.5, "body text"),
    ContrastRule("ink", "surface-panel", 4.5, "body text in panels"),
    ContrastRule("ink", "surface-sunken", 4.5, "body text in sunken wells"),
    ContrastRule("ink-quiet", "surface-page", 4.5, "micro labels, axis ticks"),
    ContrastRule("ink-quiet", "surface-panel", 4.5, "micro labels in panels"),
    ContrastRule("ink-quiet", "surface-sunken", 4.5, "micro labels in wells"),
    ContrastRule("accent", "surface-page", 3.0, "selected marks, chart strokes"),
    ContrastRule("accent", "surface-panel", 3.0, "selected marks in panels"),
    ContrastRule("accent-strong", "surface-page", 4.5, "link and button text"),
    ContrastRule("ink-inverse", "accent", 4.5, "text on a filled accent button"),
    ContrastRule("focus", "surface-page", 3.0, "focus ring (WCAG 1.4.11)"),
    ContrastRule("focus", "surface-panel", 3.0, "focus ring on panels"),
    ContrastRule("line-strong", "surface-panel", 3.0, "input and control borders"),
    ContrastRule("data-observed", "surface-panel", 3.0, "observed path stroke"),
    ContrastRule("data-necessary", "surface-panel", 3.0, "necessary-distance stroke"),
    ContrastRule("data-waste", "surface-panel", 3.0, "waste stroke"),
    ContrastRule("data-infeasible", "surface-panel", 3.0, "infeasible marking"),
    ContrastRule("data-axis", "surface-panel", 3.0, "axis lines and tick labels"),
    ContrastRule("data-suppressed", "surface-panel", 3.0, "suppressed-cell hatch"),
)


def resolve(name: str) -> str:
    """Token name to its literal value. Raises on an unknown name."""
    for group in (SEMANTIC, DATA, TYPE, SPACE, RADIUS, ELEVATION, MOTION):
        if name in group:
            return group[name]
    raise KeyError(f"unknown token: {name!r}")


def series_colours() -> list[str]:
    return [s.colour for s in SERIES]
