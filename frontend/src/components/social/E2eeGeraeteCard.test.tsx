import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { approveEigenesGeraet, deleteEigenesGeraet, getE2eeGeraete } from '@/api/social'
import { sicherheitsnummer, vergessenGeraete } from '@/services/e2eeGeraet'
import { useAuthStore } from '@/stores/authStore'
import { useConfirmStore } from '@/stores/confirmStore'
import { E2eeGeraeteCard } from './E2eeGeraeteCard'

const { liste } = vi.hoisted(() => ({
  liste: {
    inhalt: [] as { device_id: string; public_key: string; label: string; is_approved?: boolean }[],
    fehler: null as Error | null,
  },
}))

vi.mock('@/api/social', () => ({
  getE2eeGeraete: vi.fn(async () => {
    if (liste.fehler) throw liste.fehler
    return liste.inhalt
  }),
  deleteEigenesGeraet: vi.fn(async () => ({ ok: true })),
  approveEigenesGeraet: vi.fn(async () => ({ ok: true })),
}))

vi.mock('@/services/e2eeGeraet', () => ({
  eigenesGeraet: vi.fn(async () => ({
    kennung: 'dieses-geraet-0001',
    paar: { publicKeyJwk: 'pub', privateKeyJwk: 'priv' },
  })),
  sicherheitsnummer: vi.fn(async () => '11111 22222 33333 44444'),
  vergessenGeraete: vi.fn(),
  pruefeUndAktualisiereNeueGeraete: vi.fn(),
}))

/** Beantwortet den nächsten Bestätigungsdialog. */
async function bestaetige(antwort: boolean) {
  await waitFor(() => expect(useConfirmStore.getState().pending).not.toBeNull())
  useConfirmStore.getState().resolve(antwort)
}

describe('E2eeGeraeteCard', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    await i18n.changeLanguage('de')
    liste.fehler = null
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '' },
      { device_id: 'fremdes-geraet-02', public_key: 'pub-2', label: 'Arbeitsrechner' },
    ]
    useAuthStore.setState({ user: { id: 10, username: 'anna' } as any })
  })

  it('markiert das eigene Gerät und bietet dafür kein Entfernen an', async () => {
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText('Dieses Gerät')).toBeInTheDocument()
    expect(screen.getByText('Arbeitsrechner')).toBeInTheDocument()
    expect(screen.getByText('Unbenanntes Gerät')).toBeInTheDocument()

    // Genau ein Knopf: der für das fremde Gerät. Das eigene würde sich beim
    // nächsten Start sofort wieder eintragen — und im selben Tab nicht einmal
    // das, weil `geraetVeroeffentlichen` sich je Sitzung nur einmal meldet.
    expect(screen.getAllByRole('button', { name: /Entfernen/ })).toHaveLength(1)
  })

  it('entfernt ein Gerät erst nach Bestätigung und vergisst den Sendecache', async () => {
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))
    await bestaetige(true)

    await waitFor(() => expect(deleteEigenesGeraet).toHaveBeenCalledWith('fremdes-geraet-02'))
    // Ohne das Vergessen verschlüsselte dieser Tab bis zu zehn Minuten lang
    // weiter gegen die gerade entfernte Adresse.
    expect(vergessenGeraete).toHaveBeenCalledWith(10)
    // Neu geladen wird danach: einmal beim Aufbau, einmal nach dem Entfernen.
    await waitFor(() => expect(getE2eeGeraete).toHaveBeenCalledTimes(2))
  })

  it('lässt das Gerät stehen, wenn die Bestätigung abgelehnt wird', async () => {
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))
    await bestaetige(false)

    await waitFor(() => expect(useConfirmStore.getState().pending).toBeNull())
    expect(deleteEigenesGeraet).not.toHaveBeenCalled()
  })

  it('sagt es, wenn die Liste nicht geladen werden konnte', async () => {
    // Eine Karte, die bei einem Fehler einfach leer bleibt, sähe aus wie
    // „kein Gerät angemeldet" — und das ist die gefährlichere Aussage.
    liste.fehler = new Error('Netzwerk weg')
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText('Die Liste konnte nicht geladen werden.')).toBeInTheDocument()
  })

  it('zeigt die Kennung, damit zwei unbenannte Browser unterscheidbar sind', async () => {
    liste.inhalt = [
      { device_id: 'aaaaaaaaaaaa1111', public_key: 'pub-a', label: '' },
      { device_id: 'bbbbbbbbbbbb2222', public_key: 'pub-b', label: '' },
    ]
    render(<E2eeGeraeteCard />)

    // Die Kennung steht im Klartext in jedem Umschlag, sie verrät hier nichts.
    expect(await screen.findByText('aaaaaaaaaaaa')).toBeInTheDocument()
    expect(screen.getByText('bbbbbbbbbbbb')).toBeInTheDocument()
  })

  it('zeigt die Sicherheitsnummer und den Freigeben-Knopf für ausstehende Geräte', async () => {
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: true },
      { device_id: 'wartendes-geraet-03', public_key: 'pub-3', label: 'Zweitgerät', is_approved: false },
    ]
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText('Wartet auf Freigabe')).toBeInTheDocument()
    expect(screen.getByText('Zweitgerät')).toBeInTheDocument()
    expect(screen.getAllByText(/Sicherheitsnummer: 11111 22222 33333 44444/)).toHaveLength(2)

    const freigebenBtn = screen.getByRole('button', { name: /Freigeben/ })
    expect(freigebenBtn).toBeInTheDocument()

    fireEvent.click(freigebenBtn)
    await waitFor(() =>
      expect(approveEigenesGeraet).toHaveBeenCalledWith(
        'wartendes-geraet-03',
        'dieses-geraet-0001',
        undefined,
      ),
    )
    expect(vergessenGeraete).toHaveBeenCalledWith(10)
  })
})
