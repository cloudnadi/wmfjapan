import { useEffect, useState } from "react";
import {
  getNpoMembershipConfig,
  saveNpoMembershipConfig,
  getPaymentMethodsConfig,
  savePaymentMethodsConfig,
} from "./api";

const METHODS = [
  { key: "square", label: "Credit / Debit Card (Square)", automatic: true },
  { key: "paypay", label: "PayPay (confirmed manually)", linkHint: "PayPay payment link" },
  { key: "bank", label: "Bank Transfer (confirmed manually)", linkHint: "Optional link" },
];

function toMethodsForm(config) {
  const methods = {};
  for (const { key } of METHODS) {
    const entry = (config.methods && config.methods[key]) || {};
    methods[key] = {
      enabled: entry.enabled === true,
      url: entry.url || "",
      instructions: entry.instructions || "",
    };
  }
  return methods;
}

/**
 * Admin: site-wide payment methods. Square is an automatic hosted checkout
 * (confirmed by webhook); PayPay and bank transfer show the link/instructions
 * entered here to the payer and are confirmed by an admin. Used by the member membership page
 * today; other flows can read the same setting.
 */
export function PaymentMethodsSettings() {
  const [methods, setMethods] = useState(toMethodsForm({}));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getPaymentMethodsConfig()
      .then((config) => setMethods(toMethodsForm(config)))
      .catch((err) => setError(err.message || "Could not load settings"))
      .finally(() => setLoading(false));
  }, []);

  const updateMethod = (key, patch) => {
    setSaved(false);
    setMethods((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  };

  const handleSave = async () => {
    setError("");
    setSaved(false);
    for (const { key, label, automatic } of METHODS) {
      const m = methods[key];
      if (m.enabled && !automatic && !m.url.trim() && !m.instructions.trim()) {
        setError(`${label}: add a payment link or instructions before enabling it.`);
        return;
      }
    }
    setSaving(true);
    try {
      await savePaymentMethodsConfig({ methods });
      setSaved(true);
    } catch (err) {
      setError(err.message || "Could not save settings");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <p className="field-note">Loading…</p>;

  return (
    <section className="partner-panel partner-form-panel">
      <div className="panel-heading">
        <div>
          <h3>Payment Methods</h3>
          <p>
            Site-wide. Square lets members pay right inside the flow and confirms automatically. For PayPay
            and bank transfer, members are shown (and emailed) the link or instructions below, and you
            confirm the payment.
          </p>
        </div>
      </div>

      {METHODS.map(({ key, label, linkHint, automatic }) => {
        const m = methods[key];
        return (
          <div key={key} className="field" style={{ marginTop: 18 }}>
            <label className="checkbox-inline">
              <input
                type="checkbox"
                checked={m.enabled}
                onChange={(e) => updateMethod(key, { enabled: e.target.checked })}
                disabled={saving}
              />{" "}
              <strong>{label}</strong>
            </label>
            {m.enabled && automatic ? (
              <span className="field-note">
                Members complete payment on Square's hosted checkout and their membership starts
                automatically. Needs Square credentials and the webhook configured; it's hidden from
                members until they are.
              </span>
            ) : m.enabled ? (
              <>
                <input
                  type="url"
                  placeholder={linkHint}
                  value={m.url}
                  onChange={(e) => updateMethod(key, { url: e.target.value })}
                  disabled={saving}
                  aria-label={`${label} payment link`}
                />
                <textarea
                  rows={3}
                  placeholder={key === "bank" ? "Bank name, branch, account number, account holder…" : "Instructions shown to the member (optional)"}
                  value={m.instructions}
                  onChange={(e) => updateMethod(key, { instructions: e.target.value })}
                  disabled={saving}
                  aria-label={`${label} instructions`}
                />
              </>
            ) : null}
          </div>
        );
      })}

      {error ? <p className="error-banner">{error}</p> : null}
      {saved ? <p className="success-banner">Saved. Changes are live.</p> : null}

      <div className="btn-row">
        <button type="button" className="button" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save payment methods"}
        </button>
      </div>
    </section>
  );
}

const YEAR_PATTERN = /^\d{4}-\d{2}$/;
// Pre-filled for a year with no saved link (the server falls back to the same link).
const DEFAULT_WHATSAPP_LINKS = {
  "2026-27": "https://chat.whatsapp.com/JLYiFD4voZaLx5RRv6oLGO?s=cl&p=i&mlu=4&ilr=4&iam=2",
};

/** Admin: the NPO membership fee and the WhatsApp group link for each membership year. */
export default function NpoMembershipSettings() {
  const [fee, setFee] = useState("1000");
  // [{ year: "2026-27", url: "https://chat.whatsapp.com/…" }]
  const [links, setLinks] = useState([{ year: "2026-27", url: "" }]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getNpoMembershipConfig()
      .then((config) => {
        setFee(String(config.fee ?? 1000));
        const saved = { ...DEFAULT_WHATSAPP_LINKS, ...(config.whatsappLinks || {}) };
        setLinks(
          Object.entries(saved)
            .sort(([a], [b]) => b.localeCompare(a))
            .map(([year, url]) => ({ year, url }))
        );
      })
      .catch((err) => setError(err.message || "Could not load settings"))
      .finally(() => setLoading(false));
  }, []);

  const touch = () => setSaved(false);
  const updateLink = (index, patch) => {
    touch();
    setLinks((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  const handleSave = async () => {
    setError("");
    setSaved(false);
    const value = Number(fee);
    if (!Number.isInteger(value) || value < 0) {
      setError("Membership fee must be a whole number of yen.");
      return;
    }
    const whatsappLinks = {};
    for (const { year, url } of links) {
      const y = year.trim();
      const u = url.trim();
      if (!y && !u) continue;
      if (!YEAR_PATTERN.test(y)) {
        setError(`"${y || "(blank)"}" isn't a membership year — use the form 2026-27.`);
        return;
      }
      if (!u.startsWith("https://")) {
        setError(`The WhatsApp link for ${y} must start with https:// (e.g. https://chat.whatsapp.com/…).`);
        return;
      }
      if (whatsappLinks[y]) {
        setError(`${y} is listed twice.`);
        return;
      }
      whatsappLinks[y] = u;
    }
    setSaving(true);
    try {
      await saveNpoMembershipConfig({ fee: value, whatsappLinks });
      setSaved(true);
    } catch (err) {
      setError(err.message || "Could not save settings");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <p className="field-note">Loading…</p>;

  return (
    <section className="partner-panel partner-form-panel">
      <div className="panel-heading">
        <div>
          <h3>NPO Membership — Fee &amp; WhatsApp Group</h3>
          <p>Charged when a member joins or renews from the Foundation Membership page. Payment methods are set under Payment Methods.</p>
        </div>
      </div>
      <div className="field">
        <label htmlFor="npoFee">Membership fee (¥)</label>
        <input
          id="npoFee"
          type="number"
          min="0"
          step="1"
          value={fee}
          onChange={(e) => {
            touch();
            setFee(e.target.value);
          }}
          disabled={saving}
        />
      </div>

      <div className="section-label" style={{ marginTop: 22 }}>WhatsApp group link (per membership year)</div>
      <p className="field-note" style={{ marginBottom: 10 }}>
        Added to the "payment received" email once a membership for that year is confirmed, and used by the
        "WhatsApp group invite" announcement in the Members tab. Leave a year blank to send no link.
      </p>
      {links.map((row, index) => (
        <div className="npo-link-row" key={index}>
          <input
            type="text"
            className="npo-link-year"
            placeholder="2026-27"
            aria-label="Membership year"
            value={row.year}
            onChange={(e) => updateLink(index, { year: e.target.value })}
            disabled={saving}
          />
          <input
            type="url"
            placeholder="https://chat.whatsapp.com/…"
            aria-label={`WhatsApp group link for ${row.year || "this year"}`}
            value={row.url}
            onChange={(e) => updateLink(index, { url: e.target.value })}
            disabled={saving}
          />
          <button
            type="button"
            className="button secondary small"
            onClick={() => {
              touch();
              setLinks((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== index) : [{ year: "", url: "" }]));
            }}
            disabled={saving}
            aria-label="Remove this year"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        type="button"
        className="button secondary small"
        onClick={() => {
          touch();
          setLinks((prev) => [...prev, { year: "", url: "" }]);
        }}
        disabled={saving}
      >
        + Add another year
      </button>

      {error ? <p className="error-banner">{error}</p> : null}
      {saved ? <p className="success-banner">Saved.</p> : null}
      <div className="btn-row">
        <button type="button" className="button" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save settings"}
        </button>
      </div>
    </section>
  );
}
