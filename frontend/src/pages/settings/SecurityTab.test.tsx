import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  parseRateLimitInput,
  SecurityTab,
  validateRateLimitAuth,
  validateRateLimitGlobal,
} from './SecurityTab'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'
import { useConfirmStore } from '@/stores/confirmStore'
import {
  RATE_LIMIT_AUTH_DEFAULT,
  RATE_LIMIT_AUTH_MAX,
  RATE_LIMIT_AUTH_MIN,
  RATE_LIMIT_GLOBAL_DEFAULT,
  RATE_LIMIT_GLOBAL_MAX,
  RATE_LIMIT_GLOBAL_MIN,
} from './types'

vi.mock('@/api/client', () => ({ api: vi.fn() }))

const permissions = vi.fn((key: string) => {
  if (key === 'system.secrets.rotate') return true
  if (key === 'panel.settings.read') return true
  if (key === 'panel.settings.write') return true
  return false
})
vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: (key: string) => permissions(key),
}))

describe('rate-limit validation helpers', () => {
  it('accepts boundary values and rejects out of range', () => {
    expect(validateRateLimitAuth(RATE_LIMIT_AUTH_MIN)).toBeNull()
    expect(validateRateLimitAuth(RATE_LIMIT_AUTH_MAX)).toBeNull()
    expect(validateRateLimitAuth(RATE_LIMIT_AUTH_MIN - 1)).toMatch(/zwischen/i)
    expect(validateRateLimitAuth(RATE_LIMIT_AUTH_MAX + 1)).toMatch(/zwischen/i)
    expect(validateRateLimitGlobal(RATE_LIMIT_GLOBAL_MIN)).toBeNull()
    expect(validateRateLimitGlobal(RATE_LIMIT_GLOBAL_MAX)).toBeNull()
    expect(validateRateLimitGlobal(RATE_LIMIT_GLOBAL_MIN - 1)).toMatch(/zwischen/i)
    expect(validateRateLimitGlobal(RATE_LIMIT_GLOBAL_MAX + 1)).toMatch(/zwischen/i)
  })

  it('rejects non-integers and empty parse', () => {
    expect(validateRateLimitAuth(10.5 as unknown as number)).toMatch(/ganze Zahl/i)
    expect(parseRateLimitInput('')).toBeNull()
    expect(parseRateLimitInput('abc')).toBeNull()
    expect(parseRateLimitInput('10.5')).toBeNull()
    expect(parseRateLimitInput('15')).toBe(15)
  })
})

describe('SecurityTab rate limits', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    useToastStore.setState({ toasts: [] })
    useConfirmStore.setState({ pending: null })
    permissions.mockImplementation((key: string) => {
      if (key === 'system.secrets.rotate') return true
      if (key === 'panel.settings.read') return true
      if (key === 'panel.settings.write') return true
      return false
    })
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(async (path: string) => {
      if (path === '/settings' || path.startsWith('/settings?')) {
        return {
          rate_limit_auth: RATE_LIMIT_AUTH_DEFAULT,
          rate_limit_global: RATE_LIMIT_GLOBAL_DEFAULT,
        }
      }
      return {}
    })
  })

  it('loads rate-limit values from /settings and shows help texts', async () => {
    vi.mocked(client.api).mockImplementation(async (path: string, init?: RequestInit) => {
      if (!init || !init.method || init.method === 'GET') {
        if (String(path).includes('settings')) {
          return { rate_limit_auth: 12, rate_limit_global: 250 }
        }
      }
      return {}
    })

    render(<SecurityTab />)

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith('/settings')
    })

    await waitFor(() => {
      const authInput = document.getElementById('rate-limit-auth') as HTMLInputElement
      expect(authInput).toBeTruthy()
      expect(authInput.value).toBe('12')
      const globalInput = document.getElementById('rate-limit-global') as HTMLInputElement
      expect(globalInput.value).toBe('250')
    })

    expect(
      screen.getByText(/Maximal erlaubte Login- und Passwort-Versuche pro Minute pro IP/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Maximal erlaubte API-Aufrufe pro Minute pro IP/i),
    ).toBeInTheDocument()
  })

  it('rejects out-of-range client-side and does not POST', async () => {
    render(<SecurityTab />)
    await waitFor(() => expect(document.getElementById('rate-limit-auth')).toBeTruthy())

    fireEvent.change(document.getElementById('rate-limit-auth') as HTMLInputElement, {
      target: { value: '2' },
    })

    // Clear previous GET call count; only care about POST after submit
    vi.mocked(client.api).mockClear()
    vi.mocked(client.api).mockResolvedValue({ message: 'ok' })

    fireEvent.click(screen.getByRole('button', { name: /Speichern/i }))

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => /zwischen|3|50/i.test(t.message))).toBe(true)
    })
    expect(client.api).not.toHaveBeenCalled()
  })

  it('saves only the two rate-limit fields on success', async () => {
    render(<SecurityTab />)
    await waitFor(() => expect(document.getElementById('rate-limit-auth')).toBeTruthy())

    fireEvent.change(document.getElementById('rate-limit-auth') as HTMLInputElement, {
      target: { value: '20' },
    })
    fireEvent.change(document.getElementById('rate-limit-global') as HTMLInputElement, {
      target: { value: '300' },
    })

    vi.mocked(client.api).mockClear()
    vi.mocked(client.api).mockResolvedValue({ message: 'Einstellungen gespeichert' })

    fireEvent.click(screen.getByRole('button', { name: /Speichern/i }))

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith('/settings', {
        method: 'POST',
        body: JSON.stringify({
          rate_limit_auth: 20,
          rate_limit_global: 300,
        }),
      })
    })
  })

  it('surfaces API errors via toast (no silent catch)', async () => {
    render(<SecurityTab />)
    await waitFor(() => expect(document.getElementById('rate-limit-auth')).toBeTruthy())

    vi.mocked(client.api).mockClear()
    vi.mocked(client.api).mockRejectedValue(new Error('rate_limit_auth muss zwischen 3 und 50 liegen'))

    fireEvent.change(document.getElementById('rate-limit-auth') as HTMLInputElement, {
      target: { value: '10' },
    })
    fireEvent.change(document.getElementById('rate-limit-global') as HTMLInputElement, {
      target: { value: '100' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Speichern/i }))

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => /rate_limit_auth|zwischen|3 und 50/i.test(t.message))).toBe(true)
    })
  })

  it('hides rate-limit form without panel.settings.read but keeps rotation when allowed', async () => {
    permissions.mockImplementation((key: string) => key === 'system.secrets.rotate')
    render(<SecurityTab />)
    expect(document.getElementById('rate-limit-auth')).toBeNull()
    expect(screen.getByRole('button', { name: /rotieren|rotat/i })).toBeInTheDocument()
  })

  it('hides rotation without system.secrets.rotate but shows rate limits', async () => {
    permissions.mockImplementation(
      (key: string) => key === 'panel.settings.read' || key === 'panel.settings.write',
    )
    render(<SecurityTab />)
    await waitFor(() => expect(document.getElementById('rate-limit-auth')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /Cluster-Admin|rotieren/i })).not.toBeInTheDocument()
  })
})

describe('SecurityTab rotation (regression)', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
    useToastStore.setState({ toasts: [] })
    useConfirmStore.setState({ pending: null })
    permissions.mockImplementation((key: string) => {
      if (key === 'system.secrets.rotate') return true
      if (key === 'panel.settings.read') return true
      if (key === 'panel.settings.write') return true
      return false
    })
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(async (path: string) => {
      if (String(path).includes('settings') && !String(path).includes('rotate')) {
        return {
          rate_limit_auth: RATE_LIMIT_AUTH_DEFAULT,
          rate_limit_global: RATE_LIMIT_GLOBAL_DEFAULT,
        }
      }
      return {}
    })
  })

  it('refuses rotation UI without permission', async () => {
    permissions.mockImplementation(
      (key: string) => key === 'panel.settings.read' || key === 'panel.settings.write',
    )
    render(<SecurityTab />)
    await waitFor(() => expect(document.getElementById('rate-limit-auth')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /rotat/i })).not.toBeInTheDocument()
  })

  it('posts rotate after confirm and never stores a password in the success summary', async () => {
    vi.mocked(client.api).mockImplementation(async (path: string) => {
      if (String(path).includes('rotate-admin')) {
        return {
          ok: true,
          admin_user: 'msm_admin',
          nodes_updated: [1],
          nodes_skipped: [],
        }
      }
      if (String(path).includes('settings')) {
        return {
          rate_limit_auth: RATE_LIMIT_AUTH_DEFAULT,
          rate_limit_global: RATE_LIMIT_GLOBAL_DEFAULT,
        }
      }
      return {}
    })

    render(<SecurityTab />)
    await waitFor(() => screen.getByRole('button', { name: /Cluster-Admin|rotat/i }))
    fireEvent.click(screen.getByRole('button', { name: /Cluster-Admin|rotat/i }))

    await waitFor(() => {
      expect(useConfirmStore.getState().pending).not.toBeNull()
    })
    useConfirmStore.getState().resolve(true)

    await waitFor(() => {
      expect(client.api).toHaveBeenCalledWith('/admin/managed-postgres/rotate-admin', {
        method: 'POST',
      })
    })

    await waitFor(() => {
      expect(screen.getByText(/Nodes aktualisiert: 1/i)).toBeInTheDocument()
    })
    // Kein Passwort-Feld und kein Klartext-Passwort im Summary (Rate-Limit-Inputs sind ok)
    expect(document.querySelector('input[type="password"]')).toBeNull()
    expect(document.body.textContent || '').not.toMatch(/password\s*[:=]\s*\S+/i)
    expect(document.body.textContent || '').toMatch(/nicht angezeigt|never shown/i)
  })

  it('surfaces API errors without silent failure', async () => {
    vi.mocked(client.api).mockImplementation(async (path: string) => {
      if (String(path).includes('rotate-admin')) {
        throw new Error('Node offline')
      }
      return {
        rate_limit_auth: RATE_LIMIT_AUTH_DEFAULT,
        rate_limit_global: RATE_LIMIT_GLOBAL_DEFAULT,
      }
    })
    render(<SecurityTab />)
    await waitFor(() => screen.getByRole('button', { name: /Cluster-Admin|rotat/i }))
    fireEvent.click(screen.getByRole('button', { name: /Cluster-Admin|rotat/i }))
    await waitFor(() => expect(useConfirmStore.getState().pending).not.toBeNull())
    useConfirmStore.getState().resolve(true)

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts
      expect(toasts.some((t) => /offline|fehlgeschlagen|failed/i.test(t.message))).toBe(true)
    })
  })
})
