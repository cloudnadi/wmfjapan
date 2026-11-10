import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `@app` lets a plugin under ../plugins/*/frontend import the host app's own
// shared components/utilities (Navbar, auth) without a brittle relative path
// back out of its own directory. `@plugins` is the mirror: the host imports a
// plugin's pages by name instead of reaching into ../../plugins by hand.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.js"],
    include: ["src/__tests__/**/*.test.{js,jsx}"],
    restoreMocks: true,
  },
  resolve: {
    alias: {
      // The seat-selection plugin lives outside frontend/ (plugins/seat-selection/frontend)
      // so it stays a self-contained, host-agnostic package rather than app code.
      "seat-selection-plugin": fileURLToPath(new URL("../plugins/seat-selection/frontend", import.meta.url)),
      "@app": fileURLToPath(new URL("./src", import.meta.url)),
      "@plugins": fileURLToPath(new URL("../plugins", import.meta.url)),
    },
  },
});
