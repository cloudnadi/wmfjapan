import { useEffect, useState } from "react";
import Navbar from "./Navbar";
import { getMembershipStatus } from "./api";
import { getRegistrantEmail } from "./registrantAuth";
import { MembershipStatusSection, ProfileSection, TransactionsSection } from "./memberShared";

const MEMBERSHIP_PATH = "/member/membership";

/**
 * Member → Member Profile (/member/profile): who you are, where your NPO
 * membership stands and its history. Joining / renewing lives on the
 * Foundation Membership page.
 */
export default function MemberProfile({ navigateTo }) {
  const email = getRegistrantEmail();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    getMembershipStatus()
      .then(setStatus)
      .catch((err) => setError(err.message || "Could not load your profile."));
  }, []);

  const needsAction = status && !status.coversRenewal && !status.pendingPayment;

  return (
    <>
      <Navbar navigateTo={navigateTo} />
      <main className="page-wrap onam-reg">
        <header className="event-header event-header--standalone">
          <div className="banner-title">Member Profile</div>
          <div className="banner-sub-title">{email ? `Signed in as ${email}` : "Your WMF Japan"}</div>
          <div className="sky-bar" />
        </header>

        <div className="mypage-stack">
          <ProfileSection status={status} email={email} error={error} />
          <MembershipStatusSection status={status}>
            <div className="btn-row">
              <button type="button" className="btn-primary" onClick={() => navigateTo(MEMBERSHIP_PATH)}>
                {needsAction
                  ? status.isMember
                    ? "Renew your membership →"
                    : "Join the WMF Japan family →"
                  : "Manage membership & AGM →"}
              </button>
            </div>
          </MembershipStatusSection>
          <TransactionsSection status={status} />
        </div>
      </main>
    </>
  );
}
