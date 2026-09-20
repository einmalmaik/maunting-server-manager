// @vitest-environment node
/**
 * Der Weg einer Videonotiz vom Rekorder bis zu dem, was der Server annimmt.
 *
 * Diese Datei stand bis 09/2026 voller eigener Abschriften: eine Gestenfunktion,
 * die es im Produktionscode nicht mehr gab, ein erfundenes Verfahren
 * `sv-blob-v1:`, das nie eines war, und Objektliterale, auf die zwei Zeilen
 * später die Behauptung folgte. Fünfzehn Tests, kein einziger Import aus dem
 * Produktionscode. Deshalb fiel keiner der Fehler auf, die im September 2026
 * gemeldet wurden — allen voran der, dass eine lange Notiz beim Empfänger nie
 * ankam.
 *
 * Geprüft wird jetzt die Naht, die sonst niemand prüft: `CircularVideoNoteRecorder.test.tsx`
 * hört beim fertigen Blob auf, `medienKrypto.test.ts` fängt bei Bytes an. Was
 * dazwischen liegt, entscheidet darüber, ob die Notiz ankommt.
 *
 * Node-Umgebung wie beim Kryptotest: DIS prüft seine Eingaben mit
 * `instanceof Uint8Array`, und in jsdom kommt der Typ aus einem anderen Realm.
 * Das kostet React — Verhalten der Komponente gehört deshalb in deren eigene
 * Testdatei, nicht hierher.
 */

import { describe, expect, it } from 'vitest'

import { MAX_VIDEO_NOTE_DURATION_SEC, videoNotizBitraten, videoNotizBudgetBytes } from '@/lib/videoNotiz'
import {
  ANHANG_PREFIX,
  entschluesselePaket,
  maxAnhangBytes,
  maxKlartextBytes,
  verschluesselePaket,
  type MedienBindung,
} from '@/services/medienKrypto'

/** Muss `chat_media_validator.MAX_MEDIA_BYTES` entsprechen. */
const BLOB_DECKEL = 60 * 1024 * 1024

/** Der Deckel vor dem 20.09.2026. Steht hier als Beleg, nicht als Vorgabe. */
const ALTER_DECKEL = 25 * 1024 * 1024

function bindung(ueberschreiben: Partial<MedienBindung> = {}): MedienBindung {
  return { absenderId: 7, blindMailboxId: 'b'.repeat(32), fileId: 'videonotiz-1', ...ueberschreiben }
}

/**
 * Eine Aufnahme, wie der Rekorder sie liefert: MP4 mit `ftyp`-Box am Anfang.
 *
 * Der Inhalt ist nicht zufällig. Er muss sich nach dem Umlauf wiederfinden
 * lassen, und die Kennung am Anfang ist das, was im verschlüsselten Blob
 * *nicht* auftauchen darf.
 */
function aufnahme(bytes: number): Uint8Array {
  const daten = new Uint8Array(bytes)
  daten.set([0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70, 0x6d, 0x70, 0x34, 0x32], 0)
  for (let i = 12; i < bytes; i++) daten[i] = (i * 31) % 251
  return daten
}

/**
 * Derselbe Schritt wie `blobAlsDataUrl` in `Messenger.tsx`, ohne `FileReader`.
 *
 * Den gibt es in Node nicht. Das Ergebnis ist bitgleich: Base64 des Blobs mit
 * vorangestelltem Typ, und genau das kostet das Drittel, mit dem
 * `maxAnhangBytes()` rechnet.
 */
function alsDataUrl(daten: Uint8Array, mimeType: string): string {
  return `data:${mimeType};base64,${Buffer.from(daten).toString('base64')}`
}

describe('Die Kette hält', () => {
  it('gibt die Aufnahme bitgenau zurück', async () => {
    const roh = aufnahme(64 * 1024)
    const dataUrl = alsDataUrl(roh, 'video/mp4')

    const { blob, paketSchluessel } = await verschluesselePaket(dataUrl, bindung(), {
      name: 'videonotiz.mp4',
      mimeType: 'video/mp4',
    })
    const zurueck = await entschluesselePaket(blob, paketSchluessel, bindung())

    expect(zurueck).toBe(dataUrl)

    // An `;base64,` trennen, nicht am ersten Komma: ein Typ wie
    // `video/mp4;codecs=avc1,opus` bringt selbst eines mit.
    const nutzlast = Buffer.from(zurueck.slice(zurueck.indexOf(';base64,') + 8), 'base64')
    // Als Vergleich ein Wahrheitswert, kein `toEqual` über 64.000 Zahlen: bei
    // einem Unterschied baut vitest daraus sonst einen unlesbaren Bericht.
    expect(Buffer.compare(nutzlast, Buffer.from(roh))).toBe(0)
  }, 60_000)

  it('lässt das Aufnahmeformat mitreisen', async () => {
    // Der Rekorder gibt seit 09/2026 `recorder.mimeType` weiter statt eines
    // festen `video/webm`. Kommt der Typ nicht beim Empfänger an, spielt die
    // Datei dort nicht — je nach Gerät ist es H.264 in MP4 oder VP9 in WebM.
    const mimeType = 'video/webm;codecs=vp9,opus'
    const dataUrl = alsDataUrl(aufnahme(4096), mimeType)

    const { blob, paketSchluessel } = await verschluesselePaket(dataUrl, bindung(), {
      name: 'videonotiz.webm',
      mimeType,
    })

    expect(await entschluesselePaket(blob, paketSchluessel, bindung())).toContain(mimeType)
  }, 60_000)

  it('trägt kein rohes Video nach außen', async () => {
    // Das ist die Zusage, die der frühere `validateMediaPayloadEncrypted` zu
    // prüfen vorgab. Die Funktion gab am Ende unbedingt `false` zurück, ihre
    // Prüfungen darüber waren toter Code, und der Test stellte nur Fragen, auf
    // die `false` die richtige Antwort war.
    const roh = aufnahme(64 * 1024)
    const { blob } = await verschluesselePaket(alsDataUrl(roh, 'video/mp4'), bindung(), {
      name: 'privat.mp4',
      mimeType: 'video/mp4',
    })

    expect(blob.startsWith(ANHANG_PREFIX)).toBe(true)
    const huelle = Buffer.from(blob.slice(ANHANG_PREFIX.length), 'base64').toString('utf-8')
    expect(huelle).not.toContain('ftyp')
    expect(huelle).not.toContain('privat.mp4')
    expect(huelle).not.toContain('video/mp4')
  }, 60_000)
})

describe('Der Deckel', () => {
  it('nimmt an, was der Rekorder durchlässt', async () => {
    // Der gemeldete Fehler: kurze Notizen kamen an, lange nicht. Zwischen dem,
    // was der Größenwächter erlaubt, und dem, was der Server annimmt, lagen
    // mehrere Aufschläge — data-URL, Verschlüsselung, Base64 je Stück.
    //
    // Hochgerechnet statt ausgereizt: der Aufschlag wird an einer Probe
    // gemessen, das Verschlüsseln von 22 MB dauert Minuten und bewiese nichts
    // Zusätzliches. Dasselbe Verfahren wie in `medienKrypto.test.ts`.
    const probe = aufnahme(512 * 1024)
    const dataUrl = alsDataUrl(probe, 'video/mp4')
    const { blob } = await verschluesselePaket(dataUrl, bindung(), {
      name: 'probe.mp4',
      mimeType: 'video/mp4',
    })

    const aufschlag = blob.length / probe.length
    const groessteNotiz = videoNotizBudgetBytes()

    expect(Math.ceil(groessteNotiz * aufschlag)).toBeLessThanOrEqual(BLOB_DECKEL)
  }, 120_000)

  it('hätte dieselbe Notiz vor der Anhebung abgewiesen', async () => {
    // Beleg dafür, dass die Anhebung von 25 auf 60 MB den Fehler behebt und
    // nicht nur verschiebt. Fällt dieser Test, ist die Aufnahme so klein
    // geworden, dass der alte Deckel gereicht hätte — dann gehört die Anhebung
    // überprüft.
    const probe = aufnahme(512 * 1024)
    const { blob } = await verschluesselePaket(alsDataUrl(probe, 'video/mp4'), bindung(), {
      name: 'probe.mp4',
      mimeType: 'video/mp4',
    })

    const aufschlag = blob.length / probe.length
    expect(Math.ceil(videoNotizBudgetBytes() * aufschlag)).toBeGreaterThan(ALTER_DECKEL)
  }, 120_000)

  it('lässt dem Wächter Luft gegenüber der Prüfung im Sendepfad', () => {
    // Beide standen einmal auf derselben Zahl. Der Wächter schlägt erst an,
    // *nachdem* ein Stück das Budget erreicht hat — der fertige Blob liegt also
    // immer knapp darüber, und `handleSendMessage` warf genau die Aufnahmen
    // weg, die der Wächter gerade gerettet hatte.
    expect(videoNotizBudgetBytes()).toBeLessThan(maxAnhangBytes())
  })

  it('rechnet die Bitrate so, dass eine volle Minute hineinpasst', () => {
    // Die Zahl ist aus dem Deckel zurückgerechnet, nicht erfunden. Wer Deckel,
    // Dauer oder Bitrate ändert, muss hier vorbei.
    const { video, audio } = videoNotizBitraten()
    const vollesBudget = ((video + audio) * MAX_VIDEO_NOTE_DURATION_SEC) / 8

    expect(vollesBudget).toBeLessThanOrEqual(videoNotizBudgetBytes())
    expect(videoNotizBudgetBytes()).toBeLessThan(maxKlartextBytes())
  })
})
