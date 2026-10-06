import { defineConfig } from "vitest/config";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    tailwindcss(),
    react(),
    VitePWA({
      registerType: "prompt",
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
