import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Separate from vite.config.js on purpose: that file configures the dev server and the
// production build, and a test-only option there is one more thing that can affect a
// shipped bundle. This file is only ever read by `npm test`.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.js"],
    // The existing constants tests run under Node's own runner (`node --test`), which
    // predates this config and needs no DOM. Excluding them keeps one suite per runner
    // instead of two runners racing over the same files.
    include: ["src/**/*.test.jsx"],
  },
});
