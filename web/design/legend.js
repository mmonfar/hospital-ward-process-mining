/* Legends, in which colour is never the only thing carrying the meaning.
 *
 * Every swatch draws the mark shape the chart actually draws (design bar 6),
 * so the legend is decodable in greyscale, in a photocopy of a printed board
 * paper, and by a reader with any colour vision deficiency. A coloured square
 * beside a label is not enough and is not offered.
 */

import { el, mark, patternFill, root as svgRoot, token } from "./svg.js";
import { dashFor, strategyLabel } from "./strategy.js";

const SWATCH = 16;

function swatch(children) {
  const node = svgRoot(SWATCH, SWATCH, { class: "legend__swatch" });
  node.removeAttribute("role");
  node.setAttribute("aria-hidden", "true");
  for (const child of children) node.appendChild(child);
  return node;
}

function item(swatchNode, label) {
  const li = document.createElement("li");
  li.className = "legend__item";
  li.appendChild(swatchNode);
  const text = document.createElement("span");
  text.textContent = label;
  li.appendChild(text);
  return li;
}

function list(items, label) {
  const ul = document.createElement("ul");
  ul.className = "legend";
  ul.setAttribute("aria-label", label);
  for (const node of items) ul.appendChild(node);
  return ul;
}

/**
 * Categorical legend.
 * @param {{colourToken: string, shape: string, label: string}[]} entries
 */
export function categoricalLegend(entries, label = "Series") {
  return list(
    entries.map((entry) =>
      item(
        swatch([mark(entry.shape, SWATCH / 2, SWATCH / 2, entry.colourToken, 11)]),
        entry.label,
      ),
    ),
    label,
  );
}

/**
 * Strategy legend: one hue, five dash patterns, selection emphasised. The
 * bounds are labelled as bounds -- a reader should be able to see from the
 * legend alone that two of these five enclose the other three.
 */
export function strategyLegend(selected, label = "Required-specialty definition") {
  const entries = ["referral", "consult_note", "problem_list", "union", "intersection"];
  return list(
    entries.map((key) => {
      const isSelected = key === selected;
      const dash = dashFor(key);
      const line = el("path", {
        d: `M1 ${SWATCH / 2} H${SWATCH - 1}`,
        "stroke-dasharray": dash,
        style: {
          stroke: token(isSelected ? "accent" : "ink-quiet"),
          "stroke-width": isSelected ? "2.5" : "1.25",
        },
        fill: "none",
      });
      const suffix = key === "union" || key === "intersection" ? " (bound)" : "";
      return item(swatch([line]), `${strategyLabel(key)}${suffix}`);
    }),
    label,
  );
}

/**
 * Legend for the roles that are not categories: observed, necessary, waste,
 * interval, infeasible, suppressed. These recur on every figure in the system
 * and mean the same thing on each one.
 */
export function roleLegend(roles, label = "Encoding") {
  const builders = {
    observed: () =>
      el("path", {
        d: `M1 ${SWATCH / 2} H${SWATCH - 1}`,
        style: { stroke: token("data-observed"), "stroke-width": "2.5" },
        fill: "none",
      }),
    necessary: () =>
      el("path", {
        d: `M1 ${SWATCH / 2} H${SWATCH - 1}`,
        "stroke-dasharray": "4 3",
        style: { stroke: token("data-necessary"), "stroke-width": "2" },
        fill: "none",
      }),
    interval: () =>
      el("rect", {
        x: 1,
        y: 3,
        width: SWATCH - 2,
        height: SWATCH - 6,
        style: { fill: token("data-band"), stroke: token("data-band-edge") },
      }),
    baseline: () =>
      el("path", {
        d: `M1 ${SWATCH / 2} H${SWATCH - 1}`,
        "stroke-dasharray": "6 3",
        style: { stroke: token("data-baseline"), "stroke-width": "1.5" },
        fill: "none",
      }),
  };
  const patterned = {
    waste: ["diagonal", "data-waste"],
    infeasible: ["cross", "data-infeasible"],
    suppressed: ["hatch", "data-suppressed"],
  };
  const labels = {
    observed: "Observed",
    necessary: "Necessary (lower bound)",
    interval: "95% interval",
    baseline: "Today's baseline",
    waste: "Motion waste",
    infeasible: "Infeasible schedule",
    suppressed: "Suppressed (<5 patients)",
  };

  return list(
    roles.map((role) => {
      const node = svgRoot(SWATCH, SWATCH, { class: "legend__swatch" });
      node.removeAttribute("role");
      node.setAttribute("aria-hidden", "true");
      if (builders[role]) {
        node.appendChild(builders[role]());
      } else if (patterned[role]) {
        const [pattern, colourToken] = patterned[role];
        node.appendChild(
          el("rect", {
            x: 1,
            y: 1,
            width: SWATCH - 2,
            height: SWATCH - 2,
            style: {
              fill: patternFill(node, pattern, colourToken),
              stroke: token(colourToken),
            },
          }),
        );
      } else {
        throw new Error(`unknown legend role: ${role}`);
      }
      return item(node, labels[role]);
    }),
    label,
  );
}
