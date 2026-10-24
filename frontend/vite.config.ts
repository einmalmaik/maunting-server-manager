import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

import { panelCsp } from './vite.csp'
import { fontsourceWoff2Only } from './vite.fontsource'

export default defineConfig({
  plugins: [fontsourceWoff2Only(), react(), panelCsp()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 3000,
    /*
     * Keine eigenen CORS-Köpfe des Dev-Servers.
     *
     * Vite beantwortet Vorabanfragen (OPTIONS) selbst und setzt dabei nur
     * `Access-Control-Allow-Origin`, aber nie
     * `Access-Control-Allow-Credentials`. Der eigene Client schickt bei einer
     * absoluten API-Adresse `credentials: "include"` (gerätegekoppelte Apps:
     * MSS, Pontis, Handy). Der Browser verlangt für so eine Anfrage auch in
     * der Vorabanfrage `Allow-Credentials: true` — fehlt der Kopf, bricht er
     * mit „Failed to fetch“ ab, ohne dass die Anfrage je beim Backend
     * ankommt. Pontis auf `http://localhost:1450` scheiterte bis 22.10.2026
     * genau daran: Adresse eintragen ging (schlichtes GET), das Einlösen des
     * Kopplungscodes nicht (POST mit JSON).
     *
     * Mit `cors: false` reicht der Proxy die Vorabanfrage an FastAPI weiter,
     * und dort steht der vollständige Satz (Herkunft, Methoden, Köpfe,
     * Credentials). Im Betrieb liefert das Backend die Oberfläche selbst aus,
     * dort gilt dieser Block ohnehin nicht.
     */
    cors: false,
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
        // Kein `manualChunks`. Die alte Regel steckte jedes nicht erkannte
        // Paket in `vendor-utils` und alles mit „react" im Pfad in
        // `vendor-react`. Beide lud schon die Anmeldeseite: 3,6 MB mit
        // maplibre, livekit, argon2, CodeMirror und Markdown, obwohl die
        // Seiten dafür per `lazy()`/`import()` nachgeladen werden. Die
        // Standardaufteilung legt ein Paket in den Chunk der Seiten, die es
        // wirklich importieren.
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
