/**
 * Ähnliche Fotos finden, auf dem Gerät.
 *
 * Je Miniatur ein Differenz-Hash (dHash, 64 Bit): das Bild auf 9 × 8 Graupunkte
 * verkleinert, je Bit „ist der linke Nachbar heller“. Bilder, deren Hashes in
 * höchstens `ABSTAND` Bits abweichen, landen in einer Gruppe: Serienbilder,
 * dasselbe Foto zweimal, eine verkleinerte Kopie. Flächige Bilder haben fast
 * alle denselben Hash; deshalb muss auch die mittlere Farbe passen.
 *
 * Die Hashes entstehen aus entschlüsselten Bildern. Sie werden deshalb weder
 * gespeichert noch synchronisiert, und sie hängen am Schlüssel der Sitzung:
 * nach dem Sperren sind sie weg.
 */

import type { VaultItem } from './vaultEintrag'
import { miniaturenLesen } from './tresorDateien'

export const ABSTAND = 6
/** Höchster Unterschied der mittleren Farbe (je Kanal, 0–255) zwischen zwei ähnlichen Bildern. */
export const FARBE = 24

/** 64 Bit als zwei vorzeichenlose 32-Bit-Hälften, dazu die mittlere Farbe als 0xRRGGBB. */
export type Hash = readonly [number, number, number]

/** dHash aus 9 × 8 Grauwerten (zeilenweise); ohne Farbe gilt der mittlere Grauwert. */
export function dHash(grau: ArrayLike<number>, farbe?: readonly [number, number, number]): Hash {
  let hoch = 0
  let tief = 0
  let bit = 0
  let summe = 0
  for (let i = 0; i < 72; i++) summe += grau[i]
  const [r, g, b] = (farbe ?? [summe / 72, summe / 72, summe / 72]).map((k) => Math.max(0, Math.min(255, Math.round(k))))
  for (let y = 0; y < 8; y++) {
    for (let x = 0; x < 8; x++) {
      const an = grau[y * 9 + x] > grau[y * 9 + x + 1] ? 1 : 0
      if (bit < 32) hoch = (hoch | (an << (31 - bit))) >>> 0
      else tief = (tief | (an << (63 - bit))) >>> 0
      bit++
    }
  }
  return [hoch, tief, (r << 16) | (g << 8) | b]
}

function farbeNah(a: number, b: number): boolean {
  for (const schieben of [16, 8, 0]) {
    if (Math.abs(((a >> schieben) & 0xff) - ((b >> schieben) & 0xff)) > FARBE) return false
  }
  return true
}

function bits(n: number): number {
  n = n - ((n >>> 1) & 0x55555555)
  n = (n & 0x33333333) + ((n >>> 2) & 0x33333333)
  return (((n + (n >>> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24
}

export function abstand(a: readonly number[], b: readonly number[]): number {
  return bits((a[0] ^ b[0]) >>> 0) + bits((a[1] ^ b[1]) >>> 0)
}

/**
 * Gruppen ähnlicher Einträge, jede mit mindestens zwei. Wer über einen
 * Dritten ähnlich ist, gehört in dieselbe Gruppe. Reihenfolge wie die Eingabe.
 */
export function gruppieren(hashes: ReadonlyMap<string, Hash>, grenze = ABSTAND): string[][] {
  const ids = [...hashes.keys()]
  const eltern = ids.map((_, i) => i)
  const wurzel = (i: number): number => {
    while (eltern[i] !== i) {
      eltern[i] = eltern[eltern[i]]
      i = eltern[i]
    }
    return i
  }
  const werte = ids.map((id) => hashes.get(id)!)
  for (let i = 0; i < ids.length; i++) {
    for (let j = i + 1; j < ids.length; j++) {
      if (abstand(werte[i], werte[j]) <= grenze && farbeNah(werte[i][2], werte[j][2])) {
        const a = wurzel(i)
        const b = wurzel(j)
        if (a !== b) eltern[Math.max(a, b)] = Math.min(a, b)
      }
    }
  }
  const gruppen = new Map<number, string[]>()
  ids.forEach((id, i) => {
    const w = wurzel(i)
    const g = gruppen.get(w)
    if (g) g.push(id)
    else gruppen.set(w, [id])
  })
  return [...gruppen.values()].filter((g) => g.length > 1)
}

const schonBerechnet = new WeakMap<CryptoKey, Map<string, Hash>>()

async function hashAusBild(bild: Uint8Array, leinwand: HTMLCanvasElement): Promise<Hash | null> {
  try {
    const bitmap = await createImageBitmap(new Blob([bild as BlobPart], { type: 'image/webp' }))
    const g = leinwand.getContext('2d', { willReadFrequently: true })
    if (!g) return null
    g.drawImage(bitmap, 0, 0, 9, 8)
    bitmap.close()
    const rgba = g.getImageData(0, 0, 9, 8).data
    const grau = new Uint8Array(72)
    const farbe: [number, number, number] = [0, 0, 0]
    for (let i = 0; i < 72; i++) {
      grau[i] = (rgba[i * 4] * 299 + rgba[i * 4 + 1] * 587 + rgba[i * 4 + 2] * 114) / 1000
      for (let k = 0; k < 3; k++) farbe[k] += rgba[i * 4 + k] / 72
    }
    return dHash(grau, farbe)
  } catch {
    return null
  }
}

/**
 * Hashes für alle Einträge mit Miniatur, in Paketen. Was sich nicht laden
 * oder lesen lässt, fehlt im Ergebnis.
 */
export async function hashesBerechnen(
  items: VaultItem[],
  userKey: CryptoKey,
  abgebrochen: () => boolean,
  fortschritt: (fertig: number) => void,
): Promise<Map<string, Hash>> {
  let merker = schonBerechnet.get(userKey)
  if (!merker) {
    merker = new Map()
    schonBerechnet.set(userKey, merker)
  }
  const ergebnis = new Map<string, Hash>()
  const leinwand = document.createElement('canvas')
  leinwand.width = 9
  leinwand.height = 8
  const offen = items.filter((i) => i.datei && i.datei.miniatur.echt > 0)
  for (let i = 0; i < offen.length; i += 100) {
    if (abgebrochen()) return ergebnis
    const paket = offen.slice(i, i + 100)
    const fehlend = paket.filter((p) => !merker.has(p.datei!.miniatur.id))
    if (fehlend.length > 0) {
      const bilder = await miniaturenLesen(
        fehlend.map((p) => ({ kopf: p.datei!.miniatur, eintragId: p.id })),
        userKey,
      )
      for (const p of fehlend) {
        const bild = bilder.get(p.datei!.miniatur.id)
        const hash = bild ? await hashAusBild(bild, leinwand) : null
        bild?.fill(0)
        if (hash) merker.set(p.datei!.miniatur.id, hash)
      }
    }
    for (const p of paket) {
      const h = merker.get(p.datei!.miniatur.id)
      if (h) ergebnis.set(p.id, h)
    }
    fortschritt(Math.min(offen.length, i + paket.length))
  }
  return ergebnis
}
