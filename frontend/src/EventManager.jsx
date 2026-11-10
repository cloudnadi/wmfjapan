import { useCallback, useEffect, useMemo, useState } from "react";
import "./eventPlatform.css";
import AdminLogin from "./AdminLogin";
import PaymentSettings from "./PaymentSettings";
import { hasAdminSession } from "./adminSession";
import { logout } from "./cognito";
import { formatJpy, formatJst, isoToJstInput, jstInputToIso, t } from "./i18n";
import EventAddons from "./EventAddons";
import EventRegistrations from "./EventRegistrations";
import { Check, DateTimeInput, ErrorSummary, Field, Input } from "./adminFields";
import FormBuilder from "./FormBuilder";
import Navbar from "./Navbar";
import RichTextEditor from "./RichTextEditor";
import {
  createEvent,
  createTicket,
  deleteEvent,
  deleteTicket,
  duplicateEvent,
  getEvent,
  getEventAudit,
  getMe,
  listEvents,
  setEventStatus,
  updateEvent,
  updateTicket,
  uploadEventImage,
} from "./eventPlatformApi";

const STATUSES = ["draft", "published", "unlisted", "closed", "archived"];
const LAYOUTS = ["classic", "hero", "minimal"];
const PAGE_SECTIONS = ["agenda", "speakers", "sponsors", "faq", "contact"];
const PLACEHOLDERS = ["name", "event_title", "ticket_type", "registration_id", "event_date", "venue", "event_url"];
const TABS = ["basics", "schedule", "location", "design", "tickets", "addons", "form", "registrations", "access", "emails", "history"];
const EDITABLE = [
  "slug", "title", "shortDescription", "longDescription", "category", "tags", "startsAt", "endsAt",
  "registrationOpensAt", "registrationClosesAt", "capacity", "waitlistEnabled", "location", "design", "access", "emails",
  "capabilities", "slots", "upgrades", "seatMap",
];
const MAX_IMAGE_BYTES = 3 * 1024 * 1024;
const NEW_EVENT = { title: "", slug: "", status: "draft", revision: 0 };

const WHO_OPTIONS = [
  { key: "open", guestsAllowed: true },
  { key: "signin", guestsAllowed: false },
];
const whoOf = (access) => (access.guestsAllowed === false ? "signin" : "open");

const slugify = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

function withDefaults(event) {
  const sections = PAGE_SECTIONS.map(
    (key) => (event.design?.sections || []).find((s) => s.key === key) || { key, enabled: false }
  );
  const ordered = [
    ...(event.design?.sections || []).filter((s) => PAGE_SECTIONS.includes(s.key)),
    ...sections.filter((s) => !(event.design?.sections || []).some((o) => o.key === s.key)),
  ];
  return {
    ...event,
    tags: event.tags || [],
    location: event.location || {},
    emails: event.emails || {},
    waitlistEnabled: !!event.waitlistEnabled,
    access: { guestsAllowed: true, ...(event.access || {}) },
    capabilities: event.capabilities || {},
    slots: event.slots || [],
    upgrades: event.upgrades || [],
    seatMap: event.seatMap || { sections: [] },
    design: { layout: "classic", accentColor: "#1a7f5a", gallery: [], ...(event.design || {}), sections: ordered },
  };
}

function setIn(obj, path, value) {
  const [head, ...rest] = path.split(".");
  return { ...obj, [head]: rest.length ? setIn(obj[head] || {}, rest.join("."), value) : value };
}

function editablePayload(draft) {
  const payload = {};
  EDITABLE.forEach((key) => {
    payload[key] = draft[key] === undefined ? null : draft[key];
  });
  payload.capacity = draft.capacity === "" || draft.capacity === undefined ? null : Number(draft.capacity);
  return payload;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(new Error("read"));
    reader.readAsDataURL(file);
  });
}

// Client-side allow-list for the preview only; the server sanitises what is saved.
const ALLOWED = new Set(["P", "BR", "STRONG", "B", "EM", "I", "U", "UL", "OL", "LI", "H2", "H3", "H4", "BLOCKQUOTE", "A"]);
function previewHtml(html) {
  const doc = new DOMParser().parseFromString(`<div>${html || ""}</div>`, "text/html");
  const clean = (node) => {
    [...node.childNodes].forEach((child) => {
      if (child.nodeType === 1) {
        if (!ALLOWED.has(child.tagName)) {
          child.replaceWith(...child.childNodes);
          return;
        }
        [...child.attributes].forEach((a) => {
          const safeLink = child.tagName === "A" && a.name === "href" && /^(https?:\/\/|mailto:)/i.test(a.value);
          if (!safeLink) child.removeAttribute(a.name);
        });
        clean(child);
      } else if (child.nodeType !== 3) {
        child.remove();
      }
    });
  };
  doc.body.querySelectorAll("script,style,iframe,object,embed").forEach((el) => el.remove());
  clean(doc.body.firstChild);
  return doc.body.firstChild.innerHTML;
}

function StatusBadge({ status }) {
  return <span className={`ep-badge ep-badge-${status}`}>{t(`events.status.${status}`)}</span>;
}

// ── Preview ───────────────────────────────────────────────────────────────────

function EventPreview({ event, tickets }) {
  const d = event.design;
  const enabled = d.sections.filter((s) => s.enabled);
  const place = [event.location?.venueName, event.location?.address].filter(Boolean).join(", ");
  return (
    <article className={`ep-preview ep-layout-${d.layout}`} style={{ "--ep-accent": d.accentColor }} aria-label={t("events.preview.title")}>
      {d.coverImage ? (
        <img className="ep-preview-cover" src={d.coverImage} alt="" />
      ) : (
        <div className="ep-preview-cover ep-preview-cover-empty" aria-hidden="true" />
      )}
      <div className="ep-preview-body">
        <h2>{event.title || t("events.preview.untitled")}</h2>
        {event.category && <p className="ep-preview-category">{event.category}</p>}
        <p>
          {event.startsAt ? formatJst(event.startsAt) : t("events.preview.noDate")}
          {event.endsAt ? ` – ${formatJst(event.endsAt)}` : ""}
        </p>
        {place && <p>{place}</p>}
        {event.location?.onlineUrl && <p className="ep-note">{t("events.preview.onlineHidden")}</p>}
        {event.shortDescription && <p className="ep-preview-lead">{event.shortDescription}</p>}
        {/* eslint-disable-next-line react/no-danger */}
        <div dangerouslySetInnerHTML={{ __html: previewHtml(event.longDescription) }} />
        {tickets.length > 0 && (
          <ul className="ep-preview-tickets">
            {tickets.filter((x) => x.active !== false).map((x) => (
              <li key={x.ticketTypeId}>
                {x.name}: {formatJpy(x.priceJpy)}
              </li>
            ))}
          </ul>
        )}
        {enabled.map((s) => (
          <section key={s.key} className="ep-preview-section">
            <h3>{t(`events.sections.${s.key}`)}</h3>
          </section>
        ))}
        <button type="button" className="button primary" disabled style={{ background: d.accentColor }}>
          {t("events.preview.register")}
        </button>
      </div>
    </article>
  );
}

// ── Tickets ───────────────────────────────────────────────────────────────────

const EMPTY_TICKET = { name: "", description: "", priceJpy: "", earlyBirdPriceJpy: "",
  earlyBirdCutoff: "", quantityLimit: "", perPersonCap: "", sortOrder: 0, active: true };

function numOrNull(value) {
  return value === "" || value === null || value === undefined ? null : Number(value);
}

function TicketsPanel({ eventId, tickets, setTickets, readOnly, hasRegistrations }) {
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState({ message: "", fields: {} });
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    setError({ message: "", fields: {} });
    const body = {
      ...editing,
      priceJpy: numOrNull(editing.priceJpy),
      earlyBirdPriceJpy: numOrNull(editing.earlyBirdPriceJpy),
      earlyBirdCutoff: editing.earlyBirdCutoff || null,
      quantityLimit: numOrNull(editing.quantityLimit),
      perPersonCap: numOrNull(editing.perPersonCap),
      sortOrder: Number(editing.sortOrder) || 0,
    };
    try {
      const res = editing.ticketTypeId
        ? await updateTicket(eventId, editing.ticketTypeId, body)
        : await createTicket(eventId, body);
      setTickets((list) =>
        editing.ticketTypeId
          ? list.map((x) => (x.ticketTypeId === res.ticket.ticketTypeId ? res.ticket : x))
          : [...list, res.ticket]
      );
      setEditing(null);
    } catch (err) {
      setError({ message: err.message, fields: err.fields || {} });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (ticket) => {
    if (!window.confirm(t("events.tickets.confirmDelete", { name: ticket.name }))) return;
    try {
      await deleteTicket(eventId, ticket.ticketTypeId);
      setTickets((list) => list.filter((x) => x.ticketTypeId !== ticket.ticketTypeId));
    } catch (err) {
      setError({ message: err.message, fields: {} });
    }
  };

  const f = (key) => error.fields[key];
  const set = (key) => (value) => setEditing((cur) => ({ ...cur, [key]: value }));

  return (
    <section aria-labelledby="ep-tickets-h">
      <h3 id="ep-tickets-h">{t("events.tickets.title")}</h3>
      <p className="ep-note">{t("events.tickets.help")}</p>
      <ErrorSummary message={editing ? "" : error.message} fields={{}} />
      <div className="table-container">
        <table className="registrations-table ep-table">
          <thead>
            <tr>
              <th>{t("events.tickets.name")}</th>
              <th>{t("events.tickets.price")}</th>
              <th>{t("events.tickets.earlyBird")}</th>
              <th>{t("events.tickets.limit")}</th>
              <th>{t("events.tickets.cap")}</th>
              <th>{t("events.col.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {tickets.length === 0 && (
              <tr>
                <td colSpan={6}>{t("events.tickets.none")}</td>
              </tr>
            )}
            {tickets.map((x) => (
              <tr key={x.ticketTypeId} className={x.active === false ? "ep-inactive" : ""}>
                <td data-label={t("events.tickets.name")}>
                  {x.name} {x.active === false && <em>({t("events.tickets.inactive")})</em>}
                </td>
                <td data-label={t("events.tickets.price")}>{formatJpy(x.priceJpy)}</td>
                <td data-label={t("events.tickets.earlyBird")}>
                  {x.earlyBirdPriceJpy != null
                    ? `${formatJpy(x.earlyBirdPriceJpy)} → ${formatJst(x.earlyBirdCutoff)}`
                    : "—"}
                </td>
                <td data-label={t("events.tickets.limit")}>{x.quantityLimit ?? "—"}</td>
                <td data-label={t("events.tickets.cap")}>{x.perPersonCap ?? "—"}</td>
                <td data-label={t("events.col.actions")}>
                  {!readOnly && (
                    <div className="btn-row">
                      <button type="button" className="button secondary" onClick={() => { setError({ message: "", fields: {} }); setEditing({ ...EMPTY_TICKET, ...x }); }}>
                        {t("events.edit")}
                      </button>
                      <button type="button" className="button secondary" onClick={() => remove(x)} disabled={hasRegistrations} title={hasRegistrations ? t("events.tickets.deactivateHint") : undefined}>
                        {t("events.delete")}
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!readOnly && !editing && (
        <button type="button" className="button primary" onClick={() => { setError({ message: "", fields: {} }); setEditing({ ...EMPTY_TICKET, sortOrder: tickets.length }); }}>
          {t("events.tickets.add")}
        </button>
      )}
      {editing && (
        <form className="ep-card" onSubmit={(e) => { e.preventDefault(); save(); }} aria-label={t("events.tickets.formLabel")}>
          <ErrorSummary message={error.message} fields={{}} />
          <div className="field-row">
            <Input id="tk-name" label={t("events.tickets.name")} value={editing.name} onChange={set("name")} error={f("name")} required />
            <Input id="tk-desc" label={t("events.tickets.description")} value={editing.description} onChange={set("description")} />
          </div>
          <div className="field-row">
            <Input id="tk-price" label={t("events.tickets.priceJpy")} type="number" min="0" step="1" value={editing.priceJpy} onChange={set("priceJpy")} error={f("priceJpy")} required />
          </div>
          <div className="field-row">
            <Input id="tk-eb" label={t("events.tickets.earlyBirdPrice")} type="number" min="0" step="1" value={editing.earlyBirdPriceJpy ?? ""} onChange={set("earlyBirdPriceJpy")} error={f("earlyBirdPriceJpy")} help={t("events.tickets.earlyBirdHelp")} />
            <DateTimeInput id="tk-ebc" label={t("events.tickets.earlyBirdCutoff")} value={editing.earlyBirdCutoff} onChange={set("earlyBirdCutoff")} error={f("earlyBirdCutoff")} />
          </div>
          <div className="field-row">
            <Input id="tk-limit" label={t("events.tickets.limit")} type="number" min="1" step="1" value={editing.quantityLimit ?? ""} onChange={set("quantityLimit")} error={f("quantityLimit")} />
            <Input id="tk-cap" label={t("events.tickets.cap")} type="number" min="1" step="1" value={editing.perPersonCap ?? ""} onChange={set("perPersonCap")} error={f("perPersonCap")} />
            <Input id="tk-order" label={t("events.tickets.order")} type="number" step="1" value={editing.sortOrder} onChange={set("sortOrder")} />
          </div>
          <Check id="tk-active" label={t("events.tickets.active")} checked={editing.active} onChange={set("active")} />
          <div className="btn-row">
            <button type="submit" className="button primary" disabled={busy}>
              {t("events.save")}
            </button>
            <button type="button" className="button secondary" onClick={() => setEditing(null)}>
              {t("events.cancel")}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

// ── Design ────────────────────────────────────────────────────────────────────

function DesignPanel({ draft, set, eventId, readOnly, errors }) {
  const [busy, setBusy] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const d = draft.design;

  const upload = async (file, onDone) => {
    setUploadError("");
    if (!file) return;
    if (file.size > MAX_IMAGE_BYTES) return setUploadError(t("events.design.tooLarge"));
    setBusy(true);
    try {
      const res = await uploadEventImage(eventId, file.type, await fileToBase64(file));
      onDone(res.url);
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const moveSection = (index, delta) => {
    const next = [...d.sections];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    set("design.sections", next);
  };

  return (
    <fieldset disabled={readOnly} className="ep-fieldset">
      <legend>{t("events.tab.design")}</legend>
      <ErrorSummary message={uploadError} fields={{}} />
      <div className="field">
        <label htmlFor="ep-cover">{t("events.design.cover")}</label>
        {d.coverImage && <img className="ep-thumb" src={d.coverImage} alt={t("events.design.coverAlt")} />}
        <input id="ep-cover" type="file" accept="image/jpeg,image/png,image/webp" disabled={busy}
          onChange={(e) => { upload(e.target.files[0], (url) => set("design.coverImage", url)); e.target.value = ""; }} />
        <small>{t("events.design.imageHelp")}</small>
        {d.coverImage && (
          <button type="button" className="button secondary" onClick={() => set("design.coverImage", "")}>
            {t("events.design.removeCover")}
          </button>
        )}
      </div>
      <div className="field">
        <label htmlFor="ep-gallery">{t("events.design.gallery")}</label>
        <div className="ep-gallery">
          {d.gallery.map((url, i) => (
            <figure key={url}>
              <img className="ep-thumb" src={url} alt={t("events.design.galleryAlt", { n: i + 1 })} />
              <button type="button" className="button secondary" onClick={() => set("design.gallery", d.gallery.filter((u) => u !== url))}>
                {t("events.design.remove")}
              </button>
            </figure>
          ))}
        </div>
        <input id="ep-gallery" type="file" accept="image/jpeg,image/png,image/webp" disabled={busy || d.gallery.length >= 12}
          onChange={(e) => { upload(e.target.files[0], (url) => set("design.gallery", [...d.gallery, url])); e.target.value = ""; }} />
        {errors["design.gallery"] && <span className="field-error" role="alert">{errors["design.gallery"]}</span>}
      </div>
      <div className="field-row">
        <Input id="ep-accent" label={t("events.design.accent")} type="color" value={d.accentColor} onChange={(v) => set("design.accentColor", v)} error={errors["design.accentColor"]} />
        <Field id="ep-layout" label={t("events.design.layout")} help={t(`events.layout.${d.layout}.help`)}>
          <select id="ep-layout" value={d.layout} onChange={(e) => set("design.layout", e.target.value)}>
            {LAYOUTS.map((l) => (
              <option key={l} value={l}>
                {t(`events.layout.${l}`)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <h4 id="ep-sections-h">{t("events.design.sections")}</h4>
      <p className="ep-note">{t("events.design.sectionsHelp")}</p>
      <ol className="ep-sections" aria-labelledby="ep-sections-h">
        {d.sections.map((s, i) => (
          <li key={s.key} className="ep-section-item">
            <Check id={`ep-sec-${s.key}`} label={t(`events.sections.${s.key}`)} checked={s.enabled}
              onChange={(v) => set("design.sections", d.sections.map((o) => (o.key === s.key ? { ...o, enabled: v } : o)))} />
            <button type="button" className="button secondary" onClick={() => moveSection(i, -1)} disabled={i === 0}
              aria-label={t("events.design.moveUp", { name: t(`events.sections.${s.key}`) })}>↑</button>
            <button type="button" className="button secondary" onClick={() => moveSection(i, 1)} disabled={i === d.sections.length - 1}
              aria-label={t("events.design.moveDown", { name: t(`events.sections.${s.key}`) })}>↓</button>
            {s.enabled && (
              <div className="ep-section-editor">
                <RichTextEditor id={`ep-sec-content-${s.key}`} label={t("events.design.sectionContent", { name: t(`events.sections.${s.key}`) })}
                  value={s.content} onChange={(v) => set("design.sections", d.sections.map((o) => (o.key === s.key ? { ...o, content: v } : o)))} />
              </div>
            )}
          </li>
        ))}
      </ol>
    </fieldset>
  );
}

// ── History ───────────────────────────────────────────────────────────────────

function HistoryPanel({ eventId }) {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getEventAudit(eventId).then((r) => setEntries(r.entries)).catch((e) => setError(e.message));
  }, [eventId]);
  if (error) return <ErrorSummary message={error} fields={{}} />;
  if (!entries) return <p>{t("events.loading")}</p>;
  if (!entries.length) return <p>{t("events.history.none")}</p>;
  return (
    <ul className="ep-history">
      {entries.map((e) => (
        <li key={e.sortKey}>
          <strong>{formatJst(e.at)}</strong> — {e.actor}: {e.summary}
        </li>
      ))}
    </ul>
  );
}

// ── Editor ────────────────────────────────────────────────────────────────────

function EventEditor({ eventId, roles, onBack, onOpen }) {
  const isNew = !eventId;
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState(() => withDefaults(NEW_EVENT));
  const [tickets, setTickets] = useState([]);
  const [hasRegistrations, setHasRegistrations] = useState(false);
  const [tab, setTab] = useState("basics");
  const [showPreview, setShowPreview] = useState(false);
  const [slugTouched, setSlugTouched] = useState(false);
  const [loading, setLoading] = useState(!isNew);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState({ message: "", fields: {} });
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getEvent(eventId);
      const ev = withDefaults(res.event);
      setSaved(ev);
      setDraft(ev);
      setTickets(res.tickets);
      setHasRegistrations(res.hasRegistrations);
    } catch (err) {
      setError({ message: err.message, fields: {} });
    } finally {
      setLoading(false);
    }
  }, [eventId]);

  useEffect(() => {
    if (!isNew) load();
  }, [isNew, load]);

  const dirty = useMemo(
    () => JSON.stringify(editablePayload(draft)) !== JSON.stringify(saved ? editablePayload(saved) : editablePayload(withDefaults(NEW_EVENT))),
    [draft, saved]
  );
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const readOnly = false;
  const isSuper = roles.includes("super_admin");
  const set = (path, value) => setDraft((cur) => setIn(cur, path, value));

  const save = async () => {
    setBusy(true);
    setError({ message: "", fields: {} });
    setNotice("");
    try {
      if (isNew) {
        const res = await createEvent(editablePayload(draft));
        onOpen(res.event.eventId);
        return;
      }
      const res = await updateEvent(eventId, { ...editablePayload(draft), revision: saved.revision });
      const ev = withDefaults(res.event);
      setSaved(ev);
      setDraft(ev);
      setNotice(t("events.saved"));
    } catch (err) {
      setError({ message: err.message, fields: err.fields || {} });
    } finally {
      setBusy(false);
    }
  };

  const changeStatus = async (status) => {
    if (dirty && !window.confirm(t("events.unsavedConfirm"))) return;
    setBusy(true);
    setError({ message: "", fields: {} });
    setNotice("");
    try {
      const res = await setEventStatus(eventId, status);
      await load();
      setNotice(t("events.statusChanged", { status: t(`events.status.${res.event.status}`) }));
    } catch (err) {
      setError({ message: err.message, fields: err.fields || {} });
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(t("events.confirmDelete", { title: draft.title }))) return;
    try {
      await deleteEvent(eventId);
      onBack();
    } catch (err) {
      setError({ message: err.message, fields: {} });
    }
  };

  const back = () => {
    if (dirty && !window.confirm(t("events.unsavedConfirm"))) return;
    onBack();
  };

  if (loading) return <p>{t("events.loading")}</p>;
  const errors = error.fields;
  // Images and ticket types belong to a saved event, so a new one starts without those tabs.
  const tabs = isNew ? ["basics", "schedule", "location", "access", "emails"] : TABS;

  return (
    <div className="ep-editor">
      <div className="ep-toolbar">
        <button type="button" className="button secondary" onClick={back}>
          ← {t("events.back")}
        </button>
        <h2>{isNew ? t("events.new") : draft.title || t("events.preview.untitled")}</h2>
        {!isNew && <StatusBadge status={draft.status} />}
        <div className="ep-spacer" />
        <button type="button" className="button secondary" onClick={() => setShowPreview((v) => !v)} aria-pressed={showPreview}>
          {t("events.preview.toggle")}
        </button>
        {!readOnly && (
          <button type="button" className="button primary" onClick={save} disabled={busy || (!isNew && !dirty)}>
            {isNew ? t("events.create") : t("events.save")}
          </button>
        )}
      </div>

      <ErrorSummary message={error.message} fields={errors} />
      {notice && <div className="success-banner" role="status">{notice}</div>}

      {!isNew && !readOnly && (
        <div className="ep-status-row" role="group" aria-label={t("events.visibility")}>
          <span>{t("events.visibility")}:</span>
          {STATUSES.filter((s) => s !== draft.status).map((s) => (
            <button key={s} type="button" className="button secondary" onClick={() => changeStatus(s)} disabled={busy}
              title={t(`events.status.${s}.help`)}>
              {t(`events.action.${s}`)}
            </button>
          ))}
          {isSuper && (
            <button type="button" className="button secondary" onClick={remove} disabled={busy || hasRegistrations}
              title={hasRegistrations ? t("events.deleteBlocked") : undefined}>
              {t("events.delete")}
            </button>
          )}
        </div>
      )}

      <div className="admin-tabs" role="tablist" aria-label={t("events.sections.label")}>
        {tabs.map((id) => (
          <button key={id} type="button" role="tab" id={`ep-tab-${id}`} aria-selected={tab === id} aria-controls="ep-panel"
            className={`admin-tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>
            {t(`events.tab.${id}`)}
          </button>
        ))}
      </div>

      <div className={`ep-layout ${showPreview ? "with-preview" : ""}`}>
        <div id="ep-panel" role="tabpanel" aria-labelledby={`ep-tab-${tab}`} className="ep-panel">
          {tab === "basics" && (
            <fieldset disabled={readOnly} className="ep-fieldset">
              <legend>{t("events.tab.basics")}</legend>
              <Input id="ep-title" label={t("events.title")} value={draft.title} required error={errors.title}
                onChange={(v) => { set("title", v); if (isNew && !slugTouched) set("slug", slugify(v)); }} />
              <Input id="ep-slug" label={t("events.slug")} value={draft.slug} error={errors.slug} help={t("events.slugHelp", { slug: draft.slug || "…" })}
                onChange={(v) => { setSlugTouched(true); set("slug", slugify(v)); }} />
              <Input id="ep-short" label={t("events.shortDescription")} value={draft.shortDescription} error={errors.shortDescription} maxLength={300}
                onChange={(v) => set("shortDescription", v)} />
              <RichTextEditor id="ep-long" label={t("events.longDescription")} value={draft.longDescription} onChange={(v) => set("longDescription", v)} />
              <div className="field-row">
                <Input id="ep-cat" label={t("events.category")} value={draft.category} onChange={(v) => set("category", v)} />
                <Input id="ep-tags" label={t("events.tags")} value={draft.tags.join(", ")} help={t("events.tagsHelp")} error={errors.tags}
                  onChange={(v) => set("tags", v.split(",").map((x) => x.trim()).filter(Boolean))} />
              </div>
            </fieldset>
          )}

          {tab === "schedule" && (
            <fieldset disabled={readOnly} className="ep-fieldset">
              <legend>{t("events.tab.schedule")}</legend>
              <p className="ep-note">{t("events.scheduleHelp")}</p>
              <div className="field-row">
                <DateTimeInput id="ep-start" label={t("events.startsAt")} value={draft.startsAt} onChange={(v) => set("startsAt", v)} error={errors.startsAt} />
                <DateTimeInput id="ep-end" label={t("events.endsAt")} value={draft.endsAt} onChange={(v) => set("endsAt", v)} error={errors.endsAt} help={t("events.multiDayHelp")} />
              </div>
              <div className="field-row">
                <DateTimeInput id="ep-ropen" label={t("events.regOpens")} value={draft.registrationOpensAt} onChange={(v) => set("registrationOpensAt", v)} error={errors.registrationOpensAt} />
                <DateTimeInput id="ep-rclose" label={t("events.regCloses")} value={draft.registrationClosesAt} onChange={(v) => set("registrationClosesAt", v)} error={errors.registrationClosesAt} />
              </div>
              <Input id="ep-cap" label={t("events.capacity")} type="number" min="0" step="1" value={draft.capacity ?? ""} error={errors.capacity}
                help={t("events.capacityHelp")} onChange={(v) => set("capacity", v)} />
              <Check id="ep-wait" label={t("events.waitlist")} checked={draft.waitlistEnabled} onChange={(v) => set("waitlistEnabled", v)} help={t("events.waitlistHelp")} />
            </fieldset>
          )}

          {tab === "location" && (
            <fieldset disabled={readOnly} className="ep-fieldset">
              <legend>{t("events.tab.location")}</legend>
              <Input id="ep-venue" label={t("events.venue")} value={draft.location.venueName} onChange={(v) => set("location.venueName", v)} />
              <Input id="ep-addr" label={t("events.address")} value={draft.location.address} onChange={(v) => set("location.address", v)} />
              <Input id="ep-map" label={t("events.mapUrl")} type="url" value={draft.location.mapUrl} error={errors["location.mapUrl"]} onChange={(v) => set("location.mapUrl", v)} />
              <Input id="ep-online" label={t("events.onlineUrl")} type="url" value={draft.location.onlineUrl} error={errors["location.onlineUrl"]}
                help={t("events.onlineHelp")} onChange={(v) => set("location.onlineUrl", v)} />
            </fieldset>
          )}

          {tab === "design" && <DesignPanel draft={draft} set={set} eventId={eventId} readOnly={readOnly} errors={errors} />}

          {tab === "tickets" && (
            <TicketsPanel eventId={eventId} tickets={tickets} setTickets={setTickets} readOnly={readOnly} hasRegistrations={hasRegistrations} />
          )}

          {tab === "addons" && <EventAddons draft={draft} set={set} readOnly={readOnly} errors={errors} />}

          {tab === "form" && <FormBuilder eventId={eventId} roles={roles} readOnly={readOnly} />}

          {tab === "registrations" && <EventRegistrations eventId={eventId} event={saved} tickets={tickets} roles={roles} />}

          {tab === "access" && (
            <fieldset disabled={readOnly} className="ep-fieldset">
              <legend>{t("events.tab.access")}</legend>
              <div role="radiogroup" aria-labelledby="ep-who-h" className="ep-who">
                <p id="ep-who-h" className="ep-who-title">{t("events.who.title")}</p>
                {WHO_OPTIONS.map((opt) => (
                  <label key={opt.key} className={`ep-who-option ${whoOf(draft.access) === opt.key ? "selected" : ""}`}>
                    <input type="radio" name="ep-who" value={opt.key} checked={whoOf(draft.access) === opt.key}
                      onChange={() => set("access.guestsAllowed", opt.guestsAllowed)} />
                    <span><strong>{t(`events.who.${opt.key}`)}</strong><small>{t(`events.who.${opt.key}Help`)}</small></span>
                  </label>
                ))}
              </div>
              {errors["access.guestsAllowed"] && <span className="field-error" role="alert">{errors["access.guestsAllowed"]}</span>}
            </fieldset>
          )}

          {tab === "emails" && (
            <fieldset disabled={readOnly} className="ep-fieldset">
              <legend>{t("events.tab.emails")}</legend>
              <p className="ep-note">
                {t("events.emails.placeholders")} {PLACEHOLDERS.map((p) => `{${p}}`).join("  ")}
              </p>
              {["confirmation", "reminder"].map((kind) => (
                <div key={kind} className="ep-card">
                  <h4>{t(`events.emails.${kind}`)}</h4>
                  <Input id={`ep-${kind}-subject`} label={t("events.emails.subject")} value={draft.emails[kind]?.subject} error={errors[`emails.${kind}`]}
                    onChange={(v) => set(`emails.${kind}`, { ...(draft.emails[kind] || {}), subject: v })} />
                  <Field id={`ep-${kind}-body`} label={t("events.emails.body")}>
                    <textarea id={`ep-${kind}-body`} rows={6} value={draft.emails[kind]?.body || ""}
                      onChange={(e) => set(`emails.${kind}`, { ...(draft.emails[kind] || {}), body: e.target.value })} />
                  </Field>
                </div>
              ))}
            </fieldset>
          )}

          {tab === "history" && <HistoryPanel eventId={eventId} />}
        </div>
        {showPreview && <EventPreview event={draft} tickets={tickets} />}
      </div>
    </div>
  );
}

// ── List ──────────────────────────────────────────────────────────────────────

function EventList({ onOpen, onNew, roles }) {
  const [events, setEvents] = useState(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");

  const load = useCallback(() => {
    setError("");
    listEvents(filter).then((r) => setEvents(r.events)).catch((e) => setError(e.message));
  }, [filter]);
  useEffect(load, [load]);

  const act = async (id, fn) => {
    setBusyId(id);
    try {
      await fn();
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId("");
    }
  };

  return (
    <div>
      <div className="ep-toolbar">
        <h2>{t("events.title.list")}</h2>
        <div className="ep-spacer" />
        <label htmlFor="ep-filter" className="ep-inline-label">{t("events.filterStatus")}</label>
        <select id="ep-filter" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">{t("events.all")}</option>
          {STATUSES.map((s) => <option key={s} value={s}>{t(`events.status.${s}`)}</option>)}
        </select>
        <button type="button" className="button primary" onClick={onNew}>{t("events.new")}</button>
      </div>
      <ErrorSummary message={error} fields={{}} />
      {!events && !error && <p>{t("events.loading")}</p>}
      {events && events.length === 0 && <p>{t("events.none")}</p>}
      {events && events.length > 0 && (
        <div className="table-container">
          <table className="registrations-table ep-table">
            <thead>
              <tr>
                <th>{t("events.title")}</th>
                <th>{t("events.col.status")}</th>
                <th>{t("events.col.when")}</th>
                <th>{t("events.col.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev) => (
                <tr key={ev.eventId}>
                  <td data-label={t("events.title")}>
                    <button type="button" className="ep-link" onClick={() => onOpen(ev.eventId)}>{ev.title}</button>
                    <br /><small>/{ev.slug}</small>
                  </td>
                  <td data-label={t("events.col.status")}><StatusBadge status={ev.status} /></td>
                  <td data-label={t("events.col.when")}>{ev.startsAt ? formatJst(ev.startsAt) : "—"}</td>
                  <td data-label={t("events.col.actions")}>
                    <div className="btn-row">
                      <button type="button" className="button secondary" onClick={() => onOpen(ev.eventId)}>
                        {t("events.edit")}
                      </button>
                      <button type="button" className="button secondary" disabled={busyId === ev.eventId}
                        onClick={() => act(ev.eventId, () => duplicateEvent(ev.eventId))}>{t("events.duplicate")}</button>
                      {ev.status !== "archived" && (
                        <button type="button" className="button secondary" disabled={busyId === ev.eventId}
                          onClick={() => act(ev.eventId, () => setEventStatus(ev.eventId, "archived"))}>{t("events.action.archived")}</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="ep-note">{t("events.rolesNote", { roles: roles.map((r) => t(`events.role.${r}`)).join(", ") })}</p>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function EventManager({ navigateTo }) {
  const [view, setView] = useState({ name: "list" });
  const [roles, setRoles] = useState(null);
  const [error, setError] = useState("");
  const [signedIn, setSignedIn] = useState(() => hasAdminSession());

  useEffect(() => {
    if (!signedIn) return;
    getMe().then((r) => setRoles(r.roles)).catch((e) => {
      if (e.status === 401) setSignedIn(false);
      else setError(e.status === 403 ? t("events.noAccess") : e.message);
    });
  }, [signedIn]);

  const signOut = () => {
    logout();
    setRoles(null);
    setSignedIn(false);
    setView({ name: "list" });
  };

  if (!signedIn) {
    return (
      <>
        <Navbar navigateTo={navigateTo} brand={t("admin.brand")} showUserMenu={false} links={[]} />
        <main className="page-wrap">
          <AdminLogin onSignedIn={() => { setError(""); setSignedIn(true); }} />
        </main>
      </>
    );
  }

  const links = [
    { label: t("admin.nav.events"), onClick: () => setView({ name: "list" }) },
    { label: t("admin.nav.checkIn"), onClick: () => navigateTo("/admin/check-in") },
    { label: t("admin.nav.payments"), onClick: () => setView({ name: "payments" }) },
    { label: t("admin.nav.viewSite"), onClick: () => navigateTo("/") },
    { label: t("site.nav.signOut"), onClick: signOut },
  ];

  return (
    <>
    <Navbar navigateTo={navigateTo} brand={t("admin.brand")} showUserMenu={false} links={links} />
    <main className="page-wrap">
    <section className="form-card admin-dashboard">
      <div className="admin-header">
        <h2>{view.name === "payments" ? t("admin.payments.title") : t("events.page.title")}</h2>
        {roles && <span className="ep-note">{roles.map((r) => t(`events.role.${r}`)).join(", ")}</span>}
      </div>
      {error ? (
        <ErrorSummary message={error} fields={{}} />
      ) : !roles ? (
        <p>{t("events.loading")}</p>
      ) : view.name === "list" ? (
        <EventList roles={roles} onNew={() => setView({ name: "edit", id: "" })} onOpen={(id) => setView({ name: "edit", id })} />
      ) : view.name === "payments" ? (
        <PaymentSettings roles={roles} />
      ) : (
        <EventEditor key={view.id || "new"} eventId={view.id} roles={roles} onBack={() => setView({ name: "list" })} onOpen={(id) => setView({ name: "edit", id })} />
      )}
    </section>
    </main>
    </>
  );
}
