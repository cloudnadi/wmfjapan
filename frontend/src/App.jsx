import { Suspense, lazy, useEffect, useState } from "react";
import HomePage from "./HomePage";
import { useAuthSession } from "./auth/useAuth";
import { applyPageMeta } from "./pageMeta";
import { t } from "./i18n";

// Every route below the home page loads on demand, so a visitor who only sees the event list never downloads the admin
// screens, the QR code library or the registration forms.
const EventManager = lazy(() => import("./EventManager"));
const CheckIn = lazy(() => import("./CheckIn"));
const EventsList = lazy(() => import("./EventsList"));
const EventPage = lazy(() => import("./EventPage"));
const EventRegister = lazy(() => import("./EventRegister"));
const EventRegistered = lazy(() => import("./EventRegistered"));
const MyEventRegistrations = lazy(() => import("./MyEventRegistrations"));
const SignInPage = lazy(() => import("./SignInPage"));
const AuthCallback = lazy(() => import("./auth/AuthCallback"));

// StaticPages has no default export, so pick the named one out of the module.
const staticPage = (name) => lazy(() => import("./StaticPages").then((m) => ({ default: m[name] })));
const TermsAndConditions = staticPage("TermsAndConditions");
const PrivacyPolicy = staticPage("PrivacyPolicy");
const CancellationPolicy = staticPage("CancellationPolicy");

const HOME_PATH = "/";
const ADMIN_PATH = "/admin";
const ADMIN_CHECK_IN_PATH = "/admin/check-in";
const MY_EVENTS_PATH = "/my/events";
const SIGN_IN_PATH = "/signin";
const AUTH_CALLBACK_PATH = "/auth/callback";
const TERMS_PATH = "/terms";
const PRIVACY_PATH = "/privacy";
const CANCELLATION_PATH = "/cancellation";

// /events, /events/{slug}, /events/{slug}/register, /events/{slug}/registered
export function parseEventPath(path) {
  const match = /^\/events(?:\/([a-z0-9-]+)(?:\/(register|registered))?)?\/?$/.exec(path);
  return match ? { slug: match[1] || "", view: match[2] || (match[1] ? "page" : "list") } : null;
}

const FIXED_PATHS = [HOME_PATH, ADMIN_PATH, ADMIN_CHECK_IN_PATH, MY_EVENTS_PATH, SIGN_IN_PATH, AUTH_CALLBACK_PATH, TERMS_PATH, PRIVACY_PATH, CANCELLATION_PATH];
export const isKnownPath = (path) => FIXED_PATHS.includes(path) || !!parseEventPath(path);

export default function App() {
  return (
    <Suspense fallback={<div className="loading" role="status">{t("pub.loading")}</div>}>
      <AppRoutes />
    </Suspense>
  );
}

function AppRoutes() {
  const [currentPath, setCurrentPath] = useState(() => window.location.pathname || HOME_PATH);
  const { session } = useAuthSession(); // also restores a stored sign-in at start-up

  useEffect(() => {
    const onPopState = () => setCurrentPath(window.location.pathname || HOME_PATH);
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigateTo = (target, { replace = false } = {}) => {
    // Targets may carry a ?query or #hash; routing uses the path only.
    const path = target.split("#")[0].split("?")[0];
    if (window.location.pathname + window.location.search + window.location.hash !== target) {
      if (replace) window.history.replaceState({}, "", target);
      else window.history.pushState({}, "", target);
    }
    setCurrentPath(path);
    window.scrollTo(0, 0);
  };

  // Unknown addresses (including old links to pages that no longer exist) go home.
  useEffect(() => {
    if (!isKnownPath(currentPath)) navigateTo(HOME_PATH, { replace: true });
  }, [currentPath]); // eslint-disable-line react-hooks/exhaustive-deps -- navigateTo only touches history and state

  useEffect(() => {
    applyPageMeta(currentPath);
  }, [currentPath]);

  if (currentPath === HOME_PATH) return <HomePage navigateTo={navigateTo} />;
  if (currentPath === ADMIN_PATH) return <EventManager navigateTo={navigateTo} />;
  if (currentPath === ADMIN_CHECK_IN_PATH) return <CheckIn navigateTo={navigateTo} />;
  if (currentPath === SIGN_IN_PATH) return <SignInPage navigateTo={navigateTo} />;
  if (currentPath === AUTH_CALLBACK_PATH) return <AuthCallback navigateTo={navigateTo} />;
  if (currentPath === MY_EVENTS_PATH) return session ? <MyEventRegistrations navigateTo={navigateTo} /> : <SignInPage navigateTo={navigateTo} />;
  if (currentPath === TERMS_PATH) return <TermsAndConditions navigateTo={navigateTo} />;
  if (currentPath === PRIVACY_PATH) return <PrivacyPolicy navigateTo={navigateTo} />;
  if (currentPath === CANCELLATION_PATH) return <CancellationPolicy navigateTo={navigateTo} />;

  const route = parseEventPath(currentPath);
  if (route) {
    if (route.view === "list") return <EventsList navigateTo={navigateTo} />;
    if (route.view === "page") return <EventPage navigateTo={navigateTo} slug={route.slug} />;
    if (route.view === "register") return <EventRegister navigateTo={navigateTo} slug={route.slug} />;
    return <EventRegistered navigateTo={navigateTo} slug={route.slug} />;
  }
  return null;
}
