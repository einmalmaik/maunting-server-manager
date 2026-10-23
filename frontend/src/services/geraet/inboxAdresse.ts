/**
 * Die Zustelladresse eines Geräts.
 *
 * Sie entsteht aus dem geheimen Schlüsselpaar des Geräts und ist damit nur
 * für dieses Gerät berechenbar: der Server kennt den öffentlichen Teil und
 * kann sie nicht nachrechnen. Wer die Adresse kennt, kann sie allein trotzdem
 * nicht benutzen — zum Abholen gehört der Gerätebeleg dazu, und der ist nur
 * mit dem privaten Signaturschlüssel zu erzeugen.
 *
 * **Warum aus dem Paar und nicht aus der Gerätekennung:** die Kennung kann
 * sich ändern (ein neues Profil, eine Kopie des Speichers), das Paar nicht,
 * ohne dass es ein neues Gerät ist. Hätte man die Kennung genommen, würde
 * eine Kopie mit der alten Adresse das Postfach des Originals öffnen.
 *
 * Das steht hier und nicht in `e2eeZustellung.ts`: dort wird der eigene
 * Geräteschlüssel gebraucht, und das führt in einen Kreis (`e2eeGeraet` →
 * `geraeteFreigabe` → `e2eeZustellung` → `e2eeGeraet`).
 */

import { utf8ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'

import type { EigenesGeraet } from './geraeteAblage'

/** Die Zustelladresse zu einem Gerät. */
export async function inboxAdresseAus(geraet: EigenesGeraet): Promise<string> {
  return sha256Hex(utf8ToBytes(`msm:device-inbox:v1:${geraet.paar.privateKeyJwk}`))
}
