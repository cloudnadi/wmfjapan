import { useCallback, useEffect, useState } from "react";
import { createAgm, downloadCSV, getAgm, getAgms, updateAgm } from "./api";

const EMPTY = {
  title: "", date: "", startTime: "", endTime: "", venue: "", venueJa: "", access: "", mapUrl: "",
  remoteNote: "", refreshmentsNote: "", notes: "", enabled: false,
};

const FIELDS = [
  ["title", "Title", "text", "WMF Japan AGM 2026"],
  ["date", "Date", "date"],
  ["startTime", "Start time", "time"],
  ["endTime", "End time", "time"],
  ["venue", "Venue", "text", "Tobu Kuminkan, Edogawa-ku"],
  ["venueJa", "Venue (Japanese)", "text", "江戸川区 東部区民館"],
  ["access", "Access", "text", "5-minute walk from Mizue Station (Toei Shinjuku Line)"],
  ["mapUrl", "Google Maps link", "url", "https://maps.app.goo.gl/…"],
  ["remoteNote", "Remote participation note", "text", "Remote participation will also be available."],
  ["refreshmentsNote", "Refreshments note", "text", "Free refreshments for those attending in person."],
];

const ADULT_STATUS = {
  member: "NPO member",
  pending: "Payment pending",
  not_member: "Not covered",
  not_nk_member: "Not a member",
};
const ATTENDANCE = { in_person: "In person", remote: "Remote" };

function formatDate(value) {
  if (!value) return "No date yet";
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

function StatusBadges({ agm }) {
  return (
    <>
      <span className={`npo-badge ${agm.active ? "npo-member" : "npo-nonmember"}`}>{agm.active ? "Active" : "Past"}</span>{" "}
      {agm.active ? (
        <span className={`npo-badge ${agm.enabled ? "npo-member" : "npo-nonmember"}`}>
          {agm.enabled ? "Registration open" : "Registration closed"}
        </span>
      ) : null}
    </>
  );
}

/** Add / edit form. Editing is only offered for active AGMs (the server enforces it too). */
function AgmForm({ initial, onSaved, onCancel }) {
  const [form, setForm] = useState({ ...EMPTY, ...initial });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const editing = Boolean(initial?.agmId);

  const update = (patch) => setForm((prev) => ({ ...prev, ...patch }));

  const save = async (event) => {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      const saved = editing ? await updateAgm(initial.agmId, form) : await createAgm(form);
      await onSaved(saved);
    } catch (err) {
      setError(err.message || "Could not save the AGM");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="partner-panel partner-form-panel" onSubmit={save}>
      <div className="panel-heading">
        <div>
          <h3>{editing ? `Edit ${initial.title || initial.agmId}` : "Add AGM"}</h3>
          <p>Shown to members on the Foundation Membership page while registration is open and the AGM hasn't happened yet.</p>
        </div>
        <button type="button" className="button secondary" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>

      <label className="checkbox-inline">
        <input type="checkbox" checked={form.enabled} onChange={(e) => update({ enabled: e.target.checked })} disabled={saving} />{" "}
        <strong>Registration is open</strong>
      </label>

      {FIELDS.map(([key, label, type, placeholder]) => (
        <div className="field" key={key} style={{ marginTop: 12 }}>
          <label htmlFor={`agm-${key}`}>{label}</label>
          <input
            id={`agm-${key}`}
            type={type}
            placeholder={placeholder}
            value={form[key]}
            onChange={(e) => update({ [key]: e.target.value })}
            disabled={saving}
          />
        </div>
      ))}

      <div className="field" style={{ marginTop: 12 }}>
        <label htmlFor="agm-notes">Additional notes</label>
        <textarea id="agm-notes" rows={3} value={form.notes} onChange={(e) => update({ notes: e.target.value })} disabled={saving} />
      </div>

      {error ? <p className="error-banner">{error}</p> : null}

      <div className="btn-row">
        <button type="submit" className="button" disabled={saving}>
          {saving ? "Saving…" : editing ? "Save changes" : "Create AGM"}
        </button>
      </div>
    </form>
  );
}

/** One AGM: its details and every registration (past or new), with totals and CSV export. */
function AgmDetail({ agmId, onBack, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await getAgm(agmId));
    } catch (err) {
      setError(err.message || "Could not load the AGM");
    }
  }, [agmId]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <p className="error-banner">{error}</p>;
  if (!data) return <div className="loading">Loading…</div>;

  const { agm, registrations, totals } = data;

  if (editing) {
    return (
      <AgmForm
        initial={agm}
        onCancel={() => setEditing(false)}
        onSaved={async () => {
          setEditing(false);
          await load();
          onChanged();
        }}
      />
    );
  }

  const exportCsv = () => {
    const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = registrations.map((r) => [
      r.name, r.email, ATTENDANCE[r.attendance] || r.attendance, r.adults,
      (r.adultAttendees || []).map((a) => `${a.email} (${ADULT_STATUS[a.status] || a.status})`).join("; "),
      r.teens, r.kids, r.preschoolers || 0, r.toddlers || 0,
      r.totalAttendees, r.registeredAt,
    ]);
    const header = [
      "Name", "Email", "Attendance", "Adults", "Adult attendees", "Teens", "Children (6-12)", "Preschoolers (3-5)",
      "Toddlers (0-3)", "Total", "Registered",
    ];
    const csv = [header, ...rows].map((row) => row.map(esc).join(",")).join("\n");
    downloadCSV(new Blob([csv], { type: "text/csv;charset=utf-8" }), `${agm.agmId}-registrations.csv`);
  };

  const time = [agm.startTime, agm.endTime].filter(Boolean).join("–");

  return (
    <>
      <section className="partner-panel">
        <div className="panel-heading">
          <div>
            <h3>{agm.title}</h3>
            <p>
              {formatDate(agm.date)}
              {time ? ` · ${time}` : ""}
              {agm.venue ? ` · ${agm.venue}` : ""}
            </p>
            <StatusBadges agm={agm} />
          </div>
          <div className="row-actions">
            <button type="button" className="button secondary" onClick={onBack}>
              ← All AGMs
            </button>
            {agm.active ? (
              <button type="button" className="button" onClick={() => setEditing(true)}>
                Edit
              </button>
            ) : null}
          </div>
        </div>
        {!agm.active ? <p className="field-note">This AGM has taken place, so its details are a read-only record.</p> : null}
      </section>

      <section className="partner-panel" style={{ marginTop: 20 }}>
        <div className="panel-heading">
          <div>
            <h3>Registrations</h3>
            <p>
              {totals.registrations} registration{totals.registrations === 1 ? "" : "s"} · {totals.people}{" "}
              {totals.people === 1 ? "person" : "people"}
            </p>
          </div>
          <button type="button" className="button secondary small" onClick={exportCsv} disabled={!registrations.length}>
            Export CSV
          </button>
        </div>

        {!registrations.length ? (
          <div className="empty-state">No registrations yet.</div>
        ) : (
          <>
            <div className="admin-stats partner-stats">
              {[
                ["In person", totals.inPerson],
                ["Remote", totals.remote],
                ["Adults", totals.adults],
                ["Teens", totals.teens],
                ["Children", totals.kids],
                ["Preschoolers", totals.preschoolers || 0],
                ["Toddlers", totals.toddlers || 0],
              ].map(([label, value]) => (
                <div className="stat-box" key={label}>
                  <div className="stat-value">{value}</div>
                  <div className="stat-label">{label}</div>
                </div>
              ))}
            </div>
            {data.needsAttention ? (
              <p className="error-banner" style={{ marginTop: 14 }}>
                {data.needsAttention} adult attendee{data.needsAttention === 1 ? " is" : "s are"} no longer covered by an
                NPO membership for the AGM date — see the highlighted rows.
              </p>
            ) : null}
            <div className="table-container">
              <table className="registrations-table" style={{ minWidth: 0 }}>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Attendance</th>
                    <th>Adults</th>
                    <th>Teens</th>
                    <th>Children</th>
                    <th>Pre-school</th>
                    <th>Toddlers</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {registrations.map((r) => (
                    <tr key={r.email}>
                      <td>{r.name || "—"}</td>
                      <td>{r.email}</td>
                      <td>{ATTENDANCE[r.attendance] || r.attendance}</td>
                      <td>
                        {r.adults}
                        {(r.adultAttendees || []).length ? (
                          <ul className="agm-adult-list">
                            {r.adultAttendees.map((a) => (
                              <li key={a.email}>
                                {a.name || a.email}{" "}
                                <span className={`agm-adult-badge agm-adult-badge--${a.status === "member" ? "ok" : a.status === "pending" ? "wait" : "bad"}`}>
                                  {ADULT_STATUS[a.status] || a.status}
                                </span>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </td>
                      <td>{r.teens}</td>
                      <td>{r.kids}</td>
                      <td>{r.preschoolers || 0}</td>
                      <td>{r.toddlers || 0}</td>
                      <td><strong>{r.totalAttendees}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </>
  );
}

/**
 * Admin AGM tab: the list of AGMs (past and upcoming), add / edit (active ones
 * only) and, on clicking one, its registrations. AGMs and their registrations
 * are kept in separate tables, so past years stay on record.
 */
export default function AgmSettings() {
  const [agms, setAgms] = useState(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setAgms(await getAgms());
      setError("");
    } catch (err) {
      setError(err.message || "Could not load AGMs");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (selected) {
    return <AgmDetail agmId={selected} onBack={() => setSelected("")} onChanged={load} />;
  }

  if (adding) {
    return (
      <AgmForm
        onCancel={() => setAdding(false)}
        onSaved={async (saved) => {
          setAdding(false);
          await load();
          setSelected(saved.agmId);
        }}
      />
    );
  }

  return (
    <section className="partner-panel">
      <div className="panel-heading">
        <div>
          <h3>AGMs</h3>
          <p>Click an AGM to see its registrations. Only active (upcoming) AGMs can be edited.</p>
        </div>
        <button type="button" className="button" onClick={() => setAdding(true)}>
          + Add AGM
        </button>
      </div>

      {error ? <p className="error-banner">{error}</p> : null}
      {!agms && !error ? <div className="loading">Loading AGMs…</div> : null}
      {agms && agms.length === 0 ? <div className="empty-state">No AGMs yet. Add one to open registration.</div> : null}

      {agms && agms.length > 0 ? (
        <div className="table-container">
          <table className="registrations-table" style={{ minWidth: 0 }}>
            <thead>
              <tr>
                <th>AGM</th>
                <th>Date</th>
                <th>Venue</th>
                <th>Status</th>
                <th>Registrations</th>
                <th>People</th>
              </tr>
            </thead>
            <tbody>
              {agms.map((agm) => (
                <tr key={agm.agmId} onClick={() => setSelected(agm.agmId)} style={{ cursor: "pointer" }}>
                  <td>
                    <button
                      type="button"
                      className="button secondary small"
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelected(agm.agmId);
                      }}
                    >
                      {agm.title || agm.agmId}
                    </button>
                  </td>
                  <td>{formatDate(agm.date)}</td>
                  <td>{agm.venue || "—"}</td>
                  <td><StatusBadges agm={agm} /></td>
                  <td>{agm.registrationCount}</td>
                  <td>{agm.peopleCount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
