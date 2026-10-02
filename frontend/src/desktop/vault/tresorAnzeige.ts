/**
 * Was die App mit einer entschlüsselten Datei anfangen kann: selbst zeigen
 * oder auf dem Gerät speichern. Gemeinsam für Dateien und Fotos.
 */
import { adresseHerunterladen } from '@/lib/herunterladen'
import i18n from '@/i18n'
import { inDerAppSpeichern } from '@/lib/geraetSpeichern'
import { toast } from '@/stores/toastStore'
import { zipGrenzenPruefen, zipStrom } from '@/lib/zipSchreiben'
import { inDerApp } from '@/services/passkeyService'
import { useVaultStore, type VaultItem } from './vaultStore'
import type { BlobKopf } from './tresorDatei'
import { ansichtOeffnen, ansichtSchliessen, klartextTeile } from './tresorDateien'

/** Wie der Tresor eine Datei zeigt. `null`: nur eine Infokarte zum Speichern. */
export type VorschauArt =
  | 'bild'
  | 'video'
  | 'audio'
  | 'text'
  | 'markdown'
  | 'tabelle'
  | 'pdf'
  | 'archiv'
  | 'schrift'
  | 'office'

const ENDUNGEN: Record<VorschauArt, string[]> = {
  bild: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'bmp', 'ico', 'svg', 'heic', 'heif', 'tif', 'tiff'],
  video: ['mp4', 'm4v', 'webm', 'mov', 'mkv', 'ogv', 'avi'],
  audio: ['mp3', 'm4a', 'aac', 'wav', 'ogg', 'oga', 'opus', 'flac', 'weba'],
  markdown: ['md', 'markdown'],
  tabelle: ['csv', 'tsv'],
  pdf: ['pdf'],
  archiv: ['zip', 'jar', 'apk', 'tar', 'tgz', 'gz'],
  schrift: ['ttf', 'otf', 'woff', 'woff2'],
  office: ['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp', 'rtf', 'pages', 'numbers', 'key'],
  // Was ein Mensch als Text liest und schreibt. HTML und SVG-Quelltext stehen
  // nicht hier: HTML wird nie gerendert, nur als Text gezeigt (siehe unten).
  text: [
    'txt', 'text', 'log', 'json', 'jsonc', 'json5', 'yaml', 'yml', 'xml', 'html', 'htm', 'ini', 'cfg', 'conf', 'properties',
    'toml', 'env', 'js', 'jsx', 'mjs', 'cjs', 'ts', 'tsx', 'py', 'sh', 'bash', 'zsh', 'ps1', 'bat', 'cmd', 'sql', 'css',
    'scss', 'less', 'c', 'cc', 'cpp', 'cxx', 'h', 'hh', 'hpp', 'java', 'kt', 'cs', 'go', 'rs', 'lua', 'rb', 'php', 'swift',
    'dart', 'vue', 'svelte', 'gradle', 'tf', 'nix', 'srt', 'vtt', 'gitignore', 'dockerignore', 'editorconfig', 'pem', 'pub',
    'asc', 'crt', 'csr', 'key', 'mcfunction', 'mcmeta', 'lang', 'diff', 'patch', 'tex', 'bib', 'rst', 'adoc', 'org', 'ics', 'vcf',
  ],
}

/** Ohne Endung, aber Text: Dockerfile, Makefile & Co. */
const TEXT_NAMEN = ['dockerfile', 'makefile', 'license', 'readme', 'changelog', 'procfile', 'jenkinsfile', 'vagrantfile']

function endung(name: string): string {
  const basis = name.toLowerCase().split('/').pop() ?? ''
  const punkt = basis.lastIndexOf('.')
  // ".env" und ".gitignore" sind Endung und Name zugleich.
  return punkt < 0 ? '' : basis.slice(punkt + 1)
}

function nachEndung(name: string): VorschauArt | null {
  const e = endung(name)
  // Die erste Art gewinnt: "key" steht bei office und bei text und ist hier
  // eine Präsentation.
  for (const art of Object.keys(ENDUNGEN) as VorschauArt[]) {
    if (e && ENDUNGEN[art].includes(e)) return art
  }
  const basis = name.toLowerCase().split('/').pop() ?? ''
  if (TEXT_NAMEN.some((n) => basis === n || basis.startsWith(`${n}.`))) return 'text'
  return null
}

/**
 * Wie eine Datei gezeigt wird. Zuerst zählt der MIME-Typ, dann die Endung:
 * unter Windows ist der Typ vieler Textdateien leer (.md, .yml, .log).
 * Endet der Name auf eine Endung, die eindeutiger ist als ein allgemeiner Typ
 * (`application/octet-stream`), gewinnt die Endung.
 */
export function vorschauArt(typ: string, name: string): VorschauArt | null {
  const t = typ.toLowerCase()
  const ausName = nachEndung(name)
  if (t === 'text/markdown' || t === 'text/x-markdown') return 'markdown'
  if (t === 'text/csv' || t === 'text/tab-separated-values') return 'tabelle'
  if (t === 'application/pdf') return 'pdf'
  if (t.startsWith('image/')) return 'bild'
  if (t.startsWith('video/')) return 'video'
  if (t.startsWith('audio/')) return 'audio'
  if (t.startsWith('font/')) return 'schrift'
  if (['application/zip', 'application/x-zip-compressed', 'application/x-tar', 'application/gzip', 'application/x-gzip', 'application/java-archive'].includes(t)) return 'archiv'
  if (t.startsWith('application/vnd.openxmlformats') || t.startsWith('application/vnd.oasis.opendocument') || t === 'application/msword' || t.startsWith('application/vnd.ms-')) return 'office'
  if (t.startsWith('text/') || t === 'application/json' || t === 'application/xml' || t === 'application/javascript') return ausName === 'markdown' || ausName === 'tabelle' ? ausName : 'text'
  return ausName
}

/** Bezeichnung für die Infokarte, als Schlüssel unter `mss.vault.dateien.art.*`. */
export function artBezeichnung(typ: string, name: string): string {
  const e = endung(name)
  if (['doc', 'docx', 'odt', 'rtf', 'pages'].includes(e)) return 'dokument'
  if (['xls', 'xlsx', 'ods', 'numbers'].includes(e)) return 'tabellenblatt'
  if (['ppt', 'pptx', 'odp', 'key'].includes(e) && vorschauArt(typ, name) === 'office') return 'praesentation'
  if (['exe', 'msi', 'apk', 'dmg', 'deb', 'rpm', 'appimage', 'bin'].includes(e)) return 'programm'
  if (['7z', 'rar', 'xz', 'bz2', 'zst', 'iso', 'img'].includes(e)) return 'archiv'
  return vorschauArt(typ, name) ?? 'datei'
}

/**
 * Ob Bytes Text sind: kein NUL-Byte und gültiges UTF-8. Ein Mehrbytezeichen,
 * das am Ende abgeschnitten ist, zählt nicht als Fehler (`stream`).
 */
export function istText(bytes: Uint8Array): boolean {
  if (bytes.includes(0)) return false
  try {
    new TextDecoder('utf-8', { fatal: true }).decode(bytes, { stream: true })
    return true
  } catch {
    return false
  }
}

/**
 * Speichert eine Tresor-Datei auf dem Gerät. In der App fragt Rust nach dem
 * Ziel und schreibt Chunk für Chunk (auch nach `content://` auf Android); im
 * JavaScript-Speicher liegt immer nur ein Chunk Klartext, dazu seine
 * Base64-Fassung für den Weg nach Rust, bis sie eingesammelt wird. `false`
 * heißt: der Mensch hat den Dialog abgebrochen.
 */
export async function aufGeraetSpeichern(
  kopf: BlobKopf,
  eintragId: string,
  userKey: CryptoKey,
  name: string,
  typ: string,
): Promise<boolean> {
  if (!inDerApp()) {
    const teile: Blob[] = []
    for await (const klartext of klartextTeile(kopf, eintragId, userKey)) teile.push(new Blob([klartext as BlobPart]))
    if (useVaultStore.getState().userKey !== userKey) return false
    herunterladen(new Blob(teile, { type: typ }), name)
    return gespeichert(true)
  }
  return gespeichert(await inDerAppSpeichern(name, bisGesperrt(klartextTeile(kopf, eintragId, userKey), userKey)))
}

/**
 * Die Kopie auf dem Gerät ist unverschlüsselt. Bis 02.10.2026 sagte das
 * niemand, und der Tresor wirkte wie ein Ort, aus dem nichts im Klartext geht.
 */
function gespeichert(ok: boolean): boolean {
  if (ok) toast.success(i18n.t('mss.vault.dateien.gespeichertUnverschluesselt'))
  return ok
}

/** „Auf dem Gerät speichern“ für eine Datei, mit Fehlermeldung. */
export async function dateiAufsGeraet(item: VaultItem, userKey: CryptoKey | null): Promise<void> {
  if (!item.datei || !userKey) return
  try {
    await aufGeraetSpeichern(item.datei.original, item.id, userKey, item.service, item.datei.typ)
  } catch {
    toast.error(i18n.t('mss.vault.dateien.speichernFehler'))
  }
}

/**
 * Außerhalb der App (nur im Browser): als Download anbieten. Die Adresse kommt
 * aus `ansichtOeffnen`, damit das Sperren auch diesen Klartext widerruft.
 */
function herunterladen(blob: Blob, name: string): void {
  const url = ansichtOeffnen(blob)
  adresseHerunterladen(url, name)
  setTimeout(() => ansichtSchliessen(url), 60_000)
}

/** Gesperrt: nichts mehr herausgeben, die halbe Datei fällt weg. */
async function* bisGesperrt(teile: AsyncIterable<Uint8Array>, userKey: CryptoKey): AsyncGenerator<Uint8Array> {
  for await (const teil of teile) {
    if (useVaultStore.getState().userKey !== userKey) throw new Error('gesperrt')
    yield teil
  }
}

/**
 * Speichert mehrere Tresor-Dateien als ein Zip auf dem Gerät. Das Zip wird
 * als Strom geschrieben, Datei für Datei und Chunk für Chunk: im Speicher liegt
 * nie mehr als ein Chunk Klartext. Passt es nicht in ein Zip ohne Zip64,
 * wirft `zipGrenzenPruefen` vor dem Speicherdialog. `false`: abgebrochen.
 */
export async function mehrereAufGeraetSpeichern(
  dateien: { kopf: BlobKopf; eintragId: string; pfad: string; geaendert: number }[],
  userKey: CryptoKey,
  name: string,
): Promise<boolean> {
  const eintraege = dateien.map((d) => ({
    pfad: d.pfad,
    groesse: d.kopf.echt,
    datum: new Date(d.geaendert),
    teile: () => klartextTeile(d.kopf, d.eintragId, userKey),
  }))
  zipGrenzenPruefen(eintraege)
  if (!inDerApp()) {
    const teile: Blob[] = []
    for await (const teil of bisGesperrt(zipStrom(eintraege), userKey)) teile.push(new Blob([teil as BlobPart]))
    herunterladen(new Blob(teile, { type: 'application/zip' }), name)
    return gespeichert(true)
  }
  return gespeichert(await inDerAppSpeichern(name, bisGesperrt(zipStrom(eintraege), userKey)))
}
