import { useEffect, useState } from "react";
import { getPublicPartners, getSiteConfig } from "./api";
import Navbar from "./Navbar";

const HOME_PATH = "/";
const ONAM_REGISTRATION_PATH = "/onam/register";
const ADMIN_LOGIN_PATH = "/admin";
const TERMS_PATH = "/terms";
const PRIVACY_PATH = "/privacy";
const CANCELLATION_PATH = "/cancellation";
const SEAT_SELECTION_GUIDE_PATH = "/seat-selection-guide";
const ZONE_UPGRADE_GUIDE_PATH = "/zone-upgrade-guide";

export const TIER_ORDER = ["emerald", "platinum", "gold", "silver", "bronze"];

export const TIER_CONFIG = {
  emerald:  { label: "Emerald",  color: "#50C878", accent: "rgba(80, 200, 120, 0.18)"  },
  platinum: { label: "Platinum", color: "#C8C8D0", accent: "rgba(200, 200, 208, 0.15)" },
  gold:     { label: "Gold",     color: "#F5A623", accent: "rgba(245, 166, 35, 0.15)"  },
  silver:   { label: "Silver",   color: "#A8A9AD", accent: "rgba(168, 169, 173, 0.14)" },
  bronze:   { label: "Bronze",   color: "#CD7F32", accent: "rgba(205, 127, 50, 0.14)"  },
};

function TierBadge({ tier }) {
  const cfg = TIER_CONFIG[tier] || TIER_CONFIG.bronze;
  return (
    <span className={`tier-badge tier-badge--${tier}`} style={{ color: cfg.color }}>
      {cfg.label}
    </span>
  );
}

function EventFooter() {
  return (
    <footer className="event-footer">
      Host Your Event With Us
      <br />
      For bookings and inquiries, contact <a href="mailto:hello@cloudnadi.com">hello@cloudnadi.com</a>.
    </footer>
  );
}

function PartnerCard({ partner, showTier = false }) {
  const tier = partner.tier || "bronze";
  const content = (
    <div className={`partner-card-pub tier-card--${tier}`}>
      {partner.logoUrl ? (
        <div className="partner-card-pub-logo">
          <img src={partner.logoUrl} alt={`${partner.name} logo`} loading="lazy" />
        </div>
      ) : null}
      <h3 className="partner-card-pub-name">{partner.name}</h3>
      {partner.description ? (
        <p className="partner-card-pub-desc">{partner.description}</p>
      ) : null}
      {showTier ? <TierBadge tier={tier} /> : null}
    </div>
  );

  return partner.websiteUrl ? (
    <a
      href={partner.websiteUrl}
      target="_blank"
      rel="noreferrer"
      className="partner-card-pub-link"
      aria-label={`Visit ${partner.name}`}
    >
      {content}
    </a>
  ) : (
    <div className="partner-card-pub-link">{content}</div>
  );
}

export default function PartnersPage({ navigateTo }) {
  const [partners, setPartners] = useState([]);
  const [displayMode, setDisplayMode] = useState("flat");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([getPublicPartners(), getSiteConfig()])
      .then(([list, siteConfig]) => {
        if (!active) return;
        setPartners(
          (Array.isArray(list) ? list : [])
            .filter((p) => p.isActive !== false)
            .sort((a, b) => (a.displayOrder ?? 0) - (b.displayOrder ?? 0))
        );
        setDisplayMode(siteConfig.config?.partners_display_mode ?? "flat");
      })
      .finally(() => { if (active) setLoaded(true); });
    return () => { active = false; };
  }, []);

  // Group partners by tier for categorized mode.
  const tierGroups = TIER_ORDER.reduce((acc, tier) => {
    acc[tier] = partners.filter((p) => (p.tier || "bronze") === tier);
    return acc;
  }, {});

  return (
    <>
      <Navbar
        navigateTo={navigateTo}
      />
      <main className="landing-page partners-page" id="top">
        <header className="partners-page-header">
          <span className="landing-hero-kicker">WMF Japan</span>
          <h1>Our Sponsors</h1>
          <p>We are grateful for the generous support of our sponsors and partners.</p>
        </header>

        {!loaded ? (
          <div className="loading" style={{ padding: "48px 0", textAlign: "center" }}>
            Loading sponsors…
          </div>
        ) : partners.length === 0 ? (
          <p className="partners-page-empty">No sponsors to display yet.</p>
        ) : displayMode === "categorized" ? (
          <div className="partners-tiers">
            {TIER_ORDER.filter((tier) => tierGroups[tier].length > 0).map((tier) => {
              const cfg = TIER_CONFIG[tier];
              return (
                <section key={tier} className="tier-section" aria-labelledby={`tier-${tier}`}>
                  <div className="tier-section-header">
                    <div className="tier-section-line" />
                    <h2
                      id={`tier-${tier}`}
                      className="tier-section-title"
                      style={{ color: cfg.color }}
                    >
                      {cfg.label} Sponsors
                    </h2>
                    <div className="tier-section-line" />
                  </div>
                  <div className={`partners-grid partners-grid--${tier}`}>
                    {tierGroups[tier].map((partner) => (
                      <PartnerCard key={partner.partnerId} partner={partner} />
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        ) : (
          <div className="partners-grid">
            {partners.map((partner) => (
              <PartnerCard key={partner.partnerId} partner={partner} showTier />
            ))}
          </div>
        )}

        <footer className="landing-footer">
          <div className="landing-footer-copy">© WMF Japan</div>
          <div className="landing-footer-links">
            <a href="mailto:info@wmfjapan.com">Contact</a>
            {/* <button type="button" onClick={() => navigateTo(ZONE_UPGRADE_GUIDE_PATH)}>
              Zone Upgrade &amp; Dinner Guide
            </button>
            <button type="button" onClick={() => navigateTo(SEAT_SELECTION_GUIDE_PATH)}>
              Seat Selection Guide
            </button> */}
            <button type="button" onClick={() => navigateTo(TERMS_PATH)}>
              Terms &amp; Conditions
            </button>
            <button type="button" onClick={() => navigateTo(PRIVACY_PATH)}>
              Privacy Policy
            </button>
            <button type="button" onClick={() => navigateTo(CANCELLATION_PATH)}>
              Cancellation Policy
            </button>
            <button type="button" onClick={() => navigateTo(ADMIN_LOGIN_PATH)}>
              Admin Login
            </button>
          </div>
        </footer>
        <EventFooter />
      </main>
    </>
  );
}
