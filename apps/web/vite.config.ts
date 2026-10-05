import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'prompt',
      includeAssets: [],
      manifest: {
        name: 'OPD Saathi',
        short_name: 'OPD Saathi',
        description: 'Patient history-taking and queue status',
        theme_color: '#12665c',
        start_url: '/kiosk',
        display: 'standalone',
        icons: [],
      },
      workbox: { globPatterns: ['**/*.{js,css,html,woff2}'] },
    }),
  ],
  test: { environment: 'jsdom', setupFiles: './src/test-setup.ts' },
});
