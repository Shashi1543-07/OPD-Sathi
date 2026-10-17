import { defineConfig } from "vitest/config";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// The browser calls the API at /api on the page's own address, as the Docker build does
// through nginx. A phone that opens the dev server over Wi-Fi then reaches the API through
// the same port, instead of calling its own "localhost:8000".
const apiProxy = {
  "/api": {
    target: process.env.VITE_DEV_API_TARGET ?? "http://127.0.0.1:8000",
    rewrite: (path: string) => path.replace(/^\/api/, ""),
    ws: true,
    // The API scopes the staff refresh cookie to /auth; behind /api that is /api/auth.
    cookiePathRewrite: { "/auth": "/api/auth" },
  },
};

export default defineConfig({
  envDir: "../..",
  server: { proxy: apiProxy },
  preview: { proxy: apiProxy },
  plugins: [
    tailwindcss(),
    react(),
    VitePWA({
      // A new build (for example after start-demo.ps1 rebuilds the app) takes over at once and
      // reloads open pages. With "prompt" and no prompt shown, a kiosk tab that stays open
      // kept running the old version until every tab closed. A reload resumes a patient's
      // check-in from session storage, and a doctor's unsaved edit still asks before leaving.
      registerType: "autoUpdate",
      includeAssets: ["saathi-icon.svg"],
      manifest: {
        name: "OPD Saathi",
        short_name: "OPD Saathi",
        description: "Patient history-taking and queue status",
        theme_color: "#0F766E",
        start_url: "/kiosk",
        display: "standalone",
        icons: [
          {
            src: "/saathi-icon.svg",
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any",
          },
        ],
      },
      workbox: { globPatterns: ["index.html", "assets/**/*.{js,css,woff2}", "audio/**/*.{mp3,wav}"] },
    }),
  ],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test-setup.ts",
    include: ["src/**/*.test.{ts,tsx}", "src/**/*.spec.{ts,tsx}"],
    exclude: ["e2e/**"],
  },
});
