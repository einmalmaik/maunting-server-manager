import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { getE2eeGeraete } from '@/api/social'
import {
  entferneGeraet,
  gebeGeraetFrei,
  geraeteZuruecksetzen,
  gibMichSelbstFrei,
  vertrauteGeraete,
} from '@/services/e2eeGeraet'
import { passkeyNachweis } from '@/services/passkeyService'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { E2eeGeraeteCard } from './E2eeGeraeteCard'

const { liste } = vi.hoisted(() => ({
  liste: {
    inhalt: [] as {
      device_id: string
      public_key: string
      label: string
      is_approved?: boolean
      approved_by?: string
      approval_signature?: string
    }[],
    fehler: null as Error | null,
  },
}))

vi.mock('@/api/social', () => ({
  getE2eeGeraete: vi.fn(async () => {
    if (liste.fehler) throw liste.fehler
    return liste.inhalt
  }),
}))

// Die abgeleitete Bezeichnung dieses Browsers haengt an `navigator.userAgent`.
// Im Test soll sie feststehen, damit „Web-Frontend" nicht von jsdom abhaengt.
vi.mock('@/services/geraeteBezeichnung', () => ({
  STANDARD_BEZEICHNUNG: 'Web-Frontend',
  geraeteBezeichnung: () => 'Web-Frontend',
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
  gibMichSelbstFrei: vi.fn(async () => undefined),
  vertrauteGeraete: vi.fn(async (_id: number, liste: any[]) => liste),
}))

/**
 * Der Passkey-Nachweis wird hier von Hand gesteuert.
 *
 * Nur so laesst sich der Moment festhalten, in dem die Abfrage laeuft: genau
 * dort war der Knopf bis 10/2026 weiter bedienbar, und ein Abbruch lief ohne
 * Meldung ins Leere (der Aufruf stand vor dem `try`).
 */
vi.mock('@/services/passkeyService', async (importOriginal) => {
  const echt = await importOriginal<typeof import('@/services/passkeyService')>()
  return { ...echt, passkeyNachweis: vi.fn() }
})

/** Der offene Entfernen-Dialog. Die Liste dahinter bleibt im DOM — also gezielt suchen. */
async function entfernenDialog() {
  return await screen.findByRole('dialog')
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
    // Im Normalfall deckt jede Freigabe — die Ausnahmen setzt der Test.
    vi.mocked(vertrauteGeraete).mockImplementation(async (_id: number, liste: any[]) => liste)
  })

  it('markiert das eigene Gerät und bietet dafür kein Entfernen an', async () => {
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText('Dieses Gerät')).toBeInTheDocument()
    expect(screen.getByText('Arbeitsrechner')).toBeInTheDocument()
    // Das eigene Gerät heisst nie „unbenannt": seine Bezeichnung kommt aus der
    // laufenden App und dem Browser (hier fest verdrahtet).
    expect(screen.getByText('Web-Frontend')).toBeInTheDocument()
    expect(screen.queryByText('Unbenanntes Gerät')).toBeNull()

    // Genau ein Knopf: der für das fremde Gerät. Das eigene würde sich beim
    // nächsten Start sofort wieder eintragen — und im selben Tab nicht einmal
    // das, weil `geraetVeroeffentlichen` sich je Sitzung nur einmal meldet.
    expect(screen.getAllByRole('button', { name: /Entfernen/ })).toHaveLength(1)
  })

  it('entfernt ein Gerät erst nach Bestätigung — unterschrieben von diesem', async () => {
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))

    // Ein Konto mit Passwort bestätigt mit dem Passwort (Stufe 3 der Kaskade).
    const dialog = await entfernenDialog()
    const knopf = within(dialog).getByRole('button', { name: /Entfernen/ })
    expect(knopf).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Passwort'), { target: { value: 'geheim' } })
    fireEvent.click(knopf)

    // `entferneGeraet` unterschreibt und vergisst den Sendecache.
    await waitFor(() =>
      expect(entferneGeraet).toHaveBeenCalledWith(
        expect.objectContaining({ device_id: 'fremdes-geraet-02' }),
        { password: 'geheim' },
      ),
    )
    // Neu geladen wird danach: einmal beim Aufbau, einmal nach dem Entfernen.
    await waitFor(() => expect(getE2eeGeraete).toHaveBeenCalledTimes(2))
  })

  it('lässt das Gerät stehen, wenn der Dialog abgebrochen wird', async () => {
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))

    const dialog = await entfernenDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(entferneGeraet).not.toHaveBeenCalled()
  })

  it('verlangt vor dem Entfernen den Authenticator-Code, wenn 2FA eingerichtet ist', async () => {
    // Stufe 2 der Kaskade: der Code funktioniert auf jeder Plattform, auch wo
    // kein Passkey hinterlegt oder bedienbar ist.
    useAuthStore.setState({
      user: { id: 10, username: 'anna', two_factor_enabled: true, two_factor_methods: ['totp'] } as any,
    })
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))

    const dialog = await entfernenDialog()
    const knopf = within(dialog).getByRole('button', { name: /Entfernen/ })
    const feld = within(dialog).getByPlaceholderText('000000')
    expect(knopf).toBeDisabled()

    fireEvent.change(feld, { target: { value: '123456' } })
    fireEvent.click(knopf)

    await waitFor(() =>
      expect(entferneGeraet).toHaveBeenCalledWith(
        expect.objectContaining({ device_id: 'fremdes-geraet-02' }),
        { otp_code: '123456' },
      ),
    )
    // Kein Passwortfeld daneben: die Faktoren stehen vor dem Passwort.
    expect(within(dialog).queryByLabelText('Passwort')).toBeNull()
  })

  it('fragt zuerst den Passkey, wenn einer hinterlegt ist', async () => {
    // Stufe 1: der eingerichtete Passkey, gebunden an den Zweck dieses Vorgangs.
    useAuthStore.setState({
      user: { id: 10, username: 'anna', two_factor_enabled: true, two_factor_methods: ['passkey'] } as any,
    })
    vi.mocked(passkeyNachweis).mockResolvedValue({ type: 'browser', vorgang: 'v1' } as any)
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))

    const dialog = await entfernenDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: /Entfernen/ }))

    await waitFor(() => expect(passkeyNachweis).toHaveBeenCalledWith('e2ee_remove'))
    await waitFor(() =>
      expect(entferneGeraet).toHaveBeenCalledWith(
        expect.objectContaining({ device_id: 'fremdes-geraet-02' }),
        { passkey: { type: 'browser', vorgang: 'v1' } },
      ),
    )
  })

  it('kommt bei Social-Login ohne Passwort und ohne 2FA mit der Sitzung aus', async () => {
    // Stufe 4: mehr hat das Konto nicht herzugeben. Ein Feld, das es nicht
    // geben kann, waere eine Sackgasse.
    useAuthStore.setState({ user: { id: 10, username: 'anna', has_password: false } as any })
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))

    const dialog = await entfernenDialog()
    expect(within(dialog).queryByLabelText(/Passwort/)).toBeNull()
    const knopf = within(dialog).getByRole('button', { name: /Entfernen/ })
    expect(knopf).toBeEnabled()
    fireEvent.click(knopf)

    await waitFor(() =>
      expect(entferneGeraet).toHaveBeenCalledWith(
        expect.objectContaining({ device_id: 'fremdes-geraet-02' }),
        {},
      ),
    )
  })

  it('entfernt ein wartendes Gerät ohne Nachweis — es hat nie etwas bekommen', async () => {
    // Der Server verlangt dafür keinen Nachweis, und die Kopplung mit falscher
    // Sicherheitsnummer muss sofort abbrechbar bleiben.
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: true },
      { device_id: 'wartendes-geraet-03', public_key: 'pub-3', label: 'Zweitgerät', is_approved: false },
    ]
    render(<E2eeGeraeteCard />)
    fireEvent.click(await screen.findByRole('button', { name: /Entfernen/ }))

    const dialog = await entfernenDialog()
    expect(within(dialog).getByText(/hat nie Nachrichten bekommen/)).toBeInTheDocument()
    expect(within(dialog).queryByLabelText(/Passwort/)).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: /Entfernen/ }))

    await waitFor(() =>
      expect(entferneGeraet).toHaveBeenCalledWith(
        expect.objectContaining({ device_id: 'wartendes-geraet-03' }),
        {},
      ),
    )
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

  it('zeigt ein frisch gekoppeltes Gerät sofort zum Freigeben, ohne Neuladen', async () => {
    // Bis 5.0.3 lud die Liste nur beim Öffnen: das neue Gerät erschien erst,
    // wenn man die Seite neu lud.
    liste.inhalt = [{ device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: true }]
    render(<E2eeGeraeteCard />)
    expect(await screen.findByText('Dieses Gerät')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Freigeben/ })).not.toBeInTheDocument()

    // Ein fremdes Ereignis lädt nichts nach.
    window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: { entity: 'notes', action: 'updated' } }))
    expect(getE2eeGeraete).toHaveBeenCalledTimes(1)

    liste.inhalt = [
      ...liste.inhalt,
      { device_id: 'mss-geraet-00004', public_key: 'pub-4', label: 'Arbeitsrechner', is_approved: false },
    ]
    window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: { entity: 'e2ee_devices', action: 'changed' } }))

    expect(await screen.findByRole('button', { name: /Freigeben/ })).toBeInTheDocument()
    expect(screen.getByText('Arbeitsrechner')).toBeInTheDocument()
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

  it('bietet bei Social-Login ohne Passwort und ohne 2FA eine RESET-Bestätigung statt Passwort an', async () => {
    useAuthStore.setState({ user: { id: 10, username: 'anna', has_password: false } as any })

    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: false },
    ]
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText(/Dieses Gerät wartet auf Freigabe/)).toBeInTheDocument()
    // Statt "Passwort" suchen wir nach dem RESET-Eingabefeld
    const input = await screen.findByPlaceholderText(/RESET/)
    expect(input).toBeInTheDocument()

    const neu = screen.getByRole('button', { name: 'Neu beginnen' })
    expect(neu).toBeDisabled()

    fireEvent.change(input, { target: { value: 'RESET' } })
    expect(neu).not.toBeDisabled()

    fireEvent.click(neu)
    await waitFor(() =>
      expect(geraeteZuruecksetzen).toHaveBeenCalledWith({ confirmation: 'RESET' }),
    )
  })

  it('markiert ein freigegebenes Gerät, dessen Freigabe keine Unterschrift trägt', async () => {
    // Der Widerspruch aus dem Fehlerbericht: die Liste zeigte „freigegeben“,
    // der Chat sagte „es bekommt nichts“. Beides ist richtig — `is_approved`
    // ist ein Wort des Servers, die Warnung hängt an der Signaturkette.
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: true },
      {
        device_id: 'geister-geraet-02',
        public_key: 'pub-2',
        label: 'Altes Handy',
        is_approved: true,
        approved_by: '',
        approval_signature: '',
      },
    ]
    vi.mocked(vertrauteGeraete).mockImplementation(async (_id: number, geraete: any[]) =>
      geraete.filter((g) => g.device_id !== 'geister-geraet-02'),
    )

    render(<E2eeGeraeteCard />)

    expect(await screen.findByText('Ohne gültige Freigabe')).toBeInTheDocument()
    expect(
      screen.getByText(/keines deiner Geräte hat die Freigabe unterschrieben/),
    ).toBeInTheDocument()
  })

  it('zeigt kein Abzeichen, wenn die Signaturkette alle Freigaben deckt', async () => {
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: true },
      {
        device_id: 'gedecktes-geraet-02',
        public_key: 'pub-2',
        label: 'Telefon',
        is_approved: true,
        approved_by: 'dieses-geraet-0001',
        approval_signature: 'sig',
      },
    ]
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText('Telefon')).toBeInTheDocument()
    expect(screen.queryByText('Ohne gültige Freigabe')).toBeNull()
  })

  it('bietet das Freigeben auch für ein freigegebenes Gerät ohne Unterschrift an', async () => {
    // Der eigentliche Fehlerbericht: das Web-Frontend stand auf dem Telefon als
    // „freigegeben" (Wort des Servers) und zugleich als „Ohne gültige Freigabe"
    // (keine Unterschrift) — und daneben gab es nur „Entfernen". Der Knopf
    // fehlte, weil er an `is_approved === false` hing.
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: true },
      { device_id: 'web-frontend-02', public_key: 'pub-2', label: 'Web-Frontend', is_approved: true },
    ]
    vi.mocked(vertrauteGeraete).mockImplementation(async (_id: number, geraete: any[]) =>
      geraete.filter((g) => g.device_id !== 'web-frontend-02'),
    )

    render(<E2eeGeraeteCard />)

    expect(await screen.findByText('Ohne gültige Freigabe')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Freigeben/ }))
    await waitFor(() =>
      expect(gebeGeraetFrei).toHaveBeenCalledWith(
        expect.objectContaining({ device_id: 'web-frontend-02' }),
      ),
    )
  })

  it('prüft nur freigegebene Geräte gegen die Signaturkette', async () => {
    // Der erste Abruf schreibt die Vertrauensliste fest. Ein wartendes Gerät
    // hat nie etwas bekommen und darf dort nicht vorab hineinrutschen.
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: true },
      { device_id: 'wartendes-geraet-03', public_key: 'pub-3', label: '', is_approved: false },
    ]
    render(<E2eeGeraeteCard />)

    await waitFor(() => expect(vertrauteGeraete).toHaveBeenCalled())
    const [, geprueft] = vi.mocked(vertrauteGeraete).mock.calls[0]
    expect(geprueft.map((g: any) => g.device_id)).toEqual(['dieses-geraet-0001'])
  })

  it('bietet einem einzigen wartenden Gerät den Weg zurück ohne Neuanfang', async () => {
    // Der Fall aus Phase 2: das Gerät hat seinen Schlüssel neu erzeugt, es gibt
    // kein zweites freigegebenes Gerät, das unterschreiben könnte. Bis 10/2026
    // blieb nur der Neuanfang — der alle Sitzungen sperrt.
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: false },
    ]
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText(/Dieses Gerät wartet auf Freigabe/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Passwort'), { target: { value: 'geheim' } })

    const zurueck = screen.getByRole('button', { name: 'Dieses Gerät wieder zulassen' })
    fireEvent.click(zurueck)

    await waitFor(() => expect(gibMichSelbstFrei).toHaveBeenCalledWith({ password: 'geheim' }))
    // Kein Neuanfang: die anderen Sitzungen bleiben, nur das Verzeichnis nicht.
    expect(geraeteZuruecksetzen).not.toHaveBeenCalled()
  })

  it('meldet einen abgebrochenen Nachweis, statt still zu scheitern', async () => {
    // Der Nachweis stand bis 10/2026 vor dem `try`: ein abgebrochener Passkey
    // warf ohne Meldung, und der Knopf blieb waehrend der ganzen Abfrage
    // bedienbar. Beides ist hier festgehalten.
    useAuthStore.setState({
      user: { id: 10, username: 'anna', two_factor_enabled: true, two_factor_methods: ['passkey'] } as any,
    })
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: false },
    ]
    // Die Meldung selbst wird geprüft: ohne `toast.error` im `catch` bliebe
    // ein Abbruch weiter stumm, und der Test dürfte davon nichts merken.
    const gemeldet = vi.spyOn(toast, 'error').mockImplementation(() => {})
    let ablehnen: (fehler: Error) => void = () => {}
    vi.mocked(passkeyNachweis).mockImplementation(
      () =>
        new Promise((_erfuellen, verwerfen) => {
          ablehnen = verwerfen
        }),
    )

    render(<E2eeGeraeteCard />)
    const knopf = await screen.findByRole('button', { name: 'Dieses Gerät wieder zulassen' })
    fireEvent.click(knopf)

    // Waehrend die Abfrage laeuft, ist der Knopf gesperrt.
    await waitFor(() => expect(knopf).toBeDisabled())
    expect(gibMichSelbstFrei).not.toHaveBeenCalled()

    ablehnen(new Error('Passkey abgebrochen'))
    await waitFor(() => expect(knopf).toBeEnabled())
    // Kein Aufruf ohne Nachweis — und der Fehler ist nicht verschluckt.
    expect(gibMichSelbstFrei).not.toHaveBeenCalled()
    await waitFor(() => expect(gemeldet).toHaveBeenCalledWith('Passkey abgebrochen'))
    gemeldet.mockRestore()
  })

  it('verweist bei einem anderen freigegebenen Gerät auf dessen Unterschrift', async () => {
    // Dort ist der Weg die Unterschrift — „wieder zulassen" lehnt der Server
    // ab, und ein Knopf, der nur 403 erzeugt, gehörte nicht auf den Schirm.
    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: false },
      { device_id: 'altes-geraet-0002', public_key: 'pub-2', label: 'Telefon', is_approved: true },
    ]
    render(<E2eeGeraeteCard />)

    expect(
      await screen.findByText(/Ein anderes freigegebenes Gerät muss dieses hier freigeben/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Dieses Gerät wieder zulassen' })).toBeNull()
  })

  it('bietet bei aktiver 2FA die Eingabe des OTP-Codes an', async () => {
    useAuthStore.setState({
      user: { id: 10, username: 'anna', two_factor_enabled: true, two_factor_methods: ['totp'] } as any,
    })

    liste.inhalt = [
      { device_id: 'dieses-geraet-0001', public_key: 'pub-1', label: '', is_approved: false },
    ]
    render(<E2eeGeraeteCard />)

    expect(await screen.findByText(/Dieses Gerät wartet auf Freigabe/)).toBeInTheDocument()
    const otpInput = await screen.findByPlaceholderText('6-stelliger Authenticator-Code')
    expect(otpInput).toBeInTheDocument()

    const neu = screen.getByRole('button', { name: 'Neu beginnen' })
    expect(neu).toBeDisabled()

    fireEvent.change(otpInput, { target: { value: '123456' } })
    expect(neu).not.toBeDisabled()

    fireEvent.click(neu)
    await waitFor(() =>
      expect(geraeteZuruecksetzen).toHaveBeenCalledWith({ otp_code: '123456' }),
    )
  })
})
