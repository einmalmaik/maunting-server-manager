/**
 * Die Aufgabenliste.
 *
 * Sie ist das Versprechen „alles, was die KI kann, kann der Benutzer auch" als
 * Oberfläche: dieselben Dienstfunktionen, nur ohne Chat. Die Zusagen, die hier
 * als Tests stehen, sind die, die sich mit einer unauffälligen Zeile verlieren
 * ließen — Teilangaben beim Pausieren (sonst überschreibt ein Schalterklick
 * den ganzen Plan), die Bestätigung vor dem Löschen und die Adresse des
 * Hintergrundfensters, in der nur die UUID stehen darf.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiTaskEntry } from '@/api/ai'
import i18n from '@/i18n'
import { confirm } from '@/stores/confirmStore'
import { AufgabenAnsicht } from './AufgabenAnsicht'

vi.mock('@/api/ai', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/ai')>()
  return {
    ...original,
    aiApi: {
      listTasks: vi.fn(),
      createTask: vi.fn(),
      updateTask: vi.fn(),
      deleteTask: vi.fn(),
    },
  }
})

vi.mock('@/stores/confirmStore', () => ({
  confirm: vi.fn(),
}))

const aufgabe = (over: Partial<AiTaskEntry> = {}): AiTaskEntry => ({
  task_id: 'aufgabe-1',
  title: 'Morgens Logs prüfen',
  instruction: 'Prüf die Logs aller Server auf Fehler.',
  kind: 'report',
  plan: 'täglich 08:00 (Europe/Berlin)',
  plan_kind: 'daily',
  time_of_day: '08:00',
  weekdays: null,
  interval_hours: null,
  once_at: null,
  timezone: 'Europe/Berlin',
  channel: 'chat',
  enabled: true,
  conversation_id: 'fenster-1',
  next_run: '2026-08-21T06:00:00Z',
  last_started: null,
  ...over,
})

function zeichnen() {
  return render(
    <MemoryRouter initialEntries={['/ai?ansicht=aufgaben']}>
      <AufgabenAnsicht />
    </MemoryRouter>,
  )
}

describe('AufgabenAnsicht', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.listTasks).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.createTask).mockReset().mockResolvedValue(aufgabe())
    vi.mocked(aiApi.updateTask).mockReset().mockResolvedValue(aufgabe())
    vi.mocked(aiApi.deleteTask).mockReset().mockResolvedValue({ deleted: true, title: '' })
    vi.mocked(confirm).mockReset().mockResolvedValue(true)
  })

  it('zeigt die Aufgaben mit Name und Plan', async () => {
    vi.mocked(aiApi.listTasks).mockResolvedValue([aufgabe()])

    zeichnen()

    expect(await screen.findByText('Morgens Logs prüfen')).toBeInTheDocument()
    // Die Anzeige entsteht aus den strukturierten Feldern (beide Sprachen),
    // nicht aus dem deutschen `plan`-Satz des Backends.
    expect(screen.getByText('Täglich um 08:00 (Europe/Berlin)')).toBeInTheDocument()
  })

  it('sagt in der leeren Liste, wie es weitergeht', async () => {
    zeichnen()

    expect(await screen.findByText(i18n.t('ai.tasks.emptyTitle'))).toBeInTheDocument()
  })

  it('legt eine Aufgabe mit den drei Angaben an', async () => {
    zeichnen()
    await waitFor(() => expect(aiApi.listTasks).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: /Neue Aufgabe/ }))
    fireEvent.change(screen.getByLabelText(i18n.t('ai.tasks.name')), {
      target: { value: 'Backup am Morgen' },
    })
    fireEvent.change(screen.getByLabelText(i18n.t('ai.tasks.prompt')), {
      target: { value: 'Mach ein Backup von allen Servern.' },
    })
    const uhrzeitButton = screen.getByRole('button', { name: i18n.t('ai.tasks.timeOfDay') })
    fireEvent.click(uhrzeitButton)
    fireEvent.click(await screen.findByRole('option', { name: '07:30' }))
    fireEvent.change(screen.getByLabelText(i18n.t('ai.tasks.timezone')), {
      target: { value: 'Europe/Berlin' },
    })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.tasks.create') }))

    await waitFor(() => expect(aiApi.createTask).toHaveBeenCalledTimes(1))
    expect(vi.mocked(aiApi.createTask).mock.calls[0][0]).toEqual({
      title: 'Backup am Morgen',
      instruction: 'Mach ein Backup von allen Servern.',
      kind: 'report',
      channel: 'chat',
      timezone: 'Europe/Berlin',
      plan_kind: 'daily',
      time_of_day: '07:30',
      weekdays: [],
    })
  })

  it('legt eine Intervall-Aufgabe über das Dropdown-Menü an', async () => {
    zeichnen()
    await waitFor(() => expect(aiApi.listTasks).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: /Neue Aufgabe/ }))
    fireEvent.change(screen.getByLabelText(i18n.t('ai.tasks.name')), {
      target: { value: 'Stündliche Logs' },
    })
    fireEvent.change(screen.getByLabelText(i18n.t('ai.tasks.prompt')), {
      target: { value: 'Logs prüfen' },
    })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.tasks.planKindInterval') }))
    const intervallButton = screen.getByRole('button', { name: i18n.t('ai.tasks.intervalHours') })
    fireEvent.click(intervallButton)
    fireEvent.click(await screen.findByRole('option', { name: i18n.t('ai.tasks.planInterval', { count: 6 }) }))
    fireEvent.change(screen.getByLabelText(i18n.t('ai.tasks.timezone')), {
      target: { value: 'Europe/Berlin' },
    })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.tasks.create') }))

    await waitFor(() => expect(aiApi.createTask).toHaveBeenCalledTimes(1))
    expect(vi.mocked(aiApi.createTask).mock.calls[0][0]).toEqual({
      title: 'Stündliche Logs',
      instruction: 'Logs prüfen',
      kind: 'report',
      channel: 'chat',
      timezone: 'Europe/Berlin',
      plan_kind: 'interval',
      interval_hours: 6,
    })
  })

  it('pausiert mit einer Teilangabe statt den ganzen Plan zu schicken', async () => {
    // Das Backend fasst nur an, was genannt ist (`exclude_unset`). Schickte
    // der Schalter das ganze Formular mit, würde jeder Klick den Plan neu
    // schreiben — und bei einer `act`-Aufgabe ohne autonome Freigabe sogar
    // scheitern, obwohl niemand etwas geändert hat.
    vi.mocked(aiApi.listTasks).mockResolvedValue([aufgabe()])
    vi.mocked(aiApi.updateTask).mockResolvedValue(aufgabe({ enabled: false }))

    zeichnen()

    fireEvent.click(await screen.findByRole('switch', { name: i18n.t('ai.tasks.enabledAria') }))
    await waitFor(() => expect(aiApi.updateTask).toHaveBeenCalledTimes(1))
    expect(vi.mocked(aiApi.updateTask).mock.calls[0]).toEqual([
      'aufgabe-1', { enabled: false },
    ])
    // Der neue Zustand steht sichtbar in der Liste — auch eine von der KI
    // (oder einer manuellen Zeitplan-Änderung) pausierte Aufgabe sähe so aus.
    expect(await screen.findByText(i18n.t('ai.tasks.paused'))).toBeInTheDocument()
  })

  it('löscht nicht ohne Bestätigung', async () => {
    vi.mocked(aiApi.listTasks).mockResolvedValue([aufgabe()])
    vi.mocked(confirm).mockResolvedValue(false)

    zeichnen()

    fireEvent.click(await screen.findByRole('button', { name: /Löschen/ }))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(aiApi.deleteTask).not.toHaveBeenCalled()
  })

  it('löscht nach Bestätigung und nimmt die Zeile aus der Liste', async () => {
    vi.mocked(aiApi.listTasks).mockResolvedValue([aufgabe()])

    zeichnen()

    fireEvent.click(await screen.findByRole('button', { name: /Löschen/ }))
    await waitFor(() => expect(aiApi.deleteTask).toHaveBeenCalledWith('aufgabe-1'))
    await waitFor(() => {
      expect(screen.queryByText('Morgens Logs prüfen')).toBeNull()
    })
  })

  it('verlinkt das Hintergrundfenster nur über die UUID', async () => {
    // Keine sensiblen Daten in URLs: in der Adresse steht die Kennung des
    // Fensters, nie Titel oder Auftragstext.
    vi.mocked(aiApi.listTasks).mockResolvedValue([aufgabe()])

    zeichnen()

    const link = await screen.findByRole('link', { name: i18n.t('ai.tasks.openWindow') })
    expect(link).toHaveAttribute('href', '/ai?ansicht=worker&id=fenster-1')
  })

  it('zeigt ohne Fenster keinen Verlaufslink', async () => {
    vi.mocked(aiApi.listTasks).mockResolvedValue([aufgabe({ conversation_id: null })])

    zeichnen()

    expect(await screen.findByText('Morgens Logs prüfen')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: i18n.t('ai.tasks.openWindow') })).toBeNull()
  })
})
