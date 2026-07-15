import { describe, expect, it } from 'vitest'
import { resolveRouteAccessState } from './routeAccess'
import type { MePermissions } from '@/types/permissions'

const owner: MePermissions = {
  is_owner: true,
  role_id: null,
  role_name: null,
  global_keys: [],
  server_keys: {},
}

function userWith(globalKeys: string[]): MePermissions {
  return {
    is_owner: false,
    role_id: 2,
    role_name: 'test-role',
    global_keys: globalKeys,
    server_keys: {},
  }
}

function resolve(routeKey: string, me: MePermissions | null, options = {}) {
  return resolveRouteAccessState({
    routeKey,
    me,
    isLoading: false,
    error: null,
    ...options,
  })
}

describe('resolveRouteAccessState', () => {
  it('allows public protected routes without permission data', () => {
    expect(resolve('dashboard', null)).toBe('allowed')
    expect(resolve('docs', null)).toBe('allowed')
  })

  it('allows permitted access to users, roles, settings and blueprints', () => {
    expect(resolve('users', userWith(['users.read']))).toBe('allowed')
    expect(resolve('users', userWith(['users.manage']))).toBe('allowed')
    expect(resolve('roles', userWith(['roles.manage']))).toBe('allowed')
    expect(resolve('settings', userWith(['panel.settings.read']))).toBe('allowed')
    expect(resolve('blueprints', userWith(['panel.settings.read']))).toBe('allowed')
    expect(resolve('blueprints', owner)).toBe('allowed')
  })

  it('keeps pending permission state on loading instead of treating it as forbidden', () => {
    expect(resolve('users', null, { isLoading: true })).toBe('loading')
    expect(resolve('roles', null)).toBe('loading')
  })

  it('returns forbidden instead of dashboard redirect semantics', () => {
    expect(resolve('roles', userWith(['users.read']))).toBe('forbidden')
    expect(resolve('settings', userWith([]))).toBe('forbidden')
  })

  it('allows nodes only for owners (empty required keys)', () => {
    expect(resolve('nodes', owner)).toBe('allowed')
    expect(resolve('nodes', userWith(['panel.settings.write']))).toBe('forbidden')
  })

  it('separates unknown routes and permission load errors', () => {
    expect(resolve('does-not-exist', owner)).toBe('notFound')
    expect(resolve('settings', null, { error: 'PERMISSIONS_LOAD_FAILED' })).toBe('error')
  })
})
