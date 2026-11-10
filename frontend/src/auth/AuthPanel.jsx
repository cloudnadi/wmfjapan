import { useEffect, useRef, useState } from "react";
import { t } from "../i18n";
import {
  AuthError,
  confirmPasswordReset,
  confirmSignUp,
  requestPasswordReset,
  resendConfirmationCode,
  signInWithPassword,
  signUp,
} from "./cognitoApi";
import { PROVIDER_LABELS, getAuthConfig, isAuthConfigured, isSocialConfigured } from "./config";
import { consumeEndReason, establishSession } from "./session";
import { SocialAuthError, startSocialSignIn } from "./social";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const RESEND_COOLDOWN_SECONDS = 30;

function Field({ id, label, error, hint, children }) {
  const describedBy = [error && `${id}-error`, hint && `${id}-hint`].filter(Boolean).join(" ") || undefined;
  return (
    <div className="auth-field">
      <label htmlFor={id}>{label}</label>
      {children({ id, "aria-invalid": error ? "true" : undefined, "aria-describedby": describedBy })}
      {hint && <p id={`${id}-hint`} className="auth-hint">{hint}</p>}
      {error && <p id={`${id}-error`} className="field-error" role="alert">{error}</p>}
    </div>
  );
}

/**
 * Email + password accounts (sign in, sign up, verify email, forgot/reset password) and social sign-in.
 *
 * @param {object} props
 * @param {(session:object)=>void} props.onAuthenticated   called once a session exists
 * @param {string} [props.returnTo]                         where social sign-in returns to (validated again on return)
 * @param {()=>void} [props.onGuest]                         shows "Continue as a guest" when given
 * @param {()=>Promise<void>|void} [props.beforeSocialRedirect]  save in-progress work before the page is left
 * @param {string} [props.initialMode]
 */
export default function AuthPanel({ onAuthenticated, returnTo = "/", onGuest, beforeSocialRedirect, initialMode = "signIn", notice: initialNotice = "" }) {
  const [mode, setMode] = useState(initialMode); // signIn | signUp | verify | forgot | reset
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState(() => initialNotice || (consumeEndReason() === "expired" ? t("auth.notice.sessionExpired") : ""));
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const headingRef = useRef(null);
  const config = getAuthConfig();

  useEffect(() => { headingRef.current?.focus(); }, [mode]);
  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  if (!isAuthConfigured()) {
    return (
      <section className="pe-box auth-panel">
        <p role="status">{t("auth.unavailable")}</p>
        {onGuest && <button type="button" className="button secondary" onClick={onGuest}>{t("auth.link.guest")}</button>}
      </section>
    );
  }

  const go = (next, message = "") => {
    setMode(next);
    setErrors({});
    setFormError("");
    setNotice(message);
    setCode("");
  };

  const fail = (error) => {
    const code = error instanceof AuthError ? error.code : "unknown";
    setFormError(t(`auth.error.${code}`));
    return code;
  };

  const validate = (needs) => {
    const next = {};
    if (needs.includes("email")) {
      if (!email.trim()) next.email = t("auth.error.email_required");
      else if (!EMAIL_RE.test(email.trim())) next.email = t("auth.error.email_invalid");
    }
    if (needs.includes("password") && !password) next.password = t("auth.error.password_required");
    if (needs.includes("code") && !code.trim()) next.code = t("auth.error.code_required");
    setErrors(next);
    setFormError("");
    return Object.keys(next).length === 0;
  };

  const finishSignIn = async () => {
    const result = await signInWithPassword(email, password);
    const session = establishSession(result);
    setPassword("");
    onAuthenticated?.(session);
  };

  const run = (needs, action) => async (event) => {
    event.preventDefault();
    if (busy || !validate(needs)) return;
    setBusy(true);
    try {
      await action();
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const submitSignIn = run(["email", "password"], async () => {
    try {
      await finishSignIn();
    } catch (error) {
      if (error instanceof AuthError && error.code === "user_not_confirmed") {
        await resendConfirmationCode(email).catch(() => {});
        go("verify", t("auth.notice.verifyEmailSent", { email: email.trim() }));
        setCooldown(RESEND_COOLDOWN_SECONDS);
        return;
      }
      throw error;
    }
  });

  const submitSignUp = run(["email", "password"], async () => {
    const { confirmed } = await signUp({ email, password, name });
    if (confirmed) return finishSignIn();
    go("verify", t("auth.notice.verifyEmailSent", { email: email.trim() }));
    setCooldown(RESEND_COOLDOWN_SECONDS);
    return undefined;
  });

  const submitVerify = run(["email", "code"], async () => {
    await confirmSignUp(email, code);
    if (password) {
      try {
        await finishSignIn(); // we still have the password from this visit: no second sign-in step
        return;
      } catch {
        // fall through to the sign-in form
      }
    }
    go("signIn", t("auth.notice.verified"));
  });

  const resend = async () => {
    if (busy || cooldown > 0) return;
    setBusy(true);
    try {
      await resendConfirmationCode(email);
      setNotice(t("auth.notice.resent"));
      setFormError("");
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const submitForgot = run(["email"], async () => {
    try {
      await requestPasswordReset(email);
    } catch (error) {
      // Do not reveal whether the address has an account: only surface problems that are not about that.
      if (!(error instanceof AuthError) || !["bad_credentials", "user_not_confirmed", "invalid_input"].includes(error.code)) throw error;
    }
    go("reset", t("auth.notice.resetSent"));
  });

  const submitReset = run(["email", "code", "password"], async () => {
    await confirmPasswordReset(email, code, password);
    setPassword("");
    go("signIn", t("auth.notice.resetDone"));
  });

  const social = async (provider) => {
    setFormError("");
    try {
      await beforeSocialRedirect?.();
      await startSocialSignIn(provider, returnTo);
    } catch (error) {
      setFormError(t(error instanceof SocialAuthError && error.code === "not_configured" ? "auth.error.not_configured" : "auth.error.unknown"));
    }
  };

  const titles = { signIn: "auth.signIn.title", signUp: "auth.signUp.title", verify: "auth.verify.title", forgot: "auth.forgot.title", reset: "auth.reset.title" };
  const emailField = (autoComplete = "email") => (
    <Field id="auth-email" label={t("auth.field.email")} error={errors.email}>
      {(a11y) => <input {...a11y} type="email" inputMode="email" autoComplete={autoComplete} value={email} required
        onChange={(e) => setEmail(e.target.value)} />}
    </Field>
  );
  const passwordField = (isNew) => (
    <Field id="auth-password" label={t(isNew && mode === "reset" ? "auth.field.newPassword" : "auth.field.password")} error={errors.password}
      hint={isNew ? t("auth.hint.password") : undefined}>
      {(a11y) => (
        <>
          <input {...a11y} type={showPassword ? "text" : "password"} autoComplete={isNew ? "new-password" : "current-password"}
            value={password} required onChange={(e) => setPassword(e.target.value)} />
          <label className="auth-show"><input type="checkbox" checked={showPassword} onChange={(e) => setShowPassword(e.target.checked)} /> {t("auth.field.showPassword")}</label>
        </>
      )}
    </Field>
  );
  const codeField = () => (
    <Field id="auth-code" label={t("auth.field.code")} error={errors.code} hint={t("auth.hint.code")}>
      {(a11y) => <input {...a11y} type="text" inputMode="numeric" autoComplete="one-time-code" value={code} required
        onChange={(e) => setCode(e.target.value)} />}
    </Field>
  );
  const link = (label, to) => <button type="button" className="pe-link" onClick={() => go(to)}>{label}</button>;
  const submit = (label) => <button type="submit" className="button primary" disabled={busy}>{busy ? t("auth.action.working") : label}</button>;

  return (
    <section className="pe-box auth-panel" aria-labelledby="auth-title">
      <h2 id="auth-title" tabIndex={-1} ref={headingRef}>{t(titles[mode])}</h2>
      <div aria-live="polite">{notice && <p className="success-banner" role="status">{notice}</p>}</div>
      {formError && <div className="error-banner" role="alert">{formError}</div>}

      {mode === "signIn" && (
        <form onSubmit={submitSignIn} noValidate>
          {emailField()}
          {passwordField(false)}
          <div className="btn-row">{submit(t("auth.action.signIn"))}</div>
          <p className="auth-links">{link(t("auth.link.forgot"), "forgot")} · {link(t("auth.link.create"), "signUp")}</p>
        </form>
      )}

      {mode === "signUp" && (
        <form onSubmit={submitSignUp} noValidate>
          <Field id="auth-name" label={t("auth.field.name")}>
            {(a11y) => <input {...a11y} type="text" autoComplete="name" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />}
          </Field>
          {emailField()}
          {passwordField(true)}
          <div className="btn-row">{submit(t("auth.action.signUp"))}</div>
          <p className="auth-links">{link(t("auth.link.haveAccount"), "signIn")}</p>
        </form>
      )}

      {mode === "verify" && (
        <form onSubmit={submitVerify} noValidate>
          {emailField()}
          {codeField()}
          <div className="btn-row">
            {submit(t("auth.action.verify"))}
            <button type="button" className="button secondary" onClick={resend} disabled={busy || cooldown > 0 || !EMAIL_RE.test(email.trim())}>
              {cooldown > 0 ? `${t("auth.action.resend")} (${cooldown})` : t("auth.action.resend")}
            </button>
          </div>
          <p className="auth-links">{link(t("auth.link.backToSignIn"), "signIn")}</p>
        </form>
      )}

      {mode === "forgot" && (
        <form onSubmit={submitForgot} noValidate>
          {emailField()}
          <div className="btn-row">{submit(t("auth.action.sendCode"))}</div>
          <p className="auth-links">{link(t("auth.link.backToSignIn"), "signIn")}</p>
        </form>
      )}

      {mode === "reset" && (
        <form onSubmit={submitReset} noValidate>
          {emailField()}
          {codeField()}
          {passwordField(true)}
          <div className="btn-row">{submit(t("auth.action.reset"))}</div>
          <p className="auth-links">{link(t("auth.link.backToSignIn"), "signIn")}</p>
        </form>
      )}

      {isSocialConfigured() && ["signIn", "signUp"].includes(mode) && (
        <div className="auth-social">
          <p className="auth-or" aria-hidden="true">{t("auth.social.or")}</p>
          {config.socialProviders.map((provider) => (
            <button key={provider} type="button" className="button secondary" onClick={() => social(provider)}>
              {t("auth.social.continueWith", { provider: PROVIDER_LABELS[provider] || provider })}
            </button>
          ))}
        </div>
      )}

      {onGuest && (
        <p className="auth-guest"><button type="button" className="button secondary" onClick={onGuest}>{t("auth.link.guest")}</button></p>
      )}
    </section>
  );
}
