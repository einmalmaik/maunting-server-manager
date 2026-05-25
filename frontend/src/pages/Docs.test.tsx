import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Docs } from './Docs'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { useToastStore } from '@/stores/toastStore'
import type { MePermissions } from '@/types/permissions'

function renderDocs() {
  return render(
    <MemoryRouter>
      <Docs />
    </MemoryRouter>,
  )
}

function setMe(me: MePermissions | null) {
  usePermissionsStore.setState({ me, isLoading: false })
}

const ownerMe: MePermissions = {
  is_owner: true,
  role_id: null,
  role_name: null,
  global_keys: [],
  server_keys: {},
}

const readOnlyMe: MePermissions = {
  is_owner: false,
  role_id: 2,
  role_name: 'user',
  global_keys: [],
  server_keys: {},
}

describe('Docs page', () => {
  beforeEach(() => {
    i18n.changeLanguage('en')
    setMe(null)
    useToastStore.setState({ toasts: [] })
  })

  it('renders English headline and template link with correct href', async () => {
    renderDocs()
    expect(await screen.findByText('Blueprint Documentation')).toBeInTheDocument()
    const link = screen.getByTestId('docs-template-download') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/api/blueprints/template')
  })

  it('renders all expected sections', async () => {
    renderDocs()
    for (const key of [
      'intro',
      'workflow',
      'addServer',
      'addBlueprintSteam',
      'addBlueprintCustom',
      'location',
      'schema',
      'runtime',
      'ports',
      'source',
      'httpSecurity',
      'mods',
      'import',
    ]) {
      expect(screen.getByTestId(`docs-section-${key}`)).toBeInTheDocument()
    }
  })

  it('renders German headline after language switch', async () => {
    await i18n.changeLanguage('de')
    renderDocs()
    expect(await screen.findByText('Blueprint-Dokumentation')).toBeInTheDocument()
    await i18n.changeLanguage('en')
  })

  it('hides the upload button without panel.settings.write', () => {
    setMe(readOnlyMe)
    renderDocs()
    expect(screen.queryByTestId('docs-blueprint-upload')).toBeNull()
    expect(screen.queryByTestId('docs-blueprint-upload-input')).toBeNull()
  })

  it('shows the upload button for owner', () => {
    setMe(ownerMe)
    renderDocs()
    expect(screen.getByTestId('docs-blueprint-upload')).toBeInTheDocument()
    expect(screen.getByTestId('docs-blueprint-upload-input')).toBeInTheDocument()
  })

  describe('upload flow', () => {
    let fetchSpy: ReturnType<typeof vi.spyOn>

    beforeEach(() => {
      fetchSpy = vi.spyOn(global, 'fetch')
      setMe(ownerMe)
    })

    afterEach(() => {
      fetchSpy.mockRestore()
    })

    function mockJson(status: number, body: unknown) {
      return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        headers: new Headers(),
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      } as Response)
    }

    function selectFile(content: string, filename = 'test.blueprint.json') {
      const input = screen.getByTestId('docs-blueprint-upload-input') as HTMLInputElement
      const file = new File([content], filename, { type: 'application/json' })
      Object.defineProperty(input, 'files', { value: [file], configurable: true })
      fireEvent.change(input)
    }

    it('POSTs to /api/blueprints/import and shows success toast', async () => {
      fetchSpy.mockReturnValueOnce(mockJson(201, { id: 'my-blueprint', message: 'ok' }))
      renderDocs()
      selectFile(JSON.stringify({ meta: { id: 'my-blueprint' } }))

      await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
      const [url, options] = fetchSpy.mock.calls[0] as [string, RequestInit]
      expect(url).toBe('/api/blueprints/import')
      expect(options.method).toBe('POST')
      expect(options.credentials).toBe('include')
      const headers = options.headers as Record<string, string>
      expect(headers['Content-Type']).toBe('application/json')

      await waitFor(() => {
        const toasts = useToastStore.getState().toasts
        expect(toasts.some((t) => t.type === 'success' && t.message.includes('my-blueprint'))).toBe(true)
      })
    })

    it('shows an error toast for invalid JSON without calling the API', async () => {
      renderDocs()
      selectFile('this is { not json')

      await waitFor(() => {
        const toasts = useToastStore.getState().toasts
        expect(toasts.some((t) => t.type === 'error' && t.message === 'The file is not valid JSON.')).toBe(true)
      })
      expect(fetchSpy).not.toHaveBeenCalled()
    })

    it('shows the backend error message when the API rejects the blueprint', async () => {
      fetchSpy.mockReturnValueOnce(
        mockJson(400, { detail: { message: 'Blueprint-Validierung fehlgeschlagen', errors: [] } }),
      )
      renderDocs()
      selectFile(JSON.stringify({}))

      await waitFor(() => {
        const toasts = useToastStore.getState().toasts
        expect(toasts.some((t) => t.type === 'error' && t.message === 'Blueprint-Validierung fehlgeschlagen')).toBe(true)
      })
    })
  })
})
