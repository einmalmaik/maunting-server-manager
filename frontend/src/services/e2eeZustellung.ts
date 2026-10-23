/**
 * Die gerätebezogene Zustellung — der Client des neuen Wegs.
 *
 * Bis 23.10.2026 teilten sich alle Geräte eines Kontos eine blinde Mailbox.
 * Der Absender legte eine Kopie je Zielgerät hinein, und die Kopie lag dort
 * 30 Tage, obwohl sie längst gelesen war. Zwei Geräte desselben Kontos sahen
 * dieselbe Mailbox, und ein Gerät, das die Nachricht nicht öffnen konnte,
 * blockierte die für die anderen.
 *
 * Der neue Weg: **je Gerät eine opake Zustelladresse**, ein Zustellauftrag je
 * Nachricht, und ein Quittung, das den Auftrag löscht. Was das für den
 * Absender ändert, ist nur die Adresse; der Chiffretext ist derselbe und
 * geht an dasselbe Gerät.
 *
 * **Drei Dinge gehören zusammen.**
 *
 * 1. **Die Zustelladresse** entsteht aus dem geheimen Schlüsselpaar dieses
 *    Geräts. Der Server kann sie nicht nachrechnen — er kennt nur den
 *    öffentlichen Teil. Ein Gerät, das seinen Schlüssel verliert, ist ein
 *    neues Gerät und bekommt eine neue Adresse.
 * 2. **Der Gerätebeleg** ist eine Unterschrift mit dem Signaturschlüssel
 *    dieses Geräts über genau die Anfrage. Sie bindet die Operation, die
 *    Zustelladresse und die Kennungen. Der Server prüft sie gegen den
 *    öffentlichen Signaturschlüssel, den das Gerät selbst veröffentlicht hat.
 * 3. **Das Quittung** kommt nach dem lokalen Commit: erst wenn die Nachricht
 *    entschlüsselt, verarbeitet und gespeichert ist, gilt sie als zugestellt.
 *
 * **Die Zeichenketten sind dieselben wie im Server.** `backend/services/social/zustellung.py`
 * bildet sie; ändert sich eine, ändert sich die andere. Die Klartextregeln:
 * keine Leerzeichen, `:` zwischen den Feldern, `|` zwischen den Eintraegen,
 * sortiert — damit die Reihenfolge im Paket nichts bedeutet.
 */

import { api } from '@/api/client'
import { signiere } from './absenderSignatur'
import { eigenesGeraet } from './e2eeGeraet'
import { inboxAdresseAus } from './geraet/inboxAdresse'
import { nachweisKopf } from './mailboxNachweis'

/** Wie der Server die Zustelladresse nennt. */
export const TRANSPORT_FAEHIGKEIT = 'geraet-v1'

/**
 * Hoechstens so viele Zustellauftraege nimmt der Server je Anfrage an.
 *
 * Dieselbe Zahl wie `MAX_BATCH` in `backend/services/social/zustellung.py`.
 * Eine Gruppe mit mehr Zielgeraeten teilt der Client deshalb in mehrere
 * Anfragen; der Chiffretext bleibt dabei **ein** Objekt.
 */
export const MAX_AUFTRAEGE_JE_ANFRAGE = 50

/** Ein Chiffretextobjekt — ohne Ziel, ohne Absender. */
export interface ZustellPayload {
  id: string
  ciphertext: string
  ciphertext_sha256: string
}

/** Ein Zustellauftrag: ein Chiffretextobjekt fuer eine Zustelladresse. */
export interface ZustellEintrag {
  delivery_id: string
  payload_id: string
  inbox_hash: string
}

/** Was der Abruf liefert. */
export interface ZustellAbruf {
  eintraege: {
    delivery_id: string
    payload_id: string
    ciphertext: string
    ciphertext_sha256: string
    seq: number
    angenommen_am: string
    ablauf_am: string
  }[]
  naechste_seq: number
}

/**
 * Die Zustelladresse dieses Geräts.
 *
 * Sie haengt am geheimen Schluesselpaar, nicht an der Geraetekennung: die
 * Kennung kann sich aendern (neue Kopie des Profils), das Paar nicht, ohne
 * dass es ein neues Geraet ist. Wer die Adresse kennt, kann sie nicht
 * benutzen — zum Abholen gehoert der Beleg dazu.
 */
export async function eigeneInboxAdresse(): Promise<string> {
  return inboxAdresseAus(await eigenesGeraet())
}

/** Die Zeichenkette, die das sendende Geraet ueber einen Stapel unterschreibt. */
export function relayBeweisDaten(
  geraetId: string,
  mailboxId: string,
  eintraege: { delivery_id: string; payload_id: string; inbox_hash: string; ciphertext_sha256: string }[],
): string {
  const teile = eintraege
    .map(
      (e) => `${e.delivery_id}:${e.payload_id}:${e.inbox_hash}:${e.ciphertext_sha256}`,
    )
    .sort()
  return `msm:device-delivery-v1:${geraetId}:${mailboxId}:` + teile.join('|')
}

/** Die Zeichenkette, die ein Geraet ueber seinen Abruf unterschreibt. */
export function leseBeweisDaten(inboxHash: string, seitSeq: number): string {
  return `msm:device-delivery-read-v1:${inboxHash}:${seitSeq}`
}

/** Die Zeichenkette, die ein Geraet ueber sein Quittung unterschreibt. */
export function ackBeweisDaten(inboxHash: string, deliveryIds: string[]): string {
  return `msm:device-delivery-ack-v1:${inboxHash}:` + [...deliveryIds].sort().join(',')
}

async function belege(daten: string): Promise<string> {
  const geraet = await eigenesGeraet()
  return signiere(daten, geraet.signaturPaar.privateKeyJwk)
}

/**
 * Nimmt einen Stapel Zustellungen an.
 *
 * `mailboxId` ist der **Kontext**: der Server prüft daraus, ob dieses Konto in
 * dieses Gespraech schreiben darf (dieselbe Pruefung wie beim Relais). Der
 * Stapel selbst nennt je Eintrag die Zustelladresse des Ziels.
 */
export async function sendeZustellungen(
  mailboxId: string,
  payloads: ZustellPayload[],
  eintraege: ZustellEintrag[],
): Promise<{
  angenommen: { delivery_id: string; seq: number; ergebnis: string }[]
  abgelehnt: { delivery_id: string; grund: string }[]
}> {
  const geraet = await eigenesGeraet()
  const signatur = await belege(
    relayBeweisDaten(
      geraet.kennung,
      mailboxId,
      eintraege.map((e) => ({
        delivery_id: e.delivery_id,
        payload_id: e.payload_id,
        inbox_hash: e.inbox_hash,
        ciphertext_sha256:
          payloads.find((p) => p.id === e.payload_id)?.ciphertext_sha256 ?? '',
      })),
    ),
  )
  return api('/social/e2ee/deliveries', {
    method: 'POST',
    body: JSON.stringify({
      mailbox_id: mailboxId,
      geraet_id: geraet.kennung,
      payloads,
      eintraege,
      signatur,
    }),
    headers: nachweisKopf(mailboxId),
  })
}

/** Holt die offenen Auftraege der eigenen Zustelladresse. */
export async function holeZustellungen(
  inboxHash: string,
  seitSeq: number,
  grenze = 100,
): Promise<ZustellAbruf> {
  const beleg = await belege(leseBeweisDaten(inboxHash, seitSeq))
  return api(`/social/e2ee/deliveries?inbox_hash=${encodeURIComponent(inboxHash)}&seit_seq=${seitSeq}&limit=${grenze}`, {
    headers: { 'X-Geraete-Beleg': beleg },
  })
}

/**
 * Quittiert die verarbeiteten Zustellungen.
 *
 * Der Server loescht genau diese Aufträge und nichts weiter. Eine wiederholte
 * Quittung ist erfolgreich und loescht nichts mehr.
 */
export async function quittiereZustellungen(
  inboxHash: string,
  deliveryIds: string[],
): Promise<{ quittiert: number }> {
  const geraet = await eigenesGeraet()
  const signatur = await belege(ackBeweisDaten(inboxHash, deliveryIds))
  return api('/social/e2ee/deliveries/ack', {
    method: 'POST',
    body: JSON.stringify({
      inbox_hash: inboxHash,
      geraet_id: geraet.kennung,
      delivery_ids: deliveryIds,
      signatur,
    }),
  })
}
