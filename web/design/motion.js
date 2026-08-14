/* Motion, under one rule: it explains a mechanism or it does not happen.
 *
 * SPEC-005 design bar 4. Three durations exist (tokens.js `duration`), and the
 * long one is reserved for motion that *is* the information -- a clinician
 * walking a route, a schedule morphing into another schedule. Panels do not
 * animate in.
 *
 * `prefers-reduced-motion` is handled in two places because there are two
 * mechanisms: CSS transitions read duration tokens that tokens.css neutralises,
 * and JS-driven animation reads `duration()` here. Both end at the same final
 * state -- reduced motion means arriving immediately, never a different result.
 */

import { duration as raw } from "./tokens.js";

const query =
  typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;

export function prefersReducedMotion() {
  return Boolean(query && query.matches);
}

/** Milliseconds for a named duration, already reduced-motion aware. */
export function duration(name) {
  if (!(name in raw)) throw new Error(`unknown duration: ${name}`);
  return prefersReducedMotion() ? 0 : raw[name];
}

/** Run `fn` on every change of the reduced-motion preference. */
export function onMotionPreferenceChange(fn) {
  if (!query) return () => {};
  const handler = () => fn(prefersReducedMotion());
  query.addEventListener("change", handler);
  return () => query.removeEventListener("change", handler);
}

/**
 * Interpolate from 0 to 1 over a named duration, calling `step(t)` each frame
 * and `step(1)` last. At zero duration it calls `step(1)` once, synchronously:
 * the end state is identical, which is the property that matters.
 *
 * Returns a cancel function. Callers that animate on user input must cancel the
 * previous run -- two tweens fighting over the same attribute is how an
 * "explanatory" animation stops explaining anything.
 */
export function tween(name, step) {
  const ms = duration(name);
  if (ms <= 0) {
    step(1);
    return () => {};
  }
  let frame = 0;
  const start = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - start) / ms);
    step(ease(t));
    if (t < 1) frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(frame);
}

/* Matches --ease-standard closely enough for JS-driven tweens to feel like the
 * CSS ones. Not the same curve to the millisecond; the same character. */
function ease(t) {
  return 1 - (1 - t) ** 3;
}
