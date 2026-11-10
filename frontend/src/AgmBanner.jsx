import { AgmDetails } from "./AgmFields";

const MEMBERSHIP_PATH = "/member/membership";

/** The upcoming AGM, straight from the admin AGM settings; shown only while registration is open. */
export default function AgmBanner({ agm, navigateTo }) {
  if (!agm) return null;
  return (
    <section className="community-agm" aria-labelledby="agm-banner-title">
      <div className="community-agm-copy">
        <span className="landing-hero-kicker">Coming up</span>
        <h2 id="agm-banner-title">{agm.title || "Annual General Meeting"}</h2>
        <p>Members are invited — register your attendance online.</p>
        <button type="button" className="landing-hero-cta" onClick={() => navigateTo(MEMBERSHIP_PATH)}>
          Register for the AGM →
        </button>
      </div>
      <AgmDetails agm={{ ...agm, title: "" }} />
    </section>
  );
}
