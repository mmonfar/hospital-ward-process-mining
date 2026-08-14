/* Scales and ticks.
 *
 * SPEC-005 permits D3 for scales and shapes. It is not used: the chart types
 * this system needs are five, they need linear and band scales and a tick
 * generator, and that is this file. The trade was a CDN dependency (with an
 * integrity pin to maintain, on a machine that may be offline inside a hospital
 * network) against ninety lines. Recorded in SPEC-005 so it is a decision
 * rather than an omission.
 *
 * Mapping a value to a pixel is rendering, not analysis. Acceptance criterion 2
 * -- "the front end computes none" -- is about *statistics*: a total, a mean, a
 * distance, an interval. If a number is spoken aloud in the interface, it came
 * from an artefact field. A y coordinate is not that kind of number.
 */

/** Continuous scale over [min, max] -> [from, to] pixels. */
export function linear(domain, range) {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  const scale = (value) => (span === 0 ? (r0 + r1) / 2 : r0 + ((value - d0) / span) * (r1 - r0));
  scale.domain = [d0, d1];
  scale.range = [r0, r1];
  scale.invert = (px) => (span === 0 ? d0 : d0 + ((px - r0) / (r1 - r0)) * span);
  return scale;
}

/** Discrete keys -> evenly spaced band centres. */
export function band(keys, range, padding = 0.2) {
  const [r0, r1] = range;
  const step = keys.length ? (r1 - r0) / (keys.length + padding) : 0;
  const index = new Map(keys.map((k, i) => [k, i]));
  const scale = (key) => r0 + step * (index.get(key) + (1 + padding) / 2);
  scale.keys = keys;
  scale.step = step;
  scale.width = step * (1 - padding);
  return scale;
}

/**
 * Tick values at 1/2/5 x 10^n, at most `count` of them.
 *
 * Ticks are always extended to include zero when the domain is within a
 * quarter of it. A truncated axis on a motion or coverage figure exaggerates
 * every difference on it, and these figures get screenshotted into papers.
 */
export function ticks(domain, count = 5) {
  let [d0, d1] = domain;
  if (d0 > d1) [d0, d1] = [d1, d0];
  if (d0 > 0 && d0 < (d1 - d0) / 4) d0 = 0;
  if (d1 < 0 && -d1 < (d1 - d0) / 4) d1 = 0;
  const span = d1 - d0;
  if (span === 0) return [d0];
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const normalised = raw / magnitude;
  const step = (normalised >= 5 ? 5 : normalised >= 2 ? 2 : 1) * magnitude;
  const out = [];
  for (let v = Math.ceil(d0 / step) * step; v <= d1 + step / 1e6; v += step) {
    out.push(Number(v.toFixed(10)));
  }
  return out;
}

/** Domain padded to the next tick, so the top series is not clipped. */
export function niceDomain(values, count = 5) {
  const finite = values.filter((v) => Number.isFinite(v));
  if (!finite.length) return [0, 1];
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  const t = ticks([lo, hi], count);
  const step = t.length > 1 ? t[1] - t[0] : Math.abs(hi) || 1;
  const first = t[0];
  const last = t[t.length - 1];
  return [Math.min(lo, first), Math.max(hi, hi > last ? last + step : last)];
}
