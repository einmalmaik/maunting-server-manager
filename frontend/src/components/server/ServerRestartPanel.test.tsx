/**
 * Warum es diesen Test gibt: Der Schalter für automatische Neustarts war hier
 * von Hand nachgebaut — eine sr-only-Checkbox mit zwei <span> als Optik. Das
 * `disabled` saß nur auf der unsichtbaren Checkbox, nicht auf dem, was der
 * Benutzer sieht und anklickt. Ohne `server.config.write` sah der Schalter
 * deshalb voll bedienbar aus und ein Klick verpuffte wortlos.
 *
 * Der Test prüft darum nicht die Optik (die kann jsdom ohnehin nicht messen),
 * sondern genau die Zusage, die der Nachbau gebrochen hat: Der Schalter gibt
 * sich als Schalter zu erkennen und meldet, dass er gesperrt ist — und mit
 * Schreibrecht schaltet er wirklich um und gibt den Zeitplan darunter frei.
 * Ohne den Fix existiert die Rolle `switch` gar nicht und beide Tests fallen.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { ServerRestartPanel } from './ServerRestartPanel'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import type { MePermissions } from '@/types/permissions'
import type { Server } from '@/types'

const SERVER_ID = 42

// Auto-Restart aus und `restart_interval_hours` gesetzt: dann startet das Panel
// im Intervall-Modus und rendert genau ein <select>. An dessen Sperrzustand
// lässt sich ablesen, ob das Umschalten tatsächlich gewirkt hat.
const server = {
  id: SERVER_ID,
  name: 'MaickCraft Public',
  auto_restart: false,
  restart_interval_hours: 4,
  restart_time_utc: null,
  restart_times_utc: null,
  last_auto_restart_attempt_at: null,
  last_auto_restart_completed_at: null,
  last_auto_restart_status: null,
  next_auto_restart_at: null,
} as unknown as Server

// Kein Eigentümer, keine globalen Rechte — nur das, was per Delegation an
// genau diesem Server hängt. Nur so greift die Prüfung in Zeile 25 wirklich.
function nurAmServer(keys: string[]): MePermissions {
  return {
    is_owner: false,
    role_id: 2,
    role_name: 'user',
    global_keys: [],
    server_keys: { [String(SERVER_ID)]: keys },
  }
}

describe('ServerRestartPanel', () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    i18n.changeLanguage('en')
    // Das Panel holt beim Aufbau das Zeitformat aus /settings. Ohne Antwort
    // liefe der Test in einen unbehandelten Fetch-Fehler.
    fetchSpy = vi.spyOn(global, 'fetch')
    fetchSpy.mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: () => Promise.resolve({ time_format: '24h' }),
      text: () => Promise.resolve('{"time_format":"24h"}'),
    } as Response)
  })

  afterEach(() => {
    fetchSpy.mockRestore()
    usePermissionsStore.setState({ me: null, isLoading: false })
  })

  it('zeigt den Umschalter ohne server.config.write als gesperrt an', async () => {
    usePermissionsStore.setState({ me: nurAmServer(['server.config.read']), isLoading: false })
    render(<ServerRestartPanel server={server} serverId={SERVER_ID} onSaved={() => {}} />)

    // Der Handnachbau hatte keine Schalter-Rolle, nur eine versteckte Checkbox:
    // ohne den Fix findet diese Abfrage nichts.
    const schalter = await screen.findByRole('switch', { name: i18n.t('restarts.enabled') })
    expect(schalter).toBeDisabled()
  })

  it('zeigt das Abzeichen „von der KI verwaltet" nur, wenn es stimmt', async () => {
    // Das Abzeichen ist die halbe Konfliktregel: die andere Hälfte (manuelles
    // Speichern nimmt der KI die Verwaltung ab und pausiert die Aufgabe) liegt
    // im Backend. Stünde das Abzeichen immer da, wäre es keine Auskunft.
    usePermissionsStore.setState({ me: nurAmServer(['server.config.read']), isLoading: false })
    const { rerender } = render(
      <ServerRestartPanel server={server} serverId={SERVER_ID} onSaved={() => {}} />,
    )

    await screen.findByRole('switch', { name: i18n.t('restarts.enabled') })
    expect(screen.queryByText(i18n.t('restarts.aiManaged'))).toBeNull()

    rerender(
      <ServerRestartPanel
        server={{ ...server, restart_ai_managed: true } as Server}
        serverId={SERVER_ID}
        onSaved={() => {}}
      />,
    )
    expect(screen.getByText(i18n.t('restarts.aiManaged'))).toBeInTheDocument()
  })

  it('schaltet mit server.config.write um und gibt den Zeitplan frei', async () => {
    usePermissionsStore.setState({ me: nurAmServer(['server.config.write']), isLoading: false })
    render(<ServerRestartPanel server={server} serverId={SERVER_ID} onSaved={() => {}} />)

    const schalter = await screen.findByRole('switch', { name: i18n.t('restarts.enabled') })
    expect(schalter).toBeEnabled()
    expect(schalter).toHaveAttribute('aria-checked', 'false')
    // Solange der Schalter aus ist, ist die Intervallauswahl gesperrt. Seit
    // dem Wechsel auf die Dropdown-Komponente (Design-DNA: kein natives
    // <select>) ist sie ein <button aria-haspopup="listbox"> — die Rolle
    // 'combobox' gibt es hier nicht mehr, gefunden wird über das aria-label.
    const intervall = screen.getByRole('button', { name: i18n.t('restarts.interval') })
    expect(intervall).toBeDisabled()

    fireEvent.click(schalter)

    // Der Baustein muss denselben Zustand führen wie vorher der Nachbau —
    // sonst wäre der Tausch ein Funktionsverlust.
    expect(schalter).toHaveAttribute('aria-checked', 'true')
    expect(intervall).toBeEnabled()
  })

  it('waehlt ein Intervall ueber die Dropdown-Optionen', async () => {
    // Die Optionen liegen per Portal an document.body — wer sie nach dem
    // Klick sucht, nimmt screen, nicht den container. Geprüft wird, dass die
    // Auswahl wirklich im Zustand ankommt (der Trigger zeigt danach das neue
    // Label), nicht nur, dass ein Menü aufgeht.
    usePermissionsStore.setState({ me: nurAmServer(['server.config.write']), isLoading: false })
    render(<ServerRestartPanel server={{ ...server, auto_restart: true } as Server} serverId={SERVER_ID} onSaved={() => {}} />)

    const intervall = await screen.findByRole('button', { name: i18n.t('restarts.interval') })
    fireEvent.click(intervall)
    fireEvent.click(await screen.findByRole('option', { name: i18n.t('restarts.everyHours', { count: 24 }) }))

    expect(intervall).toHaveTextContent(i18n.t('restarts.everyHours', { count: 24 }))
  })

  it('zeigt auch Werte ausserhalb des Rasters an statt des Platzhalters', async () => {
    // Die KI darf jedes Intervall (1–168 h) und jede Uhrzeit setzen — das
    // Optionsraster kennt aber nur feste Stufen bzw. halbe Stunden. Ein
    // gespeicherter Wert wie 5 h oder 04:17 muss trotzdem am Trigger stehen;
    // ohne die Einspeisung zeigte die Dropdown-Komponente nur 'Auswählen'.
    usePermissionsStore.setState({ me: nurAmServer(['server.config.read']), isLoading: false })
    // Zwei getrennte Renders statt rerender: Modus und Zeiten sind
    // useState-Initialwerte und folgen einem neuen Server-Prop nicht.
    const erster = render(
      <ServerRestartPanel
        server={{ ...server, auto_restart: true, restart_interval_hours: 5 } as Server}
        serverId={SERVER_ID}
        onSaved={() => {}}
      />,
    )
    const intervall = await screen.findByRole('button', { name: i18n.t('restarts.interval') })
    expect(intervall).toHaveTextContent(i18n.t('restarts.everyHours', { count: 5 }))
    erster.unmount()

    render(
      <ServerRestartPanel
        server={{
          ...server,
          auto_restart: true,
          restart_interval_hours: null,
          restart_times_utc: '04:17',
        } as Server}
        serverId={SERVER_ID}
        onSaved={() => {}}
      />,
    )
    const zeit = await screen.findByRole('button', { name: i18n.t('restarts.fixedTimes') })
    expect(zeit).toHaveTextContent('04:17')
  })
})