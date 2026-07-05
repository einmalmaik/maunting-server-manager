import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { useConfirmStore } from '@/stores/confirmStore'
import { confirm as confirmImpl } from '@/stores/confirmStore'
import { PanelBackups } from './PanelBackups'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/stores/confirmStore', async () => {
  const actual = await vi.importActual<typeof import('@/stores/confirmStore')>('@/stores/confirmStore')
  return {
    ...actual,
    confirm: vi.fn(() => Promise.resolve(true)),
  }
})

function mockApi(handler: (path: string, opts?: any) => any) {
  vi.mocked(client.api).mockImplementation(async (path: string, opts?: any) => handler(path, opts))
}

function renderPage() {
  return render(
    <MemoryRouter>
      <PanelBackups />
    </MemoryRouter>,
  )
}

const DEFAULT_SETTINGS = {
  enabled: false,
  interval_hours: 24,
  retention_count: 7,
}

function baseBackup(over: Partial<any> = {}) {
  return {
    id: 1,
    name: null,
    size_mb: 128,
    db_type: 'postgresql',
    encrypted: false,
    s3_status: 'local',
    created_at: '2026-07-01T12:00:00Z',
    ...over,
  }
}

describe('PanelBackups', () => {
  beforeEach(async () => {
    vi.mocked(client.api).mockReset()
    vi.mocked(confirmImpl).mockClear()
    await i18n.changeLanguage('de')
    useToastStore.setState({ toasts: [] })
    useConfirmStore.setState({ pending: null })
  })

  it('renders title, subtitle and create button in German', async () => {
    mockApi((p) => {
      if (p === '/panel-backups') return []
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    renderPage()
    expect(await screen.findByText('Panel-Backups')).toBeInTheDocument()
    expect(screen.getByText('Sicherungen des MSM-Panels (Datenbank + Konfiguration)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Panel-Backup erstellen/ })).toBeInTheDocument()
  })

  it('shows German empty state when no backups exist', async () => {
    mockApi((p) => {
      if (p === '/panel-backups') return []
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    renderPage()
    expect(await screen.findByText('Keine Panel-Backups vorhanden')).toBeInTheDocument()
    expect(screen.getByText('Erstelle dein erstes Panel-Backup.')).toBeInTheDocument()
  })

  it('renders backup list with date, size and S3 status icon', async () => {
    mockApi((p) => {
      if (p === '/panel-backups') return [
        baseBackup({ id: 1, s3_status: 'cloud', encrypted: true, size_mb: 256 }),
        baseBackup({ id: 2, s3_status: 'local', encrypted: false, size_mb: 64, created_at: '2026-07-02T08:30:00Z' }),
      ]
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    renderPage()
    // Two rows each show size
    expect(await screen.findByText('256 MB')).toBeInTheDocument()
    expect(screen.getByText('64 MB')).toBeInTheDocument()
    // Cloud tooltip (cloud) and local tooltip (local)
    expect(screen.getByTitle('In S3-Cloud gespeichert (verschlüsselt)')).toBeInTheDocument()
    expect(screen.getByTitle('Nur lokal gespeichert')).toBeInTheDocument()
  })

  it('create button issues POST /panel-backups with loading state and success toast', async () => {
    const calls: any[] = []
    mockApi((p, opts) => {
      calls.push({ p, opts })
      if (p === '/panel-backups' && opts?.method === 'POST') return { id: 3, name: null, size_mb: 10, db_type: 'postgresql', encrypted: false, created_at: '2026-07-03T00:00:00Z' }
      if (p === '/panel-backups') return []
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    renderPage()
    await screen.findByText('Keine Panel-Backups vorhanden')

    fireEvent.click(screen.getByRole('button', { name: /Panel-Backup erstellen/ }))
    await waitFor(() => {
      expect(calls.some((c) => c.p === '/panel-backups' && c.opts?.method === 'POST')).toBe(true)
    })
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success' && t.message.includes('Panel-Backup erfolgreich erstellt'))).toBe(true)
    })
  })

  it('delete button opens confirmation dialog and issues DELETE on confirm', async () => {
    const calls: any[] = []
    mockApi((p, opts) => {
      calls.push({ p, opts })
      if (p === '/panel-backups') return [baseBackup({ id: 1 })]
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      if (p.startsWith('/panel-backups/1') && opts?.method === 'DELETE') return { deleted: true, id: 1 }
      return undefined
    })
    renderPage()
    await screen.findByText('128 MB')

    fireEvent.click(screen.getByTitle('Löschen'))
    await waitFor(() => {
      expect(vi.mocked(confirmImpl)).toHaveBeenCalledWith(expect.objectContaining({
        message: 'Panel-Backup wirklich löschen? Diese Aktion kann nicht rückgängig gemacht werden.',
        danger: true,
      }))
    })
    await waitFor(() => {
      expect(calls.some((c) => c.p === '/panel-backups/1' && c.opts?.method === 'DELETE')).toBe(true)
    })
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success' && t.message.includes('Panel-Backup gelöscht'))).toBe(true)
    })
  })

  it('delete does not call API when confirmation is cancelled', async () => {
    vi.mocked(confirmImpl).mockResolvedValueOnce(false)
    const calls: any[] = []
    mockApi((p, opts) => {
      calls.push({ p, opts })
      if (p === '/panel-backups') return [baseBackup({ id: 1 })]
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    renderPage()
    await screen.findByText('128 MB')

    fireEvent.click(screen.getByTitle('Löschen'))
    await waitFor(() => {
      expect(vi.mocked(confirmImpl)).toHaveBeenCalled()
    })
    // No DELETE call
    expect(calls.some((c) => c.opts?.method === 'DELETE')).toBe(false)
  })

  it('settings section renders enabled toggle, interval selector and retention input', async () => {
    mockApi((p) => {
      if (p === '/panel-backups') return []
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    renderPage()
    await screen.findByText('Keine Panel-Backups vorhanden')

    fireEvent.click(screen.getByRole('button', { name: /Einstellungen/ }))
    expect(await screen.findByText('Panel-Backup-Einstellungen')).toBeInTheDocument()
    expect(screen.getByText('Automatische Backups aktivieren')).toBeInTheDocument()
    expect(screen.getByText('Intervall')).toBeInTheDocument()
    expect(screen.getByText('Aufbewahrung (Anzahl)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Speichern/ })).toBeInTheDocument()
  })

  it('save settings issues PATCH /panel-backups/settings and shows success toast', async () => {
    const calls: any[] = []
    mockApi((p, opts) => {
      calls.push({ p, opts })
      if (p === '/panel-backups') return []
      if (p === '/panel-backups/settings' && opts?.method === 'PATCH') return { enabled: true, interval_hours: 12, retention_count: 5 }
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    renderPage()
    await screen.findByText('Keine Panel-Backups vorhanden')
    fireEvent.click(screen.getByRole('button', { name: /Einstellungen/ }))
    await screen.findByText('Panel-Backup-Einstellungen')

    // Toggle enabled on by clicking the checkbox label input
    const checkbox = screen.getByRole('checkbox')
    fireEvent.click(checkbox)

    fireEvent.click(screen.getByRole('button', { name: /Speichern/ }))
    await waitFor(() => {
      expect(calls.some((c) => c.p === '/panel-backups/settings' && c.opts?.method === 'PATCH')).toBe(true)
    })
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success' && t.message.includes('Einstellungen gespeichert'))).toBe(true)
    })
  })

  it('loads backups and settings on mount', async () => {
    const calls: any[] = []
    mockApi((p) => {
      calls.push(p)
      if (p === '/panel-backups') return [baseBackup()]
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    renderPage()
    await waitFor(() => {
      expect(calls).toContain('/panel-backups')
      expect(calls).toContain('/panel-backups/settings')
    })
  })

  it('uses msm-* Design-DNA classes and no raw hex colors', async () => {
    mockApi((p) => {
      if (p === '/panel-backups') return [baseBackup()]
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    const { container } = renderPage()
    await screen.findByText('128 MB')
    expect(container.querySelector('.msm-card')).not.toBeNull()
    expect(container.querySelectorAll('.msm-btn-primary').length).toBeGreaterThan(0)
    // No raw hex color overrides in inline styles
    const withStyle = container.querySelectorAll('[style]')
    withStyle.forEach((el) => {
      const style = (el as HTMLElement).getAttribute('style') || ''
      expect(style.toLowerCase()).not.toMatch(/#[0-9a-f]{3,8}/)
    })
  })

  it('shows German umlauts correctly in confirmDelete', async () => {
    mockApi((p) => {
      if (p === '/panel-backups') return [baseBackup()]
      if (p === '/panel-backups/settings') return DEFAULT_SETTINGS
      return undefined
    })
    renderPage()
    await screen.findByText('128 MB')
    fireEvent.click(screen.getByTitle('Löschen'))
    await waitFor(() => {
      const msg = vi.mocked(confirmImpl).mock.calls[0][0].message
      expect(msg).toContain('löschen')
      expect(msg).toContain('rückgängig')
    })
  })
})
