import { useEffect, useRef, useState } from "react";
import Navbar from "./Navbar";
import { requestLoginOtp, verifyLoginOtp } from "./api";
import { saveRegistrantSession } from "./registrantAuth";
import { siteConfig } from "./siteConfig";

const HOME_PATH = "/";
const ADMIN_LOGIN_PATH = "/admin";
const EMAIL_REGEX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const DEFAULT_REGISTER_URL = siteConfig.memberRegisterUrl;

/**
 * Email → OTP gate for the registration page. Step "email" collects the
 * address and sends a code; step "otp" verifies it and, on success, persists a
 * session token and calls onAuthenticated().
 */
export default function RegistrationLogin({
  navigateTo,
  onAuthenticated,
  title = "Sign in to register",
  subtitle = siteConfig.name,
  description = "We'll email you a 6-digit verification code to access the registration form.",
}) {
  const [stage, setStage] = useState("email"); // 'email' | 'otp'
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [registerUrl, setRegisterUrl] = useState(""); // set when email isn't a known member
  const [isBusy, setIsBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const cooldownRef = useRef(null);

  useEffect(() => {
    return () => {
      if (cooldownRef.current) {
        clearInterval(cooldownRef.current);
      }
    };
  }, []);

  const startCooldown = (seconds) => {
    setCooldown(seconds);
    if (cooldownRef.current) {
      clearInterval(cooldownRef.current);
    }
    cooldownRef.current = setInterval(() => {
      setCooldown((prev) => {
        if (prev <= 1) {
          clearInterval(cooldownRef.current);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
  };

  const sendCode = async () => {
    setError("");
    setNotice("");
    setRegisterUrl("");
    const trimmed = email.trim().toLowerCase();
    if (!EMAIL_REGEX.test(trimmed)) {
      setError("Enter a valid email address.");
      return;
    }
    setIsBusy(true);
    try {
      const result = await requestLoginOtp(trimmed);
      setEmail(trimmed);
      setStage("otp");
      setNotice(result.message || "A verification code has been sent to your email.");
      startCooldown(result.cooldown || 30);
    } catch (err) {
      if (err.notRegistered) {
        setRegisterUrl(err.registerUrl || DEFAULT_REGISTER_URL);
        setError("");
      } else {
        setError(err.message || "Could not send the code. Please try again.");
      }
    } finally {
      setIsBusy(false);
    }
  };

  const verifyCode = async () => {
    setError("");
    const trimmed = code.trim();
    if (!/^\d{6}$/.test(trimmed)) {
      setError("Enter the 6-digit code from your email.");
      return;
    }
    setIsBusy(true);
    try {
      const result = await verifyLoginOtp(email, trimmed);
      saveRegistrantSession(result.email || email, result.token);
      onAuthenticated();
    } catch (err) {
      setError(err.message || "Verification failed. Please try again.");
    } finally {
      setIsBusy(false);
    }
  };

  const onEmailKeyDown = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendCode();
    }
  };

  const onCodeKeyDown = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      verifyCode();
    }
  };

  return (
    <>
      <Navbar
        navigateTo={navigateTo}
      />
      <main className="page-wrap regform-reg">
        <header className="event-header">
          <div className="banner-title">{title}</div>
          <div className="banner-sub-title">{subtitle}</div>
          <div className="sky-bar" />
        </header>

        <section className="form-card">
          <div className="form-body reg-login-body">
            {stage === "email" ? (
              <>
                <div className="section-label">Enter your email</div>
                <p className="field-note" style={{ marginBottom: "18px" }}>
                  {description}
                </p>
                <div className="field-row full">
                  <div className="field">
                    <label htmlFor="login-email">Email Address</label>
                    <input
                      id="login-email"
                      type="email"
                      inputMode="email"
                      autoComplete="email"
                      placeholder="your@email.com"
                      className={error ? "input-invalid" : ""}
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      onKeyDown={onEmailKeyDown}
                      autoFocus
                    />
                  </div>
                </div>

                {error ? <p className="error-banner">{error}</p> : null}

                {registerUrl ? (
                  <div className="not-registered-notice">
                    <p>
                      We couldn't find a membership for <strong>{email.trim().toLowerCase()}</strong>.
                      Please register as a member first, then come back to sign in.
                    </p>
                    <a className="btn-primary" href={registerUrl} target="_blank" rel="noopener noreferrer">
                      Register as a member →
                    </a>
                  </div>
                ) : null}

                <div className="btn-row">
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={sendCode}
                    disabled={isBusy}
                  >
                    {isBusy ? "Sending…" : "Send Code →"}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="section-label">Enter verification code</div>
                <p className="field-note" style={{ marginBottom: "18px" }}>
                  We sent a 6-digit code to <strong>{email}</strong>.{" "}
                  <button type="button" className="reg-login-link" onClick={() => setStage("email")}>
                    Change email
                  </button>
                </p>

                {notice ? <p className="success-banner">{notice}</p> : null}

                <div className="field-row full">
                  <div className="field">
                    <label htmlFor="login-code">6-Digit Code</label>
                    <input
                      id="login-code"
                      type="text"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      maxLength={6}
                      placeholder="••••••"
                      className={`reg-login-code ${error ? "input-invalid" : ""}`}
                      value={code}
                      onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                      onKeyDown={onCodeKeyDown}
                      autoFocus
                    />
                  </div>
                </div>

                {error ? <p className="error-banner">{error}</p> : null}

                <div className="btn-row">
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={verifyCode}
                    disabled={isBusy}
                  >
                    {isBusy ? "Verifying…" : "Verify & Continue →"}
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={sendCode}
                    disabled={isBusy || cooldown > 0}
                  >
                    {cooldown > 0 ? `Resend in ${cooldown}s` : "Resend Code"}
                  </button>
                </div>
              </>
            )}
          </div>
        </section>
      </main>
    </>
  );
}
