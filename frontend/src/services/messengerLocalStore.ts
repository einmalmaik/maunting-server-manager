/**
 * MSM Messenger Local Store (IndexedDB)
 *
 * Persists decrypted chat messages locally so conversation histories survive
 * browser refreshes (F5) and app restarts (Web, Desktop, Mobile) without
 * re-entering recovery keys or waiting for network round-trips.
 *
 * Database: `msm_messenger_local:konto:<id>` (v8)
 * Object Stores:
 *  - `messages`: keyed by `[blindMailboxId, id]`, indexed by `blindMailboxId`, `createdAt`, `clientUuid`
 *  - `mailboxes`: keyed by `blindMailboxId`, tracks `lastSyncedEnvelopeId` and `updatedAt`
 *
 * Seit 09/2026 liegt der Inhalt hier nicht mehr offen, sobald ein Messenger-PIN
 * eingerichtet ist: jede Zeile geht durch `lokaleVersiegelung`, nur die
 * Schlüssel- und Indexfelder bleiben lesbar. Ohne PIN ändert sich nichts.
 *
 * Daraus folgt eine Regel für jeden Schreibweg in dieser Datei: **erst
 * versiegeln, dann die Transaktion öffnen.** Verschlüsseln ist asynchron, und
 * eine IndexedDB-Transaktion schließt sich, sobald der Ereignisumlauf
 * leerläuft — ein `await` zwischen `transaction()` und `put()` bricht sie ab.
 * Gelesen wird umgekehrt: erst die Rohzeilen holen, dann außerhalb der
 * Transaktion entsiegeln.
 *
 * **Eine Datenbank je Konto, seit 09/2026.** Bis dahin hieß sie schlicht
 * `msm_messenger_local` und gehörte damit dem Browserprofil statt dem
 * angemeldeten Menschen. Wer sich nacheinander mit zwei Konten anmeldete,
 * hinterließ beide Gesprächsverläufe in derselben Ablage — im Klartext, solange
 * kein Messenger-PIN gesetzt ist, und über das Abmelden hinaus. Das zweite Konto
 * las sie beim Öffnen des Messengers einfach mit: `loadLocalMessages` fragt nach
 * der Mailbox, nicht nach dem Besitzer. Aufgefallen ist es an einer Zeile mit
 * `senderId: 2` und `isSelf: true` in der Sitzung von Konto 1 — die abgelegte
 * Sicht des anderen Kontos auf dessen eigene Nachricht.
 *
 * Die Trennung liegt deshalb auf der Datenbank, nicht auf einem Schlüsselfeld:
 * eine fremde Zeile ist dann nicht „gefiltert", sondern außer Reichweite. Ohne
 * angemeldetes Konto gibt es keine Ablage — `openLocalDatabase` weist ab, statt
 * auf eine gemeinsame auszuweichen.
 */

import i18n from '@/i18n'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { entsiegleZeile, entsiegleZeilen, versiegleZeile } from './lokaleVersiegelung'
import type { AugenblickMarke, RettungsMarke } from './funkenService'
import { istSteuerzeile } from './nachrichtBezug'
import {
  MAX_AUFTRAEGE,
  type SteuerAuftrag,
} from './steuerOutboxTypen'

export interface LocalStoredMessage {
  blindMailboxId?: string
  id: number
  clientUuid?: string
  senderId: number
  senderName?: string
  text: string
  createdAt: string
  isSelf: boolean
  isDelivered?: boolean
  isRead?: boolean
  isEdited?: boolean
  editedAt?: string
  isDeleted?: boolean
  deletedAt?: string
  originalText?: string
  /**
   * Eine Systemzeile im Verlauf, etwa die Meldung über einen Sitzungsbruch.
   *
   * Sie steht hier nur, damit `saveLocalMessages` und `loadLocalMessages` sie
   * aussortieren können: eine solche Zeile ist eine Aussage über den Zustand
   * **dieses** Geräts in **diesem** Moment, kein Gesprächsinhalt. Abgelegt
   * bliebe sie für immer stehen — und weil sie ihre Kennung aus der Uhr nimmt,
   * sortiert sie sich hinter jede später eintreffende Nachricht. Nach ein paar
   * Tagen stünde am Ende jedes Gesprächs ein Stapel alter Meldungen.
   */
  isSystem?: boolean
  noteAttachment?: any
  calendarAttachment?: any
  imageAttachment?: any
  audioAttachment?: any
  fileAttachment?: any
  stickerAttachment?: any
  storyReply?: any
  videoNoteAttachment?: any
  videoUrl?: string
  status?: 'queued' | 'failed' | 'sent' | 'delivered' | 'read'

  /**
   * Wirkungen, die aus Steuerumschlägen kommen und deshalb **hier** landen
   * müssen, nicht nur im Umschlag.
   *
   * Die Mailbox gibt die letzten hundert Umschläge her. Eine Reaktion, die
   * nur dort lebt, verschwindet, sobald hundert neue darüber gelaufen sind —
   * die Nachricht bliebe stehen, die Reaktion wäre weg. Näheres in
   * `nachrichtBezug.ts`. Alle Felder gehen durch dieselbe Versiegelung wie
   * der Text; `NACHRICHT_KLARFELDER` bleibt unverändert.
   */
  reaktionen?: Record<string, number[]>
  antwortAuf?: { clientUuid: string; absenderId: number; absenderName?: string; auszug: string }
  erwaehnungen?: number[]
  erwaehntAlle?: boolean
  weitergeleitet?: boolean
  /** Sternchen. Rein lokal — welche Nachricht mir wichtig ist, geht niemanden an. */
  istMarkiert?: boolean
  /** Ab wann diese Zeile von selbst verschwindet (ISO). */
  verfaelltAm?: string
  /** Ein Augenblick für den Funken. Siehe `funkenService.ts`. */
  augenblick?: AugenblickMarke
  /** Eine Wiederherstellung des Funkens. */
  funkenRettung?: RettungsMarke
}

export interface LocalMailboxMeta {
  blindMailboxId: string
  lastSyncedEnvelopeId: number
  updatedAt: string
}

const DB_PRAEFIX = 'msm_messenger_local'
const DB_VERSION = 8

/** Die Ablage dieses Kontos. Ein anderes Konto, eine andere Datenbank. */
function dbName(kontoId: number): string {
  return `${DB_PRAEFIX}:konto:${kontoId}`
}
const STORE_MESSAGES = 'messages'
const STORE_MAILBOXES = 'mailboxes'
const STORE_KLARTEXTE = 'envelope_plaintexts'
const STORE_ENTWUERFE = 'entwuerfe'
const STORE_FUNKEN = 'funken'
const STORE_MEDIEN = 'medien'
/** Angefangene Medienuebertragungen, Stueck fuer Stueck. */
const STORE_MEDIEN_TEILE = 'medien_teile'
/** Offene Nachholauftraege fuer Anhaenge, die nicht zu holen waren. */
const STORE_MEDIEN_AUFTRAEGE = 'medien_auftraege'
/** Inhaltslose Marken geloeschter Medienkennungen. */
const STORE_MEDIEN_GRABSTEINE = 'medien_grabsteine'
/** Wirkungen, die noch nicht hinaus sind (Bearbeiten, Loeschen, Reaktion, ...). */
const STORE_STEUER_AUFTRAEGE = 'steuer_auftraege'

/**
 * So viele Grabsteine bleiben.
 *
 * Ein Grabstein ist eine Kennung und ein Zeitpunkt, kein Inhalt. Trotzdem darf
 * die Liste nicht mit jedem geloeschten Bild wachsen: sie wird bei jedem
 * Schreiben eines Anhangs gefragt. 500 decken einen langen Verlauf ab; was
 * aelter ist, faellt weg. Ein uralter Historientransfer koennte diese Kennung
 * dann wieder anlegen — die Grenze ist der Preis dafuer, dass die Ablage nicht
 * unbegrenzt waechst.
 */
export const GRABSTEINE_MAX = 500

/**
 * Die Felder einer Nachricht, die im Klartext liegen bleiben müssen.
 *
 * `blindMailboxId` und `id` bilden den Schlüssel, `clientUuid` trägt den Index,
 * über den `updateMessageInLocalStore` eine Zeile wiederfindet, `createdAt` den
 * Zeitindex. Was IndexedDB als Schlüssel braucht, kann nicht verschlüsselt
 * sein — sonst gibt es den Index nicht mehr.
 *
 * Das ist der Preis des Siegels und er steht so auch in der Dokumentation: wer
 * eine kopierte Platte hat, sieht weiter, welche Mailbox wann wie viele
 * Nachrichten bekommen hat. Den Inhalt sieht er nicht. `blindMailboxId` ist
 * bereits eine blinde Kennung, `clientUuid` eine Zufallskennung ohne Bezug zum
 * Text.
 */
const NACHRICHT_KLARFELDER = ['blindMailboxId', 'id', 'createdAt', 'clientUuid'] as const

/** Bindet eine Nachrichtenzeile an ihren Platz. Siehe `versiegleZeile`. */
function nachrichtAad(blindMailboxId: string, id: number | undefined): string {
  return `msm-nachricht:${blindMailboxId}:${id ?? ''}`
}

/** Bindet einen Umschlag-Klartext an seinen Platz. */
function klartextAad(blindMailboxId: string, envelopeId: number): string {
  return `msm-umschlag:${blindMailboxId}:${envelopeId}`
}

let dbPromise: Promise<IDBDatabase> | null = null
/** Zu welchem Konto die offene Verbindung gehört. */
let offenesKonto: number | null = null
let altbestandGeraeumt = false

/**
 * Entfernt die alte, kontolose Datenbank — einmal je Browsersitzung.
 *
 * Übernommen wird nichts. Ihre Zeilen tragen kein Konto, und keines lässt sich
 * nachträglich ermitteln: `senderId` und `isSelf` beschreiben die Sicht dessen,
 * der sie abgelegt hat, und die Mailbox-Kennung gehört dem Gespräch, nicht einem
 * Menschen. Sie dem Konto zuzuschlagen, das nach dem Umstieg zufällig als
 * erstes den Messenger öffnet, wäre genau das Leck, das hier zugeht.
 *
 * Der Preis steht dazu und ist bekannt: was nur dort lag, ist weg. Für Gruppen
 * und empfangene Nachrichten holt die Mailbox das Fenster der letzten hundert
 * Umschläge zurück; der eigene Anteil eines Direktgesprächs kommt nicht wieder,
 * denn eine selbst verschlüsselte Ratchet-Nachricht kann der Absender nicht
 * erneut öffnen. Ein Verlauf, der jedem Konto dieses Rechners offensteht, ist
 * den Tausch trotzdem nicht wert.
 */
function raeumeAltbestand(): void {
  if (altbestandGeraeumt) return
  altbestandGeraeumt = true
  try {
    indexedDB.deleteDatabase(DB_PRAEFIX)
  } catch {
    // Ein blockiertes Löschen (offene Verbindung in einem anderen Tab) holt
    // der nächste Start nach. Kein Grund, den Messenger nicht zu starten.
  }
}

/**
 * Bittet den Browser, diese Ablage zu behalten.
 *
 * Ohne das gilt eine IndexedDB als *best effort*: der Browser darf sie bei
 * Speicherdruck verwerfen oder beim Aufräumen selten besuchter Seiten
 * mitnehmen, ohne zu fragen und ohne es zu melden. Seit der Umstellung auf den
 * Double Ratchet wiegt das schwerer als früher, denn der **eigene**
 * Gesprächsanteil steht nirgendwo sonst: wer eine Ratchet-Nachricht
 * verschlüsselt, kann sie selbst nicht wieder öffnen, und der Server hat nur
 * den Umschlag. Was hier verschwindet, ist verschwunden.
 *
 * Erst `persisted()`, dann `persist()`: Firefox fragt den Menschen, und eine
 * Frage, die bei jedem Öffnen wiederkommt, beantwortet irgendwann jeder mit
 * „nein". Chrome und Safari entscheiden still anhand ihrer eigenen Kriterien.
 *
 * Die Antwort ist eine Auskunft, keine Zusage: `false` heißt, der Verlauf
 * liegt weiter da, darf aber gehen. Der Aufrufer bekommt sie zurück, damit
 * sich das später sichtbar machen lässt, statt es zu verschweigen.
 */
export async function sichereDauerhafteAblage(): Promise<boolean> {
  try {
    if (typeof navigator === 'undefined' || !navigator.storage?.persist) return false
    if (await navigator.storage.persisted()) return true
    return await navigator.storage.persist()
  } catch {
    // Ältere Browser und abgeschaltete Speicher-APIs. Kein Grund, den
    // Messenger nicht zu starten.
    return false
  }
}

/**
 * Schliesst die Verbindung — nur für Tests.
 *
 * Ein Absturztest braucht einen echten Neustart: die offene Verbindung zu,
 * alle Modulmerker zurück, und der nächste Zugriff öffnet dieselbe Datenbank
 * neu. Im Betrieb gibt es dafür keinen Grund; ein Kontowechsel schliesst die
 * alte Verbindung von selbst.
 */
export function schliesseLokaleAblageFuerTest(): void {
  const offen = dbPromise
  dbPromise = null
  offenesKonto = null
  altbestandGeraeumt = false
  if (offen) void offen.then((db) => db.close()).catch(() => {})
}

function openLocalDatabase(): Promise<IDBDatabase> {
  const konto = angemeldetesKonto()
  if (konto === null) {
    // Ohne Konto keine Ablage. Nicht „ersatzweise die gemeinsame" — dieser
    // Ausweg *war* der Fehler. Jeder Aufrufer, dem das etwas ausmacht, fängt
    // ihn; die Wege, die bewusst laut scheitern, sollen auch hier laut sein.
    return Promise.reject(new Error('Lokale Messenger-Ablage ohne angemeldetes Konto'))
  }
  // Kontowechsel in derselben Browsersitzung: die alte Verbindung geht zu,
  // bevor die neue aufgeht. Sonst bliebe die Ablage des vorigen Kontos offen
  // und `dbPromise` zeigte weiter dorthin.
  if (dbPromise && offenesKonto !== konto) {
    const alt = dbPromise
    dbPromise = null
    void alt.then((db) => db.close()).catch(() => {})
  }
  if (dbPromise) return dbPromise
  offenesKonto = konto
  dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error('IndexedDB not available in current environment'))
    }
    raeumeAltbestand()
    const req = indexedDB.open(dbName(konto), DB_VERSION)

    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_MESSAGES)) {
        const msgStore = db.createObjectStore(STORE_MESSAGES, {
          keyPath: ['blindMailboxId', 'id'],
        })
        msgStore.createIndex('by_mailbox', 'blindMailboxId', { unique: false })
        msgStore.createIndex('by_created_at', 'createdAt', { unique: false })
        msgStore.createIndex('by_client_uuid', 'clientUuid', { unique: false })
      }
      if (!db.objectStoreNames.contains(STORE_MAILBOXES)) {
        db.createObjectStore(STORE_MAILBOXES, { keyPath: 'blindMailboxId' })
      }
      // Version 2: der Klartext je Umschlag.
      //
      // Ein Double-Ratchet-Nachrichtenschlüssel wird beim Entschlüsseln
      // verbraucht — derselbe Umschlag geht kein zweites Mal auf. Ohne diese
      // Ablage wäre jeder Abruf ein Versuch, bereits verbrauchte Schlüssel
      // erneut zu benutzen, und jede Nachricht nur so lange lesbar wie die
      // Browsersitzung, in der sie ankam.
      if (!db.objectStoreNames.contains(STORE_KLARTEXTE)) {
        db.createObjectStore(STORE_KLARTEXTE, { keyPath: ['blindMailboxId', 'envelopeId'] })
      }
      // Version 3: ungesendete Entwürfe.
      //
      // Ein Entwurf ist Klartext, den noch niemand gesehen hat — das
      // Empfindlichste, was der Messenger hält. Er gehört deshalb hierher und
      // nicht in den localStorage neben die Stummschaltungen: hier greift
      // dieselbe Versiegelung wie beim Verlauf.
      if (!db.objectStoreNames.contains(STORE_ENTWUERFE)) {
        db.createObjectStore(STORE_ENTWUERFE, { keyPath: 'blindMailboxId' })
      }
      // Version 4: die Funken, eine Akte je Kontakt (`funkenService.ts`).
      //
      // Wer mit wem wie lange einen Funken hat, weiß nur dieses Gerät — der
      // Server hat dafür keine Tabelle. Deshalb liegt es hier, versiegelt wie
      // der Verlauf, und nicht offen im localStorage.
      if (!db.objectStoreNames.contains(STORE_FUNKEN)) {
        db.createObjectStore(STORE_FUNKEN, { keyPath: 'partnerId' })
      }
      // Version 5: die Anhänge selbst.
      //
      // Bis 10/2026 lag ein empfangenes Bild nur im Arbeitsspeicher
      // (`chatMediaBlobCache`). Nach einem Neuladen war es weg, und ohne Netz
      // kam es nie wieder: der Server kennt den Blob nur noch 30 Tage, und die
      // Nachricht trägt keinen Inhalt, sondern einen Zeiger darauf. Was hier
      // liegt, ist der entschlüsselte Anhang — im Siegel der Ablage, also
      // geschützt wie der Verlauf.
      if (!db.objectStoreNames.contains(STORE_MEDIEN)) {
        db.createObjectStore(STORE_MEDIEN, { keyPath: 'mediaId' })
      }
      // Version 6: angekommene Teile einer Medienuebertragung.
      //
      // Ein Anhang reist in Stuecken, weil ein Umschlag 1 MiB fasst. Erst wenn
      // alle Teile da sind und die Pruefsumme stimmt, wird daraus ein Eintrag
      // in `medien`; bis dahin liegt hier nur, was noch nicht uebernommen ist.
      if (!db.objectStoreNames.contains(STORE_MEDIEN_TEILE)) {
        const teile = db.createObjectStore(STORE_MEDIEN_TEILE, { keyPath: 'id' })
        teile.createIndex('by_transfer', 'transferId', { unique: false })
        teile.createIndex('by_media', 'mediaId', { unique: false })
      }
      /*
       * Version 7: der Nachholauftrag und der Grabstein.
       *
       * Der Auftrag liegt in **dieser** Ablage und nicht im localStorage: er
       * traegt Paketschluessel, Dateiname und Bindung, also dieselben Angaben
       * wie die Nachricht daneben. Hier gilt das Siegel des Messengers; im
       * localStorage waere er fuer jedes Konto des Browsers lesbar. Eine eigene
       * Datenbank waere ausserdem ein zweiter Ort, an dem ein Kontowechsel
       * aufgeraeumt werden muesste — die Ablage je Konto gibt es hier schon.
       *
       * Der Grabstein ist bewusst inhaltslos: nur die Medienkennung und wann
       * sie geloescht wurde. Er verhindert, dass ein verspaeteter Download
       * oder Historientransfer die Datei wieder anlegt.
       */
      if (!db.objectStoreNames.contains(STORE_MEDIEN_AUFTRAEGE)) {
        const auftraege = db.createObjectStore(STORE_MEDIEN_AUFTRAEGE, { keyPath: 'id' })
        auftraege.createIndex('by_mailbox', 'mailbox', { unique: false })
        auftraege.createIndex('by_media', 'mediaId', { unique: false })
        auftraege.createIndex('by_angelegt', 'angelegtAm', { unique: false })
      }
      if (!db.objectStoreNames.contains(STORE_MEDIEN_GRABSTEINE)) {
        const grabsteine = db.createObjectStore(STORE_MEDIEN_GRABSTEINE, { keyPath: 'mediaId' })
        grabsteine.createIndex('by_geloescht', 'geloeschtAm', { unique: false })
      }
      /*
       * Version 8: die Outbox der Steuerwirkungen.
       *
       * Bearbeiten, Loeschen, Reaktion, Anheften und Verfall reisten bis 10/2026
       * als ein einziger Relay-Aufruf; ein Netzfehler nahm die Wirkung lokal
       * zurueck und die Gegenseite erfuhr nie davon. Der Auftrag liegt hier und
       * nicht im localStorage: er traegt den Inhalt der Nutzlast und die
       * Mitgliederliste, also dieselben Angaben wie der Verlauf daneben. Hier
       * gilt das Siegel des Messengers, und die Ablage gehoert einem Konto —
       * beim Abmelden faellt die Outbox mit ihr (`clearLocalMessengerStore`).
       *
       * Lesbar bleiben nur die Felder, die einen Index brauchen: Kennung,
       * Mailbox, Typ, Ziel, Termin. Was die Wirkung sagt, steht versiegelt.
       */
      if (!db.objectStoreNames.contains(STORE_STEUER_AUFTRAEGE)) {
        const steuer = db.createObjectStore(STORE_STEUER_AUFTRAEGE, { keyPath: 'id' })
        steuer.createIndex('by_faellig', 'naechsterVersuch', { unique: false })
        steuer.createIndex('by_angelegt', 'angelegtAm', { unique: false })
        steuer.createIndex('by_ziel', 'ziel', { unique: false })
      }
      // Die vorhandene Tabelle aus Version 6 bekommt ihren Index nach. In
      // einer frisch angelegten steht er schon oben.
      const upgrade = req.transaction
      if (upgrade && db.objectStoreNames.contains(STORE_MEDIEN_TEILE)) {
        const teile = upgrade.objectStore(STORE_MEDIEN_TEILE)
        if (!teile.indexNames.contains('by_media')) {
          teile.createIndex('by_media', 'mediaId', { unique: false })
        }
      }
    }

    req.onsuccess = () => resolve(req.result)
    req.onerror = () => {
      dbPromise = null
      offenesKonto = null
      reject(req.error)
    }
  })
  return dbPromise
}

/**
 * Robust ISO timestamp parser.
 * Always normalizes missing timezone offsets to UTC ('Z') so timestamps
 * are never mistakenly parsed as local time by the browser.
 */
export function parseMessageTimestamp(isoString: string | undefined | null): number {
  if (!isoString) return 0
  let s = String(isoString).trim()
  s = s.replace(' ', 'T')
  if (!s.endsWith('Z') && !/[+-]\d{2}(?::?\d{2})?$/.test(s)) {
    s += 'Z'
  }
  const t = new Date(s).getTime()
  return isNaN(t) ? 0 : t
}

/**
 * Checks if a message is an unconfirmed / optimistic local message.
 */
/**
 * Wie ein Gespräch seine Zeilen zusammenführt.
 *
 * - `direkt`: die logische Kennung allein. In dieser Mailbox sitzen zwei
 *   Menschen; die Kopie des Gegenübers und die eigene Kopie derselben Nachricht
 *   tragen denselben `clientUuid`, aber einen anderen Absender. Schlüsselten sie
 *   über den Absender, blieben beide stehen — genau die Doppelung, die ein
 *   Platzhalter neben der echten Zeile erzeugte.
 * - `gruppe`: mit Absender, weil dort jede Nachricht ihren eigenen `clientUuid`
 *   hat und zwei Menschen nie dieselbe logische Kennung tragen dürfen.
 */
export type VerlaufsArt = 'direkt' | 'gruppe'

/**
 * Der Schlüssel, unter dem zwei Zeilen dieselbe Nachricht sind.
 *
 * Ohne logische Kennung bleibt nur die Umschlagkennung (`#<id>`): mehr weiß
 * dieser Stand über die Zeile dann nicht.
 */
export function verlaufsSchluessel(
  m: { clientUuid?: string; senderId?: number; id?: number },
  art: VerlaufsArt = 'direkt',
): string {
  if (m.clientUuid) {
    return art === 'gruppe' ? `${m.senderId ?? 0}:u:${m.clientUuid}` : `u:${m.clientUuid}`
  }
  return `#${m.id}`
}

/**
 * Der Text, den ein früherer Stand für „konnte ich nicht öffnen" schrieb.
 *
 * Er ist ein Satz, keine Nachricht: keine Anhänge, nicht von mir, nicht
 * bearbeitet, nicht gelöscht. Eine echte Nachricht mit genau diesem Wortlaut
 * bleibt damit erhalten — deshalb wird ein Platzhalter auch nur dann gelöscht,
 * wenn die echte Zeile mit derselben logischen Kennung daneben liegt.
 */
function platzhalterTexte(): Set<string> {
  const texte = new Set<string>()
  const merke = (schluessel: string) => {
    try {
      const wert = i18n.t(schluessel)
      if (typeof wert === 'string' && wert && wert !== schluessel) texte.add(wert)
    } catch {
      // Ohne Übersetzung bleibt die Liste, die sie hat.
    }
  }
  merke('messenger.encryptedMessage')
  for (const sprache of ['de', 'en']) {
    try {
      const fest = i18n.getFixedT(sprache)
      const wert = fest('messenger.encryptedMessage')
      if (typeof wert === 'string' && wert) texte.add(wert)
    } catch {
      // Sprache nicht geladen.
    }
  }
  return texte
}

/** Erkennt eine Zeile, die nur „Verschlüsselte Nachricht" sagt. */
export function istVerschluesselterPlatzhalter(m: {
  text?: string
  isSelf?: boolean
  isEdited?: boolean
  isDeleted?: boolean
  noteAttachment?: unknown
  calendarAttachment?: unknown
  imageAttachment?: unknown
  audioAttachment?: unknown
  fileAttachment?: unknown
  stickerAttachment?: unknown
  storyReply?: unknown
  videoNoteAttachment?: unknown
}): boolean {
  if (!m || typeof m.text !== 'string') return false
  if (m.isSelf || m.isEdited || m.isDeleted) return false
  for (const feld of [
    'noteAttachment',
    'calendarAttachment',
    'imageAttachment',
    'audioAttachment',
    'fileAttachment',
    'stickerAttachment',
    'storyReply',
    'videoNoteAttachment',
  ] as const) {
    if (m[feld]) return false
  }
  return platzhalterTexte().has(m.text)
}

/**
 * Wer von zwei Zeilen derselben Nachricht gewinnt.
 *
 * Die echte, entschlüsselte Zeile schlägt den Platzhalter — sonst bliebe die
 * Aussage „konnte ich nicht lesen" stehen, obwohl der Text daneben liegt. Unter
 * Gleichen gewinnt die bestätigte Fassung, sonst die frischere. Was nur der
 * Verlierer kennt (etwa ein Anhang), wird beim Zusammenführen übernommen.
 */
function gewinnt(
  neu: LocalStoredMessage,
  alt: LocalStoredMessage,
): boolean {
  const neuPlatzhalter = istVerschluesselterPlatzhalter(neu)
  const altPlatzhalter = istVerschluesselterPlatzhalter(alt)
  if (neuPlatzhalter !== altPlatzhalter) return !neuPlatzhalter
  return !isOptimisticMessage(neu) || isOptimisticMessage(alt)
}

export function isOptimisticMessage(m: { id?: number; status?: string }): boolean {
  // `failed` gehört dazu: der Server hat die Nachricht nie bestätigt, sie hat
  // also keine echte Kennung und keinen Platz zwischen den bestätigten Zeilen.
  return !m.id || m.id >= 1e11 || m.status === 'queued' || m.status === 'failed'
}

/**
 * Sorts messages strictly and monotonically:
 * 1. Server-confirmed messages always come before unconfirmed/optimistic messages.
 * 2. Confirmed messages are ordered primarily by monotonic server ID and normalized timestamps.
 * 3. Optimistic messages are ordered by their local creation timestamp at the end of the timeline.
 */
export function sortMessagesChronologically<T extends { id: number; createdAt: string; clientUuid?: string; status?: string }>(
  msgs: T[]
): T[] {
  return [...msgs].sort((a, b) => {
    const isAOpt = isOptimisticMessage(a)
    const isBOpt = isOptimisticMessage(b)

    // Confirmed messages before optimistic messages
    if (isAOpt !== isBOpt) {
      return isAOpt ? 1 : -1
    }

    if (!isAOpt) {
      const tA = parseMessageTimestamp(a.createdAt)
      const tB = parseMessageTimestamp(b.createdAt)
      // If timestamps differ by more than 60 seconds, use timestamp
      if (Math.abs(tA - tB) > 60_000) {
        return tA - tB
      }
      // Within the same minute / conversational flow, server monotonic ID is the authoritative order
      if (a.id && b.id && a.id !== b.id) {
        return a.id - b.id
      }
      return tA - tB
    }

    // Both optimistic: sort by local timestamp
    const tA = parseMessageTimestamp(a.createdAt) || (a.id || 0)
    const tB = parseMessageTimestamp(b.createdAt) || (b.id || 0)
    return tA - tB
  })
}

/**
 * Legt den Klartext eines Umschlags ab — und **wirft**, wenn das misslingt.
 *
 * Der einzige Schreibweg dieser Datei, der Fehler nicht schluckt, und das mit
 * Absicht: der Aufrufer schreibt hier, bevor er den fortgeschriebenen
 * Ratchet-Zustand speichert. Schlägt es fehl, muss der ganze Schritt scheitern,
 * damit derselbe Umschlag beim nächsten Durchlauf erneut aufgeht. Ein stilles
 * `catch` an dieser Stelle verlöre Nachrichten, ohne dass es jemand merkt.
 */
export async function speichereUmschlagKlartext(
  blindMailboxId: string,
  envelopeId: number,
  plain: string
): Promise<void> {
  const db = await openLocalDatabase()
  // Versiegeln vor der Transaktion — und ein gesperrter Messenger wirft hier,
  // wie er soll: scheitert dieser Schritt, darf der Ratchet-Schlüssel nicht
  // als verbraucht gelten.
  const zeile = await versiegleZeile(
    {
      blindMailboxId,
      envelopeId,
      plain,
      gespeichertAm: new Date().toISOString(),
    },
    ['blindMailboxId', 'envelopeId'],
    klartextAad(blindMailboxId, envelopeId),
  )
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_KLARTEXTE, 'readwrite')
    tx.objectStore(STORE_KLARTEXTE).put(zeile)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Bindet einen Entwurf an seinen Chat. */
function entwurfAad(blindMailboxId: string): string {
  return `msm-entwurf:${blindMailboxId}`
}

/** Bindet eine Funkenakte an ihren Kontakt: getauschte Zeilen gehen nicht auf. */
function funkenAad(partnerId: number): string {
  return `msm-funke:${partnerId}`
}

/**
 * Legt die Funkenakte eines Kontakts ab.
 *
 * `partnerId` bleibt als Schlüssel lesbar. Wer die Platte kopiert, sieht also,
 * mit welchen Konten dieses Gerät eine Akte führt — nicht, ob überhaupt ein
 * Funke läuft und wie lang er ist. Dieselbe Abwägung wie bei der Mailbox einer
 * Nachricht.
 */
export async function speichereFunkenAkte(akte: { partnerId: number } & Record<string, unknown>): Promise<void> {
  if (!Number.isSafeInteger(akte.partnerId) || akte.partnerId <= 0) return
  const zeile = await versiegleZeile(akte, ['partnerId'], funkenAad(akte.partnerId))
  const db = await openLocalDatabase()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_FUNKEN, 'readwrite')
    tx.objectStore(STORE_FUNKEN).put(zeile)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/**
 * Alle Funkenakten dieses Kontos, entsiegelt und ungeprüft.
 *
 * Ungeprüft heißt: der Aufrufer liest sie über `leseAkte`, das Kaputtes
 * auslässt. Eine Zeile, die nicht aufgeht, fehlt hier einfach.
 */
export async function ladeFunkenAkten(): Promise<Record<string, unknown>[]> {
  const db = await openLocalDatabase()
  const rohe = await new Promise<Record<string, any>[]>((resolve, reject) => {
    const tx = db.transaction(STORE_FUNKEN, 'readonly')
    const req = tx.objectStore(STORE_FUNKEN).getAll()
    req.onsuccess = () => resolve((req.result || []) as Record<string, any>[])
    req.onerror = () => reject(req.error)
  })
  return entsiegleZeilen<Record<string, unknown>>(rohe, (z) => funkenAad(Number(z.partnerId)))
}

/**
 * Legt den ungesendeten Text eines Chats ab — versiegelt wie alles andere.
 *
 * Ein leerer Entwurf wird gelöscht statt leer gespeichert: eine Zeile, die nur
 * sagt „hier wurde mal etwas getippt und wieder verworfen", ist ein Hinweis,
 * den niemand braucht.
 */
export async function speichereEntwurf(blindMailboxId: string, text: string): Promise<void> {
  if (!blindMailboxId) return
  const db = await openLocalDatabase()
  if (!text.trim()) {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_ENTWUERFE, 'readwrite')
      tx.objectStore(STORE_ENTWUERFE).delete(blindMailboxId)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
    return
  }
  const zeile = await versiegleZeile(
    { blindMailboxId, text, gespeichertAm: new Date().toISOString() },
    ['blindMailboxId'],
    entwurfAad(blindMailboxId),
  )
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_ENTWUERFE, 'readwrite')
    tx.objectStore(STORE_ENTWUERFE).put(zeile)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Holt den Entwurf eines Chats. Leerer String heißt „keiner". */
export async function ladeEntwurf(blindMailboxId: string): Promise<string> {
  if (!blindMailboxId) return ''
  const db = await openLocalDatabase()
  const roh = await new Promise<any>((resolve, reject) => {
    const tx = db.transaction(STORE_ENTWUERFE, 'readonly')
    const req = tx.objectStore(STORE_ENTWUERFE).get(blindMailboxId)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
  if (!roh) return ''
  try {
    const offen = await entsiegleZeile(roh, entwurfAad(blindMailboxId))
    return typeof offen?.text === 'string' ? offen.text : ''
  } catch {
    // Gesperrt oder mit einem anderen Schlüssel abgelegt. Ein unlesbarer
    // Entwurf ist kein Fehler, er ist eben keiner.
    return ''
  }
}

/** Alle Chats mit Entwurf, für die Vorschau in der Liste. */
export async function ladeAlleEntwuerfe(): Promise<Record<string, string>> {
  const db = await openLocalDatabase()
  const rohe = await new Promise<any[]>((resolve, reject) => {
    const tx = db.transaction(STORE_ENTWUERFE, 'readonly')
    const req = tx.objectStore(STORE_ENTWUERFE).getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })
  const ergebnis: Record<string, string> = {}
  for (const roh of rohe) {
    const mid = roh?.blindMailboxId
    if (!mid) continue
    try {
      const offen = await entsiegleZeile(roh, entwurfAad(mid))
      if (typeof offen?.text === 'string' && offen.text.trim()) ergebnis[mid] = offen.text
    } catch {
      // siehe `ladeEntwurf`
    }
  }
  return ergebnis
}

/**
 * Nimmt den Inhalt eines geöffneten Umschlags heraus, ohne die Zeile zu löschen.
 *
 * Gebraucht beim Löschen einer Nachricht: Hier liegt die Fassung, aus der ein
 * späterer Abruf sie wieder aufbaut. Bliebe sie stehen, käme die gelöschte
 * Nachricht beim nächsten Öffnen des Gesprächs zurück.
 *
 * Die Zeile selbst muss bleiben. Sie ist zugleich die Marke „dieser Umschlag
 * ist geöffnet", und ein Nachrichtenschlüssel des Double Ratchet ist nach dem
 * ersten Öffnen verbraucht: ohne die Marke liefe der Ratchet ein zweites Mal
 * über denselben Umschlag und bräche die Sitzung. Der leere Text ist dafür
 * ausreichend, die Prüfung fragt auf `null` (`ratchetSitzung.ts`).
 *
 * Gab es zu diesem Umschlag noch keine Zeile — etwa beim Absender, der seine
 * eigene Ratchet-Nachricht nie geöffnet hat —, entsteht hier eine leere. Das
 * schadet nicht: sie markiert einen Umschlag, der ohnehin verschwindet.
 */
export async function leereUmschlagKlartext(
  blindMailboxId: string,
  envelopeId: number
): Promise<void> {
  await speichereUmschlagKlartext(blindMailboxId, envelopeId, '')
}

/**
 * Der Klartext **eines** Umschlags — und der Fehlerfall bleibt ein Fehler.
 *
 * `ladeUmschlagKlartexte` darf eine leere Map liefern, wenn die Ablage streikt:
 * dort ist das Ergebnis eine Abkürzung, und wer sie nicht bekommt, entschlüsselt
 * eben noch einmal. Hier ist es umgekehrt. Diese Abfrage läuft im
 * Sitzungsschloss und entscheidet, ob ein Nachrichtenschlüssel verbraucht wird.
 * Ein verschluckter Fehler sähe aus wie „noch nicht geöffnet", der Ratchet liefe
 * ein zweites Mal über denselben Umschlag, und das Ergebnis wäre genau der
 * Sitzungsbruch, den diese Abfrage verhindern soll.
 */
export async function leseUmschlagKlartext(
  blindMailboxId: string,
  envelopeId: number
): Promise<string | null> {
  if (!blindMailboxId) return null
  const db = await openLocalDatabase()
  const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
    const tx = db.transaction(STORE_KLARTEXTE, 'readonly')
    const req = tx.objectStore(STORE_KLARTEXTE).get([blindMailboxId, envelopeId])
    req.onsuccess = () => resolve(req.result ?? null)
    req.onerror = () => reject(req.error)
  })
  const zeile = await entsiegleZeile<{ plain?: unknown }>(
    roh,
    klartextAad(blindMailboxId, envelopeId),
  )
  return typeof zeile?.plain === 'string' ? zeile.plain : null
}

/** Alle bereits geöffneten Umschläge einer Mailbox, nach Umschlagkennung. */
export async function ladeUmschlagKlartexte(
  blindMailboxId: string
): Promise<Map<number, string>> {
  const treffer = new Map<number, string>()
  if (!blindMailboxId) return treffer
  try {
    const db = await openLocalDatabase()
    const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
      const tx = db.transaction(STORE_KLARTEXTE, 'readonly')
      const req = tx.objectStore(STORE_KLARTEXTE).getAll(
        IDBKeyRange.bound([blindMailboxId, -Infinity], [blindMailboxId, Infinity])
      )
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })
    const offen = await entsiegleZeilen<{ envelopeId: number; plain: string }>(
      rohzeilen,
      (z) => klartextAad(blindMailboxId, z.envelopeId),
    )
    for (const e of offen) {
      if (typeof e.plain === 'string') treffer.set(e.envelopeId, e.plain)
    }
    return treffer
  } catch {
    return treffer
  }
}

// ==========================================
// Die Anhänge
// ==========================================

/**
 * Bindet einen Anhang an seinen Platz. Siehe `versiegleZeile`.
 *
 * Nur die Medienkennung bleibt im Klartext — sie ist der Schlüssel der Zeile
 * und sagt nichts über den Inhalt aus. Dateiname und Dateityp stehen im
 * versiegelten Manifest des Pakets und wandern hier nicht noch einmal mit.
 */
function mediumAad(mediaId: string): string {
  return `msm-medium:${mediaId}`
}

/** Bindet ein angekommenes Stueck an seine Uebertragung und Position. */
function medienTeilAad(transferId: string, teil: number): string {
  return `msm-medienteil:${transferId}:${teil}`
}

/**
 * Legt einen entschlüsselten Anhang dauerhaft ab.
 *
 * Das ist der Unterschied zwischen „angezeigt" und „da": bis 10/2026 lag ein
 * Bild nur im Arbeitsspeicher und war nach dem Neuladen weg. Der Server
 * behält den Blob 30 Tage, die Nachricht trägt nur einen Zeiger — nach Ablauf
 * gäbe es nichts mehr zu holen. Wer den Anhang einmal geholt hat, behält ihn.
 *
 * Der Inhalt ist der Klartext der Datei als data-URL. Er wird im Siegel der
 * Ablage abgelegt, also geschützt wie der Verlauf; ohne PIN gilt dieselbe Grenze
 * wie für den Verlauf.
 */
export async function speichereMedium(
  mediaId: string,
  klartext: string,
  name?: string | null,
): Promise<boolean> {
  if (!mediaId || !klartext) return false
  const db = await openLocalDatabase()
  // Versiegeln vor der Transaktion — ein `await` mitten drin bricht sie ab.
  const zeile = await versiegleZeile(
    { mediaId, klartext, name: name ?? null, gespeichertAm: new Date().toISOString() },
    ['mediaId'],
    mediumAad(mediaId),
  )
  return await new Promise<boolean>((resolve, reject) => {
    /*
     * Der Grabstein wird **in derselben** Transaktion gelesen wie geschrieben.
     *
     * Ein getrennter Blick davor waere ein Zeitfenster: Loeschen schreibt den
     * Grabstein, ein schon laufender Download legt danach seine Zeile ab, und
     * die geloeschte Datei ist wieder da. IndexedDB fuehrt Transaktionen mit
     * gemeinsamem Bereich nacheinander aus; damit entscheidet genau eine
     * Reihenfolge — loeschen oder speichern, nie beides.
     */
    const tx = db.transaction([STORE_MEDIEN, STORE_MEDIEN_GRABSTEINE], 'readwrite')
    const grabstein = tx.objectStore(STORE_MEDIEN_GRABSTEINE)
    const req = grabstein.get(mediaId)
    let geschrieben = false
    req.onsuccess = () => {
      // `== null` deckt beide Formen ab: die echte Ablage liefert `undefined`,
      // ein Test-Double ohne Treffer `null`.
      if (req.result == null) {
        tx.objectStore(STORE_MEDIEN).put(zeile)
        geschrieben = true
      }
    }
    req.onerror = () => reject(req.error)
    tx.oncomplete = () => resolve(geschrieben)
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/**
 * Der dauerhaft abgelegte Anhang — oder `null`, wenn es keinen gibt.
 *
 * Ein Fehler beim Lesen ist ein Fehler und kein „nichts da": die Anzeige
 * entscheidet danach, ob sie den Anhang noch holen muss oder gar nicht
 * mehr kann.
 */
export async function leseMedium(mediaId: string): Promise<string | null> {
  if (!mediaId) return null
  const db = await openLocalDatabase()
  const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
    const tx = db.transaction(STORE_MEDIEN, 'readonly')
    const req = tx.objectStore(STORE_MEDIEN).get(mediaId)
    req.onsuccess = () => resolve(req.result ?? null)
    req.onerror = () => reject(req.error)
  })
  if (!roh) return null
  const zeile = await entsiegleZeile<{ klartext?: unknown }>(roh, mediumAad(mediaId))
  return typeof zeile?.klartext === 'string' ? zeile.klartext : null
}

/** Ein Stueck einer Medienuebertragung, so wie es durchs Relais kam. */
export interface MedienTeil {
  transferId: string
  mediaId: string
  name: string | null
  teil: number
  teile: number
  sha256: string
  groesse: number
  daten: string
}

/**
 * Ein Stueck ablegen — versiegelt wie alles andere in dieser Ablage.
 *
 * Dieselbe Position zweimal (die Wiederholung eines Laufs) ersetzt nur sich
 * selbst. Erst wenn alle Teile da sind, entsteht daraus ein Anhang.
 */
export async function legeMedienTeilAb(teil: MedienTeil): Promise<void> {
  if (!teil?.transferId || !teil.mediaId || !Number.isInteger(teil.teil)) return
  const db = await openLocalDatabase()
  const id = `${teil.transferId}:${teil.teil}`
  const zeile = await versiegleZeile(
    { ...teil, id },
    ['id', 'transferId'],
    medienTeilAad(teil.transferId, teil.teil),
  )
  await new Promise<void>((resolve, reject) => {
    // Dieselbe Regel wie beim fertigen Anhang: eine geloeschte Kennung bekommt
    // auch kein Stueck mehr. Sonst fuellte ein spaeter Transfer das Staging
    // wieder, aus dem beim naechsten Teil ein Eintrag in `medien` wuerde.
    const tx = db.transaction([STORE_MEDIEN_TEILE, STORE_MEDIEN_GRABSTEINE], 'readwrite')
    const grabstein = tx.objectStore(STORE_MEDIEN_GRABSTEINE)
    const req = grabstein.get(teil.mediaId)
    req.onsuccess = () => {
      if (req.result == null) tx.objectStore(STORE_MEDIEN_TEILE).put(zeile)
    }
    req.onerror = () => reject(req.error)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Alle angekommenen Stuecke einer Uebertragung, nach Position sortiert. */
export async function holeMedienTeile(transferId: string): Promise<MedienTeil[]> {
  if (!transferId) return []
  const db = await openLocalDatabase()
  const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
    const tx = db.transaction(STORE_MEDIEN_TEILE, 'readonly')
    const index = tx.objectStore(STORE_MEDIEN_TEILE).index('by_transfer')
    const req = index.getAll(IDBKeyRange.only(transferId))
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })
  const teile = await entsiegleZeilen<MedienTeil>(rohzeilen, (z) =>
    medienTeilAad(String(z.transferId), Number(z.teil)),
  )
  return teile
    .filter((t) => typeof t?.daten === 'string' && Number.isInteger(Number(t.teil)))
    .sort((a, b) => a.teil - b.teil)
}

/** Die Stuecke einer Uebertragung wieder wegwerfen (fertig oder unbrauchbar). */
export async function loescheMedienTeile(transferId: string): Promise<void> {
  if (!transferId) return
  const db = await openLocalDatabase()
  const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
    const tx = db.transaction(STORE_MEDIEN_TEILE, 'readonly')
    const index = tx.objectStore(STORE_MEDIEN_TEILE).index('by_transfer')
    const req = index.getAll(IDBKeyRange.only(transferId))
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })
  if (rohzeilen.length === 0) return
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_MEDIEN_TEILE, 'readwrite')
    for (const zeile of rohzeilen) tx.objectStore(STORE_MEDIEN_TEILE).delete(zeile.id)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Haelt die Zahl der Grabsteine klein. Nur die echte Ablage kennt `count`. */
function begrenzeGrabsteine(store: IDBObjectStore): void {
  if (typeof store.count !== 'function') return
  const zahl = store.count()
  zahl.onsuccess = () => {
    const zuViel = zahl.result - GRABSTEINE_MAX
    if (zuViel <= 0) return
    const alle = store.index('by_geloescht').getAll()
    alle.onsuccess = () => {
      for (const alt of ((alle.result || []) as { mediaId?: string }[]).slice(0, zuViel)) {
        if (alt?.mediaId) store.delete(alt.mediaId)
      }
    }
  }
}

/**
 * Setzt die inhaltslose Marke „diese Medienkennung ist geloescht".
 *
 * Sie ist der einzige Schutz gegen Wiederauferstehen: ein Download, ein
 * Historientransfer oder ein Stueck aus dem Staging, das nach dem Loeschen
 * eintrifft, findet die Marke und legt nichts mehr an. Inhalt steht nicht
 * darin — nur die Kennung und der Zeitpunkt.
 */
export async function legeMedienGrabstein(mediaId: string): Promise<void> {
  if (!mediaId) return
  const db = await openLocalDatabase()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_MEDIEN_GRABSTEINE, 'readwrite')
    const store = tx.objectStore(STORE_MEDIEN_GRABSTEINE)
    store.put({ mediaId, geloeschtAm: new Date().toISOString() })
    begrenzeGrabsteine(store)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Traegt diese Medienkennung einen Grabstein? */
export async function istMediumGeloescht(mediaId: string): Promise<boolean> {
  if (!mediaId) return false
  const db = await openLocalDatabase()
  return await new Promise<boolean>((resolve, reject) => {
    const tx = db.transaction(STORE_MEDIEN_GRABSTEINE, 'readonly')
    const req = tx.objectStore(STORE_MEDIEN_GRABSTEINE).get(mediaId)
    req.onsuccess = () => resolve(Boolean(req.result))
    req.onerror = () => reject(req.error)
  })
}

/** Loescht jede Zeile eines Stores, deren Indexfeld den Wert traegt. */
function loescheUeberIndex(store: IDBObjectStore, indexName: string, wert: string): void {
  // Der Index fehlt nur in alten Test-Doubles; die echte Ablage hat ihn.
  if (!store.indexNames || !store.indexNames.contains(indexName)) return
  const req = store.index(indexName).getAll(IDBKeyRange.only(wert))
  req.onsuccess = () => {
    for (const zeile of ((req.result || []) as { id?: string }[])) {
      if (zeile?.id !== undefined) store.delete(zeile.id)
    }
  }
}

/**
 * Nimmt einen Anhang **ganz** aus der Ablage — und sperrt seine Kennung.
 *
 * Gehoert zum Loeschen einer Nachricht und zum Verfall. Vier Dinge gehen in
 * einer Transaktion:
 *
 * 1. der Grabstein, damit ein spaeter eintreffender Download oder Transfer
 *    nichts mehr anlegt,
 * 2. der Klartext in `medien`,
 * 3. angefangene Stuecke in `medien_teile`,
 * 4. ein offener Nachholauftrag.
 *
 * Eine Transaktion, weil ein halber Zustand genau die Wiederauferstehung
 * waere, gegen die diese Funktion geschrieben ist: Klartext weg, Auftrag
 * stehen gelassen, und der naechste Anlauf holt die Datei zurueck.
 */
export async function loescheMedium(mediaId: string): Promise<void> {
  if (!mediaId) return
  const db = await openLocalDatabase()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(
      [STORE_MEDIEN, STORE_MEDIEN_TEILE, STORE_MEDIEN_AUFTRAEGE, STORE_MEDIEN_GRABSTEINE],
      'readwrite',
    )
    const grabsteine = tx.objectStore(STORE_MEDIEN_GRABSTEINE)
    grabsteine.put({ mediaId, geloeschtAm: new Date().toISOString() })
    begrenzeGrabsteine(grabsteine)
    tx.objectStore(STORE_MEDIEN).delete(mediaId)
    loescheUeberIndex(tx.objectStore(STORE_MEDIEN_TEILE), 'by_media', mediaId)
    loescheUeberIndex(tx.objectStore(STORE_MEDIEN_AUFTRAEGE), 'by_media', mediaId)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

// ==========================================
// Die Nachholauftraege
// ==========================================

/**
 * Ein Anhang, der beim Verarbeiten nicht zu holen war.
 *
 * Der Zeiger und die Bindung stehen hier noch einmal, weil die Nachricht, aus
 * der sie stammen, laengst quittiert und aus dem Postfach verschwunden sein
 * kann. Ohne diese Kopie gaebe es keinen zweiten Anlauf.
 */
export interface MedienNachholauftrag {
  /** `${mailbox}:${mediaId}` — eine Zeile je Anhang und Gespraech. */
  id: string
  mailbox: string
  mediaId: string
  paketSchluessel: string
  fileId: string
  absenderId: number
  name: string | null
  versuche: number
  naechsterVersuch: number
  angelegtAm: number
}

/** So viele offene Auftraege bleiben; die aeltesten fallen. */
const MEDIEN_AUFTRAEGE_MAX = 200

function medienAuftragAad(id: string): string {
  return `msm-medienauftrag:${id}`
}

/** Der Auftrag ist versiegelt wie der Verlauf: Name und Schluessel sind Inhalt. */
const AUFTRAG_KLARFELDER = ['id', 'mailbox', 'mediaId', 'naechsterVersuch', 'angelegtAm'] as const

/** Haelt die Warteschlange klein. Nur die echte Ablage kennt `count`. */
function begrenzeAuftraege(store: IDBObjectStore): void {
  if (typeof store.count !== 'function') return
  const zahl = store.count()
  zahl.onsuccess = () => {
    const zuViel = zahl.result - MEDIEN_AUFTRAEGE_MAX
    if (zuViel <= 0) return
    const alle = store.index('by_angelegt').getAll()
    alle.onsuccess = () => {
      for (const alt of ((alle.result || []) as { id?: string }[]).slice(0, zuViel)) {
        if (alt?.id) store.delete(alt.id)
      }
    }
  }
}

/**
 * Legt einen Auftrag an — **idempotent**.
 *
 * Gibt es ihn schon, bleibt der alte stehen. Sonst setzte jeder Fuenf-Sekunden-
 * Takt die Versuche zurueck und den naechsten Versuch nach vorn: der Anhang
 * wuerde nie wieder versucht, weil er immer „gerade eben gescheitert" waere.
 */
export async function legeMedienAuftragAn(auftrag: MedienNachholauftrag): Promise<void> {
  if (!auftrag?.id || !auftrag.mailbox || !auftrag.mediaId) return
  const db = await openLocalDatabase()
  const zeile = await versiegleZeile(
    auftrag,
    AUFTRAG_KLARFELDER,
    medienAuftragAad(auftrag.id),
  )
  await new Promise<void>((resolve, reject) => {
    // Ein geloeschter Anhang bekommt keinen Auftrag. Die Pruefung steht in
    // derselben Transaktion wie das Schreiben: zwischen einem Blick davor und
    // dem Ablegen kann die Loeschung eintreffen.
    const tx = db.transaction([STORE_MEDIEN_AUFTRAEGE, STORE_MEDIEN_GRABSTEINE], 'readwrite')
    const store = tx.objectStore(STORE_MEDIEN_AUFTRAEGE)
    const grabstein = tx.objectStore(STORE_MEDIEN_GRABSTEINE).get(auftrag.mediaId)
    grabstein.onsuccess = () => {
      if (grabstein.result != null) return
      const vorhanden = store.get(auftrag.id)
      vorhanden.onsuccess = () => {
        if (vorhanden.result != null) return
        store.put(zeile)
        begrenzeAuftraege(store)
      }
      vorhanden.onerror = () => reject(vorhanden.error)
    }
    grabstein.onerror = () => reject(grabstein.error)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Schreibt den fortgeschriebenen Stand eines Auftrags (Versuche, naechster Termin). */
export async function schreibeMedienAuftrag(auftrag: MedienNachholauftrag): Promise<void> {
  if (!auftrag?.id) return
  const db = await openLocalDatabase()
  const zeile = await versiegleZeile(
    auftrag,
    AUFTRAG_KLARFELDER,
    medienAuftragAad(auftrag.id),
  )
  await new Promise<void>((resolve, reject) => {
    // Kein Fortschreiben an einer geloeschten Medienkennung vorbei: sonst
    // legte ein Lauf, der waehrend der Loeschung scheitert, den Auftrag wieder
    // an, den `loescheMedium` gerade entfernt hat.
    const tx = db.transaction([STORE_MEDIEN_AUFTRAEGE, STORE_MEDIEN_GRABSTEINE], 'readwrite')
    const grabstein = tx.objectStore(STORE_MEDIEN_GRABSTEINE).get(auftrag.mediaId)
    grabstein.onsuccess = () => {
      if (grabstein.result == null) tx.objectStore(STORE_MEDIEN_AUFTRAEGE).put(zeile)
    }
    grabstein.onerror = () => reject(grabstein.error)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Alle offenen Auftraege dieses Kontos, entsiegelt und ungeprueft. */
export async function ladeMedienAuftraege(): Promise<MedienNachholauftrag[]> {
  try {
    const db = await openLocalDatabase()
    const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
      const tx = db.transaction(STORE_MEDIEN_AUFTRAEGE, 'readonly')
      const req = tx.objectStore(STORE_MEDIEN_AUFTRAEGE).getAll()
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })
    const offen = await entsiegleZeilen<MedienNachholauftrag>(rohzeilen, (z) =>
      medienAuftragAad(String(z.id)),
    )
    return offen.filter(
      (a) =>
        typeof a?.id === 'string' &&
        typeof a.mailbox === 'string' &&
        typeof a.mediaId === 'string' &&
        typeof a.paketSchluessel === 'string' &&
        typeof a.fileId === 'string' &&
        Number.isInteger(a.absenderId) &&
        Number.isInteger(a.versuche) &&
        Number.isFinite(a.naechsterVersuch),
    )
  } catch {
    // Gesperrt oder Ablage weg: es gibt nichts nachzuholen. Der naechste
    // Anlauf nach dem Entsperren liest wieder.
    return []
  }
}

export async function loescheMedienAuftrag(id: string): Promise<void> {
  if (!id) return
  const db = await openLocalDatabase()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_MEDIEN_AUFTRAEGE, 'readwrite')
    tx.objectStore(STORE_MEDIEN_AUFTRAEGE).delete(id)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

// ==========================================
// Die Outbox der Steuerwirkungen
// ==========================================

/**
 * Bindet einen Auftrag an seine Kennung.
 *
 * Die Kennung ist zugleich `client_uuid` des Steuerpakets; sie steht im
 * Klartext, weil sie den Index bildet. Sie ist eine Zufallskennung ohne Bezug
 * zum Inhalt — dieselbe Abwägung wie bei `clientUuid` einer Nachricht.
 */
function steuerAuftragAad(id: string): string {
  return `msm-steuerauftrag:${id}`
}

/** Lesbar bleiben nur die Felder, die einen Index tragen oder den Termin. */
const STEUER_KLARFELDER = [
  'id',
  'mailbox',
  'typ',
  'ziel',
  'zielId',
  'naechsterVersuch',
  'angelegtAm',
] as const

/**
 * Legt einen Auftrag an — und weist ab, wenn die Warteschlange voll ist.
 *
 * **Nicht** idempotent gedacht: die Kennung entsteht beim Einreihen und wird
 * danach nie wiederverwendet. Ein zweiter Anlauf desselben Auftrags schreibt
 * über `schreibeSteuerAuftrag`, damit Versuche und Termin nicht zurückspringen.
 *
 * Gibt `false` zurück, wenn die Grenze erreicht ist. Der Aufrufer nimmt dann
 * seine lokale Wirkung zurück und sagt es — ältere Aufträge still zu verdrängen
 * wäre genau das stille Aufgeben, gegen das diese Outbox geschrieben ist.
 */
export async function legeSteuerAuftragAn(auftrag: SteuerAuftrag): Promise<boolean> {
  if (!auftrag?.id || !auftrag.mailbox) return false
  const db = await openLocalDatabase()
  const zeile = await versiegleZeile(auftrag, STEUER_KLARFELDER, steuerAuftragAad(auftrag.id))
  return await new Promise<boolean>((resolve, reject) => {
    const tx = db.transaction(STORE_STEUER_AUFTRAEGE, 'readwrite')
    const store = tx.objectStore(STORE_STEUER_AUFTRAEGE)
    let angenommen = true
    const vorhanden = store.get(auftrag.id)
    vorhanden.onsuccess = () => {
      if (vorhanden.result != null) return
      const zahl = store.count()
      zahl.onsuccess = () => {
        if (zahl.result >= MAX_AUFTRAEGE) {
          angenommen = false
          return
        }
        store.put(zeile)
      }
      zahl.onerror = () => reject(zahl.error)
    }
    vorhanden.onerror = () => reject(vorhanden.error)
    tx.oncomplete = () => resolve(angenommen)
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/** Schreibt den fortgeschriebenen Stand (Versuche, nächster Termin). */
export async function schreibeSteuerAuftrag(auftrag: SteuerAuftrag): Promise<void> {
  if (!auftrag?.id) return
  const db = await openLocalDatabase()
  const zeile = await versiegleZeile(auftrag, STEUER_KLARFELDER, steuerAuftragAad(auftrag.id))
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_STEUER_AUFTRAEGE, 'readwrite')
    tx.objectStore(STORE_STEUER_AUFTRAEGE).put(zeile)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/**
 * Alle Aufträge dieses Kontos, älteste zuerst.
 *
 * Ein Lesefehler ist hier kein Fehler: gesperrt oder Ablage weg heißt „es gibt
 * gerade nichts abzuarbeiten", und der nächste Anlass liest wieder. Was nicht
 * aufgeht (fremder Schlüssel, verbogene Zeile), bleibt ebenfalls liegen.
 */
export async function ladeSteuerAuftraege(): Promise<SteuerAuftrag[]> {
  try {
    const db = await openLocalDatabase()
    const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
      const tx = db.transaction(STORE_STEUER_AUFTRAEGE, 'readonly')
      const req = tx.objectStore(STORE_STEUER_AUFTRAEGE).getAll()
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })
    const offen = await entsiegleZeilen<SteuerAuftrag>(rohzeilen, (z) =>
      steuerAuftragAad(String(z.id)),
    )
    return offen
      .filter(
        (a) =>
          typeof a?.id === 'string' &&
          typeof a.mailbox === 'string' &&
          typeof a.typ === 'string' &&
          Number.isInteger(a.versuche) &&
          Number.isFinite(a.naechsterVersuch) &&
          (a.art === 'direkt' || a.art === 'gruppe'),
      )
      .sort((a, b) => a.angelegtAm - b.angelegtAm)
  } catch {
    return []
  }
}

export async function loescheSteuerAuftrag(id: string): Promise<void> {
  if (!id) return
  const db = await openLocalDatabase()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_STEUER_AUFTRAEGE, 'readwrite')
    tx.objectStore(STORE_STEUER_AUFTRAEGE).delete(id)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

/**
 * Räumt ausstehende Wirkungen zu einer gelöschten Nachricht ab.
 *
 * Eine Bearbeitung oder Reaktion, die noch in der Warteschlange steht, würde
 * eine inzwischen gelöschte Nachricht bei der Gegenseite wieder aufleben
 * lassen: sie trägt deren Kennung und käme nach der Löschung an. Deshalb fällt
 * beim lokalen Löschen alles weg, was sich auf diese Nachricht bezieht —
 * **außer** einer Löschung selbst. Die muss weitergehen, auch wenn sie noch
 * nicht hinaus ist.
 *
 * Ein Fehler hier ist kein Fehler des Löschens: die Nachricht ist lokal weg,
 * und ein liegengebliebener Auftrag scheitert später an einer Nachricht, die
 * es nicht mehr gibt.
 */
export async function loescheSteuerAuftraegeFuerZiel(
  mailbox: string,
  kennung: { clientUuid?: string; id?: number },
): Promise<number> {
  if (!mailbox) return 0
  if (!kennung.clientUuid && typeof kennung.id !== 'number') return 0
  try {
    const db = await openLocalDatabase()
    return await new Promise<number>((resolve, reject) => {
      const tx = db.transaction(STORE_STEUER_AUFTRAEGE, 'readwrite')
      const store = tx.objectStore(STORE_STEUER_AUFTRAEGE)
      let entfernt = 0
      const req = store.getAll()
      req.onsuccess = () => {
        for (const zeile of (req.result || []) as Record<string, any>[]) {
          if (zeile?.mailbox !== mailbox) continue
          if (zeile?.typ === 'delete_message') continue
          const trifft =
            (kennung.clientUuid !== undefined && zeile?.ziel === kennung.clientUuid) ||
            (typeof kennung.id === 'number' && zeile?.zielId === kennung.id)
          if (!trifft) continue
          store.delete(zeile.id)
          entfernt++
        }
      }
      req.onerror = () => reject(req.error)
      tx.oncomplete = () => resolve(entfernt)
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  } catch {
    return 0
  }
}

/**
 * Alle abgelegten Anhänge — für den Datenexport.
 *
 * Was hier liegt, gehört dem Nutzer: es ist sein Bild, seine Sprachnachricht.
 * Ein Export, der den Verlauf mitnimmt und die Anhänge weglässt, wäre
 * unvollständig, ohne es zu sagen.
 */
export async function listeMedien(): Promise<
  { mediaId: string; name: string | null; klartext: string }[]
> {
  const leere: { mediaId: string; name: string | null; klartext: string }[] = []
  try {
    const db = await openLocalDatabase()
    const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
      const tx = db.transaction(STORE_MEDIEN, 'readonly')
      const req = tx.objectStore(STORE_MEDIEN).getAll()
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })
    const offen = await entsiegleZeilen<{ mediaId: string; name: string | null; klartext: string }>(
      rohzeilen,
      (z) => mediumAad(String(z.mediaId)),
    )
    return offen.filter((z) => typeof z.klartext === 'string')
  } catch {
    return leere
  }
}

/**
 * Führt den lokalen Verlauf mit dem zusammen, was dieser Durchlauf entschlüsselt
 * hat.
 *
 * Seit dem Umstieg auf den Double Ratchet ist dieser Speicher nicht mehr nur ein
 * Zwischenspeicher, sondern die einzige Quelle des eigenen Gesprächsanteils: der
 * Absender kann seine eigene Ratchet-Nachricht nicht entschlüsseln — das ist der
 * Sinn der Sache. Würde ein Abruf den Verlauf wie früher **ersetzen**, wäre alles
 * selbst Geschriebene beim nächsten Abruf verschwunden.
 *
 * Zusammengeführt wird über die logische Kennung (`clientUuid`), ersatzweise über
 * die Umschlagkennung. Bei einem Treffer gewinnt der frischere Eintrag: ein vom
 * Server bestätigter schlägt einen optimistischen, sonst der zuletzt gelesene.
 * Felder, die nur die alte Fassung kennt — etwa ein Anhang, den dieser Durchlauf
 * nicht mitgelesen hat —, bleiben erhalten.
 *
 * **Seit 10/2026 ohne Absender im Schlüssel, wenn es ein Direktchat ist.** Die
 * Kopie des Gegenübers und die eigene Kopie derselben Nachricht tragen denselben
 * `clientUuid`, aber einen anderen `senderId`; mit dem Absender im Schlüssel
 * blieben beide stehen. Genau so überlebte der Platzhalter (Absender =
 * Gegenüber oder 0) neben der echten Zeile (Absender = Ich).
 */
export function mischeVerlauf(
  lokal: readonly LocalStoredMessage[],
  frisch: readonly LocalStoredMessage[],
  art: VerlaufsArt = 'direkt',
): LocalStoredMessage[] {
  const zusammen = new Map<string, LocalStoredMessage>()

  for (const m of lokal) zusammen.set(verlaufsSchluessel(m, art), m)

  for (const m of frisch) {
    const k = verlaufsSchluessel(m, art)
    const alt = zusammen.get(k)
    if (!alt) {
      zusammen.set(k, m)
      continue
    }
    // Die echte Zeile schlägt den Platzhalter, die bestätigte die optimistische.
    // Der Verlierer liefert nur, was der Gewinner nicht kennt.
    const sieger = gewinnt(m, alt) ? m : alt
    const verlierer = sieger === m ? alt : m
    zusammen.set(k, { ...verlierer, ...sieger })
  }

  return sortMessagesChronologically([...zusammen.values()])
}

/**
 * Loads cached decrypted messages for a given mailbox ID from IndexedDB.
 * Returns messages sorted chronologically (oldest to newest).
 */
export async function loadLocalMessages(blindMailboxId: string): Promise<LocalStoredMessage[]> {
  return await ladeLokaleNachrichten(blindMailboxId, false)
}

/**
 * Wie `loadLocalMessages`, aber ein Lesefehler wird geworfen.
 *
 * Der Verlaufsübergang darf einen fehlgeschlagenen Lesevorgang nicht für
 * „diese Mailbox ist leer" halten: sonst gingen leere Teile hinaus, das Ziel
 * quittierte sie, und der Verlauf galt als übergeben, obwohl nichts reiste.
 */
export async function loadLocalMessagesStrict(
  blindMailboxId: string,
): Promise<LocalStoredMessage[]> {
  return await ladeLokaleNachrichten(blindMailboxId, true)
}

async function ladeLokaleNachrichten(
  blindMailboxId: string,
  streng: boolean,
): Promise<LocalStoredMessage[]> {
  if (!blindMailboxId) return []
  try {
    const db = await openLocalDatabase()
    const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readonly')
      const store = tx.objectStore(STORE_MESSAGES)
      const index = store.index('by_mailbox')
      const range = IDBKeyRange.only(blindMailboxId)
      const req = index.getAll(range)
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })

    // Erst öffnen, dann filtern: `isSystem` und `status` liegen im versiegelten
    // Teil und stehen vor dem Entsiegeln nicht zur Verfügung.
    const rawMsgs = await entsiegleZeilen<LocalStoredMessage>(
      rohzeilen,
      (z) => nachrichtAad(blindMailboxId, z.id),
    )

    // Deduplicate by clientUuid per sender: if a confirmed server message exists with this clientUuid,
    // discard any optimistic leftover with the same clientUuid for that same sender.
    const confirmedUuids = new Set<string>()
    for (const m of rawMsgs) {
      if (m.clientUuid && !isOptimisticMessage(m)) {
        confirmedUuids.add(`${m.senderId ?? 0}:${m.clientUuid}`)
      }
    }
    const filtered = rawMsgs.filter((m) => {
      // Was hier herauskommt, muss eine Nachricht **sein** — sonst stirbt die
      // ganze Ansicht an einer einzigen Zeile.
      //
      // Nachgemessen am 21.09.2026: fünf von Hand verbogene Zeilen in der
      // IndexedDB (`text` fehlt, `text` ist ein Objekt) rissen den kompletten
      // Messenger in die Fehlergrenze — `msg.text.startsWith is not a
      // function` in `ChatMessageBubble`. Und weil die Zeilen auf der Platte
      // liegen, half der angebotene Rat „Neu laden" genau nie.
      //
      // Die Prüfung steht hier und nicht in der Blase: `msg.text` wird an
      // Dutzenden Stellen wie ein String behandelt. Eine Zeile, die das nicht
      // hergibt, gehört gar nicht erst herausgegeben. Eine defekte Zeile
      // kostet so eine Nachricht statt das Gespräch.
      if (!m || typeof m !== 'object') return false
      if (typeof m.id !== 'number' || !Number.isFinite(m.id)) return false
      if (typeof m.text !== 'string') return false
      if (typeof m.createdAt !== 'string') return false
      // Systemzeilen gehören nicht in den Verlauf. Gespeicherte gibt es
      // trotzdem: bis 09/2026 schrieb der Messenger sie mit, und bei jedem
      // Ladevorgang kamen sie zurück.
      if (m.isSystem) return false
      // Dasselbe für ein Steuerpaket, das keinen Zweig fand und als Nachricht
      // abgelegt wurde: der rohe JSON-Text bliebe sonst für immer stehen.
      if (istSteuerzeile(m.text)) return false
      // Der Platzhalter eines früheren Standes wird nicht mehr angezeigt. Er
      // bleibt liegen, solange es keine echte Zeile dazu gibt (siehe
      // `entfernePlatzhalterUndDubletten`) — gelöscht wird nur, was belegt
      // ersetzt ist.
      if (istVerschluesselterPlatzhalter(m)) return false
      if (
        m.clientUuid &&
        isOptimisticMessage(m) &&
        confirmedUuids.has(`${m.senderId ?? 0}:${m.clientUuid}`)
      ) {
        return false
      }
      return true
    })
    return sortMessagesChronologically(filtered)
  } catch (fehler) {
    if (streng) throw fehler
    return []
  }
}

/**
 * Saves a list of decrypted messages to the local IndexedDB store.
 * Existing entries with the same `[blindMailboxId, id]` are updated idempotently.
 *
 * ## Die optimistische Zeile geht dabei weg
 *
 * Eine gesendete Nachricht liegt zweimal in der Ablage: einmal unter der aus
 * der Uhr erfundenen Kennung, die sie beim Absenden bekam, und einmal unter
 * der echten Umschlagkennung, sobald der Server sie vergeben hat. Geschrieben
 * wird hier nur — die erste Fassung blieb also stehen.
 *
 * Solange beide da sind, fällt das nicht auf: `mischeVerlauf` wirft die
 * optimistische Fassung aus der Anzeige. Sie überlebt aber die bestätigte.
 * Rutscht der Umschlag aus dem Hundert-Umschläge-Fenster, ist die
 * optimistische Zeile die einzige, die dieses Gerät noch hat — und sie trägt
 * `status: 'queued'`. Gemessen am laufenden System hiess das: die älteste
 * Nachricht des Chats stand unter der neuesten, weil
 * `sortMessagesChronologically` Bestätigtes vor Optimistisches sortiert; das
 * Häkchen fehlte dauerhaft; und weil `mischeVerlauf` bei einer optimistischen
 * Zeile die Fassung aus dem Arbeitsspeicher vorzieht, nahm sie auch keine
 * Reaktion mehr an.
 *
 * Deshalb räumt jeder Schreibvorgang auf: kommt eine bestätigte Fassung
 * herein, verschwindet die optimistische Zeile mit derselben logischen
 * Kennung. Das heilt auch, was früher liegengeblieben ist.
 */
export async function saveLocalMessages(
  blindMailboxId: string,
  messages: LocalStoredMessage[],
  art: VerlaufsArt = 'direkt',
): Promise<void> {
  await speichereNachrichten(blindMailboxId, messages, art, false)
}

/**
 * Wie `saveLocalMessages`, aber ein Schreibfehler wird **geworfen**.
 *
 * Gebraucht an zwei Stellen, an denen ein stiller Fehlschlag eine Nachricht
 * kostet: die optimistische Zeile vor dem Versand (sonst geht eine Nachricht
 * hinaus, die der Absender nie wieder sieht) und der Empfangsweg vor dem
 * Transport-ACK (sonst löscht der Server den Auftrag, und der Klartext liegt
 * nur noch in einem Zwischenspeicher, den niemand mehr anzeigt).
 */
export async function saveLocalMessagesStrict(
  blindMailboxId: string,
  messages: LocalStoredMessage[],
  art: VerlaufsArt = 'direkt',
): Promise<void> {
  await speichereNachrichten(blindMailboxId, messages, art, true)
}

async function speichereNachrichten(
  blindMailboxId: string,
  messages: LocalStoredMessage[],
  art: VerlaufsArt,
  streng: boolean,
): Promise<void> {
  if (!blindMailboxId || !messages || messages.length === 0) return
  try {
    const db = await openLocalDatabase()

    /*
     * Eine Zeile je Nachricht — schon hier.
     *
     * Kam die echte Zeile und ihr Platzhalter in derselben Liste an (etwa nach
     * einer Übergabe von einem anderen Gerät), wurden bis 10/2026 beide
     * geschrieben: die Anzeige mischte sie zusammen, die Ablage behielt beide,
     * und der nächste Ladevorgang holte sie wieder herauf. Jetzt gewinnt die
     * echte Zeile, und was nur der Verlierer kennt, kommt mit.
     */
    const gewaehlt = new Map<string, LocalStoredMessage>()
    const ohneSchluessel: LocalStoredMessage[] = []
    for (const m of messages) {
      if (m.isSystem) continue
      if (!m.clientUuid) {
        ohneSchluessel.push(m)
        continue
      }
      const k = verlaufsSchluessel(m, art)
      const alt = gewaehlt.get(k)
      if (!alt) {
        gewaehlt.set(k, m)
      } else {
        const sieger = gewinnt(m, alt) ? m : alt
        const verlierer = sieger === m ? alt : m
        gewaehlt.set(k, { ...verlierer, ...sieger })
      }
    }
    const auswahl = [...gewaehlt.values(), ...ohneSchluessel]
    /** Je Schlüssel: die Kennung, die jetzt geschrieben wird. */
    const gewinnerId = new Map<string, number>()
    for (const [k, m] of gewaehlt) gewinnerId.set(k, m.id)

    // Versiegeln passiert vollständig vor der Transaktion. Siehe Dateikopf.
    let maxEnvelopeId = 0
    const zeilen: Record<string, unknown>[] = []
    for (const m of auswahl) {
      // Siehe `isSystem`: eine Meldung über die Sitzung dieses Geräts ist
      // kein Gesprächsinhalt und hat in der Ablage nichts verloren.
      zeilen.push(
        await versiegleZeile(
          { ...m, blindMailboxId },
          NACHRICHT_KLARFELDER,
          nachrichtAad(blindMailboxId, m.id),
        ),
      )
      // Only real server envelope IDs (< 1e11) advance lastSyncedEnvelopeId
      if (typeof m.id === 'number' && m.id > maxEnvelopeId && m.id < 1e11) {
        maxEnvelopeId = m.id
      }
    }

    /*
     * Welche Zeilen wegfallen, steht vor der Transaktion fest.
     *
     * Wie beim Versiegeln: eine IndexedDB-Transaktion überlebt kein `await`,
     * und ein `delete`, das erst im `onsuccess` einer Suche abgesetzt wird,
     * kommt je nach Browser zu spät. Gesucht wird über `by_mailbox` und nicht
     * über `by_client_uuid` — dieselbe logische Kennung liegt hier absichtlich
     * mehrfach, und der Index gäbe davon nur eine heraus.
     */
    const zuLoeschen: number[] = []
    if (gewaehlt.size > 0) {
      const rohzeilen = await new Promise<Record<string, any>[]>((resolve) => {
        const tx = db.transaction(STORE_MESSAGES, 'readonly')
        const req = tx
          .objectStore(STORE_MESSAGES)
          .index('by_mailbox')
          .getAll(IDBKeyRange.only(blindMailboxId))
        req.onsuccess = () => resolve((req.result || []) as Record<string, any>[])
        req.onerror = () => resolve([])
      })
      const vorhanden = await entsiegleZeilen<LocalStoredMessage>(
        rohzeilen,
        (z) => nachrichtAad(blindMailboxId, z.id)
      )
      for (const m of vorhanden) {
        if (m.blindMailboxId !== blindMailboxId) continue
        if (!m.clientUuid) continue
        const siegerId = gewinnerId.get(verlaufsSchluessel(m, art))
        if (siegerId === undefined || m.id === siegerId) continue
        // Dieselbe Nachricht, andere Zeile — gleich ob Platzhalter,
        // optimistisch oder eine zweite bestätigte Fassung: eine bleibt.
        zuLoeschen.push(m.id)
      }
    }

    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction([STORE_MESSAGES, STORE_MAILBOXES], 'readwrite')
      const msgStore = tx.objectStore(STORE_MESSAGES)
      const mbStore = tx.objectStore(STORE_MAILBOXES)

      for (const zeile of zeilen) msgStore.put(zeile)
      // Nach dem Schreiben: die bestätigte Fassung steht dann schon, und es
      // gibt keinen Augenblick, in dem die Nachricht gar nicht in der Ablage
      // liegt.
      for (const id of zuLoeschen) msgStore.delete([blindMailboxId, id])

      if (maxEnvelopeId > 0) {
        const mbMeta: LocalMailboxMeta = {
          blindMailboxId,
          lastSyncedEnvelopeId: maxEnvelopeId,
          updatedAt: new Date().toISOString(),
        }
        mbStore.put(mbMeta)
      }

      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch (fehler) {
    // Non-fatal if IndexedDB write fails — außer der Aufrufer braucht die
    // Gewissheit, dass die Zeile liegt (`saveLocalMessagesStrict`).
    if (streng) throw fehler
  }
}

/** Ein Bereinigungslauf je Mailbox zur Zeit. */
const bereinigungsKetten = new Map<string, Promise<unknown>>()

function seriell<T>(mailbox: string, arbeit: () => Promise<T>): Promise<T> {
  const vorgaenger = bereinigungsKetten.get(mailbox) ?? Promise.resolve()
  const lauf = vorgaenger.then(arbeit, arbeit)
  const kette = lauf.then(
    () => undefined,
    () => undefined,
  )
  bereinigungsKetten.set(mailbox, kette)
  void kette.then(() => {
    if (bereinigungsKetten.get(mailbox) === kette) bereinigungsKetten.delete(mailbox)
  })
  return lauf
}

/**
 * Räumt die Altlasten eines Verlaufs: Platzhalter und Doppelungen.
 *
 * **Konservativ.** Ein Platzhalter fällt nur, wenn die echte Zeile mit
 * derselben logischen Kennung daneben liegt. Ein alleinstehender bleibt in der
 * Ablage — er ist der einzige Hinweis darauf, dass dort einmal etwas war — und
 * wird nur nicht mehr angezeigt. Eine echte Nachricht mit dem Wortlaut
 * „Verschlüsselte Nachricht" bleibt unangetastet, weil sie kein Platzhalter ist
 * (sie ist von jemandem, hat vielleicht einen Anhang und ist bearbeitbar).
 *
 * Doppelungen derselben Nachricht werden auf eine Zeile zusammengelegt; der
 * Gewinner bestimmt `gewinnt` (echt vor Platzhalter, bestätigt vor
 * optimistisch). Gelöscht wird nur die Zeile, nicht ihr Inhalt: was der
 * Gewinner nicht kennt, war schon vorher verloren.
 */
export function entfernePlatzhalterUndDubletten(
  blindMailboxId: string,
  art: VerlaufsArt,
  istPlatzhalter: (m: LocalStoredMessage) => boolean = istVerschluesselterPlatzhalter,
): Promise<{ platzhalter: number; dubletten: number }> {
  const leer = { platzhalter: 0, dubletten: 0 }
  if (!blindMailboxId) return Promise.resolve(leer)
  return seriell(blindMailboxId, async () => {
    try {
      const db = await openLocalDatabase()
      const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
        const tx = db.transaction(STORE_MESSAGES, 'readonly')
        const req = tx
          .objectStore(STORE_MESSAGES)
          .index('by_mailbox')
          .getAll(IDBKeyRange.only(blindMailboxId))
        req.onsuccess = () => resolve((req.result || []) as Record<string, any>[])
        req.onerror = () => reject(req.error)
      })
      const vorhanden = await entsiegleZeilen<LocalStoredMessage>(
        rohzeilen,
        (z) => nachrichtAad(blindMailboxId, z.id),
      )

      const gruppen = new Map<string, LocalStoredMessage[]>()
      for (const m of vorhanden) {
        if (!m || m.blindMailboxId !== blindMailboxId) continue
        if (typeof m.id !== 'number' || typeof m.text !== 'string') continue
        const k = verlaufsSchluessel(m, art)
        const bisher = gruppen.get(k)
        if (bisher) bisher.push(m)
        else gruppen.set(k, [m])
      }

      const zuLoeschen: number[] = []
      let platzhalter = 0
      let dubletten = 0
      for (const zeilen of gruppen.values()) {
        if (zeilen.length < 2) continue
        const echte = zeilen.filter((m) => !istPlatzhalter(m))
        // Nur Platzhalter: ausblenden ja, löschen nein.
        if (echte.length === 0) continue
        let sieger = echte[0]
        for (const m of echte.slice(1)) if (gewinnt(m, sieger)) sieger = m
        for (const m of zeilen) {
          if (m.id === sieger.id) continue
          if (istPlatzhalter(m)) platzhalter += 1
          else dubletten += 1
          zuLoeschen.push(m.id)
        }
      }
      if (zuLoeschen.length === 0) return { platzhalter, dubletten }

      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE_MESSAGES, 'readwrite')
        const store = tx.objectStore(STORE_MESSAGES)
        for (const id of zuLoeschen) store.delete([blindMailboxId, id])
        tx.oncomplete = () => resolve()
        tx.onerror = () => reject(tx.error)
      })
      return { platzhalter, dubletten }
    } catch {
      return leer
    }
  })
}

/**
 * Nimmt eine Nachricht endgültig aus der lokalen Ablage.
 *
 * `saveLocalMessages` schreibt nur. Was in der übergebenen Liste fehlt, bleibt
 * in der Ablage stehen — eine verworfene Nachricht verschwand deshalb nur aus
 * der Ansicht und kam beim nächsten Abgleich über `loadLocalMessages` und
 * `mischeVerlauf` zurück. Am laufenden System waren das die Nachrichten mit der
 * Uhr, die sich nicht löschen liessen: weg, wieder da, weg, wieder da.
 *
 * Beide Kennungen zählen: die Zeile einer noch nicht gesendeten Nachricht trägt
 * eine aus der Uhr erfundene Umschlagkennung, die einer bestätigten die echte.
 */
export async function entferneLokaleNachricht(
  blindMailboxId: string,
  kennung: { clientUuid?: string; id?: number }
): Promise<void> {
  if (!blindMailboxId) return
  if (!kennung.clientUuid && typeof kennung.id !== 'number') return
  try {
    const db = await openLocalDatabase()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readwrite')
      const store = tx.objectStore(STORE_MESSAGES)
      // Über den Mailbox-Index, nicht über `by_client_uuid`: dieselbe logische
      // Kennung kann in der Ablage mehrfach liegen (optimistische Zeile und
      // bestätigte Fassung unter verschiedenen Umschlagkennungen), und ein
      // `get` auf dem Index fände davon nur eine.
      const req = store.index('by_mailbox').getAll(IDBKeyRange.only(blindMailboxId))
      req.onsuccess = () => {
        for (const m of (req.result || []) as LocalStoredMessage[]) {
          const trifft =
            (kennung.clientUuid !== undefined && m.clientUuid === kennung.clientUuid) ||
            (typeof kennung.id === 'number' && m.id === kennung.id)
          if (trifft) store.delete([blindMailboxId, m.id])
        }
      }
      req.onerror = () => reject(req.error)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // Non-fatal if IndexedDB write fails
  }
}

/**
 * Entfernt alle Nachrichten einer Mailbox aus der lokalen Ablage.
 *
 * Der Gegenpol zu `versteckeGespraech`: wer ein Gespräch aus der Liste nimmt,
 * erwartet, dass auch der örtliche Verlauf geht. Ohne diesen Weg blieb die
 * versiegelte Zeile liegen und `ladeUmschlagKlartexte`/`loadLocalMessages`
 * holten sie beim nächsten Öffnen zurück — ein gelöschter Chat, der wieder da
 * ist, sobald man ihn erneut öffnet.
 *
 * Gelesen wird über den Mailbox-Index, nicht über die Mailbox-Zeile: die
 * entsteht erst mit einer echten Umschlagkennung.
 *
 * **Fehler werden weitergereicht.** Ein Gespräch, das in der Ablage bleibt,
 * ist nicht entfernt; der Aufrufer zeigt dann den Fehler statt „entfernt“.
 */
export async function leereMailboxLokal(blindMailboxId: string): Promise<void> {
  if (!blindMailboxId) return
  // Kein stilles Schlucken mehr: wer ein Gespräch entfernt, erfährt, wenn die
  // Ablage es nicht hergibt. Bis 10/2026 meldete die Seite „Chat entfernt“, und
  // der Verlauf lag weiter auf der Platte.
  const db = await openLocalDatabase()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction([STORE_MESSAGES, STORE_KLARTEXTE, STORE_MAILBOXES, STORE_ENTWUERFE], 'readwrite')
    const nachrichten = tx.objectStore(STORE_MESSAGES)
    const index = nachrichten.index('by_mailbox')
    const req = index.openKeyCursor(IDBKeyRange.only(blindMailboxId))
    req.onsuccess = () => {
      const cursor = req.result
      if (!cursor) return
      nachrichten.delete(cursor.primaryKey)
      cursor.continue()
    }
    req.onerror = () => reject(req.error)

    // Der Schlüssel ist `[blindMailboxId, envelopeId]`; die Spanne trifft
    // genau diese Mailbox, ohne jede Klartextzeile des Kontos anzufassen.
    const klartexte = tx.objectStore(STORE_KLARTEXTE)
    const klartextReq = klartexte.openKeyCursor(
      IDBKeyRange.bound([blindMailboxId, -Infinity], [blindMailboxId, Infinity]),
    )
    klartextReq.onsuccess = () => {
      const cursor = klartextReq.result
      if (!cursor) return
      klartexte.delete(cursor.primaryKey)
      cursor.continue()
    }
    klartextReq.onerror = () => reject(klartextReq.error)

    tx.objectStore(STORE_MAILBOXES).delete(blindMailboxId)
    tx.objectStore(STORE_ENTWUERFE).delete(blindMailboxId)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
  })
}

/**
 * Updates a single message in the local store by clientUuid or id.
 */
export async function updateMessageInLocalStore(
  blindMailboxId: string,
  clientUuidOrId: string | number,
  patch: Partial<LocalStoredMessage>
): Promise<void> {
  if (!blindMailboxId) return
  try {
    const db = await openLocalDatabase()

    // Lesen, öffnen, ändern, wieder zumachen, schreiben. Das sind zwei
    // Transaktionen statt einer, weil Ent- und Verschlüsseln asynchron sind
    // und eine IndexedDB-Transaktion kein `await` überlebt.
    const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readonly')
      const store = tx.objectStore(STORE_MESSAGES)
      const getReq =
        typeof clientUuidOrId === 'number'
          ? store.get([blindMailboxId, clientUuidOrId])
          : store.index('by_client_uuid').get(clientUuidOrId)
      getReq.onsuccess = () => resolve(getReq.result ?? null)
      getReq.onerror = () => reject(getReq.error)
    })

    if (!roh || roh.blindMailboxId !== blindMailboxId) return

    const alt = await entsiegleZeile<LocalStoredMessage>(
      roh,
      nachrichtAad(blindMailboxId, roh.id),
    )
    if (!alt) return

    const alteId = roh.id as number
    const neu = { ...alt, ...patch, blindMailboxId }
    const zeile = await versiegleZeile(
      neu,
      NACHRICHT_KLARFELDER,
      nachrichtAad(blindMailboxId, neu.id),
    )

    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readwrite')
      const store = tx.objectStore(STORE_MESSAGES)
      // If ID was upgraded (e.g. optimistic timestamp to server id), delete old key
      if (patch.id && patch.id !== alteId) {
        store.delete([blindMailboxId, alteId])
      }
      store.put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // Non-fatal
  }
}

/**
 * Alle Mailboxen, zu denen hier Nachrichten liegen.
 *
 * Gelesen wird über den Index auf `blindMailboxId`, nicht über den
 * Mailbox-Store: dessen Zeile entsteht erst, wenn eine Nachricht eine echte
 * Umschlagkennung vom Server hat. Ein Gespräch, in dem bisher nur die eigene
 * optimistische Zeile steht, hätte dort keinen Eintrag — und genau die soll
 * der Erstabgleich mitnehmen.
 */
export async function listeLokaleMailboxen(): Promise<string[]> {
  return await ladeMailboxen(false)
}

/** Wie `listeLokaleMailboxen`, aber ein Lesefehler wird geworfen. */
export async function listeLokaleMailboxenStrict(): Promise<string[]> {
  return await ladeMailboxen(true)
}

async function ladeMailboxen(streng: boolean): Promise<string[]> {
  try {
    const db = await openLocalDatabase()
    return await new Promise<string[]>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readonly')
      const index = tx.objectStore(STORE_MESSAGES).index('by_mailbox')
      const gefunden: string[] = []
      const req = index.openKeyCursor(null, 'nextunique')
      req.onsuccess = () => {
        const cursor = req.result
        if (!cursor) return resolve(gefunden)
        if (typeof cursor.key === 'string' && cursor.key) gefunden.push(cursor.key)
        cursor.continue()
      }
      req.onerror = () => reject(req.error)
    })
  } catch (fehler) {
    if (streng) throw fehler
    return []
  }
}

/**
 * Retrieves the last synced envelope ID for a mailbox.
 */
export async function getLocalMailboxLastSyncedId(blindMailboxId: string): Promise<number> {
  if (!blindMailboxId) return 0
  try {
    const db = await openLocalDatabase()
    return await new Promise<number>((resolve) => {
      const tx = db.transaction(STORE_MAILBOXES, 'readonly')
      const req = tx.objectStore(STORE_MAILBOXES).get(blindMailboxId)
      req.onsuccess = () => resolve(req.result?.lastSyncedEnvelopeId ?? 0)
      req.onerror = () => resolve(0)
    })
  } catch {
    return 0
  }
}

/**
 * Schreibt jede Zeile dieser Ablage einmal neu.
 *
 * Das ist der Umstellungsdurchlauf beim Ein- und beim Ausschalten des PIN, und
 * es ist derselbe Code für beide Richtungen: `versiegleZeile` richtet sich nach
 * dem Schalter, der vorher gesetzt wurde. Einschalten heißt Schalter an und
 * einmal durchlaufen, Ausschalten heißt Schalter aus und einmal durchlaufen.
 *
 * Bricht der Durchlauf in der Mitte ab — geschlossener Reiter, leerer Akku —,
 * liegen beide Formen nebeneinander. Beide sind lesbar, solange der Schlüssel
 * da ist, und ein zweiter Durchlauf räumt den Rest ab. Deshalb wird hier Zeile
 * für Zeile geschrieben und nicht alles in einer großen Transaktion: eine
 * abgebrochene große Transaktion macht die halbe Arbeit rückgängig, hier bleibt
 * jede fertige Zeile fertig.
 */
export async function schreibeNachrichtenBestandNeu(): Promise<number> {
  const db = await openLocalDatabase()

  const rohNachrichten = await new Promise<Record<string, any>[]>((resolve, reject) => {
    const tx = db.transaction(STORE_MESSAGES, 'readonly')
    const req = tx.objectStore(STORE_MESSAGES).getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })

  let geschrieben = 0
  for (const roh of rohNachrichten) {
    const mailbox = String(roh.blindMailboxId || '')
    if (!mailbox) continue
    const aad = nachrichtAad(mailbox, roh.id)
    const offen = await entsiegleZeile<LocalStoredMessage>(roh, aad)
    if (!offen) continue
    const zeile = await versiegleZeile({ ...offen, blindMailboxId: mailbox }, NACHRICHT_KLARFELDER, aad)
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_MESSAGES, 'readwrite')
      tx.objectStore(STORE_MESSAGES).put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
    geschrieben++
  }

  const rohKlartexte = await new Promise<Record<string, any>[]>((resolve, reject) => {
    const tx = db.transaction(STORE_KLARTEXTE, 'readonly')
    const req = tx.objectStore(STORE_KLARTEXTE).getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })

  for (const roh of rohKlartexte) {
    const mailbox = String(roh.blindMailboxId || '')
    const umschlag = Number(roh.envelopeId)
    if (!mailbox || !Number.isFinite(umschlag)) continue
    const aad = klartextAad(mailbox, umschlag)
    const offen = await entsiegleZeile<Record<string, any>>(roh, aad)
    if (!offen) continue
    const zeile = await versiegleZeile(
      { ...offen, blindMailboxId: mailbox, envelopeId: umschlag },
      ['blindMailboxId', 'envelopeId'],
      aad,
    )
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_KLARTEXTE, 'readwrite')
      tx.objectStore(STORE_KLARTEXTE).put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
    geschrieben++
  }

  return geschrieben
}

/**
 * Clears all local stored messenger data.
 */
export async function clearLocalMessengerStore(): Promise<void> {
  try {
    const db = await openLocalDatabase()
    await new Promise<void>((resolve, reject) => {
      // Die Klartextablage gehört zwingend dazu: dort liegen die gelesenen
      // Nachrichten im Klartext, und nach einem Abmelden darf davon nichts
      // zurückbleiben.
      // Die Funken ebenso: sie sind aus diesem Verlauf gerechnet.
      const tx = db.transaction(
        [
          STORE_MESSAGES,
          STORE_MAILBOXES,
          STORE_KLARTEXTE,
          STORE_FUNKEN,
          // Die Nachholauftraege und Grabsteine gehoeren zu diesem Konto wie
          // der Verlauf. Ein „alles loeschen", das sie stehen laesst, holte
          // spaeter Anhaenge zu Nachrichten, die es hier nicht mehr gibt.
          STORE_MEDIEN_AUFTRAEGE,
          STORE_MEDIEN_GRABSTEINE,
          // Die Outbox der Steuerwirkungen ebenso: ein Auftrag nennt Mailbox,
          // Mitglieder und Inhalt einer Wirkung. Nach dem Abmelden darf davon
          // nichts liegen bleiben — und mit der Sitzung des naechsten Kontos
          // ginge er sonst hinaus (Punkt 50 des Regelwerks).
          STORE_STEUER_AUFTRAEGE,
        ],
        'readwrite'
      )
      tx.objectStore(STORE_MESSAGES).clear()
      tx.objectStore(STORE_MAILBOXES).clear()
      tx.objectStore(STORE_KLARTEXTE).clear()
      tx.objectStore(STORE_FUNKEN).clear()
      tx.objectStore(STORE_MEDIEN_AUFTRAEGE).clear()
      tx.objectStore(STORE_MEDIEN_GRABSTEINE).clear()
      tx.objectStore(STORE_STEUER_AUFTRAEGE).clear()
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    // Non-fatal
  }
}
