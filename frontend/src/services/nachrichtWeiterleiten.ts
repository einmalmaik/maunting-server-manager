/**
 * Weiterleiten: neu verschlüsseln statt umhängen.
 *
 * Der Klartext liegt lokal — das ist die einzige Fassung, die dieses Gerät je
 * hat. Weiterleiten heißt deshalb: daraus eine neue Nachricht bauen und an das
 * neue Ziel senden. Ein Umschlag lässt sich nicht umadressieren.
 *
 * **Medien müssen wirklich noch einmal hoch.** Ein Anhang ist per DIS an
 * `absenderId` **und** `blindMailboxId` gebunden (siehe `medienKrypto.ts`); in
 * einem anderen Gespräch geht er schlicht nicht auf. Also herunterladen,
 * entschlüsseln, für die Zielmailbox neu verschlüsseln, hochladen. Das dauert
 * über Mobilfunk spürbar, und der Aufrufer bekommt deshalb einen Fortschritt
 * gemeldet statt eines eingefrorenen Knopfes.
 *
 * **Nebenwirkung, die richtig so ist:** der Weiterleitende wird Eigentümer des
 * neuen Blobs. Nur der Hochladende darf löschen — also kann er die Kopie
 * später auch wieder loswerden.
 *
 * **Grenze, die genannt gehört:** der ursprüngliche Absender reist **nicht**
 * mit. Wer eine Nachricht weiterleitet, leitet Inhalt weiter, keine
 * Urheberschaft. Die Marke sagt nur „das ist nicht hier entstanden".
 */

import { ladeAnhangHerunter, ladeAnhangHoch } from '@/api/social'
import type { MedienZeiger } from '@/services/medienKrypto'

/** Das Ziel einer Weiterleitung. */
export interface Weiterleitungsziel {
  blindMailboxId: string
  /** Für einen Direktchat. */
  recipientId?: number | null
  /** Für eine Gruppe. */
  groupId?: number | null
  /** Nur zur Anzeige. */
  name: string
}

/**
 * Ein Anhang, so weit er zum Weiterleiten gebraucht wird.
 *
 * Bewusst nur die Felder, die dieses Modul anfasst, und **ohne**
 * Index-Signatur: mit einer wäre kein konkreter Anhangstyp mehr zuweisbar.
 * Alles Übrige (Größe, Dauer, Maße, Vorschaubild) reist über das Ausbreiten in
 * `hängeAnhangUm` unverändert mit.
 */
interface Anhang {
  mediaId?: string
  paketSchluessel?: string
  fileId?: string
  name?: string
  mimeType?: string
}

/** Was weitergeleitet werden soll — der Auszug aus der lokalen Zeile. */
export interface Weiterleitbar {
  text?: string
  noteAttachment?: unknown
  calendarAttachment?: unknown
  stickerAttachment?: unknown
  storyReply?: unknown
  imageAttachment?: Anhang
  fileAttachment?: Anhang
  audioAttachment?: Anhang
  videoNoteAttachment?: Anhang
}

/** Woher der Anhang stammt — das, wogegen er heute gebunden ist. */
export interface Herkunft {
  absenderId: number
  blindMailboxId: string
}

function istVollstaendig(a: Anhang | undefined): a is Required<Pick<Anhang, 'mediaId' | 'paketSchluessel' | 'fileId'>> & Anhang {
  return Boolean(a?.mediaId && a?.paketSchluessel && a?.fileId)
}

/**
 * Lädt einen Anhang herunter und für das neue Ziel wieder hoch.
 *
 * Gibt den neuen Zeiger zurück; alle übrigen Felder des Anhangs (Name, Größe,
 * Dauer, Maße) bleiben, wie sie waren — sie beschreiben den Inhalt, nicht
 * seinen Ablageort.
 */
async function hängeAnhangUm(
  anhang: Anhang,
  herkunft: Herkunft,
  ziel: Weiterleitungsziel,
  eigeneId: number,
): Promise<Anhang> {
  if (!istVollstaendig(anhang)) return anhang
  const zeiger: MedienZeiger = {
    mediaId: anhang.mediaId!,
    paketSchluessel: anhang.paketSchluessel!,
    fileId: anhang.fileId!,
  }
  const klartext = await ladeAnhangHerunter(zeiger, herkunft)
  const neu = await ladeAnhangHoch({
    klartext,
    dateiname: String(anhang.name || 'anhang'),
    mimeType: String(anhang.mimeType || 'application/octet-stream'),
    blindMailboxId: ziel.blindMailboxId,
    absenderId: eigeneId,
    groupId: ziel.groupId ?? null,
  })
  return {
    ...anhang,
    mediaId: neu.mediaId,
    paketSchluessel: neu.paketSchluessel,
    fileId: neu.fileId,
  }
}

export interface WeiterleitungsFortschritt {
  /** Wie viele Anhänge insgesamt neu hochgeladen werden müssen. */
  gesamt: number
  /** Wie viele davon durch sind. */
  fertig: number
}

/**
 * Baut aus einer lokalen Zeile den Inhalt für ein neues Ziel.
 *
 * Wirft, wenn ein Anhang nicht übertragen werden kann. Eine Weiterleitung, bei
 * der stillschweigend nur der Text ankommt, wäre schlimmer als gar keine: der
 * Absender glaubt, das Bild sei draußen.
 */
export async function baueWeiterleitung(
  quelle: Weiterleitbar,
  herkunft: Herkunft,
  ziel: Weiterleitungsziel,
  eigeneId: number,
  beiFortschritt?: (f: WeiterleitungsFortschritt) => void,
): Promise<Weiterleitbar> {
  const anhaenge = (['imageAttachment', 'fileAttachment', 'audioAttachment', 'videoNoteAttachment'] as const)
    .filter((feld) => istVollstaendig(quelle[feld]))

  const ergebnis: Weiterleitbar = {
    text: quelle.text,
    noteAttachment: quelle.noteAttachment,
    calendarAttachment: quelle.calendarAttachment,
    stickerAttachment: quelle.stickerAttachment,
    storyReply: quelle.storyReply,
  }

  let fertig = 0
  beiFortschritt?.({ gesamt: anhaenge.length, fertig })
  for (const feld of anhaenge) {
    ergebnis[feld] = await hängeAnhangUm(quelle[feld]!, herkunft, ziel, eigeneId)
    fertig++
    beiFortschritt?.({ gesamt: anhaenge.length, fertig })
  }

  // Anhänge ohne vollständigen Zeiger (Altbestand) gehen unverändert mit; sie
  // tragen keinen Blob, den man umhängen könnte.
  for (const feld of ['imageAttachment', 'fileAttachment', 'audioAttachment', 'videoNoteAttachment'] as const) {
    if (!ergebnis[feld] && quelle[feld]) ergebnis[feld] = quelle[feld]
  }

  return ergebnis
}

/** Ob an dieser Nachricht überhaupt etwas weiterzuleiten ist. */
export function istWeiterleitbar(msg: Weiterleitbar & { isDeleted?: boolean }): boolean {
  if (msg.isDeleted) return false
  return Boolean(
    (msg.text && msg.text.trim()) ||
      msg.noteAttachment ||
      msg.calendarAttachment ||
      msg.stickerAttachment ||
      msg.imageAttachment ||
      msg.fileAttachment ||
      msg.audioAttachment ||
      msg.videoNoteAttachment,
  )
}
