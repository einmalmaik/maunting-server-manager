import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createRef } from 'react'

import { SeitenSicherheitPopover } from './SeitenSicherheitPopover'
import { nativ } from '../services/nativ'
import type { Tab } from '../services/tabsStore'

vi.mock('../services/nativ', () => ({
  nativ: {
    tabSeiteZuruecksetzen: vi.fn().mockResolvedValue(undefined),
  },
}))

vi.mock('@/stores/toastStore', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

describe('SeitenSicherheitPopover', () => {
  const anker = createRef<HTMLButtonElement>()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('rendert nichts, wenn offen false ist oder kein Tab existiert', () => {
    const { container } = render(
      <SeitenSicherheitPopover offen={false} onSchliessen={vi.fn()} anker={anker} tab={null} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('zeigt Domain und Verschlüsselungsstatus fuer HTTPS-Seiten an', () => {
    const tab: Tab = {
      id: 'tab-42',
      url: 'https://portal.azure.com/dashboard',
      titel: 'Azure Portal',
      geladen: true,
      privat: false,
    }

    render(
      <SeitenSicherheitPopover offen={true} onSchliessen={vi.fn()} anker={anker} tab={tab} />
    )

    expect(screen.getByText('portal.azure.com')).toBeInTheDocument()
    expect(screen.getByText(/Deine Verbindung zu dieser Seite ist geschützt/i)).toBeInTheDocument()
    const resetButton = screen.getByText('Seite zurücksetzen').closest('button')
    expect(resetButton).toBeInTheDocument()
  })

  it('ruft nativ.tabSeiteZuruecksetzen auf und schliesst das Popover beim Klick auf den Reset-Button', async () => {
    const onSchliessen = vi.fn()
    const tab: Tab = {
      id: 'tab-101',
      url: 'https://github.com/login',
      titel: 'GitHub',
      geladen: true,
      privat: false,
    }

    render(
      <SeitenSicherheitPopover offen={true} onSchliessen={onSchliessen} anker={anker} tab={tab} />
    )

    const resetButton = screen.getByText('Seite zurücksetzen').closest('button')!
    fireEvent.click(resetButton)

    expect(nativ.tabSeiteZuruecksetzen).toHaveBeenCalledWith('tab-101')
  })
})
