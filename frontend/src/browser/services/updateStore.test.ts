/**
 * UpdateBadge des Browsers: Unter Windows erscheint es erst, wenn das Paket
 * fertig vorbereitet ist. Eine nur verfügbare Version und die Ladephase bleiben
 * unsichtbar; ein Fehler lässt sich vom Badge aus wiederholen.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { ereignisse, gerufen } = vi.hoisted(() => ({
  ereignisse: new Map<string, (event: { payload: unknown }) => void>(),
  gerufen: [] as string[],
}))

vi.mock('@tauri-apps/api/event', () => ({
  listen: (name: string, rueckruf: (event: { payload: unknown }) => void) => {
    ereignisse.set(name, rueckruf)
    return Promise.resolve(() => ereignisse.delete(name))
  },
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string) => {
    gerufen.push(befehl)
    return Promise.resolve(null)
  },
}))

import { useUpdateStore } from './updateStore'

function melden(name: string, payload: unknown) {
  ereignisse.get(name)?.({ payload })
}

describe('UpdateBadge des Browsers', () => {
  beforeEach(() => {
    ereignisse.clear()
    gerufen.length = 0
    useUpdateStore.setState({
      sichtbar: false,
      neueVersion: null,
      phase: 'verfuegbar',
      prozent: 0,
      fehlerText: null,
      initialisiert: false,
    })
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
      transformCallback: () => 0,
    }
    useUpdateStore.getState().initialisiereLauscher()
  })

  it('zeigt eine nur verfügbare Version nicht an', () => {
    melden('updater:verfuegbar', { version: '5.2.0' })
    const zustand = useUpdateStore.getState()
    expect(zustand.sichtbar).toBe(false)
    expect(zustand.neueVersion).toBe('5.2.0')
  })

  it('bleibt während des stillen Downloads unsichtbar und erscheint erst als bereit', () => {
    melden('updater:status', { status: 'laedt', prozent: 40 })
    expect(useUpdateStore.getState().sichtbar).toBe(false)
    expect(useUpdateStore.getState().prozent).toBe(40)

    melden('updater:bereit', { version: '5.2.0' })
    const zustand = useUpdateStore.getState()
    expect(zustand.sichtbar).toBe(true)
    expect(zustand.phase).toBe('bereit')
    expect(zustand.prozent).toBe(100)
  })

  it('startet aus dem bereiten Zustand nur neu, ohne erneut zu laden', async () => {
    melden('updater:bereit', { version: '5.2.0' })
    await useUpdateStore.getState().installieren()
    expect(gerufen).toEqual(['update_installieren'])
  })

  it('meldet einen Fehler sichtbar und lädt beim Klick erneut', async () => {
    melden('updater:status', { status: 'fehler', fehler: 'Feed nicht erreichbar' })
    const zustand = useUpdateStore.getState()
    expect(zustand.sichtbar).toBe(true)
    expect(zustand.phase).toBe('fehler')
    expect(zustand.fehlerText).toBe('Feed nicht erreichbar')

    await useUpdateStore.getState().installieren()
    expect(gerufen).toEqual(['update_herunterladen'])
  })

  it('blendet die Anzeige aus, sobald Android die Installation startet', () => {
    melden('updater:bereit', { version: '5.2.0' })
    melden('updater:status', { status: 'installiert_android' })
    const zustand = useUpdateStore.getState()
    expect(zustand.sichtbar).toBe(false)
    expect(zustand.phase).toBe('installiert_android')
  })
})
