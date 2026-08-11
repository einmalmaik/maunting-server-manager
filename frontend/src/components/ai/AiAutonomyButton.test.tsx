import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi } from '@/api/ai'
import i18n from '@/i18n'
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

describe('AiAutonomyButton', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
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
})
