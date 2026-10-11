import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { KATEGORIEN, sichtbareKategorien } from './kategorien'
import { EinstellungenSeite } from './EinstellungenSeite'
import { useSitzung } from '../services/sitzung'
import { useTabsStore } from '../services/tabsStore'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn().mockResolvedValue(null),
}))

vi.mock('./Allgemein', () => ({
  Allgemein: () => <div data-testid="mock-allgemein">Allgemein</div>,
}))

vi.mock('@/desktop/einstellungsreiter/SicherheitEinstellungen', () => ({
  SicherheitEinstellungen: ({ offline }: { offline?: boolean }) => (
    <div data-testid="sicherheit-einstellungen" data-offline={offline ? 'true' : 'false'}>
      <span>Sicherheit-Inhalt</span>
    </div>
  ),
}))

function Seite() {
  const url = useTabsStore((s) => s.tabs.find((t) => t.id === s.aktivId)?.url ?? '')
  return <EinstellungenSeite teil={url.split('/')[3] ?? null} />
}

const renderSeite = () =>
  render(
    <MemoryRouter>
      <Seite />
    </MemoryRouter>,
  )

describe('Sicherheit in den Browsereinstellungen', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    usePublicSettingsStore.setState({ social_enabled: true, vault_enabled: true })
    useTabsStore.setState({
      tabs: [{ id: 'tab-e', url: 'msb://einstellungen/allgemein', titel: 'Einstellungen', privat: false, nativDa: true }],
      aktivId: 'tab-e',
    })
  })

  it('Metadaten: Sicherheit ist unterpunkt und folgt auf dienste', () => {
    const diensteIndex = KATEGORIEN.findIndex((k) => k.id === 'dienste')
    const sicherheitIndex = KATEGORIEN.findIndex((k) => k.id === 'sicherheit')
    expect(sicherheitIndex).toBe(diensteIndex + 1)
    expect(KATEGORIEN[sicherheitIndex].unterpunkt).toBe(true)
  })

  it('sichtbareKategorien filtert Sicherheit heraus, wenn nicht gekoppelt', () => {
    expect(sichtbareKategorien(false, false).some((k) => k.id === 'sicherheit')).toBe(false)
    expect(sichtbareKategorien(true, false).some((k) => k.id === 'sicherheit')).toBe(false)
    expect(sichtbareKategorien(false, true).some((k) => k.id === 'sicherheit')).toBe(true)
    expect(sichtbareKategorien(true, true).some((k) => k.id === 'sicherheit')).toBe(true)
  })

  it('Navigation: ungekoppelt zeigt weder Desktop noch Mobile die Sicherheitskategorie', () => {
    useSitzung.setState({ stand: 'aus' })
    renderSeite()
    const nav = screen.getByRole('navigation', { name: 'Einstellungen' })
    expect(within(nav).queryByRole('button', { name: 'Sicherheit' })).not.toBeInTheDocument()
    const tablist = screen.getByRole('tablist', { name: 'Einstellungen' })
    expect(within(tablist).queryByRole('tab', { name: /Sicherheit/i })).not.toBeInTheDocument()
  })

  it('Navigation: gekoppelt erscheint Sicherheit visuell eingerückt unter MSM-Dienste und in mobiler TabBar', async () => {
    useSitzung.setState({ stand: 'an' })
    renderSeite()
    const nav = screen.getByRole('navigation', { name: 'Einstellungen' })
    const knopf = within(nav).getByRole('button', { name: 'Sicherheit' })
    expect(knopf).toBeInTheDocument()
    expect(knopf.className).toContain('ml-3')

    const tablist = screen.getByRole('tablist', { name: 'Einstellungen' })
    const tab = within(tablist).getByRole('tab', { name: /Sicherheit/i })
    expect(tab).toBeInTheDocument()

    // Klick auf mobilen Tab öffnet Sicherheit
    await act(async () => {
      fireEvent.click(tab)
    })
    expect(useTabsStore.getState().tabs[0].url).toBe('msb://einstellungen/sicherheit')
  })

  it('Inhalt: gekoppelt-online lädt Sicherheitskarten', async () => {
    useSitzung.setState({ stand: 'an' })
    useTabsStore.getState().einstellungen('sicherheit')
    renderSeite()
    await waitFor(() => {
      expect(screen.getByTestId('sicherheit-einstellungen')).toBeInTheDocument()
    })
    expect(screen.getByTestId('sicherheit-einstellungen')).toHaveAttribute('data-offline', 'false')
  })

  it('Inhalt: gekoppelt-offline ist zugänglich und übergibt offline-Flag', async () => {
    useSitzung.setState({ stand: 'offline' })
    useTabsStore.getState().einstellungen('sicherheit')
    renderSeite()
    await waitFor(() => {
      expect(screen.getByTestId('sicherheit-einstellungen')).toBeInTheDocument()
    })
    expect(screen.getByTestId('sicherheit-einstellungen')).toHaveAttribute('data-offline', 'true')
  })

  it('Gating: beim Start mit stand="pruefen" wartet die Seite ohne Fehlermeldung', () => {
    useSitzung.setState({ stand: 'pruefen' })
    useTabsStore.getState().einstellungen('sicherheit')
    renderSeite()
    expect(screen.queryByTestId('sicherheit-einstellungen')).not.toBeInTheDocument()
    expect(screen.queryByText('Nicht mit MSM gekoppelt')).not.toBeInTheDocument()
  })

  it('Gating: direkte Adresse ungekoppelt zeigt Kopplungshinweis statt Karten und navigiert per Klick zu /koppeln', () => {
    useSitzung.setState({ stand: 'aus' })
    useTabsStore.getState().einstellungen('sicherheit')
    renderSeite()
    expect(screen.queryByTestId('sicherheit-einstellungen')).not.toBeInTheDocument()
    expect(screen.getByText('Nicht mit MSM gekoppelt')).toBeInTheDocument()
    const koppelnKnopf = screen.getByRole('button', { name: 'Koppeln' })
    expect(koppelnKnopf).toBeInTheDocument()
    fireEvent.click(koppelnKnopf)
    expect(mockNavigate).toHaveBeenCalledWith('/koppeln')
  })

  it('Gating: direkte Adresse abgelehnt zeigt Kopplungshinweis', () => {
    useSitzung.setState({ stand: 'abgelehnt' })
    useTabsStore.getState().einstellungen('sicherheit')
    renderSeite()
    expect(screen.queryByTestId('sicherheit-einstellungen')).not.toBeInTheDocument()
    expect(screen.getByText('Nicht mit MSM gekoppelt')).toBeInTheDocument()
  })

  it('Trennung: bei Abmeldung während offener Sicherheitsseite verschwinden die Karten sofort', async () => {
    useSitzung.setState({ stand: 'an' })
    useTabsStore.getState().einstellungen('sicherheit')
    renderSeite()
    await waitFor(() => {
      expect(screen.getByTestId('sicherheit-einstellungen')).toBeInTheDocument()
    })

    act(() => {
      useSitzung.setState({ stand: 'aus' })
    })

    expect(screen.queryByTestId('sicherheit-einstellungen')).not.toBeInTheDocument()
    expect(screen.getByText('Nicht mit MSM gekoppelt')).toBeInTheDocument()
  })
})
