/* Parallel coordinates: the Pareto front browser (SPEC-005, ADR-0004).
 *
 * The hardest interface problem in this project. A user compares five to ten
 * schedules across five objectives and picks one, and the interface must not
 * make that choice for them. ADR-0004 deliberately refused to scalarise the
 * objectives in the maths; a UI that sorts the front, scores it, or ranks it
 * puts the scalarisation back in through the front door.
 *
 * So there is no sort control, no rank column, and no total. Axis order is
 * fixed by the caller and never reordered by value. Schedules are drawn in one
 * neutral tone -- colouring them would imply categories that do not exist --
 * with emphasis reserved for the one under the cursor or keyboard focus.
 *
 * Two things are always on screen: today's observed baseline, so "better" is
 * anchored to current reality rather than to the best of the alternatives; and
 * feasibility, marked on the line itself, because a smooth animation of an
 * infeasible schedule is persuasive in exactly the wrong direction.
 */

import * as scale from "./scale.js";
import { el, line, mark, root as svgRoot, token } from "./svg.js";

const PAD = { top: 30, right: 24, bottom: 34, left: 24 };

/**
 * @param {object} spec
 * @param {number} spec.width
 * @param {number} spec.height
 * @param {{key: string, label: string, unit?: string, direction?: "min"|"max",
 *          domain?: [number, number]}[]} spec.objectives  fixed order, never sorted
 * @param {{id: string, label: string, objectives: Object<string, number>,
 *          feasible?: boolean}[]} spec.schedules
 * @param {{label: string, objectives: Object<string, number>}} spec.baseline
 * @param {(id: string|null) => void} [spec.onHighlight]
 * @param {(id: string) => void} [spec.onSelect]
 */
export function paretoFront(spec) {
  const {
    width,
    height,
    objectives,
    schedules,
    baseline,
    onHighlight = () => {},
    onSelect = () => {},
  } = spec;

  if (objectives.length < 2) throw new Error("paretoFront: at least two objectives");
  if (!baseline) {
    throw new Error(
      "paretoFront: a baseline is required. SPEC-005 -- 'better' is anchored to " +
        "today's observed schedule, not to the best of the alternatives.",
    );
  }

  const svg = svgRoot(width, height, {
    tabindex: "0",
    role: "application",
    "aria-label":
      `Pareto front: ${schedules.length} schedules across ${objectives.length} ` +
      "objectives, unordered. Arrow keys move between schedules, Enter selects.",
  });

  const axisX = scale.band(
    objectives.map((o) => o.key),
    [PAD.left, width - PAD.right],
    0,
  );
  const scales = new Map(
    objectives.map((objective) => {
      const values = [
        ...schedules.map((s) => s.objectives[objective.key]),
        baseline.objectives[objective.key],
      ].filter(Number.isFinite);
      const domain = objective.domain ?? scale.niceDomain(values, 3);
      return [objective.key, scale.linear(domain, [height - PAD.bottom, PAD.top])];
    }),
  );

  /* Axes. Each carries its own unit and its direction of improvement: five
     objectives in different units, on five scales, is exactly the situation
     where an unlabelled axis gets misread. */
  const axes = el("g", { "aria-hidden": "true" });
  for (const objective of objectives) {
    const px = axisX(objective.key);
    const y = scales.get(objective.key);
    axes.appendChild(
      el("line", {
        class: "axis-line",
        x1: px,
        x2: px,
        y1: PAD.top,
        y2: height - PAD.bottom,
      }),
    );
    for (const value of scale.ticks(y.domain, 3)) {
      axes.appendChild(
        el("text", {
          x: px - 6,
          y: y(value) + 3,
          "text-anchor": "end",
          text: String(value),
        }),
      );
    }
    const arrow = objective.direction === "max" ? "higher is better" : "lower is better";
    axes.appendChild(
      el("text", { x: px, y: 14, "text-anchor": "middle", text: objective.label }),
    );
    axes.appendChild(
      el("text", {
        x: px,
        y: height - PAD.bottom + 15,
        "text-anchor": "middle",
        text: objective.unit ? `${objective.unit}, ${arrow}` : arrow,
      }),
    );
  }
  svg.appendChild(axes);

  const pointsFor = (record) =>
    objectives.map((objective) => [
      axisX(objective.key),
      scales.get(objective.key)(record.objectives[objective.key]),
    ]);

  /* Baseline first and always: it is the thing every alternative is being
     compared against, so it is never something the user has to switch on. */
  svg.appendChild(
    el("path", { class: "baseline-line", d: line(pointsFor(baseline)), "aria-hidden": "true" }),
  );
  const baselineEnd = pointsFor(baseline).at(-1);
  svg.appendChild(
    el("text", {
      x: baselineEnd[0] + 6,
      y: baselineEnd[1] + 3,
      "text-anchor": "start",
      text: baseline.label,
      style: { fill: token("data-baseline") },
    }),
  );

  const rows = new Map();
  const layer = el("g");
  for (const schedule of schedules) {
    const points = pointsFor(schedule);
    const feasible = schedule.feasible !== false;
    const path = el("path", {
      class: "series-line series-line--context",
      d: line(points),
      "stroke-dasharray": feasible ? null : "3 3",
      style: { stroke: token(feasible ? "ink-quiet" : "data-infeasible") },
    });
    const marks = el("g", { "aria-hidden": "true" });
    for (const [px, py] of points) {
      marks.appendChild(
        mark(feasible ? "circle" : "cross", px, py, feasible ? "ink-quiet" : "data-infeasible", 7),
      );
    }
    /* A wide transparent copy of the line: a 2px stroke is not a pointer
       target, and a front browser that is fiddly to hover is a front browser
       nobody explores. */
    const hit = el("path", {
      class: "hit-target",
      d: line(points),
      "stroke-width": 14,
      style: { stroke: token("surface-panel"), "stroke-opacity": "0" },
      fill: "none",
    });
    hit.addEventListener("pointerenter", () => highlight(schedule.id));
    hit.addEventListener("pointerleave", () => highlight(null));
    hit.addEventListener("click", () => onSelect(schedule.id));

    const group = el("g", {}, [path, marks, hit]);
    layer.appendChild(group);
    rows.set(schedule.id, { schedule, path, marks, feasible });
  }
  svg.appendChild(layer);

  const readout = el("text", {
    x: PAD.left,
    y: height - 4,
    "text-anchor": "start",
    style: { fill: token("ink-strong") },
  });
  svg.appendChild(readout);

  let highlighted = null;

  function highlight(id) {
    if (id === highlighted) return;
    highlighted = id;
    for (const [key, row] of rows) {
      const on = key === id;
      row.path.setAttribute(
        "class",
        `series-line${on ? "" : " series-line--context"}`,
      );
      const colourToken = row.feasible ? (on ? "accent" : "ink-quiet") : "data-infeasible";
      row.path.style.setProperty("stroke", token(colourToken));
      row.marks.style.setProperty("opacity", on ? "1" : "0.6");
    }
    const row = id ? rows.get(id) : null;
    readout.textContent = row
      ? `${row.schedule.label}${row.feasible ? "" : " -- infeasible"}`
      : "";
    onHighlight(id);
  }

  /* Keyboard: the plot is one tab stop, arrows walk the schedules in the order
     the artefact supplied them (SPEC-005 criterion 9). That order is the
     artefact's, not a ranking -- nothing here reorders it. */
  const ids = schedules.map((s) => s.id);
  svg.addEventListener("keydown", (event) => {
    const index = ids.indexOf(highlighted);
    let next = null;
    if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      next = ids[(index + 1) % ids.length];
    } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
      next = ids[(index - 1 + ids.length) % ids.length];
    } else if (event.key === "Escape") {
      highlight(null);
      return;
    } else if ((event.key === "Enter" || event.key === " ") && highlighted) {
      event.preventDefault();
      onSelect(highlighted);
      return;
    }
    if (!next) return;
    event.preventDefault();
    highlight(next);
  });
  svg.addEventListener("blur", () => highlight(null));

  return { node: svg, highlight };
}
