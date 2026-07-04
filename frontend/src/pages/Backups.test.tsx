import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { useConfirmStore } from '@/stores/confirmStore'
import { confirm as confirmImpl } from '@/stores/confirmStore'
import { Backups } from './Backups'

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

function renderBackups(serverId = 1) {
  return render(
    <MemoryRouter>
      <Backups serverId={serverId} />
    </MemoryRouter>,
  )
}

const STATUS_RESPONSE = {
  s3_configured: true,
  backup_password_set: true,
  last_panel_backup: null,
}

const STATUS_NOT_CONFIGURED = {
  s3_configured: false,
  backup_password_set: false,
  last_panel_backup: null,
}

const INACTIVE_STATUS = {
  active: false,
  operation: null,
  started_at: null,
  estimated_size_mb: null,
}

const DEFAULT_SETTINGS = {
  backup_on_start: false,
  backup_interval_hours: 0,
  backup_retention_count: 3,
}

function baseBackup(over: Partial<any> = {}) {
  return {
    id: 1,
    server_id: 1,
    name: null,
    filename: '/tmp/server_1_20260101.tar.gz',
    size_mb: 42,
    created_at: '2026-01-01T12:00:00Z',
    expires_at: null,
    s3_key: null,
    s3_bucket: null,
    encrypted: false,
    local_exists: true,
    ...over,
  }
}

describe('Backups — S3 Cloud Features', () => {
  beforeEach(async () => {
    vi.mocked(client.api).mockReset()
    vi.mocked(confirmImpl).mockClear()
    vi.mocked(confirmImpl).mockReturnValue(Promise.resolve(true))
    await i18n.changeLanguage('de')
    useToastStore.setState({ toasts: [] })
    useConfirmStore.setState({ pending: null })
  })

  it('shows S3 status badge "S3: Aktiv" when S3 is configured', async () => {
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return []
      return undefined
    })
    renderBackups()
    expect(await screen.findByText('S3: Aktiv')).toBeInTheDocument()
  })

  it('shows S3 status badge "S3: Nicht konfiguriert" when S3 not configured', async () => {
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_NOT_CONFIGURED
      if (p === '/backups/1') return []
      return undefined
    })
    renderBackups()
    expect(await screen.findByText('S3: Nicht konfiguriert')).toBeInTheDocument()
  })

  it('hides S3 status badge when status endpoint fails (non-admin)', async () => {
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') throw new Error('Forbidden')
      if (p === '/backups/1') return []
      return undefined
    })
    renderBackups()
    await waitFor(() => expect(vi.mocked(client.api)).toHaveBeenCalledWith('/backup-config/status'))
    expect(screen.queryByText('S3: Aktiv')).not.toBeInTheDocument()
    expect(screen.queryByText('S3: Nicht konfiguriert')).not.toBeInTheDocument()
  })

  it('shows filled cloud icon with German tooltip for S3-backed backup', async () => {
    const backup = baseBackup({ s3_key: 'msm-backups/servers/1/x.enc', encrypted: true })
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      return undefined
    })
    renderBackups()
    const cloudIcon = await screen.findByTitle('In S3-Cloud gespeichert (verschlüsselt)')
    expect(cloudIcon).toBeInTheDocument()
  })

  it('shows dimmed cloud icon with German tooltip for local-only backup', async () => {
    const backup = baseBackup({ s3_key: null, encrypted: false })
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      return undefined
    })
    renderBackups()
    const cloudIcon = await screen.findByTitle('Nur lokal gespeichert')
    expect(cloudIcon).toBeInTheDocument()
  })

  it('shows "In Cloud hochladen" button for local-only backup', async () => {
    const backup = baseBackup({ s3_key: null, encrypted: false, local_exists: true })
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      return undefined
    })
    renderBackups()
    expect(await screen.findByRole('button', { name: /In Cloud hochladen/ })).toBeInTheDocument()
  })

  it('hides "In Cloud hochladen" button for S3-backed backup', async () => {
    const backup = baseBackup({ s3_key: 'k', encrypted: true, local_exists: true })
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      return undefined
    })
    renderBackups()
    await waitFor(() => expect(screen.getByText('42 MB')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /In Cloud hochladen/ })).not.toBeInTheDocument()
  })

  it('posts to upload-to-cloud endpoint and shows success toast', async () => {
    const backup = baseBackup({ s3_key: null, encrypted: false, local_exists: true })
    const calls: any[] = []
    mockApi((p, opts) => {
      calls.push({ p, opts })
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      if (p.includes('/upload-to-cloud')) return { message: 'Backup in Cloud hochgeladen' }
      return undefined
    })
    renderBackups()
    const btn = await screen.findByRole('button', { name: /In Cloud hochladen/ })
    fireEvent.click(btn)
    await waitFor(() => {
      expect(calls.some((c) => c.p.includes('/upload-to-cloud') && c.opts?.method === 'POST')).toBe(true)
    })
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success' && t.message.includes('Cloud hochgeladen'))).toBe(true)
    })
  })

  it('shows "Aus Cloud wiederherstellen" when local missing but S3 available', async () => {
    const backup = baseBackup({ s3_key: 'k', encrypted: true, local_exists: false })
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      return undefined
    })
    renderBackups()
    expect(await screen.findByRole('button', { name: /Aus Cloud wiederherstellen/ })).toBeInTheDocument()
    // Regular restore button label should NOT appear (only cloud restore)
    expect(screen.queryByRole('button', { name: /^Wiederherstellen$/ })).not.toBeInTheDocument()
  })

  it('uses German confirmation dialog for restore from cloud', async () => {
    const backup = baseBackup({ s3_key: 'k', encrypted: true, local_exists: false })
    const calls: any[] = []
    mockApi((p, opts) => {
      calls.push({ p, opts })
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      if (p.includes('/restore/')) return { message: 'Backup wiederhergestellt' }
      return undefined
    })
    renderBackups()
    const btn = await screen.findByRole('button', { name: /Aus Cloud wiederherstellen/ })
    fireEvent.click(btn)
    await waitFor(() => {
      expect(vi.mocked(confirmImpl)).toHaveBeenCalledWith(
        expect.objectContaining({
          message: expect.stringContaining('Cloud'),
          danger: true,
          confirmText: expect.stringContaining('Cloud'),
        }),
      )
    })
    await waitFor(() => {
      expect(calls.some((c) => c.p.includes('/restore/') && c.opts?.method === 'POST')).toBe(true)
    })
  })

  it('preserves existing restore button for local backups', async () => {
    const backup = baseBackup({ s3_key: null, encrypted: false, local_exists: true })
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      return undefined
    })
    renderBackups()
    expect(await screen.findByRole('button', { name: /^Wiederherstellen$/ })).toBeInTheDocument()
  })

  it('preserves create, settings and delete buttons', async () => {
    const backup = baseBackup({ s3_key: null, encrypted: false, local_exists: true })
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      return undefined
    })
    renderBackups()
    expect(await screen.findByRole('button', { name: /Backup erstellen/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Einstellungen/ })).toBeInTheDocument()
    expect(screen.getByTitle('Löschen')).toBeInTheDocument()
  })

  it('uses msm-* Design-DNA classes and no raw hex colors', async () => {
    const backup = baseBackup({ s3_key: null, encrypted: false, local_exists: true })
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return [backup]
      return undefined
    })
    const { container } = renderBackups()
    await screen.findByText('42 MB')
    expect(container.querySelector('.msm-card')).not.toBeNull()
    expect(container.querySelectorAll('.msm-btn-secondary').length).toBeGreaterThan(0)
    const withStyle = container.querySelectorAll('[style]')
    withStyle.forEach((el) => {
      const style = (el as HTMLElement).getAttribute('style') || ''
      expect(style.toLowerCase()).not.toMatch(/#[0-9a-f]{3,8}/)
    })
  })

  it('badge text has no secrets', async () => {
    mockApi((p) => {
      if (p.startsWith('/backups/1/settings')) return DEFAULT_SETTINGS
      if (p.startsWith('/backups/1/status')) return INACTIVE_STATUS
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backups/1') return []
      return undefined
    })
    renderBackups()
    const badge = await screen.findByText('S3: Aktiv')
    expect(badge.textContent).not.toMatch(/access|secret|password|key/i)
  })
})
