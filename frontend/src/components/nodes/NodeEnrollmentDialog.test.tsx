import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { NodeEnrollmentDialog } from './NodeEnrollmentDialog'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    api: vi.fn(),
  }
})

const command = 'curl -fsSL https://panel.example/api/nodes/install.sh | sudo bash -s -- --panel https://panel.example'

function renderDialog(onApproved = vi.fn().mockResolvedValue(undefined)) {
  return {
    onApproved,
    ...render(
      <NodeEnrollmentDialog
        onClose={vi.fn()}
        onManualSetup={vi.fn()}
        onApproved={onApproved}
      />,
    ),
  }
}

describe('NodeEnrollmentDialog', () => {
  beforeEach(async () => {
    vi.mocked(client.api).mockReset()
    await i18n.changeLanguage('de')
    useToastStore.setState({ toasts: [] })
  })

  it('shows the secret-free install command and pending enrollment', async () => {
    vi.mocked(client.api).mockImplementation(async (path) => {
      if (path === '/nodes/install-command') return { command }
      if (path === '/nodes/enrollments/pending') {
        return [{
          id: 7,
          display_code: 'MSM-4821',
          name: 'Game Node Frankfurt',
          host: 'https://203.0.113.20:9000',
          expires_at: '2026-07-16T20:00:00Z',
        }]
      }
      return undefined
    })

    renderDialog()

    expect(await screen.findByText(command)).toBeInTheDocument()
    expect(await screen.findByText('Game Node Frankfurt')).toBeInTheDocument()
    expect(screen.getByText('MSM-4821')).toBeInTheDocument()
    expect(screen.getByText('https://203.0.113.20:9000')).toBeInTheDocument()
  })

  it('approves explicitly and refreshes the node list', async () => {
    const calls: Array<{ path: string; options?: RequestInit }> = []
    vi.mocked(client.api).mockImplementation(async (path, options) => {
      calls.push({ path, options })
      if (path === '/nodes/install-command') return { command }
      if (path === '/nodes/enrollments/pending') {
        return [{
          id: 7,
          display_code: 'MSM-4821',
          name: 'Game Node Frankfurt',
          host: 'https://203.0.113.20:9000',
          expires_at: '2026-07-16T20:00:00Z',
        }]
      }
      if (path === '/nodes/enrollments/7/approve') return { id: 12 }
      return undefined
    })
    const onApproved = vi.fn().mockResolvedValue(undefined)
    renderDialog(onApproved)

    fireEvent.click(await screen.findByRole('button', { name: 'Bestätigen' }))

    await waitFor(() => {
      expect(calls).toContainEqual({
        path: '/nodes/enrollments/7/approve',
        options: { method: 'POST' },
      })
      expect(onApproved).toHaveBeenCalledTimes(1)
    })
    expect(screen.queryByText('MSM-4821')).not.toBeInTheDocument()
  })

  it('contains keyboard focus, closes on Escape, and restores the trigger focus', async () => {
    vi.mocked(client.api).mockImplementation(async (path) => {
      if (path === '/nodes/install-command') return { command }
      if (path === '/nodes/enrollments/pending') return []
      return undefined
    })

    function DialogHarness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Node verbinden</button>
          {open && (
            <NodeEnrollmentDialog
              onClose={() => setOpen(false)}
              onManualSetup={vi.fn()}
              onApproved={vi.fn().mockResolvedValue(undefined)}
            />
          )}
        </>
      )
    }

    render(<DialogHarness />)
    const trigger = screen.getByRole('button', { name: 'Node verbinden' })
    trigger.focus()
    fireEvent.click(trigger)

    const closeButton = screen.getByRole('button', { name: 'Schließen' })
    expect(closeButton).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(screen.getByRole('button', { name: 'Manuell einrichten' })).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Tab' })
    expect(closeButton).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('names the compact copy action and announces successful copying', async () => {
    vi.mocked(client.api).mockImplementation(async (path) => {
      if (path === '/nodes/install-command') return { command }
      if (path === '/nodes/enrollments/pending') return []
      return undefined
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    renderDialog()
    const copyButton = await screen.findByRole('button', { name: 'Kopieren' })
    fireEvent.click(copyButton)

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(command)
      expect(screen.getByRole('button', { name: 'Kopiert' })).toBeInTheDocument()
      expect(screen.getByRole('status')).toHaveTextContent('Kopiert')
    })
  })

  it('retries loading the installation command in place', async () => {
    let commandAttempts = 0
    vi.mocked(client.api).mockImplementation(async (path) => {
      if (path === '/nodes/install-command') {
        commandAttempts += 1
        if (commandAttempts === 1) throw new Error('temporary failure')
        return { command }
      }
      if (path === '/nodes/enrollments/pending') return []
      return undefined
    })

    renderDialog()
    fireEvent.click(await screen.findByRole('button', { name: 'Erneut versuchen' }))

    expect(await screen.findByText(command)).toBeInTheDocument()
    expect(commandAttempts).toBe(2)
  })
})
