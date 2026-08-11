import { apiUrl } from '@/config/api'

/**
 * Loads / refreshes the support widget from GET /api/system/support-widget.
 * Call after saving widget settings (dispatch msm:support-widget-updated).
 */
const SCRIPT_ATTR = 'data-msm-support-widget'
const SINGRA_SCRIPT_SRC = 'https://singrabot.mauntingstudios.de/widget.js'

/**
 * Herkünfte, von denen ein Support-Widget-Skript geladen werden darf.
 *
 * Warum überhaupt eine Liste im Code: das Panel liefert eine CSP aus, deren
 * `script-src` kein 'unsafe-inline' und genau diese drei Hosts kennt
 * (backend/main.py, security_headers_middleware). Alles andere blockiert der
 * Browser dort ohnehin. Die Regel hier noch einmal auszusprechen kostet nichts
 * und schließt die Lücke in den Aufstellungen, in denen gar keine CSP beim
 * Browser ankommt (Caddy liefert die SPA selbst aus, getrenntes Hosting des
 * Frontends) — dort wäre ein frei eingetragenes Fremdskript sonst
 * Codeausführung auf der Loginseite jedes Besuchers, erreichbar über ein
 * gewöhnlich vergebbares Panelrecht. Ein vierter Anbieter kommt hierhin UND in
 * die CSP: bewusst und im Code, nicht über ein Eingabefeld.
 */
const ALLOWED_WIDGET_ORIGINS: readonly string[] = [
  'https://singrabot.mauntingstudios.de',
  'https://client.crisp.chat',
  'https://embed.tawk.to',
]

/**
 * Prüft eine Skript-Quelle gegen die Positivliste.
 *
 * Relative Pfade, `javascript:` und alles ohne https scheitern hier, weil
 * `new URL` sie entweder ablehnt oder auf eine Herkunft abbildet, die nicht in
 * der Liste steht. Bewusst ein Vergleich der vollen Herkunft und kein
 * Namensvergleich per Zeichenkette: `https://client.crisp.chat.angreifer.tld`
 * soll nicht durchrutschen.
 */
function isAllowedWidgetSrc(rawSrc: string): boolean {
  try {
    const url = new URL(rawSrc, window.location.origin)
    return url.protocol === 'https:' && ALLOWED_WIDGET_ORIGINS.includes(url.origin)
  } catch {
    return false
  }
}

export const SUPPORT_WIDGET_UPDATED_EVENT = 'msm:support-widget-updated'

interface PublicSupportWidget {
  enabled: boolean
  provider: string
  singra_widget_id?: string
  script_src?: string
  crisp_website_id?: string
  tawk_property_id?: string
  tawk_widget_id?: string
  custom_snippet?: string
}

function removeWidgetArtifacts() {
  document.querySelectorAll(`[${SCRIPT_ATTR}]`).forEach((el) => el.remove())
  document.getElementById('msm-support-widget-custom')?.remove()
}

function injectSingra(widgetId: string, scriptSrc: string) {
  if (!widgetId.trim()) return
  removeWidgetArtifacts()
  const script = document.createElement('script')
  script.setAttribute(SCRIPT_ATTR, 'singra')
  script.src = scriptSrc
  script.defer = true
  script.setAttribute('data-widget-id', widgetId)
  document.body.appendChild(script)
}

function injectCrisp(websiteId: string) {
  const id = websiteId.trim()
  if (!id) return
  removeWidgetArtifacts()
  // Crisp erwartet seine Konfiguration am `window`, bevor l.js startet. Früher
  // stand dafür ein Inline-Skript im Dokument — das kann unter der CSP des
  // Panels nie laufen (`script-src` ohne 'unsafe-inline'), das Widget blieb
  // also unsichtbar, ohne dass der Betreiber einen Fehler zu sehen bekam. Am
  // window gesetzt braucht es kein Inline-Skript mehr, und die Freigabe für
  // client.crisp.chat in der CSP greift endlich.
  const w = window as unknown as { $crisp?: unknown[]; CRISP_WEBSITE_ID?: string }
  w.$crisp = w.$crisp ?? []
  w.CRISP_WEBSITE_ID = id
  const script = document.createElement('script')
  script.setAttribute(SCRIPT_ATTR, 'crisp')
  script.src = 'https://client.crisp.chat/l.js'
  script.async = true
  document.body.appendChild(script)
}

function injectTawk(propertyId: string, widgetId: string) {
  const prop = propertyId.trim()
  const wid = widgetId.trim()
  if (!prop || !wid) return
  removeWidgetArtifacts()
  // Gleiche Geschichte wie bei Crisp: der von tawk.to dokumentierte Einbaucode
  // ist ein Inline-Skript und scheiterte deshalb still an der CSP. Die beiden
  // IDs sind jetzt Pfadsegmente einer URL; encodeURIComponent ersetzt das
  // frühere Wegschneiden einzelner Anführungszeichen, das Backslashes stehen
  // ließ und ohnehin nur nötig war, weil die IDs in einem String-Literal
  // landeten.
  const w = window as unknown as { Tawk_API?: Record<string, unknown>; Tawk_LoadStart?: Date }
  w.Tawk_API = w.Tawk_API ?? {}
  w.Tawk_LoadStart = new Date()
  const script = document.createElement('script')
  script.setAttribute(SCRIPT_ATTR, 'tawk')
  script.src = `https://embed.tawk.to/${encodeURIComponent(prop)}/${encodeURIComponent(wid)}`
  script.async = true
  script.setAttribute('charset', 'UTF-8')
  script.setAttribute('crossorigin', '*')
  document.body.appendChild(script)
}

function injectCustom(snippet: string) {
  if (!snippet.trim()) return
  removeWidgetArtifacts()
  // Das Snippet wird NICHT mehr als HTML in die Seite gehängt. Es stammt aus
  // einem Einstellungsfeld, das jeder Träger von `panel.settings.write` füllen
  // kann, und dieser Loader hängt an der Wurzel der App — er läuft also auch
  // auf /login, vor jeder Anmeldung, für jeden Besucher. Als innerHTML war das
  // Feld damit ein Weg, die Loginseite aller zu überschreiben; die alte
  // Schleife hat die enthaltenen <script>-Elemente sogar eigens neu aufgebaut
  // und dadurch wiederbelebt, obwohl innerHTML sie gerade stillgelegt hatte.
  //
  // Stattdessen wird das Snippet in einem toten Dokument gelesen — DOMParser
  // führt nichts aus und lädt nichts nach — und nur das übernommen, was ein
  // Widget wirklich braucht: externe Skripte von einer erlaubten Herkunft.
  // Markup, Stile und Inline-Skripte werden verworfen.
  const parsed = new DOMParser().parseFromString(snippet, 'text/html')
  let injected = 0
  parsed.querySelectorAll('script[src]').forEach((element) => {
    const src = element.getAttribute('src') ?? ''
    if (!isAllowedWidgetSrc(src)) return
    const script = document.createElement('script')
    script.setAttribute(SCRIPT_ATTR, 'custom')
    script.src = src
    script.async = true
    document.body.appendChild(script)
    injected += 1
  })
  if (injected === 0) {
    // Lieber eine laute Meldung in der Konsole als ein Widget, das ohne
    // erkennbaren Grund fehlt: genau dieses stille Scheitern war bei Crisp und
    // tawk.to der zweite Fehler in dieser Datei.
    console.warn(
      '[MSM] Support-Widget: aus dem eigenen Snippet wurde nichts übernommen. Erlaubt sind nur externe Script-Tags von: ' +
        ALLOWED_WIDGET_ORIGINS.join(', '),
    )
  }
}

export async function loadSupportWidget(): Promise<void> {
  try {
    const res = await fetch(apiUrl('/system/support-widget'), { cache: 'no-store' })
    const cfg: PublicSupportWidget | null = res.ok ? await res.json() : null
    if (!cfg?.enabled) {
      removeWidgetArtifacts()
      return
    }
    switch (cfg.provider) {
      case 'crisp':
        injectCrisp(cfg.crisp_website_id ?? '')
        break
      case 'tawk':
        injectTawk(cfg.tawk_property_id ?? '', cfg.tawk_widget_id ?? '')
        break
      case 'custom':
        injectCustom(cfg.custom_snippet ?? '')
        break
      case 'singra':
      default:
        injectSingra(cfg.singra_widget_id ?? '', cfg.script_src ?? SINGRA_SCRIPT_SRC)
    }
  } catch {
    removeWidgetArtifacts()
  }
}

export function notifySupportWidgetUpdated() {
  window.dispatchEvent(new CustomEvent(SUPPORT_WIDGET_UPDATED_EVENT))
}