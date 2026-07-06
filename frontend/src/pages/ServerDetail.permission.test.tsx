import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ServerDetail } from './ServerDetail'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import type { MePermissions } from '@/types/permissions'
import type { Server, GameInfo } from '@/types'

// ---------------------------------------------------------------------------
// Mocks: keep the test focused on ServerDetail permission-gating logic.
// We do NOT mock useHasPermission or usePermissionsStore — those are the
// real code under test. We control their state directly via the store.
// ---------------------------------------------------------------------------

vi.mock('@/api/client', () => ({
  api: vi.fn(),
  SanitizedApiError: class SanitizedApiError extends Error {
    constructor(msg: string) {
      super(msg)
      this.name = 'SanitizedApiError'
    }
  },
}))

vi.mock('@/hooks/useHostInterfaces', () => ({
  useHostInterfaces: () => ({ interfaces: [], defaultBindIp: '' }),
}))

// Stub child components so the test renders ServerDetail's permission logic
// without mounting heavy tab content or the editor dialog itself.
vi.mock('./FileManager', () => ({ FileManager: () => null }))
vi.mock('./ModManager', () => ({ ModManager: () => null }))
vi.mock('./Backups', () => ({ Backups: () => null }))
vi.mock('@/components/server/ServerConsolePanel', () => ({
  ServerConsolePanel: () => null,
}))
vi.mock('@/components/server/ServerRestartPanel', () => ({
  ServerRestartPanel: () => null,
}))
vi.mock('@/components/server/AuthSetupBanner', () => ({
  AuthSetupBanner: () => null,
}))
vi.mock('@/components/server/DatabaseManager', () => ({
  DatabaseManager: () => null,
}))
vi.mock('@/components/server/OutgoingWebhooksPanel', () => ({
  OutgoingWebhooksPanel: () => null,
}))
vi.mock('@/components/server/UptimeDisplay', () => ({
  UptimeDisplay: () => null,
}))
vi.mock('@/components/server/ResourceEditorDialog', () => ({
  ResourceEditorDialog: () => null,
}))

const mockApi = vi.mocked(client.api)

// ---------------------------------------------------------------------------
// Synthetic fixtures — no real credentials, IPs, tokens, or server metadata.
// ---------------------------------------------------------------------------

const SERVER_ID = 42
const OTHER_SERVER_ID = 99

const SYNTHETIC_SERVER: Server = {
  id: SERVER_ID,
  name: 'synthetic-permission-test-server',
  game_type: 'testgame',
  status: 'stopped',
  status_message: null,
  auth_required: false,
  auto_restart: false,
  restart_interval_hours: null,
  restart_time_utc: null,
  restart_times_utc: null,
  last_auto_restart_attempt_at: null,
  last_auto_restart_completed_at: null,
  last_auto_restart_status: null,
  next_auto_restart_at: null,
  started_at: null,
  uptime_seconds: null,
  cpu_limit_percent: 100,
  ram_limit_mb: 4096,
  disk_limit_gb: 50,
  disk_usage_mb: 1024,
  game_port: 27015,
  query_port: 27016,
  rcon_port: 27017,
  public_bind_ip: '127.0.0.1',
  ports: [],
  created_at: '2025-01-01T00:00:00Z',
}

const SYNTHETIC_STATUS = {
  status: 'stopped',
  cpu_percent: null,
  ram_mb: null,
  uptime_seconds: null,
  started_at: null,
  disk_used_mb: 1024,
  disk_free_mb: 51200,
  cpu_limit_percent: 100,
  ram_limit_mb: 4096,
  disk_limit_gb: 50,
  message: null,
}

const SYNTHETIC_GAMES: GameInfo[] = [
  {
    id: 'testgame',
    name: 'Test Game',
    platform: 'linux',
    mod_support: false,
    supports_steam_workshop: false,
    ports: [],
    source: 'native',
  },
]

// ---------------------------------------------------------------------------
// Permission fixtures covering the full topology matrix
// ---------------------------------------------------------------------------

const OWNER_ME: MePermissions = {
  is_owner: true,
  role_id: null,
  role_name: null,
  global_keys: [],
  server_keys: {},
}

const GLOBAL_RESOURCE_ME: MePermissions = {
  is_owner: false,
  role_id: 2,
  role_name: 'admin',
  global_keys: ['server.resources.manage'],
  server_keys: {},
}

const TARGET_SERVER_ME: MePermissions = {
  is_owner: false,
  role_id: 3,
  role_name: 'user',
  global_keys: [],
  server_keys: { [String(SERVER_ID)]: ['server.resources.manage'] },
}

const VIEW_ONLY_ME: MePermissions = {
  is_owner: false,
  role_id: 4,
  role_name: 'viewer',
  global_keys: ['server.view'],
  server_keys: {},
}

const OTHER_SERVER_ME: MePermissions = {
  is_owner: false,
  role_id: 5,
  role_name: 'user',
  global_keys: [],
  server_keys: { [String(OTHER_SERVER_ID)]: ['server.resources.manage'] },
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mockApiImplementation() {
  mockApi.mockImplementation(async (path: string) => {
    if (path === `/servers/${SERVER_ID}`) return SYNTHETIC_SERVER as any
    if (path === `/servers/${SERVER_ID}/status`) return SYNTHETIC_STATUS as any
    if (path === '/system/games') return SYNTHETIC_GAMES as any
    return undefined as any
  })
}

/** Sets the permissions store to a specific state. */
function setPermissions(
  me: MePermissions | null,
  isLoading = false,
  error: string | null = null,
) {
  usePermissionsStore.setState({ me, isLoading, error })
}

/** Renders ServerDetail inside a router with the :id route param. */
function renderServerDetail(serverId = SERVER_ID) {
  return render(
    <MemoryRouter initialEntries={[`/servers/${serverId}`]}>
      <Routes>
        <Route path="/servers/:id" element={<ServerDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

/** Waits for the synthetic server name to appear, proving the initial fetch resolved. */
async function waitForServerToLoad() {
  await waitFor(() => {
    expect(screen.getByText('synthetic-permission-test-server')).toBeInTheDocument()
  })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ServerDetail permission topology — VAL-UI-002 / VAL-UI-018', () => {
  beforeEach(async () => {
    mockApi.mockReset()
    mockApiImplementation()
    await i18n.changeLanguage('en')
    usePermissionsStore.setState({ me: null, isLoading: false, error: null })
  })

  afterEach(() => {
    vi.useRealTimers()
    usePermissionsStore.setState({ me: null, isLoading: false, error: null })
  })

  // -------------------------------------------------------------------------
  // Allowed cases: exactly one resource edit action is rendered
  // -------------------------------------------------------------------------

  describe('allowed cases render exactly one resource edit action', () => {
    it('owner sees exactly one resource edit action', async () => {
      setPermissions(OWNER_ME)
      renderServerDetail()
      await waitForServerToLoad()

      const editButtons = screen.getAllByTestId('resource-edit-btn')
      expect(editButtons).toHaveLength(1)
    })

    it('global server.resources.manage permission sees exactly one edit action', async () => {
      setPermissions(GLOBAL_RESOURCE_ME)
      renderServerDetail()
      await waitForServerToLoad()

      const editButtons = screen.getAllByTestId('resource-edit-btn')
      expect(editButtons).toHaveLength(1)
    })

    it('target-server-specific server.resources.manage sees exactly one edit action', async () => {
      setPermissions(TARGET_SERVER_ME)
      renderServerDetail()
      await waitForServerToLoad()

      const editButtons = screen.getAllByTestId('resource-edit-btn')
      expect(editButtons).toHaveLength(1)
    })
  })

  // -------------------------------------------------------------------------
  // Disallowed cases: resource cards render but no edit action
  // -------------------------------------------------------------------------

  describe('disallowed cases render resource cards but no edit action', () => {
    it('view-only user sees resource cards but no edit action', async () => {
      setPermissions(VIEW_ONLY_ME)
      renderServerDetail()
      await waitForServerToLoad()

      // Resource section header is visible
      expect(screen.getByText(i18n.t('serverDetail.resources'))).toBeInTheDocument()
      // CPU / RAM / Disk labels are visible (cards always render)
      expect(screen.getByText('CPU')).toBeInTheDocument()
      expect(screen.getByText('RAM')).toBeInTheDocument()
      expect(screen.getByText('Disk')).toBeInTheDocument()
      // No edit button
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()
    })

    it('user with permission only on another server sees no edit action', async () => {
      setPermissions(OTHER_SERVER_ME)
      renderServerDetail()
      await waitForServerToLoad()

      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()
    })

    it('user with no me loaded (null) sees no edit action', async () => {
      setPermissions(null)
      renderServerDetail()
      await waitForServerToLoad()

      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()
    })
  })

  // -------------------------------------------------------------------------
  // No-flash: edit action never appears transiently during loading / error
  // -------------------------------------------------------------------------

  describe('no flash during permission loading and error states', () => {
    it('does not show edit action while permissions are loading', async () => {
      setPermissions(null, true) // isLoading: true, me: null
      renderServerDetail()
      await waitForServerToLoad()

      // During loading, no edit button should be visible
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()
    })

    it('does not show edit action on permission load error', async () => {
      setPermissions(null, false, 'PERMISSIONS_LOAD_FAILED')
      renderServerDetail()
      await waitForServerToLoad()

      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()
    })

    it('edit action appears only after loading completes with permission (no flash)', async () => {
      // Start in loading state — no button
      setPermissions(null, true)
      renderServerDetail()
      await waitForServerToLoad()
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()

      // Simulate loading complete with owner permission
      act(() => {
        setPermissions(OWNER_ME, false)
      })

      // Now the button should appear
      await waitFor(() => {
        expect(screen.getByTestId('resource-edit-btn')).toBeInTheDocument()
      })
    })

    it('edit action never appears when loading completes without permission', async () => {
      setPermissions(null, true)
      renderServerDetail()
      await waitForServerToLoad()
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()

      // Loading completes but user has no resource manage permission
      act(() => {
        setPermissions(VIEW_ONLY_ME, false)
      })

      // Give React a chance to re-render, then assert button is still absent
      await waitFor(() => {
        expect(screen.getByText('synthetic-permission-test-server')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()
    })

    it('edit action never appears when loading completes with error', async () => {
      setPermissions(null, true)
      renderServerDetail()
      await waitForServerToLoad()
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()

      // Loading fails with error
      act(() => {
        setPermissions(null, false, 'PERMISSIONS_LOAD_FAILED')
      })

      await waitFor(() => {
        expect(screen.getByText('synthetic-permission-test-server')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()
    })

    it('edit action does not flash when transitioning from error to allowed', async () => {
      // Start in error state — no button
      setPermissions(null, false, 'PERMISSIONS_LOAD_FAILED')
      renderServerDetail()
      await waitForServerToLoad()
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()

      // Permissions retried and loaded successfully with permission
      act(() => {
        setPermissions(OWNER_ME, false, null)
      })

      // Button appears after the transition (no flash — it was never shown during error)
      await waitFor(() => {
        expect(screen.getByTestId('resource-edit-btn')).toBeInTheDocument()
      })
    })

    it('edit action does not flash when transitioning from loading to error', async () => {
      setPermissions(null, true)
      renderServerDetail()
      await waitForServerToLoad()
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()

      // Loading transitions directly to error (no intermediate "allowed" flash)
      act(() => {
        setPermissions(null, false, 'PERMISSIONS_LOAD_FAILED')
      })

      await waitFor(() => {
        expect(screen.getByText('synthetic-permission-test-server')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('resource-edit-btn')).not.toBeInTheDocument()
    })
  })

  // -------------------------------------------------------------------------
  // Resource cards are always visible regardless of permission state
  // -------------------------------------------------------------------------

  describe('resource cards always visible regardless of permission state', () => {
    it('owner sees resource cards', async () => {
      setPermissions(OWNER_ME)
      renderServerDetail()
      await waitForServerToLoad()

      expect(screen.getByText('CPU')).toBeInTheDocument()
      expect(screen.getByText('RAM')).toBeInTheDocument()
      expect(screen.getByText('Disk')).toBeInTheDocument()
    })

    it('view-only user sees resource cards', async () => {
      setPermissions(VIEW_ONLY_ME)
      renderServerDetail()
      await waitForServerToLoad()

      expect(screen.getByText('CPU')).toBeInTheDocument()
      expect(screen.getByText('RAM')).toBeInTheDocument()
      expect(screen.getByText('Disk')).toBeInTheDocument()
    })

    it('permission-load-error user sees resource cards', async () => {
      setPermissions(null, false, 'PERMISSIONS_LOAD_FAILED')
      renderServerDetail()
      await waitForServerToLoad()

      expect(screen.getByText('CPU')).toBeInTheDocument()
      expect(screen.getByText('RAM')).toBeInTheDocument()
      expect(screen.getByText('Disk')).toBeInTheDocument()
    })

    it('permission-loading user sees resource cards', async () => {
      setPermissions(null, true)
      renderServerDetail()
      await waitForServerToLoad()

      expect(screen.getByText('CPU')).toBeInTheDocument()
      expect(screen.getByText('RAM')).toBeInTheDocument()
      expect(screen.getByText('Disk')).toBeInTheDocument()
    })
  })
})
