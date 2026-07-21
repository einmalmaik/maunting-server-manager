import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ActionMenu } from './ActionMenu'

describe('ActionMenu', () => {
  it('supports menu focus, arrow navigation, and focus restoration', async () => {
    render(
      <ActionMenu
        label="Mehr"
        items={[
          { key: 'disabled', label: 'Nicht verfügbar', disabled: true, onSelect: vi.fn() },
          { key: 'rename', label: 'Umbenennen', onSelect: vi.fn() },
          { key: 'delete', label: 'Löschen', onSelect: vi.fn() },
        ]}
      />,
    )

    const trigger = screen.getByRole('button', { name: 'Mehr' })
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })

    const rename = await screen.findByRole('menuitem', { name: 'Umbenennen' })
    const remove = screen.getByRole('menuitem', { name: 'Löschen' })
    await waitFor(() => expect(rename).toHaveFocus())

    fireEvent.keyDown(document, { key: 'End' })
    expect(remove).toHaveFocus()
    fireEvent.keyDown(document, { key: 'ArrowDown' })
    expect(rename).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(trigger).toHaveFocus()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
