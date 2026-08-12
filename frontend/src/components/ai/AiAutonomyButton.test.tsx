import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiAutonomyGrant } from '@/api/ai'
import i18n from '@/i18n'
import { useConfirmStore } from '@/stores/confirmStore'
import { AiAutonomyButton } from './AiAutonomyButton'

vi.mock('@/api/ai', () => ({
  aiApi: {
    listAutonomyGrants: vi.fn(),
    saveAutonomyGrant: vi.fn(),
  },
}))

const SERVER = [
  { id: 7, name: 'valheim-01' },
  { id: 9, name: 'minecraft-02' },
]

function freigabe(serverId: number | null, enabled: boolean): AiAutonomyGrant {
  return {
    id: serverId ?? 0,
    server_id: serverId,
    enabled,
    max_actions_per_hour: 10,
    used_last_hour: 0,
    created_at: '2026-08-12T09:00:00Z',
    updated_at: '2026-08-12T09:00:00Z',
  }
}

/** Rendert den Knopf, klappt das Panel auf und wartet, bis die geladenen
 *  Freigaben angekommen sind — vorher steht der Schalter noch auf „aus" und
 *  ein Klick träfe den falschen Zweig von `save`. */
async function panelOeffnen(vorhandene: AiAutonomyGrant[]) {
  vi.mocked(aiApi.listAutonomyGrants).mockResolvedValue(vorhandene)
  render(<AiAutonomyButton servers={SERVER} />)
  fireEvent.click(screen.getByRole('button', { name: 'Autonomer Modus' }))
  await screen.findByLabelText('Geltungsbereich')
  const erwartetAn = vorhandene.some((row) => row.server_id === null && row.enabled)
  await waitFor(() => expect(screen.getByRole('switch'))
    .toHaveAttribute('aria-checked', String(erwartetAn)))
}

describe('AiAutonomyButton', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    useConfirmStore.setState({ pending: null })
    vi.mocked(aiApi.listAutonomyGrants).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.saveAutonomyGrant).mockReset()
  })

  it('behält das Panel offen, wenn im Bereichs-Dropdown ein Server gewählt wird', async () => {
    // Der Kern des Fehlers: das Optionsmenü unseres `Dropdown` hängt per Portal
    // an `document.body` und liegt damit außerhalb des Panels. Der
    // Außenklick-Wächter hielt eine Option deshalb für „draußen" und schloss
    // das Panel auf `mousedown` — also bevor das `click` der Option feuerte.
    // `setScope` lief nie. Eine serverbezogene Freigabe ließ sich über die
    // Oberfläche gar nicht einstellen.
    render(<AiAutonomyButton servers={SERVER} />)

    fireEvent.click(screen.getByRole('button', { name: 'Autonomer Modus' }))
    fireEvent.click(await screen.findByLabelText('Geltungsbereich'))
    fireEvent.mouseDown(screen.getByRole('option', { name: 'valheim-01' }))
    fireEvent.click(screen.getByRole('option', { name: 'valheim-01' }))

    // Das Panel steht noch, und die Wahl ist angekommen.
    const auswahl = await screen.findByLabelText('Geltungsbereich')
    expect(auswahl).toHaveTextContent('valheim-01')
  })

  it('erklärt bei Serverwahl den Server und nicht das ganze Panel', async () => {
    // `descriptionServer` lag unbenutzt in allen elf Sprachdateien, während der
    // Absatz fest behauptete, die Freigabe wirke „auf allen deinen Servern".
    // Der Text beschrieb also genau den Fall, den man gerade abgewählt hatte.
    render(<AiAutonomyButton servers={SERVER} />)
    fireEvent.click(screen.getByRole('button', { name: 'Autonomer Modus' }))

    expect(await screen.findByText(/auf allen deinen Servern/i)).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Geltungsbereich'))
    fireEvent.click(screen.getByRole('option', { name: 'valheim-01' }))

    await waitFor(() =>
      expect(screen.getByText(/auf genau diesem Server/i)).toBeInTheDocument())
    expect(screen.queryByText(/auf allen deinen Servern/i)).not.toBeInTheDocument()
  })

  it('schließt das Panel weiterhin bei einem echten Klick daneben', async () => {
    // Die Gegenprobe. Ohne sie wäre der erste Test auch dann grün, wenn der
    // Wächter gar nichts mehr schließt.
    render(<AiAutonomyButton servers={SERVER} />)
    fireEvent.click(screen.getByRole('button', { name: 'Autonomer Modus' }))
    expect(await screen.findByLabelText('Geltungsbereich')).toBeInTheDocument()

    fireEvent.mouseDown(document.body)
    await waitFor(() =>
      expect(screen.queryByLabelText('Geltungsbereich')).not.toBeInTheDocument())
  })

  /**
   * Wer „Abbrechen" drückt, hat nichts eingeschaltet.
   *
   * Der Dialog wäre reine Dekoration, wenn `save` schon vor der Antwort
   * speicherte: die Freigabe hinge dann am Konto, obwohl niemand sie erteilt
   * hat. Deshalb steht hier nicht nur „ein Dialog erscheint", sondern auch
   * „vor der Antwort gibt es keinen Speicheraufruf".
   */
  it('schaltet nichts ein, wenn die Rückfrage abgelehnt wird', async () => {
    await panelOeffnen([])
    const schalter = screen.getByRole('switch')

    fireEvent.click(schalter)

    const dialog = useConfirmStore.getState().pending
    expect(dialog?.title).toBe(i18n.t('ai.autonomy.confirmTitle'))
    expect(dialog?.title).toBe('Autonomen Modus einschalten?')
    // Einschalten erweitert Rechte — das ist der Fall, in dem MSM rot werden darf.
    expect(dialog?.danger).toBe(true)
    expect(aiApi.saveAutonomyGrant).not.toHaveBeenCalled()

    await act(async () => { useConfirmStore.getState().resolve(false) })

    expect(aiApi.saveAutonomyGrant).not.toHaveBeenCalled()
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false')
  })

  /**
   * Auch das AUSschalten fragt nach.
   *
   * Die Freigabe entscheidet seit der Guardian-Kopplung mehr als „ohne
   * Rückfrage ausführen": sie ist der Schalter, der die Engine die KI wecken
   * lässt. Wer sie stillschweigend umlegt, hat danach Server, die nachts stehen
   * bleiben, bis jemand hinsieht — und erfährt es erst aus der Störung. Ein
   * versehentlicher Klick auf den Schalter darf das nicht auslösen.
   */
  it('fragt auch beim Ausschalten nach und lässt die Freigabe bei Ablehnung an', async () => {
    await panelOeffnen([freigabe(null, true)])

    fireEvent.click(screen.getByRole('switch'))

    const dialog = useConfirmStore.getState().pending
    expect(dialog?.title).toBe(i18n.t('ai.autonomy.disableTitle'))
    expect(dialog?.title).toBe('Autonomen Modus ausschalten?')
    expect(aiApi.saveAutonomyGrant).not.toHaveBeenCalled()

    await act(async () => { useConfirmStore.getState().resolve(false) })

    // Abgelehnt heißt: nichts gespeichert und die Freigabe steht unverändert
    // weiter auf „an". Ein Schalter, der optisch umspringt und erst beim
    // Neuladen zurückfällt, wäre die schlimmere Variante dieses Fehlers.
    expect(aiApi.saveAutonomyGrant).not.toHaveBeenCalled()
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true')
  })

  /**
   * Der Ausschalt-Text muss die tatsächlich betroffene Reichweite nennen.
   *
   * Denselben Fehler gab es beim Beschreibungsabsatz schon einmal: er behauptete
   * fest „auf allen deinen Servern" und beschrieb damit genau den Fall, den man
   * gerade abgewählt hatte. Wer eine Freigabe für einen einzelnen Server
   * zurücknimmt, darf nicht lesen, sein ganzes Panel sei betroffen.
   */
  it('unterscheidet beim Ausschalten panelweit und serverbezogen', async () => {
    await panelOeffnen([freigabe(null, true), freigabe(7, true)])

    fireEvent.click(screen.getByRole('switch'))
    const panelweit = useConfirmStore.getState().pending?.message
    expect(panelweit).toBe(i18n.t('ai.autonomy.disablePanel'))
    expect(panelweit).toMatch(/auf allen deinen Servern/i)
    await act(async () => { useConfirmStore.getState().resolve(false) })

    fireEvent.click(screen.getByLabelText('Geltungsbereich'))
    fireEvent.click(screen.getByRole('option', { name: 'valheim-01' }))
    await waitFor(() =>
      expect(screen.getByText(/auf genau diesem Server/i)).toBeInTheDocument())
    await waitFor(() =>
      expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true'))

    fireEvent.click(screen.getByRole('switch'))
    const serverbezogen = useConfirmStore.getState().pending?.message
    expect(serverbezogen).toBe(i18n.t('ai.autonomy.disableServer'))
    expect(serverbezogen).not.toBe(panelweit)
    expect(serverbezogen).not.toMatch(/auf allen deinen Servern/i)
    await act(async () => { useConfirmStore.getState().resolve(false) })
  })

  /** Dieselbe Zusage für die Gegenrichtung: `confirmPanel` verspricht die
   *  Guardian-Weckung für alle Server, `confirmServer` nur für den einen.
   *  Stünde beim Einschalten immer der Paneltext, versprächen wir mehr, als die
   *  Freigabe hergibt. */
  it('unterscheidet beim Einschalten panelweit und serverbezogen', async () => {
    await panelOeffnen([])

    fireEvent.click(screen.getByRole('switch'))
    const panelweit = useConfirmStore.getState().pending?.message
    expect(panelweit).toBe(i18n.t('ai.autonomy.confirmPanel'))
    await act(async () => { useConfirmStore.getState().resolve(false) })

    fireEvent.click(screen.getByLabelText('Geltungsbereich'))
    fireEvent.click(screen.getByRole('option', { name: 'minecraft-02' }))
    await waitFor(() =>
      expect(screen.getByText(/auf genau diesem Server/i)).toBeInTheDocument())

    fireEvent.click(screen.getByRole('switch'))
    const serverbezogen = useConfirmStore.getState().pending?.message
    expect(serverbezogen).toBe(i18n.t('ai.autonomy.confirmServer'))
    expect(serverbezogen).not.toBe(panelweit)
    await act(async () => { useConfirmStore.getState().resolve(false) })
  })

  /**
   * Das Kontingent ändert die Reichweite nicht.
   *
   * `save` bekommt hier `nextEnabled === true` bei bereits erteilter Freigabe —
   * beide Rückfragezweige müssen stumm bleiben. Fragte MSM auch hier nach,
   * bekäme man den langen Einschalt-Text für eine Zahlenänderung zu lesen und
   * lernte, den Dialog wegzuklicken, ohne ihn zu lesen. Genau das soll er nicht.
   */
  it('fragt beim Speichern des Kontingents nicht nach', async () => {
    vi.mocked(aiApi.saveAutonomyGrant).mockResolvedValue(freigabe(null, true))
    await panelOeffnen([freigabe(null, true)])

    fireEvent.click(screen.getByRole('button', { name: 'Kontingent speichern' }))

    expect(useConfirmStore.getState().pending).toBeNull()
    await waitFor(() => expect(aiApi.saveAutonomyGrant).toHaveBeenCalledWith({
      server_id: null,
      enabled: true,
      max_actions_per_hour: 10,
    }))
    expect(useConfirmStore.getState().pending).toBeNull()
  })

  /**
   * Rot bedeutet in MSM unumkehrbar.
   *
   * Beim Ausschalten wird eine Erlaubnis zurückgenommen, die man im selben
   * Panel jederzeit wieder erteilen kann — nichts geht verloren, kein Server
   * wird angefasst. Färbte MSM auch diesen Dialog rot, verlöre die Farbe ihre
   * Bedeutung für die Fälle, in denen sie wirklich zählt (Löschen, Wipe,
   * Neuinstallation). Geprüft wird an den Optionen, nicht an einer CSS-Klasse:
   * die Entscheidung fällt in `save`, nicht im Dialog-Bauteil.
   */
  it('färbt den Ausschalt-Dialog nicht destruktiv ein', async () => {
    await panelOeffnen([freigabe(null, true)])

    fireEvent.click(screen.getByRole('switch'))

    const dialog = useConfirmStore.getState().pending
    expect(dialog?.title).toBe(i18n.t('ai.autonomy.disableTitle'))
    expect(dialog?.danger).toBeFalsy()
    expect(dialog?.confirmText).toBe(i18n.t('ai.autonomy.disable'))
    await act(async () => { useConfirmStore.getState().resolve(false) })
  })
})
