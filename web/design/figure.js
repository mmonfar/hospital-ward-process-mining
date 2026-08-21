/* The figure frame, and the two rules it will not let a caller break.
 *
 * SPEC-005 acceptance criteria 3 and 4 say every figure carries its interval,
 * its strategy key, its method version and its date range. Those are the kind
 * of requirement that holds for a month and then quietly stops holding on the
 * one screen nobody reviewed. So they are not a checklist here: `figure()`
 * throws without provenance, and `statistic()` throws on a point estimate that
 * has not been explicitly declared exact.
 *
 * A thrown error during development is the cheapest possible place to find
 * this. The expensive place is a screenshot in a board paper.
 */

const REQUIRED_PROVENANCE = ["strategy", "methodVersion", "dateRange"];

function h(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/**
 * The figure frame, in the order a first-time reader needs it.
 *
 * Head (name, provenance) -> summary (what it found, in words) -> body (the
 * evidence) -> method (how, and under whose authority). That order is the
 * whole of the progressive disclosure in this system: everything above the
 * `<details>` is what a reader gets on first glance, everything inside it is
 * what they get when they decide to interrogate the number.
 *
 * Provenance sits in the *head* rather than in a footer strip. It is the same
 * information either way -- criterion 4 is satisfied by presence, not by
 * position -- but as a chip row under the title it is scannable at a glance
 * and skippable on first read, where a raw label block below the body was
 * neither. The definition chip is emphasised because ADR-0006 rule 2 makes the
 * strategy the one piece of provenance that changes the number's meaning.
 *
 * @param {object} spec
 * @param {string} spec.title
 * @param {string} [spec.subtitle]     what the figure shows, in one plain line
 * @param {object} spec.provenance     {strategy, methodVersion, dateRange, [source]}
 * @param {Node}   [spec.summary]      a `plainSummary()` node
 * @param {Node}   spec.body           chart, statistic block or state
 * @param {Node}   [spec.method]       a `methodNote()` node
 */
export function figure({ title, subtitle, provenance, summary, body, method }) {
  if (!title) throw new Error("figure: a title is required");
  const missing = REQUIRED_PROVENANCE.filter((key) => !provenance || !provenance[key]);
  if (missing.length) {
    throw new Error(
      `figure "${title}": missing provenance [${missing.join(", ")}]. ` +
        "SPEC-005 criterion 4: a figure must be self-explanatory in a screenshot.",
    );
  }

  const root = h("figure", "figure");
  root.setAttribute("role", "group");
  root.setAttribute("aria-label", title);

  const head = h("div", "figure__head");
  const titleRow = h("div", "figure__title-row");
  titleRow.appendChild(h("h2", "t-title", title));
  head.appendChild(titleRow);
  if (subtitle) head.appendChild(h("p", "figure__subtitle", subtitle));
  head.appendChild(provenanceStrip(provenance));
  root.appendChild(head);

  if (summary) root.appendChild(summary);

  const bodyWrap = h("div", "figure__body");
  if (body) bodyWrap.appendChild(body);
  root.appendChild(bodyWrap);

  if (method) root.appendChild(method);
  return root;
}

/** Replace a figure's body without rebuilding its frame or provenance. */
export function setFigureBody(figureNode, body) {
  const wrap = figureNode.querySelector(".figure__body");
  wrap.replaceChildren(body);
  return figureNode;
}

/**
 * Definition, method version and period as a row of chips.
 *
 * The definition chip is emphasised: of the three, it is the only one that
 * changes what the number means rather than where it came from, and ADR-0006
 * rule 2 requires it to travel with every coverage figure. The other two are
 * present, legible and quiet -- evidence a reader can check, not chrome they
 * have to read.
 */
export function provenanceStrip({ strategy, methodVersion, dateRange, source }) {
  const dl = h("dl", "figure__provenance");
  const pairs = [
    ["Definition", strategy, true],
    ["Method", methodVersion, false],
    ["Period", dateRange, false],
  ];
  if (source) pairs.push(["Source", source, false]);
  for (const [term, value, isKey] of pairs) {
    const row = h("div", isKey ? "figure__prov figure__prov--key" : "figure__prov");
    row.appendChild(h("dt", null, term));
    row.appendChild(h("dd", null, value));
    dl.appendChild(row);
  }
  return dl;
}

/**
 * A single number, with the extent of what is not known about it.
 *
 * `interval` is [low, high] from the artefact -- never computed here. It is
 * rendered at body size next to the figure, not as a superscript: design bar 5
 * exists because an interval in fine print is read as decoration and the point
 * estimate gets quoted alone.
 *
 * `exact: true` is for counts that genuinely have no interval (number of
 * ward-days observed). It is a deliberate keystroke, so the absence of an
 * interval is always a decision someone made.
 */
export function statistic({ label, value, unit, interval, exact = false, note }) {
  if (!exact && !Array.isArray(interval)) {
    throw new Error(
      `statistic "${label}": no interval. Pass the artefact's interval, or ` +
        "exact: true if the quantity has none. SPEC-005 criterion 3.",
    );
  }
  const root = h("div", "stack");
  root.appendChild(h("div", "t-label", label));

  const row = h("div", "row");
  row.appendChild(h("div", "t-figure", unit ? `${value} ${unit}` : `${value}`));
  if (!exact) {
    row.appendChild(h("div", "t-interval", `95% CI ${interval[0]} to ${interval[1]}`));
  }
  root.appendChild(row);

  if (note) root.appendChild(h("div", "t-micro", note));
  return root;
}

/**
 * A supporting measure, collapsed to a tile.
 *
 * Not every quantity a page can compute is a quantity the reader came for.
 * Five figures at equal weight is five entry points and therefore none; a tile
 * says "this is real, it is here, it is not the story" in about a fifth of the
 * height. Expanding it is an explicit request for detail, which is the moment
 * the full statistics, the encoding legend and the method panel become welcome
 * rather than in the way.
 *
 * What does **not** collapse, and why the guards below are the same as
 * `figure()`'s and `statistic()`'s rather than a relaxed set for a smaller
 * component:
 *
 *   - The interval renders in the collapsed state. Design bar 5 is explicit
 *     that uncertainty behind a disclosure is read as absent and quoted as
 *     certain, and a compact tile is exactly the shape that gets screenshotted.
 *   - Provenance renders in the collapsed state. Criterion 4 is about
 *     screenshot self-sufficiency, and a tile is more likely to be cropped out
 *     of context than a full figure, not less.
 *
 * So the tile is smaller because it carries less *prose*, never because it
 * carries less evidence. A KPI tile that dropped either would be the "big
 * number, no interval" failure mode SPEC-005 names, in miniature.
 *
 * @param {object} spec
 * @param {string} spec.title          the measure's name
 * @param {string} spec.label          what the headline number is
 * @param {string} spec.value
 * @param {string} [spec.unit]
 * @param {string[]} [spec.interval]   [low, high] from the artefact
 * @param {boolean} [spec.exact]       deliberate opt-out, for a true count
 * @param {string} spec.summary        one plain-language sentence
 * @param {object} spec.provenance     {strategy, methodVersion, dateRange}
 * @param {Node}   spec.detail         what expansion reveals
 * @param {string} [spec.expandLabel]
 */
export function kpiTile({
  title,
  label,
  value,
  unit,
  interval,
  exact = false,
  summary,
  provenance,
  detail,
  expandLabel = "Full detail",
}) {
  if (!title) throw new Error("kpiTile: a title is required");
  if (!summary) {
    throw new Error(
      `kpiTile "${title}": a plain-language sentence is required. A collapsed ` +
        "tile whose only content is a number is a number without a meaning.",
    );
  }
  if (!exact && !Array.isArray(interval)) {
    throw new Error(
      `kpiTile "${title}": no interval. Design bar 5 -- an interval behind a ` +
        "disclosure is read as absent. Pass the artefact's interval, or " +
        "exact: true if the quantity has none.",
    );
  }
  const missing = REQUIRED_PROVENANCE.filter((key) => !provenance || !provenance[key]);
  if (missing.length) {
    throw new Error(
      `kpiTile "${title}": missing provenance [${missing.join(", ")}]. ` +
        "SPEC-005 criterion 4 applies to a tile exactly as it does to a figure.",
    );
  }

  const root = h("details", "kpi");
  const head = h("summary", "kpi__head");

  const top = h("div", "kpi__top");
  top.appendChild(h("h2", "t-label", title));
  top.appendChild(h("span", "kpi__expand", expandLabel));
  head.appendChild(top);

  const figures = h("div", "kpi__figures");
  figures.appendChild(h("span", "kpi__value", unit ? `${value} ${unit}` : `${value}`));
  if (!exact) {
    figures.appendChild(h("span", "t-interval", `95% CI ${interval[0]} to ${interval[1]}`));
  }
  figures.appendChild(h("span", "kpi__caption", label));
  head.appendChild(figures);

  head.appendChild(h("p", "kpi__summary", summary));
  head.appendChild(provenanceStrip(provenance));
  root.appendChild(head);

  const bodyWrap = h("div", "kpi__detail");
  if (detail) bodyWrap.appendChild(detail);
  root.appendChild(bodyWrap);
  return root;
}
