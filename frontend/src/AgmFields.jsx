import { useEffect, useRef, useState } from "react";
import { checkAgmAdult } from "./api";

/**
 * Shared AGM pieces for the membership page: the event details card (from the
 * admin AGM settings) and the attendee form.
 *
 * Only NPO members may attend the AGM, so adults (18+) are named by email and
 * each one is checked live; the registrant is adult #1. Teenagers, children and
 * toddlers are just counted.
 */

const EMAIL_REGEX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// Age groups other than adults (labels as defined by the committee).
export const YOUNG_GROUPS = [
  { key: "teens", label: "Teenagers", hint: "13–17 years" },
  { key: "kids", label: "Children", hint: "6–12 years" },
  { key: "preschoolers", label: "Preschoolers", hint: "3–5 years" },
  { key: "toddlers", label: "Toddlers", hint: "0–3 years" },
];

export const EMPTY_AGM_COUNTS = {
  attendance: "in_person",
  adultEmails: [], // adults 18+ besides the registrant
  teens: 0,
  kids: 0,
  preschoolers: 0,
  toddlers: 0,
};

/** Everyone attending: the registrant, the named adults and the counted young groups. */
export function agmTotal(counts) {
  const young = YOUNG_GROUPS.reduce((sum, { key }) => sum + (Number(counts?.[key]) || 0), 0);
  return 1 + (counts?.adultEmails?.length || 0) + young;
}

function formatAgmDate(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

export function AgmDetails({ agm }) {
  // On phones only the essentials (when / where) show; access, remote,
  // refreshments and notes sit behind "More details". Wider screens show all.
  const [expanded, setExpanded] = useState(false);
  const time = [agm.startTime, agm.endTime].filter(Boolean).join("–");
  const essentials = [
    ["📅", formatAgmDate(agm.date)],
    ["⏰", time],
    ["📍", [agm.venue, agm.venueJa].filter(Boolean).join(" · ")],
  ].filter(([, text]) => text);
  const extras = [
    ["🚉", agm.access],
    ["💻", agm.remoteNote],
    ["☕", agm.refreshmentsNote],
  ].filter(([, text]) => text);
  const hasMore = extras.length > 0 || !!agm.notes;

  return (
    <div className={`agm-details${expanded ? " agm-details--expanded" : ""}`}>
      <ul className="agm-details-list">
        {essentials.map(([icon, text]) => (
          <li key={icon}>
            <span aria-hidden="true">{icon}</span> {text}
          </li>
        ))}
        {extras.map(([icon, text]) => (
          <li key={icon} className="agm-details-extra">
            <span aria-hidden="true">{icon}</span> {text}
          </li>
        ))}
      </ul>
      {agm.notes ? <p className="agm-details-notes agm-details-extra">{agm.notes}</p> : null}
      <div className="agm-details-actions">
        {agm.mapUrl ? (
          <a className="agm-details-map" href={agm.mapUrl} target="_blank" rel="noopener noreferrer">
            Open in Google Maps →
          </a>
        ) : null}
        {hasMore ? (
          <button
            type="button"
            className="agm-details-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Fewer details" : "More details"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Live NPO check for every additional adult's email (debounced). `skip` lists
 * emails that don't need a check (e.g. a spouse signed up in the same order).
 * → { checks: { [email]: {state, status, message} }, allValid }
 */
export function useAdultChecks(emails, skip = []) {
  const [checks, setChecks] = useState({});
  const requested = useRef({});
  const skipKey = skip.join("|");

  useEffect(() => {
    const timers = [];
    emails.forEach((raw) => {
      const email = raw.trim().toLowerCase();
      if (!email || skip.includes(email) || requested.current[email]) return;
      if (!EMAIL_REGEX.test(email)) return;
      requested.current[email] = true;
      setChecks((prev) => ({ ...prev, [email]: { state: "checking" } }));
      timers.push(
        setTimeout(async () => {
          try {
            const result = await checkAgmAdult(email);
            setChecks((prev) => ({
              ...prev,
              [email]: result.valid
                ? { state: "ok", status: result.status }
                : { state: "error", message: result.message || "This person can't be added." },
            }));
          } catch (err) {
            delete requested.current[email]; // allow a retry on the next edit
            setChecks((prev) => ({ ...prev, [email]: { state: "error", message: err.message } }));
          }
        }, 450)
      );
    });
    return () => timers.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [emails.join("|"), skipKey]);

  const allValid = emails.every((raw) => {
    const email = raw.trim().toLowerCase();
    return skip.includes(email) || checks[email]?.state === "ok";
  });
  return { checks, allValid };
}

export function AgmAttendeeFields({ value, onChange, disabled, checks = {}, lockedAdults = [] }) {
  const set = (key, next) => onChange({ ...value, [key]: next });
  const emails = value.adultEmails || [];
  const total = agmTotal(value);

  const setEmail = (index, text) => set("adultEmails", emails.map((e, i) => (i === index ? text : e)));
  const removeAdult = (index) => set("adultEmails", emails.filter((_, i) => i !== index));
  const addAdult = () => set("adultEmails", [...emails, ""]);

  return (
    <div className="agm-fields">
      <div className="onam-toggle-group" role="radiogroup" aria-label="How will you attend?">
        {[
          ["in_person", "🏛️ In person"],
          ["remote", "💻 Remote"],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={value.attendance === key}
            className={`onam-toggle-pill ${value.attendance === key ? "selected" : ""}`}
            onClick={() => set("attendance", key)}
            disabled={disabled}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="section-label" style={{ marginTop: 20 }}>
        Adults (18+)
      </div>
      <p className="field-note" style={{ marginBottom: 10 }}>
        Only NPO members can attend the AGM. You are counted as the first adult — add each additional adult's
        email so we can check their membership.
      </p>
      <div className="agm-adults">
        <div className="agm-adult-row agm-adult-row--you">
          <span className="agm-adult-who">You</span>
          <span className="field-note mypage-ok">✓ Registrant</span>
        </div>
        {emails.map((email, index) => {
          const key = email.trim().toLowerCase();
          const locked = lockedAdults.includes(key);
          const check = locked ? { state: "ok", status: "pending", included: true } : checks[key];
          return (
            <div className="agm-adult-row" key={index}>
              <input
                type="email"
                inputMode="email"
                autoComplete="off"
                placeholder="adult@email.com"
                aria-label={`Additional adult ${index + 1} email`}
                value={email}
                className={check?.state === "error" ? "input-invalid" : ""}
                onChange={(event) => setEmail(index, event.target.value)}
                disabled={disabled || locked}
              />
              <button
                type="button"
                className="btn-secondary agm-adult-remove"
                onClick={() => removeAdult(index)}
                disabled={disabled}
                aria-label={locked ? "Remove spouse from the AGM" : `Remove adult ${index + 1}`}
                title={locked ? "Remove from the AGM (their membership stays in your order)" : "Remove"}
              >
                ✕
              </button>
              <div className="agm-adult-status">
                {!key ? null : check?.state === "checking" ? (
                  <span className="field-note">Checking NPO membership…</span>
                ) : check?.state === "ok" ? (
                  <span className="field-note mypage-ok">
                    ✓{" "}
                    {check.included
                      ? "Spouse — added with this order (membership payment pending). Remove to leave them out of the AGM."
                      : check.status === "pending"
                        ? "NPO membership payment pending — accepted for now"
                        : "NPO member"}
                  </span>
                ) : check?.state === "error" ? (
                  <span className="field-note mypage-error">{check.message}</span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
      <button type="button" className="btn-secondary agm-adult-add" onClick={addAdult} disabled={disabled || emails.length >= 19}>
        + Add another adult
      </button>

      <div className="section-label" style={{ marginTop: 22 }}>
        Teenagers &amp; children
      </div>
      <p className="field-note" style={{ marginBottom: 10 }}>
        Not required to be members — just tell us how many are coming.
      </p>
      <div className="agm-counts">
        {YOUNG_GROUPS.map(({ key, label, hint }) => (
          <div className="field" key={key}>
            <label htmlFor={`agm-${key}`}>{label}</label>
            <input
              id={`agm-${key}`}
              type="number"
              min="0"
              max="20"
              inputMode="numeric"
              value={value[key]}
              onChange={(event) => set(key, event.target.value === "" ? "" : Math.max(0, Math.min(20, Number(event.target.value))))}
              disabled={disabled}
            />
            <span className="field-note">{hint}</span>
          </div>
        ))}
      </div>
      <p className="field-note">
        {total} {total === 1 ? "person" : "people"} attending {value.attendance === "remote" ? "remotely" : "in person"}.
      </p>
    </div>
  );
}
