import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useHasPermission, useIsOwner } from './useHasPermission'
import { usePermissionsStore } from '@/stores/permissionsStore'
import type { MePermissions } from '@/types/permissions'

function setMe(me: MePermissions | null) {
  usePermissionsStore.setState({ me, isLoading: false })
}

describe('useHasPermission', () => {
  beforeEach(() => {
    usePermissionsStore.setState({ me: null, isLoading: false })
  })

  it('returns false when no permissions are loaded', () => {
    const { result } = renderHook(() => useHasPermission('users.read'))
    expect(result.current).toBe(false)
  })

  it('owner bypass: any key returns true', () => {
    setMe({
      is_owner: true,
      role_id: null,
      role_name: null,
      global_keys: [],
      server_keys: {},
    })
    expect(renderHook(() => useHasPermission('users.read')).result.current).toBe(true)
    expect(renderHook(() => useHasPermission('servers.delete')).result.current).toBe(true)
    expect(renderHook(() => useHasPermission('server.files.delete', 42)).result.current).toBe(true)
  })

  it('global key via role', () => {
    setMe({
      is_owner: false,
      role_id: 5,
      role_name: 'admin',
      global_keys: ['users.read', 'servers.create'],
      server_keys: {},
    })
    expect(renderHook(() => useHasPermission('users.read')).result.current).toBe(true)
    expect(renderHook(() => useHasPermission('servers.delete')).result.current).toBe(false)
  })

  it('server-scoped via per-server delegation', () => {
    setMe({
      is_owner: false,
      role_id: 2,
      role_name: 'user',
      global_keys: [],
      server_keys: { '42': ['server.view', 'server.start'] },
    })
    expect(renderHook(() => useHasPermission('server.view', 42)).result.current).toBe(true)
    expect(renderHook(() => useHasPermission('server.stop', 42)).result.current).toBe(false)
    expect(renderHook(() => useHasPermission('server.view', 99)).result.current).toBe(false)
    // Ohne serverId fragt der Hook nur globale Keys ab.
    expect(renderHook(() => useHasPermission('server.view')).result.current).toBe(false)
  })

  it('server-scoped via blanket role grant works through global_keys', () => {
    // Wenn die Rolle pauschal `server.start` hat, taucht der Key in global_keys auf.
    setMe({
      is_owner: false,
      role_id: 1,
      role_name: 'admin',
      global_keys: ['server.start'],
      server_keys: {},
    })
    expect(renderHook(() => useHasPermission('server.start', 42)).result.current).toBe(true)
    expect(renderHook(() => useHasPermission('server.start')).result.current).toBe(true)
  })
})

describe('useIsOwner', () => {
  beforeEach(() => {
    usePermissionsStore.setState({ me: null, isLoading: false })
  })

  it('false when no me', () => {
    expect(renderHook(() => useIsOwner()).result.current).toBe(false)
  })

  it('true when is_owner', () => {
    setMe({
      is_owner: true,
      role_id: null,
      role_name: null,
      global_keys: [],
      server_keys: {},
    })
    expect(renderHook(() => useIsOwner()).result.current).toBe(true)
  })
})
