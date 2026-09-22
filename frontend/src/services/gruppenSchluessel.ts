/**
 * Der Gruppenschlüssel: erzeugen, zustellen, rotieren, nachfordern.
 *
 * Eine Gruppe kann keinen Double Ratchet fahren. Der Ratchet ist ein Faden
 * zwischen genau zwei Geräten; bei zwanzig Mitgliedern mit je drei Geräten wären
 * das je Nachricht sechzig Umschläge. Signal löst das mit Sender Keys, und genau
 * das steht hier: ein Zufallsschlüssel je Gruppe, verschlüsselt wird einmal,
 * zugestellt wird nur der **Schlüssel** — einzeln versiegelt gegen jedes Gerät
 * jedes Mitglieds.
 *
 * **Warum nicht abgeleitet.** Vorher war der Gruppenschlüssel
 * `sha256("msm:group:key:" + groupId)`. Die `groupId` steht in der Datenbank,
 * also konnte das Backend jede Gruppennachricht mitlesen. Das war keine
 * Ende-zu-Ende-Verschlüsselung, sondern eine, die genau den nicht aussperrt, den
 * sie aussperren soll. Dieser Absatz bleibt stehen, damit niemand sie neu
 * erfindet.
 *
 * **Die Kennung ist der Schlüssel.** `keyId = sha256Hex(schluessel).slice(0,16)`.
 * Das ist nicht bloß eine Nummer: wer einen Schlüssel zustellt, kann ihn nicht
 * unter der Kennung eines anderen unterschieben — beim Empfang wird die Kennung
 * nachgerechnet. Eine Nachricht nennt die Kennung im Klartext, damit jedes Gerät
 * weiß, welchen seiner Schlüssel es nehmen muss.
 *
 * **Zustellung über die Gruppenmailbox.** Die Umschläge sind gewöhnliche
 * `sv-e2ee-hybrid-v1:`, relayed als Steuerumschläge in dieselbe Mailbox wie die
 * Nachrichten. Das Backend braucht dafür nichts Neues, und es entstehen keine
 * Direktchat-Zeilen zwischen Mitgliedern, die einander gar nicht kennen. Jeder
 * lädt alle Umschläge, öffnen lässt sich nur der eigene.
 *
 * **Rotation.** Jeder Schlüssel merkt sich, für welche Mitgliedschaft er
 * gemünzt wurde. Vor dem Senden wird das mit der heutigen Mitgliederliste
 * verglichen; weicht sie ab, entsteht ein frischer Schlüssel. Wer die Gruppe
 * verlassen hat, liest damit nichts Neues mehr — die Nachrichten von vorher
 * bleiben ihm lesbar, denn seinen alten Schlüssel kann ihm niemand wegnehmen.
 *
 * Die Prüfung läuft beim Absender und nicht an einem zentralen Ereignis, weil
 * ein Ereignis jemanden braucht, der es mitbekommt: wer rauswirft und danach den
 * Tab schließt, hätte die Rotation nie ausgelöst. So entscheidet jedes Gerät für
 * sich, aus derselben Mitgliederliste, und es heilt sich von selbst.
 *
 * **Nachzügler.** Ein Gerät ohne passenden Schlüssel fragt nach. Beantwortet
 * wird nur der **aktuelle** Schlüssel, nie ein alter: sonst könnte sich jemand
 * hinauswerfen lassen, über einen offenen Einladungslink zurückkommen und den
 * gesamten Verlauf nachfordern.
 *
 * Eine Nachfrage wird **einmal** beantwortet. Sie ist ein Umschlag und bleibt
 * als solcher in der Mailbox liegen, und der Lesepfad holt bei jedem Abruf das
 * ganze Fenster neu — ohne Gedächtnis antwortete der Zuständige deshalb alle
 * fünf Sekunden erneut, mit einem Umschlag je Gerät des Fragenden. Am laufenden
 * System waren danach 99 von 100 Umschlägen der Gruppenmailbox
 * Schlüsselzustellungen: die Nachrichten fielen aus dem Fenster, und im Verlauf
 * stand nichts mehr ausser „Verschlüsselte Nachricht". Gemerkt wird je
 * fragendem Gerät und fehlender Schlüsselkennung — fragt dasselbe Gerät später
 * nach einer anderen Kennung, bekommt es wieder eine Antwort.
 *
 * **Eine Ablage je Konto, seit 09/2026.** Sie hiess schlicht
 * `msm_e2ee_gruppen` und war damit die einzige der vier E2EE-Ablagen ohne
 * Bindung an einen Menschen: der Geräteschlüssel liegt unter `konto:<id>`, der
 * Ratchet unter einer Sitzungskennung mit dem eigenen Gerät darin — die
 * Gruppenschlüssel unter gar nichts. Wer sich auf demselben Rechner
 * nacheinander mit zwei Konten anmeldete, hinterliess dem zweiten die
 * Gruppenschlüssel des ersten.
 *
 * Übernommen wird nichts: ein Schlüssel trägt kein Konto, und ihn dem
 * zuzuschlagen, das nach dem Umstieg zufällig als erstes den Messenger
 * öffnet, wäre genau das Leck. Der Preis ist hier gering und vorgesehen —
 * ein Gerät ohne passenden Schlüssel fragt nach, und der eigene Verlauf liegt
 * ohnehin entschlüsselt in der kontogebundenen Nachrichtenablage.
 */

import { decryptString, encryptString, importAesGcmRawKey } from '@msdis/shield/aead'
import { base64ToBytes, bytesToBase64 } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'
import { randomBytes } from '@msdis/shield/random'

import { fetchE2eeEnvelopes, getGroupMembers, relayE2eeEnvelope } from '@/api/social'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import i18n from '@/i18n'

import {
  E2EE_HYBRID_ENVELOPE_SPEC,
  deriveGroupBlindMailboxId,
  deriveUserDeviceMailboxId,
  encryptE2eeHybrid,
} from './e2eeCrypto'
import { eigenesGeraet, geraeteVon } from './e2eeGeraet'
import { merkeMailboxNachweis } from './mailboxNachweis'
import { entsiegleZeile, versiegleZeile } from './lokaleVersiegelung'
import { istSchluesselhalter } from './raumSchluessel'

export const GRUPPE_PREFIX = 'sv-e2ee-group-v1:'
export const GRUPPEN_SCHLUESSEL_TYP = 'group_key'
export const GRUPPEN_ANFRAGE_TYP = 'group_key_request'

/**
 * Der Gruppenschlüssel hat kein einziges Gerät erreicht.
 *
 * Nicht zu verwechseln mit einer unvollständigen Zustellung: die ist eingeplant,
 * die übergangenen Geräte fordern nach. Erreicht aber **niemand** den Schlüssel,
 * wäre jede folgende Nachricht für alle ausser dem Absender unlesbar.
 */
export class GruppenSchluesselNichtZugestelltError extends Error {
  constructor(
    public readonly groupId: number,
    public readonly ziele: number,
  ) {
    super('Der Gruppenschlüssel erreichte kein Gerät')
    this.name = 'GruppenSchluesselNichtZugestelltError'
  }
}

const SCHLUESSEL_BYTES = 32
const KEY_ID_LAENGE = 16
const KEY_ID_MUSTER = /^[0-9a-f]{16}$/

export interface GruppenKontext {
  groupId: number
  /** Die Mailbox der Gruppe. Dort liegen Nachrichten und Schlüsselumschläge. */
  blindMailboxId: string
  eigeneId: number
  /** Die Mitglieder, wie dieses Gerät sie gerade sieht. Quelle jeder Rotation. */
  mitglieder: readonly number[]
  /**
   * Gehört die Gruppe mir?
   *
   * Pflichtfeld, kein optionales: gebraucht wird es nur an einer Stelle — wer
   * das Gruppengeheimnis einer Bestandsgruppe erzeugen darf —, und dort
   * entscheidet es darüber, ob die Gruppe in zwei Mailboxen zerfällt. Ein
   * vergessenes `true` wäre ein Fehler, den niemand sieht; ein vergessenes
   * Feld meldet der Typprüfer.
   */
  istEigentuemer: boolean
}

export type GruppenLesung =
  /**
   * Der Klartext — und keine Aussage darüber, wer ihn geschrieben hat. Ein
   * Gruppenschlüssel beweist Mitgliedschaft, nie Identität; wer den Absender
   * braucht, prüft die Nutzlastsignatur (`nutzlastSignatur.ts`).
   */
  | { art: 'klartext'; text: string }
  /** Kein Schlüssel zu dieser Kennung. Der Aufrufer darf nachfordern. */
  | { art: 'kein-schluessel'; keyId: string }
  /** Kein Umschlag dieses Verfahrens — Altbestand aus der Zeit der Ableitung. */
  | { art: 'unbekannt' }
  /** Schlüssel vorhanden, Tag gescheitert: am Umschlag wurde gedreht. */
  | { art: 'bruch'; keyId: string; grund: string }

export type GruppenSteuerung =
  | { art: 'schluessel'; keyId: string }
  | { art: 'anfrage'; vonKonto: number; beantwortet: boolean }
  | { art: 'keine' }

// ==========================================
// Ablage
// ==========================================

export interface GruppenSchluesselEintrag {
  groupId: number
  keyId: string
  /** Base64 der 32 Schlüsselbytes. */
  schluessel: string
  /** Die Mitgliedschaft, für die dieser Schlüssel gemünzt wurde. Sortiert. */
  mitglieder: number[]
  erzeugtAm: string
}

/**
 * Das Gruppengeheimnis — 32 Bytes, die eine Gruppe ihr Leben lang behält.
 *
 * Nicht zu verwechseln mit dem Nachrichtenschlüssel darüber: **der** rotiert
 * bei jedem Mitgliederwechsel, und das muss er auch. Das Geheimnis darf das
 * nicht, denn aus ihm entsteht die Kennung der Mailbox. Rotierte es mit, zöge
 * die Gruppe bei jedem Beitritt um und liesse ihr Fenster stehen.
 *
 * Es reist mit dem Schlüssel: dieselbe Zustellung, dieselben Empfänger. Ein
 * eigener Weg wäre ein zweiter, der genauso oft scheitern kann — und wer den
 * Schlüssel nicht hat, kann mit dem Geheimnis ohnehin nichts anfangen.
 */
export interface GruppenGeheimnis {
  groupId: number
  /** Base64 der 32 Geheimnisbytes. */
  geheimnis: string
  erzeugtAm: string
}

export interface GruppenAblage {
  lies(groupId: number, keyId: string): Promise<GruppenSchluesselEintrag | null>
  liesAktuellen(groupId: number): Promise<GruppenSchluesselEintrag | null>
  /** Das Geheimnis dieser Gruppe, falls dieses Gerät es kennt. */
  liesGeheimnis(groupId: number): Promise<GruppenGeheimnis | null>
  /**
   * Legt das Geheimnis ab — und überschreibt ein vorhandenes **nie**.
   *
   * Zwei Geräte, die gleichzeitig eines erzeugen, wären zwei Mailboxen und
   * damit eine gespaltene Gruppe. Wer schon eines hat, behält es; der andere
   * erfährt seines mit der nächsten Schlüsselzustellung.
   */
  schreibeGeheimnis(eintrag: GruppenGeheimnis): Promise<void>
  /** Legt den Schlüssel ab und macht ihn zum aktuellen dieser Gruppe. */
  schreibe(eintrag: GruppenSchluesselEintrag): Promise<void>
  /**
   * Legt den Schlüssel ab, **ohne** den aktuellen zu ersetzen.
   *
   * Der Weg für einen zugestellten Schlüssel, dessen Generation dieses Gerät
   * schon bedient: lesen soll er, senden nicht. Warum, steht bei `nimmSchluessel`.
   */
  schreibeNebenher(eintrag: GruppenSchluesselEintrag): Promise<void>
  loescheGruppe(groupId: number): Promise<void>
  /** Wurde diese Nachfrage schon beantwortet? Siehe Kopf der Datei. */
  kennstAnfrage(groupId: number, kennung: string): Promise<boolean>
  /** Erst rufen, wenn die Antwort wirklich rausging. */
  merkeAnfrage(groupId: number, kennung: string): Promise<void>
}

const DB_PRAEFIX = 'msm_e2ee_gruppen'
/** Version 2: `beantwortet` kommt hinzu. Version 3: das Gruppengeheimnis. */
const DB_VERSION = 3
const STORE_KEYS = 'keys'
const STORE_AKTUELL = 'aktuell'
const STORE_ANFRAGEN = 'beantwortet'
const STORE_GEHEIMNISSE = 'geheimnisse'

/** Die Ablage dieses Kontos. Ein anderes Konto, eine andere Datenbank. */
function dbName(kontoId: number): string {
  return `${DB_PRAEFIX}:konto:${kontoId}`
}

let offeneDb: Promise<IDBDatabase> | null = null
let offenesKonto: number | null = null
let altbestandGeraeumt = false

/**
 * Entfernt die alte, kontolose Datenbank — einmal je Browsersitzung.
 *
 * Ohne Übernahme, aus demselben Grund wie beim Nachrichtenverlauf: ein
 * abgelegter Gruppenschlüssel sagt nicht, wem er gehört.
 */
function raeumeAltbestand(): void {
  if (altbestandGeraeumt) return
  altbestandGeraeumt = true
  try {
    indexedDB.deleteDatabase(DB_PRAEFIX)
  } catch {
    // Blockiert durch einen anderen Tab. Der nächste Start holt es nach.
  }
}

function oeffneDatenbank(): Promise<IDBDatabase> {
  const konto = angemeldetesKonto()
  if (konto === null) {
    // Ohne Konto keine Schlüsselablage. Nicht „ersatzweise die gemeinsame".
    return Promise.reject(new Error(i18n.t('chat.errors.indexedDbUnavailable')))
  }
  if (offeneDb && offenesKonto !== konto) {
    const alt = offeneDb
    offeneDb = null
    void alt.then((db) => db.close()).catch(() => {})
  }
  if (offeneDb) return offeneDb
  offenesKonto = konto
  offeneDb = new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error(i18n.t('chat.errors.indexedDbUnavailable')))
    }
    raeumeAltbestand()
    const req = indexedDB.open(dbName(konto), DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_KEYS)) {
        db.createObjectStore(STORE_KEYS, { keyPath: ['groupId', 'keyId'] })
      }
      if (!db.objectStoreNames.contains(STORE_AKTUELL)) {
        db.createObjectStore(STORE_AKTUELL, { keyPath: 'groupId' })
      }
      if (!db.objectStoreNames.contains(STORE_ANFRAGEN)) {
        db.createObjectStore(STORE_ANFRAGEN, { keyPath: ['groupId', 'kennung'] })
      }
      if (!db.objectStoreNames.contains(STORE_GEHEIMNISSE)) {
        db.createObjectStore(STORE_GEHEIMNISSE, { keyPath: 'groupId' })
      }
    }
    req.onsuccess = () => {
      // Ein zweiter Tab kann jederzeit eine neuere Version aufziehen. Ohne
      // diesen Abgang liefen laufende Zugriffe danach gegen eine Verbindung,
      // die der Browser nur noch mit Fehlern beantwortet.
      //
      // Seit die Verbindung gemerkt wird, muss der Abgang sie auch aus dem
      // Gedächtnis nehmen: vorher öffnete jeder Aufruf eine neue und heilte
      // sich dadurch von selbst.
      req.result.onversionchange = () => {
        offeneDb = null
        offenesKonto = null
        req.result.close()
      }
      req.result.onclose = () => {
        offeneDb = null
        offenesKonto = null
      }
      resolve(req.result)
    }
    req.onerror = () => {
      offeneDb = null
      offenesKonto = null
      reject(req.error)
    }
    // Ein älterer Tab hält die Vorgängerversion offen. Ohne diesen Zweig
    // meldet der Browser weder Erfolg noch Fehler, und das Öffnen hinge
    // stillschweigend, bis der andere Tab zugeht.
    req.onblocked = () => {
      offeneDb = null
      offenesKonto = null
      reject(new Error(i18n.t('chat.errors.groupDbBlocked')))
    }
  })
  return offeneDb
}

function hole<T>(store: string, key: IDBValidKey): Promise<T | null> {
  return oeffneDatenbank().then(
    (db) =>
      new Promise<T | null>((resolve, reject) => {
        const tx = db.transaction(store, 'readonly')
        const req = tx.objectStore(store).get(key)
        req.onsuccess = () => resolve((req.result as T) ?? null)
        req.onerror = () => reject(req.error)
      })
  )
}

/** Bindet einen Gruppenschlüssel an seinen Platz. Siehe `versiegleZeile`. */
function gruppenAad(groupId: number, keyId: string): string {
  return `msm-gruppenschluessel:${groupId}:${keyId}`
}

/** Bindet ein Gruppengeheimnis an seine Gruppe. */
function geheimnisAad(groupId: number): string {
  return `msm-gruppengeheimnis:${groupId}`
}

/**
 * Nur `keys` geht durchs Siegel. In `aktuell` steht ein Zeiger auf eine
 * Schlüsselkennung und in `beantwortet` eine Marke — beides sagt nichts über
 * Inhalte aus, und beides muss lesbar bleiben, damit die Zeiger ohne PIN noch
 * stimmen.
 */
async function liesSchluessel(
  groupId: number,
  keyId: string,
): Promise<GruppenSchluesselEintrag | null> {
  const roh = await hole<Record<string, any>>(STORE_KEYS, [groupId, keyId])
  const zeile = await entsiegleZeile<GruppenSchluesselEintrag>(roh, gruppenAad(groupId, keyId))
  return zeile?.schluessel ? zeile : null
}

const indexedDbAblage: GruppenAblage = {
  async lies(groupId, keyId) {
    return liesSchluessel(groupId, keyId)
  },
  async liesAktuellen(groupId) {
    const zeiger = await hole<{ groupId: number; keyId: string }>(STORE_AKTUELL, groupId)
    if (!zeiger?.keyId) return null
    return liesSchluessel(groupId, zeiger.keyId)
  },
  async schreibe(eintrag) {
    const db = await oeffneDatenbank()
    // Versiegeln vor der Transaktion — ein `await` mitten drin bricht sie ab.
    const zeile = await versiegleZeile(
      eintrag,
      ['groupId', 'keyId'],
      gruppenAad(eintrag.groupId, eintrag.keyId),
    )
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction([STORE_KEYS, STORE_AKTUELL], 'readwrite')
      tx.objectStore(STORE_KEYS).put(zeile)
      tx.objectStore(STORE_AKTUELL).put({ groupId: eintrag.groupId, keyId: eintrag.keyId })
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
  async schreibeNebenher(eintrag) {
    const db = await oeffneDatenbank()
    const zeile = await versiegleZeile(
      eintrag,
      ['groupId', 'keyId'],
      gruppenAad(eintrag.groupId, eintrag.keyId),
    )
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_KEYS, 'readwrite')
      tx.objectStore(STORE_KEYS).put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
  async liesGeheimnis(groupId) {
    const roh = await hole<Record<string, any>>(STORE_GEHEIMNISSE, groupId)
    const zeile = await entsiegleZeile<GruppenGeheimnis>(roh, geheimnisAad(groupId))
    return zeile?.geheimnis ? zeile : null
  },
  async schreibeGeheimnis(eintrag) {
    // Erst nachsehen, dann schreiben. Ein vorhandenes Geheimnis zu
    // überschreiben hiesse, die Gruppe in zwei Mailboxen zu spalten.
    if (await indexedDbAblage.liesGeheimnis(eintrag.groupId)) return
    const db = await oeffneDatenbank()
    const zeile = await versiegleZeile(eintrag, ['groupId'], geheimnisAad(eintrag.groupId))
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_GEHEIMNISSE, 'readwrite')
      tx.objectStore(STORE_GEHEIMNISSE).put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
  async loescheGruppe(groupId) {
    const db = await oeffneDatenbank()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(
        [STORE_KEYS, STORE_AKTUELL, STORE_ANFRAGEN, STORE_GEHEIMNISSE],
        'readwrite',
      )
      tx.objectStore(STORE_GEHEIMNISSE).delete(groupId)
      // Der zusammengesetzte Schlüssel beginnt mit der Gruppenkennung, also
      // trifft ein Bereich über [groupId] genau deren Schlüssel.
      tx.objectStore(STORE_KEYS).delete(IDBKeyRange.bound([groupId], [groupId, []]))
      tx.objectStore(STORE_AKTUELL).delete(groupId)
      tx.objectStore(STORE_ANFRAGEN).delete(IDBKeyRange.bound([groupId], [groupId, []]))
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
  async kennstAnfrage(groupId, kennung) {
    return (await hole<unknown>(STORE_ANFRAGEN, [groupId, kennung])) !== null
  },
  async merkeAnfrage(groupId, kennung) {
    const db = await oeffneDatenbank()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_ANFRAGEN, 'readwrite')
      tx.objectStore(STORE_ANFRAGEN).put({ groupId, kennung, beantwortetAm: new Date().toISOString() })
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
}

/**
 * Schreibt jeden Gruppenschlüssel einmal neu — der Umstellungsdurchlauf.
 * Siehe `schreibeNachrichtenBestandNeu`.
 *
 * Läuft bewusst an `ablage` vorbei direkt auf die IndexedDB: die Schnittstelle
 * kennt kein „alle", und sie soll es auch nicht lernen. Alles auflisten ist
 * genau das, was im Alltag niemand tun soll.
 */
export async function schreibeGruppenBestandNeu(): Promise<number> {
  const db = await oeffneDatenbank()
  const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
    const tx = db.transaction(STORE_KEYS, 'readonly')
    const req = tx.objectStore(STORE_KEYS).getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })

  let geschrieben = 0
  for (const roh of rohzeilen) {
    const groupId = Number(roh.groupId)
    const keyId = String(roh.keyId || '')
    if (!Number.isFinite(groupId) || !keyId) continue
    const aad = gruppenAad(groupId, keyId)
    const offen = await entsiegleZeile<GruppenSchluesselEintrag>(roh, aad)
    if (!offen?.schluessel) continue
    const zeile = await versiegleZeile(offen, ['groupId', 'keyId'], aad)
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_KEYS, 'readwrite')
      tx.objectStore(STORE_KEYS).put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
    geschrieben++
  }
  return geschrieben
}

let ablage: GruppenAblage = indexedDbAblage

/** Hängt eine andere Ablage ein. Nur für Tests gedacht. */
export function setzeGruppenAblageFuerTest(neu: GruppenAblage | null): void {
  ablage = neu ?? indexedDbAblage
  gefragt.clear()
}

// ==========================================
// Format
// ==========================================

function gebundeneDaten(groupId: number, keyId: string): string {
  return `msm:group:aad:${groupId}:${keyId}`
}

/**
 * Führt eine Operation mit dem entpackten Schlüssel aus und nullt die Bytes
 * danach. Der CryptoKey selbst ist nicht auslesbar; die Rohbytes wären es.
 */
async function mitSchluessel<T>(
  eintrag: GruppenSchluesselEintrag,
  arbeit: (key: CryptoKey) => Promise<T>,
): Promise<T> {
  const bytes = base64ToBytes(eintrag.schluessel)
  try {
    return await arbeit(await importAesGcmRawKey(bytes, ['encrypt', 'decrypt']))
  } finally {
    bytes.fill(0)
  }
}

function lieseGruppenKopf(umschlag: string): { keyId: string; rumpf: string } | null {
  if (!umschlag.startsWith(GRUPPE_PREFIX)) return null
  const nutzlast = umschlag.slice(GRUPPE_PREFIX.length)
  const punkt = nutzlast.indexOf('.')
  if (punkt === -1) return null
  const keyId = nutzlast.slice(0, punkt)
  const rumpf = nutzlast.slice(punkt + 1)
  if (!KEY_ID_MUSTER.test(keyId) || !rumpf) return null
  return { keyId, rumpf }
}

/** Sortiert, entdoppelt, ohne Unsinn — damit zwei Geräte denselben Vergleich bilden. */
function normalisiereMitglieder(ids: readonly unknown[]): number[] {
  const sauber = new Set<number>()
  for (const roh of ids) {
    const id = Number(roh)
    if (Number.isInteger(id) && id > 0) sauber.add(id)
  }
  return [...sauber].sort((a, b) => a - b)
}

function gleicheMitglieder(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i])
}

// ==========================================
// Geheimnis, Mailbox und Besitznachweis
// ==========================================

/**
 * Zwei Werte aus einem Geheimnis, und beide sind ein SHA-256 mit eigenem
 * Vorsatz.
 *
 * Der Vorsatz ist nicht Zierde: ohne ihn wäre die Mailbox-Kennung derselbe
 * Wert wie der Besitznachweis. Wer die Kennung kennt — und die steht in jedem
 * Aufruf —, hätte damit auch den Nachweis, und das ganze Schloss wäre ein
 * Türschild. Dieselbe Trennung wie beim Tresor, wo `bucketId` und
 * `bucketAuthToken` aus demselben Seed mit verschiedenen Zusätzen entstehen.
 */
const MAILBOX_VORSATZ = 'msm:gruppe:mailbox:'
const NACHWEIS_VORSATZ = 'msm:gruppe:nachweis:'

/** Die Mailbox-Kennung einer Gruppe aus ihrem Geheimnis. 64 Hexzeichen. */
export async function mailboxAusGeheimnis(geheimnis: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(MAILBOX_VORSATZ + geheimnis))
}

/** Der Besitznachweis derselben Mailbox. 64 Hexzeichen, nie gleich der Kennung. */
export async function nachweisAusGeheimnis(geheimnis: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(NACHWEIS_VORSATZ + geheimnis))
}

/**
 * Merkt sich den Besitznachweis dieser Gruppe für alle folgenden Aufrufe.
 *
 * **Hier wird bewusst nicht registriert.** Der erste Entwurf tat es, und eine
 * Laufzeitprobe am 22.09.2026 zeigte, warum das die Gruppe zerstört: der
 * Gruppenschlüssel wird über dieselbe Mailbox zugestellt, die der Nachweis
 * verschliesst (`anJedesGeraet` schickt an `kontext.blindMailboxId`). Sobald
 * der Eigentümer registriert hatte, bekam jedes Mitglied ohne Geheimnis auf
 * diese Mailbox 403 — und damit nie den Schlüssel, aus dem das Geheimnis
 * gekommen wäre. Gemessen: das Mitglied sah die Nachricht nicht einmal mehr
 * als „Verschlüsselte Nachricht", die Mailbox lieferte gar nichts.
 *
 * Ein Schloss vor die Tür zu hängen, deren Schlüssel dahinter liegt, ist kein
 * Sicherheitsgewinn, sondern ein Aussperren. Das Registrieren gehört deshalb
 * erst dorthin, wo die Zustellung die Gruppenmailbox nicht mehr braucht:
 * Steuerumschläge über die Geräte-Mailbox, danach der Umzug der Kennung.
 *
 * Bis dahin gilt für die Gruppenmailbox weiter allein `assert_mailbox_participant`
 * — genau wie vorher, es geht also nichts verloren. Das Geheimnis ist dann
 * schon bei allen Mitgliedern angekommen, und der Nachweis lässt sich
 * jederzeit daraus nachrechnen.
 */
async function sichereBesitznachweis(kontext: GruppenKontext, geheimnis: string): Promise<void> {
  merkeMailboxNachweis(kontext.blindMailboxId, await nachweisAusGeheimnis(geheimnis))
}

/**
 * Das Geheimnis dieser Gruppe — und wer es erzeugen darf, wenn es keines gibt.
 *
 * Bestandsgruppen haben keines. Eines zu erzeugen darf deshalb **nur der
 * Eigentümer**: erzeugten zwei Mitglieder gleichzeitig eines, hätte die Gruppe
 * zwei Mailboxen und zerfiele in zwei Hälften, die einander nicht mehr sehen.
 * Wer nicht Eigentümer ist, wartet — spätestens mit der nächsten
 * Schlüsselzustellung bekommt er es.
 */
async function geheimnisFuer(
  kontext: GruppenKontext,
): Promise<{ geheimnis: string | null; frischErzeugt: boolean }> {
  const vorhanden = await ablage.liesGeheimnis(kontext.groupId)
  if (vorhanden) return { geheimnis: vorhanden.geheimnis, frischErzeugt: false }
  if (!kontext.istEigentuemer) return { geheimnis: null, frischErzeugt: false }

  const roh = randomBytes(SCHLUESSEL_BYTES)
  const neu = bytesToBase64(roh)
  roh.fill(0)
  await ablage.schreibeGeheimnis({
    groupId: kontext.groupId,
    geheimnis: neu,
    erzeugtAm: new Date().toISOString(),
  })
  // Nicht das gerade erzeugte zurückgeben, sondern das abgelegte: ein
  // gleichzeitiger zweiter Aufruf hat vielleicht gewonnen, und dann gilt
  // seines.
  const abgelegt = (await ablage.liesGeheimnis(kontext.groupId))?.geheimnis ?? neu
  return { geheimnis: abgelegt, frischErzeugt: true }
}

// ==========================================
// Schlüssel
// ==========================================

/**
 * Die Mitglieder, wie der Server sie in diesem Augenblick sieht.
 *
 * `kontext.mitglieder` kommt aus der Oberfläche und ist eine Momentaufnahme vom
 * Öffnen des Gesprächs: `activeGroup` wird in `Messenger.tsx` gesetzt und
 * danach nicht mehr aufgefrischt. Daran hingen bis 09/2026 beide Entscheidungen
 * über den Gruppenschlüssel, und beide gingen still schief, sobald jemand nach
 * dem Öffnen beitrat. Er stand in keiner Verteilung, und auf seine Nachfrage
 * antwortete niemand, weil er in der Momentaufnahme nicht vorkam. Am laufenden
 * System las ein frisch beigetretenes Mitglied dadurch überhaupt nichts.
 *
 * Wer über Schlüssel entscheidet, fragt deshalb den Server. Antwortet der
 * nicht, gilt die Momentaufnahme weiter: lieber gegen eine alte Liste
 * verteilen als gar nicht senden können.
 */
async function frischeMitglieder(kontext: GruppenKontext): Promise<number[]> {
  let roh: readonly number[] = kontext.mitglieder
  try {
    const liste = await getGroupMembers(kontext.groupId)
    if (liste.length > 0) roh = liste.map((m) => m.user_id)
  } catch {
    // Server nicht erreichbar — die Momentaufnahme muss reichen.
  }
  return normalisiereMitglieder([...roh, kontext.eigeneId])
}

export async function erzeugeGruppenSchluessel(): Promise<{
  keyId: string
  schluessel: Uint8Array
}> {
  const schluessel = randomBytes(SCHLUESSEL_BYTES)
  const keyId = (await sha256Hex(schluessel)).slice(0, KEY_ID_LAENGE)
  return { keyId, schluessel }
}

/**
 * Der Schlüssel, mit dem jetzt zu senden ist.
 *
 * Passt der aktuelle nicht mehr zur Mitgliedschaft, entsteht hier ein frischer
 * und geht an alle Geräte aller Mitglieder raus. Erreicht die Zustellung nicht
 * jedes Gerät, ist das kein Abbruch: die übergangenen Geräte sehen eine
 * unbekannte Kennung und fordern nach.
 */
async function schluesselZumSenden(kontext: GruppenKontext): Promise<GruppenSchluesselEintrag> {
  const jetzige = await frischeMitglieder(kontext)
  const { geheimnis, frischErzeugt } = await geheimnisFuer(kontext)
  if (geheimnis) await sichereBesitznachweis(kontext, geheimnis)

  const vorhanden = await ablage.liesAktuellen(kontext.groupId)
  /**
   * Ein **frisch erzeugtes** Geheimnis muss zu den anderen, und der einzige
   * Weg dorthin ist die Schlüsselzustellung. Ohne Mitgliederwechsel rotiert
   * aber nichts — also erzwingt es hier einmal eine Rotation. Das kostet eine
   * Generation und heilt sich von selbst; alles andere wäre ein zweiter
   * Zustellweg neben dem ersten, der genauso oft scheitern kann.
   */
  if (vorhanden && !frischErzeugt && gleicheMitglieder(vorhanden.mitglieder, jetzige)) {
    return vorhanden
  }

  const { keyId, schluessel } = await erzeugeGruppenSchluessel()
  const eintrag: GruppenSchluesselEintrag = {
    groupId: kontext.groupId,
    keyId,
    schluessel: bytesToBase64(schluessel),
    mitglieder: jetzige,
    erzeugtAm: new Date().toISOString(),
  }
  schluessel.fill(0)

  // Erst ablegen, dann verteilen: ein Schlüssel, der schon unterwegs ist und
  // nirgends liegt, macht die eigenen Nachrichten unlesbar.
  await ablage.schreibe(eintrag)
  await verteileSchluessel(kontext, eintrag, jetzige, geheimnis)
  return eintrag
}

/**
 * Versiegelt eine Nutzlast für jedes Gerät der genannten Konten und relayed sie.
 *
 * Zugestellt wird in die **Geräte-Mailbox des Empfängers**, nicht in die der
 * Gruppe. Bis 09/2026 lief es andersherum, mit zwei Folgen:
 *
 * 1. Die Gruppenmailbox liess sich nicht verschliessen. Wer den Schlüssel noch
 *    nicht hatte, hätte ihn hinter genau dem Schloss holen müssen, das er
 *    aufsperren sollte — eine Laufzeitprobe zeigte das Mitglied danach vor
 *    einer stummen Mailbox.
 * 2. Das Lesefenster verhungerte: 99 von 100 Umschlägen waren
 *    Schlüsselzustellungen für fremde Geräte, die dieses Gerät nie öffnen kann.
 *
 * Gelesen wird weiterhin aus beiden Quellen — Altbestand liegt noch in den
 * Gruppenmailboxen, und der soll nicht verloren gehen.
 */
async function anJedesGeraet(
  kontext: GruppenKontext,
  empfaengerIds: readonly number[],
  nutzlast: string,
  kontrolltyp: string,
): Promise<number> {
  const meins = await eigenesGeraet()
  const basis =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `gk-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`

  let lfd = 0
  let zugestellt = 0
  let ziele = 0
  for (const empfaengerId of empfaengerIds) {
    const geraete = await geraeteVon(empfaengerId)
    const ziel = await deriveUserDeviceMailboxId(empfaengerId)
    for (const geraet of geraete) {
      // Das eigene Gerät hat den Schlüssel schon.
      if (empfaengerId === kontext.eigeneId && geraet.device_id === meins.kennung) continue
      ziele += 1
      const nummer = lfd++
      try {
        const umschlag = await encryptE2eeHybrid(nutzlast, geraet.public_key)
        await relayE2eeEnvelope({
          blind_mailbox_id: ziel,
          ciphertext_envelope: umschlag,
          // Je Gerät eine eigene Kennung: das Relais gibt beim zweiten Aufruf
          // mit derselben Kennung still den ersten Umschlag zurück.
          client_uuid: `${basis}#${nummer}`,
          is_control: true,
          control_type: kontrolltyp,
        })
        zugestellt += 1
      } catch {
        // Ein Gerät, das sich nicht erreichen lässt, hält die übrigen nicht auf.
        // Es sieht später eine unbekannte Kennung und fordert nach.
      }
    }
  }

  // Kein einziges Ziel erreicht, obwohl es welche gab. Das war bis 09/2026 eine
  // stille 0, die niemand auswertete: der Absender schrieb munter weiter mit
  // einem Schlüssel, den ausser ihm keiner hatte, und im Verlauf der anderen
  // stand nur „Verschlüsselte Nachricht".
  if (ziele > 0 && zugestellt === 0) {
    throw new GruppenSchluesselNichtZugestelltError(kontext.groupId, ziele)
  }
  return zugestellt
}

function schluesselNutzlast(
  eintrag: GruppenSchluesselEintrag,
  geheimnis: string | null,
): string {
  return JSON.stringify({
    typ: GRUPPEN_SCHLUESSEL_TYP,
    v: 1,
    groupId: eintrag.groupId,
    keyId: eintrag.keyId,
    schluessel: eintrag.schluessel,
    mitglieder: eintrag.mitglieder,
    // Fehlt bei Gruppen, deren Eigentümer noch nicht gesendet hat. Ein
    // Empfänger, der nichts bekommt, bleibt schlicht ohne — er darf selbst
    // keines erzeugen.
    ...(geheimnis ? { geheimnis } : {}),
  })
}

async function verteileSchluessel(
  kontext: GruppenKontext,
  eintrag: GruppenSchluesselEintrag,
  empfaenger: readonly number[],
  geheimnis: string | null,
): Promise<number> {
  return anJedesGeraet(
    kontext,
    empfaenger,
    schluesselNutzlast(eintrag, geheimnis),
    GRUPPEN_SCHLUESSEL_TYP,
  )
}

// ==========================================
// Nachrichten
// ==========================================

export async function verschluesseleFuerGruppe(
  kontext: GruppenKontext,
  klartext: string,
): Promise<string> {
  const eintrag = await schluesselZumSenden(kontext)
  const chiffre = await mitSchluessel(eintrag, (key) =>
    encryptString(klartext, key, gebundeneDaten(kontext.groupId, eintrag.keyId))
  )
  return `${GRUPPE_PREFIX}${eintrag.keyId}.${chiffre}`
}

export async function entschluesseleGruppenUmschlag(
  groupId: number,
  umschlag: string,
): Promise<GruppenLesung> {
  const kopf = lieseGruppenKopf(umschlag)
  if (!kopf) return { art: 'unbekannt' }

  const eintrag = await ablage.lies(groupId, kopf.keyId)
  if (!eintrag) return { art: 'kein-schluessel', keyId: kopf.keyId }

  try {
    const text = await mitSchluessel(eintrag, (key) =>
      decryptString(kopf.rumpf, key, gebundeneDaten(groupId, kopf.keyId))
    )
    return { art: 'klartext', text }
  } catch (fehler) {
    const grund = fehler instanceof Error ? fehler.name : 'unbekannt'
    return { art: 'bruch', keyId: kopf.keyId, grund }
  }
}

// ==========================================
// Steuerumschläge
// ==========================================

/**
 * Nimmt einen entschlüsselten Hybrid-Klartext aus einer Gruppenmailbox entgegen.
 *
 * Meldet `keine`, wenn es keiner der beiden Steuertypen war — dann gehört der
 * Klartext in den normalen Lesepfad.
 */
export async function verarbeiteGruppenSteuerung(
  kontext: GruppenKontext,
  klartext: string,
): Promise<GruppenSteuerung> {
  let roh: Record<string, unknown>
  try {
    const geparst = JSON.parse(klartext)
    if (!geparst || typeof geparst !== 'object') return { art: 'keine' }
    roh = geparst as Record<string, unknown>
  } catch {
    return { art: 'keine' }
  }

  // Ein Umschlag aus einer fremden Gruppe darf hier nichts setzen. Ohne diese
  // Prüfung könnte ein Mitglied von A einen Schlüssel für B unterschieben.
  if (Number(roh.groupId) !== kontext.groupId) return { art: 'keine' }

  if (roh.typ === GRUPPEN_SCHLUESSEL_TYP) {
    return nimmSchluessel(kontext, roh)
  }
  if (roh.typ === GRUPPEN_ANFRAGE_TYP) {
    return beantworteAnfrage(kontext, roh)
  }
  return { art: 'keine' }
}

async function nimmSchluessel(
  kontext: GruppenKontext,
  roh: Record<string, unknown>,
): Promise<GruppenSteuerung> {
  const keyId = String(roh.keyId ?? '')
  const schluessel = String(roh.schluessel ?? '')
  if (!KEY_ID_MUSTER.test(keyId) || !schluessel) return { art: 'keine' }

  let bytes: Uint8Array
  try {
    bytes = base64ToBytes(schluessel)
  } catch {
    return { art: 'keine' }
  }
  try {
    if (bytes.length !== SCHLUESSEL_BYTES) return { art: 'keine' }
    // Die Kennung wird nachgerechnet. Sie ist der Hash des Schlüssels, also
    // lässt sich unter einer bekannten Kennung kein anderer Schlüssel
    // unterschieben — und ein Gerät, das denselben Schlüssel zweimal bekommt,
    // schreibt zweimal dasselbe.
    const gerechnet = (await sha256Hex(bytes)).slice(0, KEY_ID_LAENGE)
    if (gerechnet !== keyId) return { art: 'keine' }
  } finally {
    bytes.fill(0)
  }

  // Die mitgeschickte Mitgliederliste entscheidet nur, wann *dieses* Gerät das
  // nächste Mal rotiert. Eine falsche Angabe verzögert die Rotation bis zum
  // nächsten eigenen Sendevorgang — mehr kann sie nicht, denn wer den Schlüssel
  // münzt, hat ihn ohnehin.
  const mitglieder = normalisiereMitglieder(Array.isArray(roh.mitglieder) ? roh.mitglieder : [])
  const eintrag: GruppenSchluesselEintrag = {
    groupId: kontext.groupId,
    keyId,
    schluessel,
    mitglieder,
    erzeugtAm: new Date().toISOString(),
  }

  /*
   * Das Gruppengeheimnis reist mit. Wer zuerst da war, bleibt stehen — das
   * entscheidet die Ablage, nicht diese Stelle.
   *
   * Dass ein Mitglied hier ein falsches Geheimnis unterschieben könnte, ist
   * dieselbe Lage wie beim Schlüssel selbst: wer eines schickt, hat den
   * Schlüssel ohnehin. Wirkung hätte es nur bei einem Gerät, das noch gar
   * keines kennt, und die Folge wäre kein Mitlesen, sondern ein Gerät, das in
   * der falschen Mailbox sucht und nichts mehr sieht. Kryptographisch
   * abgesichert wird die Herkunft erst mit MLS; bis dahin ist die
   * Erstankunft die Regel.
   */
  const geheimnis = typeof roh.geheimnis === 'string' ? roh.geheimnis : ''
  if (geheimnis) {
    try {
      await ablage.schreibeGeheimnis({
        groupId: kontext.groupId,
        geheimnis,
        erzeugtAm: new Date().toISOString(),
      })
      merkeMailboxNachweis(kontext.blindMailboxId, await nachweisAusGeheimnis(geheimnis))
    } catch {
      // Ein Geheimnis, das sich nicht ablegen lässt, darf den Schlüssel nicht
      // aufhalten: ohne den wäre die Nachricht unlesbar, ohne jenes nur die
      // Mailbox ungeschützt.
    }
  }

  /*
   * Ankommen ja, verdrängen nein.
   *
   * Ein zugestellter Schlüssel wird immer abgelegt — ohne ihn wäre die
   * Nachricht unlesbar, für die er gilt. Der *aktuelle* wird er aber nur, wenn
   * für die heutige Mitgliedschaft noch keiner da ist.
   *
   * Bis 09/2026 gewann der zuletzt eingetroffene. Damit konnte ein Mitglied
   * einem anderen einen nur ihm bekannten Schlüssel unterschieben: das Opfer
   * verschlüsselte ab da gegen ihn, und der Rest der Gruppe las nur noch
   * „Verschlüsselte Nachricht". Wer zuerst da ist, bleibt jetzt stehen — und
   * das ist genau der, der beim letzten Mitgliederwechsel als erster gesendet
   * hat, also der vorgesehene Weg.
   *
   * Verglichen wird gegen `kontext.mitglieder`, die Momentaufnahme der
   * Oberfläche — **nicht** gegen `frischeMitglieder`. Zwei Gründe: die Liste
   * aus der Zustellung ist die des Absenders und damit Teil des Angriffs, und
   * `frischeMitglieder` fragt den Server. Diese Funktion läuft bei *jedem*
   * Abruf über *jeden* Schlüsselumschlag, der in der Mailbox liegt — also alle
   * fünf Sekunden. Ein Netzaufruf an dieser Stelle wäre eine Dauerlast.
   *
   * Was die Momentaufnahme kostet, wenn sie veraltet ist: dieses Gerät behält
   * seinen alten Schlüssel, münzt beim nächsten Senden einen frischen (dort
   * entscheidet `frischeMitglieder`) und verteilt ihn. Die Gruppe hat dann
   * eine Generation mehr als nötig. Lesbar bleibt alles.
   *
   * Was das nicht kann: ein Gerät, das noch gar keinen Schlüssel für diese
   * Mitgliedschaft hat, nimmt weiterhin den ersten, der kommt. Ohne eine
   * Absenderbeglaubigung auf der Zustellung ist das nicht enger zu fassen; die
   * Grenze steht so auch in `docs/agent-rules/security.md`.
   */
  const vorhanden = await ablage.liesAktuellen(kontext.groupId)
  const behalte =
    vorhanden !== null &&
    vorhanden.keyId !== keyId &&
    gleicheMitglieder(
      vorhanden.mitglieder,
      normalisiereMitglieder([...kontext.mitglieder, kontext.eigeneId]),
    )

  if (behalte) {
    await ablage.schreibeNebenher(eintrag)
    return { art: 'schluessel', keyId }
  }

  await ablage.schreibe(eintrag)
  return { art: 'schluessel', keyId }
}

/**
 * Wer einem Nachzügler antwortet, entscheidet jedes Gerät für sich aus der
 * Mitgliederliste — dieselbe Regel wie beim Raumschlüssel eines Anrufs.
 *
 * Dazu kommt ein Fall, den ein Anruf nicht kennt: fragt ein **zweites Gerät
 * desselben Kontos**, antworten die eigenen Geräte immer. `istSchluesselhalter`
 * schließt das fragende Konto aus den Kandidaten aus, und ein frisch gekoppeltes
 * Gerät in einer Gruppe, deren übrige Mitglieder gerade offline sind, bekäme
 * sonst nie einen Schlüssel.
 */
async function beantworteAnfrage(
  kontext: GruppenKontext,
  roh: Record<string, unknown>,
): Promise<GruppenSteuerung> {
  const anfragerId = Number(roh.vonKonto)
  if (!Number.isInteger(anfragerId) || anfragerId <= 0) return { art: 'keine' }
  // Nur Mitglieder bekommen Schlüssel. Und die Geräteschlüssel kommen aus dem
  // Verzeichnis, nie aus der Anfrage — sonst bestimmte der Fragende selbst,
  // wogegen versiegelt wird.
  //
  // Die Liste kommt vom Server und nicht aus `kontext.mitglieder`: wer gerade
  // erst beigetreten ist, steht in der Momentaufnahme der Oberfläche noch
  // nicht, und genau der fragt hier. Mit der alten Liste wurde er abgewiesen,
  // ohne dass irgendwo etwas davon stand.
  const mitglieder = await frischeMitglieder(kontext)
  if (!mitglieder.includes(anfragerId)) return { art: 'keine' }

  const eigenesKonto = anfragerId === kontext.eigeneId
  const zustaendig =
    eigenesKonto || istSchluesselhalter(kontext.eigeneId, mitglieder, anfragerId)
  if (!zustaendig) return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }

  // Diese Nachfrage liegt als Umschlag in der Mailbox und kommt bei jedem
  // Abruf wieder. Einmal beantworten reicht; siehe Kopf der Datei.
  const kennung = `${anfragerId}:${String(roh.vonGeraet ?? '')}:${String(roh.keyId ?? '')}`
  if (await ablage.kennstAnfrage(kontext.groupId, kennung)) {
    return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }
  }

  const eintrag = await ablage.liesAktuellen(kontext.groupId)
  if (!eintrag) return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }

  // Nur der aktuelle Schlüssel. Ältere bleiben hier, sonst holte sich ein
  // Zurückgekehrter über eine Anfrage den ganzen Verlauf.
  //
  // Hier darf nichts fliegen: das läuft mitten im Lesen der Mailbox, und eine
  // gescheiterte Antwort darf nicht den Verlauf des Antwortenden abreissen. Der
  // Fragende versucht es in einer Minute erneut.
  let zugestellt = 0
  try {
    zugestellt = await anJedesGeraet(
      kontext,
      [anfragerId],
      // Das Geheimnis geht mit: wer nachfragt, hat es oft ebenfalls nicht —
      // ein frisches Gerät eines Mitglieds kennt weder Schlüssel noch
      // Geheimnis, und eine Antwort mit nur der Hälfte liesse es in der
      // falschen Mailbox zurück.
      schluesselNutzlast(eintrag, (await ablage.liesGeheimnis(kontext.groupId))?.geheimnis ?? null),
      GRUPPEN_SCHLUESSEL_TYP,
    )
  } catch {
    zugestellt = 0
  }
  // Erst merken, wenn wirklich etwas rausging. Eine gescheiterte Antwort soll
  // der nächste Durchlauf erneut versuchen — sonst bliebe der Fragende ohne
  // Schlüssel und niemand käme je darauf zurück.
  if (zugestellt > 0) {
    await ablage.merkeAnfrage(kontext.groupId, kennung).catch(() => {})
  }
  return { art: 'anfrage', vonKonto: anfragerId, beantwortet: zugestellt > 0 }
}

/** Wonach dieses Gerät seit dem Laden der Seite schon gefragt hat. */
const gefragt = new Set<string>()

/**
 * Fordert den aktuellen Gruppenschlüssel an.
 *
 * Gerichtet an den zuständigen Halter und an die eigenen Zweitgeräte, statt an
 * alle Mitglieder: antworten würde ohnehin nur dieser eine Kreis, und eine
 * Rundsendung an jedes Gerät jedes Mitglieds kostet in großen Gruppen ein
 * Vielfaches.
 *
 * **Einmal je fehlender Kennung**, nicht einmal je Minute. Eine Nachfrage ist
 * ein Umschlag und bleibt liegen: wer erst in einer Stunde online geht, liest
 * sie dann und antwortet. Ein zweites Mal zu fragen bringt deshalb nichts und
 * kostet je Frage einen Umschlag pro Gerät des Gefragten — bei einer alten
 * Nachricht, deren Schlüssel niemand mehr herausgibt, jede Minute aufs Neue,
 * für immer. Der Aufrufer ruft das ohnehin nur für die **neueste** unlesbare
 * Kennung auf.
 *
 * Das Gedächtnis hält bis zum Neuladen. Das ist der Wiederholungsweg für den
 * seltenen Fall, dass die Nachfrage aus dem Abruffenster gefallen ist, bevor
 * sie jemand gesehen hat.
 */
export async function fordereGruppenSchluessel(
  kontext: GruppenKontext,
  fehlendeKeyId: string,
): Promise<boolean> {
  const marke = `${kontext.groupId}:${fehlendeKeyId}`
  if (gefragt.has(marke)) return false
  // Sofort merken, damit zwei überlappende Durchläufe nicht beide fragen.
  gefragt.add(marke)

  try {
    // Auch hier die Liste vom Server: der Fragende ist oft gerade erst
    // beigetreten, und wen er fragt, muss zur heutigen Gruppe passen.
    const mitglieder = await frischeMitglieder(kontext)
    const andere = mitglieder.filter((id) => id !== kontext.eigeneId)
    const ziele = andere.length > 0 ? [Math.min(...andere), kontext.eigeneId] : [kontext.eigeneId]

    const meins = await eigenesGeraet()
    const nutzlast = JSON.stringify({
      typ: GRUPPEN_ANFRAGE_TYP,
      v: 1,
      groupId: kontext.groupId,
      vonKonto: kontext.eigeneId,
      vonGeraet: meins.kennung,
      keyId: fehlendeKeyId,
    })

    const erreicht = await anJedesGeraet(kontext, ziele, nutzlast, GRUPPEN_ANFRAGE_TYP)
    // Nichts rausgegangen heißt: es liegt auch nichts in der Mailbox, das
    // jemand später noch beantworten könnte. Also Marke zurück.
    if (erreicht === 0) gefragt.delete(marke)
    return erreicht > 0
  } catch {
    // Auch das läuft im Lesepfad: eine Nachfrage, die niemanden erreicht, ist
    // ein „noch nicht", kein Grund, die Anzeige abzubrechen. Ein Netzfehler
    // darf dieses Gerät aber nicht bis zum Neuladen ohne Schlüssel lassen.
    gefragt.delete(marke)
    return false
  }
}

// ==========================================
// Die eigene Geräte-Mailbox
// ==========================================

/**
 * Wie weit die eigene Geräte-Mailbox schon gelesen ist, je Konto.
 *
 * Nur im Arbeitsspeicher. Nach einem Neuladen wird einmal alles gelesen — das
 * kostet einen Durchlauf und ist der Preis dafür, nichts zu verpassen. Je
 * Konto getrennt, damit ein Kontowechsel im selben Tab nicht den Stand des
 * vorigen erbt und dessen Umschläge überspringt.
 */
const geraeteStand = new Map<number, number>()

/** Vergisst den Lesestand. Gehört zum Abmelden. */
export function leereGeraeteStand(): void {
  geraeteStand.clear()
}

/**
 * Liest die Steuerumschläge aus der eigenen Geräte-Mailbox.
 *
 * Der Weg, auf dem ein Gruppenschlüssel heute ankommt. Er hängt an keiner
 * Gruppe: ein Gerät, das von einer Gruppe noch gar nichts weiss, bekommt hier
 * ihren Schlüssel und ihr Geheimnis — und erst damit kann es ihre Mailbox
 * öffnen. Genau diese Reihenfolge fehlte, solange die Zustellung durch die
 * Gruppenmailbox lief.
 *
 * Der Gruppenkontext entsteht aus der Nutzlast selbst, denn beim Lesen ist
 * noch nicht bekannt, um welche Gruppe es geht. Das ist keine Lücke: der
 * Umschlag war hybrid für dieses Gerät versiegelt, und was er setzen kann,
 * entscheidet `verarbeiteGruppenSteuerung` — ein Schlüssel wird abgelegt, aber
 * verdrängt keinen, und ein Geheimnis kommt nur an, wo noch keines ist.
 *
 * `entsiegle` kommt von aussen, damit diese Datei den Schlüsselbund des
 * Geräts nicht kennen muss. Wirft sie, war der Umschlag für ein anderes Gerät
 * — der Normalfall, kein Fehler.
 */
export async function holeGeraeteSteuerung(
  eigeneId: number,
  entsiegle: (umschlag: string) => Promise<string>,
): Promise<number> {
  if (!Number.isInteger(eigeneId) || eigeneId <= 0) return 0

  const mid = await deriveUserDeviceMailboxId(eigeneId)
  const seit = geraeteStand.get(eigeneId) ?? 0

  let umschlaege: { id: number; ciphertext_envelope: string }[]
  try {
    umschlaege = await fetchE2eeEnvelopes(mid, seit)
  } catch {
    // Kein Netz oder ein Server, der diese Mailbox nicht kennt. Der nächste
    // Durchlauf holt es nach; der Stand bleibt stehen.
    return 0
  }

  let verarbeitet = 0
  for (const env of umschlaege) {
    if (env.id > (geraeteStand.get(eigeneId) ?? 0)) geraeteStand.set(eigeneId, env.id)
    if (!env.ciphertext_envelope.startsWith(E2EE_HYBRID_ENVELOPE_SPEC.currentPrefix)) continue

    let klartext: string
    try {
      klartext = await entsiegle(env.ciphertext_envelope)
    } catch {
      continue // Für ein anderes Gerät desselben Kontos.
    }

    let groupId = 0
    let mitglieder: number[] = []
    try {
      const roh = JSON.parse(klartext)
      groupId = Number(roh?.groupId)
      mitglieder = normalisiereMitglieder(Array.isArray(roh?.mitglieder) ? roh.mitglieder : [])
    } catch {
      continue // Kein JSON — gehört einem anderen Nutzer dieser Mailbox
      // (Notizen, Kalender) und nicht hierher.
    }
    if (!Number.isInteger(groupId) || groupId <= 0) continue

    if (!mitglieder.includes(eigeneId)) mitglieder.push(eigeneId)
    const kontext: GruppenKontext = {
      groupId,
      blindMailboxId: await deriveGroupBlindMailboxId(groupId),
      eigeneId,
      mitglieder,
      // Aus einem eingehenden Umschlag wird niemand Eigentümer. Das Flag
      // entscheidet allein, ob dieses Gerät ein Geheimnis *erzeugen* darf —
      // und das darf es beim Empfangen unter keinen Umständen.
      istEigentuemer: false,
    }

    try {
      const ergebnis = await verarbeiteGruppenSteuerung(kontext, klartext)
      if (ergebnis.art !== 'keine') verarbeitet += 1
    } catch {
      // Eine Zustellung, die sich nicht verarbeiten lässt, hält die übrigen
      // nicht auf.
    }
  }
  return verarbeitet
}

/** Wirft die Schlüssel einer Gruppe weg. Beim Verlassen und beim Löschen fällig. */
export async function verwirfGruppenSchluessel(groupId: number): Promise<void> {
  for (const marke of gefragt) {
    if (marke.startsWith(`${groupId}:`)) gefragt.delete(marke)
  }
  await ablage.loescheGruppe(groupId)
}
