import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DynamicForm from "./DynamicForm";
import { CONDITION_OPS } from "./formLogic";
import { formatJst, t } from "./i18n";
import {
  applyFormSource,
  createFormTemplate,
  deleteFormTemplate,
  getEventForm,
  getEventFormVersion,
  listEvents,
  listFormTemplates,
  saveEventForm,
  validateEventForm,
} from "./eventPlatformApi";

const TYPES = ["text", "textarea", "email", "phone", "number", "dropdown", "radio", "checkbox", "multiselect", "date", "file", "consent", "heading", "info", "group"];
const TEXT_TYPES = ["text", "textarea", "email", "phone"];
const CHOICE_TYPES = ["dropdown", "radio", "multiselect"];
const DISPLAY_TYPES = ["heading", "info"];
const FILE_TYPES = [
  ["application/pdf", "PDF"],
  ["image/jpeg", "JPEG"],
  ["image/png", "PNG"],
  ["image/webp", "WebP"],
];
const SYSTEM_TEST_VALUES = { membershipId: "M-TEST", membershipStatus: "active", membershipType: "Individual", membershipExpiry: "2099-03-31" };

// ── helpers ───────────────────────────────────────────────────────────────────

const clone = (value) => JSON.parse(JSON.stringify(value));

function camel(text) {
  const out = text.toLowerCase().replace(/[^a-z0-9]+(.)?/g, (_, c) => (c ? c.toUpperCase() : ""));
  const key = /^[a-z]/.test(out) ? out : `f${out}`;
  return key.slice(0, 40) || "field";
}

function allKeys(fields) {
  return fields.flatMap((f) => [f.key, ...(f.type === "group" ? allKeys(f.fields || []) : [])]);
}

function uniqueKey(base, taken) {
  let key = base;
  let n = 2;
  while (taken.includes(key)) key = `${base}${n++}`;
  return key;
}

function newField(type, fields) {
  const label = t(`events.form.type.${type}`);
  const field = { key: uniqueKey(camel(label), allKeys(fields)), type, label, required: false, _new: true };
  if (CHOICE_TYPES.includes(type)) {
    field.options = [
      { value: "option1", label: t("events.form.optionN", { n: 1 }) },
      { value: "option2", label: t("events.form.optionN", { n: 2 }) },
    ];
  }
  if (type === "file") field.validation = { maxSizeMb: 4, allowedTypes: ["application/pdf", "image/jpeg", "image/png"] };
  if (type === "group") {
    field.maxRepeat = 4;
    field.fields = [{ ...newField("text", [...fields, field]), label: t("events.form.name"), key: uniqueKey("name", allKeys(fields)) }];
  }
  if (DISPLAY_TYPES.includes(type)) delete field.required;
  return field;
}

function strip(fields) {
  return fields.map((f) => {
    const { _new, _keyTouched, ...rest } = f;
    if (rest.options) rest.options = rest.options.map(({ _new: n, _valueTouched, ...o }) => o);
    return rest.type === "group" ? { ...rest, fields: strip(rest.fields || []) } : rest;
  });
}

const OPTIONAL_TEXT = ["helpText", "placeholder", "defaultValue"];

/** Merge a patch, dropping keys that were emptied so "cleared" equals "never set". */
function merge(field, patch) {
  const next = { ...field, ...patch };
  Object.keys(next).forEach((k) => {
    if (next[k] === undefined || (OPTIONAL_TEXT.includes(k) && next[k] === "")) delete next[k];
  });
  return next;
}

function listAt(fields, path) {
  return path.length <= 1 ? fields : listAt(fields[path[0]].fields, path.slice(1));
}

function getAt(fields, path) {
  return listAt(fields, path)[path[path.length - 1]];
}

function mapList(fields, path, fn) {
  if (path.length === 1) return fn(fields);
  return fields.map((f, i) => (i === path[0] ? { ...f, fields: mapList(f.fields, path.slice(1), fn) } : f));
}

const updateAt = (fields, path, patch) =>
  mapList(fields, path, (list) => list.map((f, i) => (i === path[path.length - 1] ? merge(f, typeof patch === "function" ? patch(f) : patch) : f)));
const removeAt = (fields, path) => mapList(fields, path, (list) => list.filter((_, i) => i !== path[path.length - 1]));
const insertAt = (fields, parentPath, index, field) =>
  parentPath.length === 0
    ? [...fields.slice(0, index), field, ...fields.slice(index)]
    : fields.map((f, i) =>
        i === parentPath[0] ? { ...f, fields: insertAt(f.fields, parentPath.slice(1), index, field) } : f
      );

function moveBy(fields, path, delta) {
  return mapList(fields, path, (list) => {
    const i = path[path.length - 1];
    const j = i + delta;
    if (j < 0 || j >= list.length) return list;
    const next = [...list];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });
}

/** Where a palette-added field goes: just before the trailing consent block. */
function defaultInsertIndex(fields) {
  let i = fields.length;
  while (i > 0 && fields[i - 1].section === "consent") i -= 1;
  return i;
}

/** Fields a condition at `path` may refer to: everything earlier (plus earlier siblings in a group). */
function conditionSources(fields, path) {
  const usable = (f) => !DISPLAY_TYPES.includes(f.type) && f.type !== "group";
  if (path.length === 1) return fields.slice(0, path[0]).filter(usable);
  const group = fields[path[0]];
  return [...fields.slice(0, path[0]).filter(usable), ...group.fields.slice(0, path[1]).filter(usable)];
}

function hasBrokenReference(field, sources) {
  const rules = field.showIf?.all || field.showIf?.any || [];
  return rules.some((r) => !sources.some((s) => s.key === r.field));
}

// ── inspector pieces ──────────────────────────────────────────────────────────

function TextInput({ id, label, value, onChange, type = "text", help, ...rest }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} type={type} value={value ?? ""} onChange={(e) => onChange(e.target.value)} {...rest} />
      {help && <small>{help}</small>}
    </div>
  );
}

function NumInput({ id, label, value, onChange, ...rest }) {
  return (
    <TextInput id={id} label={label} type="number" value={value ?? ""} {...rest}
      onChange={(v) => onChange(v === "" ? undefined : Number(v))} />
  );
}

function OptionsEditor({ field, onChange }) {
  const options = field.options || [];
  const set = (next) => onChange({ options: next });
  return (
    <fieldset className="ep-fieldset">
      <legend>{t("events.form.options")}</legend>
      {options.map((o, i) => (
        <div className="ep-option-row" key={i}>
          <input aria-label={t("events.form.optionLabel", { n: i + 1 })} value={o.label}
            onChange={(e) => set(options.map((x, n) => {
              if (n !== i) return x;
              const touched = x._valueTouched;
              return { ...x, label: e.target.value, value: touched || !field._new ? x.value : camel(e.target.value) || x.value };
            }))} />
          <input aria-label={t("events.form.optionValue", { n: i + 1 })} value={o.value} readOnly={!field._new && !o._new}
            onChange={(e) => set(options.map((x, n) => (n === i ? { ...x, value: e.target.value, _valueTouched: true } : x)))} />
          <button type="button" className="button secondary" disabled={i === 0}
            aria-label={t("events.form.optionUp", { n: i + 1 })}
            onClick={() => { const next = [...options]; [next[i - 1], next[i]] = [next[i], next[i - 1]]; set(next); }}>↑</button>
          <button type="button" className="button secondary" disabled={options.length <= 1}
            aria-label={t("events.form.optionRemove", { n: i + 1 })}
            onClick={() => set(options.filter((_, n) => n !== i))}>✕</button>
        </div>
      ))}
      <button type="button" className="button secondary"
        onClick={() => {
          const n = options.length + 1;
          set([...options, { value: uniqueKey(`option${n}`, options.map((o) => o.value)), label: t("events.form.optionN", { n }), _new: true }]);
        }}>
        {t("events.form.addOption")}
      </button>
    </fieldset>
  );
}

function ConditionEditor({ field, sources, onChange }) {
  const mode = field.showIf?.any ? "any" : "all";
  const rules = field.showIf?.[mode] || [];
  const setRules = (next, m = mode) => onChange({ showIf: next.length ? { [m]: next } : undefined });
  const sourceOf = (key) => sources.find((s) => s.key === key);

  const valueInput = (rule, i) => {
    if (rule.op === "empty" || rule.op === "notEmpty") return null;
    const src = sourceOf(rule.field);
    const setValue = (v) => setRules(rules.map((r, n) => (n === i ? { ...r, value: v } : r)));
    const label = t("events.form.ruleValue", { n: i + 1 });
    if (src && (CHOICE_TYPES.includes(src.type)) && rule.op !== "gt" && rule.op !== "lt") {
      const opts = src.options || [];
      if (rule.op === "in") {
        const chosen = Array.isArray(rule.value) ? rule.value : [];
        return (
          <fieldset className="ep-rule-values">
            <legend>{label}</legend>
            {opts.map((o) => (
              <label key={o.value}>
                <input type="checkbox" checked={chosen.includes(o.value)}
                  onChange={(e) => setValue(e.target.checked ? [...chosen, o.value] : chosen.filter((v) => v !== o.value))} /> {o.label}
              </label>
            ))}
          </fieldset>
        );
      }
      return (
        <select aria-label={label} value={rule.value ?? ""} onChange={(e) => setValue(e.target.value)}>
          <option value="">{t("events.form.choose")}</option>
          {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      );
    }
    if (src && (src.type === "checkbox" || src.type === "consent")) {
      return (
        <select aria-label={label} value={String(rule.value ?? "")} onChange={(e) => setValue(e.target.value === "true")}>
          <option value="">{t("events.form.choose")}</option>
          <option value="true">{t("events.form.checked")}</option>
          <option value="false">{t("events.form.unchecked")}</option>
        </select>
      );
    }
    if (rule.op === "in") {
      return (
        <input aria-label={label} placeholder={t("events.form.commaList")} value={Array.isArray(rule.value) ? rule.value.join(", ") : ""}
          onChange={(e) => setValue(e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
      );
    }
    return <input aria-label={label} type={rule.op === "gt" || rule.op === "lt" ? "number" : "text"} value={rule.value ?? ""}
      onChange={(e) => setValue(rule.op === "gt" || rule.op === "lt" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)} />;
  };

  return (
    <fieldset className="ep-fieldset ep-cond">
      <legend>{t("events.form.logic")}</legend>
      {sources.length === 0 && <p className="ep-note">{t("events.form.logicNoSources")}</p>}
      {rules.length > 1 && (
        <div className="field">
          <label htmlFor="cond-mode">{t("events.form.logicMode")}</label>
          <select id="cond-mode" value={mode} onChange={(e) => setRules(rules, e.target.value)}>
            <option value="all">{t("events.form.logicAll")}</option>
            <option value="any">{t("events.form.logicAny")}</option>
          </select>
        </div>
      )}
      {rules.map((rule, i) => (
        <div className="ep-rule" key={i}>
          <select aria-label={t("events.form.ruleField", { n: i + 1 })} value={rule.field}
            onChange={(e) => setRules(rules.map((r, n) => (n === i ? { field: e.target.value, op: "eq", value: "" } : r)))}>
            {!sourceOf(rule.field) && <option value={rule.field}>{rule.field} ⚠</option>}
            {sources.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <select aria-label={t("events.form.ruleOp", { n: i + 1 })} value={rule.op}
            onChange={(e) => setRules(rules.map((r, n) => (n === i ? { ...r, op: e.target.value, value: e.target.value === "in" ? [] : r.value } : r)))}>
            {CONDITION_OPS.map((op) => <option key={op} value={op}>{t(`events.form.op.${op}`)}</option>)}
          </select>
          {valueInput(rule, i)}
          <button type="button" className="button secondary" aria-label={t("events.form.ruleRemove", { n: i + 1 })}
            onClick={() => setRules(rules.filter((_, n) => n !== i))}>✕</button>
        </div>
      ))}
      <button type="button" className="button secondary" disabled={sources.length === 0}
        onClick={() => setRules([...rules, { field: sources[0].key, op: "eq", value: "" }])}>
        {rules.length ? t("events.form.addRule") : t("events.form.addCondition")}
      </button>
    </fieldset>
  );
}

function Inspector({ fields, path, setFields, onSelect }) {
  const field = getAt(fields, path);
  const locked = !!field.locked;
  const patch = (p) => setFields((cur) => updateAt(cur, path, p));
  const v = field.validation || {};
  const setV = (p) => {
    const next = { ...v, ...p };
    Object.keys(next).forEach((k) => (next[k] === undefined || next[k] === "") && delete next[k]);
    patch({ validation: next });
  };
  const textLike = TEXT_TYPES.includes(field.type);
  const display = DISPLAY_TYPES.includes(field.type);
  const idp = `insp-${path.join("-")}`;
  const sources = conditionSources(fields, path);

  return (
    <div className="ep-inspector" aria-label={t("events.form.inspector")}>
      <h4>{t(`events.form.type.${field.type}`)}</h4>
      {locked && (
        <p className="ep-note" role="note">
          🔒 {t(field.source === "system" ? "events.form.coreSystem" : field.source === "profile" ? "events.form.coreProfile" : "events.form.coreConsent")}
        </p>
      )}
      <TextInput id={`${idp}-label`} label={display ? t("events.form.text") : t("events.form.label")} value={field.label}
        onChange={(label) => patch(field._new && !field._keyTouched ? { label, key: uniqueKey(camel(label), allKeys(fields).filter((k) => k !== field.key)) } : { label })} />
      {!display && (
        <TextInput id={`${idp}-key`} label={t("events.form.key")} value={field.key} readOnly={!field._new || locked}
          help={field._new && !locked ? t("events.form.keyHelpNew") : t("events.form.keyHelpSaved")}
          onChange={(key) => patch({ key: key.replace(/[^a-zA-Z0-9_]/g, ""), _keyTouched: true })} />
      )}
      {!display && <TextInput id={`${idp}-help`} label={t("events.form.helpText")} value={field.helpText} onChange={(helpText) => patch({ helpText })} />}
      {(textLike || field.type === "number" || field.type === "dropdown") && field.source !== "system" && (
        <TextInput id={`${idp}-ph`} label={t("events.form.placeholder")} value={field.placeholder} onChange={(placeholder) => patch({ placeholder })} />
      )}
      {!display && !locked && field.type !== "consent" && (
        <div className="field ep-check">
          <label htmlFor={`${idp}-req`}>
            <input id={`${idp}-req`} type="checkbox" checked={!!field.required} onChange={(e) => patch({ required: e.target.checked })} />{" "}
            {field.type === "group" ? t("events.form.requiredGroup") : t("events.form.required")}
          </label>
        </div>
      )}
      {field.type === "consent" && !locked && (
        <div className="field ep-check">
          <label htmlFor={`${idp}-req`}>
            <input id={`${idp}-req`} type="checkbox" checked={!!field.required} onChange={(e) => patch({ required: e.target.checked })} /> {t("events.form.required")}
          </label>
        </div>
      )}
      {!locked && !display && field.type !== "group" && field.type !== "file" && field.type !== "multiselect" && (
        <TextInput id={`${idp}-def`} label={t("events.form.defaultValue")} value={field.defaultValue} onChange={(defaultValue) => patch({ defaultValue })} />
      )}

      {CHOICE_TYPES.includes(field.type) && <OptionsEditor field={field} onChange={patch} />}

      {!locked && textLike && (
        <fieldset className="ep-fieldset">
          <legend>{t("events.form.validation")}</legend>
          <div className="field-row">
            <NumInput id={`${idp}-minl`} label={t("events.form.minLength")} min="0" value={v.minLength} onChange={(x) => setV({ minLength: x })} />
            <NumInput id={`${idp}-maxl`} label={t("events.form.maxLength")} min="1" value={v.maxLength} onChange={(x) => setV({ maxLength: x })} />
          </div>
          <TextInput id={`${idp}-re`} label={t("events.form.regex")} value={v.regex} help={t("events.form.regexHelp")} onChange={(x) => setV({ regex: x })} />
          {v.regex && <TextInput id={`${idp}-rem`} label={t("events.form.regexMessage")} value={v.regexMessage} onChange={(x) => setV({ regexMessage: x })} />}
        </fieldset>
      )}
      {!locked && field.type === "number" && (
        <fieldset className="ep-fieldset">
          <legend>{t("events.form.validation")}</legend>
          <div className="field-row">
            <NumInput id={`${idp}-min`} label={t("events.form.min")} value={v.min} onChange={(x) => setV({ min: x })} />
            <NumInput id={`${idp}-max`} label={t("events.form.max")} value={v.max} onChange={(x) => setV({ max: x })} />
          </div>
        </fieldset>
      )}
      {!locked && field.type === "date" && (
        <fieldset className="ep-fieldset">
          <legend>{t("events.form.validation")}</legend>
          <div className="field-row">
            <TextInput id={`${idp}-dmin`} type="date" label={t("events.form.earliest")} value={v.min} onChange={(x) => setV({ min: x })} />
            <TextInput id={`${idp}-dmax`} type="date" label={t("events.form.latest")} value={v.max} onChange={(x) => setV({ max: x })} />
          </div>
        </fieldset>
      )}
      {field.type === "multiselect" && (
        <fieldset className="ep-fieldset">
          <legend>{t("events.form.validation")}</legend>
          <div className="field-row">
            <NumInput id={`${idp}-smin`} label={t("events.form.minSelected")} min="0" value={v.min} onChange={(x) => setV({ min: x })} />
            <NumInput id={`${idp}-smax`} label={t("events.form.maxSelected")} min="1" value={v.max} onChange={(x) => setV({ max: x })} />
          </div>
        </fieldset>
      )}
      {field.type === "file" && (
        <fieldset className="ep-fieldset">
          <legend>{t("events.form.fileRules")}</legend>
          <NumInput id={`${idp}-size`} label={t("events.form.maxSizeMb")} min="1" max="4" value={v.maxSizeMb} onChange={(x) => setV({ maxSizeMb: x })} />
          {FILE_TYPES.map(([type, name]) => (
            <label key={type} className="ep-choice">
              <input type="checkbox" checked={(v.allowedTypes || []).includes(type)}
                onChange={(e) => setV({ allowedTypes: e.target.checked ? [...(v.allowedTypes || []), type] : (v.allowedTypes || []).filter((x) => x !== type) })} /> {name}
            </label>
          ))}
        </fieldset>
      )}

      {field.type === "group" && (
        <fieldset className="ep-fieldset">
          <legend>{t("events.form.groupSettings")}</legend>
          <NumInput id={`${idp}-rep`} label={t("events.form.maxRepeat")} min="1" max="50" value={field.maxRepeat} onChange={(x) => patch({ maxRepeat: x })} />
          <p className="ep-note">{t("events.form.groupFields")}</p>
          <ol className="ep-fb-list">
            {(field.fields || []).map((child, j) => (
              <li key={child.key + j}>
                <button type="button" className="ep-fb-item" onClick={() => onSelect([...path, j])}>
                  <span className="ep-fb-type">{t(`events.form.type.${child.type}`)}</span> {child.label}
                </button>
                <button type="button" className="button secondary" disabled={j === 0} aria-label={t("events.form.moveUp", { name: child.label })}
                  onClick={() => setFields((cur) => moveBy(cur, [...path, j], -1))}>↑</button>
                <button type="button" className="button secondary" disabled={j === field.fields.length - 1} aria-label={t("events.form.moveDown", { name: child.label })}
                  onClick={() => setFields((cur) => moveBy(cur, [...path, j], 1))}>↓</button>
                <button type="button" className="button secondary" disabled={field.fields.length <= 1} aria-label={t("events.form.remove", { name: child.label })}
                  onClick={() => setFields((cur) => removeAt(cur, [...path, j]))}>✕</button>
              </li>
            ))}
          </ol>
          <label htmlFor={`${idp}-addchild`}>{t("events.form.addToGroup")}</label>
          <select id={`${idp}-addchild`} value="" onChange={(e) => {
            if (!e.target.value) return;
            setFields((cur) => insertAt(cur, path, field.fields.length, newField(e.target.value, cur)));
          }}>
            <option value="">{t("events.form.choose")}</option>
            {TYPES.filter((x) => x !== "group").map((x) => <option key={x} value={x}>{t(`events.form.type.${x}`)}</option>)}
          </select>
        </fieldset>
      )}

      {!locked && <ConditionEditor field={field} sources={sources} onChange={patch} />}
    </div>
  );
}

// ── test panel ────────────────────────────────────────────────────────────────

function TestPanel({ eventId, fields, dirty }) {
  const [answers, setAnswers] = useState({});
  const [asMember, setAsMember] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const check = async () => {
    setError("");
    try {
      setResult(await validateEventForm(eventId, answers, asMember));
    } catch (err) {
      setError(err.message);
    }
  };
  return (
    <div className="ep-card">
      <p className="ep-note">{t("events.form.testHelp")}</p>
      <div className="field ep-check">
        <label htmlFor="ep-asmember">
          <input id="ep-asmember" type="checkbox" checked={asMember} onChange={(e) => setAsMember(e.target.checked)} /> {t("events.form.testAsMember")}
        </label>
      </div>
      <DynamicForm fields={fields} answers={answers} idPrefix="fbtest" onChange={(k, val) => setAnswers((a) => ({ ...a, [k]: val }))}
        errors={result?.errors || {}} system={asMember ? SYSTEM_TEST_VALUES : { membershipStatus: "none" }} />
      <div className="btn-row">
        <button type="button" className="button primary" onClick={check} disabled={dirty}
          title={dirty ? t("events.form.saveFirst") : undefined}>
          {t("events.form.checkServer")}
        </button>
        <button type="button" className="button secondary" onClick={() => { setAnswers({}); setResult(null); }}>{t("events.form.resetTest")}</button>
      </div>
      {dirty && <p className="ep-note">{t("events.form.saveFirst")}</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      {result && (
        <div role="status" className={Object.keys(result.errors).length ? "error-banner" : "success-banner"}>
          {Object.keys(result.errors).length ? t("events.form.testInvalid") : t("events.form.testValid", { version: result.version })}
          {Object.keys(result.errors).length === 0 && <pre className="ep-pre">{JSON.stringify(result.clean, null, 2)}</pre>}
        </div>
      )}
    </div>
  );
}

// ── builder ───────────────────────────────────────────────────────────────────

export default function FormBuilder({ eventId, roles, readOnly }) {
  const [loaded, setLoaded] = useState(null); // {form, versions, hasRegistrations}
  const [fields, setFields] = useState([]);
  const [viewing, setViewing] = useState(null); // version number being viewed/restored
  const [selected, setSelected] = useState(null);
  const [mode, setMode] = useState("build");
  const [problems, setProblems] = useState([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [templates, setTemplates] = useState([]);
  const [templateId, setTemplateId] = useState("");
  const [events, setEvents] = useState(null);
  const [copyFrom, setCopyFrom] = useState("");
  const [dropIndex, setDropIndex] = useState(null);
  const dragRef = useRef(null);
  const isSuper = roles.includes("super_admin");

  const load = useCallback(async () => {
    setProblems([]);
    try {
      const res = await getEventForm(eventId);
      setLoaded(res);
      setFields(clone(res.form.fields));
      setViewing(null);
      setSelected(null);
    } catch (err) {
      setProblems([err.message]);
    }
  }, [eventId]);

  useEffect(() => {
    load();
    listFormTemplates().then((r) => setTemplates(r.templates)).catch(() => {});
  }, [load]);

  const savedFields = loaded?.form.fields;
  const dirty = useMemo(() => !!loaded && JSON.stringify(strip(fields)) !== JSON.stringify(savedFields), [fields, savedFields, loaded]);
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const report = (err) => {
    const list = Object.values(err.fields || {});
    setProblems(list.length ? [err.message, ...list] : [err.message]);
  };

  const save = async () => {
    setBusy(true);
    setProblems([]);
    setMessage("");
    try {
      const res = await saveEventForm(eventId, strip(fields), loaded.form.version);
      setLoaded(res);
      setFields(clone(res.form.fields));
      setViewing(null);
      setMessage(res.changed ? t("events.form.savedVersion", { version: res.form.version }) : t("events.form.noChanges"));
    } catch (err) {
      report(err);
    } finally {
      setBusy(false);
    }
  };

  const confirmDiscard = () => !dirty || window.confirm(t("events.form.discardConfirm"));

  const applySource = async (source) => {
    if (!confirmDiscard() || !window.confirm(t("events.form.replaceConfirm"))) return;
    setBusy(true);
    setProblems([]);
    setMessage("");
    try {
      const res = await applyFormSource(eventId, source, loaded.form.version);
      setLoaded(res);
      setFields(clone(res.form.fields));
      setSelected(null);
      setMessage(t("events.form.savedVersion", { version: res.form.version }));
    } catch (err) {
      report(err);
    } finally {
      setBusy(false);
    }
  };

  const saveTemplate = async () => {
    const name = window.prompt(t("events.form.templateName"));
    if (!name) return;
    try {
      await createFormTemplate(name, strip(fields));
      setTemplates((await listFormTemplates()).templates);
      setMessage(t("events.form.templateSaved"));
    } catch (err) {
      report(err);
    }
  };

  const removeTemplate = async () => {
    if (!templateId || !window.confirm(t("events.form.templateDeleteConfirm"))) return;
    try {
      await deleteFormTemplate(templateId);
      setTemplates((list) => list.filter((x) => x.templateId !== templateId));
      setTemplateId("");
    } catch (err) {
      report(err);
    }
  };

  const openCopy = () => {
    if (!events) listEvents().then((r) => setEvents(r.events.filter((e) => e.formId && e.eventId !== eventId))).catch(() => setEvents([]));
  };

  const viewVersion = async (version) => {
    if (!confirmDiscard()) return;
    try {
      const res = await getEventFormVersion(eventId, version);
      setFields(clone(res.form.fields));
      setViewing(version === loaded.form.version ? null : version);
      setSelected(null);
    } catch (err) {
      report(err);
    }
  };

  // ── drag and drop (top-level fields) ────────────────────────────────────────
  const onDragOverItem = (e, index) => {
    if (!dragRef.current) return;
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    setDropIndex(e.clientY < rect.top + rect.height / 2 ? index : index + 1);
  };
  const onDrop = (e) => {
    e.preventDefault();
    const drag = dragRef.current;
    const at = dropIndex;
    dragRef.current = null;
    setDropIndex(null);
    if (!drag || at === null) return;
    if (drag.kind === "new") {
      setFields((cur) => insertAt(cur, [], at, newField(drag.type, cur)));
      setSelected([at]);
    } else {
      setFields((cur) => {
        const moving = cur[drag.index];
        const without = cur.filter((_, i) => i !== drag.index);
        return insertAt(without, [], at > drag.index ? at - 1 : at, moving);
      });
      setSelected(null);
    }
  };

  const addField = (type) => {
    const index = defaultInsertIndex(fields);
    setFields((cur) => insertAt(cur, [], index, newField(type, cur)));
    setSelected([index]);
  };

  const removeField = (index) => {
    const field = fields[index];
    if (field.locked) return;
    if (!window.confirm(t("events.form.removeConfirm", { name: field.label }))) return;
    setFields((cur) => removeAt(cur, [index]));
    setSelected(null);
  };

  const duplicateField = (index) => {
    const copy = clone(fields[index]);
    copy.key = uniqueKey(`${copy.key}Copy`, allKeys(fields));
    copy.label = `${copy.label} (${t("events.form.copy")})`;
    copy._new = true;
    copy._keyTouched = true;
    if (copy.type === "group") copy.fields = copy.fields.map((c) => ({ ...c, key: uniqueKey(`${c.key}Copy`, [...allKeys(fields), copy.key]), _new: true }));
    setFields((cur) => insertAt(cur, [], index + 1, copy));
    setSelected([index + 1]);
  };

  if (!loaded) return problems.length ? <div className="error-banner" role="alert">{problems[0]}</div> : <p>{t("events.loading")}</p>;

  const selectedField = selected && (() => { try { return getAt(fields, selected); } catch { return null; } })();
  const editable = !readOnly && !viewing;

  return (
    <section aria-labelledby="fb-h" className="ep-fb-wrap">
      <h3 id="fb-h">{t("events.form.title")}</h3>
      <p className="ep-note">{t("events.form.intro", { version: loaded.form.version })}</p>
      {loaded.hasRegistrations && <p className="ep-note" role="note">{t("events.form.hasRegistrations")}</p>}

      <div className="ep-toolbar" role="toolbar" aria-label={t("events.form.tools")}>
        <button type="button" className="button primary" onClick={save} disabled={busy || !dirty}>
          {t("events.form.saveVersion")}
        </button>
        <button type="button" className="button secondary" onClick={() => viewing ? load() : window.confirm(t("events.form.discardConfirm")) && load()} disabled={!dirty && !viewing}>
          {t("events.form.discard")}
        </button>
        <div className="ep-spacer" />
        <div className="admin-tabs" role="tablist" aria-label={t("events.form.mode")}>
          {["build", "test"].map((m) => (
            <button key={m} type="button" role="tab" aria-selected={mode === m} className={`admin-tab ${mode === m ? "active" : ""}`} onClick={() => setMode(m)}>
              {t(`events.form.mode.${m}`)}
            </button>
          ))}
        </div>
      </div>

      {problems.length > 0 && (
        <div className="error-banner" role="alert">
          <strong>{problems[0]}</strong>
          {problems.length > 1 && <ul>{problems.slice(1).map((p, i) => <li key={i}>{p}</li>)}</ul>}
        </div>
      )}
      {message && <div className="success-banner" role="status">{message}</div>}
      {viewing && (
        <p className="ep-note" role="status">
          {t("events.form.viewing", { version: viewing })}{" "}
          <button type="button" className="ep-link" onClick={load}>{t("events.form.backToLatest")}</button>
          {!readOnly && (
            <> · <button type="button" className="ep-link" onClick={() => setViewing(null)}>{t("events.form.restore")}</button></>
          )}
        </p>
      )}

      <div className="ep-card ep-form-tools">
        <div className="field-row">
          <div className="field">
            <label htmlFor="fb-version">{t("events.form.versions")}</label>
            <select id="fb-version" value={viewing || loaded.form.version} onChange={(e) => viewVersion(Number(e.target.value))}>
              {[...loaded.versions].reverse().map((x) => (
                <option key={x.version} value={x.version}>
                  v{x.version} · {formatJst(x.createdAt)}{x.createdBy ? ` · ${x.createdBy}` : ""}
                </option>
              ))}
            </select>
          </div>
          {editable && (
            <div className="field">
              <label htmlFor="fb-template">{t("events.form.templates")}</label>
              <select id="fb-template" value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
                <option value="">{t("events.form.choose")}</option>
                {templates.map((x) => <option key={x.templateId} value={x.templateId}>{x.name}</option>)}
              </select>
              <div className="btn-row">
                <button type="button" className="button secondary" disabled={!templateId || busy} onClick={() => applySource({ templateId })}>{t("events.form.useTemplate")}</button>
                {isSuper && <button type="button" className="button secondary" disabled={!templateId} onClick={removeTemplate}>{t("events.form.deleteTemplate")}</button>}
              </div>
            </div>
          )}
          {editable && (
            <div className="field">
              <label htmlFor="fb-copy">{t("events.form.copyFrom")}</label>
              <select id="fb-copy" value={copyFrom} onFocus={openCopy} onChange={(e) => setCopyFrom(e.target.value)}>
                <option value="">{t("events.form.choose")}</option>
                {(events || []).map((x) => <option key={x.eventId} value={x.eventId}>{x.title}</option>)}
              </select>
              <div className="btn-row">
                <button type="button" className="button secondary" disabled={!copyFrom || busy} onClick={() => applySource({ copyFromEventId: copyFrom })}>{t("events.form.copyButton")}</button>
              </div>
            </div>
          )}
        </div>
        {editable && <button type="button" className="button secondary" onClick={saveTemplate}>{t("events.form.saveTemplate")}</button>}
      </div>

      {mode === "test" ? (
        <TestPanel eventId={eventId} fields={fields} dirty={dirty} />
      ) : (
        <div className="ep-fb">
          {editable && (
            <aside className="ep-fb-palette" aria-label={t("events.form.palette")}>
              <h4>{t("events.form.addField")}</h4>
              <p className="ep-note">{t("events.form.paletteHelp")}</p>
              <ul>
                {TYPES.map((type) => (
                  <li key={type}>
                    <button type="button" className="button secondary" draggable
                      onDragStart={(e) => { dragRef.current = { kind: "new", type }; e.dataTransfer.setData("text/plain", type); e.dataTransfer.effectAllowed = "copy"; }}
                      onDragEnd={() => { dragRef.current = null; setDropIndex(null); }}
                      onClick={() => addField(type)}>
                      + {t(`events.form.type.${type}`)}
                    </button>
                  </li>
                ))}
              </ul>
            </aside>
          )}

          <ol className="ep-fb-canvas ep-fb-list" aria-label={t("events.form.canvas")} onDrop={onDrop} onDragOver={(e) => dragRef.current && e.preventDefault()}>
            {fields.map((field, i) => {
              const isSel = selected && selected.length === 1 && selected[0] === i;
              const broken = hasBrokenReference(field, conditionSources(fields, [i]));
              return (
                <li key={field.key} className={`ep-fb-row ${isSel ? "selected" : ""} ${dropIndex === i ? "drop-before" : ""} ${dropIndex === fields.length && i === fields.length - 1 ? "drop-after" : ""}`}
                  draggable={editable}
                  onDragStart={(e) => { dragRef.current = { kind: "move", index: i }; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", field.key); }}
                  onDragEnd={() => { dragRef.current = null; setDropIndex(null); }}
                  onDragOver={(e) => onDragOverItem(e, i)}>
                  <button type="button" className="ep-fb-item" aria-pressed={!!isSel} onClick={() => setSelected([i])}>
                    <span className="ep-fb-type">{t(`events.form.type.${field.type}`)}</span>{" "}
                    <strong>{field.label}</strong>
                    {field.locked && <span title={t("events.form.locked")} aria-label={t("events.form.locked")}> 🔒</span>}
                    {field.required && <span aria-label={t("events.form.requiredWord")}> *</span>}
                    {field.showIf && <span title={t("events.form.conditional")} aria-label={t("events.form.conditional")}> ⚡</span>}
                    {broken && <span className="ep-warn" role="img" aria-label={t("events.form.brokenRule")} title={t("events.form.brokenRule")}> ⚠</span>}
                  </button>
                  {editable && (
                    <div className="ep-fb-actions">
                      <button type="button" className="button secondary" disabled={i === 0} aria-label={t("events.form.moveUp", { name: field.label })}
                        onClick={() => { setFields((cur) => moveBy(cur, [i], -1)); setSelected([i - 1]); }}>↑</button>
                      <button type="button" className="button secondary" disabled={i === fields.length - 1} aria-label={t("events.form.moveDown", { name: field.label })}
                        onClick={() => { setFields((cur) => moveBy(cur, [i], 1)); setSelected([i + 1]); }}>↓</button>
                      {!field.locked && (
                        <>
                          <button type="button" className="button secondary" aria-label={t("events.form.duplicate", { name: field.label })} onClick={() => duplicateField(i)}>⧉</button>
                          <button type="button" className="button secondary" aria-label={t("events.form.remove", { name: field.label })} onClick={() => removeField(i)}>✕</button>
                        </>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>

          <div className="ep-fb-side">
            {selectedField ? (
              <fieldset disabled={!editable} className="ep-fieldset">
                <Inspector fields={fields} path={selected} setFields={setFields} onSelect={setSelected} />
                {selected.length > 1 && (
                  <button type="button" className="button secondary" onClick={() => setSelected([selected[0]])}>{t("events.form.backToGroup")}</button>
                )}
              </fieldset>
            ) : (
              <p className="ep-note">{t("events.form.selectHint")}</p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
