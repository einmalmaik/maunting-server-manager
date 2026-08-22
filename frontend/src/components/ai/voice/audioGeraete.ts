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

/**
 * Die Mikrofon-Verarbeitung der Sprachsitzung — Chromiums eingebaute, lokale
 * Kette (WebRTC-Audioverarbeitung): Echounterdrückung, Rauschunterdrückung,
 * automatische Pegelanpassung, dazu eine Software-Verstärkung. Im Panel
 * bleiben die Vorgaben (alles an, Verstärkung neutral); die Desktop-App
 * registriert die Wahl des Benutzers aus ihrer Konfiguration.
 */
export interface AudioVerarbeitung {
  echo: boolean
  rauschen: boolean
  autogain: boolean
  /** Software-Eingangsverstärkung, 1 = neutral. Geklemmt auf 0,25–4. */
  verstaerkung: number
}

const VERARBEITUNG_VORGABE: AudioVerarbeitung = {
  echo: true,
  rauschen: true,
  autogain: true,
  verstaerkung: 1,
}

let verarbeitung: AudioVerarbeitung = { ...VERARBEITUNG_VORGABE }

export function registriereAudioVerarbeitung(neu: Partial<AudioVerarbeitung>): void {
  // Feld für Feld statt Spread: ein explizit `undefined` übergebenes Feld
  // (Konfiguration von vor diesen Feldern) fällt auf die Vorgabe, statt sie
  // zu überschreiben.
  verarbeitung = {
    echo: neu.echo ?? VERARBEITUNG_VORGABE.echo,
    rauschen: neu.rauschen ?? VERARBEITUNG_VORGABE.rauschen,
    autogain: neu.autogain ?? VERARBEITUNG_VORGABE.autogain,
    verstaerkung: neu.verstaerkung ?? VERARBEITUNG_VORGABE.verstaerkung,
  }
}

/** Die aktuelle Verarbeitung, Verstärkung bereits geklemmt. */
export function aktuelleVerarbeitung(): AudioVerarbeitung {
  const roh = verarbeitung.verstaerkung
  const wert = Number.isFinite(roh) ? Math.min(4, Math.max(0.25, roh)) : 1
  return { ...verarbeitung, verstaerkung: wert }
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
