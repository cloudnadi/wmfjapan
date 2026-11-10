import { useEffect, useState } from "react";
import EventShell from "../EventShell";
import { t } from "../i18n";
import { SocialAuthError, completeSocialSignIn } from "./social";

// React StrictMode runs effects twice in development; an authorization code can be exchanged only once.
let inFlight = null;

/** /auth/callback: finishes social sign-in, then returns to the page the visitor came from. */
export default function AuthCallback({ navigateTo }) {
  const [failure, setFailure] = useState(null);

  useEffect(() => {
    let alive = true;
    inFlight ||= completeSocialSignIn();
    inFlight
      .then(({ returnTo }) => alive && navigateTo(returnTo, { replace: true }))
      .catch((error) => alive && setFailure(error instanceof SocialAuthError ? error : new SocialAuthError("exchange_failed")))
      .finally(() => { inFlight = null; });
    return () => { alive = false; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- run once per visit to the callback

  return (
    <EventShell navigateTo={navigateTo}>
      <h1>{t("auth.callback.title")}</h1>
      {!failure && <p role="status">{t("auth.callback.working")}</p>}
      {failure && (
        <>
          <div className="error-banner" role="alert">{t(`auth.callback.${failure.code}`)}</div>
          <button type="button" className="button primary" onClick={() => navigateTo(failure.returnTo && failure.returnTo !== "/" ? failure.returnTo : "/signin", { replace: true })}>
            {t("auth.callback.retry")}
          </button>
        </>
      )}
    </EventShell>
  );
}
