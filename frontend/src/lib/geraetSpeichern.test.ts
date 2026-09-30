import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const tauriKern = vi.hoisted(() => ({ invoke: vi.fn() }))
vi.mock('@tauri-apps/api/core', () => tauriKern)

import { exportSpeichern } from '@/services/datenexport'
import { blobTeile, inDerAppSpeichern } from './geraetSpeichern'

const MIB = 1024 * 1024

describe('Speichern auf dem Gerät (App)', () => {
  let geschrieben: Uint8Array[]
  let ende: unknown[]

  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    geschrieben = []
    ende = []
    tauriKern.invoke.mockImplementation(async (befehl: string, nutzlast: unknown) => {
      if (befehl === 'datei_speichern_start') return 5
      if (befehl === 'datei_speichern_teil') {
        // Android kennt nur JSON: nichts darf beim Serialisieren verloren gehen.
        expect(JSON.parse(JSON.stringify(nutzlast))).toEqual(nutzlast)
        const { teil } = nutzlast as { teil: string }
        geschrieben.push(Uint8Array.from(atob(teil), (z) => z.charCodeAt(0)))
        return null
      }
      if (befehl === 'datei_speichern_ende') {
        ende.push(nutzlast)
        return null
      }
      throw new Error(befehl)
    })
  })
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
    tauriKern.invoke.mockReset()
  })

  it('schickt den Datenexport in Teilen von 1 MiB und kommt vollständig an', async () => {
    const bytes = Uint8Array.from({ length: 2 * MIB + 17 }, (_, i) => (i * 31) % 256)

    expect(await exportSpeichern(new Blob([bytes]), 'msm-datenexport-2026-09-30.zip')).toBe(true)

    expect(tauriKern.invoke).toHaveBeenCalledWith('datei_speichern_start', { name: 'msm-datenexport-2026-09-30.zip' })
    expect(geschrieben.map((t) => t.length)).toEqual([MIB, MIB, 17])
    const zusammen = new Uint8Array(await new Blob(geschrieben as BlobPart[]).arrayBuffer())
    expect(zusammen.every((x, i) => x === bytes[i])).toBe(true)
    expect(ende).toEqual([{ vorgang: 5, abbrechen: false }])
  })

  it('verwirft die halbe Datei, wenn ein Teil nicht gelesen werden kann', async () => {
    async function* kaputt() {
      yield new Uint8Array([1, 2, 3])
      throw new Error('kaputt')
    }
    await expect(inDerAppSpeichern('x.bin', kaputt())).rejects.toThrow('kaputt')
    expect(geschrieben).toHaveLength(1)
    expect(ende).toEqual([{ vorgang: 5, abbrechen: true }])
  })

  it('teilt einen leeren Blob in nichts', async () => {
    const teile: Uint8Array[] = []
    for await (const teil of blobTeile(new Blob([]))) teile.push(teil)
    expect(teile).toEqual([])
  })
})
