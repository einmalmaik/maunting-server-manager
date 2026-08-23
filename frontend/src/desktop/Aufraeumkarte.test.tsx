/**
 * Die Aufräumkarte — was sie zeigt und was sie bestätigt.
 *
 * Die eine Aussage, die hier wirklich zählt: **bestätigt wird der Plan, den
 * Rust hält, nicht der, den diese Karte anzeigt.** Die Karte schickt beim
 * Klick keine Pfadliste mit; sie ruft `aufraeumen_bestaetigen` ohne
 * Argumente. Ein Renderer, der eine harmlose Liste zeigt und eine andere
 * löschen lässt, ist damit gar nicht erst möglich.
 *
 * Daneben: die Ablehnung fasst nichts an und meldet trotzdem — sonst stünde
 * der Auftrag bis zum Fristablauf offen, und das Modell erführe Stille statt
 * einer Antwort. Und: gemeldet wird der Auftrag, der gefragt hat — die
 * Kennung steht im Ereignis, nicht im Zustand der Oberfläche.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

// i18n ist im Test nicht geladen: `t()` gibt den Schluessel zurueck. Die
// Knoepfe werden deshalb ueber ihre Schluessel gegriffen und nicht ueber
// deutschen Text — sonst prueft der Test die Uebersetzungsdatei mit, und ein
// umformulierter Knopf faerbte ihn rot, ohne dass sich Verhalten geaendert
// haette.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const bestaetigenMock = vi.fn()
const ablehnenMock = vi.fn()
const ergebnisMeldenMock = vi.fn()
let ereignisRuf: ((e: { payload: unknown }) => void) | null = null

vi.mock('@tauri-apps/api/event', () => ({
  listen: (_name: string, rueckruf: (e: { payload: unknown }) => void) => {
    ereignisRuf = rueckruf
    return Promise.resolve(() => {})
  },
}))

vi.mock('./tauri', () => ({
  aufraeumenBestaetigen: (...args: unknown[]) => bestaetigenMock(...args),
  aufraeumenAblehnen: (...args: unknown[]) => ablehnenMock(...args),
}))

vi.mock('./desktopJobs', () => ({
  ergebnisMelden: (...args: unknown[]) => ergebnisMeldenMock(...args),
}))

import { Aufraeumkarte } from './Aufraeumkarte'

const PLAN = {
  aktion: 'papierkorb',
  grund: 'Alte Installationsdateien in Downloads',
  posten: [
    { pfad: 'C:\\Users\\einma\\Downloads\\setup.exe', bytes: 524_288_000, zone: 'frei' },
    { pfad: 'C:\\Users\\einma\\Downloads\\alt.iso', bytes: 1_073_741_824, zone: 'frei' },
  ],
}

async function zeigen(plan: unknown = PLAN) {
  render(<Aufraeumkarte offenerAuftragId="job-1" />)
  await waitFor(() => expect(ereignisRuf).not.toBeNull())
  ereignisRuf!({ payload: plan })
}

describe('Aufraeumkarte', () => {
  beforeEach(() => {
    ereignisRuf = null
    bestaetigenMock.mockReset().mockResolvedValue({ geloescht: 2 })
    ablehnenMock.mockReset().mockResolvedValue(undefined)
    ergebnisMeldenMock.mockReset().mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('zeigt ohne Ereignis nichts', () => {
    const { container } = render(<Aufraeumkarte offenerAuftragId={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('nennt jeden Pfad und den Grund', async () => {
    await zeigen()
    await screen.findByText(PLAN.grund)
    expect(screen.getByText(PLAN.posten[0].pfad)).toBeInTheDocument()
    expect(screen.getByText(PLAN.posten[1].pfad)).toBeInTheDocument()
  })

  it('bestaetigt ohne die angezeigte Liste mitzuschicken', async () => {
    await zeigen()
    const knopf = await screen.findByRole('button', { name: /jaWeich/i })
    fireEvent.click(knopf)

    await waitFor(() => expect(bestaetigenMock).toHaveBeenCalledTimes(1))
    // Der Kern: keine Argumente. Der Plan liegt in Rust.
    expect(bestaetigenMock).toHaveBeenCalledWith()
    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledTimes(1))
    const [jobId, ok, inhalt] = ergebnisMeldenMock.mock.calls[0]
    expect(jobId).toBe('job-1')
    expect(ok).toBe(true)
    expect(inhalt).toEqual({ geloescht: 2 })
  })

  it('beantwortet den Auftrag aus dem Ereignis, nicht den aus dem Zustand', async () => {
    // Die Zuordnung hing daran, dass die Auftragsschleife ihre Kennung noch
    // vor dem Ereignis in den Zustand bekommt. Kommt das Ereignis zuerst —
    // oder liegt dort noch die Kennung des vorigen Auftrags —, quittierte der
    // Klick den falschen: die Dateien dieses Auftrags verschwanden, und als
    // erledigt galt ein anderer. Rust legt die richtige Kennung in die
    // Nutzlast, und die gilt.
    render(<Aufraeumkarte offenerAuftragId="job-alt" />)
    await waitFor(() => expect(ereignisRuf).not.toBeNull())
    ereignisRuf!({ payload: { ...PLAN, auftrag_id: 'job-neu' } })

    fireEvent.click(await screen.findByRole('button', { name: /jaWeich/i }))
    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledTimes(1))
    expect(ergebnisMeldenMock.mock.calls[0][0]).toBe('job-neu')
  })

  it('fasst bei Ablehnung nichts an und meldet trotzdem', async () => {
    await zeigen()
    const knopf = await screen.findByRole('button', { name: /aufraeumen\.nein/i })
    fireEvent.click(knopf)

    await waitFor(() => expect(ablehnenMock).toHaveBeenCalledTimes(1))
    expect(bestaetigenMock).not.toHaveBeenCalled()
    const [, ok, inhalt] = ergebnisMeldenMock.mock.calls[0]
    expect(ok).toBe(true)
    expect(inhalt).toMatchObject({ bestaetigt: false })
  })

  it('meldet einen Fehlschlag als Fehlschlag', async () => {
    bestaetigenMock.mockRejectedValue(new Error('Zugriff verweigert'))
    await zeigen()
    fireEvent.click(await screen.findByRole('button', { name: /jaWeich/i }))

    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledTimes(1))
    const [, ok, inhalt, code] = ergebnisMeldenMock.mock.calls[0]
    expect(ok).toBe(false)
    expect(inhalt).toEqual({ fehler: 'Zugriff verweigert' })
    expect(code).toBe('DESKTOP_TOOL_FAILED')
  })

  it('verspricht den Papierkorb nur, wenn wirklich alles dorthin geht', async () => {
    await zeigen()
    // Beide Posten liegen in `frei` — hier stimmt die weiche Zusage.
    expect(await screen.findByText('mss.aufraeumen.warnungWeich')).toBeInTheDocument()
    expect(screen.queryByText('mss.aufraeumen.sofortWeg')).toBeNull()
  })

  it('sagt es, wenn ein Posten am Papierkorb vorbei geloescht wird', async () => {
    // Ein Muell-Posten: Rust ueberspringt dort den Papierkorb
    // (`aufraeumen::ausfuehren`). Stuende hier weiter die weiche Zusage,
    // bestaetigte der Mensch ein endgueltiges Loeschen mit einem
    // "laesst sich zurueckholen" vor Augen.
    await zeigen({
      ...PLAN,
      posten: [
        PLAN.posten[0],
        { pfad: 'C:\\Users\\einma\\AppData\\Local\\Temp\\alt.tmp', bytes: 4096, zone: 'muell' },
      ],
    })
    expect(await screen.findByText('mss.aufraeumen.warnungGemischt')).toBeInTheDocument()
    expect(screen.queryByText('mss.aufraeumen.warnungWeich')).toBeNull()
    // Und die betroffene Zeile ist als solche erkennbar.
    expect(screen.getByText(/mss\.aufraeumen\.sofortWeg/)).toBeInTheDocument()
  })

  it('braucht beim Leeren des Papierkorbs keine Liste', async () => {
    await zeigen({ aktion: 'papierkorb_leeren', grund: 'Platz freigeben', posten: [] })
    await screen.findByText('Platz freigeben')
    // Kein Listeneintrag, aber ein Knopf, der endgueltig heisst.
    expect(screen.queryByRole('listitem')).toBeNull()
  })
})
