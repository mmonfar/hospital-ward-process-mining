/* The design system's public surface.
 *
 * Views import from here, not from the individual modules, so that what the
 * system offers is one list in one place -- and so that adding a primitive is a
 * visible act rather than a new import path appearing in a screen.
 */

export * as svg from "./svg.js";
export * as scale from "./scale.js";
export * as motion from "./motion.js";
export * from "./figure.js";
export * from "./summary.js";
export * from "./states.js";
export * from "./legend.js";
export * from "./strategy.js";
export * from "./series-chart.js";
export * from "./parallel.js";
export { colour, series, seriesOther, strategyDash, duration, space, type } from "./tokens.js";
