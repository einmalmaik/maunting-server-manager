import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

/**
 * Wirft die `woff`-Rückfallebene aus den @fontsource-Stylesheets.
 *
 * Jede `@font-face`-Regel dort nennt zwei Dateien: `woff2` und dahinter
 * dasselbe Zeichen-Set noch einmal als `woff`. Vite sieht beide Verweise und
 * legt deshalb beide Dateien in den Build — 63 Dateien, die kein Browser je
 * abruft, der dieses Panel überhaupt starten kann. `woff2` versteht jede
 * Engine, die ES-Module, Service Worker und WebSockets versteht, und ohne die
 * kommt die Oberfläche keine Zeile weit.
 *
 * Der Eingriff ist absichtlich eng: greift der Ausdruck nicht mehr, weil
 * @fontsource seine Schreibweise ändert, bleibt das Stylesheet unverändert und
 * der Build enthält wieder beide Formate. Das ist Ballast, kein Defekt.
 *
 * `enforce: 'pre'` ist Pflicht — nach Vites CSS-Auflösung stünden statt der
 * Pfade bereits Asset-Kennungen da, und die Dateien wären längst eingeplant.
 */
function fontsourceWoff2Only(): Plugin {
  return {
    name: 'msm:fontsource-woff2-only',
    enforce: 'pre',
    transform(code, id) {
      if (!id.includes('@fontsource') || !id.endsWith('.css')) return null
      const stripped = code.replace(/,\s*url\([^)]+\.woff\)\s*format\(['"]woff['"]\)/g, '')
      return stripped === code ? null : { code: stripped, map: null }
    },
  }
}

export default defineConfig({
  plugins: [fontsourceWoff2Only(), react()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    // Same-origin proxy when VITE_API_URL is unset. For true split hosting
    // set VITE_API_URL=http://127.0.0.1:8000 and skip the proxy.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // Console WebSocket upgrades
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    chunkSizeWarningLimit: 600,
    /*
     * Schriften niemals als data:-URI in die CSS legen.
     *
     * Vites Standard bettet Assets unter 4 KB ein. Bei Schriften kehrt das den
     * Sinn von `unicode-range` um: eingebettet steckt das Zeichen-Set im
     * Stylesheet, und das lädt jeder Besucher vollständig — die deutsche
     * Oberfläche zöge sich die vietnamesischen und kyrillischen Schnitte als
     * Base64 mit, obwohl sie kein Zeichen daraus zeigt. Als eigene Datei lädt
     * der Browser sie erst, wenn ein Zeichen sie verlangt.
     */
    assetsInlineLimit: (filePath: string) =>
      /\.(woff2?|ttf|otf|eot)$/i.test(filePath) ? false : undefined,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].[hash].js',
        chunkFileNames: 'assets/[name].[hash].js',
        assetFileNames: 'assets/[name].[hash][extname]',
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('react') || id.includes('react-dom') || id.includes('react-router-dom')) {
              return 'vendor-react';
            }
            if (id.includes('lucide-react') || id.includes('clsx') || id.includes('tailwind-merge')) {
              return 'vendor-ui';
            }
            return 'vendor-utils';
          }
        }
      }
    }
  },
  test: {
    globals: true,
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
  },
})
