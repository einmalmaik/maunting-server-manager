/**
 * Die Rückfrage, bevor der Verlauf auf ein neues Gerät zieht.
 *
 * Für welches Gerät versiegelt wird, sagt der Server — genau dort könnte er ein
 * eigenes unterschieben. Bis 09/2026 ging der ganze Verlauf samt
 * Notizschlüssel ohne Nachfrage an jedes Gerät, das er nach dem Einlösen
 * nannte. Jetzt zeigt die Karte Name und Sicherheitsnummer und übergibt erst
 * auf Bestätigung; bei mehr als einem Gerät gar nicht.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'

const CODE = 'ABCD-EFGH-JKLM'

const { lage, uebergabe, entzogen, ausDerZustellung } = vi.hoisted(() => ({
  /** Was der Status-Endpunkt gerade antwortet. */
  lage: { status: null as Record<string, unknown> | null },
  uebergabe: vi.fn(async () => true),
  /** Die Familien, deren Anmeldung widerrufen wurde. */
  entzogen: [] as string[],
  ausDerZustellung: vi.fn(async (_kennung: string) => ({ ok: true })),
}))

vi.mock('@/api/client', () => ({
  api: vi.fn(async (pfad: string, opt?: { method?: string }) => {
    if (pfad === '/auth/devices') return []
    if (pfad === '/auth/devices/pairing' && opt?.method === 'POST') {
      return { code: CODE, qr_data_uri: null }
    }
    if (pfad.endsWith('/status')) return lage.status
    if (pfad.startsWith('/auth/devices/') && opt?.method === 'DELETE') {
      entzogen.push(decodeURIComponent(pfad.slice('/auth/devices/'.length)))
      return { message: 'Gerät entkoppelt' }
    }
    throw new Error(`unerwartet: ${pfad}`)
  }),
}))

vi.mock('@/api/social', () => ({ deleteEigenesGeraet: ausDerZustellung }))

vi.mock('@/services/verlaufsUebergabe', () => ({ uebergebeVerlauf: uebergabe }))

vi.mock('@/services/e2eeGeraet', () => ({
  // Die Rechnung selbst prüft `e2eeGeraet.test.ts`. Hier zählt, dass die
  // Nummer aus dem Schlüssel des gemeldeten Geräts kommt.
  sicherheitsnummer: vi.fn(async (schluessel: string) =>
    schluessel === 'schluessel-neu' ? '11111 22222 33333 44444' : '99999 99999 99999 99999',
  ),
  vergessenGeraete: vi.fn(),
}))

const { AiDevicePairingCard } = await import('./AiDevicePairingCard')

const NEUES_GERAET = {
  device_id: 'neu-0001',
  public_key: 'schluessel-neu',
  label: 'Arbeitsrechner',
  created_at: '2026-09-23T10:00:00+00:00',
}

async function codeErzeugenUndEinloesen(neueGeraete: unknown[]) {
  lage.status = { exists: true, redeemed: false, expired: false }
  render(<AiDevicePairingCard />)
  fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.profile.devicesPair') }))
  await screen.findByText(CODE)
  lage.status = {
    exists: true,
    redeemed: true,
    expired: false,
    label: 'Laptop',
    family: 'familie-neu',
    neue_geraete: neueGeraete,
    verlauf_abgelegt: false,
  }
}

describe('AiDevicePairingCard — Rückfrage vor der Übergabe', () => {
  beforeEach(() => {
    uebergabe.mockClear()
    ausDerZustellung.mockClear()
    entzogen.length = 0
    useToastStore.setState({ toasts: [] })
  })

  it('zeigt Gerät und Sicherheitsnummer und übergibt erst auf Bestätigung', async () => {
    await codeErzeugenUndEinloesen([NEUES_GERAET])

    await screen.findByText('11111 22222 33333 44444', undefined, { timeout: 6_000 })
    expect(screen.getByText('Arbeitsrechner')).toBeInTheDocument()
    expect(uebergabe).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.profile.devicePairHandOver') }))

    await waitFor(() => expect(uebergabe).toHaveBeenCalledWith(CODE, [NEUES_GERAET]))
  }, 15_000)

  it('übergibt nichts, wenn die Nummer nicht passt und abgelehnt wird', async () => {
    await codeErzeugenUndEinloesen([NEUES_GERAET])
    await screen.findByText('11111 22222 33333 44444', undefined, { timeout: 6_000 })

    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.profile.devicePairDecline') }))

    await waitFor(() =>
      expect(screen.queryByText('11111 22222 33333 44444')).not.toBeInTheDocument(),
    )
    expect(uebergabe).not.toHaveBeenCalled()
  }, 15_000)

  it('sagt beim Ablehnen, dass das Gerät gekoppelt bleibt', async () => {
    // Ohne Verlauf ist es trotzdem ein Gerät des Kontos: es bekommt jede neue
    // Nachricht und den Schlüssel für Notizen und Kalender. Wer das nicht will,
    // muss es entfernen — und das muss hier stehen, nicht erst im Kleingedruckten.
    await codeErzeugenUndEinloesen([NEUES_GERAET])
    await screen.findByText('11111 22222 33333 44444', undefined, { timeout: 6_000 })

    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.profile.devicePairDecline') }))

    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
        i18n.t('ai.profile.devicePairDeclined'),
      ),
    )
    expect(i18n.t('ai.profile.devicePairDeclined', { lng: 'de' })).toMatch(/neue Nachrichten.*Notizen/)
    expect(i18n.t('ai.profile.devicePairDeclined', { lng: 'en' })).toMatch(/new messages.*notes/)
    expect(entzogen).toEqual([])
    expect(ausDerZustellung).not.toHaveBeenCalled()
  }, 15_000)

  it('entfernt ein Gerät, dessen Nummer nicht passt — Anmeldung und Zustellung', async () => {
    // Nur die Karte zu schliessen liess das Gerät im Verzeichnis: der nächste
    // Abgleich schickte ihm den Notizschlüssel, und jede neue Nachricht ging
    // auch an seinen Schlüssel. Ohne den Widerruf der Anmeldung trüge es sich
    // beim nächsten Start einfach wieder ein.
    await codeErzeugenUndEinloesen([NEUES_GERAET])
    await screen.findByText('11111 22222 33333 44444', undefined, { timeout: 6_000 })

    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.profile.devicePairRemove') }))

    await waitFor(() => expect(ausDerZustellung).toHaveBeenCalledWith('neu-0001'))
    expect(entzogen).toEqual(['familie-neu'])
    expect(uebergabe).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
        i18n.t('ai.profile.devicePairRemoved'),
      ),
    )
    expect(screen.queryByText('11111 22222 33333 44444')).not.toBeInTheDocument()
  }, 15_000)

  it('übergibt gar nicht, wenn sich mehr als ein Gerät gemeldet hat', async () => {
    // Welches wäre das neue? Raten hiesse, den Verlauf womöglich an das
    // untergeschobene zu geben.
    await codeErzeugenUndEinloesen([
      NEUES_GERAET,
      { ...NEUES_GERAET, device_id: 'fremd-0002', public_key: 'schluessel-fremd', label: 'Neu' },
    ])

    await waitFor(
      () =>
        expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
          i18n.t('ai.profile.devicePairTooMany'),
        ),
      { timeout: 6_000 },
    )
    expect(uebergabe).not.toHaveBeenCalled()
    expect(screen.queryByText('11111 22222 33333 44444')).not.toBeInTheDocument()
  }, 15_000)
})
