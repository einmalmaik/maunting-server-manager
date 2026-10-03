import { afterEach, describe, expect, it } from 'vitest'
import { randomUuidNachruesten, uuidV4 } from './uuidNachruesten'

const original = Object.getOwnPropertyDescriptor(crypto, 'randomUUID') ?? Object.getOwnPropertyDescriptor(Object.getPrototypeOf(crypto), 'randomUUID')
const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

describe('crypto.randomUUID nachrüsten (WebView vor Chrome 92)', () => {
  afterEach(() => {
    delete (crypto as { randomUUID?: unknown }).randomUUID
    if (original && !('randomUUID' in crypto)) Object.defineProperty(crypto, 'randomUUID', original)
  })

  it('rüstet nach, wenn die Funktion fehlt, mit Version 4 und Variante RFC', () => {
    Object.defineProperty(crypto, 'randomUUID', { value: undefined, configurable: true })
    randomUuidNachruesten()
    const ids = new Set(Array.from({ length: 200 }, () => crypto.randomUUID()))
    expect(ids.size).toBe(200)
    for (const id of ids) expect(id).toMatch(V4)
  })

  it('setzt Version und Variante auch bei Bytes, die sie nicht tragen', () => {
    expect(uuidV4(new Uint8Array(16).fill(0xff))).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff')
    expect(uuidV4(new Uint8Array(16))).toBe('00000000-0000-4000-8000-000000000000')
  })

  it('lässt eine vorhandene Funktion stehen', () => {
    const eigene = () => 'eigene' as `${string}-${string}-${string}-${string}-${string}`
    Object.defineProperty(crypto, 'randomUUID', { value: eigene, configurable: true })
    randomUuidNachruesten()
    expect(crypto.randomUUID()).toBe('eigene')
  })
})
