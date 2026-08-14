/* Empty, loading and suppressed -- the three states that get built last and
 * seen first (design bar 7).
 *
 * Each one says, in words, what is true. "No MDT opportunities were detected on
 * this ward-day" is a finding: a ward where the rounds already coincide is the
 * outcome the project is trying to produce, and a panel that looks broken when
 * it happens teaches the reader to distrust the tool exactly when it is working.
 *
 * The loading state is hatched rather than skeletal on purpose. A grey bar
 * chart of placeholder bars is a picture of data that does not exist, and it is
 * read as data for the half-second before it is replaced.
 */

function h(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function state(modifier, message, detail, live) {
  const node = h("div", `state state--${modifier}`);
  node.appendChild(h("div", null, message));
  if (detail) node.appendChild(h("div", "state__detail", detail));
  if (live) node.setAttribute("aria-live", "polite");
  return node;
}

/**
 * A result that is empty, stated as a result.
 * @param {string} message  what is true, e.g. "No missed MDT opportunities"
 * @param {string} [detail] why that is a finding rather than an error
 */
export function emptyState(message, detail) {
  return state("empty", message, detail, true);
}

export function loadingState(message = "Loading", detail) {
  const node = state("loading", message, detail, true);
  node.setAttribute("aria-busy", "true");
  return node;
}

/**
 * A cell or figure withheld under the ADR-0005 disclosure floor.
 *
 * Named as a rule, not as an absence: "withheld" tells the reader the number
 * exists and is being protected, which is the honest description and heads off
 * the request to see it anyway.
 */
export function suppressedState(count = 5) {
  return state(
    "suppressed",
    "Withheld: fewer than 5 patients",
    `Groups below ${count} patients are not displayed, to prevent identification.`,
  );
}

/**
 * The boundary that will be asked about (SPEC-005 governance requirement 5).
 * Stated as a design decision, in place, rather than left as a gap someone
 * offers to fill in.
 */
export function absentByDesign(what, why) {
  const node = h("div", "state state--empty");
  node.appendChild(h("div", null, `${what} is not available in this tool.`));
  node.appendChild(h("div", "state__detail", why));
  return node;
}
