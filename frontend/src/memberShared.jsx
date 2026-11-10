/** Pieces shared by the member pages (/member/profile and /member/membership). */

const STATUS_LABELS = { paid: "Paid", pending: "Awaiting payment", cancelled: "Cancelled" };
const VIA_LABELS = { square: "Card (Square)", paypay: "PayPay", bank: "Bank transfer" };

export function formatDate(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

export function formatYear(code) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(code || ""));
  return match ? `${match[1]}–${match[2]}` : code || "";
}

export const formatVia = (via) => VIA_LABELS[via] || (via ? "Other" : "—");
export const formatYen = (value) => (value == null || value === "" ? "—" : `¥${Number(value).toLocaleString()}`);

export function ProfileRow({ label, value }) {
  return (
    <div className="mypage-row">
      <span className="mypage-row-label">{label}</span>
      <span className="mypage-row-value">{value || "—"}</span>
    </div>
  );
}

export function Section({ id, title, children }) {
  return (
    <section className="form-card standalone-card" id={id}>
      <div className="form-body">
        <div className="section-label">{title}</div>
        {children}
      </div>
    </section>
  );
}


export function ProfileSection({ status, email, error }) {
  const profile = status?.profile || {};
  return (
    <Section id="profile" title="Member Profile">
      {!status && !error ? <p className="field-note">Loading your profile…</p> : null}
      {error && !status ? <p className="error-banner">{error}</p> : null}
      {status ? (
        <div className="mypage-rows">
          <ProfileRow label="Name" value={profile.fullName || status.memberName} />
          <ProfileRow label="Email" value={profile.email || email} />
          <ProfileRow label="Member ID" value={profile.memberId} />
          <ProfileRow label="Phone" value={profile.mobile || profile.phone} />
        </div>
      ) : null}
    </Section>
  );
}

/** Read-only summary of where the member stands for the current membership year. */
export function MembershipStatusSection({ status, children }) {
  const year = formatYear(status?.renewalYear);
  const pending = status?.pendingPayment;
  let headline = "Not a member yet";
  if (status?.isMember) headline = `Active member until ${formatDate(status.validUntil)}`;
  else if (status?.validUntil) headline = `Lapsed on ${formatDate(status.validUntil)}`;

  return (
    <Section id="status" title="NPO Membership Status">
      {!status ? (
        <p className="field-note">Loading your membership…</p>
      ) : (
        <>
          <div className="mypage-rows">
            <ProfileRow label="Status" value={headline} />
            <ProfileRow
              label={`${year} membership`}
              value={
                status.coversRenewal
                  ? "Covered"
                  : pending
                    ? "Awaiting payment"
                    : "Not yet renewed"
              }
            />
            {status.coversRenewal && status.renewalTo ? (
              <ProfileRow label="Runs until" value={formatDate(status.renewalTo)} />
            ) : null}
          </div>
          {children}
        </>
      )}
    </Section>
  );
}

export function TransactionsSection({ status }) {
  const transactions = status?.transactions || [];
  return (
    <Section id="transactions" title="Membership Transactions">
      {!status ? null : transactions.length === 0 ? (
        <p className="field-note">No membership transactions yet.</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="registrations-table" style={{ minWidth: 0 }}>
            <thead>
              <tr>
                <th>#</th>
                <th>Membership</th>
                <th>Paid on</th>
                <th>Amount</th>
                <th>Method</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((t) => (
                <tr key={t.transactionNo}>
                  <td>{t.transactionNo}</td>
                  <td>
                    {t.kind === "spouse-payment"
                      ? `Spouse membership${t.forEmail ? ` (${t.forEmail})` : ""}`
                      : t.from
                        ? `${formatDate(t.from.slice(0, 10))} – ${formatDate(t.to.slice(0, 10))}`
                        : formatYear(t.year) || "—"}
                    {t.paidBy ? <small> · paid by {t.paidBy}</small> : null}
                  </td>
                  <td>{t.paidDate ? formatDate(t.paidDate.slice(0, 10)) : "—"}</td>
                  <td>{formatYen(t.amount)}</td>
                  <td>{formatVia(t.via)}</td>
                  <td>{STATUS_LABELS[t.status] || t.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
