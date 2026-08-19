import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiSkillManaged } from '@/api/ai'
import i18n from '@/i18n'
import { confirm } from '@/stores/confirmStore'
import { AiSkillManager } from './AiSkillManager'

vi.mock('@/api/ai', () => ({
  aiApi: {
    listSkills: vi.fn(),
    listManagedSkills: vi.fn(),
    saveSkill: vi.fn(),
    deleteSkill: vi.fn(),
    toggleSkill: vi.fn(),
  },
}))
vi.mock('@/stores/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/stores/confirmStore', () => ({ confirm: vi.fn() }))

const basis: AiSkillManaged = {
  id: '1', skill_key: 'valheim-ram', name: 'Valheim-RAM', description: 'Wenn Valheim ruckelt.',
  body: 'Erst den Arbeitsspeicher prüfen.', scope: 'global', origin: 'operator', team_id: null,
  status: 'active', enabled: true, created_by: 1, fingerprint: 'fp-valheim-ram',
  created_at: '2026-08-01T12:00:00Z', updated_at: '2026-08-01T12:00:00Z',
}

/** Über den Schalter der Zeile abgeschaltet — dieser Zustand ist eine Entscheidung. */
const abgeschaltet: AiSkillManaged = { ...basis, id: '2', skill_key: 'backup', name: 'Backup', enabled: false }

const panel = { kind: 'panel', canManage: true } as const

describe('AiSkillManager', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.listManagedSkills).mockReset().mockResolvedValue([basis])
    vi.mocked(aiApi.saveSkill).mockReset().mockResolvedValue(basis)
    vi.mocked(aiApi.deleteSkill).mockReset().mockResolvedValue(undefined)
    // Die Antwort auf die Rückfrage gehört in den einzelnen Test, nicht in die
    // Attrappe: ein `confirm`, das überall zustimmt, kann nicht zeigen, dass
    // überhaupt gefragt wird.
    vi.mocked(confirm).mockReset().mockResolvedValue(true)
  })

  it('fragt vor dem Löschen und lässt es bei einem Nein', async () => {
    // Ein Skill ist die Vorgehensweise, die der Assistent für einen ganzen
    // Bereich anwendet — von Hand geschrieben, ohne Papierkorb im Backend.
    // Ohne diesen Test liesse sich die Rückfrage entfernen, ohne dass eine
    // Zeile rot wird: alle übrigen Tests stimmen ihr zu.
    vi.mocked(confirm).mockResolvedValue(false)
    render(<AiSkillManager scope={panel} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Löschen: Valheim-RAM' }))

    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Diesen Skill wirklich löschen?',
      // Rot gefragt, weil rot gemeint: die Farbe ist hier der Unterschied
      // zwischen „speichern?" und „endgültig weg?".
      danger: true,
    })))
    expect(aiApi.deleteSkill).not.toHaveBeenCalled()
  })

  it('lässt einen abgeschalteten Skill abgeschaltet, wenn nur der Text korrigiert wird', async () => {
    // Speichern ist keine Freigabe. Vorher schickte das Formular fest
    // `enabled: true`, und das Backend übernimmt den Wert ungefragt — eine
    // Tippfehlerkorrektur an der Beschreibung stellte den Skill damit wieder
    // für alle im Bereich scharf, ohne dass irgendwo stand, dass sie das tut.
    vi.mocked(aiApi.listManagedSkills).mockResolvedValue([abgeschaltet])
    render(<AiSkillManager scope={panel} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Backup' }))
    fireEvent.change(screen.getByLabelText('Beschreibung'), { target: { value: 'Backup-Routine, korrigiert.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(aiApi.saveSkill).toHaveBeenCalledWith(expect.objectContaining({
      skill_key: 'backup', description: 'Backup-Routine, korrigiert.', enabled: false,
    })))
  })

  it('nennt die Schlüsselregel und lässt bis dahin nicht speichern', async () => {
    // Sonst erfährt man sie erst aus der englischen Pydantic-Meldung des
    // Backends, und erst nachdem man Name, Beschreibung und Vorgehensweise
    // getippt hat.
    render(<AiSkillManager scope={panel} />)

    expect(await screen.findByText('Kleinbuchstaben, Ziffern, Bindestriche. Zum Beispiel valheim-ram.'))
      .toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Schlüssel'), { target: { value: 'Valheim RAM' } })
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Valheim RAM' } })
    fireEvent.change(screen.getByLabelText('Beschreibung'), { target: { value: 'Wenn es ruckelt.' } })
    fireEvent.change(screen.getByLabelText('Vorgehensweise'), { target: { value: 'Arbeitsspeicher prüfen.' } })
    // Das Leerzeichen überlebt `toLowerCase()` — das Backend nähme den Schlüssel
    // nicht an, also darf der Knopf gar nicht erst klickbar sein.
    expect(screen.getByRole('button', { name: 'Neuer Skill' })).toBeDisabled()

    fireEvent.change(screen.getByLabelText('Schlüssel'), { target: { value: 'valheim-ram-neu' } })
    expect(screen.getByRole('button', { name: 'Neuer Skill' })).toBeEnabled()
  })

  it('behält das Suchfeld, wenn die Liste unter die Schwelle schrumpft', async () => {
    // Dieselbe Sackgasse wie bei den Erinnerungen: Bedienelement weg, Filter
    // noch aktiv, und heraus käme man nur über einen Bereichswechsel.
    const vier: AiSkillManaged[] = [
      basis,
      abgeschaltet,
      { ...basis, id: '3', skill_key: 'dayz-start', name: 'DayZ-Start' },
      { ...basis, id: '4', skill_key: 'freitags', name: 'Freitags' },
    ]
    vi.mocked(aiApi.listManagedSkills)
      .mockReset()
      .mockResolvedValueOnce(vier)
      .mockResolvedValue(vier.filter((row) => row.id !== abgeschaltet.id))
    render(<AiSkillManager scope={panel} />)

    fireEvent.change(await screen.findByLabelText('Skills durchsuchen'), { target: { value: 'backup' } })
    fireEvent.click(screen.getByRole('button', { name: 'Löschen: Backup' }))
    await waitFor(() => expect(aiApi.deleteSkill).toHaveBeenCalledWith('2'))

    const feld = await screen.findByLabelText('Skills durchsuchen')
    expect(feld).toHaveValue('backup')
    fireEvent.change(feld, { target: { value: '' } })
    expect(screen.getByText('DayZ-Start')).toBeInTheDocument()
  })
})
