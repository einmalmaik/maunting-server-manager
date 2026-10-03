/**
 * `crypto.randomUUID` gibt es erst ab Chrome 92. Android-WebViews, die nicht
 * aktualisiert werden (Geräte ohne Play Store, alte Firmware), haben es nicht:
 * unter Android 12 mit WebView 91 scheiterte das Einschalten der
 * Kamera-Sicherung an genau diesem Aufruf (03.10.2026).
 *
 * Fehlt die Funktion, kommt eine UUID v4 aus `crypto.getRandomValues` dazu,
 * demselben sicheren Zufall. Eine vorhandene bleibt unangetastet. Läuft
 * einmal beim Start, vor allem anderen.
 */
export function uuidV4(bytes: Uint8Array): string {
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function randomUuidNachruesten(): void {
  if (typeof crypto === 'undefined' || typeof crypto.randomUUID === 'function') return
  Object.defineProperty(crypto, 'randomUUID', {
    value: () => uuidV4(crypto.getRandomValues(new Uint8Array(16))),
    configurable: true,
  })
}
