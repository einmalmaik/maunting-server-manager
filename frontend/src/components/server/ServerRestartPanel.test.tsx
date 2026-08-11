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

  it('schaltet mit server.config.write um und gibt den Zeitplan frei', async () => {
    usePermissionsStore.setState({ me: nurAmServer(['server.config.write']), isLoading: false })
    render(<ServerRestartPanel server={server} serverId={SERVER_ID} onSaved={() => {}} />)

    const schalter = await screen.findByRole('switch', { name: i18n.t('restarts.enabled') })
    expect(schalter).toBeEnabled()
    expect(schalter).toHaveAttribute('aria-checked', 'false')
    // Solange der Schalter aus ist, sperrt das <fieldset> die Intervallauswahl.
    expect(screen.getByRole('combobox')).toBeDisabled()

    fireEvent.click(schalter)

    // Der Baustein muss denselben Zustand führen wie vorher der Nachbau —
    // sonst wäre der Tausch ein Funktionsverlust.
    expect(schalter).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('combobox')).toBeEnabled()
  })
})