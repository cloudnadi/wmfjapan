import { useState, useEffect, lazy, Suspense } from "react";
import { applyPageMeta } from "./pageMeta";
import RegistrationLogin from "./RegistrationLogin";
import {
  isRegistrantAuthenticated,
  clearRegistrantSession,
  onRegistrantSessionChange,
} from "./registrantAuth";
import { hasAdminSession } from "./adminSession";
import { startIdleWatcher } from "./sessionActivity";
import Navbar from "./Navbar";
import CommunityLanding, { AgmBanner } from "./CommunityLanding";
// import PartnersSection from "./PartnersSection";
import { getSiteConfig, getMyRegistrations, logPageVisit, prefetchMembershipStatus } from "./api";

// Every route below the landing page is loaded on demand, so a visitor who only
// sees the home page never downloads the admin dashboard, the QR code library or
// the registration forms. Keep the landing page's own imports static.
const AdminDashboard = lazy(() => import("./AdminDashboard"));
const EventManager = lazy(() => import("./EventManager"));
const CheckIn = lazy(() => import("./CheckIn"));
const EventsList = lazy(() => import("./EventsList"));
const EventPage = lazy(() => import("./EventPage"));
const EventRegister = lazy(() => import("./EventRegister"));
const EventRegistered = lazy(() => import("./EventRegistered"));
const MyEventRegistrations = lazy(() => import("./MyEventRegistrations"));
const OnamRegistration = lazy(() => import("./OnamRegistration"));
const MyRegistrations = lazy(() => import("./MyRegistrations"));
const MemberProfile = lazy(() => import("./MemberProfile"));
const MemberMembership = lazy(() => import("./MemberMembership"));
const HallSeatSelection = lazy(() => import("./HallSeatSelection"));
const SeatUpgrade = lazy(() => import("@plugins/seat-upgrade/frontend/SeatUpgrade"));
const CelebrityDinnerSignup = lazy(() => import("./CelebrityDinnerSignup"));
const PartnersPage = lazy(() => import("./PartnersPage"));
const EntryPassCheckIn = lazy(() => import("./EntryPassCheckIn"));
const SeatPurchaseCheckIn = lazy(() => import("./SeatPurchaseCheckIn"));
const SadhyaSlotGuide = lazy(() => import("./SadhyaSlotGuide"));
const SeatSelectionGuide = lazy(() => import("./SeatSelectionGuide"));
const ZoneUpgradeGuide = lazy(() => import("./ZoneUpgradeGuide"));

// StaticPages has no default export, so pick the named one out of the module.
const staticPage = (name) =>
  lazy(() => import("./StaticPages").then((m) => ({ default: m[name] })));
const TermsAndConditions = staticPage("TermsAndConditions");
const PrivacyPolicy = staticPage("PrivacyPolicy");
const CancellationPolicy = staticPage("CancellationPolicy");

const HOME_PATH = "/";
const ONAM_REGISTRATION_PATH = "/onam/register";
const MY_REGISTRATIONS_PATH = "/my/registrations";
const MEMBER_PROFILE_PATH = "/member/profile";
const MEMBER_MEMBERSHIP_PATH = "/member/membership";
const LEGACY_MY_PAGE_PATH = "/mypage"; // old link (e.g. a Square return URL) → profile
const HALL_SEAT_SELECTION_PATH = "/my/hall-seat-selection";
const MY_SEAT_UPGRADE_PATH = "/my/seat-upgrade";
const CELEBRITY_DINNER_SIGNUP_PATH = "/celebrity-dinner";
const ADMIN_LOGIN_PATH = "/admin";
const EVENT_PLATFORM_PATH = "/admin/event-platform";
const ADMIN_CHECK_IN_PATH = "/admin/check-in";
const EVENTS_PATH = "/events";
const MY_EVENTS_PATH = "/my/events";

// /events, /events/{slug}, /events/{slug}/register, /events/{slug}/registered
function parseEventPath(path) {
  const match = /^\/events(?:\/([a-z0-9-]+)(?:\/(register|registered))?)?\/?$/.exec(path);
  return match ? { slug: match[1] || "", view: match[2] || (match[1] ? "page" : "list") } : null;
}
const TERMS_PATH = "/terms";
const PRIVACY_PATH = "/privacy";
const CANCELLATION_PATH = "/cancellation";
const PARTNERS_PATH = "/sponsors";
const CHECK_IN_PATH = "/check-in";
const SEAT_CHECK_IN_PATH = "/seat-check-in";
const SADHYA_SLOT_GUIDE_PATH = "/sadhya-slot-guide";
const SEAT_SELECTION_GUIDE_PATH = "/seat-selection-guide";
const ZONE_UPGRADE_GUIDE_PATH = "/zone-upgrade-guide";

const STATIC_PATHS = [
  TERMS_PATH,
  PRIVACY_PATH,
  CANCELLATION_PATH,
  PARTNERS_PATH,
  SADHYA_SLOT_GUIDE_PATH,
  SEAT_SELECTION_GUIDE_PATH,
  ZONE_UPGRADE_GUIDE_PATH,
];

export default function App() {
  return (
    <Suspense fallback={<div className="loading">Loading…</div>}>
      <AppRoutes />
    </Suspense>
  );
}

function AppRoutes() {
  const [currentPath, setCurrentPath] = useState(() => window.location.pathname || HOME_PATH);
  const [registrantAuthed, setRegistrantAuthed] = useState(() => isRegistrantAuthenticated());

  useEffect(() => {
    const handlePopState = () => {
      setCurrentPath(window.location.pathname || HOME_PATH);
    };

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  // Keep the page gates in sync with the stored session: sign-in/out in this
  // tab or another one, and a token the API rejected (api.js clears it on 401),
  // drop the user back to the sign-in screen instead of leaving a page up whose
  // every request fails.
  useEffect(
    () => onRegistrantSessionChange(() => setRegistrantAuthed(isRegistrantAuthenticated())),
    []
  );

  // Sign out after an hour of inactivity. Both checks clear their session
  // when idle, and the listeners above / in AdminDashboard update the UI.
  useEffect(
    () =>
      startIdleWatcher(() => {
        isRegistrantAuthenticated();
        hasAdminSession();
      }),
    []
  );

  // The 24h token can lapse while the tab sits open, so re-check it whenever
  // the user moves to another page.
  useEffect(() => {
    setRegistrantAuthed(isRegistrantAuthenticated());
  }, [currentPath]);

  const navigateTo = (target, { replace = false } = {}) => {
    // Targets may carry a #hash or ?query (e.g. "/mypage#membership"); routing uses the path only.
    const path = target.split("#")[0].split("?")[0];
    if (window.location.pathname + window.location.search + window.location.hash !== target) {
      if (replace) {
        window.history.replaceState({}, "", target);
      } else {
        window.history.pushState({}, "", target);
      }
    }
    setCurrentPath(path);
  };

  useEffect(() => {
    if (currentPath === LEGACY_MY_PAGE_PATH) navigateTo(MEMBER_PROFILE_PATH, { replace: true });
  }, [currentPath]);

  useEffect(() => {
    if (
      currentPath !== HOME_PATH &&
      currentPath !== ADMIN_LOGIN_PATH &&
      currentPath !== EVENT_PLATFORM_PATH &&
      currentPath !== ADMIN_CHECK_IN_PATH &&
      currentPath !== MY_EVENTS_PATH &&
      !parseEventPath(currentPath) &&
      currentPath !== ONAM_REGISTRATION_PATH &&
      currentPath !== MY_REGISTRATIONS_PATH &&
      currentPath !== MEMBER_PROFILE_PATH &&
      currentPath !== MEMBER_MEMBERSHIP_PATH &&
      currentPath !== LEGACY_MY_PAGE_PATH &&
      currentPath !== HALL_SEAT_SELECTION_PATH &&
      currentPath !== MY_SEAT_UPGRADE_PATH &&
      currentPath !== CELEBRITY_DINNER_SIGNUP_PATH &&
      currentPath !== CHECK_IN_PATH &&
      currentPath !== SEAT_CHECK_IN_PATH &&
      !STATIC_PATHS.includes(currentPath)
    ) {
      navigateTo(HOME_PATH, { replace: true });
    }
  }, [currentPath]);

  // Member pages: ask for the membership status now, while their code chunk is
  // still downloading, instead of only after it has mounted.
  useEffect(() => {
    if (registrantAuthed && (currentPath === MEMBER_PROFILE_PATH || currentPath === MEMBER_MEMBERSHIP_PATH)) {
      prefetchMembershipStatus();
    }
  }, [currentPath, registrantAuthed]);

  // Tab title + metadata follow the page (runs before the visit is logged, so analytics see the right title).
  useEffect(() => {
    applyPageMeta(currentPath);
  }, [currentPath]);

  useEffect(() => {
    const isKnownPath =
      currentPath === HOME_PATH ||
      currentPath === ADMIN_LOGIN_PATH ||
      currentPath === EVENT_PLATFORM_PATH ||
      currentPath === ADMIN_CHECK_IN_PATH ||
      currentPath === MY_EVENTS_PATH ||
      !!parseEventPath(currentPath) ||
      currentPath === ONAM_REGISTRATION_PATH ||
      currentPath === MY_REGISTRATIONS_PATH ||
      currentPath === MEMBER_PROFILE_PATH ||
      currentPath === MEMBER_MEMBERSHIP_PATH ||
      currentPath === HALL_SEAT_SELECTION_PATH ||
      currentPath === MY_SEAT_UPGRADE_PATH ||
      currentPath === CELEBRITY_DINNER_SIGNUP_PATH ||
      currentPath === CHECK_IN_PATH ||
      currentPath === SEAT_CHECK_IN_PATH ||
      STATIC_PATHS.includes(currentPath);

    if (!isKnownPath) return;

    void logPageVisit({ path: currentPath, referrer: document.referrer });
  }, [currentPath]);

  // Home page now hosts admin login/dashboard.
  if (currentPath === HOME_PATH) {
    return <HomeAdminLanding navigateTo={navigateTo} />;
  }

  if (currentPath === ADMIN_LOGIN_PATH) {
    return <AdminDashboard onLogout={() => navigateTo(HOME_PATH)} />;
  }

  if (currentPath === EVENT_PLATFORM_PATH) {
    return <EventManager navigateTo={navigateTo} />;
  }

  if (currentPath === ADMIN_CHECK_IN_PATH) {
    return <CheckIn navigateTo={navigateTo} />;
  }

  const eventRoute = parseEventPath(currentPath);
  if (eventRoute) {
    if (eventRoute.view === "list") return <EventsList navigateTo={navigateTo} />;
    if (eventRoute.view === "page") return <EventPage navigateTo={navigateTo} slug={eventRoute.slug} />;
    if (eventRoute.view === "register") return <EventRegister navigateTo={navigateTo} slug={eventRoute.slug} />;
    return <EventRegistered navigateTo={navigateTo} slug={eventRoute.slug} />;
  }

  if (currentPath === MY_EVENTS_PATH) {
    if (!registrantAuthed) {
      return (
        <RegistrationLogin
          navigateTo={navigateTo}
          onAuthenticated={() => setRegistrantAuthed(true)}
          title="Sign in to My Event Registrations"
          description="We'll email you a 6-digit verification code to view and manage your event registrations."
        />
      );
    }
    return <MyEventRegistrations navigateTo={navigateTo} />;
  }

  if (currentPath === TERMS_PATH) {
    return <TermsAndConditions navigateTo={navigateTo} />;
  }

  if (currentPath === PRIVACY_PATH) {
    return <PrivacyPolicy navigateTo={navigateTo} />;
  }

  if (currentPath === CANCELLATION_PATH) {
    return <CancellationPolicy navigateTo={navigateTo} />;
  }

  if (currentPath === PARTNERS_PATH) {
    return <PartnersPage navigateTo={navigateTo} />;
  }

  if (currentPath === CHECK_IN_PATH) {
    return <EntryPassCheckIn navigateTo={navigateTo} />;
  }

  if (currentPath === SEAT_CHECK_IN_PATH) {
    return <SeatPurchaseCheckIn navigateTo={navigateTo} />;
  }
  
  if (currentPath === SADHYA_SLOT_GUIDE_PATH) {
    return <SadhyaSlotGuide navigateTo={navigateTo} />;
  }

  if (currentPath === SEAT_SELECTION_GUIDE_PATH) {
    return <SeatSelectionGuide navigateTo={navigateTo} />;
  }

  if (currentPath === ZONE_UPGRADE_GUIDE_PATH) {
    return <ZoneUpgradeGuide navigateTo={navigateTo} />;
  }

  const handleRegistrantLogout = () => {
    clearRegistrantSession();
    setRegistrantAuthed(false);
  };

  // After signing in to register, a member with an existing registration goes
  // straight to My Registrations instead of the registration form; a first-time
  // registrant proceeds to the form as before. My Registrations offers its own
  // "+ Add New Registration" button for members who want another one.
  const handleRegistrantAuthenticated = () => {
    setRegistrantAuthed(true);
    getMyRegistrations()
      .then((items) => {
        if (Array.isArray(items) && items.length > 0) {
          navigateTo(MY_REGISTRATIONS_PATH, { replace: true });
        }
      })
      .catch(() => {
        // Non-fatal: if we can't check, let the user proceed to register.
      });
  };

  if (currentPath === ONAM_REGISTRATION_PATH) {
    if (!registrantAuthed) {
      return (
        <RegistrationLogin
          navigateTo={navigateTo}
          onAuthenticated={handleRegistrantAuthenticated}
        />
      );
    }
    return (
      <OnamRegistration
        navigateTo={navigateTo}
        onLogout={handleRegistrantLogout}
      />
    );
  }

  if (currentPath === MY_REGISTRATIONS_PATH) {
    if (!registrantAuthed) {
      return (
        <RegistrationLogin
          navigateTo={navigateTo}
          onAuthenticated={() => setRegistrantAuthed(true)}
          title="Sign in to My Registrations"
          description="We'll email you a 6-digit verification code to view and manage your registrations."
        />
      );
    }
    return (
      <MyRegistrations
        navigateTo={navigateTo}
        onLogout={handleRegistrantLogout}
      />
    );
  }

  if (currentPath === MEMBER_PROFILE_PATH) {
    if (!registrantAuthed) {
      return (
        <RegistrationLogin
          navigateTo={navigateTo}
          onAuthenticated={() => setRegistrantAuthed(true)}
          title="Login / Register"
          subtitle="Member Profile"
          description="We'll email you a 6-digit verification code to view your profile and NPO membership."
        />
      );
    }
    return <MemberProfile navigateTo={navigateTo} />;
  }

  if (currentPath === MEMBER_MEMBERSHIP_PATH) {
    if (!registrantAuthed) {
      return (
        <RegistrationLogin
          navigateTo={navigateTo}
          onAuthenticated={() => setRegistrantAuthed(true)}
          title="Login / Register"
          subtitle="WMF Japan Foundation Membership"
          description="We'll email you a 6-digit verification code to login."
        />
      );
    }
    return <MemberMembership navigateTo={navigateTo} />;
  }

  if (currentPath === HALL_SEAT_SELECTION_PATH) {
    if (!registrantAuthed) {
      return (
        <RegistrationLogin
          navigateTo={navigateTo}
          onAuthenticated={() => setRegistrantAuthed(true)}
          title="Sign in to select your seats"
          description="We'll email you a 6-digit verification code to access hall seat selection."
        />
      );
    }
    return (
      <HallSeatSelection
        navigateTo={navigateTo}
        onLogout={handleRegistrantLogout}
      />
    );
  }

  if (currentPath === MY_SEAT_UPGRADE_PATH) {
    if (!registrantAuthed) {
      return (
        <RegistrationLogin
          navigateTo={navigateTo}
          onAuthenticated={() => setRegistrantAuthed(true)}
          title="Sign in to upgrade your zone"
          description="We'll email you a 6-digit verification code to access zone upgrades."
        />
      );
    }
    return (
      <SeatUpgrade
        navigateTo={navigateTo}
        onLogout={handleRegistrantLogout}
      />
    );
  }

  if (currentPath === CELEBRITY_DINNER_SIGNUP_PATH) {
    if (!registrantAuthed) {
      return (
        <RegistrationLogin
          navigateTo={navigateTo}
          onAuthenticated={() => setRegistrantAuthed(true)}
          title="Sign in for the Dinner with Celebrity"
          description="We'll email you a 6-digit verification code to access the Dinner with Celebrity sign-up."
        />
      );
    }
    return (
      <CelebrityDinnerSignup
        navigateTo={navigateTo}
        onLogout={handleRegistrantLogout}
      />
    );
  }

  return null;
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

// Both 2026 events have wrapped, so the landing page shows the community page
// (membership + AGM) instead of the poster carousel and the register CTA. Flip this back to false
// (and refresh UPCOMING_EVENTS) when the next event opens for registration.
const EVENT_COMPLETED = true;

const UPCOMING_EVENTS = [
  {
    date: "September 12, 2026",
    name: "Aavani 2026",
    venue: "Kasai Civic Hall, Edogawa",
    time: "10:30 AM – 8:00 PM",
  },
  {
    date: "October 3, 2026",
    name: "40th Anniversary Celebrity Event",
    venue: "Kasai Civic Hall, Edogawa",
    time: "1:00 PM – 6:00 PM",
  },
];

function HomeAdminLanding({ navigateTo }) {
  const [onamImageSrc, setOnamImageSrc] = useState(
    import.meta.env.VITE_ONAM_IMAGE || "/onam_banner.png"
  );
  const [anniversaryImageSrc, setAnniversaryImageSrc] = useState(
    import.meta.env.VITE_ANNIVERSARY_IMAGE || "/onam_banner.png"
  );
  const [activeSlide, setActiveSlide] = useState(0);
  const [registrationClosed, setRegistrationClosed] = useState(false);
  const [agm, setAgm] = useState(null);

  // Fetch live carousel URLs from the backend; fall back to env/static on failure.
  useEffect(() => {
    getSiteConfig().then(({ config }) => {
      if (config?.onam?.imageUrl) setOnamImageSrc(config.onam.imageUrl);
      if (config?.anniversary?.imageUrl) setAnniversaryImageSrc(config.anniversary.imageUrl);
      setAgm(config?.agm || null);
      // Past the deadline the register CTA becomes a dead end, so label it as closed.
      const expiry = String(config?.onamRegistrationExpiry || "");
      setRegistrationClosed(Boolean(expiry && new Date(`${expiry}T23:59:59`) < new Date()));
    }).catch(() => {});
  }, []);

  const handleOnamImageError = () =>
    setOnamImageSrc((c) => (c !== "/logo.png" ? "/logo.png" : c));
  const handleAnniversaryImageError = () =>
    setAnniversaryImageSrc((c) => (c !== "/logo.png" ? "/logo.png" : c));

  const slides = [
    {
      src: onamImageSrc,
      alt: "Aavani Onam 2026 – September 12 at Kasai Civic Hall",
      onError: handleOnamImageError,
    },
    {
      src: anniversaryImageSrc,
      alt: "WMF Japan 40th Anniversary – October 3 at Kasai Civic Hall",
      onError: handleAnniversaryImageError,
    },
  ];

  const prevSlide = () =>
    setActiveSlide((s) => (s - 1 + slides.length) % slides.length);
  const nextSlide = () =>
    setActiveSlide((s) => (s + 1) % slides.length);

  return (
    <>
      <Navbar
        navigateTo={navigateTo}
      />
      <main className="landing-page" id="top">
        {EVENT_COMPLETED ? (
          <CommunityLanding navigateTo={navigateTo} agm={agm} />
        ) : (
        <section className="landing-hero">
          {/* Poster carousel — one event at a time */}
          <div
            className="hero-carousel"
            role="region"
            aria-label="Event posters"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft") prevSlide();
              if (e.key === "ArrowRight") nextSlide();
            }}
          >
            {slides.map((slide, idx) => (
              <div
                key={idx}
                className={`hero-slide${activeSlide === idx ? " active" : ""}`}
                role="group"
                aria-roledescription="slide"
                aria-label={`Slide ${idx + 1} of ${slides.length}`}
                aria-hidden={activeSlide !== idx}
              >
                <img
                  src={slide.src}
                  alt={slide.alt}
                  className="hero-slide-img"
                  onError={slide.onError}
                />
              </div>
            ))}

            <button
              type="button"
              className="hero-carousel-prev"
              onClick={prevSlide}
              aria-label="Previous slide"
            >
              ‹
            </button>
            <button
              type="button"
              className="hero-carousel-next"
              onClick={nextSlide}
              aria-label="Next slide"
            >
              ›
            </button>

            <div className="hero-carousel-dots" role="tablist" aria-label="Slide navigation">
              {slides.map((_, idx) => (
                <button
                  key={idx}
                  type="button"
                  role="tab"
                  className={`hero-carousel-dot${activeSlide === idx ? " active" : ""}`}
                  aria-label={`Go to slide ${idx + 1}`}
                  aria-selected={activeSlide === idx}
                  onClick={() => setActiveSlide(idx)}
                />
              ))}
            </div>
          </div>

          {/* Event intro + CTA */}
          <div className="landing-hero-cta-row">
            <span className="landing-hero-kicker">WMF Japan Presents</span>
            <h1>Aavani 2026 &amp; 40th Anniversary Celebration</h1>
            <p>A celebration of Onam, togetherness, and 40 years of WMF Japan.</p>
            {registrationClosed ? (
              <button type="button" className="landing-hero-cta" disabled>
                Registrations Closed
              </button>
            ) : (
              <button
                type="button"
                className="landing-hero-cta"
                onClick={() => navigateTo(ONAM_REGISTRATION_PATH)}
              >
                Register Now →
              </button>
            )}
          </div>
        </section>
        )}

        {/* Upcoming events — hidden once the listed events are behind us. */}
        {EVENT_COMPLETED ? null : (
        <section className="upcoming-events" aria-labelledby="upcoming-events-heading">
          <h2 id="upcoming-events-heading" className="upcoming-events-title">
            Upcoming Events
          </h2>
          <div className="upcoming-events-grid">
            {UPCOMING_EVENTS.map((ev) => (
              <div key={ev.name} className="upcoming-event-card">
                <span className="upcoming-event-date">{ev.date}</span>
                <h3 className="upcoming-event-name">{ev.name}</h3>
                <p className="upcoming-event-meta">{ev.venue}</p>
                <p className="upcoming-event-meta">{ev.time}</p>
              </div>
            ))}
          </div>
        </section>
        )}

        {EVENT_COMPLETED ? null : <AgmBanner agm={agm} navigateTo={navigateTo} />}

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
