/* Hand-built SVG primitives -- SPEC-005 ("charts are hand-built SVG").
 *
 * Nothing here knows anything about wards, schedules or coverage. It knows
 * about elements, marks and patterns. Domain meaning arrives as data from an
 * artefact, per 02-ARCHITECTURE.md's layering rule applied to JavaScript.
 *
 * Colours are referenced as CSS custom-property *names*, never as values: a
 * literal colour anywhere in web/design/*.js is caught by
 * tests/test_design_tokens.py. That is what keeps one visual system one.
 */

const NS = "http://www.w3.org/2000/svg";

/** Create an SVG element. Attribute names are passed through verbatim. */
export function el(name, attrs = {}, children = []) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "style" && typeof value === "object") {
      for (const [prop, val] of Object.entries(value)) node.style.setProperty(prop, val);
    } else if (key === "text") {
      node.textContent = value;
    } else {
      node.setAttribute(key, String(value));
    }
  }
  for (const child of [].concat(children)) if (child) node.appendChild(child);
  return node;
}

/** `var(--name)`, so callers pass token names and never colour values. */
export function token(name) {
  return `var(--${name})`;
}

/** Polyline path data. Points are already in pixel space. */
export function line(points) {
  return points.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(2)} ${p[1].toFixed(2)}`).join(" ");
}

/** Closed band between an upper and a lower edge -- the shape an interval is. */
export function band(upper, lower) {
  if (!upper.length) return "";
  return `${line(upper)} L${lower
    .slice()
    .reverse()
    .map((p) => `${p[0].toFixed(2)} ${p[1].toFixed(2)}`)
    .join(" L")} Z`;
}

/* Mark shapes. One per categorical series, so a reader can tell two series
 * apart with the page in greyscale or with any colour vision. `size` is the
 * mark's full width in pixels; shapes are area-matched by eye rather than
 * mathematically, because a triangle of equal area reads smaller than a square.
 */
const SHAPES = {
  circle: (r) => `M${-r} 0 a${r} ${r} 0 1 0 ${2 * r} 0 a${r} ${r} 0 1 0 ${-2 * r} 0 Z`,
  square: (r) => `M${-r} ${-r} H${r} V${r} H${-r} Z`,
  triangle: (r) => `M0 ${-r * 1.15} L${r * 1.1} ${r * 0.75} L${-r * 1.1} ${r * 0.75} Z`,
  diamond: (r) => `M0 ${-r * 1.25} L${r * 1.15} 0 L0 ${r * 1.25} L${-r * 1.15} 0 Z`,
  cross: (r) => {
    const a = r * 0.42;
    return (
      `M${-a} ${-r} H${a} V${-a} H${r} V${a} H${a} V${r} H${-a} ` +
      `V${a} H${-r} V${-a} H${-a} Z`
    );
  },
  dot: (r) => SHAPES.circle(r * 0.55),
};

export function markPath(shape, size = 9) {
  const build = SHAPES[shape];
  if (!build) throw new Error(`unknown mark shape: ${shape}`);
  return build(size / 2);
}

export function mark(shape, x, y, colourToken, size = 9, attrs = {}) {
  return el("path", {
    d: markPath(shape, size),
    transform: `translate(${x.toFixed(2)} ${y.toFixed(2)})`,
    style: { fill: token(colourToken) },
    class: "mark",
    ...attrs,
  });
}

/* Fill patterns, for areas where a mark cannot carry the shape. Registered
 * once per <svg> in its <defs>. */
const PATTERNS = {
  solid: null,
  diagonal: "M0 8 L8 0 M-2 2 L2 -2 M6 10 L10 6",
  cross: "M0 8 L8 0 M0 0 L8 8",
  vertical: "M4 0 V8",
  hatch: "M0 6 L6 0",
  dots: null,
};

function defs(svg) {
  let node = svg.querySelector("defs");
  if (!node) {
    node = el("defs");
    svg.insertBefore(node, svg.firstChild);
  }
  return node;
}

/**
 * Ensure a fill pattern exists for (pattern, colourToken) and return its id.
 * Returns null for `solid`, which needs no pattern.
 */
export function ensurePattern(svg, pattern, colourToken) {
  if (!(pattern in PATTERNS)) throw new Error(`unknown fill pattern: ${pattern}`);
  if (pattern === "solid") return null;
  const id = `p-${pattern}-${colourToken}`;
  if (svg.querySelector(`#${CSS.escape(id)}`)) return id;
  const size = pattern === "hatch" ? 6 : 8;
  const child =
    pattern === "dots"
      ? el("circle", { cx: 4, cy: 4, r: 1.4, style: { fill: token(colourToken) } })
      : el("path", {
          d: PATTERNS[pattern],
          style: { stroke: token(colourToken), "stroke-width": "1.2" },
          fill: "none",
        });
  defs(svg).appendChild(
    el("pattern", { id, width: size, height: size, patternUnits: "userSpaceOnUse" }, [child]),
  );
  return id;
}

export function patternFill(svg, pattern, colourToken) {
  const id = ensurePattern(svg, pattern, colourToken);
  return id ? `url(#${id})` : token(colourToken);
}

/** Root <svg>. `viewBox` is set and width is fluid: figures get screenshotted
 *  at whatever size the window happens to be, and must stay legible. */
export function root(width, height, attrs = {}) {
  return el("svg", {
    class: "chart",
    viewBox: `0 0 ${width} ${height}`,
    preserveAspectRatio: "xMidYMid meet",
    role: "img",
    ...attrs,
  });
}
