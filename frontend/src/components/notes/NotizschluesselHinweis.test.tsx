/**
 * Der Hinweis auf Notizen und Kalender: nur wenn dieses Gerät den Schlüssel
 * des Kontos nicht hat, und dann mit dem Grund.
 *
 * Bis 5.0.3 stand dort nichts. Ein Gerät hinter dem Messenger-PIN meldete sich
 * nie, wurde nie freigegeben, und Web und Desktop-App zeigten die Einträge des
 * jeweils anderen als `sv-note-v1:…` — ohne ein Wort, warum.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { MessengerVerschlossenError } from '@/services/lokaleVersiegelung'
import { useAuthStore } from '@/stores/authStore'

const { lage } = vi.hoisted(() => ({
  lage: {
    stand: 'wartet' as string,
    meins: true as boolean | null,
    liste: [] as { device_id: string; is_approved: boolean }[],
    veroeffentlichen: null as Error | null,
    server: 'kontoabdruck' as string | null | Error,
    eigen: 'eigener-abdruck' as string | null,
  },
}))

vi.mock('@/api/notizschluessel', () => ({
  holeKontoschluessel: vi.fn(async () => {
    if (lage.server instanceof Error) throw lage.server
    return { abdruck: lage.server, stand: 3, geraet: 'web', signatur: 'x' }
  }),
}))
vi.mock('@/api/social', () => ({
  getE2eeGeraete: vi.fn(async () => lage.liste),
}))
vi.mock('@/services/e2eeGeraet', () => ({
  geraetVeroeffentlichen: vi.fn(async () => {
    if (lage.veroeffentlichen) throw lage.veroeffentlichen
    return { kennung: 'dieses-geraet' }
  }),
}))
vi.mock('@/services/notesCalendarCrypto', () => ({
  kontoschluesselAbgleichen: vi.fn(async () => lage.stand),
  eigenerIstKontoschluessel: vi.fn(async () => lage.meins),
  eigenerAbdruck: vi.fn(async () => lage.eigen),
  kontoAbdruckVergessen: vi.fn(),
}))

import { geraetVeroeffentlichen } from '@/services/e2eeGeraet'
import { kontoAbdruckVergessen, kontoschluesselAbgleichen } from '@/services/notesCalendarCrypto'
import { NotizschluesselHinweis } from './NotizschluesselHinweis'

/** Bis die Pruefung durch ist: erst dann sagt ein leerer Schirm etwas. */
async function geprueft(aufruf: unknown) {
  await waitFor(() => expect(aufruf).toHaveBeenCalled())
  await act(async () => {
    await new Promise((r) => setTimeout(r, 20))
  })
}

function zeige() {
  return render(
    <MemoryRouter>
      <NotizschluesselHinweis />
    </MemoryRouter>,
  )
}

describe('NotizschluesselHinweis', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    await i18n.changeLanguage('de')
    lage.stand = 'wartet'
    lage.meins = false
    lage.liste = [{ device_id: 'dieses-geraet', is_approved: true }]
    lage.veroeffentlichen = null
    lage.server = 'kontoabdruck'
    lage.eigen = 'eigener-abdruck'
    useAuthStore.setState({ user: { id: 3, username: 'anna' } as never })
  })

  it('schweigt, wenn dieses Geraet den Schluessel des Kontos hat — auch mit gesperrtem Messenger', async () => {
    // Der Messenger sperrt sich nach Frist von selbst. Ein freigegebenes
    // Geraet mit Schluessel soll dann nicht mahnen.
    lage.stand = 'passt'
    lage.veroeffentlichen = new MessengerVerschlossenError()
    const { container } = zeige()
    await geprueft(kontoschluesselAbgleichen)
    expect(container).toBeEmptyDOMElement()
    // Gar nicht erst nach dem Grund gesucht.
    expect(geraetVeroeffentlichen).not.toHaveBeenCalled()
  })

  it('schweigt bei gesperrtem Messenger, wenn der Server den eigenen Schluessel als den des Kontos nennt', async () => {
    // So startet die Desktop-App mit PIN jedes Mal: gesperrt, der Abgleich
    // kann nicht pruefen. Der Schluessel ist trotzdem der richtige.
    lage.stand = 'unbekannt'
    lage.meins = null
    lage.veroeffentlichen = new MessengerVerschlossenError()
    lage.eigen = 'kontoabdruck'
    const { container } = zeige()
    await geprueft(kontoschluesselAbgleichen)
    expect(container).toBeEmptyDOMElement()
    expect(geraetVeroeffentlichen).not.toHaveBeenCalled()
  })

  it('schweigt bei gesperrtem Messenger ohne Netz', async () => {
    lage.stand = 'unbekannt'
    lage.meins = null
    lage.veroeffentlichen = new MessengerVerschlossenError()
    lage.server = new TypeError('Failed to fetch')
    const { container } = zeige()
    await geprueft(kontoschluesselAbgleichen)
    expect(container).toBeEmptyDOMElement()
    expect(geraetVeroeffentlichen).not.toHaveBeenCalled()
  })

  it('nennt den Messenger-PIN, wenn der Geraeteschluessel versiegelt ist', async () => {
    lage.veroeffentlichen = new MessengerVerschlossenError()
    zeige()
    expect(await screen.findByText(i18n.t('notes.schluesselLage.gesperrt.titel'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: i18n.t('notes.schluesselLage.gesperrt.link') })).toHaveAttribute('href', '/chat')
  })

  it('nennt die fehlende Freigabe und verschwindet, sobald sie in Echtzeit kommt', async () => {
    lage.liste = [{ device_id: 'dieses-geraet', is_approved: false }]
    zeige()
    expect(await screen.findByText(i18n.t('notes.schluesselLage.freigabe.titel'))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: i18n.t('notes.schluesselLage.freigabe.link') })).toHaveAttribute(
      'href',
      '/profile?tab=devices',
    )

    // Freigegeben auf dem anderen Geraet, der Schluessel kommt an.
    lage.liste = [{ device_id: 'dieses-geraet', is_approved: true }]
    lage.stand = 'passt'
    act(() => {
      window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: { entity: 'e2ee_devices' } }))
    })
    await waitFor(() =>
      expect(screen.queryByText(i18n.t('notes.schluesselLage.freigabe.titel'))).not.toBeInTheDocument(),
    )
    // Freigegeben heisst: gleich noch einmal fragen, nicht aus der Frist antworten.
    expect(kontoAbdruckVergessen).toHaveBeenCalledWith(3)
  })

  it('sagt, dass der Schluessel angefragt ist, wenn das Geraet freigegeben ist', async () => {
    zeige()
    expect(await screen.findByText(i18n.t('notes.schluesselLage.schluessel.titel'))).toBeInTheDocument()
  })

  it('verschwindet, sobald der Schluessel ankommt — nicht erst im naechsten Takt', async () => {
    zeige()
    expect(await screen.findByText(i18n.t('notes.schluesselLage.schluessel.titel'))).toBeInTheDocument()
    lage.stand = 'passt'
    act(() => {
      window.dispatchEvent(new CustomEvent('msm:notes-key-updated', { detail: { userId: 3 } }))
    })
    await waitFor(() =>
      expect(screen.queryByText(i18n.t('notes.schluesselLage.schluessel.titel'))).not.toBeInTheDocument(),
    )
  })

  it('mahnt nicht, wenn es nur nicht weiss — offline oder Server nicht erreichbar', async () => {
    lage.veroeffentlichen = new Error('offline')
    const { container, unmount } = zeige()
    await geprueft(geraetVeroeffentlichen)
    expect(container).toBeEmptyDOMElement()
    unmount()

    vi.clearAllMocks()
    lage.veroeffentlichen = null
    lage.stand = 'unbekannt'
    const zweiter = zeige()
    await geprueft(geraetVeroeffentlichen)
    expect(zweiter.container).toBeEmptyDOMElement()
  })
})
