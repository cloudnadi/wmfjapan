/**
 * Thin wrapper over the Cognito Identity Provider API for attendee accounts (public, unauthenticated
 * operations only: no AWS credentials, no client secret). The SDK is loaded on first use so pages that
 * never sign anyone in do not download it.
 */
import { getAuthConfig } from "./config";

let sdkPromise;
let client;

const loadSdk = () => (sdkPromise ||= import("@aws-sdk/client-cognito-identity-provider"));

async function send(commandName, input) {
  const sdk = await loadSdk();
  client ||= new sdk.CognitoIdentityProviderClient({ region: getAuthConfig().region });
  return client.send(new sdk[commandName]({ ClientId: getAuthConfig().clientId, ...input }));
}

/** Map any thrown SDK/network error to a stable code the UI can translate. Never exposes raw messages. */
export function mapAuthError(error) {
  const name = error?.name || error?.code || "";
  switch (name) {
    case "UsernameExistsException":
    case "AliasExistsException":
      return "account_exists";
    case "UserNotConfirmedException":
      return "user_not_confirmed";
    case "NotAuthorizedException":
    case "UserNotFoundException":
      return "bad_credentials"; // one answer for both: do not reveal whether an address has an account
    case "CodeMismatchException":
      return "code_invalid";
    case "ExpiredCodeException":
      return "code_expired";
    case "InvalidPasswordException":
      return "password_policy";
    case "InvalidParameterException":
      return "invalid_input";
    case "LimitExceededException":
    case "TooManyRequestsException":
    case "TooManyFailedAttemptsException":
      return "rate_limited";
    case "ResourceNotFoundException":
      return "not_configured";
    default:
      break;
  }
  if (error instanceof TypeError || /network|fetch/i.test(String(error?.message || ""))) return "network";
  return "unknown";
}

export class AuthError extends Error {
  constructor(error) {
    const code = error instanceof AuthError ? error.code : mapAuthError(error);
    super(code);
    this.name = "AuthError";
    this.code = code;
  }
}

async function guarded(commandName, input) {
  try {
    return await send(commandName, input);
  } catch (error) {
    throw new AuthError(error);
  }
}

const normEmail = (email) => String(email || "").trim().toLowerCase();

export async function signUp({ email, password, name }) {
  const attributes = [{ Name: "email", Value: normEmail(email) }];
  if (name && name.trim()) attributes.push({ Name: "name", Value: name.trim() });
  const result = await guarded("SignUpCommand", { Username: normEmail(email), Password: password, UserAttributes: attributes });
  return { confirmed: !!result.UserConfirmed };
}

export const confirmSignUp = (email, code) =>
  guarded("ConfirmSignUpCommand", { Username: normEmail(email), ConfirmationCode: String(code).trim() });

export const resendConfirmationCode = (email) =>
  guarded("ResendConfirmationCodeCommand", { Username: normEmail(email) });

/** Returns Cognito's AuthenticationResult ({IdToken, AccessToken, RefreshToken, ExpiresIn}). */
export async function signInWithPassword(email, password) {
  const result = await guarded("InitiateAuthCommand", {
    AuthFlow: "USER_PASSWORD_AUTH",
    AuthParameters: { USERNAME: normEmail(email), PASSWORD: password },
  });
  if (!result.AuthenticationResult) throw new AuthError({ name: "UnsupportedChallenge" }); // MFA etc. are not enabled on this pool
  return result.AuthenticationResult;
}

export const requestPasswordReset = (email) => guarded("ForgotPasswordCommand", { Username: normEmail(email) });

export const confirmPasswordReset = (email, code, newPassword) =>
  guarded("ConfirmForgotPasswordCommand", {
    Username: normEmail(email),
    ConfirmationCode: String(code).trim(),
    Password: newPassword,
  });

export async function refreshAuthResult(refreshToken) {
  const result = await guarded("InitiateAuthCommand", {
    AuthFlow: "REFRESH_TOKEN_AUTH",
    AuthParameters: { REFRESH_TOKEN: refreshToken },
  });
  return result.AuthenticationResult;
}

export async function globalSignOut(accessToken) {
  const sdk = await loadSdk();
  client ||= new sdk.CognitoIdentityProviderClient({ region: getAuthConfig().region });
  await client.send(new sdk.GlobalSignOutCommand({ AccessToken: accessToken }));
}
