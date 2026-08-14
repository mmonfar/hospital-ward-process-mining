/* The RequiredSpecialty strategy selector.
 *
 * SPEC-001 makes the definition of "a specialty was required" a runtime choice;
 * SPEC-005 makes the *sensitivity* of the answer to that choice the most
 * important thing this control communicates. So:
 *
 *   - it is a radiogroup showing all five definitions at once, never a dropdown;
 *   - selecting one emphasises it and leaves the other four drawn as context;
 *   - union and intersection are always the outer bounds of the drawn envelope,
 *     so the plausible range stays on screen whatever is selected;
 *   - the transition between selections is animated, because watching the
 *     answer move is the argument.
 *
 * A reader who uses this control should leave less confident in any single
 * number and more confident in the range. That is the design intent, and it is
 * why this file has a "spread" concept rather than a "filter" concept.
 */

import { strategyDash } from "./tokens.js";

export const STRATEGY_KEYS = Object.freeze([
  "referral",
  "consult_note",
  "problem_list",
  "union",
  "intersection",
]);

/** Union and intersection bound the others by construction (SPEC-001). */
export const BOUND_KEYS = Object.freeze(["union", "intersection"]);

const LABELS = Object.freeze({
  referral: "Referral",
  consult_note: "Consult note",
  problem_list: "Problem list",
  union: "Union",
  intersection: "Intersection",
});

export function strategyLabel(key) {
  return LABELS[key] ?? key;
}

/** SVG stroke-dasharray for a strategy. Identity is carried by dash, not hue. */
export function dashFor(key) {
  const dash = strategyDash[key];
  if (dash === undefined) throw new Error(`unknown strategy: ${key}`);
  return dash === "none" ? null : dash;
}

/**
 * Build the selector.
 *
 * Keyboard: arrow keys move and select (a radiogroup's expected behaviour),
 * Home/End jump to the ends, and the group holds a single tab stop.
 * SPEC-005 criterion 9.
 *
 * @param {object} spec
 * @param {string} spec.selected
 * @param {(key: string) => void} spec.onSelect
 * @param {string} [spec.label]
 */
export function strategySelector({ selected, onSelect, label = "Required-specialty definition" }) {
  let current = selected;
  const root = document.createElement("div");
  root.className = "segmented";
  root.setAttribute("role", "radiogroup");
  root.setAttribute("aria-label", label);

  const buttons = STRATEGY_KEYS.map((key) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "segmented__option";
    button.setAttribute("role", "radio");
    button.dataset.strategy = key;
    button.textContent = strategyLabel(key);
    button.addEventListener("click", () => select(key));
    root.appendChild(button);
    return button;
  });

  function paint(key) {
    for (const button of buttons) {
      const isSelected = button.dataset.strategy === key;
      button.setAttribute("aria-checked", String(isSelected));
      button.tabIndex = isSelected ? 0 : -1;
    }
  }

  function select(key, focus = false) {
    if (key === current) return;
    current = key;
    paint(current);
    if (focus) root.querySelector('[aria-checked="true"]').focus();
    onSelect(current);
  }

  root.addEventListener("keydown", (event) => {
    const index = STRATEGY_KEYS.indexOf(current);
    let next = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = STRATEGY_KEYS[(index + 1) % STRATEGY_KEYS.length];
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = STRATEGY_KEYS[(index - 1 + STRATEGY_KEYS.length) % STRATEGY_KEYS.length];
    } else if (event.key === "Home") {
      next = STRATEGY_KEYS[0];
    } else if (event.key === "End") {
      next = STRATEGY_KEYS[STRATEGY_KEYS.length - 1];
    }
    if (!next) return;
    event.preventDefault();
    select(next, true);
  });

  paint(current);
  return root;
}
