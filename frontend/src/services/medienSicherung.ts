/**
 * Die Anhänge dauerhaft auf dem Gerät halten.
 *
 * Bis 10/2026 lag ein empfangenes Bild nur im Arbeitsspeicher
 * (`chatMediaBlobCache`). Nach dem Neuladen war es weg, und ohne Netz kam es
 * nie wieder: der Server behält den Blob 30 Tage, und die Nachricht trägt nur
 * einen Zeiger darauf. Ein Anhang, den niemand angeklickt hat, war damit
 * nach einem Neuladen verloren — bei einer Sprachnachricht heisst das: die
 * Nachricht steht da und ist stumm.
 *
 * Dieser Dienst holt einen Anhang **einmal** und legt ihn entschlüsselt in der
 * kontogebundenen Ablage ab (`messengerLocalStore`). Danach kommt er von
 * dort, ohne Netz und ohne Server.
 *
 * Drei Regeln:
 *
 * 1. **Ein Anhang wird einmal geholt.** Liegt er schon in der Ablage, ist der
 *    Download erledigt. Der Arbeitsspeicher davor ist nur eine Optimierung.
 * 2. **Ein Fehler ist kein Verlust.** Ein Anhang, der nicht geladen werden
 *    kann (Server weg, Ablauf, Kontingent), blockiert nichts. Er bekommt einen
 *    dauerhaften Nachholauftrag (`medienNachholen.ts`); die Anzeige versucht
 *    es später wieder, auch ohne offenen Chat und nach einem Neustart.
 * 3. **Der Inhalt bleibt lokal.** Was hier abgelegt wird, ist der Klartext der
 *    Datei als data-URL — im Siegel der Ablage, also mit demselben Schutz wie
 *    der Verlauf. Ohne PIN gilt dieselbe Grenze wie dort.
 */

import { ladeAnhangHerunter } from '@/api/social'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { getSafeAttachmentUrl } from '@/lib/sanitizeSvg'
import {
  istMediumGeloescht,
  leseMedium,
  speichereMedium,
} from './messengerLocalStore'
import { chatMediaBlobCache } from './klartextSpeicher'
import { merkeMedienNachholauftrag } from './medienAuftraege'
import type { MedienZeiger } from './medienKrypto'

/** Wie viele Anhänge gleichzeitig geholt werden. */
const GLEICHZEITIG = 2

/** Ein Anhang, wie er in einer Nachricht steht. */
export interface SicherbarerAnhang {
  mediaId?: string
  paketSchluessel?: string
  fileId?: string
  name?: string
}

/** Die Zeile, um die es geht. */
export interface SicherbareNachricht {
  senderId?: number
  isSelf?: boolean
  imageAttachment?: SicherbarerAnhang | null
  audioAttachment?: SicherbarerAnhang | null
  fileAttachment?: SicherbarerAnhang | null
  videoNoteAttachment?: SicherbarerAnhang | null
}

/** Woran ein Anhang hängt: wer ihn schickte und in welche Mailbox. */
export interface MedienBindung {
  absenderId: number
  blindMailboxId: string
}

/** Der Zeiger eines Anhangs — oder `null`, wenn er unvollständig ist. */
export function zeigerVon(anhang: SicherbarerAnhang | null | undefined): MedienZeiger | null {
  if (!anhang?.mediaId || !anhang.paketSchluessel || !anhang.fileId) return null
  return {
    mediaId: anhang.mediaId,
    paketSchluessel: anhang.paketSchluessel,
    fileId: anhang.fileId,
  }
}

/**
 * Der Anhang, wie er gerade dasteht: im Arbeitsspeicher, sonst in der Ablage.
 *
 * `null` heisst: hier liegt nichts. Die Anzeige holt ihn dann selbst — so ist
 * das Verhalten vor und nach dem Speichern dasselbe.
 */
export async function holeMedium(mediaId: string): Promise<string | null> {
  if (!mediaId) return null
  const imSpeicher = chatMediaBlobCache.get(mediaId)
  if (imSpeicher) return imSpeicher
  try {
    return await leseMedium(mediaId)
  } catch {
    return null
  }
}

/**
 * Holt einen Anhang und legt ihn ab — wenn er nicht schon **dauerhaft** da
 * liegt.
 *
 * Gibt zurück, ob der Anhang danach lokal gesichert ist. Das ist die
 * Zusage, die die Anzeige braucht: „dauerhaft verfügbar" heisst „liegt auf
 * diesem Gerät", nicht „stand eben noch im Arbeitsspeicher".
 *
 * **Der Arbeitsspeicher zählt hier nicht.** Er ist eine Optimierung über der
 * Ablage: ein Anhang, den die Anzeige eben geholt hat, ist im RAM, aber nach
 * dem Neuladen weg. Zählte er als gesichert, bliebe die Ablage leer — und die
 * Zusage wäre falsch. Bis 23.10.2026 war genau das so: die native App zeigte
 * das Bild, und nach dem Neuladen war es weg.
 */
export async function sichereMedium(
  zeiger: MedienZeiger,
  bindung: MedienBindung,
  name?: string | null,
): Promise<boolean> {
  return (await sichereMediumMitGrund(zeiger, bindung, name)) === 'gesichert'
}

/**
 * Warum ein Anhang (noch) nicht liegt — die Auskunft, die der Nachholauftrag
 * braucht.
 *
 * `gesichert` heisst: liegt dauerhaft in der Ablage.
 * `geloescht` heisst: seine Nachricht wurde geloescht oder ist verfallen; ein
 * Grabstein weist jeden weiteren Versuch ab.
 * `nicht_mehr_da` heisst: der Blob ist endgueltig nicht zu bekommen (404/410)
 * oder die Antwort ist unbrauchbar. Ein zweiter Versuch wuerde nichts aendern.
 * `offen` heisst: Netz, Sperre, Kontingent — spaeter noch einmal.
 */
export type SicherungsGrund = 'gesichert' | 'geloescht' | 'nicht_mehr_da' | 'offen'

/** Ist der Fehler endgueltig? Nur ein Status, kein Text. */
function istEndgueltigWeg(fehler: unknown): boolean {
  const status = Number((fehler as { status?: unknown } | null | undefined)?.status)
  return status === 404 || status === 410
}

export async function sichereMediumMitGrund(
  zeiger: MedienZeiger,
  bindung: MedienBindung,
  name?: string | null,
  /**
   * Das Konto, fuer das der Aufrufer gestartet ist.
   *
   * Ein Hintergrundlauf darf nicht in die Ablage eines anderen Kontos
   * schreiben, wenn waehrend des Downloads jemand wechselt. Ist der Wert
   * angegeben und stimmt nicht mehr, wird nicht gespeichert.
   */
  erwartetesKonto?: number | null,
): Promise<SicherungsGrund> {
  // Ein geloeschter Anhang wird nicht mehr geholt. Der Grabstein entsteht vor
  // dem Loeschen; danach ist die Kennung fuer immer gesperrt.
  try {
    if (await istMediumGeloescht(zeiger.mediaId)) return 'geloescht'
  } catch {
    // Ohne Ablage entscheidet am Ende das Schreiben.
  }

  try {
    const gespeichert = await leseMedium(zeiger.mediaId)
    if (gespeichert) return 'gesichert'
  } catch {
    // Die Ablage streikt: dann holen wir den Anhang und versuchen es noch
    // einmal mit dem Schreiben.
  }

  try {
    const klartext = await ladeAnhangHerunter(zeiger, bindung)
    const sicher = getSafeAttachmentUrl(klartext)
    // Eine abgewiesene URL wird durch Wiederholen nicht besser.
    if (!sicher) return 'nicht_mehr_da'

    /*
     * Nach dem Download noch einmal fragen.
     *
     * Zwischen dem ersten Blick und der Antwort des Servers kann die Nachricht
     * geloescht worden sein. Das Schreiben prueft den Grabstein zwar selbst in
     * seiner Transaktion; hier sparen wir uns den Weg dorthin — und vor allem
     * den Eintrag im Arbeitsspeicher, der sonst genau die geloeschte Datei
     * wieder anzeigte.
     */
    try {
      if (await istMediumGeloescht(zeiger.mediaId)) return 'geloescht'
    } catch {
      // siehe oben
    }

    if (erwartetesKonto !== undefined && angemeldetesKonto() !== erwartetesKonto) {
      return 'offen'
    }
    const abgelegt = await speichereMedium(zeiger.mediaId, sicher, name ?? null)
    if (!abgelegt) return 'geloescht'
    // Erst nach dem Schreiben in den Zwischenspeicher: der Arbeitsspeicher ist
    // die Optimierung, nicht der Bestand.
    chatMediaBlobCache.set(zeiger.mediaId, sicher)
    return 'gesichert'
  } catch (fehler) {
    // Kein Netz, abgelaufen, Kontingent: der Anhang bleibt offen und wird
    // spaeter erneut versucht. Nur ein ausdrueckliches 404/410 gibt auf.
    return istEndgueltigWeg(fehler) ? 'nicht_mehr_da' : 'offen'
  }
}

/**
 * Holt alle Anhänge dieser Nachrichten und legt sie ab.
 *
 * **Auch die eigenen Nachrichten.** Das ist nicht selbstverständlich: das
 * Gerät, das den Anhang hochgeladen hat, legt ihn beim Hochladen ab. Das
 * *zweite* Gerät desselben Kontos hat die Datei aber nicht — es sieht nur
 * den Zeiger im Nachrichtenpayload. Es stand hier bis 23.10.2026 als „eigene
 * Nachricht“ auf der Liste und holte den Anhang nie; das Bild war dann nur im
 * RAM, nach dem Neuladen weg.
 *
 * Das Hochladenegerät lädt dabei nichts zweimal: `sichereMedium` findet den
 * Anhang in der Ablage und ist fertig.
 *
 * Gibt zurück, wie viele Anhänge danach lokal gesichert sind. Ein Anhang, der
 * nicht gesichert werden konnte, ist **nicht** gesichert — und wird nicht so
 * gemeldet. Für ihn liegt danach ein Nachholauftrag in der Ablage; ein
 * geloeschter Anhang bekommt keinen.
 */
export async function sichereAnhaenge(
  nachrichten: readonly SicherbareNachricht[],
  blindMailboxId: string,
): Promise<{ gesichert: number; offen: number }> {
  if (!blindMailboxId) return { gesichert: 0, offen: 0 }

  // Das Konto, fuer das dieser Durchlauf arbeitet. Wechselt es mittendrin,
  // wird nichts mehr gespeichert und kein Auftrag in die fremde Ablage gelegt.
  const konto = angemeldetesKonto()
  const aufträge: { zeiger: MedienZeiger; bindung: MedienBindung; name?: string | null }[] = []
  for (const nachricht of nachrichten) {
    const absenderId = Number(nachricht.senderId)
    if (!Number.isInteger(absenderId) || absenderId <= 0) continue
    for (const anhang of [
      nachricht.imageAttachment,
      nachricht.audioAttachment,
      nachricht.fileAttachment,
      nachricht.videoNoteAttachment,
    ]) {
      const zeiger = zeigerVon(anhang)
      if (!zeiger) continue
      aufträge.push({
        zeiger,
        bindung: { absenderId, blindMailboxId },
        name: anhang?.name ?? null,
      })
    }
  }
  if (aufträge.length === 0) return { gesichert: 0, offen: 0 }

  let gesichert = 0
  let offen = 0
  // Nicht alle auf einmal: ein Chat mit fünf Bildern soll den Speicher nicht
  // auf einmal fünf Mal beanspruchen.
  for (let i = 0; i < aufträge.length; i += GLEICHZEITIG) {
    const stueck = aufträge.slice(i, i + GLEICHZEITIG)
    const ergebnisse = await Promise.all(
      stueck.map(async (a) => {
        const grund = await sichereMediumMitGrund(a.zeiger, a.bindung, a.name, konto)
        /*
         * Ein offener Anhang bekommt einen dauerhaften Auftrag.
         *
         * Bisher haing der zweite Versuch daran, dass dieselben Umschlaege
         * noch einmal verarbeitet werden. Eine quittierte Postfach-Nachricht
         * kommt nie wieder, und ohne offenen Chat gab es gar keinen Anlass —
         * der Anhang war dann fuer immer verloren, obwohl der Server ihn noch
         * haelt. Der Auftrag ueberlebt Neustart und Chatwechsel.
         */
        if (grund === 'offen' && angemeldetesKonto() === konto) {
          await merkeMedienNachholauftrag(a.zeiger, a.bindung, a.name).catch(() => {})
        }
        return grund
      }),
    )
    for (const grund of ergebnisse) {
      if (grund === 'gesichert') gesichert += 1
      else if (grund === 'offen') offen += 1
    }
  }
  return { gesichert, offen }
}
