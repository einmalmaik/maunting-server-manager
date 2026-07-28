import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BlueprintBuilder } from './BlueprintBuilder'
import { validateBlueprintDraft, type BlueprintDraft } from './contract'
import i18n from '@/i18n'

function Harness({ mode = 'create' as const }) {
  const [open, setOpen] = useState(false)
  return <><button type="button" onClick={() => setOpen(true)}>Editor öffnen</button>{open && <BlueprintBuilder mode={mode} entries={[]} onClose={() => setOpen(false)} onSaved={vi.fn().mockResolvedValue(undefined)} />}</>
}

describe('BlueprintBuilder accessibility', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('announces the create-mode safety rule and exposes native package selects', async () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Editor öffnen' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAccessibleDescription(/niemals stillschweigend ersetzt/i)
    expect(screen.getByRole('button', { name: /Kategorie/i })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /Editor schließen/i })).toHaveFocus())
  })

  it('keeps the overlay full-screen and the navigation horizontal below lg', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Editor öffnen' }))

    const overlay = screen.getByTestId('blueprint-builder-overlay')
    const navigation = screen.getByRole('navigation', { name: /Blueprint-Abschnitte/i })
    const actions = screen.getByTestId('blueprint-builder-actions')
    expect(overlay.className).toContain('lg:pl-64')
    expect(overlay.className).not.toContain('md:pl-64')
    expect(navigation.className).toContain('lg:overflow-y-auto')
    expect(navigation.querySelector('ol')?.className).toContain('grid-cols-4')
    expect(navigation.querySelector('ol')?.className).toContain('lg:grid-cols-1')
    expect(actions.className).toContain('grid-cols-[auto_minmax(0,1fr)]')
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('closes on Escape and returns focus to the opener', async () => {
    render(<Harness />)
    const opener = screen.getByRole('button', { name: 'Editor öffnen' })
    opener.focus()
    fireEvent.click(opener)
    await waitFor(() => screen.getByRole('dialog'))
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(opener).toHaveFocus())
    expect(document.body.style.overflow).not.toBe('hidden')
  })

  it('uses keyboard-operable native dropdowns and links review errors back to a section', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Editor öffnen' }))
    const category = screen.getByRole('button', { name: /Kategorie/i })
    fireEvent.click(category)
    const option = screen.getByRole('option', { name: /Bot/i })
    fireEvent.click(option)
    expect(category).toHaveTextContent(/Bot/i)
    fireEvent.click(screen.getByRole('button', { name: /Prüfen/i }))
    fireEvent.click(screen.getByRole('button', { name: /meta\.id/i }))
    expect(screen.getByRole('button', { name: /Grundlagen/i })).toHaveAttribute('aria-current', 'step')
  })

  it('preserves process and recovery siblings when individual Guardian controls change', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Editor öffnen' }))
    fireEvent.click(screen.getByRole('button', { name: /Autopilot/ }))

    fireEvent.click(screen.getByRole('checkbox', { name: /Hauptprozess-Liveness/i }))
    fireEvent.click(screen.getByText('Erweiterte Prüfparameter'))
    fireEvent.change(screen.getAllByLabelText('Prüf-ID')[0], { target: { value: 'main-process' } })
    fireEvent.click(screen.getByRole('button', { name: 'Policy hinzufügen' }))

    fireEvent.click(screen.getByRole('button', { name: /Prüfen/ }))
    const json = JSON.parse(screen.getByText(/"main-process"/).textContent ?? '{}')
    expect(json.health.process).toEqual({
      required: false,
      id: 'main-process',
      interval: '15s',
      failure_threshold: 1,
      success_threshold: 1,
      required_for_startup: true,
      required_for_verification: true,
    })
    expect(json.recovery).toMatchObject({
      policies: [{ match: 'port-conflict', action: 'restart' }],
      max_attempts: 3,
      attempt_window_seconds: 1800,
      cooldown_seconds: 300,
      verification: {
        minimum_healthy_duration_seconds: 30,
        required_consecutive_successes: 3,
        verification_timeout_seconds: 180,
      },
    })
  })

  it('removes an HTTP-only path when the application probe type changes but keeps its siblings', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Editor öffnen' }))
    fireEvent.click(screen.getByRole('button', { name: /Autopilot/ }))

    const type = screen.getByRole('button', { name: 'Abfrage-Typ' })
    fireEvent.click(type)
    fireEvent.click(screen.getByRole('option', { name: /HTTP Ping/ }))
    fireEvent.change(screen.getByLabelText('HTTP-Pfad'), { target: { value: '/ready' } })
    fireEvent.click(screen.getByText('Erweiterte Prüfparameter'))
    fireEvent.change(screen.getByLabelText('Maximale Antwortgröße'), { target: { value: '8192' } })
    fireEvent.click(type)
    fireEvent.click(screen.getByRole('option', { name: 'TCP' }))

    fireEvent.click(screen.getByRole('button', { name: /Prüfen/ }))
    const json = JSON.parse(screen.getByText(/"max_response_bytes": 8192/).textContent ?? '{}')
    expect(json.health.application).toMatchObject({
      type: 'tcp',
      max_response_bytes: 8192,
      interval: '30s',
      timeout: '3s',
      failure_threshold: 3,
      success_threshold: 1,
    })
    expect(json.health.application).not.toHaveProperty('path')
  })

  it.each([
    /Minecraft \(Paper/,
    /SteamCMD Server/,
    /Node\.js/,
    /Guardian deaktivieren/,
  ])('creates a Guardian-valid preset for %s', (presetName) => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Editor öffnen' }))
    fireEvent.click(screen.getByRole('button', { name: /Autopilot/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Vorlage laden...' }))
    fireEvent.click(screen.getByRole('option', { name: presetName }))
    fireEvent.click(screen.getByRole('button', { name: /Prüfen/ }))

    const draft = JSON.parse(screen.getByText(/"version": 1/).textContent ?? '{}') as BlueprintDraft
    const guardianIssues = validateBlueprintDraft(draft).filter(issue =>
      /^(health|logs|diagnostics|recovery|backups)/.test(issue.path),
    )
    expect(guardianIssues).toEqual([])
  })

  it('removes all Guardian blocks on opt-out and restores complete safe defaults on re-enable', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Editor öffnen' }))
    fireEvent.click(screen.getByRole('button', { name: /Autopilot/ }))
    const toggle = screen.getByRole('checkbox', { name: /Guardian für dieses Blueprint aktivieren/ })

    fireEvent.click(toggle)
    fireEvent.click(screen.getByRole('button', { name: /Prüfen/ }))
    let json = JSON.parse(screen.getByText(/"version": 1/).textContent ?? '{}')
    for (const key of ['health', 'logs', 'diagnostics', 'recovery', 'backups']) {
      expect(json).not.toHaveProperty(key)
    }

    fireEvent.click(screen.getByRole('button', { name: /Autopilot/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /Guardian für dieses Blueprint aktivieren/ }))
    fireEvent.click(screen.getByRole('button', { name: /Prüfen/ }))
    json = JSON.parse(screen.getByText(/"version": 1/).textContent ?? '{}')
    expect(json.health.process).toMatchObject({ id: 'process', interval: '15s', required_for_verification: true })
    expect(json.recovery).toMatchObject({
      max_attempts: 3,
      verification: { required_consecutive_successes: 3 },
    })
    expect(validateBlueprintDraft(json)).not.toEqual(expect.arrayContaining([
      expect.objectContaining({ path: expect.stringMatching(/^(health|logs|diagnostics|recovery|backups)/) }),
    ]))
  })
})
