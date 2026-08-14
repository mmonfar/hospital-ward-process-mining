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
 * @param {object} spec
 * @param {string} spec.title
 * @param {string} [spec.subtitle]     what the figure shows, in one line
 * @param {object} spec.provenance     {strategy, methodVersion, dateRange, [source]}
 * @param {Node}   spec.body           chart, statistic block or state
 */
export function figure({ title, subtitle, provenance, body }) {
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
  const titles = h("div");
  titles.appendChild(h("h2", "t-title", title));
  if (subtitle) titles.appendChild(h("p", "figure__subtitle", subtitle));
  head.appendChild(titles);
  root.appendChild(head);

  const bodyWrap = h("div", "figure__body");
  if (body) bodyWrap.appendChild(body);
  root.appendChild(bodyWrap);

  root.appendChild(provenanceStrip(provenance));
  return root;
}

/** Replace a figure's body without rebuilding its frame or provenance. */
export function setFigureBody(figureNode, body) {
  const wrap = figureNode.querySelector(".figure__body");
  wrap.replaceChildren(body);
  return figureNode;
}

export function provenanceStrip({ strategy, methodVersion, dateRange, source }) {
  const dl = h("dl", "figure__provenance");
  const pairs = [
    ["Definition", strategy],
    ["Method", methodVersion],
    ["Period", dateRange],
  ];
  if (source) pairs.push(["Source", source]);
  for (const [term, value] of pairs) {
    const row = h("div");
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
