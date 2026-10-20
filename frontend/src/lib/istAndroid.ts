/**
 * Läuft die App auf einem Android-Gerät? Eine Stelle für die Frage statt
 * Prüfungen des User-Agents über den Code verteilt. Der Browser nutzt sie
 * über `browser/services/plattform.ts`, das Smart System in Einstellungen und
 * Kopfzeile (Update-Knopf, Badge).
 */
export function istAndroid(): boolean {
  return typeof navigator !== 'undefined' && /\bAndroid\b/.test(navigator.userAgent)
}
