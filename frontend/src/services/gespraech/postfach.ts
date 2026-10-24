/**
 * Das Geraetepostfach: lesen, dem Gespraech zuordnen, ablegen, quittieren.
 *
 * Der Hintergrundlauf des neuen Transports. Er laeuft ohne offenen Chat und
 * erledigt genau das, was der Chat sonst beim Oeffnen tut:
 *
 * 1. Die offenen Zustellungen der eigenen Inbox holen (die Adresse entsteht
 *    aus dem geheimen Schluessel dieses Geraets).
 * 2. Jeden Eintrag seinem Gespraech zuordnen — der Umschlag selbst sagt, wohin
 *    er gehoert:
 *    - **Ratchet:** der Kopf nennt das Absenderkonto, also die Gegenstelle.
 *    - **Gruppe:** der Kopf nennt die Schluesselkennung; sie gehoert zu genau
 *      einer Gruppe dieses Kontos.
 *    - **Hybrid:** entschluesseln; ein Sitzungsaufbau nennt sein Konto, eine
 *      Wirkung ihre Mailbox.
 * 3. Im Gespraech verarbeiten (`verarbeiteGespraech`, nur die Eintraege) und
 *    in den Verlauf schreiben (`uebernimmLesungen`).
 * 4. **Erst danach quittieren.** Das Quittung loescht den Auftrag beim Server;
 *    vor dem lokalen Commit waere die Nachricht weg, sobald der Schreibweg
 *    scheitert.
 *
 * **Was nicht zugeordnet werden kann, bleibt liegen.** Ein fremdes Gespraech,
 * ein fehlender Schluessel, ein Umschlag aus einer Generation, die dieses
 * Geraet nicht kennt: nichts davon wird quittiert, damit der naechste Lauf es
 * wiedersieht. Deshalb gibt es **keinen** Lesestand fuer das Postfach: was
 * quittiert ist, ist erledigt, und was liegt, kommt wieder.
 */

import type { BlindEnvelopeItem, ChatGroupItem } from '@/api/social'
import { decryptE2eeHybridWithKeyring } from '@/services/e2eeCrypto'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import { eigeneInboxAdresse, holeZustellungen, quittiereZustellungen } from '@/services/e2eeZustellung'
import { sendeSteuerpaket } from '@/services/gespraech/steuerung'
import { ablage } from '@/services/gruppen/gruppenAblage'
import { GRUPPE_PREFIX, type GruppenKontext } from '@/services/gruppenSchluessel'
import { lieseGruppenKopf } from '@/services/gruppen/typen'
import {
  merkePartnerKennungen,
  merkePartnerQuittung,
  merkeQuittungKennungen,
  partnerstand,
  quittungsKennungen,
} from '@/services/quittungsstand'
import { DR_PREFIX, drUrheber, type DrKontext } from '@/services/ratchetSitzung'

import { verarbeiteGespraech } from './verarbeitung'
import { uebernimmLesungen } from './uebernahme'

/** Ein Gespraech, dem ein Eintrag im Postfach gehoeren kann. */
export interface PostfachGespraech {
  /** Die Mailbox des Gespraechs (die abgeleitete Kennung). */
  mailbox: string
  drKontext: DrKontext | null
  gruppenKontext: GruppenKontext | null
  contact: { userId: number; username?: string; isFriend?: boolean } | null
  group: ChatGroupItem | null
}

export interface PostfachLauf {
  eigeneId: number
  decryptionKeys: string[]
  gespraeche: readonly PostfachGespraech[]
  gruppenrechteVon: (konto: number) => Set<string>
  /**
   * Die Rechte **dieses** Gespraechs. Der sichtbare Chat liefert seine eigene
   * Gruppe; der Hintergrundlauf braucht sie fuer jedes Gespraech, auch ohne
   * offenen Chat. Fehlt der Rueckruf, gilt `gruppenrechteVon`.
   */
  rechteFuerGruppe?: (group: ChatGroupItem) => Promise<(konto: number) => Set<string>>
  t: (schluessel: string, werte?: Record<string, unknown>) => string
  markAsRead: (mailbox: string) => void
  setVerfallSekunden: (sekunden: number) => void
  /** Fuer die Zustellquittung an den Absender; ohne sie bleibt sie aus. */
  identitaet?: E2eeIdentity | null
  /**
   * Neue, eingehende Zeilen nach dem lokalen Commit. Der Aufrufer meldet sie
   * (Zahl, Ton, Einblendung); das Gerätepostfach erzeugt kein Sync-Ereignis,
   * aus dem eine andere Stelle sie erfahren könnte.
   */
  beiNeuerNachricht?: (
    gespraech: PostfachGespraech,
    nachrichten: readonly { clientUuid?: string; senderId?: number }[],
  ) => void
  /** Blockierte Kontakte bekommen keine Zustellquittung und keine Meldung. */
  istBlockiert?: (userId: number) => boolean
}

/** Hoechstens so viele Eintraege holt ein Lauf (die Grenze des Servers). */
const SEITE = 50

/**
 * Ordnet einen Eintrag seinem Gespraech zu.
 *
 * Die Zuordnung kommt aus dem Umschlag, nicht aus der Adresse: das Postfach
 * gehoert dem Geraet und traegt alle Gespraeche.
 */
async function ordneZu(
  eintrag: { ciphertext: string },
  gespraeche: readonly PostfachGespraech[],
  decryptionKeys: string[],
): Promise<PostfachGespraech | null> {
  const umschlag = eintrag.ciphertext
  if (umschlag.startsWith(DR_PREFIX)) {
    const kopf = drUrheber(umschlag)
    if (!kopf) return null
    return gespraeche.find((g) => g.drKontext?.peerId === kopf.vonKonto) ?? null
  }
  if (umschlag.startsWith(GRUPPE_PREFIX)) {
    const kopf = lieseGruppenKopf(umschlag)
    if (!kopf) return null
    for (const gespraech of gespraeche) {
      if (!gespraech.gruppenKontext) continue
      const aktuell = await ablage
        .liesAktuellen(gespraech.gruppenKontext.groupId)
        .catch(() => null)
      if (aktuell?.keyId === kopf.keyId) return gespraech
    }
    return null
  }

  // Hybrid: Aufbau oder Wirkung. Beides ist gegen den Geraeteschluessel
  // versiegelt, also laesst es sich hier oeffnen.
  let klartext: string
  try {
    klartext = await decryptE2eeHybridWithKeyring(umschlag, decryptionKeys)
  } catch {
    return null
  }
  let roh: Record<string, unknown>
  try {
    const geparst = JSON.parse(klartext)
    if (!geparst || typeof geparst !== 'object') return null
    roh = geparst as Record<string, unknown>
  } catch {
    return null
  }
  if (roh.typ === 'dr-init') {
    const konto = Number(roh.vonKonto)
    return gespraeche.find((g) => g.drKontext?.peerId === konto) ?? null
  }
  if (typeof roh.mailbox === 'string') {
    return gespraeche.find((g) => g.mailbox === roh.mailbox) ?? null
  }
  // Eine Wirkung ohne Mailbox-Bindung (etwa eine Quittung an die eigene
  // Mailbox): nicht zuzuordnen, also liegen lassen.
  return null
}

/**
 * Ein Lauf ueber das ganze Postfach.
 *
 * Gibt zurueck, wie viele Eintraege verarbeitet und wie viele quittiert
 * wurden. Ein Eintrag, dessen Gespraech nicht bekannt ist oder dessen
 * Verarbeitung scheitert, bleibt liegen und kommt beim naechsten Lauf wieder.
 */
export interface PostfachErgebnis {
  verarbeitet: number
  quittiert: number
}

/**
 * Ein Lauf zur Zeit, auch über zwei Aufhänger hinweg.
 *
 * Der Kern der Messenger-Seite und die Pontis-Wache können im selben Moment
 * anstoßen (Seitenwechsel, Sync-Ereignis, Takt). Beide lesen dasselbe
 * Gerätepostfach; zwei Läufe würden dieselben Einträge entschlüsseln und der
 * zweite liefe in bereits verbrauchte Ratchet-Schlüssel. Der zweite Aufrufer
 * wartet deshalb auf den laufenden und bekommt „nichts Neues".
 */
let offenerLauf: Promise<PostfachErgebnis> | null = null

export function verarbeitePostfach(lauf: PostfachLauf): Promise<PostfachErgebnis> {
  if (offenerLauf) {
    return offenerLauf.then(() => ({ verarbeitet: 0, quittiert: 0 }))
  }
  offenerLauf = verarbeitePostfachInnen(lauf).finally(() => {
    offenerLauf = null
  })
  return offenerLauf
}

async function verarbeitePostfachInnen(
  lauf: PostfachLauf,
): Promise<PostfachErgebnis> {
  if (!lauf.eigeneId || lauf.decryptionKeys.length === 0) return { verarbeitet: 0, quittiert: 0 }

  const inbox = await eigeneInboxAdresse()
  if (!inbox) return { verarbeitet: 0, quittiert: 0 }
  const abruf = await holeZustellungen(inbox, 0, SEITE).catch(() => null)
  if (!abruf || abruf.eintraege.length === 0) return { verarbeitet: 0, quittiert: 0 }

  // Erst zuordnen, dann je Gespraech verarbeiten: ein Lauf je Gespraech haelt
  // die Reihenfolge innerhalb des Gespraechs (Aufbau vor Nachricht).
  const proGespraech = new Map<PostfachGespraech, typeof abruf.eintraege>()
  for (const eintrag of abruf.eintraege) {
    const ziel = await ordneZu(eintrag, lauf.gespraeche, lauf.decryptionKeys)
    if (!ziel) continue
    const liste = proGespraech.get(ziel) ?? []
    liste.push(eintrag)
    proGespraech.set(ziel, liste)
  }

  let verarbeitet = 0
  let quittiert = 0
  for (const [gespraech, eintraege] of proGespraech) {
    /*
     * Ein Gespraech, dessen Ablage streikt, haelt die uebrigen nicht auf.
     * Nichts davon wird quittiert; der naechste Lauf sieht dieselben Auftraege
     * wieder. Nur so ist „ein Fehler kostet einen Versuch" wahr.
     */
    try {
      const umschlaege: BlindEnvelopeItem[] = eintraege.map((e) => ({
        // Stabil und negativ: derselbe Auftrag bekommt bei jedem Abruf dieselbe
        // Kennung. Der Klartext-Speicher merkt sich damit, was schon geoeffnet
        // ist — sonst liefe ein Ratchet-Schritt ein zweites Mal.
        id: -e.seq,
        blind_mailbox_id: gespraech.mailbox,
        ciphertext_envelope: e.ciphertext,
        client_uuid: e.delivery_id,
        created_at: e.angenommen_am,
        // Die globale Nummer: an ihr misst der Absender seine Haekchen.
        annahme_seq: e.seq,
      }))

      const rechte =
        gespraech.group && lauf.rechteFuerGruppe
          ? await lauf.rechteFuerGruppe(gespraech.group)
          : lauf.gruppenrechteVon
      const stand = partnerstand(gespraech.mailbox)
      const ergebnis = await verarbeiteGespraech(
        {
          mailbox: gespraech.mailbox,
          eigeneId: lauf.eigeneId,
          drKontext: gespraech.drKontext,
          gruppenKontext: gespraech.gruppenKontext,
          decryptionKeys: lauf.decryptionKeys,
          nurPostfach: true,
        },
        {
          // Kein Lesestand: das Postfach kennt keinen Cursor, das Quittung ist
          // der Fortschritt.
          lesestand: new Map(),
          istAktuell: () => true,
          meldeSitzungsbruch: () => {},
          meldeAufbauAbgelehnt: () => {},
        },
        umschlaege,
      )
      if (!ergebnis) continue

      const uebernahme = await uebernimmLesungen(
        {
          mailbox: gespraech.mailbox,
          eigeneId: lauf.eigeneId,
          contact: gespraech.contact,
          group: gespraech.group,
          gruppenrechteVon: rechte,
          t: lauf.t,
          maxPartnerReadId: stand.gelesen,
          maxPartnerDeliveredId: stand.zugestellt,
          quittiertGelesen: (bis) => merkePartnerQuittung(gespraech.mailbox, 'gelesen', bis),
          quittiertZugestellt: (bis) => merkePartnerQuittung(gespraech.mailbox, 'zugestellt', bis),
          quittiertGelesenIds: (ids) => merkePartnerKennungen(gespraech.mailbox, 'gelesen', ids),
          quittiertZugestelltIds: (ids) => merkePartnerKennungen(gespraech.mailbox, 'zugestellt', ids),
          markAsRead: lauf.markAsRead,
          setVerfallSekunden: lauf.setVerfallSekunden,
          // Eine Systemzeile im Verlauf braucht die Oberflaeche; im Hintergrund
          // gibt es sie nicht, und der naechste offene Chat meldet es selbst.
          zeigeSystemzeile: () => {},
        },
        ergebnis.lesungen,
      )
      if (!uebernahme) continue
      verarbeitet += uebernahme.processedList.length

      /*
       * Nur wirklich **neue** Zeilen. Blieb das Transport-ACK liegen, kommt
       * derselbe Auftrag beim nächsten Lauf wieder — und mit ihm dieselbe
       * Nachricht. Ohne diesen Vergleich ginge alle zehn Sekunden eine
       * Zustellquittung hinaus, und eine Meldung klingelte erneut, bis der
       * Auftrag verfällt.
       */
      const bekannt = new Set(
        uebernahme.lokalerVerlauf
          .map((m) => m.clientUuid)
          .filter((id): id is string => Boolean(id)),
      )
      const neueZeilen = uebernahme.processedList.filter(
        (m) => !m.isSelf && Boolean(m.clientUuid) && !bekannt.has(m.clientUuid as string),
      )
      const neue = neueZeilen.map((m) => m.clientUuid as string)
      const blockiert = Boolean(
        !gespraech.group &&
          gespraech.contact &&
          lauf.istBlockiert?.(gespraech.contact.userId),
      )

      /*
       * Das Gerätepostfach erzeugt kein Sync-Ereignis. Wer den Messenger
       * geschlossen hat, erführe von einer hier abgelegten Nachricht sonst
       * nichts — keine Zahl, kein Ton. Deshalb meldet der Lauf sie selbst,
       * und nur die, die wirklich neu sind.
       */
      if (neueZeilen.length > 0 && !blockiert) {
        try {
          lauf.beiNeuerNachricht?.(gespraech, neueZeilen)
        } catch {
          // Eine Meldung ist Beiwerk; die Nachricht ist abgelegt und wird
          // trotzdem quittiert. Der nächste Lauf meldet sie nicht erneut.
        }
      }

      /*
       * Die Zustellquittung an den Absender — auch ohne offenen Chat.
       *
       * Bis 23.10.2026 entstand sie nur im offenen Gespraech. Eine Nachricht,
       * die im Hintergrund ankam, blieb beim Absender auf „gesendet", bis jemand
       * den Chat öffnete — obwohl sie längst entschlüsselt und gespeichert war.
       * Genau das ist die Zusage „zugestellt": ein vertrauenswürdiges Gerät der
       * Gegenstelle hat sie lokal abgelegt.
       *
       * Gelesen ist sie damit nicht. Eine Lesequittung gibt es erst, wenn die
       * Nachricht wirklich auf einem Schirm stand.
       */
      if (!gespraech.group && gespraech.drKontext && lauf.identitaet && !blockiert) {
        if (neue.length > 0) {
          const ids = Array.from(
            new Set([...quittungsKennungen(gespraech.mailbox, 'zugestellt'), ...neue]),
          ).slice(-100)
          merkeQuittungKennungen(gespraech.mailbox, 'zugestellt', ids)
          await sendeSteuerpaket(
            {
              eigeneId: lauf.eigeneId,
              mailbox: gespraech.mailbox,
              drKontext: gespraech.drKontext,
              gruppenKontext: null,
              identitaet: lauf.identitaet,
            },
            {
              type: 'delivery_receipt',
              delivered_ids: ids,
              receiver_id: lauf.eigeneId,
              timestamp: new Date().toISOString(),
            },
            crypto.randomUUID(),
          ).catch(() => null)
        }
      }

      // **Nach dem Commit.** Was hier quittiert wird, ist entschluesselt und
      // gespeichert; ein Fehler beim Quittieren kostet nur eine Wiederholung.
      //
      // **Nicht alles.** Ein Umschlag, den dieser Lauf nicht abschliessend
      // behandeln konnte (`offen`: keine Sitzung, Aufbau noch offen, fehlender
      // Gruppenschluessel, Ablagefehler), bleibt beim Server liegen. Bis 10/2026
      // wurde die ganze Gruppe quittiert — der Auftrag war damit weg, obwohl die
      // Nachricht im naechsten Lauf geoeffnet worden waere.
      const offen = new Set(ergebnis.offen ?? [])
      const ids = eintraege.filter((e) => !offen.has(-e.seq)).map((e) => e.delivery_id)
      if (ids.length > 0) {
        const antwort = await quittiereZustellungen(inbox, ids).catch(() => null)
        if (antwort) quittiert += antwort.quittiert
      }
    } catch {
      // Siehe oben: nichts geschrieben, nichts quittiert.
      continue
    }
  }

  return { verarbeitet, quittiert }
}
