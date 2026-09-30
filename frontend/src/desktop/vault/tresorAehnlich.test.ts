import { describe, expect, it } from 'vitest'
import { abstand, dHash, gruppieren, type Hash } from './tresorAehnlich'

/** 9 × 8 Grauwerte aus einer Funktion der Position. */
function bild(f: (x: number, y: number) => number): number[] {
  const werte: number[] = []
  for (let y = 0; y < 8; y++) for (let x = 0; x < 9; x++) werte.push(Math.max(0, Math.min(255, Math.round(f(x, y)))))
  return werte
}

describe('Ähnliche Fotos', () => {
  const verlauf = bild((x, y) => 255 - x * 25 - y * 3)
  const heller = bild((x, y) => 255 - x * 25 - y * 3 + 10)
  const muster = bild((x, y) => ((x + y) % 2 ? 200 : 40))

  it('gibt einem Bild und seiner helleren Fassung denselben Hash', () => {
    expect(abstand(dHash(verlauf), dHash(heller))).toBe(0)
    expect(dHash(verlauf).slice(0, 2)).toEqual([0xffffffff, 0xffffffff])
  })

  it('trennt verschiedene Bilder deutlich', () => {
    expect(abstand(dHash(verlauf), dHash(muster))).toBeGreaterThan(20)
  })

  it('zählt abweichende Bits über beide Hälften', () => {
    expect(abstand([0, 0], [0x80000001, 0x3])).toBe(4)
    expect(abstand([0xffffffff, 0xffffffff], [0, 0])).toBe(64)
  })

  it('gruppiert auch über einen Dritten und lässt Einzelne weg', () => {
    const h = (hoch: number, farbe = 0x646464): Hash => [hoch >>> 0, 0, farbe]
    const gruppen = gruppieren(
      new Map<string, Hash>([
        ['a', h(0b0)],
        ['einzeln', h(0xffff0000)],
        ['b', h(0b1111)], // 4 von a
        ['c', h(0b11111111)], // 4 von b, 8 von a
        ['flach-dunkel', h(0b0, 0x141414)], // gleicher Hash wie a, aber viel dunkler
      ]),
      4,
    )
    expect(gruppen).toEqual([['a', 'b', 'c']])
  })

  it('hält flächige Bilder verschiedener Farbe auseinander, auch bei gleicher Helligkeit', () => {
    const flach = bild(() => 120)
    const rot = dHash(flach, [200, 90, 60])
    const gruen = dHash(flach, [60, 170, 90])
    const fastRot = dHash(flach, [210, 95, 50])
    expect(abstand(rot, gruen)).toBe(0)
    expect(gruppieren(new Map([['rot', rot], ['gruen', gruen], ['fastRot', fastRot]]))).toEqual([['rot', 'fastRot']])
    expect(gruppieren(new Map([['hell', dHash(bild(() => 200))], ['dunkel', dHash(bild(() => 90))]]))).toEqual([])
  })
})
