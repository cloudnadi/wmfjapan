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
import HomePage from "./HomePage";
import { useAuthSession } from "./auth/useAuth";
import { logPageVisit, prefetchMembershipStatus } from "./api";

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
const SignInPage = lazy(() => import("./SignInPage"));
const AuthCallback = lazy(() => import("./auth/AuthCallback"));
const MyRegistrations = lazy(() => import("./MyRegistrations"));
const MemberProfile = lazy(() => import("./MemberProfile"));
const MemberMembership = lazy(() => import("./MemberMembership"));
const HallSeatSelection = lazy(() => import("./HallSeatSelection"));
const SeatUpgrade = lazy(() => import("@plugins/seat-upgrade/frontend/SeatUpgrade"));
const CelebrityDinnerSignup = lazy(() => import("./CelebrityDinnerSignup"));
const PartnersPage = lazy(() => import("./PartnersPage"));
const EntryPassCheckIn = lazy(() => import("./EntryPassCheckIn"));
const SeatPurchaseCheckIn = lazy(() => import("./SeatPurchaseCheckIn"));

// StaticPages has no default export, so pick the named one out of the module.
const staticPage = (name) =>
  lazy(() => import("./StaticPages").then((m) => ({ default: m[name] })));
const TermsAndConditions = staticPage("TermsAndConditions");
const PrivacyPolicy = staticPage("PrivacyPolicy");
const CancellationPolicy = staticPage("CancellationPolicy");

const HOME_PATH = "/";
const SIGN_IN_PATH = "/signin";
const AUTH_CALLBACK_PATH = "/auth/callback";
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

const STATIC_PATHS = [
  TERMS_PATH,
  PRIVACY_PATH,
  CANCELLATION_PATH,
  PARTNERS_PATH,
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
  const { session: accountSession } = useAuthSession();   // also restores a stored attendee session at start-up

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
      currentPath !== SIGN_IN_PATH &&
      currentPath !== AUTH_CALLBACK_PATH &&
      !parseEventPath(currentPath) &&
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
      currentPath === SIGN_IN_PATH ||
      !!parseEventPath(currentPath) ||
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

  if (currentPath === HOME_PATH) {
    return <HomePage navigateTo={navigateTo} />;
  }

  if (currentPath === SIGN_IN_PATH) {
    return <SignInPage navigateTo={navigateTo} />;
  }

  if (currentPath === AUTH_CALLBACK_PATH) {
    return <AuthCallback navigateTo={navigateTo} />;
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
    if (!accountSession && !registrantAuthed) {
      return <SignInPage navigateTo={navigateTo} />;
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

  const handleRegistrantLogout = () => {
    clearRegistrantSession();
    setRegistrantAuthed(false);
  };

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
