/**
 * Bearbeiten im Tresor: Zuschnitt, Drehen, Texteditor. Die Neuverschlüsselung
 * selbst prüft `tresorDateien.test.ts` („Bearbeiten“).
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { useConfirmStore } from '@/stores/confirmStore'
import { rahmenZiehen, zeichnen } from './TresorBildeditor'
import { TresorTexteditor } from './TresorTexteditor'
import { useVaultStore } from './vaultStore'
import { type VaultItem } from './vaultEintrag'

vi.mock('../tauri', () => ({
  FACH_TRESOR: 'vault_biometric_key',
  biometrieSpeichern: vi.fn(),
  biometrieEntsperren: vi.fn(),
  biometrieLoeschen: vi.fn(),
  pruefeBiometrieVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherVerfuegbar: vi.fn().mockResolvedValue(false),
  biometrieSpeicherFragtSelbst: vi.fn().mockResolvedValue(false),
  verifiziereBiometrie: vi.fn().mockResolvedValue(false),
  setzeTresorSchutz: vi.fn(),
}))

// CodeMirror braucht ein Layout, das jsdom nicht hat: ein Textfeld mit denselben Rückrufen.
vi.mock('@/components/server/FileEditorWorkspace', () => ({
  FileEditorWorkspace: ({ tabs, onChange, onSave, onClose }: {
    tabs: { path: string; content: string }[]
    onChange: (p: string, c: string) => void
    onSave: (p: string) => void
    onClose: (p: string) => void
  }) => (
    <div>
      <textarea aria-label="inhalt" value={tabs[0].content} onChange={(e) => onChange(tabs[0].path, e.target.value)} />
      <button type="button" onClick={() => onSave(tabs[0].path)}>speichern</button>
      <button type="button" onClick={() => onClose(tabs[0].path)}>tab-zu</button>
    </div>
  ),
}))

describe('Zuschnitt', () => {
  const ganz = { x: 0, y: 0, b: 1, h: 1 }

  it('bleibt im Bild und wird nicht kleiner als die Mindestgröße', () => {
    expect(rahmenZiehen(ganz, 'nw', 0.2, 0.1)).toEqual({ x: 0.2, y: 0.1, b: 0.8, h: 0.9 })
    expect(rahmenZiehen(ganz, 'se', 0.5, 0.5)).toEqual(ganz)
    const klein = rahmenZiehen(ganz, 'se', -2, -2)
    expect(klein.b).toBeCloseTo(0.05)
    expect(klein.h).toBeCloseTo(0.05)
    const nw = rahmenZiehen({ x: 0.5, y: 0.5, b: 0.2, h: 0.2 }, 'nw', 0.5, 0.5)
    expect(nw.x + nw.b).toBeCloseTo(0.7)
    expect(nw.b).toBeCloseTo(0.05)
  })

  it('verschiebt den Rahmen nur innerhalb des Bildes', () => {
    expect(rahmenZiehen({ x: 0.1, y: 0.1, b: 0.5, h: 0.5 }, 'mitte', 0.9, -0.9)).toEqual({ x: 0.5, y: 0, b: 0.5, h: 0.5 })
  })

  it('dreht vor dem Zuschneiden: ein Querformat wird hochkant', () => {
    const aufrufe: string[] = []
    const kontext = {
      scale: (x: number, y: number) => aufrufe.push(`scale ${x} ${y}`),
      translate: (x: number, y: number) => aufrufe.push(`translate ${x} ${y}`),
      rotate: (w: number) => aufrufe.push(`rotate ${Math.round((w * 180) / Math.PI)}`),
      drawImage: (_q: unknown, x: number, y: number) => aufrufe.push(`draw ${x} ${y}`),
    }
    const echt = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation(((tag: string) => {
      const el = echt(tag)
      if (tag === 'canvas') (el as HTMLCanvasElement).getContext = (() => kontext) as never
      return el
    }) as typeof document.createElement)

    const leinwand = zeichnen({} as CanvasImageSource, 400, 200, 90, { x: 0, y: 0.5, b: 1, h: 0.5 })
    expect([leinwand.width, leinwand.height]).toEqual([200, 200])
    expect(aufrufe).toEqual(['scale 1 1', 'translate 0 -200', 'translate 100 200', 'rotate 90', 'draw -200 -100'])
    vi.restoreAllMocks()
  })
})

describe('Texteditor', () => {
  const dateiErsetzen = vi.fn(async (_id: string, _inhalt: Blob) => undefined)
  const item = {
    id: 't1',
    service: 'notiz.txt',
    username: '',
    password: '',
    category: 'datei',
    createdAt: 1,
    updatedAt: 1,
    revision: 1,
    datei: { typ: 'text/plain' },
  } as unknown as VaultItem

  beforeEach(() => {
    dateiErsetzen.mockClear()
    useVaultStore.setState({ dateiErsetzen })
  })

  afterEach(() => useConfirmStore.setState({ pending: null }))

  it('speichert als neue Fassung und behält die Zeilenenden der Datei', async () => {
    const fertig = vi.fn()
    render(<TresorTexteditor item={item} text={'eins\r\nzwei'} onFertig={fertig} />)
    fireEvent.change(await screen.findByLabelText('inhalt'), { target: { value: 'eins\nzwei\ndrei' } })
    fireEvent.click(screen.getByText('speichern'))
    await vi.waitFor(() => expect(dateiErsetzen).toHaveBeenCalledTimes(1))
    const [id, blob] = dateiErsetzen.mock.calls[0]
    expect(id).toBe('t1')
    expect(await blob.text()).toBe('eins\r\nzwei\r\ndrei')
    expect(blob.type).toBe('text/plain')

    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.close') }))
    await vi.waitFor(() => expect(fertig).toHaveBeenCalledWith('eins\nzwei\ndrei'))
  })

  it('fragt vor dem Schließen, wenn noch etwas ungespeichert ist', async () => {
    const fertig = vi.fn()
    render(<TresorTexteditor item={item} text="alt" onFertig={fertig} />)
    fireEvent.change(await screen.findByLabelText('inhalt'), { target: { value: 'neu' } })
    fireEvent.click(screen.getByText('tab-zu'))
    await vi.waitFor(() => expect(useConfirmStore.getState().pending).not.toBeNull())
    useConfirmStore.getState().pending!.resolve(false)
    await Promise.resolve()
    expect(fertig).not.toHaveBeenCalled()
    expect(dateiErsetzen).not.toHaveBeenCalled()
  })

  // „Bestätigen“ sagt nicht, was passiert: der Knopf heißt wie die Folge.
  it('nennt den Knopf der Rückfrage „Verwerfen“', async () => {
    render(<TresorTexteditor item={item} text="alt" onFertig={vi.fn()} />)
    fireEvent.change(await screen.findByLabelText('inhalt'), { target: { value: 'neu' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.close') }))
    await vi.waitFor(() => expect(useConfirmStore.getState().pending).not.toBeNull())
    const frage = useConfirmStore.getState().pending!
    expect(frage.confirmText).toBe(i18n.t('mss.vault.bearbeiten.verwerfenKnopf'))
    expect(frage.confirmText).not.toBe(i18n.t('common.confirm'))
    expect(frage.danger).toBe(true)
  })

  it('schließt mit der Zurück-Taste, aber nicht mit Escape', async () => {
    await vi.waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    const fertig = vi.fn()
    render(<TresorTexteditor item={item} text="alt" onFertig={fertig} />)
    await screen.findByLabelText('inhalt')
    // Escape gehört der Suche im Editor.
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(fertig).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
    act(() => window.history.back())
    await vi.waitFor(() => expect(fertig).toHaveBeenCalledWith(null))
  })

  it('fragt bei Zurück nach, wenn noch etwas ungespeichert ist', async () => {
    await vi.waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    const fertig = vi.fn()
    render(<TresorTexteditor item={item} text="alt" onFertig={fertig} />)
    fireEvent.change(await screen.findByLabelText('inhalt'), { target: { value: 'neu' } })
    await vi.waitFor(() => expect(window.history.state?.msmTiefe).toBe(1))
    act(() => window.history.back())
    await vi.waitFor(() => expect(useConfirmStore.getState().pending).not.toBeNull())
    expect(fertig).not.toHaveBeenCalled()
  })
})
