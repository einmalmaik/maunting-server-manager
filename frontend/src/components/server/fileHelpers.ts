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
export type FileLanguage =
  | 'json' | 'yaml' | 'xml' | 'markdown' | 'ini' | 'properties'
  | 'javascript' | 'typescript' | 'python' | 'shell' | 'sql' | 'css'
  | 'cpp' | 'java' | 'csharp' | 'go' | 'rust' | 'lua' | 'toml'
  | 'dockerfile' | 'plain'

export function detectLanguage(filename: string): FileLanguage {
  const lower = filename.toLowerCase()
  const base = lower.split('/').pop() || lower
  if (lower.endsWith('.json')) return 'json'
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return 'yaml'
  if (lower.endsWith('.xml') || lower.endsWith('.html') || lower.endsWith('.htm')) return 'xml'
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) return 'markdown'
  if (lower.endsWith('.ini') || lower.endsWith('.cfg') || lower.endsWith('.conf')) return 'ini'
  if (lower.endsWith('.properties')) return 'properties'
  if (lower.endsWith('.js') || lower.endsWith('.jsx') || lower.endsWith('.mjs') || lower.endsWith('.cjs')) return 'javascript'
  if (lower.endsWith('.ts') || lower.endsWith('.tsx')) return 'typescript'
  if (lower.endsWith('.py')) return 'python'
  if (lower.endsWith('.sh') || lower.endsWith('.bash') || lower.endsWith('.zsh') || lower.endsWith('.ps1')) return 'shell'
  if (lower.endsWith('.sql')) return 'sql'
  if (lower.endsWith('.css') || lower.endsWith('.scss') || lower.endsWith('.less')) return 'css'
  if (/\.(c|cc|cpp|cxx|h|hh|hpp)$/.test(lower)) return 'cpp'
  if (lower.endsWith('.java')) return 'java'
  if (lower.endsWith('.cs')) return 'csharp'
  if (lower.endsWith('.go')) return 'go'
  if (lower.endsWith('.rs')) return 'rust'
  if (lower.endsWith('.lua')) return 'lua'
  if (lower.endsWith('.toml')) return 'toml'
  if (base === 'dockerfile' || base.startsWith('dockerfile.')) return 'dockerfile'
  return 'plain'
}

export function fileName(path: string): string {
  return path.split('/').pop() || path
}

export function detectLineEnding(content: string): '\n' | '\r\n' {
  return content.includes('\r\n') ? '\r\n' : '\n'
}

/** CodeMirror normalizes its document to LF; serialize back to the file's original EOL. */
export function serializeLineEndings(content: string, lineEnding: '\n' | '\r\n'): string {
  const normalized = content.replace(/\r\n/g, '\n')
  return lineEnding === '\r\n' ? normalized.replace(/\n/g, '\r\n') : normalized
}

/** A successful response only covers the text that was actually submitted.
 * Newer keystrokes must remain dirty so a following autosave can persist them.
 */
export function reconcileSavedContent(
  currentContent: string,
  submittedContent: string,
): { savedContent: string; saveState: 'clean' | 'dirty' } {
  return {
    savedContent: submittedContent,
    saveState: currentContent === submittedContent ? 'clean' : 'dirty',
  }
}

export function detectIndentation(content: string): string {
  const indented = content.split(/\r?\n/).find((line) => /^(?:\t+| +)\S/.test(line))
  if (!indented) return 'Einzug: –'
  const prefix = indented.match(/^(\t+| +)/)?.[0] ?? ''
  return prefix.startsWith('\t') ? 'Tabs' : `Leerzeichen: ${prefix.length}`
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

/** Stabiler Schluessel fuer laufende Uploads (verhindert Doppel-POST bei Drop-Bubbling). */
export function uploadDestinationKey(destinationPath: string, fileName: string): string {
  const dir = destinationPath.replace(/^\/+|\/+$/g, '')
  const name = fileName.replace(/^\/+|\/+$/g, '')
  return dir ? `${dir}/${name}` : name
}
