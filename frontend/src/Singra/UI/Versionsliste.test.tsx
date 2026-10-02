import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { Versionsliste } from './Versionsliste'

const versionen = [
  { id: 'v2', zeit: Date.UTC(2026, 9, 1, 12), groesse: 2048 },
  { id: 'v1', zeit: Date.UTC(2026, 8, 30, 9), groesse: 1024 },
]

describe('Versionsliste', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('stellt eine Fassung wieder her und sperrt, solange das läuft', () => {
    const onWiederherstellen = vi.fn()
    const { rerender } = render(<Versionsliste versionen={versionen} onWiederherstellen={onWiederherstellen} />)
    const knoepfe = screen.getAllByRole('button', { name: i18n.t('common.versionen.wiederherstellen') })
    expect(knoepfe).toHaveLength(2)
    fireEvent.click(knoepfe[1])
    expect(onWiederherstellen).toHaveBeenCalledWith('v1')
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()

    rerender(<Versionsliste versionen={versionen} onWiederherstellen={onWiederherstellen} laeuft="v1" />)
    for (const knopf of screen.getAllByRole('button', { name: i18n.t('common.versionen.wiederherstellen') })) expect(knopf).toBeDisabled()
  })

  it('macht „Wiederherstellen“ auf dem Telefon 44 px hoch', () => {
    render(<Versionsliste versionen={versionen} onWiederherstellen={vi.fn()} />)
    for (const knopf of screen.getAllByRole('button', { name: i18n.t('common.versionen.wiederherstellen') })) {
      expect(knopf.className.split(/\s+/)).toContain('max-sm:min-h-11')
    }
  })

  it('zeigt ohne Schreibrecht nur an und sagt, wenn es nichts gibt', () => {
    const { rerender } = render(<Versionsliste versionen={versionen} />)
    expect(screen.queryByRole('button')).toBeNull()
    rerender(<Versionsliste versionen={[]} />)
    expect(screen.getByText(i18n.t('common.versionen.leer'))).toBeInTheDocument()
  })
})
