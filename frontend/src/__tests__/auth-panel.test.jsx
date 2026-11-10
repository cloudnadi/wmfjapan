import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cognitoError, sdkState } from "./sdkMock";
import { fakeIdToken } from "./fixtures";

vi.mock("@aws-sdk/client-cognito-identity-provider", async () => (await import("./sdkMock")).mockSdk());
vi.mock("../auth/social", async (importOriginal) => ({ ...(await importOriginal()), startSocialSignIn: vi.fn(async () => {}) }));

import AuthPanel from "../auth/AuthPanel";
import * as session from "../auth/session";
import { startSocialSignIn } from "../auth/social";

const authResult = () => ({ AuthenticationResult: { IdToken: fakeIdToken(), AccessToken: "a", RefreshToken: "r" } });
const names = () => sdkState.calls.map((c) => c.name);
const type = (label, value) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name) => fireEvent.click(screen.getByRole("button", { name }));

beforeEach(() => {
  sdkState.calls = [];
  sdkState.respond = null;
  session.__resetSessionForTests();
});

describe("sign in", () => {
  it("signs in with email and password and reports the session", async () => {
    sdkState.respond = () => authResult();
    const onAuthenticated = vi.fn();
    render(<AuthPanel onAuthenticated={onAuthenticated} />);
    type(/email address/i, "pat@example.com");
    type(/^password/i, "Passw0rd-test");
    click(/^sign in$/i);
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith(expect.objectContaining({ email: "pat@example.com" })));
    expect(session.isSignedIn()).toBe(true);
  });

  it("shows a generic message for wrong credentials (and for unknown users)", async () => {
    for (const name of ["NotAuthorizedException", "UserNotFoundException"]) {
      sdkState.respond = () => { throw cognitoError(name); };
      const { unmount } = render(<AuthPanel onAuthenticated={vi.fn()} />);
      type(/email address/i, "pat@example.com");
      type(/^password/i, "wrong");
      click(/^sign in$/i);
      expect((await screen.findByRole("alert")).textContent).toBe("The email or password is incorrect.");
      unmount();
    }
  });

  it("validates before calling Cognito, with accessible error messages", async () => {
    render(<AuthPanel onAuthenticated={vi.fn()} />);
    click(/^sign in$/i);
    expect(await screen.findByText("Enter your email address.")).toBeTruthy();
    expect(screen.getByText("Enter your password.")).toBeTruthy();
    expect(screen.getByLabelText(/email address/i).getAttribute("aria-invalid")).toBe("true");
    type(/email address/i, "not-an-email");
    click(/^sign in$/i);
    expect(await screen.findByText("Enter a valid email address.")).toBeTruthy();
    expect(sdkState.calls).toHaveLength(0);
  });

  it("an unverified account is sent to email verification, then signed in with the same password", async () => {
    sdkState.respond = (name) => {
      if (name === "InitiateAuthCommand" && !names().includes("ConfirmSignUpCommand")) throw cognitoError("UserNotConfirmedException");
      if (name === "InitiateAuthCommand") return authResult();
      return {};
    };
    const onAuthenticated = vi.fn();
    render(<AuthPanel onAuthenticated={onAuthenticated} />);
    type(/email address/i, "pat@example.com");
    type(/^password/i, "Passw0rd-test");
    click(/^sign in$/i);
    expect(await screen.findByRole("heading", { name: /verify your email/i })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("pat@example.com");
    expect(names()).toContain("ResendConfirmationCodeCommand");
    type(/verification code/i, "123456");
    click(/^verify email$/i);
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalled());
    expect(sdkState.calls.find((c) => c.name === "ConfirmSignUpCommand").input).toMatchObject({ ConfirmationCode: "123456", Username: "pat@example.com" });
  });
});

describe("create an account and verify the email", () => {
  const fill = () => {
    fireEvent.click(screen.getByRole("button", { name: /create an account/i }));
  };

  it("sign-up → verify → signed in", async () => {
    sdkState.respond = (name) => (name === "SignUpCommand" ? { UserConfirmed: false } : name === "InitiateAuthCommand" ? authResult() : {});
    const onAuthenticated = vi.fn();
    render(<AuthPanel onAuthenticated={onAuthenticated} />);
    fill();
    type(/name/i, "Pat");
    type(/email address/i, "pat@example.com");
    type(/^password/i, "Passw0rd-test");
    click(/^create account$/i);
    expect(await screen.findByRole("heading", { name: /verify your email/i })).toBeTruthy();
    type(/verification code/i, "654321");
    click(/^verify email$/i);
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalled());
    expect(names()).toEqual(["SignUpCommand", "ConfirmSignUpCommand", "InitiateAuthCommand"]);
  });

  it("explains a duplicate account and a weak password", async () => {
    render(<AuthPanel onAuthenticated={vi.fn()} initialMode="signUp" />);
    sdkState.respond = () => { throw cognitoError("UsernameExistsException"); };
    type(/email address/i, "pat@example.com");
    type(/^password/i, "Passw0rd-test");
    click(/^create account$/i);
    expect((await screen.findByRole("alert")).textContent).toMatch(/already exists/i);
    sdkState.respond = () => { throw cognitoError("InvalidPasswordException"); };
    click(/^create account$/i);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/requirements/i));
  });

  it("a wrong or expired code is explained and does not sign in", async () => {
    render(<AuthPanel onAuthenticated={vi.fn()} initialMode="verify" />);
    type(/email address/i, "pat@example.com");
    type(/verification code/i, "000000");
    sdkState.respond = () => { throw cognitoError("CodeMismatchException"); };
    click(/^verify email$/i);
    expect((await screen.findByRole("alert")).textContent).toMatch(/not correct/i);
    sdkState.respond = () => { throw cognitoError("ExpiredCodeException"); };
    click(/^verify email$/i);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/expired/i));
    expect(session.isSignedIn()).toBe(false);
  });

  it("resends the code, with a cool-down", async () => {
    sdkState.respond = () => ({});
    render(<AuthPanel onAuthenticated={vi.fn()} initialMode="verify" />);
    type(/email address/i, "pat@example.com");
    click(/send a new code/i);
    await waitFor(() => expect(names()).toContain("ResendConfirmationCodeCommand"));
    expect((await screen.findByRole("button", { name: /send a new code \(\d+\)/i })).disabled).toBe(true);
  });
});

describe("password reset", () => {
  it("never reveals whether an account exists", async () => {
    sdkState.respond = () => { throw cognitoError("UserNotFoundException"); };
    render(<AuthPanel onAuthenticated={vi.fn()} initialMode="forgot" />);
    type(/email address/i, "nobody@example.com");
    click(/send reset code/i);
    expect(await screen.findByRole("heading", { name: /choose a new password/i })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/if an account exists/i);
  });

  it("request code → set new password → back to sign-in with a confirmation", async () => {
    sdkState.respond = () => ({});
    render(<AuthPanel onAuthenticated={vi.fn()} initialMode="forgot" />);
    type(/email address/i, "pat@example.com");
    click(/send reset code/i);
    await screen.findByRole("heading", { name: /choose a new password/i });
    type(/verification code/i, "112233");
    type(/^new password/i, "Another-Passw0rd");
    click(/set new password/i);
    expect(await screen.findByRole("heading", { name: /^sign in$/i })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/password has been changed/i);
    expect(sdkState.calls.at(-1)).toMatchObject({ name: "ConfirmForgotPasswordCommand", input: { ConfirmationCode: "112233", Password: "Another-Passw0rd" } });
  });

  it("a bad reset code is reported", async () => {
    render(<AuthPanel onAuthenticated={vi.fn()} initialMode="reset" />);
    sdkState.respond = () => { throw cognitoError("CodeMismatchException"); };
    type(/email address/i, "pat@example.com");
    type(/verification code/i, "1");
    type(/^new password/i, "Another-Passw0rd");
    click(/set new password/i);
    expect((await screen.findByRole("alert")).textContent).toMatch(/not correct/i);
  });
});

describe("session expiry, social buttons and availability", () => {
  it("tells the visitor when their session expired", async () => {
    window.localStorage.setItem(session.REFRESH_KEY, "stale");
    sdkState.respond = () => { throw cognitoError("NotAuthorizedException"); };
    await session.initSession();
    render(<AuthPanel onAuthenticated={vi.fn()} />);
    expect(screen.getByRole("status").textContent).toMatch(/session expired/i);
  });

  it("offers each configured social provider and saves work before leaving the page", async () => {
    const before = vi.fn();
    render(<AuthPanel onAuthenticated={vi.fn()} returnTo="/events/x/register" beforeSocialRedirect={before} />);
    fireEvent.click(screen.getByRole("button", { name: /continue with google/i }));
    await waitFor(() => expect(startSocialSignIn).toHaveBeenCalledWith("Google", "/events/x/register"));
    expect(before).toHaveBeenCalledTimes(1);
    expect(before.mock.invocationCallOrder[0]).toBeLessThan(startSocialSignIn.mock.invocationCallOrder[0]);
  });

  it("shows no social buttons when no provider is configured", () => {
    vi.stubEnv("VITE_AUTH_SOCIAL_PROVIDERS", "");
    render(<AuthPanel onAuthenticated={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /continue with/i })).toBeNull();
  });

  it("cancelled or failed social start is reported without leaving the page", async () => {
    startSocialSignIn.mockRejectedValueOnce(new Error("boom"));
    render(<AuthPanel onAuthenticated={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /continue with google/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/something went wrong/i);
  });

  it("without Cognito configuration it says so and keeps the guest path open", () => {
    vi.stubEnv("VITE_AUTH_CLIENT_ID", "");
    const onGuest = vi.fn();
    render(<AuthPanel onAuthenticated={vi.fn()} onGuest={onGuest} />);
    expect(screen.getByRole("status").textContent).toMatch(/isn't set up/i);
    fireEvent.click(screen.getByRole("button", { name: /continue as a guest/i }));
    expect(onGuest).toHaveBeenCalled();
  });

  it("offers 'continue as a guest' only when the caller allows it", () => {
    const { rerender } = render(<AuthPanel onAuthenticated={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /continue as a guest/i })).toBeNull();
    rerender(<AuthPanel onAuthenticated={vi.fn()} onGuest={() => {}} />);
    expect(screen.getByRole("button", { name: /continue as a guest/i })).toBeTruthy();
  });
});
