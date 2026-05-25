// Reine Hilfsfunktionen fuer den File-Manager.
// Bewusst frei von React/DOM, damit sie via Vitest direkt getestet
// werden koennen.

/** Verbindet ein Verzeichnis mit einem Eintrag, ohne fuehrenden Slash und
 *  ohne `./`-Schnipsel. Auch wenn `parent` leer ist liefern wir den
 *  Eintragsnamen unveraendert zurueck.
 */
export function joinPath(parent: string, name: string): string {
  const cleanParent = parent.replace(/^\/+|\/+$/g, '')
  const cleanName = name.replace(/^\/+|\/+$/g, '')
  if (!cleanParent) return cleanName
  if (!cleanName) return cleanParent
  return `${cleanParent}/${cleanName}`
}

/** Liefert das uebergeordnete Verzeichnis. Wirft nicht, sondern liefert "". */
export function parentPath(path: string): string {
  const cleaned = path.replace(/^\/+|\/+$/g, '')
  const idx = cleaned.lastIndexOf('/')
  return idx === -1 ? '' : cleaned.slice(0, idx)
}

/** Splittet einen Pfad in Breadcrumb-Segmente. */
export function pathSegments(path: string): string[] {
  return path.replace(/^\/+|\/+$/g, '').split('/').filter(Boolean)
}

/** Sortiert Verzeichnisse vor Dateien, jeweils alphabetisch (i18n-aware). */
export function sortEntries<T extends { name: string; is_dir: boolean }>(entries: T[]): T[] {
  return [...entries].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
    return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })
  })
}

/** Mappt eine Datei-Endung auf die CodeMirror-Lang-Extension-Kennung.
 *  Implementierung haelt sich an die im Plugin-Set installierten Sprachen.
 */
export function detectLanguage(filename: string): 'json' | 'yaml' | 'xml' | 'markdown' | 'ini' | 'properties' | 'plain' {
  const lower = filename.toLowerCase()
  if (lower.endsWith('.json')) return 'json'
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return 'yaml'
  if (lower.endsWith('.xml')) return 'xml'
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) return 'markdown'
  if (lower.endsWith('.ini') || lower.endsWith('.cfg') || lower.endsWith('.conf')) return 'ini'
  if (lower.endsWith('.properties')) return 'properties'
  return 'plain'
}

/** Praezise Anzeige fuer Bytes — KISS ohne Locale. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

/** Pruefen, ob ein neuer Pfad innerhalb eines bestehenden Pfads liegt
 *  (z.B. Move "outer" -> "outer/inner" blockieren). Naiv reicht hier,
 *  weil Backend den definitiven Check macht — KISS.
 */
export function isWithin(base: string, candidate: string): boolean {
  const b = base.replace(/^\/+|\/+$/g, '')
  const c = candidate.replace(/^\/+|\/+$/g, '')
  if (!b) return false
  if (b === c) return true
  return c.startsWith(`${b}/`)
}
