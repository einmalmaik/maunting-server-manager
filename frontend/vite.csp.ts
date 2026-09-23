import type { Plugin } from 'vite'

/**
 * Die Content-Security-Policy der Weboberfläche.
 *
 * Sie kommt als `<meta>` in die gebaute `index.html` und damit mit jedem
 * Update an. Der gleichlautende Caddy-Kopf in `install.sh` erreicht nur neue
 * Installationen — `update.sh` fasst die Caddy-Site nicht an.
 *
 * Was jede Freigabe trägt:
 * - `script-src` ohne `'unsafe-inline'`: eingeschleuster Text wird kein Code.
 *   `'wasm-unsafe-eval'` braucht `hash-wasm`.
 * - `img-src https:` für Avatare, die als fremde Adresse gespeichert sind.
 * - `media-src blob:` für entschlüsselte Anhänge und Videonotizen.
 * - `worker-src blob:` für den Kachel-Worker von MapLibre.
 * - `connect-src blob: data:` für `fetch` auf entschlüsselte Anhänge.
 *
 * `frame-ancestors` wirkt im `<meta>` nicht; das setzt Caddy.
 */
export const PANEL_CSP = [
  "default-src 'self'",
  "script-src 'self' 'wasm-unsafe-eval' https://singrabot.mauntingstudios.de https://client.crisp.chat https://embed.tawk.to",
  "style-src 'self' 'unsafe-inline' https://singrabot.mauntingstudios.de",
  "img-src 'self' data: blob: https:",
  "media-src 'self' blob: data: mediastream:",
  "worker-src 'self' blob:",
  "connect-src 'self' https: http: ws: wss: blob: data:",
  "font-src 'self' data: https://singrabot.mauntingstudios.de",
  "frame-src 'self' https://singrabot.mauntingstudios.de",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ')

/**
 * Setzt die Policy nur in den Bau, nicht in den Entwicklungsserver: dessen
 * React-Refresh steht als Inline-Skript in der Seite und fiele sonst unter
 * genau die Regel, um die es geht. Nur `index.html` — `desktop.html` läuft
 * unter der CSP von Tauri und lädt Bilder von einer fremden Herkunft.
 */
export function panelCsp(): Plugin {
  return {
    name: 'msm-panel-csp',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!ctx.filename.replace(/\\/g, '/').endsWith('/index.html')) return html
        return html.replace(
          '<meta charset="UTF-8" />',
          `<meta charset="UTF-8" />\n    <meta http-equiv="Content-Security-Policy" content="${PANEL_CSP}" />`,
        )
      },
    },
  }
}
