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

import { listeLokaleMailboxen, loadLocalMessages } from './messengerLocalStore'
import { tilgeNachrichtBeimServer, tilgeNachrichtLokal } from './nachrichtLoeschen'

/**
 * Die wählbaren Fristen. `0` heißt aus.
 *
 * `dativ` steht daneben, weil die Systemzeile „verschwinden nach …" lautet und
 * „nach 7 Tage" kein Deutsch ist. Zwei Formen sind billiger als ein Satzbau,
 * der die Zahl umstellt.
 */
export const VERFALL_STUFEN = [
  { sekunden: 0, label: 'Aus', dativ: 'aus' },
  { sekunden: 86_400, label: '24 Stunden', dativ: '24 Stunden' },
  { sekunden: 604_800, label: '7 Tage', dativ: '7 Tagen' },
  { sekunden: 7_776_000, label: '90 Tage', dativ: '90 Tagen' },
] as const

export type VerfallSekunden = (typeof VERFALL_STUFEN)[number]['sekunden']

export function istBekannteStufe(sekunden: unknown): sekunden is VerfallSekunden {
  return VERFALL_STUFEN.some((s) => s.sekunden === Number(sekunden))
}

export function stufenLabel(sekunden: number): string {
  return VERFALL_STUFEN.find((s) => s.sekunden === sekunden)?.label ?? `${sekunden} s`
}

/** Dieselbe Frist, wie sie hinter „nach" stehen muss. */
export function stufenDativ(sekunden: number): string {
  return VERFALL_STUFEN.find((s) => s.sekunden === sekunden)?.dativ ?? `${sekunden} Sekunden`
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

/** Als Zahl, damit ein Zeitpunkt ohne `Z` nicht anders sortiert als einer mit. */
function alsZahl(stand: string): number {
  const t = Date.parse(stand)
  return Number.isFinite(t) ? t : 0
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
  if (alsZahl(stand) <= alsZahl(bisher.stand)) return false
  setzeVerfallsfrist(blindMailboxId, sekunden, stand)
  return bisher.sekunden !== sekunden
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
