/**
 * Kamera-Sicherung (Android): neue Aufnahmen aus DCIM gehen verschlüsselt in
 * den Tresor, auch bei geschlossener App und gesperrtem Tresor.
 *
 * Gesichert wird nur im Hintergrund-Job des Telefons (`KameraArbeit.kt`), auch
 * bei offener App: ein Weg (AGENTS.md Punkt 32). Er legt jede Aufnahme samt
 * Datensatz in den Posteingang, und eine App mit offenem Tresor macht daraus
 * einen Eintrag (`tresorEingang.ts`). Das Original geht Bit für Bit mit, mit
 * Aufnahmeort.
 *
 * Diese Datei richtet den Job ein (Posteingang, Sicherungszugang mit frischem
 * Nachweis), zeigt seinen Stand und gibt Speicher in der Galerie frei. Was der
 * Job weiß, liegt bei ihm (`KameraAblage.kt`), nicht in der Ablage der App.
 */
import { create } from 'zustand'

import {
  kameraAendern,
  kameraEinrichten,
  kameraJetzt,
  kameraStand,
  kameraVergessen,
  medienPapierkorb,
  medienPruefsumme,
  medienZugriff,
  sicherungSchluessel,
  type KameraStand,
  type MedienZugriff,
} from '@/desktop/tauri'
import { getEffectiveApiUrl } from '@/config/api'
import { angemeldetesKonto, beiKontowechsel } from '@/lib/angemeldetesKonto'
import { blobStand, sicherungszugangAnlegen, sicherungszugangEntfernen } from './tresorBlobApi'
import { posteingangEinrichten } from './tresorEingang'
import { sichtbareEintraege } from './tresorOrdner'
import { getPendingQueue, useVaultStore } from './vaultStore'

export type { KameraStand, KameraWarten } from '@/desktop/tauri'

export const useKameraSicherung = create<{ stand: KameraStand | null }>(() => ({ stand: null }))

/** Liest den Stand des Jobs in die Anzeige. */
export async function kameraStandLaden(): Promise<KameraStand | null> {
  const stand = await kameraStand().catch(() => null)
  useKameraSicherung.setState({ stand })
  return stand
}

/**
 * Was schon im Tresor liegt, als `medienId:sha256`; geht nicht noch einmal
 * hoch. Von jeder Gerätekennung: nach einem neuen Einrichten hat dasselbe
 * Telefon eine neue, und Kennung samt Prüfsumme meinen trotzdem dieselbe Datei.
 */
function bekannteAufnahmen(): string[] {
  const liste: string[] = []
  for (const item of useVaultStore.getState().items) {
    const q = item.datei?.quelle
    if (q) liste.push(`${q.medienId}:${q.sha256}`)
  }
  return liste
}

/** Der Job verschlüsselt in Rust, und dort ist der RSA-Schlüssel ein SPKI, kein JWK. */
async function rsaAlsSpki(jwk: string): Promise<string> {
  const schluessel = await crypto.subtle.importKey('jwk', JSON.parse(jwk), { name: 'RSA-OAEP', hash: 'SHA-256' }, true, ['encrypt'])
  const spki = new Uint8Array(await crypto.subtle.exportKey('spki', schluessel))
  let text = ''
  for (const b of spki) text += String.fromCharCode(b)
  return btoa(text)
}

export type Einschalten = 'ok' | 'eingang' | Exclude<MedienZugriff['stand'], 'voll'>

/**
 * Schaltet ein oder erneuert den Zugang: Zugriff auf die Aufnahmen,
 * Posteingang, Sicherungszugang (mit frischem Nachweis), dann der Job.
 * Gesichert wird, was ab jetzt aufgenommen wird; derselbe Tresor auf
 * demselben Gerät macht an der bisherigen Stelle weiter.
 *
 * Fehler beim Zugang (falscher Nachweis) kommen als Ausnahme.
 */
export async function kameraEinschalten(bucket: string, nachweis: Record<string, unknown>): Promise<Einschalten> {
  const zugriff = await medienZugriff(true)
  if (zugriff.stand !== 'voll') return zugriff.stand
  const konto = angemeldetesKonto()
  if (konto === null) return 'eingang'
  const alt = await kameraStand()
  const geraet = alt.eingerichtet && alt.bucket === bucket && alt.konto === konto ? alt.geraet : crypto.randomUUID()

  const eingang = await posteingangEinrichten(bucket, geraet, sicherungSchluessel).catch(() => null)
  if (!eingang) return 'eingang'
  const rsa = await rsaAlsSpki(eingang.rsaPublicKey)
  const zugang = await sicherungszugangAnlegen(bucket, nachweis)
  // Ab hier gibt es einen Zugang beim Server. Kommt er nicht beim Job an, fällt er wieder weg.
  try {
    // Nach jedem `await` gilt der vorher gelesene Zustand nicht mehr (Punkt 45).
    if (angemeldetesKonto() !== konto || useVaultStore.getState().bucketId !== bucket) {
      await sicherungszugangEntfernen().catch(() => {})
      return 'eingang'
    }
    const stand = await kameraEinrichten({
      konto,
      server: getEffectiveApiUrl() || window.location.origin,
      bucket,
      geraet,
      zugang,
      eingangId: eingang.id,
      pq: eingang.pqPublicKey,
      rsa,
      nurWlan: alt.eingerichtet ? alt.nurWlan : false,
    })
    useKameraSicherung.setState({ stand })
    return 'ok'
  } catch (err) {
    await sicherungszugangEntfernen().catch(() => {})
    throw err
  }
}

/** Schaltet aus: der Zugang fällt beim Server, Stand und offene Aufträge auf dem Telefon. */
export async function kameraAusschalten(): Promise<void> {
  await sicherungszugangEntfernen().catch(() => {})
  await kameraVergessen()
  useKameraSicherung.setState({ stand: { eingerichtet: false } })
}

export async function kameraNurWlan(nurWlan: boolean): Promise<void> {
  useKameraSicherung.setState({ stand: await kameraAendern({ nurWlan }) })
}

/** Auch Bildschirmfotos sichern, ab jetzt. */
export async function kameraScreenshots(screenshots: boolean): Promise<void> {
  useKameraSicherung.setState({ stand: await kameraAendern({ screenshots }) })
}

/** Sichert auch, was vor dem Einschalten aufgenommen wurde; was schon im Tresor liegt, nicht. */
export async function kameraVorhandeneSichern(): Promise<void> {
  useKameraSicherung.setState({ stand: await kameraAendern({ vorhandene: true, bekannt: bekannteAufnahmen() }) })
}

/**
 * Der Job gehört dem Konto, das ihn eingerichtet hat (Punkt 50). Meldet sich
 * ein anderes an, fällt er weg. Ohne Anmeldung bleibt er: eine bloß
 * abgelaufene Sitzung soll offene Aufträge nicht verlieren, und sein Zugang
 * gilt ohnehin nur, solange die Sitzung lebt.
 */
async function kontoPruefen(konto: number | null): Promise<void> {
  if (konto === null) return
  const stand = await kameraStand().catch(() => null)
  if (stand?.eingerichtet && stand.konto !== konto) await kameraVergessen()
  await kameraStandLaden()
}

/** Wie oft die Anzeige bei offener App nachliest, was der Job tut. */
const TAKT_MS = 15_000

/**
 * Hält die Anzeige aktuell und stößt den Job an, wenn die App wieder vorn ist
 * (der Auslöser für neue Aufnahmen hat bis zu einer Minute Verzug). Einmal
 * beim Start der App (nur Android).
 */
export function kameraBeobachten(): () => void {
  const vorn = () => {
    if (document.visibilityState !== 'visible') return
    void kameraJetzt()
      .then((stand) => useKameraSicherung.setState({ stand }))
      .catch(() => {})
  }
  const nachlesen = () => {
    if (document.visibilityState === 'visible') void kameraStandLaden()
  }
  void kontoPruefen(angemeldetesKonto())
  const abmelden = beiKontowechsel((konto) => void kontoPruefen(konto))
  const takt = window.setInterval(nachlesen, TAKT_MS)
  document.addEventListener('visibilitychange', vorn)
  return () => {
    abmelden()
    window.clearInterval(takt)
    document.removeEventListener('visibilitychange', vorn)
  }
}

export interface Freigabe {
  bilder: number[]
  videos: number[]
  bytes: number
}

function offenerBucket(): string | null {
  const s = useVaultStore.getState()
  return s.isUnlocked && s.userKey && s.bucketId ? s.bucketId : null
}

/**
 * Was sich aus der Galerie entfernen lässt: nur Aufnahmen dieses Geräts, deren
 * Eintrag beim Server angekommen ist, deren Original dort fertig liegt und
 * deren Datei in DCIM noch genau die gesicherten Bytes hat.
 */
export async function freigebbar(bucket: string, geraet: string): Promise<Freigabe> {
  const ergebnis: Freigabe = { bilder: [], videos: [], bytes: 0 }
  if (offenerBucket() !== bucket) return ergebnis
  const wartend = new Set(getPendingQueue(bucket).map((e) => e.id))
  for (const item of sichtbareEintraege(useVaultStore.getState().items)) {
    const q = item.datei?.quelle
    if (!q || q.geraet !== geraet || wartend.has(item.id)) continue
    const original = item.datei!.original
    const amServer = await blobStand(bucket, original.id).catch(() => null)
    if (amServer?.state !== 'fertig') continue
    const jetzt = await medienPruefsumme(q.medienId, q.art).catch(() => null)
    if (!jetzt || jetzt.sha256 !== q.sha256) continue
    if (offenerBucket() !== bucket) return { bilder: [], videos: [], bytes: 0 }
    ;(q.art === 'video' ? ergebnis.videos : ergebnis.bilder).push(q.medienId)
    ergebnis.bytes += jetzt.groesse
  }
  return ergebnis
}

/** Legt die Aufnahmen in den Papierkorb der Galerie. Android fragt selbst nach. */
export async function speicherFreigeben(freigabe: Freigabe): Promise<boolean> {
  if (freigabe.bilder.length + freigabe.videos.length === 0) return false
  return medienPapierkorb(freigabe.bilder, freigabe.videos)
}
