import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { TresorOrdnerBaum } from './TresorOrdnerBaum'
import type { VaultItem } from './vaultStore'

const ordner = (id: string, service: string, eltern?: string) => ({ id, service, category: 'ordner', ordner: eltern }) as VaultItem

const vertraege = ordner('v', 'Verträge')
const miete = ordner('m', 'Miete', 'v')
const alt = ordner('a', '2024', 'm')
const fotos = ordner('f', 'Fotos')
const urlaub = ordner('u', 'Urlaub', 'f')
const alle = [fotos, urlaub, alt, miete, vertraege]

describe('TresorOrdnerBaum', () => {
  it('zeigt vom Stammverzeichnis aus den Weg bis zum geöffneten Ordner', () => {
    render(<TresorOrdnerBaum ordner={alle} aktuell="m" pfad={[vertraege, miete]} onWaehlen={vi.fn()} />)

    expect(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.stamm') })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Fotos' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Verträge' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Miete' })).toHaveAttribute('aria-current', 'page')
    // Der geöffnete Ordner zeigt seine Unterordner, andere Zweige bleiben zu, bis man sie aufklappt.
    expect(screen.getByRole('button', { name: '2024' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Urlaub' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.aufklappen', { name: 'Fotos' }) }))
    expect(screen.getByRole('button', { name: 'Urlaub' })).toBeInTheDocument()
  })

  it('wählt Ordner und Stammverzeichnis', () => {
    const onWaehlen = vi.fn()
    render(<TresorOrdnerBaum ordner={alle} aktuell="v" pfad={[vertraege]} onWaehlen={onWaehlen} />)

    fireEvent.click(screen.getByRole('button', { name: 'Miete' }))
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.stamm') }))
    expect(onWaehlen.mock.calls).toEqual([['m'], [undefined]])
  })

  it('klappt den Weg wieder auf, wenn man einen Ordner darin öffnet', () => {
    const { rerender } = render(<TresorOrdnerBaum ordner={alle} aktuell="v" pfad={[vertraege]} onWaehlen={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.zuklappen', { name: 'Verträge' }) }))
    expect(screen.queryByRole('button', { name: 'Miete' })).not.toBeInTheDocument()

    rerender(<TresorOrdnerBaum ordner={alle} aktuell="a" pfad={[vertraege, miete, alt]} onWaehlen={vi.fn()} />)
    expect(screen.getByRole('button', { name: '2024' })).toHaveAttribute('aria-current', 'page')
  })

  // Ein `role="tree"` verspricht Pfeiltasten, die es hier nicht gibt; `aria-selected`
  // stand am Listeneintrag statt am Knopf. Der Baum ist eine Navigation aus Listen.
  it('ist eine Navigation ohne Baum-Rollen; nur der geöffnete Ordner trägt aria-current', () => {
    render(<TresorOrdnerBaum ordner={alle} aktuell="m" pfad={[vertraege, miete]} onWaehlen={vi.fn()} />)
    const nav = screen.getByRole('navigation', { name: i18n.t('mss.vault.dateien.ordnerBaum') })
    expect(nav.querySelector('[role="tree"], [role="treeitem"], [role="group"], [aria-selected]')).toBeNull()
    expect(nav.querySelectorAll('[aria-current]')).toHaveLength(1)
    expect(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.stamm') })).not.toHaveAttribute('aria-current')
    // Das Aufklappen meldet seinen Zustand selbst.
    expect(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.zuklappen', { name: 'Verträge' }) })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.aufklappen', { name: 'Fotos' }) })).toHaveAttribute('aria-expanded', 'false')
  })

  it('nimmt Abgelegtes an und klappt einen zugeklappten Ordner beim Darüberziehen auf', () => {
    vi.useFakeTimers()
    const onAblegen = vi.fn()
    const kannAblegen = (id: string | undefined) => id !== 'u'
    render(<TresorOrdnerBaum ordner={alle} aktuell={undefined} pfad={[]} onWaehlen={vi.fn()} ablage={{ kannAblegen, onAblegen }} />)
    const daten = { types: ['Files'], getData: () => '', files: [] }

    const fotosKnopf = screen.getByRole('button', { name: 'Fotos' })
    fireEvent.dragEnter(fotosKnopf, { dataTransfer: daten })
    expect(fireEvent.dragOver(fotosKnopf, { dataTransfer: daten })).toBe(false)
    expect(screen.queryByRole('button', { name: 'Urlaub' })).not.toBeInTheDocument()
    act(() => vi.advanceTimersByTime(800))
    const urlaub = screen.getByRole('button', { name: 'Urlaub' })
    // Was `kannAblegen` ablehnt, ist kein Ziel.
    expect(fireEvent.dragOver(urlaub, { dataTransfer: daten })).toBe(true)

    fireEvent.drop(screen.getByRole('button', { name: i18n.t('mss.vault.dateien.stamm') }), { dataTransfer: daten })
    expect(onAblegen).toHaveBeenCalledWith(undefined, expect.anything())
    vi.useRealTimers()
  })
})
