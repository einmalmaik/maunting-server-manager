import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi } from '@/api/ai'
import i18n from '@/i18n'
import { useConfirmStore } from '@/stores/confirmStore'
import { AiAutonomyPanel } from './AiAutonomyPanel'

vi.mock('@/api/ai', () => ({
  aiApi: {
    listAutonomyGrants: vi.fn(),
    saveAutonomyGrant: vi.fn(),
    deleteAutonomyGrant: vi.fn(),
  },
}))

const hasPermission = vi.fn()
vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => hasPermission(key),
}))

const grant = {
  id: 1,
  server_id: 7,
  enabled: true,
  max_actions_per_hour: 10,
  used_last_hour: 3,
  created_at: '2026-08-08T10:00:00Z',
  updated_at: '2026-08-08T10:00:00Z',
}

describe('AiAutonomyPanel', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    useConfirmStore.setState({ pending: null })
    hasPermission.mockReset().mockReturnValue(true)
    vi.mocked(aiApi.listAutonomyGrants).mockReset().mockResolvedValue([])
    vi.mocked(aiApi.saveAutonomyGrant).mockReset().mockResolvedValue(grant)
  })

  it('stays hidden without the permission', async () => {
    hasPermission.mockReturnValue(false)

    const { container } = render(<AiAutonomyPanel serverId={7} />)

    await waitFor(() => expect(container).toBeEmptyDOMElement())
    expect(aiApi.listAutonomyGrants).not.toHaveBeenCalled()
  })

  it('names what autonomy does not remove', async () => {
    render(<AiAutonomyPanel serverId={7} />)

    // Die Grenze muss ohne Aufklappen sichtbar sein — sie ist der wichtigste
    // Satz auf dieser Karte.
    expect(await screen.findByText(/Berechtigungen, Serversperren und Audit/)).toBeInTheDocument()
  })

  it('requires an explicit confirmation before enabling', async () => {
    render(<AiAutonomyPanel serverId={7} />)
    const toggle = await screen.findByLabelText('Aktionen ohne Rückfrage ausführen')

    fireEvent.click(toggle)

    expect(aiApi.saveAutonomyGrant).not.toHaveBeenCalled()
    expect(useConfirmStore.getState().pending?.danger).toBe(true)

    await act(async () => useConfirmStore.getState().resolve(true))

    await waitFor(() => expect(aiApi.saveAutonomyGrant).toHaveBeenCalledWith({
      server_id: 7,
      enabled: true,
      max_actions_per_hour: 10,
    }))
  })

  it('does not save when the confirmation is declined', async () => {
    render(<AiAutonomyPanel serverId={7} />)
    const toggle = await screen.findByLabelText('Aktionen ohne Rückfrage ausführen')

    fireEvent.click(toggle)
    await act(async () => useConfirmStore.getState().resolve(false))

    expect(aiApi.saveAutonomyGrant).not.toHaveBeenCalled()
  })

  it('shows the used budget so a sudden prompt is explainable', async () => {
    vi.mocked(aiApi.listAutonomyGrants).mockResolvedValue([grant])

    render(<AiAutonomyPanel serverId={7} />)

    expect(await screen.findByText(/automatisch ausgeführt: 3/)).toBeInTheDocument()
  })

  it('turning autonomy off needs no confirmation', async () => {
    vi.mocked(aiApi.listAutonomyGrants).mockResolvedValue([grant])
    vi.mocked(aiApi.saveAutonomyGrant).mockResolvedValue({ ...grant, enabled: false })
    render(<AiAutonomyPanel serverId={7} />)
    const toggle = await screen.findByLabelText('Aktionen ohne Rückfrage ausführen')

    fireEvent.click(toggle)

    await waitFor(() => expect(aiApi.saveAutonomyGrant).toHaveBeenCalledWith({
      server_id: 7,
      enabled: false,
      max_actions_per_hour: 10,
    }))
  })
})
