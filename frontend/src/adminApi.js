// Authenticated request helper for the admin API (/admin/*). The admin signs in with the admin Cognito pool; the API
// Gateway authorizer checks the ID token and the Lambda checks the Admins / EventManagers group.
import { notifyAdminUnauthorized } from "./adminSession";
import { getValidIdToken } from "./cognito";

export async function requestAdminJson(path, options = {}) {
  const idToken = await getValidIdToken();
  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL}${path}`, {
    ...options,
    headers: { Authorization: idToken, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    if (response.status === 401) notifyAdminUnauthorized();      // the authorizer rejected the token
    const err = new Error(data.error || (response.status === 401 ? "Your admin session has expired. Please sign in again." : "Request failed"));
    err.fields = data.fields || {};
    err.status = response.status;
    throw err;
  }
  return data;
}
