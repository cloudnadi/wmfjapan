import EventShell from "./EventShell";
import AuthPanel from "./auth/AuthPanel";
import { sanitizeReturnPath } from "./auth/redirect";
import { useAuthSession } from "./auth/useAuth";
import { t } from "./i18n";

/** /signin?returnTo=/events/…  — sign in or create an account, then go back where you were (allow-listed paths only). */
export default function SignInPage({ navigateTo }) {
  const { session, restoring } = useAuthSession();
  const returnTo = sanitizeReturnPath(new URLSearchParams(window.location.search).get("returnTo"), "/my/events");

  return (
    <EventShell navigateTo={navigateTo}>
      <h1>{t("auth.signIn.title")}</h1>
      {session ? (
        <>
          <p role="status">{t("auth.signedInAs", { email: session.email })}</p>
          <button type="button" className="button primary" onClick={() => navigateTo(returnTo)}>{t("pub.continue")}</button>
        </>
      ) : (
        !restoring && <AuthPanel returnTo={returnTo} onAuthenticated={() => navigateTo(returnTo, { replace: true })} />
      )}
    </EventShell>
  );
}
