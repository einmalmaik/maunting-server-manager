/**
 * Was aus dem lokalen Verlauf in eine Übergabe gehört — und wie er geteilt wird.
 *
 * Beide Wege (`geraet/verlaufsAbgleich` über die Geräte-Mailbox und
 * `verlaufsUebergabe` über den Kopplungscode) müssen dieselben Zeilen
 * auslassen: Abgelaufenes, was nie hinausging, den alten Platzhalter und den
 * Inhalt eines Grabsteins. Bis 10/2026 filterte jeder Weg das für sich, und
 * der Kopplungsweg ließ dabei liegen, was der Geräteweg längst aussortierte.
 *
 * **Bewusst getrennt bleibt das Packen.** Der Geräteweg teilt in feste,
 * wiederholbare Teile (200 Nachrichten, höchstens 180 KB Klartext), damit ein
 * verlorener Teil unter derselben Kennung noch einmal gehen kann. Der
 * Erstabgleich über den Kopplungscode hat einen anderen Deckel (25 MB Blob),
 * keine Wiederaufnahme und trägt den Notizschlüssel mit; er wählt darum weiter
 * seine eigenen Stufen. Die gemeinsame Auswahl ist die Schnittmenge, nicht
 * eine Angleichung der Sicherheitsmodelle.
 */

import {
  istVerschluesselterPlatzhalter,
  sortMessagesChronologically,
  verlaufsSchluessel,
  type LocalStoredMessage,
  type VerlaufsArt,
} from '../messengerLocalStore'

/** Wie viele Nachrichten ein Geräteteil höchstens trägt. */
export const VERLAUFS_CHUNK = 200

/** Eine einzelne Nachricht über dieser Grenze ist ein Anhang, kein Verlauf. */
export const MAX_NACHRICHT_BYTES = 40_000

/**
 * Wie viel Klartext ein Geräteteil höchstens tragen darf.
 *
 * Der Umschlag fasst 1 MiB Zeichen; Base64, AES-GCM und die äußere Hülle
 * kosten etwa das Vierfache. 180 KB Klartext bleiben mit Sicherheit darunter.
 */
export const MAX_KLARTEXT_BYTES = 180_000

/** Wie viele Teile eine Mailbox höchstens bekommt (200 000 Nachrichten). */
export const MAX_TEILE = 1000

/**
 * `geraet` ist der Weg über die Geräte-Mailbox: Anhänge reisen als eigene
 * Pakete, hier bleibt nur der Verweis. `erstabgleich` ist der einmalige Blob
 * über den Kopplungscode: er hat keinen Mediennachlauf, die eingebetteten
 * Daten müssen mit.
 */
export type AuswahlModus = 'geraet' | 'erstabgleich'

function byteLaenge(text: string): number {
  return new TextEncoder().encode(text).length
}

/**
 * Eine Zeile, die übergeben werden darf — oder `null`.
 *
 * Die Prüfungen an Form und Inhalt sind für beide Wege dieselben. Nur was mit
 * den Anhangsdaten geschieht, entscheidet der Modus: der Geräteweg streicht
 * `dataUrl`, weil die Datei als eigenes Paket folgt; der Erstabgleich behält
 * sie, weil es dort keinen Nachlauf gibt.
 */
export function kompakteNachricht(
  nachricht: LocalStoredMessage,
  modus: AuswahlModus = 'geraet',
): LocalStoredMessage | null {
  if (!nachricht || typeof nachricht !== 'object') return null
  if (typeof nachricht.text !== 'string') return null
  if (!Number.isFinite(Number(nachricht.id)) || !Number.isFinite(Number(nachricht.senderId))) {
    return null
  }
  if (typeof nachricht.createdAt !== 'string') return null
  if (typeof nachricht.isSelf !== 'boolean') return null
  // Was nie hinausging, gehört auch nicht in den Verlauf eines anderen
  // Geräts: es stünde dort für immer auf „wird gesendet“. Das gilt nur für
  // **eigene** Zeilen: eine empfangene Nachricht aus dem Gerätepostfach trägt
  // lokal ebenfalls `queued` (ihre Kennung ist negativ), ist aber längst da.
  // Bis 23.10.2026 fiel sie damit aus jeder Verlaufsübergabe.
  if (nachricht.isSelf && (nachricht.status === 'queued' || nachricht.status === 'failed')) {
    return null
  }
  // Abgelaufenes wird weder exportiert noch importiert. Auf dem Quellgerät
  // fehlt es meist schon; ein altes Paket darf es nicht zurückbringen.
  if (typeof nachricht.verfaelltAm === 'string') {
    const ablauf = Date.parse(nachricht.verfaelltAm)
    if (Number.isFinite(ablauf) && ablauf <= Date.now()) return null
  }

  // Der Platzhalter eines früheren Standes ist keine Nachricht, sondern die
  // Aussage „konnte ich nicht lesen". Er darf nicht im Verlauf eines anderen
  // Geräts landen — dort stünde er für immer neben dem echten Text.
  if (istVerschluesselterPlatzhalter(nachricht)) return null

  const kopie: LocalStoredMessage = { ...nachricht }
  delete kopie.isSystem
  // Gelöscht bleibt gelöscht. Der Grabstein darf mitreisen, sein Inhalt nicht.
  if (kopie.isDeleted) {
    kopie.text = ''
    delete kopie.originalText
  }
  const alsFelder = kopie as unknown as Record<string, unknown>
  for (const feld of [
    'imageAttachment',
    'fileAttachment',
    'audioAttachment',
    'videoNoteAttachment',
  ] as const) {
    const anhang = alsFelder[feld]
    if (anhang && typeof anhang === 'object') {
      if (modus === 'geraet') {
        const ohneDaten = { ...(anhang as Record<string, unknown>) }
        delete ohneDaten.dataUrl
        alsFelder[feld] = ohneDaten
      }
    }
  }
  if (kopie.isDeleted) {
    delete alsFelder.imageAttachment
    delete alsFelder.fileAttachment
    delete alsFelder.audioAttachment
    delete alsFelder.videoNoteAttachment
    delete alsFelder.noteAttachment
    delete alsFelder.calendarAttachment
    delete alsFelder.stickerAttachment
    delete alsFelder.storyReply
  }
  // Ein übergroßes Einzelstück ist ein Anhang, kein Verlauf. Für den
  // Erstabgleich entscheidet der Blob-Deckel, nicht diese Grenze.
  if (modus === 'geraet' && byteLaenge(JSON.stringify(kopie)) > MAX_NACHRICHT_BYTES) return null
  return kopie
}

/**
 * Sortiert, filtert und legt dieselbe logische Nachricht einmal zusammen.
 *
 * Ohne das Zusammenlegen reiste der Platzhalter neben der echten Zeile und
 * derselbe Umschlag zweimal ins Paket; auf dem Ziel wurden daraus zwei Zeilen.
 */
export function verlaufsAuswahl(
  nachrichten: readonly LocalStoredMessage[],
  art: VerlaufsArt = 'direkt',
  modus: AuswahlModus = 'geraet',
): LocalStoredMessage[] {
  const brauchbar = sortMessagesChronologically([...nachrichten])
    .map((m) => kompakteNachricht(m, modus))
    .filter((m): m is LocalStoredMessage => m !== null)
  const jeSchluessel = new Map<string, LocalStoredMessage>()
  for (const m of brauchbar) {
    const k = verlaufsSchluessel(m, art)
    if (!jeSchluessel.has(k)) jeSchluessel.set(k, m)
  }
  return [...jeSchluessel.values()]
}

/**
 * Die Teile eines Verlaufs, so wie sie später wieder gefunden werden.
 *
 * Geplant wird **vor** der Verschlüsselung und nur am Klartext. Damit hängt
 * die Teilgrenze nicht an einem Zufallswert der Verschlüsselung: ein zweiter
 * Lauf mit unverändertem Bestand schneidet dieselben Teile, und ein
 * verlorener Teil kann unter derselben Kennung noch einmal gehen.
 */
export function planeVerlaufsTeile(
  auswahl: readonly LocalStoredMessage[],
  umfeld: { mailbox: string; art: VerlaufsArt; dmGeheimnis?: unknown },
): LocalStoredMessage[][] {
  const teile: LocalStoredMessage[][] = []
  let start = 0
  while (start < auswahl.length) {
    let anzahl = Math.min(VERLAUFS_CHUNK, auswahl.length - start)
    while (anzahl > 1) {
      const probe = auswahl.slice(start, start + anzahl)
      const klartext = JSON.stringify({
        v: 1,
        mailbox: umfeld.mailbox,
        art: umfeld.art,
        ...(teile.length === 0 && umfeld.dmGeheimnis
          ? { dmGeheimnis: umfeld.dmGeheimnis }
          : {}),
        nachrichten: probe,
      })
      if (byteLaenge(klartext) <= MAX_KLARTEXT_BYTES) break
      anzahl = Math.max(1, Math.floor(anzahl / 2))
    }
    teile.push(auswahl.slice(start, start + anzahl))
    start += anzahl
  }
  return teile
}
