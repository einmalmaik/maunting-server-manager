/**
 * Die Gerätewahl der Desktop-App — für Aufnahme und Wiedergabe der Sprachsitzung.
 *
 * Das Panel kennt keine Gerätewahl: dort bleibt alles auf dem Browser-Standard,
 * und dieses Modul ist ein stiller Durchlauf (`null`). Die Desktop-App setzt
 * beim Start die Namen aus ihrer Konfiguration (`registriereAudioGeraete` in
 * `desktop/DesktopRoot`-Umfeld) — dieselben Namen, die Rust über WASAPI sieht,
 * denn Chromium-Labels und WASAPI-Friendly-Names sind identisch.
 *
 * Aufgelöst wird per Label über `enumerateDevices`. Labels gibt der Browser
 * erst her, wenn die Mikrofonfreigabe einmal erteilt war — in der App ist sie
 * das immer (WebView2-Handler in Rust). Findet sich kein Gerät mit dem Namen
 * (abgezogen, umbenannt), fällt die Sitzung still auf den Standard zurück:
 * ein fehlendes USB-Mikrofon soll den Sprachmodus nicht lahmlegen.
 */

let eingabeName: string | null = null
let ausgabeName: string | null = null

/** Setzt die Wunschgeräte — `null` heißt: dem Systemstandard folgen. */
export function registriereAudioGeraete(eingabe: string | null, ausgabe: string | null): void {
  eingabeName = eingabe
  ausgabeName = ausgabe
}

async function deviceIdZuLabel(
  art: 'audioinput' | 'audiooutput',
  label: string,
): Promise<string | null> {
  try {
    const geraete = await navigator.mediaDevices.enumerateDevices()
    return geraete.find((g) => g.kind === art && g.label === label)?.deviceId ?? null
  } catch {
    return null
  }
}

/** Die deviceId des gewünschten Mikrofons — `null` heißt Standard. */
export async function eingabeGeraetId(): Promise<string | null> {
  if (!eingabeName) return null
  return deviceIdZuLabel('audioinput', eingabeName)
}

/** Die sinkId des gewünschten Lautsprechers — `null` heißt Standard. */
export async function ausgabeGeraetId(): Promise<string | null> {
  if (!ausgabeName) return null
  return deviceIdZuLabel('audiooutput', ausgabeName)
}
