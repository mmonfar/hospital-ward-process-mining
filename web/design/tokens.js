/* GENERATED FILE -- do not edit.
   Source: src/hwpm/design/tokens.py
   Rebuild: python -m hwpm.cli design build
   Verify:  python -m hwpm.cli design check
   SPEC-005 design bar 1: one visual system, declared once. */

export const colour = Object.freeze({
  "surface-page": "#eef1f0",
  "surface-panel": "#ffffff",
  "surface-sunken": "#e2e8e6",
  "surface-scene": "#eef1f0",
  "ink-strong": "#1c2b30",
  "ink": "#33454b",
  "ink-quiet": "#54666b",
  "ink-inverse": "#ffffff",
  "line-hairline": "#ccd5d3",
  "line-strong": "#54666b",
  "accent": "#16777a",
  "accent-strong": "#106064",
  "accent-quiet": "#dcebea",
  "focus": "#106064",
  "data-observed": "#106064",
  "data-necessary": "#33454b",
  "data-waste": "#a06a0c",
  "data-waste-fill": "rgba(160, 106, 12, 0.16)",
  "data-baseline": "#33454b",
  "data-infeasible": "#78264a",
  "data-band": "rgba(22, 119, 122, 0.18)",
  "data-band-edge": "#82b6b7",
  "data-suppressed": "#54666b",
  "data-grid": "#ccd5d3",
  "data-axis": "#54666b",
});

export const series = Object.freeze([
    { key: "s1", colour: "#16777a", shape: "circle", pattern: "solid", label: "Series 1" },
    { key: "s2", colour: "#2f3f86", shape: "square", pattern: "diagonal", label: "Series 2" },
    { key: "s3", colour: "#78264a", shape: "triangle", pattern: "cross", label: "Series 3" },
    { key: "s4", colour: "#a06a0c", shape: "diamond", pattern: "dots", label: "Series 4" },
    { key: "s5", colour: "#43663a", shape: "cross", pattern: "vertical", label: "Series 5" },
].map(Object.freeze));

export const seriesOther = Object.freeze({ key: "other", colour: "#8a9599", shape: "dot", pattern: "hatch", label: "Other" });

export const strategyDash = Object.freeze({
  "referral": "none",
  "consult_note": "5 3",
  "problem_list": "1 3",
  "union": "9 4",
  "intersection": "2 2 7 2",
});

/* Milliseconds, for JS-driven animation. Read through
   `prefersReducedMotion()` in motion.js -- never used raw. */
export const duration = Object.freeze({
  state: 90,
  transition: 240,
  narrative: 900,
});

export const space = Object.freeze({
  "s-0": 2,
  "s-1": 4,
  "s-2": 8,
  "s-3": 12,
  "s-4": 16,
  "s-5": 24,
  "s-6": 32,
  "s-7": 48,
});

export const type = Object.freeze({
  "font-sans": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
  "t-micro": "11px",
  "t-body": "13px",
  "t-title": "17px",
  "t-figure": "30px",
  "lh-micro": "1.35",
  "lh-body": "1.5",
  "lh-title": "1.3",
  "lh-figure": "1.1",
  "w-regular": "400",
  "w-strong": "600",
  "track-micro": "0.05em",
  "track-title": "-0.01em",
});
