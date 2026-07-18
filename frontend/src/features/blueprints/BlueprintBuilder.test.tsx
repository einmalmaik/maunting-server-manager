import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { BlueprintBuilder } from './BlueprintBuilder'

function Harness({ mode = 'create' as const }) {
  const [open, setOpen] = useState(false)
  return <><button type="button" onClick={() => setOpen(true)}>Editor öffnen</button>{open && <BlueprintBuilder mode={mode} entries={[]} onClose={() => setOpen(false)} onSaved={vi.fn().mockResolvedValue(undefined)} />}</>
}

describe('BlueprintBuilder accessibility', () => {
  it('announces the create-mode safety rule and exposes native package selects', async () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Editor öffnen' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAccessibleDescription(/niemals stillschweigend ersetzt/i)
    expect(screen.getByRole('combobox', { name: /Kategorie/i })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: /Editor schließen/i })).toHaveFocus())
  })

  it('closes on Escape and returns focus to the opener', async () => {
    render(<Harness />)
    const opener = screen.getByRole('button', { name: 'Editor öffnen' })
    opener.focus()
    fireEvent.click(opener)
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(opener).toHaveFocus())
  })

  it('uses keyboard-operable native dropdowns and links review errors back to a section', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: 'Editor öffnen' }))
    const category = screen.getByRole('combobox', { name: /Kategorie/i })
    fireEvent.change(category, { target: { value: 'bot' } })
    expect(category).toHaveValue('bot')
    fireEvent.click(screen.getByRole('button', { name: /Prüfen/i }))
    fireEvent.click(screen.getByRole('button', { name: /meta\.id/i }))
    expect(screen.getByRole('button', { name: /Grundlagen/i })).toHaveAttribute('aria-current', 'step')
  })
})
