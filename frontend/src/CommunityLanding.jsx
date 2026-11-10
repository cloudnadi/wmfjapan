import { useState } from "react";
import { AgmDetails } from "./AgmFields";

const MEMBERSHIP_PATH = "/member/membership";
const SPONSORS_PATH = "/sponsors";

// Placeholder artwork until the real closure image is uploaded: drop the file
// into frontend/public and point this at it, or set VITE_EVENT_CLOSURE_IMAGE.
const THANK_YOU_IMAGE = import.meta.env.VITE_EVENT_CLOSURE_IMAGE || "/badhradeepam.jpeg";

const BENEFITS = [
  {
    icon: "🎟️",
    title: "Member pricing",
    text: "NPO members enjoy member pricing at our events, such as discounted Onam adult tickets.",
  },
  {
    icon: "👨‍👩‍👧",
    title: "Bring your family",
    text: "Join or renew yourself and your spouse together, in a single payment.",
  },
  {
    icon: "📣",
    title: "Be part of the AGM",
    text: "Register online for the Annual General Meeting — in person or remotely.",
  },
];

/** The upcoming AGM, straight from the admin AGM settings; shown only while registration is open. */
export function AgmBanner({ agm, navigateTo }) {
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

/**
 * Landing page for the period between events: it points people at what is
 * actually open (NPO membership, the AGM) and keeps the thank-you for the last
 * event without making "it's over" the headline.
 */
export default function CommunityLanding({ navigateTo, agm }) {
  const [imageSrc, setImageSrc] = useState(THANK_YOU_IMAGE);

  return (
    <>
      <section className="community-hero">
        <div className="community-hero-copy">
          <span className="landing-hero-kicker">WMF Japan</span>
          <h1>Stay part of the WMF Japan family</h1>
          <p>
            Thank you for celebrating Aavani 2026 and our 40th Anniversary with us. Join or renew your NPO
            membership to be first in line for what comes next.
          </p>
          <div className="community-hero-actions">
            <button type="button" className="landing-hero-cta" onClick={() => navigateTo(MEMBERSHIP_PATH)}>
              Join / Renew NPO Membership →
            </button>
            <button type="button" className="community-secondary" onClick={() => navigateTo(SPONSORS_PATH)}>
              Our sponsors
            </button>
          </div>
        </div>
        <figure className="community-hero-photo">
          <img
            src={imageSrc}
            alt="Aavani 2026 and the 40th Anniversary Celebration"
            onError={() => setImageSrc((current) => (current !== "/logo.png" ? "/logo.png" : current))}
          />
          <figcaption>Aavani 2026 &amp; the 40th Anniversary — thank you for celebrating with us.</figcaption>
        </figure>
      </section>

      <AgmBanner agm={agm} navigateTo={navigateTo} />

      <section className="community-benefits" aria-labelledby="benefits-title">
        <h2 id="benefits-title" className="upcoming-events-title">
          Why become a member
        </h2>
        <div className="community-benefits-grid">
          {BENEFITS.map((benefit) => (
            <article key={benefit.title} className="community-benefit">
              <span className="community-benefit-icon" aria-hidden="true">
                {benefit.icon}
              </span>
              <h3>{benefit.title}</h3>
              <p>{benefit.text}</p>
            </article>
          ))}
        </div>
        <div className="community-benefits-cta">
          <button type="button" className="landing-hero-cta" onClick={() => navigateTo(MEMBERSHIP_PATH)}>
            Become an NPO Member →
          </button>
        </div>
      </section>
    </>
  );
}
