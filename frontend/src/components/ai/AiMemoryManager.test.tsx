import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi, type AiMemoryEntry } from '@/api/ai'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiMemoryManager } from './AiMemoryManager'

vi.mock('@/api/ai', () => ({
  aiApi: {
    listMemory: vi.fn(),
    getMemoryPreference: vi.fn(),
    setMemoryPreference: vi.fn(),
    saveMemory: vi.fn(),
    deleteMemory: vi.fn(),
  },
}))

vi.mock('@/stores/confirmStore', () => ({ confirm: vi.fn().mockResolvedValue(true) }))

const entry: AiMemoryEntry = {
  id: '00000000-0000-0000-0000-000000000101',
  scope: 'user',
  server_id: null,
  key: 'response.language',
  value: 'Synthetic test preference',
  created_at: '2026-08-01T12:00:00Z',
  updated_at: '2026-08-01T12:00:00Z',
}

describe('AiMemoryManager', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    usePermissionsStore.setState({
      me: { is_owner: false, role_id: null, role_name: null, global_keys: ['ai.memory.use'], server_keys: {} },
      isLoading: false,
      error: null,
    })
    vi.mocked(aiApi.listMemory).mockReset().mockResolvedValue([entry])
    vi.mocked(aiApi.getMemoryPreference).mockReset().mockResolvedValue({ enabled: true })
    vi.mocked(aiApi.setMemoryPreference).mockReset().mockResolvedValue({ enabled: false })
    vi.mocked(aiApi.saveMemory).mockReset().mockResolvedValue(entry)
  })

  it('loads explicit entries and persists the opt-out without exposing hidden values', async () => {
    render(<AiMemoryManager />)

    expect(await screen.findByText('Synthetic test preference')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('switch', { name: 'Memory im KI-Kontext verwenden' }))
    await waitFor(() => expect(aiApi.setMemoryPreference).toHaveBeenCalledWith(false))

    fireEvent.change(screen.getByLabelText('Schlüssel, z. B. response.language'), { target: { value: 'answer.format' } })
    fireEvent.change(screen.getByLabelText('Präferenz'), { target: { value: 'Use concise synthetic output' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))
    await waitFor(() => expect(aiApi.saveMemory).toHaveBeenCalledWith({
      scope: 'user', key: 'answer.format', value: 'Use concise synthetic output',
    }))
  })

  it('renders nothing without the memory permission', () => {
    usePermissionsStore.setState({
      me: { is_owner: false, role_id: null, role_name: null, global_keys: [], server_keys: {} },
      isLoading: false,
      error: null,
    })
    const { container } = render(<AiMemoryManager />)
    expect(container).toBeEmptyDOMElement()
    expect(aiApi.listMemory).not.toHaveBeenCalled()
  })
})
