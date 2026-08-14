"""Colour maths for the design system: contrast, CIELAB distance, CVD simulation.

Standalone by design. SPEC-005 design bar 6 makes accessibility a *correctness*
property, and a correctness property has to be checkable by a test rather than by
someone squinting at a palette. Everything here is pure arithmetic over sRGB hex
strings so `tests/test_design_tokens.py` can assert on the palette directly.

References for the constants, so an auditor does not have to trust them:

- Relative luminance and contrast ratio: WCAG 2.1 §1.4.3 definitions.
- sRGB -> XYZ (D65) and XYZ -> CIELAB: CIE 15:2004 / IEC 61966-2-1.
- Colour vision deficiency: Vienot, Brettel & Mollon (1999), "Digital video
  colourmaps for checking the legibility of displays by dichromats", the
  LMS-plane-projection method. Simulation is approximate and individual
  dichromats vary; it is used here as a *floor* on separation, not a claim that
  two colours look identical to any particular person.
"""

from __future__ import annotations

import re
from typing import Final

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

RGB = tuple[float, float, float]

# sRGB (linear) -> LMS, Hunt-Pointer-Estevez normalised to D65, as used by
# Vienot et al. (1999).
_RGB_TO_LMS: Final[tuple[RGB, RGB, RGB]] = (
    (17.8824, 43.5161, 4.11935),
    (3.45565, 27.1554, 3.86714),
    (0.0299566, 0.184309, 1.46709),
)
_LMS_TO_RGB: Final[tuple[RGB, RGB, RGB]] = (
    (0.0809444479, -0.130504409, 0.116721066),
    (-0.0102485335, 0.0540193266, -0.113614708),
    (-0.000365296938, -0.00412161469, 0.693511405),
)

# Dichromat projections in LMS. Each replaces the missing cone response with a
# linear combination of the two that remain.
_DICHROMAT: Final[dict[str, tuple[RGB, RGB, RGB]]] = {
    "protanopia": (
        (0.0, 2.02344, -2.52581),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    ),
    "deuteranopia": (
        (1.0, 0.0, 0.0),
        (0.494207, 0.0, 1.24827),
        (0.0, 0.0, 1.0),
    ),
    "tritanopia": (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (-0.395913, 0.801109, 0.0),
    ),
}

CVD_KINDS: Final[tuple[str, ...]] = tuple(_DICHROMAT)

# D65 white point, 2 degree observer.
_WHITE: Final[RGB] = (0.95047, 1.0, 1.08883)


def parse_hex(value: str) -> RGB:
    """`#rrggbb` (or `#rgb`) to 0..1 sRGB. Raises on anything else."""
    if not _HEX.match(value):
        raise ValueError(f"not a hex colour: {value!r}")
    digits = value[1:]
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return tuple(int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def to_hex(rgb: RGB) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def _linearise(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _delinearise(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _apply(m: tuple[RGB, RGB, RGB], v: RGB) -> RGB:
    return tuple(sum(row[i] * v[i] for i in range(3)) for row in m)  # type: ignore[return-value]


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance."""
    r, g, b = (_linearise(c) for c in parse_hex(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio, 1.0 to 21.0. Order-independent."""
    a, b = relative_luminance(fg), relative_luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def to_lab(colour: str) -> RGB:
    """CIELAB under D65."""
    r, g, b = (_linearise(c) for c in parse_hex(colour))
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = (f(c / w) for c, w in zip((x, y, z), _WHITE, strict=True))
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e(a: str, b: str) -> float:
    """CIE76 distance in CIELAB.

    CIE76 rather than CIEDE2000 deliberately: it is a plain Euclidean distance
    that anyone can recompute from the printed Lab values, and the threshold it
    is compared against was chosen empirically against it. CIE76 overstates
    differences among saturated colours, so the threshold in `tokens.py` is set
    correspondingly high rather than at the textbook "just noticeable" value.
    """
    la, lb = to_lab(a), to_lab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb, strict=True)) ** 0.5


def simulate_cvd(colour: str, kind: str) -> str:
    """Vienot et al. (1999) dichromat simulation. `kind` from `CVD_KINDS`."""
    if kind not in _DICHROMAT:
        raise ValueError(f"unknown CVD kind: {kind!r}")
    linear = tuple(_linearise(c) for c in parse_hex(colour))
    lms = _apply(_RGB_TO_LMS, linear)  # type: ignore[arg-type]
    projected = _apply(_DICHROMAT[kind], lms)
    back = _apply(_LMS_TO_RGB, projected)
    return to_hex(tuple(_delinearise(c) for c in back))  # type: ignore[arg-type]


def min_separation(colours: list[str]) -> tuple[float, tuple[str, str]]:
    """Smallest pairwise CIE76 distance, and the pair that produced it."""
    worst = (float("inf"), (colours[0], colours[0]))
    for i, a in enumerate(colours):
        for b in colours[i + 1 :]:
            d = delta_e(a, b)
            if d < worst[0]:
                worst = (d, (a, b))
    return worst
