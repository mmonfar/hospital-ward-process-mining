/* Time series with intervals and strategy spread.
 *
 * This is the chart the governance view is made of (ADR-0006 requirements 1
 * and 2): coverage over time, with interval bands rather than a line of point
 * estimates, and all five RequiredSpecialty definitions visible at once with
 * the selected one emphasised.
 *
 * Two things it refuses to draw:
 *
 *   1. A series without an interval. Uncertainty is rendered as extent, never
 *      annotated (design bar 5). `exact: true` is the deliberate opt-out.
 *   2. Only the selected strategy. The envelope between union and intersection
 *      is drawn first, always, so the range the answer could take is on the
 *      page before the answer is.
 *
 * It computes nothing. `y`, `lo` and `hi` are artefact fields; this file turns
 * them into coordinates (SPEC-005 criterion 2).
 */

import { tween } from "./motion.js";
import * as scale from "./scale.js";
import { band, el, line, mark, root as svgRoot, token } from "./svg.js";
import { BOUND_KEYS, dashFor, strategyLabel } from "./strategy.js";

const PAD = { top: 12, right: 16, bottom: 28, left: 44 };

function requireIntervals(key, series) {
  if (series.exact) return;
  const bad = series.points.find(
    (p) => !Number.isFinite(p.lo) || !Number.isFinite(p.hi),
  );
  if (bad) {
    throw new Error(
      `series "${key}": point at x=${bad.x} has no interval. Pass lo/hi from ` +
        "the artefact, or exact: true. SPEC-005 criterion 3.",
    );
  }
}

/**
 * @param {object} spec
 * @param {number} spec.width
 * @param {number} spec.height
 * @param {Object<string, {points: {x,y,lo,hi}[], exact?: boolean}>} spec.strategies
 * @param {string} spec.selected
 * @param {{value: number, label: string}[]} spec.xTicks
 * @param {string} spec.yLabel
 * @param {(v: number) => string} [spec.yFormat]
 * @param {string} [spec.description]  the figure's accessible summary
 */
export function timeSeries(spec) {
  const {
    width,
    height,
    strategies,
    selected,
    xTicks,
    yLabel,
    yFormat = (v) => String(v),
    description,
  } = spec;

  for (const [key, series] of Object.entries(strategies)) requireIntervals(key, series);
  for (const key of BOUND_KEYS) {
    if (!strategies[key]) {
      throw new Error(
        `timeSeries: strategy "${key}" is missing. Union and intersection are ` +
          "the outer bounds of the spread and are always drawn (SPEC-005).",
      );
    }
  }

  const svg = svgRoot(width, height);
  if (description) {
    const title = el("title", { text: description });
    svg.appendChild(title);
    svg.setAttribute("aria-label", description);
  }

  const all = Object.values(strategies).flatMap((s) => s.points);
  const xs = all.map((p) => p.x);
  const ys = all.flatMap((p) => [p.y, p.lo, p.hi].filter(Number.isFinite));
  const x = scale.linear([Math.min(...xs), Math.max(...xs)], [PAD.left, width - PAD.right]);
  const y = scale.linear(scale.niceDomain(ys), [height - PAD.bottom, PAD.top]);

  /* Grid and axes first, so every stroke of data sits above them. */
  const grid = el("g", { "aria-hidden": "true" });
  for (const value of scale.ticks(y.domain, 4)) {
    const py = y(value);
    grid.appendChild(
      el("line", { class: "grid-line", x1: PAD.left, x2: width - PAD.right, y1: py, y2: py }),
    );
    grid.appendChild(
      el("text", {
        x: PAD.left - 6,
        y: py + 3,
        "text-anchor": "end",
        text: yFormat(value),
      }),
    );
  }
  for (const tick of xTicks) {
    grid.appendChild(
      el("text", {
        x: x(tick.value),
        y: height - PAD.bottom + 16,
        "text-anchor": "middle",
        text: tick.label,
      }),
    );
  }
  grid.appendChild(
    el("line", {
      class: "axis-line",
      x1: PAD.left,
      x2: width - PAD.right,
      y1: height - PAD.bottom,
      y2: height - PAD.bottom,
    }),
  );
  grid.appendChild(
    el("text", {
      x: PAD.left,
      y: PAD.top - 2,
      "text-anchor": "start",
      text: yLabel,
    }),
  );
  svg.appendChild(grid);

  /* The spread envelope: everything between the two bounding definitions.
     Drawn under the data, unlabelled here -- the legend names it. */
  const envelope = el("path", {
    class: "band",
    d: band(
      strategies[BOUND_KEYS[0]].points.map((p) => [x(p.x), y(p.y)]),
      strategies[BOUND_KEYS[1]].points.map((p) => [x(p.x), y(p.y)]),
    ),
    "aria-hidden": "true",
  });
  svg.appendChild(envelope);

  /* The selected strategy's own interval. */
  const interval = el("path", { class: "band", "aria-hidden": "true" });
  const intervalEdge = el("path", { class: "band-edge", "aria-hidden": "true" });
  svg.appendChild(interval);
  svg.appendChild(intervalEdge);

  const lines = new Map();
  for (const key of Object.keys(strategies)) {
    const node = el("path", {
      class: "series-line",
      "stroke-dasharray": dashFor(key),
      style: { stroke: token("ink-quiet") },
      d: line(strategies[key].points.map((p) => [x(p.x), y(p.y)])),
    });
    lines.set(key, node);
    svg.appendChild(node);
  }

  const marks = el("g");
  svg.appendChild(marks);

  let cancel = () => {};
  let current = null;

  /** Draw the emphasised band and marks for an already-resolved point set. */
  function draw(points, exact) {
    const upper = points.map((p) => [x(p.x), y(exact ? p.y : p.hi)]);
    const lower = points.map((p) => [x(p.x), y(exact ? p.y : p.lo)]);
    interval.setAttribute("d", exact ? "" : band(upper, lower));
    intervalEdge.setAttribute("d", exact ? "" : `${line(upper)} ${line(lower)}`);
    marks.replaceChildren();
    for (const point of points) {
      marks.appendChild(mark("circle", x(point.x), y(point.y), "accent", 7));
    }
  }

  function emphasise(key) {
    for (const [other, node] of lines) {
      const isSelected = other === key;
      const isBound = BOUND_KEYS.includes(other);
      const modifier = isSelected ? "" : isBound ? " series-line--bound" : " series-line--context";
      node.setAttribute("class", `series-line${modifier}`);
      node.style.setProperty("stroke", token(isSelected ? "accent" : "ink-quiet"));
    }
    svg.setAttribute(
      "aria-label",
      `${description ?? yLabel} Emphasised definition: ${strategyLabel(key)}.`,
    );
  }

  const alignable = (a, b) =>
    a.length === b.length && a.every((p, i) => p.x === b[i].x);

  /* Selection is animated because the movement between definitions is the
     argument this control is making: the reader is meant to see how far the
     answer travels when the definition changes. The band is interpolated
     point by point rather than cross-faded, so what moves is the quantity.
     At zero duration -- reduced motion -- the end state is identical. */
  function select(key) {
    if (key === current) return;
    cancel();
    const from = current ? strategies[current] : null;
    const to = strategies[key];
    current = key;
    emphasise(key);

    if (!from || from.exact !== to.exact || !alignable(from.points, to.points)) {
      draw(to.points, to.exact);
      return;
    }
    cancel = tween("transition", (t) => {
      const at = (a, b) => a + (b - a) * t;
      draw(
        to.points.map((p, i) => ({
          x: p.x,
          y: at(from.points[i].y, p.y),
          lo: at(from.points[i].lo, p.lo),
          hi: at(from.points[i].hi, p.hi),
        })),
        to.exact,
      );
    });
  }

  select(selected);
  return { node: svg, select };
}
