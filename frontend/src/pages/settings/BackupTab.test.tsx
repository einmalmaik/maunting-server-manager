import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { BackupTab } from './BackupTab'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: () => true,
}))

function mockApi(handler: (path: string, opts?: any) => any) {
  vi.mocked(client.api).mockImplementation(async (path: string, opts?: any) => handler(path, opts))
}

function renderTab() {
  return render(
    <MemoryRouter>
      <BackupTab />
    </MemoryRouter>,
  )
}

const CONFIG_RESPONSE = {
  endpoint: 's3.example.com',
  access_key: '****1234',
  secret_key: '****5678',
  bucket: 'my-bucket',
  region: 'eu-central',
}

const STATUS_RESPONSE = {
  s3_configured: true,
  backup_password_set: true,
  last_panel_backup: null,
}

describe('BackupTab', () => {
  beforeEach(async () => {
    vi.mocked(client.api).mockReset()
    await i18n.changeLanguage('de')
    useToastStore.setState({ toasts: [] })
  })

  it('renders the S3 config form with 5 labeled fields and masked secret key', async () => {
    mockApi((p) => {
      if (p === '/backup-config') return CONFIG_RESPONSE
      if (p === '/backup-config/status') return STATUS_RESPONSE
      return undefined
    })
    renderTab()

    expect(await screen.findByText('S3-Konfiguration')).toBeInTheDocument()
    expect(screen.getByLabelText(/Endpoint/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Access Key/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Secret Key/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Bucket/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Region/)).toBeInTheDocument()

    const secretInput = screen.getByLabelText(/Secret Key/)
    expect(secretInput).toHaveAttribute('type', 'password')
  })

  it('marks region as optional', async () => {
    mockApi((p) => {
      if (p === '/backup-config') return CONFIG_RESPONSE
      if (p === '/backup-config/status') return STATUS_RESPONSE
      return undefined
    })
    renderTab()
    expect(await screen.findByText(/optional/)).toBeInTheDocument()
  })

  it('loads S3 config and status on mount (masked values populated)', async () => {
    mockApi((p) => {
      if (p === '/backup-config') return CONFIG_RESPONSE
      if (p === '/backup-config/status') return STATUS_RESPONSE
      return undefined
    })
    renderTab()
    await waitFor(() => {
      expect(vi.mocked(client.api)).toHaveBeenCalledWith('/backup-config')
      expect(vi.mocked(client.api)).toHaveBeenCalledWith('/backup-config/status')
    })
    expect(await screen.findByLabelText(/Endpoint/)).toHaveValue('s3.example.com')
    expect(screen.getByLabelText(/Bucket/)).toHaveValue('my-bucket')
  })

  it('status section shows S3 configured and password set flags', async () => {
    mockApi((p) => {
      if (p === '/backup-config') return CONFIG_RESPONSE
      if (p === '/backup-config/status') return STATUS_RESPONSE
      return undefined
    })
    renderTab()
    expect(await screen.findByText('S3: Konfiguriert')).toBeInTheDocument()
    expect(screen.getByText('Backup-Passwort: Gesetzt')).toBeInTheDocument()
  })

  it('status section shows not-configured flags when false', async () => {
    mockApi((p) => {
      if (p === '/backup-config') return { ...CONFIG_RESPONSE, endpoint: '', access_key: '', secret_key: '', bucket: '', region: '' }
      if (p === '/backup-config/status') return { s3_configured: false, backup_password_set: false, last_panel_backup: null }
      return undefined
    })
    renderTab()
    expect(await screen.findByText('S3: Nicht konfiguriert')).toBeInTheDocument()
    expect(screen.getByText('Backup-Passwort: Nicht gesetzt')).toBeInTheDocument()
  })

  it('save button posts S3 config and shows German success toast', async () => {
    const calls: any[] = []
    mockApi((p, opts) => {
      calls.push({ p, opts })
      if (p === '/backup-config') return CONFIG_RESPONSE
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backup-config/s3') return { message: 'S3-Konfiguration gespeichert' }
      return undefined
    })
    renderTab()
    await screen.findByLabelText(/Endpoint/)

    fireEvent.change(screen.getByLabelText(/Bucket/), { target: { value: 'new-bucket' } })
    const s3Form = screen.getByLabelText(/Endpoint/).closest('form')!
    fireEvent.submit(s3Form)

    await waitFor(() => {
      expect(calls.some((c) => c.p === '/backup-config/s3')).toBe(true)
    })
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success' && t.message.includes('S3-Konfiguration gespeichert'))).toBe(true)
    })
  })

  it('test connection button posts to test-s3 and shows success toast', async () => {
    const calls: any[] = []
    mockApi((p, opts) => {
      calls.push({ p, opts })
      if (p === '/backup-config') return CONFIG_RESPONSE
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backup-config/test-s3') return { ok: true, message: 'Verbindung erfolgreich', bucket: 'my-bucket' }
      return undefined
    })
    renderTab()
    await screen.findByLabelText(/Endpoint/)

    const testBtn = screen.getByRole('button', { name: /Verbindung testen/ })
    fireEvent.click(testBtn)

    await waitFor(() => {
      expect(calls.some((c) => c.p === '/backup-config/test-s3' && c.opts?.method === 'POST')).toBe(true)
    })
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success' && t.message.includes('Verbindung erfolgreich'))).toBe(true)
    })
  })

  it('test connection error shows German error toast without credentials', async () => {
    mockApi((p) => {
      if (p === '/backup-config') return CONFIG_RESPONSE
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backup-config/test-s3') throw new Error('S3-Verbindungstest fehlgeschlagen')
      return undefined
    })
    renderTab()
    await screen.findByLabelText(/Endpoint/)

    const testBtn = screen.getByRole('button', { name: /Verbindung testen/ })
    fireEvent.click(testBtn)

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => t.type === 'error' && t.message.includes('S3-Verbindungstest fehlgeschlagen'))).toBe(true)
      // Keine Credentials in den Toasts
      expect(toasts.every((t) => !t.message.includes('****1234') && !t.message.includes('secret'))).toBe(true)
    })
  })

  it('password form posts to /backup-config/password and clears input', async () => {
    const calls: any[] = []
    mockApi((p, opts) => {
      calls.push({ p, opts })
      if (p === '/backup-config') return CONFIG_RESPONSE
      if (p === '/backup-config/status') return STATUS_RESPONSE
      if (p === '/backup-config/password') return { message: 'Backup-Passwort gespeichert' }
      return undefined
    })
    renderTab()
    await screen.findByLabelText(/Endpoint/)

    const pwInput = screen.getByLabelText(/Neues Backup-Passwort/)
    fireEvent.change(pwInput, { target: { value: 'supersecret' } })
    const pwSaveBtn = screen.getByRole('button', { name: /Passwort speichern/ })
    fireEvent.click(pwSaveBtn)

    await waitFor(() => {
      expect(calls.some((c) => c.p === '/backup-config/password')).toBe(true)
    })
    await waitFor(() => {
      expect((screen.getByLabelText(/Neues Backup-Passwort/) as HTMLInputElement).value).toBe('')
    })
    await waitFor(() => {
      expect(useToastStore.getState().toasts.some((t) => t.type === 'success' && t.message.includes('Backup-Passwort gespeichert'))).toBe(true)
    })
  })

  it('uses msm-* Design-DNA classes and no raw hex colors', async () => {
    mockApi((p) => {
      if (p === '/backup-config') return CONFIG_RESPONSE
      if (p === '/backup-config/status') return STATUS_RESPONSE
      return undefined
    })
    const { container } = renderTab()
    await screen.findByText('S3-Konfiguration')
    expect(container.querySelector('.msm-card')).not.toBeNull()
    expect(container.querySelectorAll('.msm-input').length).toBeGreaterThan(0)
    // No raw hex color overrides in inline styles
    const withStyle = container.querySelectorAll('[style]')
    withStyle.forEach((el) => {
      const style = (el as HTMLElement).getAttribute('style') || ''
      expect(style.toLowerCase()).not.toMatch(/#[0-9a-f]{3,8}/)
    })
  })
})
