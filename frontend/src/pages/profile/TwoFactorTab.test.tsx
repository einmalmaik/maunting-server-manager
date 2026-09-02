import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { TwoFactorTab } from './TwoFactorTab'

// Die Bezeichner kommen aus i18n statt aus dem Test. In der Testumgebung
// greift `fallbackLng: 'en'`, und ein fest eingetippter deutscher Text
// wuerde hier nur pruefen, welche Sprache gerade gewinnt.
const t = (schluessel: string) => i18n.t(schluessel)

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn() }
})

const QR = 'data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%3E%3C%2Fsvg%3E'
const URI = 'otpauth://totp/Maunting%20Server%20Manager:pruefer@beispiel.de?secret=JBSWY3DPEHPK3PXP'

function anmelden() {
  useAuthStore.setState({
    user: { id: 1, email: 'pruefer@beispiel.de', two_factor_enabled: false } as never,
  })
}

describe('Zwei-Faktor-Einrichtung', () => {
  beforeEach(() => {
    vi.mocked(api).mockReset()
    anmelden()
  })

  it('zeigt den QR-Code, den das Panel selbst erzeugt hat', async () => {
    vi.mocked(api).mockResolvedValueOnce({ secret: 'JBSWY3DPEHPK3PXP', uri: URI, qr_data_uri: QR })
    render(<TwoFactorTab />)

    fireEvent.click(screen.getByRole('button', { name: t('profile.2faSetup') }))

    const bild = await screen.findByRole('img', { name: t('profile.2faQrCode') })
    // Die eigentliche Zusage: die Quelle ist eine data-URI und kein fremder
    // Host. Frueher stand hier api.qrserver.com, und die otpauth-URI ging als
    // Query-Parameter samt Geheimnis in dessen Zugriffslog.
    expect(bild.getAttribute('src')).toBe(QR)
    expect(bild.getAttribute('src')).toMatch(/^data:image\/svg\+xml/)
  })

  it('bleibt ohne Bild vollstaendig bedienbar', async () => {
    vi.mocked(api).mockResolvedValueOnce({ secret: 'JBSWY3DPEHPK3PXP', uri: URI, qr_data_uri: null })
    render(<TwoFactorTab />)

    fireEvent.click(screen.getByRole('button', { name: t('profile.2faSetup') }))

    // Kein Bild — aber der Weg ohne Kamera steht: Schluessel zum Abschreiben
    // und der Link, den die Authenticator-Apps selbst oeffnen.
    await screen.findByText('JBSWY3DPEHPK3PXP')
    expect(screen.queryByRole('img', { name: t('profile.2faQrCode') })).not.toBeInTheDocument()
    expect(screen.getByRole('link')).toHaveAttribute('href', URI)
  })

  it('laesst das Geheimnis nicht im Zustand stehen, wenn 2FA aktiv ist', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ secret: 'JBSWY3DPEHPK3PXP', uri: URI, qr_data_uri: QR })
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ codes: ['aaaa-bbbb'] })
      .mockResolvedValueOnce({ two_factor_enabled: true })
    render(<TwoFactorTab />)

    fireEvent.click(screen.getByRole('button', { name: t('profile.2faSetup') }))
    await screen.findByText('JBSWY3DPEHPK3PXP')

    fireEvent.change(screen.getByPlaceholderText('000000'), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    // Nach der Aktivierung hat das Geheimnis in der Oberflaeche nichts mehr
    // verloren — es bliebe sonst bis zum Seitenwechsel im Zustand stehen.
    await waitFor(() => {
      expect(screen.queryByText('JBSWY3DPEHPK3PXP')).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('img', { name: t('profile.2faQrCode') })).not.toBeInTheDocument()
  })
})

describe('Regressionswache', () => {
  it('holt nirgends im Frontend mehr ein QR-Bild von fremd', () => {
    // Der Befund war nicht "das Bild fehlt", sondern "das Bild kam von
    // api.qrserver.com und trug das TOTP-Geheimnis dorthin". Diese Wache
    // faengt es, falls jemand den bequemen Weg wieder einbaut.
    //
    // Die JSX-Kommentare werden vorher entfernt: der Quelltext erklaert an
    // genau dieser Stelle, was frueher dort stand, und nennt den Host dabei
    // beim Namen. Eine Wache, die daran anschlaegt, waere nur ein Verbot,
    // ueber den Fehler zu schreiben.
    const roh = readFileSync(resolve(__dirname, 'TwoFactorTab.tsx'), 'utf8')
    const quelle = roh.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

    expect(quelle).not.toContain('qrserver')
    // Allgemeiner als der eine Host: gar kein Bild von aussen.
    expect(quelle).not.toMatch(/<img[\s\S]{0,200}?src=\{?[`'"]https?:/)
  })
})
