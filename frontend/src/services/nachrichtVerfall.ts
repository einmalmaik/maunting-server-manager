/**
 * Verschwindende Nachrichten.
 *
 * Erst jetzt ehrlich machbar, weil Löschen seit 09/2026 wirklich löscht:
 * Umschläge, hochgeladene Anhänge und die lokale Zeile. Vorher hätte
 * „verschwindet nach sieben Tagen" geheißen „wird nach sieben Tagen
 * ausgeblendet", und das ist etwas anderes.
 *
 * ## Die Frist gilt für beide Seiten
 *
 * Wer sie umstellt, schickt einen `retention`-Umschlag. Die Gegenseite
 * übernimmt ihn, schreibt ihn in ihre eigene Ablage und zeigt eine Systemzeile
 * im Verlauf. Ab da hängen auch **ihre** Nachrichten ein `verfaellt_am` an, und
 * jedes Gerät beider Seiten tilgt sie, sobald die Zeit um ist. Eine Frist, die
 * nur beim Einstellenden wirkte, wäre eine Lüge über das, was beim Gegenüber
 * passiert.
 *
 * Beide dürfen umstellen, und **die letzte Umstellung gilt** — deshalb reist
 * ein Zeitpunkt mit und liegt neben der Frist. Ohne ihn wäre die Sache kaputt:
 * die Mailbox liefert dieselben 100 Umschläge bei jedem Abruf erneut, eine
 * eigene Abschaltung würde also beim nächsten Abruf vom alten Einschalten der
 * Gegenseite wieder überrollt.
 *
 * ## In der Gruppe braucht es ein Recht
 *
 * Bis 09/2026 stellte jedes Mitglied die Frist für die ganze Gruppe, und der
 * Empfänger glaubte obendrein der `actor_id` im Paket: jedes Mitglied konnte
 * bei allen „<Eigentümer> hat eingestellt …" erscheinen lassen. Seitdem prüft
 * der Lesepfad zuerst den belegten Urheber (`urheberVon`) und dann dessen Recht
 * `set_disappearing_messages` (`durfteVerfallStellen`). Wie beim Anheften kann
 * der Server das nicht: er liest die Umstellung nie.
 *
 * Die Reihenfolge zählt. Eine verworfene Umstellung darf `uebernehmeVerfall`
 * nie erreichen — ihr Zeitpunkt stünde sonst als neuester Stand in der Ablage,
 * und jede spätere berechtigte Umstellung verlöre gegen ihn. Im Direktchat
 * gibt es keine Rollen; dort dürfen weiterhin beide.
 *
 * ## Wer was wegräumt
 *
 * Jede Seite tilgt abgelaufene Zeilen bei sich lokal und nimmt **ihre eigenen**
 * Umschläge und Anhänge beim Server weg. Zusammen ist damit nach Ablauf auf
 * beiden Seiten und auf dem Server nichts mehr da.
 *
 * ## Zwei Grenzen, die klar dastehen müssen
 *
 * Beim Server löschen kann nur, wer hochgeladen hat. Kommt ein Gerät nie wieder
 * online, bleibt sein Chiffretext in der Mailbox liegen, auch wenn die Nachricht
 * überall sonst verschwunden ist. Das steht so in der Datenschutzerklärung und
 * im Einstellungstext, nicht in einer Fußnote.
 *
 * Und: die Umstellung erreicht alle Geräte der **Gegenseite**, aber nicht die
 * eigenen übrigen. Ein Steuerumschlag an das eigene Konto ist in einer
 * Chat-Mailbox serverseitig verboten (`relay_blind_envelope` antwortet 400);
 * selbstadressiert läuft nur über den Gerätekanal. Wer die Frist am Telefon
 * setzt, muss sie am Rechner noch einmal setzen. Das betrifft jede Wirkung, die
 * über einen Steuerumschlag reist, und gehört in eine eigene Runde.
 */

import type { ChatGroupItem } from '@/api/social'
import { listeLokaleMailboxen, loadLocalMessages } from './messengerLocalStore'
import { zeitAlsZahl } from './nachrichtBezug'
import { tilgeNachrichtBeimServer, tilgeNachrichtLokal } from './nachrichtLoeschen'

/**
 * Die wählbaren Fristen. `0` heißt aus.
 *
 * `dativKey` steht neben `labelKey`, weil die Systemzeile „verschwinden nach …"
 * lautet und „nach 7 Tage" kein Deutsch ist. Zwei Formen sind billiger als ein
 * Satzbau, der die Zahl umstellt. Im Englischen sind beide gleich — die
 * Sprachdatei entscheidet das, nicht diese Liste.
 *
 * **Die längste Stufe hat ein Gegenstück im Backend.** Ein hochgeladener Anhang
 * wird dort nach `MEDIEN_AUFBEWAHRUNG_TAGE` (`chat_media_service.py`) abgeräumt.
 * Steht hier eine längere Frist als dort, lebt die Nachricht weiter und ihr
 * Anhang ist schon weg: die Anlage bricht mit einem 410 weg, ohne dass jemand
 * etwas gelöscht hat. Wer hier eine Stufe ergänzt, zieht die Zahl dort mit.
 */
export const VERFALL_STUFEN = [
  { sekunden: 0, labelKey: 'messenger.retention.off', dativKey: 'messenger.retentionDative.off' },
  { sekunden: 86_400, labelKey: 'messenger.retention.h24', dativKey: 'messenger.retentionDative.h24' },
  { sekunden: 604_800, labelKey: 'messenger.retention.d7', dativKey: 'messenger.retentionDative.d7' },
  { sekunden: 7_776_000, labelKey: 'messenger.retention.d90', dativKey: 'messenger.retentionDative.d90' },
] as const

export type VerfallSekunden = (typeof VERFALL_STUFEN)[number]['sekunden']

/** Was `t` können muss — mehr braucht diese Datei von i18next nicht. */
type Uebersetzer = (schluessel: string, werte?: Record<string, unknown>) => string

export function istBekannteStufe(sekunden: unknown): sekunden is VerfallSekunden {
  return VERFALL_STUFEN.some((s) => s.sekunden === Number(sekunden))
}

export function stufenLabel(sekunden: number, t: Uebersetzer): string {
  const stufe = VERFALL_STUFEN.find((s) => s.sekunden === sekunden)
  return stufe ? t(stufe.labelKey) : t('messenger.retentionSeconds', { count: sekunden })
}

/** Dieselbe Frist, wie sie hinter „nach" stehen muss. */
export function stufenDativ(sekunden: number, t: Uebersetzer): string {
  const stufe = VERFALL_STUFEN.find((s) => s.sekunden === sekunden)
  return stufe ? t(stufe.dativKey) : t('messenger.retentionSeconds', { count: sekunden })
}

const SCHLUESSEL = 'msm:chat_retention'

/** Die Frist eines Chats und wann sie zuletzt umgestellt wurde. */
export interface Verfallstand {
  sekunden: number
  /** ISO-Zeitpunkt der Umstellung. Leer heißt „Altbestand, gilt als ältestes". */
  stand: string
}

/** Was in der Ablage stehen kann: der heutige Stand oder eine alte blanke Zahl. */
type Eintrag = Verfallstand | number

function lies(): Record<string, Eintrag> {
  try {
    const roh = localStorage.getItem(SCHLUESSEL)
    const gelesen = roh ? JSON.parse(roh) : {}
    return gelesen && typeof gelesen === 'object' ? gelesen : {}
  } catch {
    return {}
  }
}

function alsStand(eintrag: Eintrag | undefined): Verfallstand {
  if (typeof eintrag === 'number') return { sekunden: eintrag, stand: '' }
  if (eintrag && typeof eintrag === 'object') {
    return { sekunden: Number(eintrag.sekunden) || 0, stand: String(eintrag.stand || '') }
  }
  return { sekunden: 0, stand: '' }
}

/**
 * Die Frist eines Chats in Sekunden, 0 heißt aus.
 *
 * Liegt im localStorage neben den Stummschaltungen, nicht versiegelt: dass für
 * einen Chat eine Frist gilt, ist dieselbe Klasse Metadatum wie „dieser Chat
 * ist stummgeschaltet". Der Inhalt ist nicht betroffen.
 */
export function verfallsfrist(blindMailboxId: string): number {
  return alsStand(lies()[blindMailboxId]).sekunden
}

export function verfallStand(blindMailboxId: string): Verfallstand {
  return alsStand(lies()[blindMailboxId])
}

/**
 * Auch die Null wird geschrieben, nicht gelöscht.
 *
 * Ein fehlender Eintrag hätte keinen Zeitpunkt, und ein Abschalten ohne
 * Zeitpunkt verlöre gegen jedes ältere Einschalten der Gegenseite, das noch im
 * Mailbox-Fenster liegt.
 */
export function setzeVerfallsfrist(
  blindMailboxId: string,
  sekunden: number,
  stand: string = new Date().toISOString(),
): void {
  const alle = lies()
  alle[blindMailboxId] = { sekunden, stand }
  try {
    localStorage.setItem(SCHLUESSEL, JSON.stringify(alle))
  } catch {
    // Ohne localStorage gilt die Frist eben nur für diese Sitzung.
  }
}

/**
 * Eine Umstellung der Gegenseite oder eines eigenen zweiten Geräts übernehmen.
 *
 * `true` heißt: die Frist ist dadurch eine andere geworden. Nur dann gehört
 * eine Systemzeile in den Verlauf — derselbe Umschlag kommt bei jedem Abruf
 * erneut vorbei, und eine Meldung je Abruf wäre unerträglich.
 */
export function uebernehmeVerfall(blindMailboxId: string, sekunden: number, stand: string): boolean {
  const bisher = verfallStand(blindMailboxId)
  if (zeitAlsZahl(stand) <= zeitAlsZahl(bisher.stand)) return false
  setzeVerfallsfrist(blindMailboxId, sekunden, stand)
  return bisher.sekunden !== sekunden
}

/**
 * Ob dieses Mitglied die Frist der Gruppe stellen durfte.
 *
 * Dieselbe Bauart wie `durfteAnheften`: die Antwort kommt aus der Marke, die
 * der Server je Mitglied ausrechnet, nicht aus einer hier nachgebauten
 * Rollenlogik. Fehlt die Marke, gilt **nein** — eine ausbleibende Umstellung
 * ist der sichere Ausgang, eine unberechtigte nicht.
 *
 * `absenderId` muss der **belegte** Urheber sein, nie die `actor_id` aus dem
 * Paket: sonst liehe sich jeder das Recht des Eigentümers, indem er dessen
 * Kennung hineinschreibt.
 */
export function durfteVerfallStellen(
  gruppe: Pick<ChatGroupItem, 'members'> | null | undefined,
  absenderId: number,
): boolean {
  if (!gruppe?.members) return false
  const m = gruppe.members.find((x) => Number(x.user_id) === Number(absenderId))
  return Boolean(m?.can_set_disappearing_messages)
}

/** Wann eine jetzt gesendete Nachricht verfällt, oder `undefined` ohne Frist. */
export function verfaelltAm(sekunden: number, ab: Date = new Date()): string | undefined {
  if (!sekunden || sekunden <= 0) return undefined
  return new Date(ab.getTime() + sekunden * 1000).toISOString()
}

/** Ob diese Zeile ihre Zeit überschritten hat. */
export function istVerfallen(msg: { verfaelltAm?: string; isDeleted?: boolean }, jetzt = Date.now()): boolean {
  if (!msg.verfaelltAm || msg.isDeleted) return false
  const ziel = Date.parse(msg.verfaelltAm)
  return Number.isFinite(ziel) && ziel <= jetzt
}

/** Alle Zeilen, die jetzt fällig sind. */
export function faelligeZeilen<T extends { verfaelltAm?: string; isDeleted?: boolean }>(
  zeilen: readonly T[],
  jetzt = Date.now(),
): T[] {
  return zeilen.filter((m) => istVerfallen(m, jetzt))
}

/**
 * Einmal über **alle** Chats, nicht nur über den offenen.
 *
 * Ohne diesen Durchgang verschwände eine Nachricht erst, wenn man ihren Chat
 * das nächste Mal öffnet — bei einem Chat, den man nie wieder öffnet, also nie.
 * Das wäre genau die Sorte Zusage, die dieses Produkt nicht machen darf.
 *
 * Läuft nur bei entsperrtem Messenger: gesperrt gibt die Ablage nichts heraus.
 * Fehlschläge werden übergangen, der nächste Start versucht es erneut.
 */
export async function raeumeAlleChats(jetzt = Date.now()): Promise<number> {
  let weggeraeumt = 0
  const zeitpunkt = new Date(jetzt).toISOString()
  for (const mailboxId of await listeLokaleMailboxen()) {
    let faellig: Awaited<ReturnType<typeof loadLocalMessages>> = []
    try {
      faellig = faelligeZeilen(await loadLocalMessages(mailboxId), jetzt)
    } catch {
      continue
    }
    for (const msg of faellig) {
      try {
        // Beim Server räumt jede Seite nur ihr eigenes ab. Zusammen ist danach
        // nichts mehr da; allein wäre keine der beiden dazu berechtigt.
        if (msg.isSelf) await tilgeNachrichtBeimServer(mailboxId, msg)
        await tilgeNachrichtLokal(mailboxId, msg, zeitpunkt)
        weggeraeumt += 1
      } catch {
        // Was jetzt nicht wegging, geht beim nächsten Durchgang.
      }
    }
  }
  return weggeraeumt
}
