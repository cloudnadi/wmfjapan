import { isoToJstInput, jstInputToIso, t } from "./i18n";

// Form controls shared by the event-management screens.

export function Field({ id, label, error, help, children, className = "" }) {
  return (
    <div className={`field ${className}`}>
      <label htmlFor={id}>{label}</label>
      {children}
      {help && <small id={`${id}-help`}>{help}</small>}
      {error && (
        <span className="field-error" id={`${id}-error`} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export function Input({ id, label, value, onChange, error, help, type = "text", ...rest }) {
  return (
    <Field id={id} label={label} error={error} help={help}>
      <input
        id={id}
        type={type}
        value={value ?? ""}
        className={error ? "input-invalid" : ""}
        aria-invalid={!!error}
        aria-describedby={[help && `${id}-help`, error && `${id}-error`].filter(Boolean).join(" ") || undefined}
        onChange={(e) => onChange(e.target.value)}
        {...rest}
      />
    </Field>
  );
}

export function DateTimeInput({ id, label, value, onChange, error, help }) {
  return (
    <Input
      id={id}
      label={`${label} (JST)`}
      type="datetime-local"
      value={isoToJstInput(value)}
      onChange={(v) => onChange(jstInputToIso(v))}
      error={error}
      help={help}
    />
  );
}

export function Check({ id, label, checked, onChange, help, disabled }) {
  return (
    <div className="field ep-check">
      <label htmlFor={id}>
        <input id={id} type="checkbox" checked={!!checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />{" "}
        {label}
      </label>
      {help && <small>{help}</small>}
    </div>
  );
}

export function ErrorSummary({ message, fields }) {
  const entries = Object.entries(fields || {});
  if (!message && !entries.length) return null;
  return (
    <div className="error-banner" role="alert">
      <strong>{message}</strong>
      {entries.length > 0 && (
        <ul>
          {entries.map(([key, text]) => (
            <li key={key}>
              {key}: {text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
