import { fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AiRegionalAnalysis, AiVoiceConfig } from '@/api/ai'
import i18n from '@/i18n'
import { SprachAnsicht } from './SprachAnsicht'
import type { Beleg, Sprachzeile, Sprachzustand, Vorschlag } from './useSprachsitzung'

const starten = vi.fn()
const beenden = vi.fn()

let sitzung: {
  zustand: Sprachzustand
  zeilen: Sprachzeile[]
  werkzeug: string | null
  fehler: string | null
  belege: Beleg[]
  vorschlag: Vorschlag | null
  geoData: AiRegionalAnalysis | null
  setGeoData: ReturnType<typeof vi.fn>
}

vi.mock('./useSprachsitzung', () => ({
  useSprachsitzung: () => ({ ...sitzung, pegel: () => 0, starten, beenden }),
}))

// Die Kugel zeichnet auf ein Canvas, und jsdom hat keinen 2D-Kontext. Sie
// bringt sich selbst nicht um (der Kontext wird geprueft), aber hier geht es um
// die Bedienung — nicht um Farbverlaeufe.
vi.mock('./Sprachblase', () => ({ Sprachblase: () => null }))

const KONFIGURATION: AiVoiceConfig = {
  available: true,
  model: 'openai/gpt-5.6',
  // Die Voice ID des Zugangs. Eine Standardstimme gibt es nicht mehr: ohne
  // hinterlegte Kennung meldet `/config` `available: false`, und diese Ansicht
  // wird gar nicht erst gezeichnet. Sie bekommt deshalb nie `null`.
  voice: '21m00Tcm4TlvDq8ikWAM',
  sample_rate: 24_000,
  max_seconds: 900,
}

function ansicht(
  teil: Partial<typeof sitzung> = {},
  aufChat = vi.fn(),
  konfiguration: AiVoiceConfig = KONFIGURATION,
) {
  sitzung = {
    zustand: 'bereit',
    zeilen: [],
    werkzeug: null,
    fehler: null,
    belege: [],
    vorschlag: null,
    geoData: null,
    setGeoData: vi.fn(),
    ...teil,
  }
  const ergebnis = render(
    <SprachAnsicht konfiguration={konfiguration} aufChat={aufChat} />,
  )
  return { ...ergebnis, aufChat }
}

describe('SprachAnsicht', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  beforeEach(() => {
    starten.mockClear()
    beenden.mockClear()
  })

  it('hat fuer jeden Zustand Ueberschrift und Erklaerung', () => {
    const zustaende: Sprachzustand[] = ['aus', 'verbindet', 'bereit', 'hoert', 'denkt', 'spricht']

    // Ein fehlender Schluessel kommt bei i18next als der Schluessel selbst
    // zurueck. Ohne diese Zusicherung stuende im Panel woertlich
    // `ai.voice.hint.hoert` — gross und mittig.
    for (const zustand of zustaende) {
      expect(i18n.t(`ai.voice.zustand.${zustand}`)).not.toBe(`ai.voice.zustand.${zustand}`)
      expect(i18n.t(`ai.voice.hint.${zustand}`)).not.toBe(`ai.voice.hint.${zustand}`)
    }
    for (const s of ['ai.voice.end', 'ai.voice.endHint', 'ai.voice.toVoiceMode', 'ai.voice.toTextMode']) {
      expect(i18n.t(s)).not.toBe(s)
    }
  })

  it('faengt von selbst an zu hoeren', () => {
    ansicht()

    // Wer in den Sprachmodus wechselt, will sprechen. Ein zweiter Klick auf
    // „jetzt aber wirklich" waere eine Tuer hinter der Tuer.
    expect(starten).toHaveBeenCalledOnce()
  })

  it('legt auf, bevor sie zurueck in den Chat geht', () => {
    const { aufChat } = ansicht({ zustand: 'hoert' })

    fireEvent.click(screen.getByText(i18n.t('ai.voice.end')))

    // Die Reihenfolge ist die Zusage: andersherum bliebe fuer einen
    // Wimpernschlag ein offenes Mikrofon hinter einer Ansicht stehen, die es
    // nicht mehr anzeigt.
    expect(beenden).toHaveBeenCalledOnce()
    expect(aufChat).toHaveBeenCalledOnce()
    expect(beenden.mock.invocationCallOrder[0])
      .toBeLessThan(aufChat.mock.invocationCallOrder[0])
  })

  it('beendet das Gespraech mit ESC', () => {
    const { aufChat } = ansicht({ zustand: 'spricht' })

    fireEvent.keyDown(window, { key: 'Escape' })

    expect(beenden).toHaveBeenCalledOnce()
    expect(aufChat).toHaveBeenCalledOnce()
  })

  it('nennt die Tastenkombination am Knopf', () => {
    ansicht()

    // Eine Tastenkombination, die man nicht sieht, ist keine.
    expect(screen.getByText(i18n.t('ai.voice.endHint'))).toBeInTheDocument()
  })

  it('zeigt den Fehler statt der Zustandsueberschrift', () => {
    ansicht({ zustand: 'aus', fehler: 'ai.voice.errors.audio' })

    expect(screen.getByText(i18n.t('ai.voice.errors.audio'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('ai.voice.zustand.aus'))).not.toBeInTheDocument()
  })

  it('zeigt nur den Werkzeugnamen', () => {
    ansicht({ zustand: 'denkt', werkzeug: 'read_server_status' })

    // Argumente tragen Serverkennungen und Pfade.
    expect(screen.getByText('read_server_status')).toBeInTheDocument()
  })

  it('haelt den Zustand vorlesbar', () => {
    ansicht({
      zustand: 'spricht',
    })

    const bereich = screen.getByText('Spricht').closest('[aria-live]')
    expect(bereich).toHaveAttribute('aria-live', 'polite')
  })

  it('zeigt die Belegstelle als reinen Text und sagt, woher sie stammt', () => {
    ansicht({
      zustand: 'spricht',
      belege: [
        {
          quelle: 'latest.log',
          zeilen: [
            '[12:03:44] [Server thread/ERROR]: **kein fetter Text**',
            'siehe [kein Link](https://boese.example/) — Zeile 88',
          ],
        },
      ],
    })

    expect(screen.getByText(i18n.t('ai.voice.beleg.heading'))).toBeInTheDocument()
    expect(screen.getByText('latest.log')).toBeInTheDocument()
    // Ohne diesen Hinweis kann niemand unterscheiden, was die KI *sagt* und was
    // sie nur *zeigt* — und genau darauf beruht der ganze Kasten.
    expect(screen.getByText(i18n.t('ai.voice.beleg.untrusted'))).toBeInTheDocument()

    // Reiner Text heisst reiner Text: Sternchen und Klammern stehen so da, wie
    // sie im Log stehen. Ein Markdown-Renderer waere hier der kuerzeste Weg von
    // einer Logzeile zu einem klickbaren Link im Panel — geschrieben hat die
    // Zeile irgendwer auf irgendeinem Server, nicht die KI.
    expect(screen.getByText(/\*\*kein fetter Text\*\*/)).toBeInTheDocument()
    expect(screen.getByText(/\[kein Link\]\(https:\/\/boese\.example\/\)/)).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('nennt die anstehende Aktion beim Namen — und gibt ihr keinen Knopf', () => {
    ansicht({
      zustand: 'spricht',
      vorschlag: {
        werkzeug: 'propose_server_delete',
        wirkung: 'Der Server „Kreativ" und alle seine Dateien werden entfernt.',
      },
    })

    expect(screen.getByText(i18n.t('ai.voice.vorschlag.heading'))).toBeInTheDocument()
    // Derselbe Werkzeugname wie auf der Karte im Chat, aus derselben Quelle.
    expect(
      screen.getByText(i18n.t('ai.actions.tools.propose_server_delete')),
    ).toBeInTheDocument()
    expect(screen.getByText(/Der Server „Kreativ"/)).toBeInTheDocument()

    // Der Kern der Zusage. Im Sprachmodus entscheidet die Stimme; ein Knopf
    // waere ein zweiter Weg zum selben Ziel — und damit ein Zustand, den Bruecke
    // und Ansicht auseinanderhalten muessten (geklickt, waehrend gesprochen
    // wurde?). Die Knoepfe, die die Ansicht sonst hat, sind Mikrofon, Zahnrad
    // und „Gespraech beenden"; im Vorschlagskasten selbst ist keiner.
    const kasten = screen.getByText(i18n.t('ai.voice.vorschlag.heading')).closest('section')
    expect(kasten).not.toBeNull()
    expect(kasten?.querySelector('button')).toBeNull()
    expect(screen.getByText(i18n.t('ai.voice.vorschlag.hint'))).toBeInTheDocument()
  })

  it('zeigt keinen Vorschlagskasten, solange keiner ansteht', () => {
    ansicht({ zustand: 'spricht' })

    expect(screen.queryByText(i18n.t('ai.voice.vorschlag.heading'))).not.toBeInTheDocument()
  })

  it('zeigt nur die zuletzt gezeigte Stelle', () => {
    ansicht({
      zustand: 'spricht',
      belege: [
        { quelle: 'server.properties', zeilen: ['online-mode=true'] },
        { quelle: 'latest.log', zeilen: ['[Server thread/ERROR]: Adresse belegt'] },
      ],
    })

    // Uebereinander waere es ein Protokoll. Die KI spricht ueber *eine* Stelle,
    // und die muss dastehen — sonst liest der Mensch beim Zuhoeren die falsche.
    expect(screen.getByText(/Adresse belegt/)).toBeInTheDocument()
    expect(screen.queryByText(/online-mode=true/)).not.toBeInTheDocument()
    expect(screen.queryByText('server.properties')).not.toBeInTheDocument()
  })

  it('zeigt hinter dem Zahnrad Angaben und keine erfundenen Regler', () => {
    ansicht()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.voice.settings') }))

    expect(screen.getByText('openai/gpt-5.6')).toBeInTheDocument()
    expect(screen.getByText('24 kHz')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.voice.info.minutes', { count: 15 }))).toBeInTheDocument()
    // Kein Schalter, der nichts tut: es gibt am Sprachmodus nichts zu stellen,
    // und das steht auch so da.
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('nennt hinter dem Zahnrad die Stimme, mit der gerade gesprochen wird', () => {
    ansicht({}, vi.fn(), { ...KONFIGURATION, voice: 'pNInz6obpgDQGcFmaJgB' })

    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.voice.settings') }))

    // Was hier steht, kommt vom Server. Eine fest eingetragene Kennung waere
    // eine zweite Wahrheit — und sie loege in dem Augenblick, in dem der
    // Betreiber am Zugang eine andere Stimme hinterlegt. Seit ElevenLabs ist
    // das kein theoretischer Fall mehr: die Kennung gehoert seinem Konto, MSM
    // kann sie nicht einmal raten.
    expect(screen.getByText(i18n.t('ai.voice.info.voice'))).toBeInTheDocument()
    expect(screen.getByText('pNInz6obpgDQGcFmaJgB')).toBeInTheDocument()
  })

  it('schliesst mit ESC erst die Angaben und dann das Gespraech', () => {
    const { aufChat } = ansicht()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.voice.settings') }))

    fireEvent.keyDown(window, { key: 'Escape' })

    // Die erste Flucht gilt dem, was zuletzt aufging. Sonst riesse ein ESC,
    // das nur das Panel schliessen sollte, das ganze Gespraech ab.
    expect(screen.queryByText('openai/gpt-5.6')).not.toBeInTheDocument()
    expect(aufChat).not.toHaveBeenCalled()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(aufChat).toHaveBeenCalledOnce()
  })

  it('oeffnet 3-Spalten-Kommandozentren-Modus sofort bei analyze_region', () => {
    ansicht({ werkzeug: 'analyze_region', zustand: 'denkt' })

    // Im 3-Spalten-Modus wird das RegionalInfoPanel gerendert
    expect(screen.getByLabelText(i18n.t('ai.geo.panelTitle', 'Regionale Analyse'))).toBeInTheDocument()
  })

  it('zeigt im regionalen Echtzeitmodus keine Transkriptzeilen', () => {
    ansicht({
      werkzeug: 'analyze_region',
      zeilen: [{ wer: 'ich', text: 'Wie ist es in Los Angeles?' }],
    })

    expect(screen.queryByText('Wie ist es in Los Angeles?')).not.toBeInTheDocument()
  })

  it('bleibt bei einem unvollständigen Echtzeit-Payload bedienbar', () => {
    ansicht({
      werkzeug: 'analyze_region',
      geoData: { location: 'Berlin', coordinates: { latitude: 52.52 } } as unknown as AiRegionalAnalysis,
    })

    expect(screen.getByLabelText(i18n.t('ai.geo.panelTitle', 'Regionale Analyse'))).toBeInTheDocument()
    expect(screen.queryByText('52.5200° N')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('ai.voice.end') })).toBeInTheDocument()
  })
})
