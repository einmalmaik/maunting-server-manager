/**
 * Eine Wanderung mit Verfallsdatum: der alte Verlauf zieht einmal um.
 *
 * Bis zur Umstellung auf Gerätesitzungen hing die E2EE am **Konto**. Ein
 * RSA-Paar je Benutzer, der private Teil in der lokalen IndexedDB
 * (`msm_e2ee_keystore`, Store `keys`), und jede Direktnachricht lag als
 * `sv-e2ee-hybrid-v1:` in der Mailbox, versiegelt gegen genau dieses Paar.
 *
 * Seit 09/2026 gehört der Schlüssel dem Gerät, und der Kontoschlüssel wird
 * nirgends mehr angelegt. Damit wäre mit dem nächsten Aufräumen nicht nur der
 * unsichere Altbestand weg, sondern **der heute funktionierende DM-Verlauf** —
 * die Umschläge lägen weiter auf dem Server und niemand hätte mehr den
 * Schlüssel dazu. Also wird er einmal gelesen und als Klartext in den lokalen
 * Speicher geschrieben, der ohnehin die Hauptquelle des Verlaufs ist.
 *
 * **Warum der Schlüssel zuletzt stirbt.** Gelöscht wird erst, wenn jede
 * Mailbox durch ist. Ein Abbruch mittendrin — Netz weg, Tab zu — lässt den
 * Schlüssel liegen, und der nächste Start nimmt den Faden wieder auf. Ein
 * Löschen vorab spart eine Zeile und kostet im Fehlerfall den Verlauf.
 *
 * Dieses Modul ist Wegwerfware. Wenn absehbar kein Gerät mehr einen
 * Kontoschlüssel hat, kann es ersatzlos verschwinden; es hängt an keiner
 * anderen Stelle.
 */

import { fetchE2eeEnvelopes } from '@/api/social'
import {
  decryptE2eeHybrid,
  deleteLocalKeyPair,
  getLocalKeyPair,
} from './e2eeCrypto'
// Steuerpakete sind keine Nachrichten. Die Liste stand hier bis 09/2026 ein
// zweites Mal und lief auseinander, sobald der Messenger eine Paketart
// dazubekam: jedes unbekannte Paket landete beim Umzug als leere Zeile im
// Verlauf.
import { STEUERTYPEN } from './nachrichtBezug'
import {
  loadLocalMessages,
  mischeVerlauf,
  saveLocalMessages,
  sortMessagesChronologically,
  type LocalStoredMessage,
} from './messengerLocalStore'

const HYBRID_PREFIX = 'sv-e2ee-hybrid-v1:'
/** Nur ein Merker, kein Geheimnis: „dieses Konto ist auf diesem Gerät durch". */
const FLAGGE = 'msm_altbestand_v1_'
/** So viele Nachrichten je Mailbox behält der lokale Speicher ohnehin. */
const JE_MAILBOX = 200


export interface UebernahmeErgebnis {
  /** Ob der Kontoschlüssel danach weg ist. */
  abgeschlossen: boolean
  /** Wie viele Nachrichten insgesamt übernommen wurden. */
  uebernommen: number
  /** Mailboxen, die nicht vollständig gelesen werden konnten. */
  offen: number
}

function schonGelaufen(userId: number): boolean {
  try {
    return localStorage.getItem(FLAGGE + userId) === '1'
  } catch {
    return false
  }
}

function merkeLauf(userId: number): void {
  try {
    localStorage.setItem(FLAGGE + userId, '1')
  } catch {
    // Ohne localStorage läuft die Übernahme eben noch einmal. Sie ist idempotent.
  }
}

/**
 * Macht aus einem entschlüsselten Payload eine Verlaufszeile.
 *
 * `null` heißt „gehört nicht in den Verlauf": ein Steuerpaket, oder etwas, das
 * sich nicht als Nachricht lesen lässt. Der Altbestand kannte auch reinen Text
 * ohne JSON-Hülle, mit `[ME]:` als Absendermarke — den nimmt der zweite Zweig.
 */
function alsNachricht(
  plain: string,
  envId: number,
  createdAt: string,
  eigeneId: number,
  clientUuidAusUmschlag?: string,
): LocalStoredMessage | null {
  try {
    const parsed = JSON.parse(plain)
    if (!parsed || typeof parsed !== 'object') return null
    if (typeof parsed.type === 'string' && STEUERTYPEN.has(parsed.type)) return null

    const senderId = Number(parsed.sender_id) || 0
    return {
      id: envId,
      clientUuid: (parsed.client_uuid as string) || clientUuidAusUmschlag,
      senderId,
      senderName: parsed.sender_name || parsed.sender_username,
      text: typeof parsed.text === 'string' ? parsed.text : '',
      createdAt,
      isSelf: senderId === eigeneId,
      noteAttachment: parsed.note_attachment,
      calendarAttachment: parsed.calendar_attachment,
      imageAttachment: parsed.image_attachment,
      audioAttachment: parsed.audio_attachment,
      fileAttachment: parsed.file_attachment,
      stickerAttachment: parsed.sticker_attachment,
      storyReply: parsed.story_reply,
      videoNoteAttachment: parsed.video_note_attachment,
      status: 'sent',
    }
  } catch {
    if (!plain) return null
    const istSelbst = plain.startsWith('[ME]:')
    return {
      id: envId,
      clientUuid: clientUuidAusUmschlag,
      senderId: istSelbst ? eigeneId : 0,
      senderName: undefined,
      text: istSelbst ? plain.slice('[ME]:'.length) : plain,
      createdAt,
      isSelf: istSelbst,
      status: 'sent',
    }
  }
}

/**
 * Holt eine Mailbox herüber. Liefert die Zahl der übernommenen Nachrichten,
 * oder `null`, wenn die Mailbox gar nicht erst gelesen werden konnte.
 */
async function eineMailbox(
  blindMailboxId: string,
  altSchluessel: string,
  eigeneId: number,
): Promise<number | null> {
  let umschlaege: Awaited<ReturnType<typeof fetchE2eeEnvelopes>>
  try {
    umschlaege = await fetchE2eeEnvelopes(blindMailboxId)
  } catch {
    return null
  }

  const gelesen: LocalStoredMessage[] = []
  for (const env of umschlaege) {
    if (!env.ciphertext_envelope?.startsWith(HYBRID_PREFIX)) continue
    // Steuerumschläge sind als solche gekennzeichnet; der Rest fällt in
    // `alsNachricht` durch die Typprüfung.
    if ((env as { is_control?: boolean }).is_control) continue
    try {
      const plain = await decryptE2eeHybrid(env.ciphertext_envelope, altSchluessel)
      const zeile = alsNachricht(plain, env.id, env.created_at, eigeneId, env.client_uuid ?? undefined)
      if (zeile) gelesen.push(zeile)
    } catch {
      // Nicht mit diesem Schlüssel versiegelt. Kein Fehler: in derselben
      // Mailbox liegen auch Umschläge für andere Geräte und aus neuerer Zeit.
    }
  }

  if (gelesen.length === 0) return 0

  // Mischen, nicht ersetzen. Auf demselben Gerät kann längst neuer Verlauf
  // liegen, und der ist der aktuellere Stand.
  const vorhanden = await loadLocalMessages(blindMailboxId)
  const zusammen = vorhanden.length
    ? (mischeVerlauf(vorhanden, gelesen) as LocalStoredMessage[])
    : sortMessagesChronologically(gelesen)
  await saveLocalMessages(blindMailboxId, zusammen.slice(-JE_MAILBOX))
  return gelesen.length
}

/** Läuft nur einmal, auch wenn mehrere Aufrufer gleichzeitig fragen. */
let laufend: Promise<UebernahmeErgebnis> | null = null

/**
 * Holt den Verlauf aus der Zeit des Kontoschlüssels herüber.
 *
 * Der Aufrufer gibt die Mailboxen mit, weil nur er sie kennt: sie ergeben sich
 * aus der Kontaktliste, und die lädt der Messenger ohnehin. Ein leerer Aufruf
 * ist erlaubt und tut nichts.
 *
 * Wirft nie. Eine gescheiterte Übernahme darf den Messenger nicht aufhalten;
 * sie versucht es beim nächsten Start wieder.
 */
export async function uebernimmAltbestand(
  eigeneId: number,
  mailboxen: readonly string[],
): Promise<UebernahmeErgebnis> {
  if (laufend) return laufend
  laufend = (async () => {
    const nichts: UebernahmeErgebnis = { abgeschlossen: true, uebernommen: 0, offen: 0 }
    if (!eigeneId || schonGelaufen(eigeneId)) return nichts

    let altPaar: Awaited<ReturnType<typeof getLocalKeyPair>>
    try {
      altPaar = await getLocalKeyPair(eigeneId)
    } catch {
      return { abgeschlossen: false, uebernommen: 0, offen: mailboxen.length }
    }
    if (!altPaar?.privateKeyJwk) {
      // Nichts zu holen — dieses Gerät hatte nie einen Kontoschlüssel.
      merkeLauf(eigeneId)
      return nichts
    }

    let uebernommen = 0
    let offen = 0
    for (const mid of mailboxen) {
      if (!mid) continue
      const anzahl = await eineMailbox(mid, altPaar.privateKeyJwk, eigeneId)
      if (anzahl === null) offen++
      else uebernommen += anzahl
    }

    if (offen > 0) {
      // Der Schlüssel bleibt liegen. Ohne ihn wäre der Rest für immer weg.
      return { abgeschlossen: false, uebernommen, offen }
    }

    await deleteLocalKeyPair(eigeneId)
    merkeLauf(eigeneId)
    return { abgeschlossen: true, uebernommen, offen: 0 }
  })()

  try {
    return await laufend
  } catch {
    return { abgeschlossen: false, uebernommen: 0, offen: mailboxen.length }
  } finally {
    laufend = null
  }
}

/** Nur für Tests: den Einmal-Merker zurücksetzen. */
export function setzeUebernahmeZurueckFuerTest(eigeneId: number): void {
  try {
    localStorage.removeItem(FLAGGE + eigeneId)
  } catch {}
  laufend = null
}
