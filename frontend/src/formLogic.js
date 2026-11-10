// Conditional-logic rules for registration forms. Mirrors lambdas/form_engine.py;
// both are tested against tests/fixtures/form_conditions.json. The server is the
// authority: this only decides what to show while someone fills the form in.

function isEmpty(value) {
  return (
    value === null ||
    value === undefined ||
    value === false ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );
}

function norm(value) {
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function toNumber(value) {
  if (typeof value === "boolean" || value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function ruleHolds(rule, answers) {
  const answer = answers[rule.field];
  const { op, value: expected } = rule;
  if (op === "empty") return isEmpty(answer);
  if (op === "notEmpty") return !isEmpty(answer);
  const answered = Array.isArray(answer) ? answer : [answer];
  const given = answered.filter((a) => a !== null && a !== undefined).map(norm);
  if (op === "eq") return given.includes(norm(expected));
  if (op === "ne") return !given.includes(norm(expected));
  if (op === "in") {
    const wanted = (Array.isArray(expected) ? expected : [expected]).map(norm);
    return wanted.some((w) => given.includes(w));
  }
  if (op === "gt" || op === "lt") {
    const a = toNumber(answer);
    const b = toNumber(expected);
    if (a === null || b === null) return false;
    return op === "gt" ? a > b : a < b;
  }
  return false;
}

export function evaluateCondition(cond, answers) {
  if (!cond) return true;
  if (cond.all) return cond.all.every((r) => ruleHolds(r, answers));
  if (cond.any) return cond.any.some((r) => ruleHolds(r, answers));
  return true;
}

export function isVisible(field, answers) {
  return evaluateCondition(field.showIf, answers);
}

/** Fields shown for the current answers; a hidden field counts as unanswered for later conditions. */
export function visibleFields(fields, answers, outer = {}) {
  const scope = { ...outer, ...answers };
  const shown = [];
  fields.forEach((field) => {
    if (isVisible(field, scope)) shown.push(field);
    else scope[field.key] = null;
  });
  return shown;
}

export const CONDITION_OPS = ["eq", "ne", "in", "notEmpty", "empty", "gt", "lt"];
