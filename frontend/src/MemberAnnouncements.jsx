import { useCallback, useEffect, useState } from "react";
import { getMemberAnnouncements, previewMemberAnnouncement, sendMemberAnnouncement } from "./api";

const ANNOUNCEMENTS = [
  {
    type: "agm",
    title: "AGM announcement",
    audience: "All members",
    text: "Invites everyone to the upcoming AGM with its date, venue and how to register. Uses the next AGM from the AGM tab.",
  },
  {
    type: "join",
    title: "Register / renew NPO membership",
    audience: "All members",
    text: "Invites everyone to register or renew their NPO membership. Includes a note to ignore it if they have already registered or renewed.",
  },
  {
    type: "renew",
    title: "Renewal reminder (2026–27)",
    audience: "Existing NPO members who have not renewed for 2026–27",
    text: "Reminds current members to renew, with their current end date. Includes a note to ignore it if they have already renewed. Members whose renewal is awaiting payment are skipped.",
  },
  {
    type: "whatsapp",
    title: "WhatsApp group invite (2026–27)",
    audience: "Members covered for 2026–27 (new, returning and renewed)",
    text: "Sends the members' WhatsApp group link set for 2026–27 in the NPO Membership tab. Includes a note to ignore it if they have already joined. Needs the link to be set first.",
  },
];

const TYPE_TITLES = Object.fromEntries(ANNOUNCEMENTS.map((a) => [a.type, a.title]));
const STATUS_LABELS = { queued: "Queued", sending: "Sending…", done: "Sent", failed: "Failed" };

function formatWhen(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

/**
 * Admin Members tab → Announcements. Each card previews the audience, asks for
 * confirmation, then queues the send; the history below shows progress
 * (sends run in the background, a few minutes for the whole member list).
 */
export default function MemberAnnouncements() {
  const [campaigns, setCampaigns] = useState([]);
  const [busyType, setBusyType] = useState("");
  const [message, setMessage] = useState(null); // { tone: "success" | "error", text }

  const refresh = useCallback(async () => {
    try {
      setCampaigns(await getMemberAnnouncements());
    } catch {
      // History is a convenience; the cards still work.
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const sending = campaigns.some((c) => c.status === "queued" || c.status === "sending");
  useEffect(() => {
    if (!sending) return undefined;
    const timer = setInterval(refresh, 8000);
    return () => clearInterval(timer);
  }, [sending, refresh]);

  const send = async (announcement) => {
    setMessage(null);
    setBusyType(announcement.type);
    try {
      const preview = await previewMemberAnnouncement(announcement.type);
      const lines = [
        `Send "${announcement.title}" to ${preview.recipientCount} member${preview.recipientCount === 1 ? "" : "s"}?`,
        preview.agm ? `AGM: ${preview.agm.title} (${preview.agm.date})` : "",
        preview.whatsappLink ? `Link: ${preview.whatsappLink}` : "",
        preview.sample?.length ? `e.g. ${preview.sample.join(", ")}` : "",
        "The emails are sent in the background and can't be recalled.",
      ].filter(Boolean);
      if (!window.confirm(lines.join("\n\n"))) return;
      await sendMemberAnnouncement(announcement.type);
      setMessage({ tone: "success", text: `"${announcement.title}" is being sent to ${preview.recipientCount} members. Progress is shown below.` });
      await refresh();
    } catch (err) {
      setMessage({ tone: "error", text: err.message || "Could not send the announcement." });
    } finally {
      setBusyType("");
    }
  };

  return (
    <section className="partner-panel" style={{ marginBottom: 20 }}>
      <div className="panel-heading">
        <div>
          <h3>Announcements</h3>
          <p>Email all or some members. Each send is recorded below.</p>
        </div>
      </div>

      <div className="announcement-grid">
        {ANNOUNCEMENTS.map((announcement) => (
          <article key={announcement.type} className="announcement-card">
            <h4>{announcement.title}</h4>
            <p className="announcement-audience">To: {announcement.audience}</p>
            <p className="field-note">{announcement.text}</p>
            <button
              type="button"
              className="button"
              onClick={() => send(announcement)}
              disabled={Boolean(busyType) || campaigns.some((c) => c.type === announcement.type && (c.status === "queued" || c.status === "sending"))}
            >
              {busyType === announcement.type ? "Checking…" : "Send…"}
            </button>
          </article>
        ))}
      </div>

      {message ? <p className={message.tone === "error" ? "error-banner" : "success-banner"}>{message.text}</p> : null}

      {campaigns.length > 0 ? (
        <>
          <div className="panel-heading" style={{ marginTop: 20 }}>
            <div>
              <h4 style={{ margin: 0 }}>Recent sends</h4>
            </div>
            <button type="button" className="button secondary small" onClick={refresh}>
              Refresh
            </button>
          </div>
          <div className="table-container">
            <table className="registrations-table" style={{ minWidth: 0 }}>
              <thead>
                <tr>
                  <th>Announcement</th>
                  <th>Started</th>
                  <th>Status</th>
                  <th>Sent</th>
                  <th>Failed</th>
                </tr>
              </thead>
              <tbody>
                {campaigns.map((c) => (
                  <tr key={c.campaignId}>
                    <td>{TYPE_TITLES[c.type] || c.type}</td>
                    <td>{formatWhen(c.startedAt || c.createdAt)}</td>
                    <td>
                      {STATUS_LABELS[c.status] || c.status}
                      {c.error ? <small> — {c.error}</small> : null}
                    </td>
                    <td>
                      {c.sent ?? 0} / {c.recipientCount ?? 0}
                    </td>
                    <td title={(c.failedSample || []).join(", ")}>{c.failed ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  );
}
