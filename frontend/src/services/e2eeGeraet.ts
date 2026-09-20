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
 *
 * **Ein Schlüssel je Gerät *und* Konto.** Bis 09/2026 lag hier genau ein Paar
 * unter dem festen Namen `self`, mit der Begründung, ein Gerät sei ein Gerät,
 * egal wer sich anmelde. Das war falsch, und zwar zweifach: der private
 * Schlüssel öffnete danach die Post beider Konten, und weil die blinde Mailbox
 * bewusst jedem Angemeldeten offensteht, ist er die einzige Schranke davor.
 * Dazu stritten beide Konten um dieselben Ratchet-Sitzungen. Ein Browser, in
 * dem sich zwei Menschen nacheinander anmelden, ist kein seltener Fall, und
 * `clearSession` räumt die IndexedDB nicht mit ab.
 */

import { randomBytes } from '@msdis/shield/random'
import { bytesToHex } from '@msdis/shield/core'
import i18n from '@/i18n'

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { generateLocalE2eeKeyPair, type LocalE2eeKeyPair } from './e2eeCrypto'
import {
  MessengerVerschlossenError,
  entsiegleZeile,
  istOffen,
  versiegleZeile,
} from './lokaleVersiegelung'
import { uebernehmeAltbestand } from './ratchetSpeicher'
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

let geraetImRam: { konto: number; geraet: EigenesGeraet } | null = null
/**
 * Ein einziger Aufbau-Lauf, auch wenn mehrere Aufrufer gleichzeitig fragen.
 * Ohne diese Klammer erzeugten zwei parallele `eigenesGeraet()` zwei Paare, und
 * das zweite überschriebe das erste — mitsamt allen Sitzungen, die schon gegen
 * das erste laufen.
 *
 * Das Konto steht daneben, weil ein laufender Aufbau für Konto A die Antwort
 * für Konto B nicht sein darf.
 */
let aufbau: { konto: number; lauf: Promise<EigenesGeraet> } | null = null

/** Der Ablageschlüssel dieses Kontos. Vor 09/2026 hieß er für alle `self`. */
function ablageSchluessel(kontoId: number): string {
  return `konto:${kontoId}`
}

function oeffneDatenbank(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error(i18n.t('chat.errors.indexedDbUnavailable')))
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
        // Ein Eintrag je Konto, Schlüssel `konto:<id>`. Der Altbestand steht
        // unter `self` und wird von `uebernimmAltbestand` einmalig umgehängt.
        db.createObjectStore(IDB_GERAETE_STORE, { keyPath: 'id' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

/**
 * Eine abgelegte Zeile.
 *
 * `sitzungenUebernommen` ist der Haken hinter der einmaligen Umbenennung der
 * Ratchet-Sitzungen. Er steht hier und nicht in einer zweiten Ablage, weil er
 * genau dann etwas bedeutet, wenn es diese Zeile gibt. Fehlt er, gilt die
 * Umbenennung als offen: Zeilen aus der Übernahme tragen ihn erst, nachdem sie
 * gelaufen ist, und frisch erzeugte tragen ihn sofort, weil es bei ihnen nichts
 * zu übernehmen gibt.
 */
interface GeraeteZeile {
  geraet: EigenesGeraet
  sitzungenUebernommen: boolean
}

function ausZeile(zeile: any): GeraeteZeile | null {
  if (!zeile?.kennung || !zeile?.publicKeyJwk || !zeile?.privateKeyJwk) return null
  return {
    geraet: {
      kennung: zeile.kennung,
      paar: { publicKeyJwk: zeile.publicKeyJwk, privateKeyJwk: zeile.privateKeyJwk },
    },
    sitzungenUebernommen: zeile.sitzungenUebernommen === true,
  }
}

/** Bindet die Zeile eines Kontos an ihren Platz. Siehe `versiegleZeile`. */
function geraetAad(kontoId: number): string {
  return `msm-geraet:${ablageSchluessel(kontoId)}`
}

/**
 * Baut die Zeile und macht sie zu, falls ein Messenger-PIN eingerichtet ist.
 *
 * Nur `id` bleibt lesbar, das ist der Ablageschlüssel. Der private Teil des
 * Geräteausweises lag hier bis 09/2026 als JWK im Klartext — wer das Profil
 * kopierte, konnte sich als dieses Gerät ausgeben und mitlesen.
 */
async function zuZeile(
  kontoId: number,
  geraet: EigenesGeraet,
  sitzungenUebernommen: boolean,
): Promise<Record<string, unknown>> {
  return await versiegleZeile(
    {
      id: ablageSchluessel(kontoId),
      kennung: geraet.kennung,
      publicKeyJwk: geraet.paar.publicKeyJwk,
      privateKeyJwk: geraet.paar.privateKeyJwk,
      sitzungenUebernommen,
    },
    ['id'],
    geraetAad(kontoId),
  )
}

async function lies(kontoId: number): Promise<GeraeteZeile | null> {
  try {
    const db = await oeffneDatenbank()
    const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
      const tx = db.transaction(IDB_GERAETE_STORE, 'readonly')
      const req = tx.objectStore(IDB_GERAETE_STORE).get(ablageSchluessel(kontoId))
      req.onsuccess = () => resolve(req.result ?? null)
      req.onerror = () => reject(req.error)
    })
    return ausZeile(await entsiegleZeile(roh, geraetAad(kontoId)))
  } catch {
    return null
  }
}

async function schreibe(
  kontoId: number,
  geraet: EigenesGeraet,
  sitzungenUebernommen: boolean,
): Promise<void> {
  const db = await oeffneDatenbank()
  const zeile = await zuZeile(kontoId, geraet, sitzungenUebernommen)
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(IDB_GERAETE_STORE, 'readwrite')
    const req = tx.objectStore(IDB_GERAETE_STORE).put(zeile)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
  })
}

/**
 * Schreibt die Zeile dieses Kontos einmal neu — der Umstellungsdurchlauf für
 * den Geräteausweis. Siehe `schreibeNachrichtenBestandNeu`.
 */
export async function schreibeGeraetBestandNeu(kontoId: number): Promise<boolean> {
  const vorhanden = await lies(kontoId)
  if (!vorhanden) return false
  await schreibe(kontoId, vorhanden.geraet, vorhanden.sitzungenUebernommen)
  return true
}

/**
 * Hängt den alten `self`-Eintrag an das Konto, das sich als erstes meldet.
 *
 * Ohne diesen Schritt verlöre jede bestehende Installation beim Update ihren
 * Schlüssel und damit jede laufende Sitzung. Welchem Konto der Altbestand
 * gehört, steht nirgends — er hat nie eines getragen. Das erste Konto nach dem
 * Update ist die einzige verfügbare Antwort und fast immer die richtige: es ist
 * dasselbe, das den Browser vorher benutzt hat.
 *
 * Der Anspruch selbst liegt in **einer** Transaktion: zwei Tabs, die
 * gleichzeitig starten, sollen nicht beide denselben Altbestand übernehmen und
 * anschließend verschiedene Meinungen darüber haben, wem er gehört. Nur wer
 * `self` beim Zugreifen noch vorfindet, gewinnt.
 *
 * Das Versiegeln passiert davor, außerhalb der Transaktion — es ist asynchron
 * und eine IndexedDB-Transaktion überlebt kein `await`. Der Altbestand selbst
 * stammt aus der Zeit vor dem Siegel und liegt deshalb immer im Klartext.
 */
async function uebernimmAltbestand(kontoId: number): Promise<EigenesGeraet | null> {
  try {
    const db = await oeffneDatenbank()

    const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
      const tx = db.transaction(IDB_GERAETE_STORE, 'readonly')
      const req = tx.objectStore(IDB_GERAETE_STORE).get('self')
      req.onsuccess = () => resolve(req.result ?? null)
      req.onerror = () => reject(req.error)
    })
    const alt = ausZeile(await entsiegleZeile(roh, geraetAad(kontoId)))
    if (!alt) return null

    // Der Haken steht bewusst auf `false`: die Sitzungen sind noch nicht
    // umbenannt, und wenn das gleich scheitert, muss der nächste Start es
    // erneut versuchen dürfen.
    const zeile = await zuZeile(kontoId, alt.geraet, false)

    const gewonnen = await new Promise<boolean>((resolve, reject) => {
      const tx = db.transaction(IDB_GERAETE_STORE, 'readwrite')
      const store = tx.objectStore(IDB_GERAETE_STORE)
      let anspruch = false
      const req = store.get('self')
      req.onsuccess = () => {
        if (!req.result) return
        anspruch = true
        store.put(zeile)
        store.delete('self')
      }
      req.onerror = () => reject(req.error)
      tx.oncomplete = () => resolve(anspruch)
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })

    return gewonnen ? alt.geraet : null
  } catch {
    return null
  }
}

/**
 * Das angemeldete Konto. Kommt aus `lib/angemeldetesKonto`, nicht aus dem
 * Store: der holt sich `clearGeraeteMemory` von hier, ein Rückimport wäre ein
 * Zyklus.
 */
function meinKonto(): number {
  const id = angemeldetesKonto()
  if (id === null) {
    throw new Error(i18n.t('chat.errors.noE2eeIdentity'))
  }
  return id
}

/**
 * Die Identität dieses Geräts für das angemeldete Konto, bei Bedarf erzeugt.
 *
 * Ohne IndexedDB wirft das: ein Gerät, dessen Schlüssel jeden Neustart nicht
 * überlebt, kann keine Sitzung halten. Ein stiller Rückfall auf reinen
 * RAM-Betrieb sähe eine Minute lang aus wie ein funktionierender Messenger und
 * würde danach jedes Gespräch verlieren.
 *
 * Ohne angemeldetes Konto wirft es ebenfalls. Ein Rückfall auf einen über alle
 * Konten geteilten Schlüssel ist genau der Zustand, den diese Datei seit
 * 09/2026 nicht mehr herstellt.
 */
export async function eigenesGeraet(): Promise<EigenesGeraet> {
  const konto = meinKonto()

  /*
   * Bei gesperrtem Messenger hört das hier auf, und zwar bevor irgendetwas
   * gelesen wird.
   *
   * Der Grund steht ein paar Zeilen tiefer: findet `lies` nichts, gilt das als
   * „dieses Gerät hat noch keine Identität" und der Weg endet bei einem frisch
   * erzeugten Schlüsselpaar. Gesperrt findet `lies` aber **nie** etwas — die
   * Ablage gibt ohne Schlüssel nichts heraus. Ohne diese Schranke liefe jeder
   * Zugriff im gesperrten Zustand auf den Neuanlage-Zweig zu und versuchte, die
   * bestehende Identität zu überschreiben. Das Schreiben scheitert zwar
   * seinerseits am Siegel, aber der Fehler käme dann aus der Ablage und hiesse
   * irgendetwas — hier heisst er, was er ist.
   */
  if (!istOffen()) throw new MessengerVerschlossenError()

  if (geraetImRam?.konto === konto) return geraetImRam.geraet
  if (aufbau?.konto === konto) return aufbau.lauf

  const lauf = (async () => {
    let zeile = await lies(konto)
    if (!zeile) {
      const alt = await uebernimmAltbestand(konto)
      if (alt) zeile = { geraet: alt, sitzungenUebernommen: false }
    }
    if (zeile) {
      if (!zeile.sitzungenUebernommen) await holeSitzungenNach(konto, zeile.geraet)
      geraetImRam = { konto, geraet: zeile.geraet }
      return zeile.geraet
    }
    const frisch: EigenesGeraet = {
      kennung: bytesToHex(randomBytes(KENNUNG_BYTES)),
      paar: await generateLocalE2eeKeyPair(),
    }
    // Ein frisches Gerät hat nichts zu übernehmen: der Haken steht sofort.
    // Wichtig für ein **zweites** Konto in diesem Browser — die Sitzungen des
    // ersten gehören ihm nicht.
    await schreibe(konto, frisch, true)
    geraetImRam = { konto, geraet: frisch }
    return frisch
  })()
  aufbau = { konto, lauf }

  try {
    return await lauf
  } finally {
    aufbau = null
  }
}

/**
 * Benennt die Sitzungen um, die an dem übernommenen Schlüssel hängen.
 *
 * Beides gehört zusammen: die Ratchet-Zustände tragen seit 09/2026 die eigene
 * Gerätekennung im Namen, und die steht erst fest, wenn der Altbestand ein
 * Konto gefunden hat.
 *
 * Der Haken fällt erst, wenn das Umbenennen geglückt ist, und genau darum geht
 * es hier. In der Erstfassung lief die Umbenennung **einmal**, im Augenblick der
 * Übernahme. Genau das ging am laufenden System schief: der Schlüssel war
 * umgehängt, das Umbenennen scheiterte, `self` war weg — und damit gab es keinen
 * zweiten Versuch mehr. Der Browser stand mit einem gültigen Schlüssel und
 * lauter unauffindbaren Sitzungen da. Ein Fehlschlag darf einen Versuch kosten,
 * nicht den Gesprächsfaden.
 */
async function holeSitzungenNach(kontoId: number, geraet: EigenesGeraet): Promise<void> {
  try {
    await uebernehmeAltbestand(geraet.kennung)
    await schreibe(kontoId, geraet, true)
  } catch {
    // Beim nächsten Start noch einmal. `uebernehmeAltbestand` ist wiederholbar.
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

/**
 * Leert die RAM-Zwischenspeicher. Die IndexedDB überlebt das Abmelden.
 *
 * Der Schlüssel bleibt absichtlich liegen: wer sich wieder anmeldet, soll sein
 * Gespräch vorfinden und nicht jedes Mal eine neue Sitzung aufbauen. Dass er
 * dabei nicht mehr in fremde Hände gerät, regelt der Kontoschlüssel der Ablage,
 * nicht das Aufräumen hier.
 */
export function clearGeraeteMemory(): void {
  geraeteCache.clear()
  geraetImRam = null
  aufbau = null
  veroeffentlichtAls = null
}
