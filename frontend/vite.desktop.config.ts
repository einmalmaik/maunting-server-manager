import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

import { fontsourceWoff2Only } from './vite.fontsource'

/**
 * Der Desktop-Bau (MSS — Maunting Smart System).
 *
 * Dieselbe Codebasis, ein zweiter Einstieg: `desktop.html` lädt
 * `src/desktop/main.tsx`, und Tauri (`smart-system/src-tauri`) zeigt seine
 * Fenster auf das Ergebnis in `dist-desktop/`. Der Panel-Bau
 * (`vite.config.ts`) bleibt unberührt — zwei Konfigurationen, weil die
 * Artefakte an zwei verschiedene Orte gehören und der Dev-Server hier auf
 * dem Tauri-Port lauscht.
 *
 * Port 1430: recovery/ belegt 1420 — beide Apps müssen nebeneinander
 * entwickelbar sein (ein Arbeitsverzeichnis, mehrere Sessions).
 *
 * Kein `/api`-Proxy: die App spricht ihre konfigurierte API-Adresse absolut
 * an. Im Dev-Modus blockt deren CORS-Allowlist den Origin `localhost:1430` —
 * bekannt und hingenommen; der Betriebstest läuft mit der gebauten App
 * (Origin `tauri.localhost` steht in `TAURI_ORIGINS`).
 */
export default defineConfig({
  plugins: [fontsourceWoff2Only(), react()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  clearScreen: false,
  server: {
    host: process.env.TAURI_DEV_HOST || '0.0.0.0',
    port: 1430,
    strictPort: true,
  },
  build: {
    outDir: 'dist-desktop',
    sourcemap: true,
    chunkSizeWarningLimit: 600,
    // Wie im Panel-Bau: Schriften nie als data:-URI einbetten, sonst kehrt
    // sich `unicode-range` um (Begründung in vite.config.ts).
    assetsInlineLimit: (filePath: string) =>
      /\.(woff2?|ttf|otf|eot)$/i.test(filePath) ? false : undefined,
    rollupOptions: {
      input: resolve(__dirname, 'desktop.html'),
      output: {
        entryFileNames: 'assets/[name].[hash].js',
        chunkFileNames: 'assets/[name].[hash].js',
        assetFileNames: 'assets/[name].[hash][extname]',
      },
    },
  },
})
