/**
 * Zwei Schritte des Assistenten.
 *
 * Der Kopplungsschritt ist der einzige Weg hinein; geprueft wird die Kette
 * Code → /auth/devices/redeem → Token im Tresor → hydrierter authStore, und
 * dass ein falscher Code eine Meldung zeigt statt die App zu verlassen. Danach
 * steht die Sicherheitsnummer des Geraets auf dem Schirm: das Panel uebergibt
 * den Verlauf erst, wenn dort jemand dieselbe Nummer bestaetigt.
 *
 * Der Adress-Schritt traegt die Regel, ueber welche Leitung das alles geht:
 * `https://` ist Pflicht, `http://` nur auf dem eigenen Rechner.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'

const invokeMock = vi.fn()
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}))
vi.mock('@tauri-apps/api/event', () => ({
  emit: vi.fn(() => Promise.resolve()),
  listen: vi.fn(() => Promise.resolve(() => {})),
}))
vi.mock('@tauri-apps/plugin-dialog', () => ({ open: vi.fn() }))
// Der Wake-Word-Schritt hat eigene Belange; hier reicht ein Platzhalter.
vi.mock('./WakewordEinrichtung', () => ({
  WakewordEinrichtung: () => null,
}))

const { veroeffentlichen, abholen } = vi.hoisted(() => ({
  veroeffentlichen: vi.fn(),
  abholen: vi.fn(async () => 0),
}))
// Das Geraet und sein Schluessel sind erfunden — ein RSA-Paar zu erzeugen
// kostete hier Sekunden und pruefte nichts, was `e2eeGeraet.test.ts` nicht
// schon prueft. Die Sicherheitsnummer rechnet die echte Funktion.
vi.mock('@/services/e2eeGeraet', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/e2eeGeraet')>()),
  geraetVeroeffentlichen: veroeffentlichen,
}))
vi.mock('@/services/verlaufsUebergabe', () => ({ holeVerlaufAb: abholen }))

import { sicherheitsnummer } from '@/services/e2eeGeraet'

import { Wizard } from './Wizard'
import { setzeAccessToken } from './transport'

const OEFFENTLICH = JSON.stringify({ kty: 'RSA', n: 'geraeteschluessel', e: 'AQAB' })

const KONFIG = {
  backend_url: 'https://api.example.com',
  sandbox_pfad: null,
  eingerichtet: true,
  hotkey_fenster: 'Alt+Space',
  hotkey_sprache: 'Alt+Shift+Space',
  wakeword_aktiv: false,
  wakeword_wort: null,
  audio_eingabe: null,
  audio_ausgabe: null,
  wakeword_schwelle: 0.45,
  audio_echo: true,
  audio_rauschen: true,
  audio_autogain: true,
  audio_verstaerkung: 1,
  computer_use_aktiv: false,
  artifact_install_aktiv: false,
  max_download_bytes: 524288000,
  search_roots: [],
}

function json(status: number, koerper: unknown): Response {
  return new Response(JSON.stringify(koerper), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('Wizard: Kopplung', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    invokeMock.mockReset()
    invokeMock.mockResolvedValue(null)
    setzeAccessToken(null)
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: false })
    veroeffentlichen.mockReset()
    veroeffentlichen.mockResolvedValue({
      kennung: 'dieses-geraet',
      paar: { publicKeyJwk: OEFFENTLICH, privateKeyJwk: '{}' },
    })
    abholen.mockClear()
  })

  /** Der Server nimmt den Code an und kennt den Benutzer. */
  function panelNimmtAn() {
    vi.stubGlobal(
      'fetch',
      vi.fn((eingabe: RequestInfo | URL) => {
        const url = String(eingabe)
        if (url.includes('/auth/devices/redeem')) {
          return Promise.resolve(
            json(200, { access_token: 'acc', refresh_token: 'ref', expires_in: 900 }),
          )
        }
        if (url.includes('/auth/me')) {
          return Promise.resolve(json(200, { id: 1, username: 'tester', agent_name: null }))
        }
        return Promise.resolve(json(200, { global_permissions: [], server_permissions: {} }))
      }),
    )
  }

  function codeEingeben(fertig: () => void) {
    render(<Wizard konfig={KONFIG} startSchritt="kopplung" nurDieserSchritt onFertig={fertig} />)
    fireEvent.change(screen.getByLabelText(i18n.t('mss.wizard.codeLabel')), {
      target: { value: 'abcd efgh jklm' },
    })
    fireEvent.change(screen.getByLabelText(i18n.t('mss.wizard.geraetenameLabel')), {
      target: { value: 'Arbeitsrechner' },
    })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.wizard.koppeln') }))
  }

  it('ein eingeloester Code fuellt Tresor und Sitzung', async () => {
    panelNimmtAn()
    const fertig = vi.fn()
    codeEingeben(fertig)

    await screen.findByText(i18n.t('mss.wizard.sicherheitsnummerTitel'))
    expect(invokeMock).toHaveBeenCalledWith('refresh_token_speichern', { token: 'ref' })
    expect(useAuthStore.getState().isAuthenticated).toBe(true)
  })

  it('zeigt die Sicherheitsnummer und geht erst auf Weiter weiter', async () => {
    // Dieselbe Nummer steht gleich im Panel, neben der Frage, ob der Verlauf
    // hierher soll. Verschwaende sie sofort, gaebe es nichts zu vergleichen.
    panelNimmtAn()
    const fertig = vi.fn()
    codeEingeben(fertig)

    await screen.findByText(await sicherheitsnummer(OEFFENTLICH))
    expect(veroeffentlichen).toHaveBeenCalledWith('Arbeitsrechner')
    expect(abholen).toHaveBeenCalledWith('abcd efgh jklm', '{}')
    expect(fertig).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.wizard.weiter') }))
    await waitFor(() => expect(fertig).toHaveBeenCalled())
  })

  it('zeigt einen Fehler beim Weiter unter der Nummer, statt ihn zu verschlucken', async () => {
    // Der Knopf rief `fortfahren()` ohne Auffangen: scheiterte das Speichern
    // der Einstellungen, blieb der Schirm einfach stehen, und niemand sah,
    // warum.
    panelNimmtAn()
    const fertig = vi.fn()
    codeEingeben(fertig)
    await screen.findByText(await sicherheitsnummer(OEFFENTLICH))

    invokeMock.mockImplementation(async (befehl: string) => {
      if (befehl === 'konfig_speichern') throw new Error('Platte voll')
      return null
    })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.wizard.weiter') }))

    expect(await screen.findByText('Platte voll')).toBeInTheDocument()
    expect(fertig).not.toHaveBeenCalled()
    // Die Nummer bleibt stehen: ein zweiter Versuch vergleicht dieselbe.
    expect(screen.getByText(await sicherheitsnummer(OEFFENTLICH))).toBeInTheDocument()
  })

  it('geht ohne Nummer weiter, wenn sich das Geraet nicht melden liess', async () => {
    // Dann gibt es auch keinen Verlauf, ueber den jemand entscheiden muesste.
    // Die Kopplung selbst steht trotzdem.
    veroeffentlichen.mockRejectedValue(new Error('kein Netz'))
    panelNimmtAn()
    const fertig = vi.fn()
    codeEingeben(fertig)

    await waitFor(() => expect(fertig).toHaveBeenCalled())
    expect(screen.queryByText(i18n.t('mss.wizard.sicherheitsnummerTitel'))).not.toBeInTheDocument()
    expect(abholen).not.toHaveBeenCalled()
  })

  it('ein abgelehnter Code zeigt eine Meldung und bleibt im Schritt', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(json(404, { detail: 'Unbekannter oder abgelaufener Code' }))),
    )
    const fertig = vi.fn()
    render(<Wizard konfig={KONFIG} startSchritt="kopplung" nurDieserSchritt onFertig={fertig} />)

    fireEvent.change(screen.getByLabelText(i18n.t('mss.wizard.codeLabel')), {
      target: { value: 'FALSCH' },
    })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.wizard.koppeln') }))

    await waitFor(() => {
      expect(screen.getByText('Unbekannter oder abgelaufener Code')).toBeInTheDocument()
    })
    expect(fertig).not.toHaveBeenCalled()
    expect(invokeMock).not.toHaveBeenCalledWith('refresh_token_speichern', expect.anything())
  })
})

describe('Wizard: Adresse', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    invokeMock.mockReset()
    invokeMock.mockResolvedValue(null)
    setzeAccessToken(null)
  })

  /** Adresse eintragen und auf „Verbinden" klicken. */
  function adresseEintragen(wert: string) {
    render(
      <Wizard
        konfig={{ ...KONFIG, backend_url: null, eingerichtet: false }}
        startSchritt="backend"
        onFertig={vi.fn()}
      />,
    )
    fireEvent.change(screen.getByLabelText(i18n.t('mss.wizard.adresseLabel')), {
      target: { value: wert },
    })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('mss.wizard.verbinden') }))
  }

  it('weist Klartext ausserhalb des eigenen Rechners ab, ohne ihn anzufassen', async () => {
    // Ueber diese Adresse gehen Kopplungscode und Refresh-Token. Rust lehnt
    // sie beim Speichern ab (`konfig.rs::backend_url_verboten`) — vorher
    // pruefte der Assistent aber noch die Erreichbarkeit und bestaetigte
    // damit eine Adresse, die er gleich darauf nicht speichern konnte.
    const holen = vi.fn(() => Promise.resolve(json(200, { setup_required: false })))
    vi.stubGlobal('fetch', holen)

    adresseEintragen('http://192.168.1.50:8000')

    await waitFor(() => {
      expect(screen.getByText(i18n.t('mss.wizard.adresseSchema'))).toBeInTheDocument()
    })
    expect(holen).not.toHaveBeenCalled()
    expect(invokeMock).not.toHaveBeenCalledWith('konfig_speichern', expect.anything())
  })

  it('laesst https und den eigenen Rechner durch', async () => {
    const holen = vi.fn(() => Promise.resolve(json(200, { setup_required: false })))
    vi.stubGlobal('fetch', holen)

    // Die Entwicklungsstrecke (Sidecar auf localhost) bleibt nutzbar: dort
    // verlaesst nichts das Geraet.
    adresseEintragen('http://localhost:8000')

    await waitFor(() => {
      expect(holen).toHaveBeenCalledWith('http://localhost:8000/api/auth/setup-status')
    })
    expect(screen.queryByText(i18n.t('mss.wizard.adresseSchema'))).not.toBeInTheDocument()
  })

  it('ergaenzt https:// automatisch, wenn nur die Domain eingegeben wird', async () => {
    const holen = vi.fn(() => Promise.resolve(json(200, { setup_required: false })))
    vi.stubGlobal('fetch', holen)

    adresseEintragen('panel.example.com')

    await waitFor(() => {
      expect(holen).toHaveBeenCalledWith('https://panel.example.com/api/auth/setup-status')
    })
    expect(screen.queryByText(i18n.t('mss.wizard.adresseSchema'))).not.toBeInTheDocument()
  })

  it('faengt fuehrendes https:// beim Einfuegen ab und doppelt nicht', async () => {
    const holen = vi.fn(() => Promise.resolve(json(200, { setup_required: false })))
    vi.stubGlobal('fetch', holen)

    adresseEintragen('https://panel.example.com')

    await waitFor(() => {
      expect(holen).toHaveBeenCalledWith('https://panel.example.com/api/auth/setup-status')
    })
    expect(screen.queryByText(i18n.t('mss.wizard.adresseSchema'))).not.toBeInTheDocument()
  })
})
