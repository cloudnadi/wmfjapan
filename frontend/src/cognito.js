import {
  CognitoIdentityProviderClient,
  InitiateAuthCommand,
  RespondToAuthChallengeCommand,
  ForgotPasswordCommand,
  ConfirmForgotPasswordCommand,
  ChangePasswordCommand,
  GlobalSignOutCommand,
} from "@aws-sdk/client-cognito-identity-provider";
import { clearAdminSession, getStoredAdminEmail, hasAdminSession, markAdminSignIn } from "./adminSession";

const COGNITO_CONFIG = {
  clientId: window.COGNITO_CLIENT_ID || import.meta.env.VITE_COGNITO_CLIENT_ID,
  region: window.COGNITO_REGION || import.meta.env.VITE_COGNITO_REGION || "ap-northeast-1",
};

if (!COGNITO_CONFIG.clientId) {
  console.warn(
    "⚠️  Cognito config incomplete. Set VITE_COGNITO_CLIENT_ID in .env or window globals"
  );
}

// No AWS credentials needed — public Cognito unauthenticated operations only.
const client = new CognitoIdentityProviderClient({ region: COGNITO_CONFIG.region });

function persistSession(email, authResult) {
  markAdminSignIn();
  localStorage.setItem("adminIdToken", authResult.IdToken);
  localStorage.setItem("adminAccessToken", authResult.AccessToken);
  localStorage.setItem("adminRefreshToken", authResult.RefreshToken);
  localStorage.setItem("adminEmail", email);
  return authResult.IdToken;
}

export async function loginUser(email, password) {
  const response = await client.send(
    new InitiateAuthCommand({
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: COGNITO_CONFIG.clientId,
      AuthParameters: { USERNAME: email, PASSWORD: password },
    })
  );

  if (response.ChallengeName === "NEW_PASSWORD_REQUIRED") {
    const err = new Error("New password required");
    err.code = "NEW_PASSWORD_REQUIRED";
    err.session = response.Session;
    err.userAttributes = JSON.parse(response.ChallengeParameters?.userAttributes || "{}");
    err.requiredAttributes = JSON.parse(response.ChallengeParameters?.requiredAttributes || "[]");
    throw err;
  }

  return persistSession(email, response.AuthenticationResult);
}

export async function completeNewPasswordChallenge(session, email, newPassword) {
  if (!session) {
    throw new Error("Password setup session expired. Please sign in again.");
  }

  const response = await client.send(
    new RespondToAuthChallengeCommand({
      ClientId: COGNITO_CONFIG.clientId,
      ChallengeName: "NEW_PASSWORD_REQUIRED",
      Session: session,
      ChallengeResponses: { USERNAME: email, NEW_PASSWORD: newPassword },
    })
  );

  return persistSession(email, response.AuthenticationResult);
}

export async function requestPasswordResetCode(email) {
  await client.send(
    new ForgotPasswordCommand({ ClientId: COGNITO_CONFIG.clientId, Username: email })
  );
  return "CODE_SENT";
}

export async function confirmPasswordReset(email, code, newPassword) {
  await client.send(
    new ConfirmForgotPasswordCommand({
      ClientId: COGNITO_CONFIG.clientId,
      Username: email,
      ConfirmationCode: code,
      Password: newPassword,
    })
  );
  return "SUCCESS";
}

export async function changePasswordAuthenticated(currentPassword, newPassword) {
  const accessToken = localStorage.getItem("adminAccessToken");
  if (!accessToken) {
    throw new Error("No authenticated user. Please login again.");
  }

  await client.send(
    new ChangePasswordCommand({
      AccessToken: accessToken,
      PreviousPassword: currentPassword,
      ProposedPassword: newPassword,
    })
  );
  return "SUCCESS";
}

export async function refreshIdToken() {
  const refreshToken = localStorage.getItem("adminRefreshToken");
  if (!refreshToken) {
    throw new Error("No refresh token available. Please login again.");
  }

  const response = await client.send(
    new InitiateAuthCommand({
      AuthFlow: "REFRESH_TOKEN_AUTH",
      ClientId: COGNITO_CONFIG.clientId,
      AuthParameters: { REFRESH_TOKEN: refreshToken },
    })
  );

  const { IdToken, AccessToken } = response.AuthenticationResult;
  localStorage.setItem("adminIdToken", IdToken);
  if (AccessToken) localStorage.setItem("adminAccessToken", AccessToken);
  return IdToken;
}

function sessionExpiredError() {
  const err = new Error("Your admin session has expired. Please sign in again.");
  err.status = 401;
  return err;
}

export async function getValidIdToken() {
  // hasAdminSession() also enforces the inactivity timeout (and clears).
  const token = hasAdminSession() ? localStorage.getItem("adminIdToken") : null;
  if (!token) {
    clearAdminSession();
    throw sessionExpiredError();
  }

  let expiresAt = 0;
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    expiresAt = payload.exp * 1000;
  } catch (e) {
    console.error("Error parsing token:", e);
  }

  if (Date.now() > expiresAt - 60000) {
    try {
      return await refreshIdToken();
    } catch (e) {
      // Refresh token expired/revoked: the session is over, not just this call.
      console.error("Admin token refresh failed:", e);
      clearAdminSession();
      throw sessionExpiredError();
    }
  }

  return token;
}

export function logout() {
  const accessToken = localStorage.getItem("adminAccessToken");

  clearAdminSession();

  // Best-effort server-side token revocation.
  if (accessToken) {
    client.send(new GlobalSignOutCommand({ AccessToken: accessToken })).catch(() => {});
  }
}

export function isAuthenticated() {
  return hasAdminSession();
}

export function getAdminEmail() {
  return getStoredAdminEmail();
}

