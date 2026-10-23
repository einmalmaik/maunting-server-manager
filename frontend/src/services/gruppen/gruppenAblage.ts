/**
 * Die Ablage der Gruppenschluessel: IndexedDB, Versiegelung, Kontobindung.
 *
 * Eine Datenbank je Konto. Der Altbestand ohne Konto wird beim ersten Zugriff
 * entfernt, nicht uebernommen - ein Schluessel traegt keinen Besitzer.
 */

import i18n from '@/i18n'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { entsiegleZeile, versiegleZeile } from '../lokaleVersiegelung'
import { leereGefragt } from './zustand'
import type {
  DmGeheimnis,
  GruppenAblage,
  GruppenGeheimnis,
  GruppenSchluesselEintrag,
  GruppenZustellung,
} from './typen'

const DB_PRAEFIX = 'msm_e2ee_gruppen'
/**
 * Version 2: `beantwortet` kommt hinzu. Version 3: das Gruppengeheimnis.
 * Version 4: dasselbe für den Direktchat. Version 5: offene Zustellaufträge.
 */
const DB_VERSION = 5
const STORE_KEYS = 'keys'
const STORE_AKTUELL = 'aktuell'
const STORE_ANFRAGEN = 'beantwortet'
const STORE_GEHEIMNISSE = 'geheimnisse'
const STORE_DM_GEHEIMNISSE = 'dm_geheimnisse'
const STORE_ZUSTELLUNGEN = 'zustellungen'

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
      // Dasselbe für Direktchats. Ein eigener Speicher und kein geteilter mit
      // negativen Schlüsseln: `peerId` und `groupId` sind beides kleine
      // Ganzzahlen, und eine Verwechslung wäre ein Gespräch, das das Geheimnis
      // eines fremden bekäme.
      if (!db.objectStoreNames.contains(STORE_DM_GEHEIMNISSE)) {
        db.createObjectStore(STORE_DM_GEHEIMNISSE, { keyPath: 'peerId' })
      }
      // Offene Schlüsselzustellungen. Sie enthalten Konten und Gerätekennungen,
      // aber keinen Schlüssel: der liegt bereits unter `keys`. Deshalb bleiben
      // sie lesbar wie der Zeiger in `aktuell` — ohne PIN muss der Auftrag
      // trotzdem auffindbar sein.
      if (!db.objectStoreNames.contains(STORE_ZUSTELLUNGEN)) {
        db.createObjectStore(STORE_ZUSTELLUNGEN, { keyPath: ['groupId', 'keyId'] })
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

/** Bindet ein Chatgeheimnis an seine Gegenstelle. */
function dmGeheimnisAad(peerId: number): string {
  return `msm-chatgeheimnis:${peerId}`
}

/** Legt eine Chatgeheimnis-Zeile ab, ohne zu fragen, ob dort schon eine liegt. */
async function dmZeileSchreiben(eintrag: DmGeheimnis): Promise<void> {
  const db = await oeffneDatenbank()
  const zeile = await versiegleZeile(eintrag, ['peerId'], dmGeheimnisAad(eintrag.peerId))
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_DM_GEHEIMNISSE, 'readwrite')
    tx.objectStore(STORE_DM_GEHEIMNISSE).put(zeile)
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
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
  async liesDmGeheimnis(peerId) {
    const roh = await hole<Record<string, any>>(STORE_DM_GEHEIMNISSE, peerId)
    const zeile = await entsiegleZeile<DmGeheimnis>(roh, dmGeheimnisAad(peerId))
    return zeile?.geheimnis ? zeile : null
  },
  async schreibeDmGeheimnis(eintrag) {
    // Wie bei der Gruppe: ein vorhandenes zu überschreiben hiesse, das
    // Gespräch in zwei Mailboxen zu spalten.
    if (await indexedDbAblage.liesDmGeheimnis(eintrag.peerId)) return
    await dmZeileSchreiben(eintrag)
  },
  async merkeDmVerteilung(peerId, wann, eigeneGeraete = []) {
    // Der einzige erlaubte Nachtrag an einem abgelegten Chatgeheimnis: das
    // Geheimnis selbst bleibt, nur die Zustellmarke und die Liste der eigenen
    // Geräte kommen dazu. `verteiltAm` wird nie zurückgenommen.
    const vorhanden = await indexedDbAblage.liesDmGeheimnis(peerId)
    if (!vorhanden) return
    const bekannt = vorhanden.verteiltAn ?? []
    const verteiltAn = [...new Set([...bekannt, ...eigeneGeraete])]
    const verteiltAm = vorhanden.verteiltAm ?? wann
    if (vorhanden.verteiltAm === verteiltAm && bekannt.length === verteiltAn.length) return
    await dmZeileSchreiben({ ...vorhanden, verteiltAm, verteiltAn })
  },
  async liesZustellungen(groupId) {
    const db = await oeffneDatenbank()
    const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
      const tx = db.transaction(STORE_ZUSTELLUNGEN, 'readonly')
      const req =
        groupId === undefined
          ? tx.objectStore(STORE_ZUSTELLUNGEN).getAll()
          : tx.objectStore(STORE_ZUSTELLUNGEN).getAll(IDBKeyRange.bound([groupId], [groupId, []]))
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })
    return rohzeilen as unknown as GruppenZustellung[]
  },
  async schreibeZustellung(eintrag) {
    const db = await oeffneDatenbank()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_ZUSTELLUNGEN, 'readwrite')
      tx.objectStore(STORE_ZUSTELLUNGEN).put(eintrag)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
  async loescheZustellung(groupId, keyId) {
    const db = await oeffneDatenbank()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_ZUSTELLUNGEN, 'readwrite')
      tx.objectStore(STORE_ZUSTELLUNGEN).delete([groupId, keyId])
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
  async loescheGruppe(groupId) {
    const db = await oeffneDatenbank()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(
        [STORE_KEYS, STORE_AKTUELL, STORE_ANFRAGEN, STORE_GEHEIMNISSE, STORE_ZUSTELLUNGEN],
        'readwrite',
      )
      tx.objectStore(STORE_GEHEIMNISSE).delete(groupId)
      // Der zusammengesetzte Schlüssel beginnt mit der Gruppenkennung, also
      // trifft ein Bereich über [groupId] genau deren Schlüssel.
      tx.objectStore(STORE_KEYS).delete(IDBKeyRange.bound([groupId], [groupId, []]))
      tx.objectStore(STORE_AKTUELL).delete(groupId)
      tx.objectStore(STORE_ANFRAGEN).delete(IDBKeyRange.bound([groupId], [groupId, []]))
      tx.objectStore(STORE_ZUSTELLUNGEN).delete(IDBKeyRange.bound([groupId], [groupId, []]))
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

/** Haengt eine andere Ablage ein. Nur fuer Tests gedacht. */
export function setzeGruppenAblageFuerTest(neu: GruppenAblage | null): void {
  ablage = neu ?? indexedDbAblage
  leereGefragt()
}

export { ablage }
