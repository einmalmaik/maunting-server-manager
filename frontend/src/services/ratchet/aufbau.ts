/** Sitzungsaufbau des Double Ratchet: beginnen, prüfen, anwenden. */

import { base64ToBytes, bytesToBase64 } from '@msdis/shield/core'
import { randomBytes } from '@msdis/shield/random'
import {
  generateRatchetKeyPair,
  initReceiverState,
  initSenderState,
  type RatchetDhKeyPair,
} from '@msdis/shield/messaging'

import { signiere } from '../absenderSignatur'
import { encryptE2eeHybrid } from '../e2eeCrypto'
import { eigenesGeraet, pruefeGeraeteBeleg, type EigenesGeraet } from '../e2eeGeraet'
import { kennstMarke, merkeMarke, schritt, sitzungsId } from '../ratchetSpeicher'
import {
  DR_INIT_TYP,
  GEHEIMNIS_BYTES,
  aufbauDaten,
  gebundeneDaten,
  istAufbauInhalt,
  type AufbauErgebnis,
  type BootstrapInhalt,
  type DrKontext,
} from './format'

export async function beginneSitzung(
  kontext: DrKontext,
  meins: EigenesGeraet,
  zielGeraet: string,
  zielOeffentlicher: string,
) {
  const paar: RatchetDhKeyPair = await generateRatchetKeyPair()
  const geheimnis = randomBytes(GEHEIMNIS_BYTES)

  const inhalt: BootstrapInhalt = {
    typ: DR_INIT_TYP,
    v: 1,
    vonKonto: kontext.eigeneId,
    vonGeraet: meins.kennung,
    fuerGeraet: zielGeraet,
    geheimnis: bytesToBase64(geheimnis),
    paar: {
      algorithm: paar.algorithm,
      publicKey: bytesToBase64(paar.publicKey),
      privateKey: bytesToBase64(paar.privateKey),
    },
  }
  // Scheitert das, scheitert der Aufbau für dieses Ziel. Ein unsignierter
  // Aufbau aus diesem Stand wäre genau die Lücke, die die Unterschrift schließt.
  inhalt.sig = await signiere(aufbauDaten(kontext, inhalt), meins.signaturPaar.privateKeyJwk)
  const umschlag = await encryptE2eeHybrid(
    JSON.stringify(inhalt),
    zielOeffentlicher,
    meins.paar.publicKeyJwk,
  )
  paar.privateKey.fill(0)

  const zustand = await initSenderState({
    sharedSecret: geheimnis,
    remotePublicKey: paar.publicKey,
    associatedData: gebundeneDaten(kontext, meins.kennung, zielGeraet),
  })
  return { umschlag, zustand }
}

/**
 * Nimmt einen entschlüsselten Hybrid-Klartext entgegen und richtet die Sitzung
 * ein, falls es ein Sitzungsaufbau ist.
 *
 * Meldet `istAufbau: false`, wenn der Klartext etwas anderes war — dann gehört
 * er in den normalen Lesepfad. Ein Aufbau für ein fremdes Gerät wird
 * übergangen: er ist eine der aufgefächerten Kopien und nicht für dieses Gerät
 * bestimmt.
 *
 * Kommt ein Aufbau, obwohl schon eine Sitzung steht, ersetzt er sie. Die
 * Gegenstelle hat dann ihren Zustand verloren, und ihr altes Gegenstück liegt
 * nur noch im Weg. `ersetzt: true` sagt dem Aufrufer, dass er das sichtbar
 * machen soll.
 *
 * Angewandt wird nur, was das nennende Gerät unterschrieben hat — oder was von
 * einem Konto kommt, das nirgends unterschreiben kann. Siehe `AufbauErgebnis`
 * für die beiden anderen Ausgänge.
 */
export async function verarbeiteBootstrap(
  kontext: DrKontext,
  klartext: string,
): Promise<AufbauErgebnis> {
  let inhalt: BootstrapInhalt
  try {
    const roh = JSON.parse(klartext)
    if (!roh || typeof roh !== 'object' || roh.typ !== DR_INIT_TYP) {
      return { istAufbau: false, ersetzt: false }
    }
    if (!istAufbauInhalt(roh)) return { istAufbau: true, ersetzt: false }
    inhalt = roh
  } catch {
    return { istAufbau: false, ersetzt: false }
  }

  const meins = await eigenesGeraet()
  if (inhalt.fuerGeraet !== meins.kennung) {
    // Kopie für ein anderes Gerät desselben Kontos.
    return { istAufbau: true, ersetzt: false }
  }

  // Derselbe Umschlag beim nächsten Abruf ist kein Neuaufbau. Der öffentliche
  // Teil des mitgereisten Paares wird je Aufbau frisch erzeugt und ist damit
  // die Kennung dieses einen Aufbaus — anders als die Gerätekennung, die über
  // alle Aufbauten hinweg dieselbe bleibt.
  const aufbauKennung = inhalt.paar.publicKey
  const herkunft = { vonKonto: inhalt.vonKonto, vonGeraet: inhalt.vonGeraet }
  // Der übliche Fall, und er soll billig bleiben: ein längst angewandter — oder
  // abgewiesener — Aufbau liegt bei jedem Abruf wieder im Fenster und braucht
  // weder Verzeichnis noch Schloss. Die verbindliche Prüfung steht unten.
  if (await kennstMarke('aufbau', aufbauKennung)) {
    // Ein abgewiesener meldet sich nicht noch einmal, schont aber weiter: die
    // Nachricht hinter ihm liegt ebenso wieder im Fenster.
    if (await kennstMarke('abgewiesen', aufbauKennung)) {
      return { istAufbau: true, ersetzt: false, abgewiesen: true, ...herkunft }
    }
    return { istAufbau: true, ersetzt: false, vonGeraet: inhalt.vonGeraet }
  }

  /**
   * Abweisen und merken, in dieser Reihenfolge.
   *
   * `abgewiesen` zuerst: bricht der Vorgang zwischen beiden Marken ab, prüft
   * der nächste Durchlauf den Aufbau eben noch einmal und meldet ihn ein
   * zweites Mal. Andersherum stünde er als angewandt da, und die Nachricht
   * hinter ihm kostete die laufende Sitzung doch noch.
   */
  const weiseAb = async (): Promise<AufbauErgebnis> => {
    await merkeMarke('abgewiesen', aufbauKennung)
    await merkeMarke('aufbau', aufbauKennung)
    return { istAufbau: true, ersetzt: false, abgelehnt: true, abgewiesen: true, ...herkunft }
  }

  // Nur die beiden Konten dieses Gesprächs dürfen hier eine Sitzung setzen.
  // Ohne diese Prüfung könnte ein Absender eine fremde Kontokennung behaupten
  // und damit eine Sitzung überschreiben, die einem anderen Gespräch gehört.
  if (inhalt.vonKonto !== kontext.eigeneId && inhalt.vonKonto !== kontext.peerId) {
    return weiseAb()
  }

  // Von wem ist er? Die Kontenprüfung oben begrenzt das auf zwei Verzeichnisse,
  // die der Sendepfad ohnehin schon abfragt.
  const beleg = await pruefeGeraeteBeleg(
    inhalt.vonKonto,
    inhalt.vonGeraet,
    aufbauDaten(kontext, inhalt),
    inhalt.sig,
  )
  if (beleg === 'offen' || beleg === 'unbekannt') {
    // Nichts merken: der nächste Durchlauf fragt noch einmal. Ein Gerät, das
    // sich eben erst gemeldet hat, steht dann im Verzeichnis.
    return { istAufbau: true, ersetzt: false, offen: true, ...herkunft }
  }
  if (beleg === 'falsch') {
    // Gemerkt, damit derselbe Umschlag nicht bei jedem Abruf eine neue Meldung
    // auslöst. Angewandt wird nichts — die laufende Sitzung bleibt, wie sie ist,
    // auch gegen die Nachricht dahinter (`DrLeseOptionen.schonen`).
    return weiseAb()
  }

  const id = sitzungsId(meins.kennung, inhalt.vonKonto, inhalt.vonGeraet)

  const paar: RatchetDhKeyPair = {
    algorithm: inhalt.paar.algorithm as RatchetDhKeyPair['algorithm'],
    publicKey: base64ToBytes(inhalt.paar.publicKey),
    privateKey: base64ToBytes(inhalt.paar.privateKey),
  }
  const geheimnis = base64ToBytes(inhalt.geheimnis)

  /**
   * Prüfen, anwenden und merken gehören unter dasselbe Schloss.
   *
   * Die Prüfung oben allein reicht nicht: zwei überlappende Lesedurchläufe
   * sehen denselben Aufbau, beide finden die Marke noch nicht, und beide
   * wenden ihn an. Der zweite setzt damit einen Zustand zurück, der
   * inzwischen schon Nachrichten getragen hat — der Faden verliert seine
   * Sendekette, beide Seiten bauen neu auf, und im Verlauf erscheint ein
   * Sitzungsbruch, den niemand ausgelöst hat.
   *
   * Marke und Sitzung legt `schritt` in einer Transaktion ab. Bis 09/2026
   * stand die Marke vorher in einer eigenen: scheiterte danach das Ablegen der
   * Sitzung, galt der Aufbau bei jedem Abruf als erledigt, und jede Nachricht
   * dieses Geräts lief als Bruch ins Leere. Die Gegenstelle sendet mit ihrem
   * Zustand weiter und baut von sich aus nicht neu auf.
   */
  const ersetzt = await schritt<boolean | null>(id, async (alterZustand) => {
    if (await kennstMarke('aufbau', aufbauKennung)) return { ergebnis: null }
    const neu = await initReceiverState({
      sharedSecret: geheimnis,
      dhKeyPair: paar,
      associatedData: gebundeneDaten(kontext, inhalt.vonGeraet, meins.kennung),
    })
    // `alterZustand` räumt `schritt` selbst ab; hier zählt nur, ob es ihn gab.
    return {
      naechster: neu,
      marke: { bereich: 'aufbau', kennung: aufbauKennung },
      ergebnis: alterZustand !== null,
    }
  })
  paar.privateKey.fill(0)

  return { istAufbau: true, ersetzt: ersetzt === true, ...herkunft }
}
