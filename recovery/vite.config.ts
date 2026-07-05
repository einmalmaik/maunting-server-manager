import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath } from 'node:url';

// Tauri dev server uses port 1420 (Tauri default).
const TAURI_PORT = 1420;

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },

  // Tauri expects a fixed port and the Vite default host.
  clearScreen: false,
  server: {
    port: TAURI_PORT,
    strictPort: true,
    host: '0.0.0.0',
    watch: {
      // Tell Vite to ignore watching src-tauri (Rust backend).
      ignored: ['**/src-tauri/**'],
    },
  },
  envPrefix: ['VITE_', 'TAURI_ENV_*'],
  build: {
    // Tauri webview supports modern ES; smaller bundle.
    target: 'esnext',
    chunkSizeWarningLimit: 600,
  },
  test: {
    globals: true,
    // Default to node environment: DIS crypto (WebCrypto + hash-wasm) needs the
    // Node global `crypto.subtle` provider, which DOM emulators do not expose.
    // Component tests override this per-file via `@vitest-environment jsdom`.
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    testTimeout: 60_000,
  },
});
