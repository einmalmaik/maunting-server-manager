/**
 * Die E2EE-Identität dieses Geräts — und es gibt nur diese eine.
 *
 * Vorher hing sie am Konto: ein RSA-Paar, dessen privater Teil mit einem
 * Argon2id-Schlüssel aus einem abgetippten Wiederherstellungsschlüssel verpackt
 * beim Server lag, damit jedes Gerät ihn holen konnte. Das war selbst schon die
 * zweite Reparatur eines Mehrgeräteproblems — davor überschrieb jedes neue
 * Gerät den Schlüssel des vorigen.
 *
 * Mit dem Double Ratchet ist ein geteilter privater Schlüssel nicht bloß
 * unnötig, sondern falsch. Der Ratchet ist eine lineare Kette: wer eine
 * Nachricht entschlüsselt, vernichtet dabei den Schlüssel, der sie geöffnet
 * hat. Zwei Geräte mit demselben privaten Schlüssel laufen darum unweigerlich
 * auseinander und verklemmen die Sitzung.
 *
 * Also gehört der Schlüssel jetzt dem Gerät. Er entsteht hier, er bleibt hier,
 * und veröffentlicht wird nur der öffentliche Teil. Kein Dialog, kein Code,
 * nichts zum Aufschreiben: ein Browser bekommt seine Identität nach dem
 * normalen Login, die Tauri-App und die APK nach dem Kopplungscode, den sie
 * ohnehin zum Anmelden einlösen.
 *
 * Was das kostet, steht bewusst hier: **ein Gerät, das seine IndexedDB
 * verliert, verliert seinen Gesprächsfaden.** Es meldet sich mit einem neuen
 * Schlüssel, die Gegenstellen bauen neue Sitzungen auf, und alles davor bleibt
 * unlesbar. Genau dafür gibt es den Verlaufs-Erstabgleich über die Kopplung.
 */

import { randomBytes } from '@msdis/shield/random'
import { bytesToHex } from '@msdis/shield/core'

import { generateLocalE2eeKeyPair, type LocalE2eeKeyPair } from './e2eeCrypto'
import {
  getE2eeGeraete,
  putEigenesGeraet,
  type E2eeGeraetItem,
} from '@/api/social'

/** 16 Bytes hex. Bedeutungsfrei — die Kennung steht im Klartext in jedem Umschlag. */
const KENNUNG_BYTES = 16

const IDB_DB_NAME = 'msm_e2ee_keystore'
const IDB_GERAETE_STORE = 'devices'

export interface EigenesGeraet {
  kennung: string
  paar: LocalE2eeKeyPair
}

// ==========================================
// Lokale Ablage
// ==========================================

let geraetImRam: EigenesGeraet | null = null
/**
 * Ein einziger Aufbau-Lauf, auch wenn mehrere Aufrufer gleichzeitig fragen.
 * Ohne diese Klammer erzeugten zwei parallele `eigenesGeraet()` zwei Paare, und
 * das zweite überschriebe das erste — mitsamt allen Sitzungen, die schon gegen
 * das erste laufen.
 */
let aufbau: Promise<EigenesGeraet> | null = null

function oeffneDatenbank(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error('IndexedDB nicht verfügbar'))
    }
    // Version 3: Store `devices` kommt hinzu. `keys` und `keyring` stammen aus
    // der Zeit des Kontoschlüssels und bleiben unangetastet — dort liegt das
    // Material, mit dem `altbestandUebernahme` den alten Verlauf einmal rettet.
    const req = indexedDB.open(IDB_DB_NAME, 3)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains('keys')) {
        db.createObjectStore('keys', { keyPath: 'userId' })
      }
      if (!db.objectStoreNames.contains('keyring')) {
        db.createObjectStore('keyring', { keyPath: 'userId' })
      }
      if (!db.objectStoreNames.contains(IDB_GERAETE_STORE)) {
        // Fester Schlüssel: es gibt genau ein Gerät je Installation, und das
        // ist unabhängig davon, wer sich gerade anmeldet.
        db.createObjectStore(IDB_GERAETE_STORE, { keyPath: 'id' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function lies(): Promise<EigenesGeraet | null> {
  try {
    const db = await oeffneDatenbank()
    return await new Promise<EigenesGeraet | null>((resolve, reject) => {
      const tx = db.transaction(IDB_GERAETE_STORE, 'readonly')
      const req = tx.objectStore(IDB_GERAETE_STORE).get('self')
      req.onsuccess = () => {
        const zeile = req.result
        resolve(
          zeile?.kennung && zeile?.publicKeyJwk && zeile?.privateKeyJwk
            ? {
                kennung: zeile.kennung,
                paar: { publicKeyJwk: zeile.publicKeyJwk, privateKeyJwk: zeile.privateKeyJwk },
              }
            : null
        )
      }
      req.onerror = () => reject(req.error)
    })
  } catch {
    return null
  }
}

async function schreibe(geraet: EigenesGeraet): Promise<void> {
  const db = await oeffneDatenbank()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(IDB_GERAETE_STORE, 'readwrite')
    const req = tx.objectStore(IDB_GERAETE_STORE).put({
      id: 'self',
      kennung: geraet.kennung,
      publicKeyJwk: geraet.paar.publicKeyJwk,
      privateKeyJwk: geraet.paar.privateKeyJwk,
    })
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
  })
}

/**
 * Die Identität dieses Geräts, bei Bedarf erzeugt.
 *
 * Ohne IndexedDB wirft das: ein Gerät, dessen Schlüssel jeden Neustart nicht
 * überlebt, kann keine Sitzung halten. Ein stiller Rückfall auf reinen
 * RAM-Betrieb sähe eine Minute lang aus wie ein funktionierender Messenger und
 * würde danach jedes Gespräch verlieren.
 */
export async function eigenesGeraet(): Promise<EigenesGeraet> {
  if (geraetImRam) return geraetImRam
  if (aufbau) return aufbau

  aufbau = (async () => {
    const vorhanden = await lies()
    if (vorhanden) {
      geraetImRam = vorhanden
      return vorhanden
    }
    const frisch: EigenesGeraet = {
      kennung: bytesToHex(randomBytes(KENNUNG_BYTES)),
      paar: await generateLocalE2eeKeyPair(),
    }
    await schreibe(frisch)
    geraetImRam = frisch
    return frisch
  })()

  try {
    return await aufbau
  } finally {
    aufbau = null
  }
}

// ==========================================
// Veröffentlichen
// ==========================================

let veroeffentlichtAls: string | null = null

/**
 * Meldet dieses Gerät beim Konto an. Idempotent und bei jedem Start fällig.
 *
 * Das erneute Melden ist kein Selbstzweck: es frischt `last_seen_at` auf, und
 * danach richtet sich der Gerätedeckel im Backend. Ein Gerät, das sich nie
 * wieder meldet, soll irgendwann Platz machen.
 */
export async function geraetVeroeffentlichen(label = ''): Promise<EigenesGeraet> {
  const geraet = await eigenesGeraet()
  if (veroeffentlichtAls === geraet.kennung) return geraet
  await putEigenesGeraet({
    deviceId: geraet.kennung,
    publicKey: geraet.paar.publicKeyJwk,
    label,
  })
  veroeffentlichtAls = geraet.kennung
  return geraet
}

// ==========================================
// Gegenstellen
// ==========================================

const CACHE_FRIST_MS = 600000

const geraeteCache = new Map<number, { geraete: E2eeGeraetItem[]; geholtAm: number }>()

/**
 * Die Geräte eines Kontos, zehn Minuten gecacht.
 *
 * Die Frist ist der Grund für den Cache, nicht die Ersparnis: ohne sie hielte
 * ein lange offener Tab eine veraltete Liste für immer fest und verschlüsselte
 * weiter gegen ein Gerät, das es nicht mehr gibt — oder gar nicht erst gegen
 * ein neues. `vergessenGeraete` räumt denselben Eintrag, wenn sich beim Senden
 * herausstellt, dass er nicht mehr stimmt.
 */
export async function geraeteVon(userId: number): Promise<E2eeGeraetItem[]> {
  try {
    return await holeGeraete(userId)
  } catch {
    return []
  }
}

/**
 * Derselbe Abruf, aber ohne die leere Liste als Notausgang.
 *
 * Wer nur anzeigt, kommt mit `geraeteVon` und einer leeren Liste zurecht. Wer
 * verschlüsselt, darf einen misslungenen Abruf nicht für „niemand angemeldet"
 * halten: ohne Antwort weiss niemand, ob ein Gerät da ist, und der Benutzer
 * bekäme eine Aussage über die Gegenstelle zu lesen, die gar nicht geprüft
 * wurde.
 */
async function holeGeraete(userId: number): Promise<E2eeGeraetItem[]> {
  const cached = geraeteCache.get(userId)
  if (cached && Date.now() - cached.geholtAm < CACHE_FRIST_MS) {
    return cached.geraete
  }
  try {
    const geraete = await getE2eeGeraete(userId)
    geraeteCache.set(userId, { geraete, geholtAm: Date.now() })
    return geraete
  } catch (fehler) {
    // Ein abgelaufener Eintrag ist immer noch besser als gar keiner: die
    // Geräteliste ändert sich selten, der Abruf scheitert oft nur kurz.
    if (cached) return cached.geraete
    throw fehler
  }
}

export function vergessenGeraete(userId: number): void {
  geraeteCache.delete(userId)
}

/** Der Empfänger hat noch kein Gerät angemeldet — es gibt nichts, wogegen verschlüsselt werden kann. */
export class E2eeKeinGeraetError extends Error {
  constructor(public readonly userId: number) {
    super('Für diesen Empfänger ist kein Gerät angemeldet')
    this.name = 'E2eeKeinGeraetError'
  }
}

/**
 * Wie `geraeteVon`, wirft aber statt eine leere Liste zu liefern.
 *
 * Der Sendepfad braucht diese Variante. Früher gab es an dieser Stelle einen
 * symmetrischen Notweg, dessen Schlüssel sich allein aus den beiden
 * Benutzerkennungen ergab — für den Server also nachbaubar. Lieber nicht senden
 * und es sagen, als eine Zusage brechen, die niemand nachprüfen kann.
 */
export async function verlangeGeraeteVon(userId: number): Promise<E2eeGeraetItem[]> {
  const geraete = await holeGeraete(userId)
  if (geraete.length === 0) throw new E2eeKeinGeraetError(userId)
  return geraete
}

/** Leert die RAM-Zwischenspeicher. Die IndexedDB überlebt das Abmelden. */
export function clearGeraeteMemory(): void {
  geraeteCache.clear()
  geraetImRam = null
  veroeffentlichtAls = null
}
