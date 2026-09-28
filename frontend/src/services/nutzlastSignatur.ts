/**
 * Wer hat das geschrieben — beantwortet an der Nutzlast, nicht am Umschlag.
 *
 * **Das Loch.** Eine Nutzlast nennt ihren Urheber selbst: `sender_id` bei einer
 * Nachricht, `actor_id` bei einem Steuerpaket. Beide Felder sind frei
 * wählbar, und an zwei Stellen glaubte der Messenger ihnen:
 *
 * - In einer Gruppe verschlüsselt ein *geteilter* Schlüssel. Er beweist
 *   „von jemandem aus dieser Gruppe", nie „von Anna". Jedes Mitglied konnte
 *   sich damit als jedes andere ausgeben.
 * - Im Direktchat laufen Steuerpakete bewusst über den Hybridumschlag und
 *   nicht durch den Ratchet (Begründung in `useKonversation.baueSteuerversand`).
 *   Ein Hybridumschlag hat keinen Absenderkopf — `edit_message` mit fremdem
 *   `actor_id` schrieb deshalb die Nachricht der Gegenseite um.
 *
 * Beide Fälle sind dasselbe Loch, und es gehört an eine Stelle geschlossen.
 * Deshalb sitzt die Beglaubigung hier an der Nutzlast und nicht in einem
 * Umschlagformat: eine Zusage, zwei verschiedene Verfahren, wären zwei
 * Gelegenheiten, sie unterschiedlich falsch zu machen.
 *
 * **Was signiert wird.** Alle Felder der Nutzlast ausser `sig`, nach
 * Feldnamen sortiert, plus die Mailbox-Kennung. Sortiert, damit beide Seiten
 * dieselbe Zeichenkette bilden, ohne sich auf die Reihenfolge in einem
 * JSON-Objekt verlassen zu müssen. Die Mailbox gehört hinein, sonst liesse
 * sich dieselbe signierte Nutzlast in ein anderes Gespräch umhängen, in dem
 * derselbe Mensch auch Mitglied ist. Eine Wiederholung in *derselben* Mailbox
 * fängt das Relais ab — derselbe Chiffretext zweimal ist dort eine 409.
 *
 * **Die Downgrade-Schranke steht nicht hier.** Dieses Modul sagt nur, wer
 * unterschrieben hat. Ob eine *unsignierte* Nutzlast noch durchgehen darf,
 * entscheidet der Lesepfad — er allein weiss, welches Feld den Urheber nennt.
 * Die Regel dort lautet: wer beglaubigen kann, muss es auch
 * (`kontoNutztSignaturen`). Ohne sie nähme ein Fälscher einfach die Signatur
 * weg und stünde wieder am Anfang.
 */

import { pruefe, signiere } from './absenderSignatur'
import { eigenesGeraet, signaturSchluesselVon } from './e2eeGeraet'

/** Das Feld mit dem Beleg. Wird selbst nicht mitsigniert. */
const FELD_SIGNATUR = 'sig'
/** Wer unterschrieben hat. Mitsigniert — sonst liesse es sich austauschen. */
const FELD_KONTO = 'von_konto'
const FELD_GERAET = 'von_geraet'

export type Nutzlastpruefung =
  /** Beleg vorhanden und gültig. `vonKonto` ist die geprüfte Kennung. */
  | { art: 'geprueft'; vonKonto: number }
  /** Kein Beleg. Ein Gerät, das die Signatur noch nicht kennt. */
  | { art: 'unsigniert' }
  /** Beleg vorhanden und falsch. Die Nutzlast gehört verworfen. */
  | { art: 'gefaelscht'; behauptet: number }

/**
 * Die Zeichenkette, über die unterschrieben wird.
 *
 * `undefined` fällt raus, weil `JSON.stringify` es beim Verschicken ebenfalls
 * weglässt: stünde es hier drin, bildete der Absender eine Zeichenkette, die
 * der Empfänger nie wieder erzeugen könnte, und jede solche Nachricht käme als
 * Fälschung an.
 */
function signaturDaten(mailboxId: string, roh: Record<string, unknown>): string {
  const felder = Object.keys(roh)
    .filter((name) => name !== FELD_SIGNATUR && roh[name] !== undefined)
    .sort()
    .map((name) => `${name}=${JSON.stringify(roh[name])}`)
  return `msm:nutzlast:v1:${mailboxId}:${felder.join('&')}`
}

/**
 * Hängt Urheber und Beleg an eine Nutzlast.
 *
 * Wirft nicht: ein Gerät ohne Signaturpaar — etwa weil der Messenger gerade
 * verschlossen ist — soll weiter senden können. Es sendet dann unsigniert,
 * und der Empfänger entscheidet nach seiner Schranke. Eine Nachricht am
 * Signaturschlüssel scheitern zu lassen, wäre der teuerste denkbare Preis für
 * eine Prüfung, die es vorher gar nicht gab.
 */
export async function signiereNutzlast(
  mailboxId: string,
  eigeneId: number,
  roh: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  try {
    const meins = await eigenesGeraet()
    const mitAbsender = { ...roh, [FELD_KONTO]: eigeneId, [FELD_GERAET]: meins.kennung }
    return {
      ...mitAbsender,
      [FELD_SIGNATUR]: await signiere(
        signaturDaten(mailboxId, mitAbsender),
        meins.signaturPaar.privateKeyJwk,
      ),
    }
  } catch {
    return roh
  }
}

/**
 * Prüft den Beleg einer empfangenen Nutzlast.
 *
 * Ein Beleg, dessen Gerät im Verzeichnis keinen Signaturschlüssel führt, gilt
 * als falsch und nicht als fehlend: die Nutzlast nennt ein Gerät, das es unter
 * diesem Konto nicht gibt. Das ist keine fehlende Auskunft, sondern eine
 * widerlegte Behauptung.
 */
export async function pruefeNutzlast(
  mailboxId: string,
  roh: Record<string, unknown>,
): Promise<Nutzlastpruefung> {
  const beleg = roh[FELD_SIGNATUR]
  const konto = Number(roh[FELD_KONTO])
  const geraet = roh[FELD_GERAET]

  if (typeof beleg !== 'string' || !beleg) return { art: 'unsigniert' }
  if (!Number.isInteger(konto) || konto <= 0 || typeof geraet !== 'string' || !geraet) {
    return { art: 'gefaelscht', behauptet: Number.isInteger(konto) ? konto : 0 }
  }

  const oeffentlich = await signaturSchluesselVon(konto, geraet)
  if (!oeffentlich) return { art: 'gefaelscht', behauptet: konto }

  const gilt = await pruefe(signaturDaten(mailboxId, roh), beleg, oeffentlich)
  return gilt ? { art: 'geprueft', vonKonto: konto } : { art: 'gefaelscht', behauptet: konto }
}