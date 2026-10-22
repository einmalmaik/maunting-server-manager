/** Lesen und Verwerfen von Double-Ratchet-Umschlägen. */

import { base64ToBytes, bytesToUtf8, utf8ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'
import { decryptMessage, deserializeRatchetMessage } from '@msdis/shield/messaging'

import { eigenesGeraet } from '../e2eeGeraet'
import {
  hatSitzung,
  kennstMarke,
  merkeMarke,
  schritt,
  sitzungsId,
  verwirfSitzung,
} from '../ratchetSpeicher'
import {
  lieseKopf,
  type DrKontext,
  type DrLeseOptionen,
  type DrLesung,
} from './format'

export interface KlartextAblage {
  /** Der abgelegte Klartext, oder `null`. Wirft, wenn die Ablage nicht antwortet. */
  lies(): Promise<string | null>
  lege(text: string): Promise<void>
}

/**
 * Öffnet einen Umschlag dieses Verfahrens.
 *
 * Alles am Zustand passiert innerhalb des Sitzungsschlosses und in dieser
 * Reihenfolge: nachsehen, ob ein anderer Durchlauf den Umschlag schon geöffnet
 * hat; entschlüsseln; ablegen; fortschreiben. Wirft `lege`, bleibt der Zustand
 * stehen und derselbe Umschlag ist beim nächsten Durchlauf erneut lesbar. Das
 * ist der einzige Schutz gegen einen verbrauchten Nachrichtenschlüssel ohne
 * abgelegten Klartext.
 */
export async function liesDrUmschlag(
  kontext: DrKontext,
  umschlag: string,
  klartext: KlartextAblage,
  optionen: DrLeseOptionen = {},
): Promise<DrLesung> {
  const kopf = lieseKopf(umschlag)
  if (!kopf) return { art: 'unbekannt' }

  const meins = await eigenesGeraet()
  if (kopf.vonGeraet === meins.kennung) return { art: 'eigen' }
  if (kopf.fuerGeraet !== meins.kennung) return { art: 'fremd' }
  if (kopf.vonKonto !== kontext.eigeneId && kopf.vonKonto !== kontext.peerId) {
    return { art: 'fremd' }
  }

  // Ein Umschlag wird genau einmal beurteilt. Er bleibt bis zum Ende seiner
  // Aufbewahrung in der Mailbox liegen, und der Lesepfad holt bei jedem Abruf
  // das ganze Fenster neu; abgelegt wird nur Klartext, ein unlesbarer Umschlag
  // also nie. Ohne diese Marke wertete ihn jeder Durchlauf erneut als Bruch —
  // und ein Bruch wirft die Sitzung weg. Am laufenden System hiess das: bei
  // jedem Öffnen des Gesprächs starb die gerade funktionierende Sitzung, die
  // Systemzeile erschien wieder, und die eigenen Nachrichten kamen nicht mehr
  // an. Erkannt wird er am Rumpf, der je Nachricht ein anderer ist.
  const rumpfHash = (await sha256Hex(utf8ToBytes(kopf.rumpf))).slice(0, 32)
  const marke = `${kopf.vonKonto}:${kopf.vonGeraet}:${rumpfHash}`
  if (await kennstMarke('bruch', marke)) return { art: 'beurteilt' }
  if (await kennstMarke('abgewiesen', marke)) return { art: 'abgewiesen' }

  // Der Aufbau davor ist noch nicht entschieden. Jetzt zu öffnen hiesse, gegen
  // die alte Sitzung oder gegen gar keine zu laufen — und das endete als Bruch
  // mit Marke, die Nachricht wäre für immer weg.
  if (optionen.zurueckstellen?.(kopf.vonKonto, kopf.vonGeraet)) return { art: 'zurueckgestellt' }

  /**
   * Was sich nicht öffnen lässt, ist ein Bruch — ausser, von diesem Gerät liegt
   * ein abgewiesener Aufbau im Fenster.
   *
   * Dann ist es die Nachricht aus der gefälschten Sitzung, und als Bruch
   * gezählt kippte sie die echte doch noch. Gemerkt wird sie trotzdem, unter
   * eigener Marke: beim nächsten Abruf bleibt sie still, auch wenn der Aufbau
   * bis dahin aus dem Fenster gerutscht ist.
   */
  const id = sitzungsId(meins.kennung, kopf.vonKonto, kopf.vonGeraet)

  const unlesbar = async (grund: string): Promise<DrLesung> => {
    if (optionen.schonen?.(kopf.vonKonto, kopf.vonGeraet)) {
      await merkeMarke('abgewiesen', marke)
      return { art: 'abgewiesen' }
    }
    /*
     * Ein Bruch ist erst ein Bruch, wenn diese Sitzung schon einmal getragen
     * hat.
     *
     * Eine Empfängersitzung, die noch keine Nachricht geöffnet hat, ist beim
     * ersten Kontakt angelegt — aber nichts an ihr hat je funktioniert. Bis
     * 10/2026 wurde jeder unlesbare Umschlag dazu als Bruch gewertet, mit
     * Marke: die Nachricht war für immer weg, und der Verlauf meldete einen
     * Neuaufbau, den es nie gab. Jetzt bleibt sie ungeöffnet liegen.
     */
    if (!(await kennstMarke('etabliert', id))) return { art: 'keine-sitzung' }
    await merkeMarke('bruch', marke)
    return { art: 'bruch', vonKonto: kopf.vonKonto, vonGeraet: kopf.vonGeraet, grund }
  }
  /*
   * Noch keine Sitzung ist **kein** Bruch.
   *
   * Kam die Nachricht vor ihrem Aufbau an — umgekehrte Zeitfolge, zwei Geräte,
   * ein Fenster voller Umschläge —, wurde bis 10/2026 ein Bruch daraus, mit
   * Marke. Die Nachricht war damit dauerhaft verloren, obwohl die Sitzung im
   * selben Durchlauf noch entstand. Jetzt bleibt sie liegen und wird beim
   * nächsten Versuch geöffnet.
   */
  if (!(await hatSitzung(id))) return { art: 'keine-sitzung' }

  let nachricht
  try {
    nachricht = deserializeRatchetMessage(bytesToUtf8(base64ToBytes(kopf.rumpf)))
  } catch {
    return { art: 'unbekannt' }
  }

  try {
    const text = await schritt<string>(id, async (zustand) => {
      // Hat ihn ein anderer Durchlauf in der Zwischenzeit geöffnet? Dann ist
      // sein Nachrichtenschlüssel verbraucht, und ein zweiter Versuch wäre
      // nicht bloß vergeblich: er sähe aus wie eine Fälschung und kostete die
      // Sitzung. Ohne `naechster` bleibt der abgelegte Zustand unangetastet.
      const schon = await klartext.lies()
      if (schon !== null) return { ergebnis: schon }

      if (!zustand) throw new Error('Sitzung verschwunden')
      const { nextState, plaintext } = await decryptMessage(zustand, nachricht)
      const text = bytesToUtf8(plaintext)
      // Erst ablegen, dann den Zustand fortschreiben: ein verbrauchter
      // Nachrichtenschlüssel ohne abgelegten Klartext wäre eine verlorene
      // Nachricht, und zwar ohne jede Meldung.
      await klartext.lege(text)
      /*
       * Ab jetzt ist diese Sitzung **etabliert**.
       *
       * Nur dann meldet ein späterer Bruch einen Neuaufbau. Ein Aufbau, der
       * nichts getragen hat, ist ein Erstkontakt — und wer ihn als „Sitzung
       * neu aufgebaut" meldet, zeigt beim ersten Zusammentreffen eine Warnung,
       * die niemand deuten kann. Die Marke geht mit dem fortgeschriebenen
       * Zustand in dieselbe Transaktion.
       */
      return {
        naechster: nextState,
        marken: [{ bereich: 'etabliert', kennung: id }],
        ergebnis: text,
      }
    })
    return { art: 'klartext', text, vonKonto: kopf.vonKonto, vonGeraet: kopf.vonGeraet }
  } catch (fehler) {
    // Was DIS hier werfen kann, betrifft immer die Sitzung: ein gescheiterter
    // Tag (`DisDecryptionError`) oder ein Zustand, der sich nicht mehr lesen
    // lässt (`DisInvalidArgumentError` aus `deserializeRatchetState`). Beides
    // heißt, dass dieser Faden nicht mehr trägt. Die Nachricht selbst ist schon
    // vor dem Schloss geprüft worden.
    //
    // Alles andere — eine volle Platte, eine geschlossene Datenbank, ein
    // abgebrochener Schreibvorgang — ist vorübergehend und darf die Sitzung
    // nicht kosten.
    const name = fehler instanceof Error ? fehler.name : 'unbekannt'
    if (!name.startsWith('Dis')) {
      return { art: 'fehler', grund: name }
    }
    return unlesbar(name)
  }
}

/**
 * Wirft die Sitzung zu einem Gerät weg.
 *
 * Danach baut der nächste Sendevorgang eine frische auf. Der Aufrufer meldet das
 * sichtbar im Verlauf — eine stillschweigend neu aufgebaute Sitzung wäre genau
 * das, was ein Angreifer sich wünscht.
 */
export async function verwirfDrSitzung(vonKonto: number, vonGeraet: string): Promise<void> {
  const meins = await eigenesGeraet()
  await verwirfSitzung(sitzungsId(meins.kennung, vonKonto, vonGeraet))
}
