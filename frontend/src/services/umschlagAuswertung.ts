/**
 * Was die gelesenen Umschläge eines Gesprächs bedeuten.
 *
 * `useKonversation` entschlüsselt, hier wird ausgewertet: wer eine Nachricht
 * geschrieben hat, ob er das durfte, und welche Steuerpakete (Quittungen,
 * Bearbeiten, Löschen, Reaktionen, Verfallsfrist, Anheften) wirken. Jede
 * Absenderprüfung des Empfängers sitzt in dieser Datei.
 *
 * Bis 09/2026 stand das als Schleife mitten in `loadMessages` in
 * `Messenger.tsx` und war nur über die ganze Seite zu testen. Der Körper ist
 * beim Umzug wortgleich geblieben; die Namen im Kontext sind die der Seite.
 *
 * Vier Dinge wirken nach aussen und kommen deshalb als Rückruf herein. Sie
 * laufen **sofort** im Durchlauf, nicht am Ende: `loadMessages` bricht danach
 * ab, wenn inzwischen ein zweiter Abruf läuft, und `uebernehmeVerfall` meldet
 * dieselbe Umstellung kein zweites Mal.
 */

import type { ChatMessage } from '@/components/social/ChatMessageBubble'
import type { ChatGroupItem } from '@/api/social'
import type { Lesung } from '@/hooks/useKonversation'
import { kontoNutztSignaturen } from './e2eeGeraet'
import {
  istSteuerpaket,
  neueBezugstafel,
  neueSammeltafel,
  type Bezugstafel,
  type Sammeltafel,
} from './nachrichtBezug'
import { leseAugenblickMarke, leseRettungsMarke } from './funkenService'
import { pruefeNutzlast } from './nutzlastSignatur'
import { logischeUuid } from './ratchetSitzung'
import type { RohReaktion } from './reaktionen'
import { verarbeiteAnheftung } from './auswertung/anheftung'
import { verarbeiteBearbeitung } from './auswertung/bearbeitung'
import { verarbeiteLoeschung } from './auswertung/loeschung'
import { verarbeiteQuittung } from './auswertung/quittung'
import { verarbeiteReaktion } from './auswertung/reaktion'
import { verarbeiteVerfall } from './auswertung/verfall'

export interface Auswertungskontext {
  currentMid: string
  currentUserId: number
  /** Im Direktchat die Gegenseite, sonst `null`. */
  activeContact: { userId: number; username: string } | null
  /** In der Gruppe die Gruppe, sonst `null`. */
  activeGroup: ChatGroupItem | null
  /** Was ein Konto in der offenen Gruppe darf; aus `wirksameGruppenrechte`. */
  gruppenrechteVon: (konto: number) => Set<string>
  t: (schluessel: string, werte?: Record<string, unknown>) => string
  /** Die Gegenseite hat bis zu dieser Umschlagkennung gelesen. */
  quittiertGelesen: (bis: number) => void
  /** Die Gegenseite hat bis zu dieser Umschlagkennung empfangen. */
  quittiertZugestellt: (bis: number) => void
  /** Ein eigenes anderes Gerät hat diese Mailbox gelesen. */
  markAsRead: (mailbox: string) => void
  setVerfallSekunden: (sekunden: number) => void
  zeigeSystemzeile: (text: string) => void
}

export interface Auswertung {
  decryptedList: ChatMessage[]
  aenderungen: Bezugstafel<{ newText: string; editedAt: string }>
  loeschungen: Bezugstafel<{ deletedAt: string }>
  reaktionen: Sammeltafel<RohReaktion>
  maxPartnerReadId: number
  maxPartnerDeliveredId: number
  maxIncomingId: number
}

export async function werteUmschlaegeAus(
  gelesen: Lesung[],
  kontext: Auswertungskontext,
): Promise<Auswertung> {
  const {
    currentMid,
    currentUserId,
    activeContact,
    activeGroup,
    gruppenrechteVon,
    t,
    quittiertGelesen,
    quittiertZugestellt,
    markAsRead,
    setVerfallSekunden,
    zeigeSystemzeile,
  } = kontext

  const decryptedList: ChatMessage[] = []
  const seenEnvelopeIds = new Set<number>()
  const seenClientUuids = new Set<string>()

  // Wirkungen, die aus Steuerumschlägen kommen. Jede Tafel kennt ihre
  // Nachricht über die Umschlagkennung **und** die logische Kennung; warum
  // beides nötig ist, steht in `nachrichtBezug.ts`.
  const aenderungen = neueBezugstafel<{ newText: string; editedAt: string }>()
  const loeschungen = neueBezugstafel<{ deletedAt: string }>()
  const reaktionen = neueSammeltafel<RohReaktion>()
  const quittungszustand = { maxPartnerReadId: 0, maxPartnerDeliveredId: 0 }
  let maxIncomingId = 0

  for (const lesung of gelesen) {
    const env = lesung.env
    if (seenEnvelopeIds.has(env.id)) continue
    seenEnvelopeIds.add(env.id)
    // 'still' — eine Kopie für ein anderes Gerät, die eigene
    // Ratchet-Nachricht oder eine Schlüsselzustellung. Nichts davon ist ein
    // Fehler, und nichts davon darf als „Verschlüsselte Nachricht" im
    // Verlauf stehen.
    if (lesung.art === 'still') continue
    const plain = lesung.art === 'klartext' ? lesung.text : ''

    if (!plain) {
      const isControl =
        Boolean((env as any).is_control) ||
        Boolean((env as any).control_type) ||
        Boolean(
          env.client_uuid &&
            (env.client_uuid.startsWith('receipt:') ||
              env.client_uuid.startsWith('ctrl-') ||
              env.client_uuid.startsWith('deliv-') ||
              env.client_uuid.toLowerCase().includes('control') ||
              env.client_uuid.toLowerCase().includes('receipt'))
        )

      if (isControl) {
        // Silence un-decryptable control envelopes; never render in chat timeline
        continue
      }

      const senderId = activeContact ? activeContact.userId : 0
      const clientUuid = logischeUuid(env.client_uuid)
      const dedupKey = `${senderId}:${clientUuid}`
      if (clientUuid && seenClientUuids.has(dedupKey)) {
        continue
      }
      if (clientUuid) {
        seenClientUuids.add(dedupKey)
      }
      decryptedList.push({
        id: env.id,
        clientUuid,
        senderId,
        text: t('messenger.encryptedMessage'),
        createdAt: env.created_at,
        isSelf: false,
      })
      if (env.id > maxIncomingId) {
        maxIncomingId = env.id
      }
      continue
    }

    /*
     * Nur das Lesen steht im `try`. Bis 09/2026 umschloss es auch die ganze
     * Absenderprüfung. Warf sie (eine kaputte Unterschrift genügte), landete
     * die Nutzlast im Zweig für Altklartext und stand in einer Gruppe als
     * roher JSON-Text im Verlauf, ohne jede Absenderprüfung.
     */
    let parsed: any
    try {
      parsed = JSON.parse(plain)
    } catch {
      parsed = undefined
    }

    if (typeof parsed === 'object' && parsed !== null) {
      try {
        /*
         * Wer das hier geschrieben hat — einmal beantwortet, für alles was
         * folgt.
         *
         * Zwei Belege können vorliegen: die Ratchet-Sitzung (nur im
         * Direktchat, nur für Nachrichten) und die Nutzlastsignatur
         * (überall, auch für Steuerpakete). Widersprechen sie einander,
         * hat jemand an einer von beiden gedreht.
         *
         * `belegterUrheber` bleibt `undefined`, wenn keiner der beiden
         * greift. Das ist kein Freibrief: jede Auswertung unten prüft
         * zusätzlich, ob das behauptete Konto beglaubigen *könnte* — wer
         * es kann, muss es auch.
         */
        const beleg = await pruefeNutzlast(currentMid, parsed as Record<string, unknown>)
        if (beleg.art === 'gefaelscht') {
          console.warn(
            '[Messenger] Dropping payload with invalid sender signature, claimed:',
            beleg.behauptet,
          )
          continue
        }
        const ratchetUrheber = lesung.art === 'klartext' ? lesung.vonKonto : undefined
        if (
          beleg.art === 'geprueft' &&
          ratchetUrheber !== undefined &&
          Number(ratchetUrheber) !== beleg.vonKonto
        ) {
          console.warn(
            '[Messenger] Dropping payload: signature and ratchet disagree on sender',
          )
          continue
        }
        // Die Downgrade-Schranke gilt auch für den Ratchet. Eine Sitzung aus
        // der Zeit vor der Unterschrift am Sitzungsaufbau (09/2026) wurde nie
        // geprüft — auch eine untergeschobene nicht, und die liefe weiter.
        // Wer unterschreiben kann, unterschreibt jede Nachricht; fehlt der
        // Beleg trotzdem, schreibt jemand anderes über diese Sitzung.
        if (
          beleg.art === 'unsigniert' &&
          ratchetUrheber !== undefined &&
          (await kontoNutztSignaturen(Number(ratchetUrheber)))
        ) {
          console.warn(
            '[Messenger] Dropping unsigned ratchet payload from an account that signs:',
            ratchetUrheber,
          )
          continue
        }
        const belegterUrheber =
          beleg.art === 'geprueft' ? beleg.vonKonto : ratchetUrheber

        /**
         * Der Urheber, gegen eine Behauptung aus der Nutzlast geprüft.
         *
         * `null` heißt verwerfen: entweder widerspricht die Behauptung dem
         * Beleg, oder sie nennt ein Konto, das beglaubigen könnte und es
         * hier nicht tut — das wäre der Weg, die Prüfung einfach
         * wegzulassen.
         */
        const urheberVon = async (
          behauptetRoh: unknown,
        ): Promise<number | undefined | null> => {
          const behauptet =
            behauptetRoh === undefined || behauptetRoh === null
              ? undefined
              : Number(behauptetRoh)
          if (belegterUrheber !== undefined) {
            if (behauptet !== undefined && behauptet !== Number(belegterUrheber)) return null
            return Number(belegterUrheber)
          }
          if (behauptet !== undefined && behauptet > 0) {
            if (await kontoNutztSignaturen(behauptet)) return null
          }
          return behauptet
        }

        if (
          verarbeiteQuittung(
            parsed,
            env,
            currentMid,
            currentUserId,
            quittungszustand,
            quittiertGelesen,
            quittiertZugestellt,
            markAsRead,
          )
        ) {
          continue
        }

        if (await verarbeiteBearbeitung(parsed, env, urheberVon, aenderungen)) {
          continue
        }

        if (await verarbeiteLoeschung(parsed, env, urheberVon, loeschungen)) {
          continue
        }

        if (await verarbeiteReaktion(parsed, env, urheberVon, reaktionen)) {
          continue
        }

        if (
          await verarbeiteVerfall(parsed, env, urheberVon, {
            currentMid,
            currentUserId,
            activeContact,
            activeGroup,
            t,
            setVerfallSekunden,
            zeigeSystemzeile,
          })
        ) {
          continue
        }

        if (await verarbeiteAnheftung(parsed, env, urheberVon, activeGroup, currentMid)) {
          continue
        }

        /**
         * Ein Steuerpaket, für das dieser Stand keinen Zweig hat.
         *
         * Etwa von einem neueren Client. Ohne diese Schranke fiele es in
         * den gewöhnlichen Weg und stünde als roher JSON-Text im Verlauf —
         * und weil der Verlauf gespeichert wird, für immer. Genau so eine
         * Zeile lag nach der ersten Laufzeitprobe im Testchat.
         */
        if (istSteuerpaket(parsed.type)) continue

        /*
         * Direktchat ohne jeden Beleg: keine Unterschrift und kein Ratchet.
         *
         * So kommt ein Hybridumschlag an, und versiegeln kann den jeder,
         * der den Geräteschlüssel dieses Geräts kennt — der Server
         * allemal. Bis 09/2026 stand er trotzdem als Nachricht der
         * Gegenseite im Verlauf. Gilt nur noch, solange die Gegenseite
         * nicht unterschreiben kann; dieselbe Schranke wie oben für den
         * Ratchet. Vor der Kennungsliste, damit eine verworfene Fälschung
         * der echten Nachricht mit derselben Kennung nicht den Platz nimmt.
         */
        if (
          activeContact &&
          belegterUrheber === undefined &&
          (await kontoNutztSignaturen(Number(activeContact.userId)))
        ) {
          console.warn(
            '[Messenger] Dropping unsigned direct message without a ratchet sender:',
            activeContact.userId,
          )
          continue
        }

        // Normal Chat Message
        // Die Kennung aus dem Umschlag trägt einen Gerätezusatz je Kopie;
        // für den Verlauf zählt die logische darunter.
        const clientUuid = (parsed.client_uuid as string) || logischeUuid(env.client_uuid)

        let senderId: number
        let senderName: string
        let isSelf: boolean
        /** Im Direktchat immer; in der Gruppe entscheidet `attach_media`. */
        let anhaengeErlaubt = true

        if (activeContact) {
          /*
           * Direktchat: die Kennung kommt aus dem Beleg — dem Ratchet oder
           * der Nutzlastsignatur —, nie aus `parsed.sender_id`.
           *
           * Fehlt jeder Beleg, ist die Gegenseite die einzig mögliche
           * Antwort: in dieser Mailbox sitzen genau zwei Menschen, und der
           * eigene Gesprächsanteil kommt aus dem lokalen Speicher, nicht
           * von hier. Die Behauptung aus der Nutzlast gewinnt also in
           * keinem der Fälle.
           */
          senderId = Number(belegterUrheber ?? activeContact.userId)
          if (parsed.sender_id !== undefined && Number(parsed.sender_id) !== senderId) {
            console.warn(
              '[Messenger] Dropping message with forged sender_id in direct chat:',
              parsed.sender_id,
              'expected:',
              senderId,
            )
            continue
          }
          isSelf = Number(senderId) === Number(currentUserId)
          senderName = isSelf ? t('messenger.you') : activeContact.username
        } else if (activeGroup) {
          /*
           * Gruppe: der Absender steht in der Nutzlastsignatur.
           *
           * Ein Gruppenschlüssel ist geteilt — jedes Mitglied kann jede
           * Nachricht der Gruppe erzeugen. `parsed.sender_id` war deshalb
           * nie eine Auskunft, sondern eine Behauptung. Anders als im
           * Direktchat gibt es hier auch keinen Rückfall: „aus dieser
           * Gruppe" sagt nichts darüber, von wem.
           *
           * Bleibt der Urheber unbelegt, weil das sendende Gerät die
           * Signatur noch nicht kennt, gilt die Zeile weiterhin — aber nur
           * solange das behauptete Konto nirgends einen Signaturschlüssel
           * führt. Diese Prüfung steckt in `urheberVon`.
           */
          const urheber = await urheberVon(parsed.sender_id)
          if (urheber === null) {
            console.warn(
              '[Messenger] Dropping group message with forged sender_id:',
              parsed.sender_id,
            )
            continue
          }
          senderId = Number(urheber ?? 0)

          isSelf = Number(senderId) === Number(currentUserId)

          /**
           * Durfte dieses Konto hier überhaupt schreiben?
           *
           * Hier sitzt die Durchsetzung von `send_messages` und
           * `attach_media` — nicht am Eingabefeld. Ein verändertes Programm
           * schickt trotzdem; dass es niemand **anzeigt**, ist die
           * Wirkung. Dieselbe Bauart wie bei `@everyone` und beim
           * Anheften.
           *
           * Zwei Feinheiten, die leicht verloren gehen:
           *
           * - Geprüft wird nur bei **aktuellen** Mitgliedern. Wer die
           *   Gruppe verlassen hat oder hinausgeworfen wurde, steht in
           *   keiner Rolle mehr; seine alten Nachrichten deshalb
           *   nachträglich verschwinden zu lassen, wäre Geschichtsfälschung
           *   — er durfte, als er schrieb.
           * - Verworfen wird beim **ersten Sehen**. Eine Nachricht, die
           *   schon in der Ablage steht, bleibt: `mischeVerlauf` behält
           *   lokale Zeilen. Sonst löschte das Stummschalten rückwirkend
           *   alles, was noch im Hundert-Umschläge-Fenster liegt.
           */
          const istMitglied = Boolean(
            activeGroup.members?.some((m) => Number(m.user_id) === Number(senderId)),
          )
          const senderrechte = gruppenrechteVon(senderId)
          if (!isSelf && istMitglied && !senderrechte.has('send_messages')) {
            console.warn(
              '[Messenger] Gruppennachricht verworfen, Absender darf nicht schreiben:',
              senderId,
            )
            continue
          }
          // Ein Anhang ohne das Recht dazu fällt weg, der Text bleibt: die
          // Nachricht ganz zu verwerfen nähme jemandem seine Worte wegen
          // eines Bildes.
          anhaengeErlaubt = isSelf || !istMitglied || senderrechte.has('attach_media')
          // Der Anzeigename kommt aus der Mitgliederliste, nie aus der
          // Nutzlast: sonst stünde unter der richtigen Kennung ein
          // fremder Name.
          const groupMember = activeGroup.members?.find((m) => Number(m.user_id) === Number(senderId))
          senderName = isSelf
            ? t('messenger.you')
            : (groupMember?.username || parsed.sender_name || parsed.sender_username || '')
        } else {
          senderId = Number(parsed.sender_id || 0)
          isSelf = Number(senderId) === Number(currentUserId)
          senderName = parsed.sender_name || parsed.sender_username || ''
        }

        const dedupKey = `${senderId}:${clientUuid}`
        if (clientUuid && seenClientUuids.has(dedupKey)) {
          continue
        }
        if (clientUuid) {
          seenClientUuids.add(dedupKey)
        }

        if (!isSelf && env.id > maxIncomingId) {
          maxIncomingId = env.id
        }

        decryptedList.push({
          id: env.id,
          clientUuid,
          senderId,
          senderName,
          text: parsed.text || '',
          createdAt: env.created_at,
          isSelf,
          noteAttachment:
            anhaengeErlaubt && parsed.note_attachment && typeof parsed.note_attachment === 'object'
              ? {
                  title: String(parsed.note_attachment.title ?? ''),
                  content: String(parsed.note_attachment.content ?? ''),
                  color: parsed.note_attachment.color ? String(parsed.note_attachment.color) : undefined,
                  category: parsed.note_attachment.category ? String(parsed.note_attachment.category) : undefined,
                }
              : undefined,
          calendarAttachment:
            anhaengeErlaubt && parsed.calendar_attachment && typeof parsed.calendar_attachment === 'object'
              ? {
                  title: String(parsed.calendar_attachment.title ?? ''),
                  start: String(parsed.calendar_attachment.start ?? ''),
                  end: String(parsed.calendar_attachment.end ?? ''),
                  description: parsed.calendar_attachment.description ? String(parsed.calendar_attachment.description) : undefined,
                  location: parsed.calendar_attachment.location ? String(parsed.calendar_attachment.location) : undefined,
                }
              : undefined,
          imageAttachment: anhaengeErlaubt ? parsed.image_attachment : undefined,
          audioAttachment: anhaengeErlaubt ? parsed.audio_attachment : undefined,
          fileAttachment: anhaengeErlaubt ? parsed.file_attachment : undefined,
          stickerAttachment: anhaengeErlaubt ? parsed.sticker_attachment : undefined,
          storyReply: anhaengeErlaubt ? parsed.story_reply : undefined,
          videoNoteAttachment: anhaengeErlaubt ? parsed.video_note_attachment : undefined,
          antwortAuf:
            parsed.antwort_auf && typeof parsed.antwort_auf === 'object'
              ? {
                  clientUuid: String(parsed.antwort_auf.clientUuid ?? parsed.antwort_auf.client_uuid ?? ''),
                  absenderId: Number(parsed.antwort_auf.absenderId ?? parsed.antwort_auf.absender_id ?? 0),
                  absenderName: parsed.antwort_auf.absenderName || parsed.antwort_auf.absender_name ? String(parsed.antwort_auf.absenderName ?? parsed.antwort_auf.absender_name) : undefined,
                  auszug:
                    typeof parsed.antwort_auf.auszug === 'string'
                      ? parsed.antwort_auf.auszug
                      : typeof parsed.antwort_auf.auszug === 'object' && parsed.antwort_auf.auszug !== null
                        ? JSON.stringify(parsed.antwort_auf.auszug)
                        : String(parsed.antwort_auf.auszug ?? ''),
                }
              : undefined,
          weitergeleitet: Boolean(parsed.weitergeleitet) || undefined,
          erwaehnungen: Array.isArray(parsed.erwaehnungen) ? parsed.erwaehnungen : undefined,
          // Ob daraus eine Erwähnung wird, entscheidet nicht dieses Feld,
          // sondern die Rechtelage des Absenders — geprüft beim Anzeigen.
          erwaehntAlle: Boolean(parsed.erwaehnt_alle) || undefined,
          verfaelltAm: typeof parsed.verfaellt_am === 'string' ? parsed.verfaellt_am : undefined,
          // Funken gibt es nur im Direktchat. In einer Gruppe bleibt ein
          // Augenblick ein gewöhnliches Foto.
          augenblick: activeContact && anhaengeErlaubt ? leseAugenblickMarke(parsed.augenblick) : undefined,
          funkenRettung: activeContact ? leseRettungsMarke(parsed.funken_rettung) : undefined,
        })
        continue
      } catch (err) {
        // Eine Prüfung, die nicht zu Ende kommt, ist keine bestandene. Der
        // Umschlag wird beim nächsten Abruf erneut ausgewertet.
        console.warn(
          '[Messenger] Dropping payload, sender check failed:',
          err instanceof Error ? err.name : typeof err,
        )
        continue
      }
    }

    /*
     * Klartext ohne JSON-Hülle, aus der Zeit vor der Hülle.
     *
     * Er trägt keine Unterschrift. Wer ihn geschrieben hat, sagt im
     * Direktchat der Ratchet; ohne ihn bleibt nur die Behauptung —
     * `[ME]:` am Anfang, sonst die Gegenseite. Bis 09/2026 galt die
     * Behauptung auch über den Ratchet: eine Nachricht der Gegenseite
     * mit `[ME]:` stand als eigene im Verlauf. Und wie jeder Beleg gilt
     * sie nur für ein Konto, das nicht unterschreiben kann — wer es
     * kann, schickt JSON mit Unterschrift.
     */
    const ratchetUrheber = lesung.art === 'klartext' ? lesung.vonKonto : undefined
    const vonMirBehauptet = plain.startsWith('[ME]:')
    const urheber =
      ratchetUrheber !== undefined
        ? Number(ratchetUrheber)
        : vonMirBehauptet
          ? Number(currentUserId)
          : activeContact
            ? Number(activeContact.userId)
            : 0
    if (urheber > 0 && (await kontoNutztSignaturen(urheber))) {
      console.warn('[Messenger] Dropping unsigned plain-text message from an account that signs:', urheber)
      continue
    }

    const senderId = urheber || (activeContact ? activeContact.userId : 0)
    const clientUuid = logischeUuid(env.client_uuid)
    const dedupKey = `${senderId}:${clientUuid}`
    if (clientUuid && seenClientUuids.has(dedupKey)) {
      continue
    }
    if (clientUuid) {
      seenClientUuids.add(dedupKey)
    }

    const isSelf = urheber === Number(currentUserId)
    // Die Markierung fällt nur weg, wo sie stimmt. Nennt der Ratchet die
    // Gegenseite, bleibt sie stehen: so sieht man, was behauptet wurde.
    const text = isSelf && vonMirBehauptet ? plain.slice('[ME]:'.length) : plain
    if (!isSelf && env.id > maxIncomingId) {
      maxIncomingId = env.id
    }
    decryptedList.push({
      id: env.id,
      clientUuid,
      senderId,
      text,
      createdAt: env.created_at,
      isSelf,
    })
  }

  return {
    decryptedList,
    aenderungen,
    loeschungen,
    reaktionen,
    maxPartnerReadId: quittungszustand.maxPartnerReadId,
    maxPartnerDeliveredId: quittungszustand.maxPartnerDeliveredId,
    maxIncomingId,
  }
}
