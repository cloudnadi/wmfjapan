import { useState } from "react";
import { visibleFields } from "./formLogic";
import { t } from "./i18n";

/**
 * Renders a registration form schema. Used by the builder's test mode today and
 * by the public registration flow later.
 *
 *   fields   schema fields (see lambdas/event_model.py)
 *   answers  { [key]: value }
 *   onChange (key, value) => void
 *   errors   { [pathOrKey]: message }, e.g. "diet" or "guests[0].name"
 *   system   values for read-only system fields, e.g. { membershipStatus: "active" }
 */
export default function DynamicForm({ fields, answers, onChange, errors = {}, system = {}, idPrefix = "df", outer = {}, errorPrefix = "", uploadFile, readOnlyKeys = [] }) {
  return (
    <div className="ep-dynform">
      {visibleFields(fields, answers, outer).map((field) => (
        <FieldView key={field.key} field={field} answers={answers} onChange={onChange} errors={errors} system={system}
          idPrefix={idPrefix} errorPrefix={errorPrefix} outer={outer} uploadFile={uploadFile} readOnlyKeys={readOnlyKeys} />
      ))}
    </div>
  );
}

function FieldView({ field, answers, onChange, errors, system, idPrefix, errorPrefix, outer, uploadFile, readOnlyKeys }) {
  const [busy, setBusy] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const id = `${idPrefix}-${field.key}`;
  const error = errors[`${errorPrefix}${field.key}`];
  const value = answers[field.key];
  const readOnly = !errorPrefix && readOnlyKeys.includes(field.key);
  const common = {
    id,
    readOnly: readOnly || undefined,
    "aria-readonly": readOnly || undefined,
    "aria-invalid": !!error,
    "aria-describedby": [field.helpText && `${id}-help`, error && `${id}-error`].filter(Boolean).join(" ") || undefined,
  };
  const label = (
    <>
      {field.label}
      {field.required && <span aria-hidden="true"> *</span>}
      {field.required && <span className="ep-sr-only"> ({t("events.form.requiredWord")})</span>}
    </>
  );
  const help = field.helpText && <small id={`${id}-help`}>{field.helpText}</small>;
  const err = error && (
    <span className="field-error" id={`${id}-error`} role="alert">
      {error}
    </span>
  );
  const set = (v) => onChange(field.key, v);

  if (field.type === "heading") return <h3 className="ep-form-heading">{field.label}</h3>;
  if (field.type === "info") return <p className="ep-note">{field.label}</p>;

  if (field.source === "system") {
    const shown = system[field.key];
    return (
      <div className="field">
        <label htmlFor={id}>{field.label}</label>
        <input id={id} value={shown ?? ""} placeholder={t("events.form.systemFilled")} readOnly aria-readonly="true" />
        {help}
      </div>
    );
  }

  if (field.type === "group") {
    const entries = Array.isArray(value) ? value : [];
    const update = (index, key, v) => set(entries.map((e, i) => (i === index ? { ...e, [key]: v } : e)));
    return (
      <fieldset className="ep-group">
        <legend>{label}</legend>
        {help}
        {entries.map((entry, i) => (
          <div className="ep-card" key={i}>
            <strong>{t("events.form.entryN", { n: i + 1 })}</strong>
            <DynamicForm fields={field.fields} answers={entry} onChange={(k, v) => update(i, k, v)} errors={errors}
              system={system} idPrefix={`${id}-${i}`} outer={{ ...outer, ...answers }} errorPrefix={`${errorPrefix}${field.key}[${i}].`}
              uploadFile={uploadFile} />
            <button type="button" className="button secondary" onClick={() => set(entries.filter((_, n) => n !== i))}>
              {t("events.form.removeEntry", { n: i + 1 })}
            </button>
          </div>
        ))}
        <button type="button" className="button secondary" disabled={entries.length >= field.maxRepeat}
          onClick={() => set([...entries, {}])}>
          {t("events.form.addEntry")}
        </button>
        {err}
      </fieldset>
    );
  }

  if (field.type === "radio" || field.type === "multiselect") {
    const multi = field.type === "multiselect";
    const selected = multi ? (Array.isArray(value) ? value : []) : value;
    return (
      <fieldset className="field ep-choices" aria-describedby={common["aria-describedby"]}>
        <legend>{label}</legend>
        {(field.options || []).map((o) => (
          <label key={o.value} className="ep-choice">
            <input
              type={multi ? "checkbox" : "radio"}
              name={id}
              checked={multi ? selected.includes(o.value) : selected === o.value}
              onChange={(e) =>
                set(multi ? (e.target.checked ? [...selected, o.value] : selected.filter((v) => v !== o.value)) : o.value)
              }
            />{" "}
            {o.label}
          </label>
        ))}
        {help}
        {err}
      </fieldset>
    );
  }

  if (field.type === "checkbox" || field.type === "consent") {
    return (
      <div className="field ep-check">
        <label htmlFor={id}>
          <input {...common} type="checkbox" checked={!!value} onChange={(e) => set(e.target.checked)} /> {label}
        </label>
        {help}
        {err}
      </div>
    );
  }

  let control;
  if (field.type === "textarea") {
    control = <textarea {...common} rows={4} value={value ?? ""} placeholder={field.placeholder} onChange={(e) => set(e.target.value)} />;
  } else if (field.type === "dropdown") {
    control = (
      <select {...common} value={value ?? ""} onChange={(e) => set(e.target.value)}>
        <option value="">{field.placeholder || t("events.form.choose")}</option>
        {(field.options || []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  } else if (field.type === "file") {
    const current = value && value.name;
    control = (
      <>
        <input {...common} type="file" disabled={busy}
          accept={(field.validation?.allowedTypes || []).join(",")}
          onChange={async (e) => {
            const file = e.target.files[0];
            setUploadError("");
            if (!file) return set(null);
            if (!uploadFile) return set({ fileId: `local-${file.name}`, name: file.name, size: file.size, contentType: file.type });
            setBusy(true);
            try {
              set(await uploadFile(field, file));
            } catch (err) {
              set(null);
              setUploadError(err.message);
            } finally {
              setBusy(false);
            }
          }} />
        {busy && <small role="status">{t("events.form.uploading")}</small>}
        {current && !busy && <small>{t("events.form.attached", { name: current })}</small>}
        {uploadError && <span className="field-error" role="alert">{uploadError}</span>}
      </>
    );
  } else {
    const type = { email: "email", phone: "tel", number: "number", date: "date" }[field.type] || "text";
    control = (
      <input {...common} type={type} value={value ?? ""} placeholder={field.placeholder}
        autoComplete={field.type === "phone" ? "tel" : undefined} inputMode={field.type === "phone" ? "tel" : undefined}
        min={field.type === "number" || field.type === "date" ? field.validation?.min : undefined}
        max={field.type === "number" || field.type === "date" ? field.validation?.max : undefined}
        maxLength={field.type === "text" ? field.validation?.maxLength : undefined}
        onChange={(e) => set(e.target.value)} />
    );
  }
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {control}
      {help}
      {err}
    </div>
  );
}
