import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import type { VaultItem } from './vaultEintrag'
import { TresorBildeditor } from './TresorBildeditor'

/** jsdom lädt keine Bilder; die Attrappe meldet sich sofort als geladen. */
class BildAttrappe {
  onload: (() => void) | null = null
  naturalWidth = 400
  naturalHeight = 300
  set src(_url: string) {
    queueMicrotask(() => this.onload?.())
  }
}

const item = { id: 'foto', service: 'urlaub.jpg', datei: { typ: 'image/jpeg' } } as unknown as VaultItem

describe('TresorBildeditor auf dem Telefon', () => {
  beforeEach(async () => {
    vi.stubGlobal('Image', BildAttrappe)
    await i18n.changeLanguage('de')
  })
  afterEach(() => vi.unstubAllGlobals())

  it('gibt den Zuschnittgriffen eine unsichtbare Trefferfläche von 44 px', async () => {
    render(<TresorBildeditor item={item} vorschauUrl="blob:vorschau" onFertig={vi.fn()} />)
    const zuschnitt = await screen.findByTestId('zuschnitt')
    const griffe = zuschnitt.querySelectorAll('[data-griff]')
    expect(griffe).toHaveLength(4)
    for (const griff of griffe) {
      const klassen = griff.className.split(/\s+/)
      // 16 px sichtbar plus 2 × 14 px rundum.
      expect(klassen).toContain('h-4')
      expect(klassen).toContain('before:absolute')
      expect(klassen).toContain('before:-inset-3.5')
    }
  })

  it('macht die Knöpfe der Fußzeile 44 px groß und lässt sie umbrechen', async () => {
    render(<TresorBildeditor item={item} vorschauUrl="blob:vorschau" onFertig={vi.fn()} />)
    const fuss = screen.getByRole('button', { name: i18n.t('mss.vault.bearbeiten.links') }).closest('div.border-t')!
    const klassen = fuss.className.split(/\s+/)
    expect(klassen).toContain('max-sm:[&_button]:min-h-11')
    expect(klassen).toContain('max-sm:[&_button]:min-w-11')
    expect(klassen).toContain('flex-wrap')
    expect(fuss).toContainElement(screen.getByRole('button', { name: i18n.t('common.save') }))
  })
})
