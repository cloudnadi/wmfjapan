import { useState } from "react";
import { Input } from "./adminFields";
import { completeNewPasswordChallenge, confirmPasswordReset, loginUser, requestPasswordResetCode } from "./cognito";
import { t } from "./i18n";

/** Sign-in for organisers (the admin Cognito pool): password, first-sign-in password change, and password reset. */
export default function AdminLogin({ onSignedIn }) {
  const [mode, setMode] = useState("signIn"); // signIn | newPassword | forgot | reset
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const run = (action) => async (event) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err?.code === "NEW_PASSWORD_REQUIRED" ? "" : messageFor(err));
    } finally {
      setBusy(false);
    }
  };

  const signIn = run(async () => {
    try {
      await loginUser(email.trim(), password);
      onSignedIn();
    } catch (err) {
      if (err?.code === "NEW_PASSWORD_REQUIRED") {
        setChallenge(err.session);
        setPassword("");
        setNotice(t("admin.login.newPasswordNotice"));
        setMode("newPassword");
        return;
      }
      throw err;
    }
  });
  const setNew = run(async () => {
    await completeNewPasswordChallenge(challenge, email.trim(), password);
    onSignedIn();
  });
  const sendCode = run(async () => {
    await requestPasswordResetCode(email.trim());
    setNotice(t("admin.login.resetSent"));
    setMode("reset");
  });
  const reset = run(async () => {
    await confirmPasswordReset(email.trim(), code.trim(), password);
    setPassword("");
    setCode("");
    setNotice(t("admin.login.resetDone"));
    setMode("signIn");
  });

  const submit = { signIn, newPassword: setNew, forgot: sendCode, reset }[mode];
  const label = { signIn: "admin.login.signIn", newPassword: "admin.login.setPassword", forgot: "admin.login.sendCode", reset: "admin.login.setPassword" }[mode];

  return (
    <section className="form-card ep-card admin-login" aria-labelledby="admin-login-h">
      <h2 id="admin-login-h">{t("admin.login.title")}</h2>
      {notice && <p className="success-banner" role="status">{notice}</p>}
      {error && <div className="error-banner" role="alert">{error}</div>}
      <form onSubmit={submit} noValidate>
        {mode !== "newPassword" && (
          <Input id="admin-email" label={t("auth.field.email")} type="email" autoComplete="username" value={email} onChange={setEmail} required />
        )}
        {mode === "reset" && <Input id="admin-code" label={t("auth.field.code")} autoComplete="one-time-code" value={code} onChange={setCode} required />}
        {mode !== "forgot" && (
          <Input id="admin-password" label={t(mode === "signIn" ? "auth.field.password" : "auth.field.newPassword")} type="password"
            autoComplete={mode === "signIn" ? "current-password" : "new-password"} value={password} onChange={setPassword} required
            help={mode === "signIn" ? undefined : t("admin.login.passwordHelp")} />
        )}
        <div className="btn-row">
          <button type="submit" className="button primary" disabled={busy}>{busy ? t("auth.action.working") : t(label)}</button>
        </div>
      </form>
      {mode === "signIn" && (
        <p><button type="button" className="ep-link" onClick={() => { setError(""); setNotice(""); setMode("forgot"); }}>{t("auth.link.forgot")}</button></p>
      )}
      {(mode === "forgot" || mode === "reset") && (
        <p><button type="button" className="ep-link" onClick={() => { setError(""); setNotice(""); setMode("signIn"); }}>{t("auth.link.backToSignIn")}</button></p>
      )}
    </section>
  );
}

function messageFor(err) {
  switch (err?.name) {
    case "NotAuthorizedException":
    case "UserNotFoundException":
      return t("auth.error.bad_credentials");
    case "CodeMismatchException":
      return t("auth.error.code_invalid");
    case "ExpiredCodeException":
      return t("auth.error.code_expired");
    case "InvalidPasswordException":
      return t("auth.error.password_policy");
    case "LimitExceededException":
    case "TooManyRequestsException":
      return t("auth.error.rate_limited");
    default:
      return err instanceof TypeError ? t("auth.error.network") : t("auth.error.unknown");
  }
}
