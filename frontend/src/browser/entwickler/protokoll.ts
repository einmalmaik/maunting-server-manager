/**
 * Das DevTools-Protokoll eines Tabs (`tab_protokoll`, `desktop/entwickler.rs`).
 * Welche Methoden durchgehen, entscheidet Rust; hier stehen nur die Typen, die
 * die Werkzeuge lesen, und ein Aufruf mit Ergebnis.
 */
import { nativ } from '../services/nativ'

export interface ObjektVorschau {
  type: string
  subtype?: string
  description?: string
  overflow: boolean
  properties: { name: string; type: string; subtype?: string; value?: string; valuePreview?: ObjektVorschau }[]
  entries?: { key?: ObjektVorschau; value: ObjektVorschau }[]
}

/** `Runtime.RemoteObject`. */
export interface Objekt {
  type: string
  subtype?: string
  className?: string
  value?: unknown
  unserializableValue?: string
  description?: string
  objectId?: string
  preview?: ObjektVorschau
}

export interface Eigenschaft {
  name: string
  value?: Objekt
  get?: Objekt
  set?: Objekt
  enumerable?: boolean
  isOwn?: boolean
}

export interface Aufruf {
  functionName: string
  url: string
  lineNumber: number
  columnNumber: number
  scriptId: string
}

export interface Stapel {
  description?: string
  callFrames: Aufruf[]
  parent?: Stapel
}

export interface Ort {
  scriptId: string
  lineNumber: number
  columnNumber?: number
}

/** Ruft eine Methode im Tab; außerhalb von Tauri kommt ein leeres Objekt zurück. */
export async function rufen<T = Record<string, unknown>>(tab: string, methode: string, parameter: Record<string, unknown> = {}): Promise<T> {
  const antwort = await nativ.tabProtokoll(tab, methode, parameter)
  return (antwort ?? {}) as T
}

/** Wie Chrome einen Knoten in der Seite hervorhebt. */
export const HERVORHEBUNG = {
  showInfo: true,
  showStyles: true,
  showAccessibilityInfo: true,
  contentColor: { r: 111, g: 168, b: 220, a: 0.66 },
  paddingColor: { r: 147, g: 196, b: 125, a: 0.55 },
  borderColor: { r: 255, g: 229, b: 153, a: 0.66 },
  marginColor: { r: 246, g: 178, b: 107, a: 0.66 },
}

export function hervorheben(tab: string, ziel: { nodeId?: number; backendNodeId?: number; objectId?: string } | null) {
  if (!ziel) return void rufen(tab, 'Overlay.hideHighlight').catch(() => null)
  void rufen(tab, 'Overlay.highlightNode', { highlightConfig: HERVORHEBUNG, ...ziel }).catch(() => null)
}

/** Kurzer Ort im Quelltext: Dateiname und Zeile ab 1. */
export function ortText(url: string, zeile: number): string {
  let name = url
  try {
    const u = new URL(url)
    // Das Dokument selbst heißt wie in jedem Browser „(index)“.
    name = u.pathname.split('/').pop() || (u.protocol.startsWith('http') ? '(index)' : u.host)
  } catch {
    name = url.split('/').pop() || url
  }
  return `${name}:${zeile + 1}`
}
