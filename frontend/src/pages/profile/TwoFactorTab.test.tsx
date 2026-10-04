import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import i18n from '@/i18n'
import { passkeyNachweis } from '@/services/passkeyService'
import { useAuthStore } from '@/stores/authStore'
import { TwoFactorTab } from './TwoFactorTab'

// Die Bezeichner kommen aus i18n statt aus dem Test. In der Testumgebung
// greift `fallbackLng: 'en'`, und ein fest eingetippter deutscher Text
// wuerde hier nur pruefen, welche Sprache gerade gewinnt.
const t = (schluessel: string, werte?: Record<string, unknown>) => i18n.t(schluessel, werte)

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn() }
})

const NACHWEIS = { type: 'browser', vorgang: 'v'.repeat(20) }
const ANLAGE = { id: 'neu', rawId: 'neu', type: 'public-key', response: {} }

vi.mock('@/services/passkeyService', async () => {
  const actual = await vi.importActual<typeof import('@/services/passkeyService')>('@/services/passkeyService')
  return {
    ...actual,
    passkeyNachweis: vi.fn(async () => NACHWEIS),
    passkeyAnlegen: vi.fn(async () => ANLAGE),
    webauthnVerfuegbar: () => true,
    inDerApp: () => false,
  }
})

const QR = 'data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%3E%3C%2Fsvg%3E'
const URI = 'otpauth://totp/Maunting%20Server%20Manager:pruefer@beispiel.de?secret=JBSWY3DPEHPK3PXP'

type Antworten = Record<string, unknown | ((optionen?: RequestInit) => unknown)>

/** Beantwortet `api()` je Pfad; `/auth/2fa/passkeys` ist ohne Eintrag leer. */
function antworten(tabelle: Antworten) {
  vi.mocked(api).mockImplementation(async (pfad: string, optionen?: RequestInit) => {
    const pfadOhneQuery = pfad.split('?')[0]
    const eintrag = tabelle[pfadOhneQuery]
    if (typeof eintrag === 'function') return eintrag(optionen) as never
    if (eintrag !== undefined) return eintrag as never
    if (pfadOhneQuery === '/auth/2fa/passkeys') return [] as never
    return {} as never
  })
}

function anmelden(user: Record<string, unknown>) {
  useAuthStore.setState({ user: { id: 1, email: 'pruefer@beispiel.de', ...user } as never })
}

function aufrufe(pfad: string) {
  return vi.mocked(api).mock.calls.filter(([p]) => String(p).split('?')[0] === pfad)
}

describe('Zwei-Faktor-Einrichtung ohne aktive 2FA', () => {
  beforeEach(() => {
    vi.mocked(api).mockReset()
    anmelden({ two_factor_enabled: false, has_password: true })
  })

  it('verlangt das Passwort und zeigt den QR-Code, den das Panel selbst erzeugt hat', async () => {
    antworten({ '/auth/2fa/setup': { secret: 'JBSWY3DPEHPK3PXP', uri: URI, qr_data_uri: QR } })
    render(<TwoFactorTab />)

    const einrichten = screen.getByRole('button', { name: t('profile.zweitfaktoren.appEinrichten') })
    expect(einrichten).toBeDisabled()
    fireEvent.change(screen.getByLabelText(t('profile.currentPassword')), { target: { value: 'geheim' } })
    fireEvent.click(einrichten)

    const bild = await screen.findByRole('img', { name: t('profile.2faQrCode') })
    // Die eigentliche Zusage: die Quelle ist eine data-URI und kein fremder
    // Host. Frueher stand hier api.qrserver.com, und die otpauth-URI ging als
    // Query-Parameter samt Geheimnis in dessen Zugriffslog.
    expect(bild.getAttribute('src')).toBe(QR)
    expect(JSON.parse(String(aufrufe('/auth/2fa/setup')[0][1]?.body))).toEqual({ password: 'geheim' })
  })

  it('bleibt ohne Bild vollstaendig bedienbar', async () => {
    antworten({ '/auth/2fa/setup': { secret: 'JBSWY3DPEHPK3PXP', uri: URI, qr_data_uri: null } })
    render(<TwoFactorTab />)

    fireEvent.change(screen.getByLabelText(t('profile.currentPassword')), { target: { value: 'geheim' } })
    fireEvent.click(screen.getByRole('button', { name: t('profile.zweitfaktoren.appEinrichten') }))

    // Kein Bild — aber der Weg ohne Kamera steht: Schluessel zum Abschreiben
    // und der Link, den die Authenticator-Apps selbst oeffnen.
    await screen.findByText('JBSWY3DPEHPK3PXP')
    expect(screen.queryByRole('img', { name: t('profile.2faQrCode') })).not.toBeInTheDocument()
    expect(screen.getByRole('link')).toHaveAttribute('href', URI)
  })

  it('laesst das Geheimnis nicht im Zustand stehen und zeigt die ersten Backup-Codes', async () => {
    antworten({
      '/auth/2fa/setup': { secret: 'JBSWY3DPEHPK3PXP', uri: URI, qr_data_uri: QR },
      '/auth/2fa/enable': { backup_codes: ['AAAA-BBBB'] },
      '/auth/me': { id: 1, email: 'pruefer@beispiel.de', two_factor_enabled: true, two_factor_methods: ['totp'] },
    })
    render(<TwoFactorTab />)

    fireEvent.change(screen.getByLabelText(t('profile.currentPassword')), { target: { value: 'geheim' } })
    fireEvent.click(screen.getByRole('button', { name: t('profile.zweitfaktoren.appEinrichten') }))
    await screen.findByText('JBSWY3DPEHPK3PXP')

    fireEvent.change(screen.getByLabelText(t('profile.2faEnterCode')), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    // Nach der Aktivierung hat das Geheimnis in der Oberflaeche nichts mehr
    // verloren — es bliebe sonst bis zum Seitenwechsel im Zustand stehen.
    await waitFor(() => {
      expect(screen.queryByText('JBSWY3DPEHPK3PXP')).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: t('profile.downloadBackupCodes') })).toBeInTheDocument()
    // Die Codes kommen mit der Aktivierung, nicht aus einem zweiten Aufruf ohne Nachweis.
    expect(aufrufe('/auth/2fa/backup/generate')).toHaveLength(0)
  })

  it('richtet ein Konto nur mit Social Login ohne Nachweis ein', () => {
    anmelden({ two_factor_enabled: false, has_password: false })
    antworten({})
    render(<TwoFactorTab />)

    expect(screen.queryByLabelText(t('profile.currentPassword'))).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('profile.zweitfaktoren.appEinrichten') })).toBeEnabled()
  })
})

describe('Mehrere Faktoren', () => {
  const PASSKEYS = [
    { id: 1, name: 'PC', created_at: '2026-09-01T10:00:00Z', last_used_at: null },
    { id: 2, name: 'Handy', created_at: '2026-09-20T10:00:00Z', last_used_at: null },
  ]

  beforeEach(() => {
    vi.mocked(api).mockReset()
    vi.mocked(passkeyNachweis).mockClear()
  })

  it('listet die Passkeys und entfernt einen mit Passkey-Nachweis', async () => {
    anmelden({ two_factor_enabled: true, two_factor_methods: ['passkey'] })
    antworten({ '/auth/2fa/passkeys': PASSKEYS, '/auth/me': { id: 1, two_factor_enabled: true, two_factor_methods: ['passkey'] } })
    render(<TwoFactorTab />)

    await screen.findByText('Handy')
    fireEvent.click(
      screen.getByRole('button', { name: t('profile.zweitfaktoren.passkeyEntfernen', { name: 'PC' }) }),
    )

    await waitFor(() => expect(aufrufe('/auth/2fa/passkeys/1/remove')).toHaveLength(1))
    expect(passkeyNachweis).toHaveBeenCalledWith('2fa_change')
    expect(JSON.parse(String(aufrufe('/auth/2fa/passkeys/1/remove')[0][1]?.body))).toEqual({ passkey: NACHWEIS })
  })

  it('warnt, solange nur ein Faktor eingerichtet ist', async () => {
    anmelden({ two_factor_enabled: true, two_factor_methods: ['passkey'] })
    antworten({ '/auth/2fa/passkeys': [PASSKEYS[0]] })
    render(<TwoFactorTab />)

    expect(await screen.findByText(t('profile.zweitfaktoren.nurEinFaktor'))).toBeInTheDocument()
  })

  it('zeigt beide Wege sichtbar zur Wahl, ohne Vorgeschichte steht der Passkey vorn', () => {
    anmelden({ two_factor_enabled: true, two_factor_methods: ['passkey', 'totp'] })
    antworten({ '/auth/2fa/passkeys': [PASSKEYS[0]] })
    render(<TwoFactorTab />)

    expect(screen.getByRole('button', { name: t('auth.zweitfaktor.wegPasskey') })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: t('auth.zweitfaktor.wegTotp') })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByLabelText(t('auth.zweitfaktor.codeLabel'))).not.toBeInTheDocument()
  })

  it('am neuen Handy: weiterer Passkey per App-Code, ohne den Passkey vom PC abzufragen', async () => {
    // Fehler 04.10.2026: Angemeldet per App-Code, trotzdem stand der Passkey
    // vorn, und "Passkey hinzufuegen" fragte zuerst den Passkey vom PC ab.
    anmelden({ two_factor_enabled: true, two_factor_methods: ['passkey', 'totp'], two_factor_last_method: 'totp' })
    antworten({
      '/auth/2fa/passkeys': [PASSKEYS[0]],
      '/auth/2fa/passkey/options': { challenge: 'c' },
      '/auth/2fa/passkey/enable': { id: 2 },
      '/auth/me': { id: 1, two_factor_enabled: true, two_factor_methods: ['passkey', 'totp'] },
    })
    render(<TwoFactorTab />)

    expect(screen.getByRole('button', { name: t('auth.zweitfaktor.wegTotp') })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.change(screen.getByLabelText(t('auth.zweitfaktor.codeLabel')), { target: { value: '654321' } })
    fireEvent.change(screen.getByLabelText(t('profile.zweitfaktoren.passkeyName')), { target: { value: 'Handy' } })
    fireEvent.click(screen.getByRole('button', { name: t('profile.zweitfaktoren.passkeyHinzufuegen') }))

    await screen.findByText(t('profile.zweitfaktoren.passkeyGespeichert'))
    expect(passkeyNachweis).not.toHaveBeenCalled()
    expect(JSON.parse(String(aufrufe('/auth/2fa/passkey/options')[0][1]?.body))).toEqual({ otp_code: '654321' })
    expect(JSON.parse(String(aufrufe('/auth/2fa/passkey/enable')[0][1]?.body))).toMatchObject({ name: 'Handy' })
  })

  it('nur mit dem Passkey vom PC: am Handy gibt ein Backup-Code den neuen Passkey frei', async () => {
    // Entscheidung 04.10.2026: der Backup-Code gilt fuer einen neuen Faktor, fuer nichts sonst.
    anmelden({ two_factor_enabled: true, two_factor_methods: ['passkey'] })
    antworten({
      '/auth/2fa/passkeys': [PASSKEYS[0]],
      '/auth/2fa/passkey/options': { challenge: 'c' },
      '/auth/2fa/passkey/enable': { id: 2 },
      '/auth/me': { id: 1, two_factor_enabled: true, two_factor_methods: ['passkey'] },
    })
    render(<TwoFactorTab />)

    await screen.findByText('PC')
    fireEvent.click(screen.getByRole('button', { name: t('auth.useBackupCode') }))
    expect(screen.getByRole('button', { name: t('auth.useBackupCode') })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: t('auth.zweitfaktor.wegPasskey') })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByText(t('profile.zweitfaktoren.backupNurNeu'))).toBeInTheDocument()
    // Was etwas wegnimmt, nimmt der Server mit dem Backup-Code nicht an.
    for (const name of [
      t('profile.zweitfaktoren.passkeyEntfernen', { name: 'PC' }),
      t('profile.regenerateBackupCodes'),
      t('profile.2faDisable'),
    ]) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
    }
    const hinzufuegen = screen.getByRole('button', { name: t('profile.zweitfaktoren.passkeyHinzufuegen') })
    fireEvent.change(screen.getByLabelText(t('auth.backupCode')), { target: { value: 'abcd-efg' } })
    expect(hinzufuegen).toBeDisabled()
    fireEvent.change(screen.getByLabelText(t('auth.backupCode')), { target: { value: 'abcd-efgh' } })
    fireEvent.click(hinzufuegen)

    await screen.findByText(t('profile.zweitfaktoren.passkeyGespeichert'))
    expect(passkeyNachweis).not.toHaveBeenCalled()
    expect(JSON.parse(String(aufrufe('/auth/2fa/passkey/options')[0][1]?.body))).toEqual({ backup_code: 'ABCD-EFGH' })
  })

  it('ein veralteter letzter Faktor zaehlt nicht: ohne App bleibt es beim Passkey', () => {
    anmelden({ two_factor_enabled: true, two_factor_methods: ['passkey'], two_factor_last_method: 'totp' })
    antworten({ '/auth/2fa/passkeys': [PASSKEYS[0]] })
    render(<TwoFactorTab />)

    expect(screen.queryByLabelText(t('auth.zweitfaktor.codeLabel'))).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('auth.zweitfaktor.wegTotp') })).not.toBeInTheDocument()
    expect(screen.getByText(t('auth.zweitfaktor.passkeyHint'))).toBeInTheDocument()
  })

  it('erneuert Backup-Codes nur mit einem Faktor, hier dem Code aus der App', async () => {
    anmelden({ two_factor_enabled: true, two_factor_methods: ['passkey', 'totp'] })
    antworten({ '/auth/2fa/passkeys': [PASSKEYS[0]], '/auth/2fa/backup/generate': { codes: ['CCCC-DDDD'] } })
    render(<TwoFactorTab />)

    const erneuern = screen.getByRole('button', { name: t('profile.regenerateBackupCodes') })
    fireEvent.click(screen.getByRole('button', { name: t('auth.zweitfaktor.wegTotp') }))
    expect(erneuern).toBeDisabled()
    fireEvent.change(screen.getByLabelText(t('auth.zweitfaktor.codeLabel')), { target: { value: '654321' } })
    fireEvent.click(erneuern)

    await screen.findByRole('button', { name: t('profile.downloadBackupCodes') })
    expect(JSON.parse(String(aufrufe('/auth/2fa/backup/generate')[0][1]?.body))).toEqual({ otp_code: '654321' })
    expect(passkeyNachweis).not.toHaveBeenCalled()
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
