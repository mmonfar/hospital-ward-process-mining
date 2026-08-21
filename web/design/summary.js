/* Plain-language summary, epistemic status, and the method disclosure panel.
 *
 * Three related problems this file exists to solve, all of them found by
 * reading the built viewer as a first-time clinical-governance reader rather
 * than as its author:
 *
 * 1. **A figure that opens with a statistic is read as a statistic.** The
 *    number is the evidence, but it is not the finding. `plainSummary()` puts
 *    one sentence of clinical-register English above the number, so the reader
 *    knows what quantity they are looking at before they look at it.
 *
 * 2. **Not every figure makes the same kind of claim.** An observed coverage
 *    proportion, an upper bound under perfect foresight, a list of candidates
 *    awaiting review, and a set of modelled alternatives are four different
 *    epistemic objects, and a page that frames them identically invites the
 *    reader to quote them identically. `STATUSES` names the difference in a
 *    word, and marks it with a rule whose colour *and* line style differ, so
 *    the distinction survives both a greyscale print and a colour vision
 *    deficiency (design bar 6, D3).
 *
 *    These statuses are deliberately **epistemic, not evaluative**. There is no
 *    "good", no "concerning", no green and no red -- SPEC-005 design-system
 *    decision 5 forbids a palette that makes the judgement on the reader's
 *    behalf, and the strongest failure mode in the spec is this view drifting
 *    into a KPI dashboard. "Measured" and "bounded" describe how the number was
 *    arrived at. They do not say whether the ward should be pleased about it.
 *
 * 3. **Method is not the same as headline.** The governing decisions, the
 *    interval's construction, the disclosure floor and the deliberate absences
 *    all have to be *reachable* (design bar 8, ADR-0006), but a reader meeting
 *    the page for the first time should not have to read past three paragraphs
 *    of provenance to find out what it says. `methodNote()` is a `<details>`
 *    element -- collapsed by default, keyboard-operable and findable by the
 *    browser's own in-page search for free, which a custom disclosure widget
 *    would have to re-earn.
 *
 * What must NOT move in here: the interval, the denominator, and the strategy
 * key. Design bar 5 is explicit that an interval behind a disclosure is read as
 * absent, and ADR-0006 rule 2 requires the denominator's definition to travel
 * with the number. Those stay in the open, above this panel. What belongs here
 * is the internal citation, the estimator, and the caveat prose -- the material
 * a reader wants when they have decided to interrogate a number, not before.
 */

function h(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/**
 * How a figure's number came to exist. A word, not a verdict.
 *
 * Each entry's `label` is the visible text -- which is what makes the status
 * decodable without colour. The rule beside it varies in both hue and line
 * style, so it is never the only carrier of the distinction.
 */
export const STATUSES = Object.freeze({
  measured: {
    label: "Measured",
    hint: "Counted from events that were observed to have happened.",
  },
  bounded: {
    label: "Upper bound",
    hint: "The most this could be, not an estimate of what it is.",
  },
  candidate: {
    label: "For review",
    hint: "Flagged for a human to check. Not a finding.",
  },
  modelled: {
    label: "Modelled",
    hint: "Computed from alternatives that were not run. Not observed.",
  },
  reference: {
    label: "Reference",
    hint: "Context for the figures. Makes no claim of its own.",
  },
  withheld: {
    label: "Withheld",
    hint: "Not shown, to protect small groups from identification.",
  },
});

/**
 * One sentence saying what the figure found, in the reader's language.
 *
 * @param {object} spec
 * @param {string} spec.text    a complete sentence, clinical register, no
 *                              internal citation and no node or document id
 * @param {keyof STATUSES} spec.status
 */
export function plainSummary({ text, status = "measured" }) {
  if (!text) {
    throw new Error(
      "plainSummary: a plain-language sentence is required. A figure whose " +
        "headline is its statistic is read as a statistic, not as a finding.",
    );
  }
  const meta = STATUSES[status];
  if (!meta) {
    throw new Error(
      `plainSummary: unknown status "${status}". ` +
        `Known statuses: ${Object.keys(STATUSES).join(", ")}.`,
    );
  }
  const root = h("div", `summary summary--${status}`);
  const tag = h("span", "summary__status", meta.label);
  tag.title = meta.hint;
  root.appendChild(tag);
  root.appendChild(h("p", "summary__text", text));
  return root;
}

/**
 * The collapsed panel where method, provenance detail and internal citations
 * live.
 *
 * @param {object} spec
 * @param {string} [spec.label]     the disclosure's own text
 * @param {Array<[string, string]>} spec.entries  [term, prose] pairs
 */
export function methodNote({ label = "How this was measured", entries }) {
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new Error(
      "methodNote: entries are required. An empty method panel is worse than " +
        "no panel: it promises the reader an answer that is not there.",
    );
  }
  const root = h("details", "method");
  const head = h("summary", "method__summary", label);
  root.appendChild(head);

  const dl = h("dl", "method__body");
  for (const [term, prose] of entries) {
    const row = h("div", "method__row");
    row.appendChild(h("dt", null, term));
    row.appendChild(h("dd", null, prose));
    dl.appendChild(row);
  }
  root.appendChild(dl);
  return root;
}
