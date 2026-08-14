"""Generate `web/design/tokens.css` and `tokens.js` from `tokens.py`.

Both outputs are byte-for-byte deterministic so `hwpm design check` can assert
that what is committed is what the source produces. The CSS is what components
consume; the JS exists because three.js materials and hand-built SVG take values
in JavaScript, and `getComputedStyle` at render time is both slower and silently
degradable to an empty string.
"""

from __future__ import annotations

from pathlib import Path

from hwpm.design import tokens as t

HEADER = (
    "/* GENERATED FILE -- do not edit.\n"
    "   Source: src/hwpm/design/tokens.py\n"
    "   Rebuild: python -m hwpm.cli design build\n"
    "   Verify:  python -m hwpm.cli design check\n"
    "   SPEC-005 design bar 1: one visual system, declared once. */\n"
)

JS_HEADER = HEADER.replace("/* ", "/* ").rstrip() + "\n"


def _ramp_lookup() -> dict[str, str]:
    """Literal value -> ramp custom-property name, so semantics can cite a step."""
    out: dict[str, str] = {}
    for step, value in t.NEUTRAL.items():
        out.setdefault(value, f"--c-neutral-{step}")
    for step, value in t.TEAL.items():
        out.setdefault(value, f"--c-teal-{step}")
    return out


def _css_value(value: str, ramp: dict[str, str]) -> str:
    name = ramp.get(value)
    return f"var({name})" if name else value


def _block(title: str, rows: list[tuple[str, str]]) -> str:
    body = "".join(f"  --{name}: {value};\n" for name, value in rows)
    return f"\n  /* {title} */\n{body}"


def css() -> str:
    ramp = _ramp_lookup()
    out = [HEADER, "\n:root {"]

    out.append(
        _block(
            "Neutral ramp -- slate-green, not grey: it sits under a teal accent",
            [(f"c-neutral-{k}", v) for k, v in t.NEUTRAL.items()],
        )
    )
    out.append(
        _block(
            "Teal ramp -- extended from the prototype seed #16777a",
            [(f"c-teal-{k}", v) for k, v in t.TEAL.items()],
        )
    )
    out.append(
        _block(
            "Surfaces, ink and lines -- components reference only these",
            [(k, _css_value(v, ramp)) for k, v in t.SEMANTIC.items()],
        )
    )
    out.append(
        _block(
            "Categorical series -- five slots, each with a shape and a pattern",
            [(f"series-{s.key}", s.colour) for s in (*t.SERIES, t.SERIES_OTHER)],
        )
    )
    out.append(
        _block(
            "Data roles -- no green, no red: judgement belongs to the reader",
            [(k, _css_value(v, ramp)) for k, v in t.DATA.items()],
        )
    )
    out.append(
        _block(
            "RequiredSpecialty strategies -- one hue, distinguished by dash",
            [(f"dash-{k.replace('_', '-')}", v) for k, v in t.STRATEGY_DASH.items()],
        )
    )
    out.append(_block("Type -- four sizes, two weights", list(t.TYPE.items())))
    out.append(_block("Space", list(t.SPACE.items())))
    out.append(_block("Radius", list(t.RADIUS.items())))
    out.append(
        _block(
            "Elevation -- only where something is physically above",
            list(t.ELEVATION.items()),
        )
    )
    out.append(
        _block("Motion -- explanatory only (design bar 4)", list(t.MOTION.items()))
    )
    out.append("}\n")

    # Redefining the duration tokens rather than blanket-disabling transitions
    # keeps one mechanism: every component already reads var(--m-*), so this
    # covers components that do not exist yet. 0.01ms rather than 0 so that
    # `transitionend` still fires and JS sequencing does not stall.
    out.append(
        "\n@media (prefers-reduced-motion: reduce) {\n"
        "  :root {\n"
        "    --m-state: 0.01ms;\n"
        "    --m-transition: 0.01ms;\n"
        "    --m-narrative: 0.01ms;\n"
        "  }\n"
        "}\n"
    )
    return "".join(out)


def _js_entries(pairs: list[tuple[str, str]], indent: str = "  ") -> str:
    return "".join(f'{indent}"{k}": "{v}",\n' for k, v in pairs)


def js() -> str:
    series = "".join(
        f'    {{ key: "{s.key}", colour: "{s.colour}", shape: "{s.shape}", '
        f'pattern: "{s.pattern}", label: "{s.label}" }},\n'
        for s in t.SERIES
    )
    other = t.SERIES_OTHER
    durations = {
        "state": t.MOTION["m-state"],
        "transition": t.MOTION["m-transition"],
        "narrative": t.MOTION["m-narrative"],
    }
    return (
        JS_HEADER
        + "\nexport const colour = Object.freeze({\n"
        + _js_entries(list(t.SEMANTIC.items()))
        + _js_entries(list(t.DATA.items()))
        + "});\n"
        + "\nexport const series = Object.freeze([\n"
        + series
        + "].map(Object.freeze));\n"
        + "\nexport const seriesOther = Object.freeze("
        + f'{{ key: "{other.key}", colour: "{other.colour}", shape: "{other.shape}", '
        + f'pattern: "{other.pattern}", label: "{other.label}" }});\n'
        + "\nexport const strategyDash = Object.freeze({\n"
        + _js_entries(list(t.STRATEGY_DASH.items()))
        + "});\n"
        + "\n/* Milliseconds, for JS-driven animation. Read through\n"
        + "   `prefersReducedMotion()` in motion.js -- never used raw. */\n"
        + "export const duration = Object.freeze({\n"
        + "".join(f"  {k}: {v.removesuffix('ms')},\n" for k, v in durations.items())
        + "});\n"
        + "\nexport const space = Object.freeze({\n"
        + "".join(f'  "{k}": {v.removesuffix("px")},\n' for k, v in t.SPACE.items())
        + "});\n"
        + "\nexport const type = Object.freeze({\n"
        + _js_entries(list(t.TYPE.items()))
        + "});\n"
    )


OUTPUTS: dict[str, str] = {
    "tokens.css": "css",
    "tokens.js": "js",
}


def render(filename: str) -> str:
    return {"tokens.css": css, "tokens.js": js}[filename]()


def write_all(target_dir: Path) -> list[Path]:
    written = []
    for filename in OUTPUTS:
        path = target_dir / filename
        path.write_text(render(filename), encoding="utf-8", newline="\n")
        written.append(path)
    return written


def drift(target_dir: Path) -> list[str]:
    """Generated files that are missing or no longer match the source."""
    out = []
    for filename in OUTPUTS:
        path = target_dir / filename
        if not path.exists():
            out.append(f"{filename}: missing")
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != render(filename):
            out.append(f"{filename}: differs from src/hwpm/design/tokens.py")
    return out
