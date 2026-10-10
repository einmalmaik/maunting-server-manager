/**
 * Der Hinweis, wenn eine Seite den ganzen Bildschirm nimmt (Windows). Im
 * Vollbild kann eine Seite die Leisten des Browsers samt Adresse nachbauen.
 * Wer den Bildschirm hat und wie man herauskommt, steht deshalb jedes Mal oben
 * in der Mitte, gezeichnet von Windows über der Seite (`kurzinfo.rs`). Escape
 * beendet das Vollbild immer, das hält Rust fest (`webview2.rs`). Unter Android
 * zeigt `TabsPlugin.vollbildZeigen` den Hinweis selbst.
 */
import i18n from '@/i18n'

import { blaseSenden } from '../leiste/leistenname'

export const HINWEIS_MS = 4000
/** Ein längerer Host wird vorne gekürzt: das Ende nennt, wem die Seite gehört. */
const HOST_MAX = 48

let verbergen: ReturnType<typeof setTimeout> | undefined

export function hostFuerHinweis(url: string | undefined): string {
  let host = ''
  try {
    host = new URL(url ?? '').host
  } catch {
    host = ''
  }
  return host.length > HOST_MAX ? `…${host.slice(-HOST_MAX)}` : host
}

export function vollbildHinweis(an: boolean, url: string | undefined): void {
  clearTimeout(verbergen)
  if (!an) return blaseSenden(null)
  const host = hostFuerHinweis(url)
  const text = host ? i18n.t('browser.vollbild.hinweis', { host }) : i18n.t('browser.vollbild.hinweisOhneHost')
  blaseSenden({ text, x: 0, y: 0, richtung: 'oben' })
  verbergen = setTimeout(() => blaseSenden(null), HINWEIS_MS)
}
