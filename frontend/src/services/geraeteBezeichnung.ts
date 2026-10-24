/**
 * Der Name, unter dem dieses Gerät in der Geräteliste erscheint.
 *
 * Ohne ihn stand das Web-Frontend dauerhaft als „Unbenanntes Gerät“ in der
 * Liste — neben dem Telefon, das seinen Namen aus der Kopplungseinladung
 * bekommt. Der Grund war kein Fehler im Server, sondern eine Auslassung im
 * Client: `geraetVeroeffentlichen()` wurde ohne Bezeichnung aufgerufen, und
 * `_auffrischen` überschreibt ein vorhandenes Label nur mit einem nicht-leeren
 * Wert. Ein leeres Feld bleibt also für immer leer.
 *
 * Der Name ist **Produkt und Plattform**, kein Satz: er steht in der Datenbank
 * und wird jedem angezeigt, der die Geräteliste sieht — auch in der englischen
 * Oberfläche. Deshalb hier keine Übersetzung, sondern Eigennamen.
 *
 * Was hier bewusst **nicht** passiert: einen bereits vergebenen Namen
 * überschreiben. Wer sein Gerät „Arbeitsrechner“ genannt hat, behält den
 * Namen; gefüllt wird nur, was leer ist (`geraetVeroeffentlichen`).
 */

import { geraeteArt } from '@/desktop/geraeteArt'

/** Der Server nimmt höchstens 64 Zeichen (`e2ee_device_service.MAX_BEZEICHNUNG`). */
const HOECHSTENS = 60

/**
 * Der Name, den ein Browser-Login ohne eigene Bezeichnung bekommt.
 *
 * Dieselbe Zeichenkette wie `e2ee_device_service.STANDARD_BEZEICHNUNG`: der
 * Server setzt sie für den Bestand ein, dieser Client ersetzt sie beim ersten
 * Start durch den genaueren Namen dieses Browsers.
 */
export const STANDARD_BEZEICHNUNG = 'Web-Frontend'

function laufendeApp(): string {
  const tauri =
    typeof window !== 'undefined' &&
    ('__TAURI_INTERNALS__' in window || '__TAURI__' in window)
  if (!tauri) return STANDARD_BEZEICHNUNG
  // Der Browser teilt sich mit MSS Kopplung und Transport, ist aber eine eigene
  // App — und stand bis 09/2026 als „desktop“ in derselben Liste.
  return geraeteArt() === 'browser' ? 'Pondys Browser' : 'MSS-App'
}

function browserName(ua: string): string {
  if (/\bEdg\//.test(ua)) return 'Edge'
  if (/\bOPR\/|\bOpera\b/.test(ua)) return 'Opera'
  if (/\bFirefox\/|\bFxiOS\//.test(ua)) return 'Firefox'
  if (/\bChrome\/|\bCriOS\//.test(ua)) return 'Chrome'
  if (/\bSafari\//.test(ua)) return 'Safari'
  return ''
}

function systemName(ua: string): string {
  if (/Android/i.test(ua)) return 'Android'
  if (/iPhone|iPad|iPod/i.test(ua)) return 'iOS'
  if (/Windows/i.test(ua)) return 'Windows'
  if (/Mac OS X|Macintosh/i.test(ua)) return 'macOS'
  if (/Linux|X11/i.test(ua)) return 'Linux'
  return ''
}

/**
 * Die Bezeichnung dieses Geräts, etwa `Web-Frontend · Chrome/Windows`,
 * `Pondys Browser · Windows` oder `MSS-App · Android`.
 */
export function geraeteBezeichnung(): string {
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent || '' : ''
  const app = laufendeApp()
  const system = systemName(ua)
  const browser = app === STANDARD_BEZEICHNUNG ? browserName(ua) : ''
  const plattform = [browser, system].filter(Boolean).join('/')
  return (plattform ? `${app} · ${plattform}` : app).slice(0, HOECHSTENS)
}
