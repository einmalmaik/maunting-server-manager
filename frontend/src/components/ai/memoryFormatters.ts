/**
 * Hilfsfunktionen zur Formatierung von Memory-Schlüsseln und -Metadaten.
 *
 * Verwandelt technische Schlüssel (z. B. "vorliebe-getraenke" oder "vorliebe_getraenke")
 * in lesbaren Text (z. B. "Vorliebe: Getränke") und bereitet Datumsangaben für das
 * Logbuch auf.
 */

const ACRONYMS: Record<string, string> = {
  ram: 'RAM',
  cpu: 'CPU',
  ip: 'IP',
  url: 'URL',
  uri: 'URI',
  id: 'ID',
  ai: 'AI',
  ki: 'KI',
  os: 'OS',
  ui: 'UI',
  api: 'API',
  dns: 'DNS',
  ssh: 'SSH',
  ftp: 'FTP',
  tls: 'TLS',
  ssl: 'SSL',
  tts: 'TTS',
  sse: 'SSE',
  dis: 'DIS',
  db: 'DB',
  io: 'I/O',
  llm: 'LLM',
  gpt: 'GPT',
  json: 'JSON',
  yaml: 'YAML',
  xml: 'XML',
  html: 'HTML',
  css: 'CSS',
  sql: 'SQL',
  http: 'HTTP',
  https: 'HTTPS',
}

const GERMAN_TRANSLITERATIONS: Record<string, string> = {
  getraenke: 'Getränke',
  getraenk: 'Getränk',
  praeferenz: 'Präferenz',
  praeferenzen: 'Präferenzen',
  qualitaet: 'Qualität',
  kapazitaet: 'Kapazität',
  prioritaet: 'Priorität',
  aktivitaet: 'Aktivität',
  uebersicht: 'Übersicht',
  ausfuehren: 'Ausführen',
  ausfuehrung: 'Ausführung',
  loeschen: 'Löschen',
  loeschung: 'Löschung',
  waehlen: 'Wählen',
  auswaehlen: 'Auswählen',
  aehnlich: 'Ähnlich',
  hinzufuegen: 'Hinzufügen',
  schluessel: 'Schlüssel',
  groesse: 'Größe',
  schriftgroesse: 'Schriftgröße',
  rueckruf: 'Rückruf',
  rueckgaengig: 'Rückgängig',
  zusaetzlich: 'Zusätzlich',
  bevorzugt: 'Bevorzugt',
  oberflaeche: 'Oberfläche',
  bestaetigung: 'Bestätigung',
  taeglich: 'Täglich',
  woechentlich: 'Wöchentlich',
  monatlich: 'Monatlich',
  erklaerung: 'Erklärung',
  uebertragung: 'Übertragung',
  ueberwachung: 'Überwachung',
  einschraenkung: 'Einschränkung',
  schwaeche: 'Schwäche',
  staerke: 'Stärke',
}

const CATEGORY_PREFIXES: Record<string, string> = {
  vorliebe: 'Vorliebe',
  praeferenz: 'Präferenz',
  preference: 'Preference',
  einstellung: 'Einstellung',
  setting: 'Setting',
  settings: 'Settings',
  kategorie: 'Kategorie',
  category: 'Category',
  option: 'Option',
  optionen: 'Optionen',
  konfiguration: 'Konfiguration',
  config: 'Config',
  profil: 'Profil',
  profile: 'Profile',
  favorite: 'Favorite',
  favorit: 'Favorit',
}

function formatSingleWord(word: string): string {
  if (!word) return ''
  const lower = word.toLowerCase()
  if (ACRONYMS[lower]) return ACRONYMS[lower]
  if (GERMAN_TRANSLITERATIONS[lower]) return GERMAN_TRANSLITERATIONS[lower]
  return word.charAt(0).toUpperCase() + word.slice(1)
}

function formatWordGroup(text: string): string {
  // Support camelCase, kebab-case, snake_case und Leerzeichen
  const normalized = text.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
  const words = normalized.split(/[-_ ]+/).filter(Boolean)
  return words.map(formatSingleWord).join(' ')
}

/**
 * Formatiert einen technischen Schlüssel in eine saubere, lesbare Beschriftung.
 *
 * Beispiele:
 * - "vorliebe-getraenke"        -> "Vorliebe: Getränke"
 * - "vorliebe_getraenke"        -> "Vorliebe: Getränke"
 * - "praeferenz-sprache"        -> "Präferenz: Sprache"
 * - "response.language"         -> "Response: Language"
 * - "ram.bevorzugt"             -> "RAM: Bevorzugt"
 * - "server:backup.frequency"   -> "Server: Backup: Frequency"
 * - "start-timeout"             -> "Start Timeout"
 * - "favoriteDrink"             -> "Favorite Drink"
 */
export function formatMemoryKey(rawKey: string): string {
  if (!rawKey) return ''
  const trimmed = rawKey.trim()
  if (!trimmed) return ''

  // Fall 1: Bereits durch Doppelpunkt oder Punkt strukturiert (z. B. "response.language", "server:backup")
  if (trimmed.includes('.') || trimmed.includes(':')) {
    const parts = trimmed.split(/[:.]+/).map((p) => p.trim()).filter(Boolean)
    return parts.map(formatWordGroup).join(': ')
  }

  // Fall 2: Schlüssel mit Kategorie-Präfix (z. B. "vorliebe-getraenke", "praeferenz_theme")
  const prefixMatch = trimmed.match(/^([a-zA-Z]+)[-_](.+)$/)
  if (prefixMatch) {
    const prefixCandidate = prefixMatch[1].toLowerCase()
    if (CATEGORY_PREFIXES[prefixCandidate]) {
      const categoryLabel = CATEGORY_PREFIXES[prefixCandidate]
      const restLabel = formatWordGroup(prefixMatch[2])
      return `${categoryLabel}: ${restLabel}`
    }
  }

  // Fall 3: Allgemeiner Kebab-, Snake- oder Camel-Case (z. B. "start-timeout", "theme_mode", "favoriteDrink")
  return formatWordGroup(trimmed)
}

/**
 * Formatiert einen ISO-Datumsstring in eine kompakte Datumsanzeige (z. B. "01.08.2026").
 */
export function formatMemoryDate(isoDate: string | null | undefined, locale?: string): string {
  if (!isoDate) return ''
  try {
    const date = new Date(isoDate)
    if (Number.isNaN(date.getTime())) return ''
    return date.toLocaleDateString(locale ?? undefined, {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    })
  } catch {
    return ''
  }
}
