/**
 * Inactivity timeout shared by the registrant and admin sessions.
 *
 * The last time the user interacted with the site is kept in localStorage, so
 * activity in any tab keeps every tab signed in. Once IDLE_TIMEOUT_MS passes
 * without activity, registrantAuth / adminSession treat their sessions as
 * expired (on top of the tokens' own server-side expiry).
 */
const ACTIVITY_KEY = "nk-last-activity";
export const IDLE_TIMEOUT_MS = 60 * 60 * 1000;

// Activity events fire constantly; persist at most this often.
const WRITE_THROTTLE_MS = 30 * 1000;
const CHECK_INTERVAL_MS = 60 * 1000;
const ACTIVITY_EVENTS = ["pointerdown", "keydown", "scroll", "touchstart", "mousemove"];

let lastWrite = 0;

function readLastActivity() {
  try {
    return Number(localStorage.getItem(ACTIVITY_KEY)) || 0;
  } catch {
    return 0;
  }
}

/** Record user activity. `force` skips the throttle (e.g. right after sign-in). */
export function markActivity({ force = false } = {}) {
  const now = Date.now();
  if (!force && now - lastWrite < WRITE_THROTTLE_MS) return;
  lastWrite = now;
  try {
    localStorage.setItem(ACTIVITY_KEY, String(now));
  } catch {
    // Storage disabled: the idle timeout simply doesn't apply.
  }
}

/** True once the user has been inactive for longer than IDLE_TIMEOUT_MS. */
export function isIdleExpired() {
  const last = readLastActivity();
  if (!last) {
    // A session from before this timeout existed: start the clock now.
    markActivity({ force: true });
    return false;
  }
  return Date.now() - last > IDLE_TIMEOUT_MS;
}

/**
 * Track activity and run `onCheck` every minute (and whenever the tab becomes
 * visible again, since background timers are throttled) so an idle session is
 * signed out even if the user never touches the page again. Returns a cleanup.
 */
export function startIdleWatcher(onCheck) {
  // Check before recording activity, so returning to a tab after an hour away
  // signs the user out instead of the first click reviving the session.
  const onActivity = () => {
    if (isIdleExpired()) onCheck();
    else markActivity();
  };
  const onVisible = () => {
    if (document.visibilityState === "visible") onCheck();
  };

  onCheck();
  ACTIVITY_EVENTS.forEach((type) => window.addEventListener(type, onActivity, { passive: true }));
  document.addEventListener("visibilitychange", onVisible);
  const timer = setInterval(onCheck, CHECK_INTERVAL_MS);

  return () => {
    ACTIVITY_EVENTS.forEach((type) => window.removeEventListener(type, onActivity));
    document.removeEventListener("visibilitychange", onVisible);
    clearInterval(timer);
  };
}
