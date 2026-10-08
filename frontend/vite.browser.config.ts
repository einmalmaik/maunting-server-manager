import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

import autoprefixer from 'autoprefixer'
import tailwindcss from 'tailwindcss'

import { breitenAlsContainer } from './vite.breitenAlsContainer'
import { fontsourceWoff2Only } from './vite.fontsource'

/**
 * Der Browser-Bau (MSB — Maunting Secure Browser).
 *
 * Dieselbe Codebasis, dritter Einstieg: `browser.html` lädt
 * `src/browser/main.tsx`, und Tauri (`browser/src-tauri`) zeigt seine
 * Fenster auf das Ergebnis in `dist-browser/`.
 *
 * Port 1450: recovery belegt 1420, smart-system belegt 1430.
 */
export default defineConfig({
  plugins: [fontsourceWoff2Only(), react()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  css: {
    // Wie `postcss.config.js`, dazu die Breakpoints je Bereich statt je Fenster.
    postcss: { plugins: [tailwindcss(), autoprefixer(), breitenAlsContainer()] },
  },
  clearScreen: false,
  server: {
    host: process.env.TAURI_DEV_HOST || '0.0.0.0',
    port: 1450,
    strictPort: true,
  },
  build: {
    outDir: 'dist-browser',
    sourcemap: true,
    chunkSizeWarningLimit: 600,
    assetsInlineLimit: (filePath: string) =>
      /\.(woff2?|ttf|otf|eot)$/i.test(filePath) ? false : undefined,
    rollupOptions: {
      input: resolve(__dirname, 'browser.html'),
      output: {
        entryFileNames: 'assets/[name].[hash].js',
        chunkFileNames: 'assets/[name].[hash].js',
        assetFileNames: 'assets/[name].[hash][extname]',
      },
    },
  },
})
