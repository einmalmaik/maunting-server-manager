import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { getE2eeGeraete } from '@/api/social'
import { entferneGeraet, gebeGeraetFrei, geraeteZuruecksetzen } from '@/services/e2eeGeraet'
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
}))

vi.mock('@/services/e2eeGeraet', () => ({
  eigenesGeraet: vi.fn(async () => ({
    kennung: 'dieses-geraet-0001',
    paar: { publicKeyJwk: 'pub', privateKeyJwk: 'priv' },
  })),
  sicherheitsnummer: vi.fn(async () => '11111 22222 33333 44444'),
  entferneGeraet: vi.fn(async () => undefined),
  gebeGeraetFrei: vi.fn(async () => undefined),
  geraeteZuruecksetzen: vi.fn(async () => undefined),
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

  it('entfernt ein Gerät erst nach Bestätigung — unterschrieben von diesem', async () => {
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))
    await bestaetige(true)

    // `entferneGeraet` unterschreibt und vergisst den Sendecache.
    await waitFor(() =>
      expect(entferneGeraet).toHaveBeenCalledWith(
        expect.objectContaining({ device_id: 'fremdes-geraet-02' }),
      ),
    )
    // Neu geladen wird danach: einmal beim Aufbau, einmal nach dem Entfernen.
    await waitFor(() => expect(getE2eeGeraete).toHaveBeenCalledTimes(2))
  })

  it('lässt das Gerät stehen, wenn die Bestätigung abgelehnt wird', async () => {
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))
    await bestaetige(false)

    await waitFor(() => expect(useConfirmStore.getState().pending).toBeNull())
    expect(entferneGeraet).not.toHaveBeenCalled()
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

  it('zeigt die Sicherheitsnummer und gibt ein wartendes Gerät frei', async () => {
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: true },
      { device_id: 'wartendes-geraet-03', public_key: 'pub-3', label: 'Zweitgerät', is_approved: false },
    ]
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText('Wartet auf Freigabe')).toBeInTheDocument()
    expect(screen.getAllByText(/Sicherheitsnummer: 11111 22222 33333 44444/)).toHaveLength(2)

    fireEvent.click(screen.getByRole('button', { name: /Freigeben/ }))
    await waitFor(() =>
      expect(gebeGeraetFrei).toHaveBeenCalledWith(
        expect.objectContaining({ device_id: 'wartendes-geraet-03' }),
      ),
    )
  })

  it('wartet dieses Gerät selbst, gibt es nichts frei und bietet den Neubeginn an', async () => {
    // Ein wartendes Gerät kann nicht unterschreiben — der Server nähme es
    // nicht an. Wer es in der Hand hat, hat vielleicht nur das Passwort.
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: false },
      { device_id: 'altes-geraet-0002', public_key: 'pub-2', label: 'Telefon', is_approved: true },
      { device_id: 'wartendes-geraet-03', public_key: 'pub-3', label: '', is_approved: false },
    ]
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText(/Dieses Gerät wartet auf Freigabe/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Freigeben/ })).toBeNull()
    // Nur das andere wartende Gerät lässt sich entfernen, das freigegebene nicht.
    expect(screen.getAllByRole('button', { name: /Entfernen/ })).toHaveLength(1)

    const neu = screen.getByRole('button', { name: 'Neu beginnen' })
    expect(neu).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Passwort'), { target: { value: 'geheim' } })
    fireEvent.click(neu)
    await waitFor(() => expect(geraeteZuruecksetzen).toHaveBeenCalledWith('geheim'))
  })
})
