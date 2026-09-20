/**
 * Verschwindende Nachrichten.
 *
 * Erst jetzt ehrlich machbar, weil Löschen seit 09/2026 wirklich löscht:
 * Umschläge, hochgeladene Anhänge und die lokale Zeile. Vorher hätte
 * „verschwindet nach sieben Tagen" geheißen „wird nach sieben Tagen
 * ausgeblendet", und das ist etwas anderes.
 *
 * ## Wer was wegräumt
 *
 * **Jede Seite** tilgt abgelaufene Zeilen bei sich lokal. **Der Absender**
 * räumt zusätzlich Chiffretext und Blobs beim Server ab — er ist der einzige,
 * der das darf.
 *
 * ## Die Grenze, die klar dastehen muss
 *
 * Löschen kann beim Server nur, wer den Blob hochgeladen hat. Kommt das Gerät
 * des Absenders nie wieder online, bleibt sein Chiffretext in der Mailbox
 * liegen, auch wenn die Nachricht auf allen Geräten längst verschwunden ist.
 * Das steht so in der Datenschutzerklärung und im Einstellungstext — nicht in
 * einer Fußnote.
 *
 * ## Keine heimliche Änderung
 *
 * Wer die Frist ändert, schickt einen `retention`-Umschlag, und beide Seiten
 * zeigen das als Systemzeile im Verlauf. Eine still umgestellte Frist wäre
 * genau die Art Überraschung, gegen die dieses Produkt gebaut ist.
 */

/** Die wählbaren Fristen. `0` heißt aus. */
export const VERFALL_STUFEN = [
  { sekunden: 0, label: 'Aus' },
  { sekunden: 86_400, label: '24 Stunden' },
  { sekunden: 604_800, label: '7 Tage' },
  { sekunden: 7_776_000, label: '90 Tage' },
] as const

export type VerfallSekunden = (typeof VERFALL_STUFEN)[number]['sekunden']

export function istBekannteStufe(sekunden: unknown): sekunden is VerfallSekunden {
  return VERFALL_STUFEN.some((s) => s.sekunden === Number(sekunden))
}

export function stufenLabel(sekunden: number): string {
  return VERFALL_STUFEN.find((s) => s.sekunden === sekunden)?.label ?? `${sekunden} s`
}

const SCHLUESSEL = 'msm:chat_retention'

function lies(): Record<string, number> {
  try {
    const roh = localStorage.getItem(SCHLUESSEL)
    const gelesen = roh ? JSON.parse(roh) : {}
    return gelesen && typeof gelesen === 'object' ? gelesen : {}
  } catch {
    return {}
  }
}

/**
 * Die Frist eines Chats.
 *
 * Liegt im localStorage neben den Stummschaltungen, nicht versiegelt: dass für
 * einen Chat eine Frist gilt, ist dieselbe Klasse Metadatum wie „dieser Chat
 * ist stummgeschaltet". Der Inhalt ist nicht betroffen.
 */
export function verfallsfrist(blindMailboxId: string): number {
  return Number(lies()[blindMailboxId] || 0)
}

export function setzeVerfallsfrist(blindMailboxId: string, sekunden: number): void {
  const alle = lies()
  if (sekunden > 0) alle[blindMailboxId] = sekunden
  else delete alle[blindMailboxId]
  try {
    localStorage.setItem(SCHLUESSEL, JSON.stringify(alle))
  } catch {
    // Ohne localStorage gilt die Frist eben nur für diese Sitzung.
  }
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
