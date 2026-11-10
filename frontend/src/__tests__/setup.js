import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Deterministic, secret-free configuration for every test (all values are obvious placeholders).
beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "https://api.test/prod");
  vi.stubEnv("VITE_AUTH_REGION", "ap-northeast-1");
  vi.stubEnv("VITE_AUTH_USER_POOL_ID", "ap-northeast-1_TESTPOOL");
  vi.stubEnv("VITE_AUTH_CLIENT_ID", "test-client-id");
  vi.stubEnv("VITE_AUTH_COGNITO_DOMAIN", "auth.example.test");
  vi.stubEnv("VITE_AUTH_SOCIAL_PROVIDERS", "Google");
  window.history.replaceState({}, "", "/");
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

// jsdom lacks these; the app only needs trivially working versions.
window.scrollTo = () => {};
