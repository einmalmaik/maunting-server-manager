/**
 * Das Schlüsselfach des Betriebssystems, soweit es eines gibt.
 *
 * Drei Bauten, drei Antworten:
 *
 * - **Windows-Desktop**: Windows Hello fragt, der Credential Store verwahrt.
 * - **Android**: der Hardware-Keystore verwahrt und fragt in einem Zug. Der
 *   Schlüssel liegt dort so, dass ihn kein Prozess auslesen kann, und die
 *   Hardware gibt ihn erst nach einem erfolgreichen Fingerabdruck frei. Bis
 *   09/2026 fehlte diese Anbindung; das Plugin konnte nur fragen, nicht
 *   verwahren.
 * - **Web**: nichts. Ein Browser hat keinen hardwaregeschützten Platz für ein
 *   Geheimnis. Das ist keine Nachlässigkeit, sondern die Entscheidung
 *   SEC-CRIT-01: ein im localStorage verwahrtes Geheimnis sieht aus wie Schutz
 *   und ist keiner, weil es genau dort liegt, wo auch der Angreifer sucht.
 *
 * Diese Datei ist die einzige Stelle, an der der Messenger in die
 * Desktop-Brücke greift. Der Rest fragt sie und bekommt überall eine ehrliche
 * Antwort, statt drei Plattformfälle durch die Oberfläche zu ziehen.
 */

import { base64ToBytes, bytesToBase64 } from '@msdis/shield/core'
import { randomBytes } from '@msdis/shield/random'

import {
  FACH_MESSENGER,
  FACH_MESSENGER_GERAET,
  biometrieEntsperren,
  biometrieLoeschen,
  biometrieSpeichern,
  biometrieSpeicherFragtSelbst,
  biometrieSpeicherVerfuegbar,
  messengerGeraetegeheimnis,
  pruefeBiometrieVerfuegbar,
  verifiziereBiometrie,
} from '@/desktop/tauri'

/** 32 Bytes. Dieselbe Länge wie jeder andere Schlüssel im Messenger. */
const GERAETEGEHEIMNIS_BYTES = 32

/**
 * Kann hier ein Geheimnis verwahrt werden?
 *
 * Die Frage, die der Tresor bis 09/2026 nicht gestellt hat: er prüfte nur, ob
 * sich ein Fingerabdruck abfragen lässt, bot auf Android daraufhin den
 * Schnelleinstieg an — und scheiterte beim Einrichten am fehlenden Speicher.
 */
export async function schluesselfachVerfuegbar(): Promise<boolean> {
  return await biometrieSpeicherVerfuegbar()
}

/**
 * Lässt sich der Mensch hier biometrisch bestätigen **und** ein Geheimnis
 * verwahren? Nur dann darf ein Schnelleinstieg angeboten werden.
 */
export async function biometrieMoeglich(): Promise<boolean> {
  if (!(await schluesselfachVerfuegbar())) return false
  return await pruefeBiometrieVerfuegbar()
}

/**
 * Das Gerätegeheimnis, das zusätzlich zum PIN in die Schlüsselableitung
 * eingeht. `null`, wenn diese Plattform keines verwahren kann.
 *
 * `erzeugen` legt eines an, falls noch keines da ist. Beim Entsperren steht das
 * auf `false`: ein frisch erzeugtes Geheimnis wäre dort das falsche, und aus
 * dem falschen Geheimnis käme ein falscher Schlüssel. Fehlt es beim Entsperren,
 * ist das eine Aussage — der Schlüsselspeicher ist weg — und keine Einladung,
 * ein neues zu erfinden.
 */
export async function geraeteGeheimnis(erzeugen = false): Promise<Uint8Array | null> {
  const vorhanden = await messengerGeraetegeheimnis()
  if (vorhanden) {
    try {
      return base64ToBytes(vorhanden)
    } catch {
      return null
    }
  }
  if (!erzeugen) return null
  if (!(await schluesselfachVerfuegbar())) return null

  // Der Zufall kommt aus `crypto.getRandomValues`, derselben Quelle wie jeder
  // Nachrichtenschlüssel. Verwahrt wird er drüben im Credential Store.
  const frisch = randomBytes(GERAETEGEHEIMNIS_BYTES)
  try {
    await biometrieSpeichern(bytesToBase64(frisch), FACH_MESSENGER_GERAET)
  } catch {
    return null
  }
  return frisch
}

/** Nimmt das Gerätegeheimnis zurück. Nur beim Abschalten des PIN. */
export async function vergissGeraeteGeheimnis(): Promise<void> {
  await biometrieLoeschen(FACH_MESSENGER_GERAET)
}

/**
 * Legt den PIN ins biometrische Fach des Messengers.
 *
 * Genau eine Bestätigung, egal auf welcher Plattform. Wer den Schnelleinstieg
 * einrichtet, soll einmal gezeigt haben, dass er der ist, für den ihn das Gerät
 * später hält — sonst bände jemand an einem unbeaufsichtigten Rechner seinen
 * eigenen Finger an ein fremdes Geheimnis.
 *
 * Wer fragt, hängt vom Schlüsselspeicher ab: auf Android verlangt der Keystore
 * die Bestätigung schon zum Verschlüsseln, auf Windows nimmt der Credential
 * Store das Geheimnis wortlos entgegen. Deshalb die Abfrage davor nur dort, wo
 * sie sonst fehlte.
 */
export async function verwahrePin(pin: string): Promise<void> {
  if (!(await biometrieSpeicherFragtSelbst())) {
    const bestaetigt = await verifiziereBiometrie('Messenger-PIN hinterlegen')
    if (!bestaetigt) throw new Error('Biometrische Bestätigung fehlgeschlagen.')
  }
  await biometrieSpeichern(pin, FACH_MESSENGER)
}

/**
 * Fragt nach der Bestätigung und gibt den PIN erst danach heraus. `null`, wenn
 * keiner hinterlegt ist oder die Bestätigung scheitert.
 */
export async function rufePinAb(): Promise<string | null> {
  try {
    const pin = await biometrieEntsperren('Messenger entsperren', FACH_MESSENGER)
    return pin || null
  } catch {
    return null
  }
}

/** Nimmt den PIN aus dem biometrischen Fach. */
export async function vergissPin(): Promise<void> {
  await biometrieLoeschen(FACH_MESSENGER)
}
