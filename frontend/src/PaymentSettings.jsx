import { useEffect, useState } from "react";
import { Check, ErrorSummary, Input } from "./adminFields";
import { getPaymentMethods, savePaymentMethods } from "./eventPlatformApi";
import { t } from "./i18n";

const ORDER = ["square", "bank", "paypay"];

/** Which payment methods attendees see at checkout, and the instructions for the manual ones. */
export default function PaymentSettings({ roles }) {
  const [data, setData] = useState(null);
  const [methods, setMethods] = useState({});
  const [error, setError] = useState({ message: "", fields: {} });
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const isSuper = roles.includes("super_admin");

  useEffect(() => {
    getPaymentMethods()
      .then((d) => { setData(d); setMethods(d.methods); })
      .catch((e) => setError({ message: e.message, fields: {} }));
  }, []);

  const patch = (key, change) => setMethods((m) => ({ ...m, [key]: { ...m[key], ...change } }));
  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    setNotice("");
    setError({ message: "", fields: {} });
    try {
      const d = await savePaymentMethods(methods);
      setData(d);
      setMethods(d.methods);
      setNotice(t("admin.payments.saved"));
    } catch (e) {
      setError({ message: e.message, fields: e.fields || {} });
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <><ErrorSummary message={error.message} fields={{}} />{!error.message && <p>{t("events.loading")}</p>}</>;
  return (
    <form className="ep-card" onSubmit={save}>
      <p className="ep-note">{t("admin.payments.help")}</p>
      {!isSuper && <p className="ep-note" role="status">{t("admin.payments.superOnly")}</p>}
      <ErrorSummary message={error.message} fields={error.fields} />
      {notice && <div className="success-banner" role="status">{notice}</div>}
      <fieldset disabled={!isSuper} className="ep-fieldset">
        {ORDER.map((key) => (
          <section key={key} className="ep-addon" aria-labelledby={`pay-${key}-h`}>
            <h3 id={`pay-${key}-h`}>{data.labels[key]}</h3>
            <Check id={`pay-${key}-on`} label={t("admin.payments.enable")} checked={methods[key]?.enabled} onChange={(v) => patch(key, { enabled: v })} />
            {key === "square" && !data.cardConfigured && <p className="ep-note">{t("admin.payments.cardNotConfigured")}</p>}
            {key !== "square" && (
              <>
                <Input id={`pay-${key}-url`} label={t("admin.payments.link")} type="url" value={methods[key]?.url || ""} onChange={(v) => patch(key, { url: v })} error={error.fields[`${key}.url`]} />
                <div className="field">
                  <label htmlFor={`pay-${key}-ins`}>{t("admin.payments.instructions")}</label>
                  <textarea id={`pay-${key}-ins`} rows={4} maxLength={2000} value={methods[key]?.instructions || ""} onChange={(e) => patch(key, { instructions: e.target.value })} />
                </div>
              </>
            )}
          </section>
        ))}
      </fieldset>
      {isSuper && <div className="btn-row"><button type="submit" className="button primary" disabled={busy}>{t("events.save")}</button></div>}
    </form>
  );
}
