import { api } from './client'
import type {
  MePermissions,
  PermissionCatalog,
  Role,
  RoleCreate,
  RoleUpdate,
  ServerPermissionsResponse,
} from '@/types/permissions'

export const rbacApi = {
  catalog: () => api<PermissionCatalog>('/permissions/catalog'),
  me: () => api<MePermissions>('/permissions/me'),

  listRoles: () => api<Role[]>('/roles'),
  getRole: (id: number) => api<Role>(`/roles/${id}`),
  createRole: (body: RoleCreate) =>
    api<Role>('/roles', { method: 'POST', body: JSON.stringify(body) }),
  updateRole: (id: number, body: RoleUpdate) =>
    api<Role>(`/roles/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteRole: (id: number) =>
    api<void>(`/roles/${id}`, { method: 'DELETE' }),

  assignRole: (userId: number, roleId: number | null) =>
    api<void>(`/admin/users/${userId}/role`, {
      method: 'PATCH',
      body: JSON.stringify({ role_id: roleId }),
    }),

  getServerPermissions: (userId: number, serverId: number) =>
    api<ServerPermissionsResponse>(
      `/admin/users/${userId}/server-permissions/${serverId}`,
    ),
  setServerPermissions: (userId: number, serverId: number, permissions: string[]) =>
    api<ServerPermissionsResponse>(
      `/admin/users/${userId}/server-permissions/${serverId}`,
      { method: 'PUT', body: JSON.stringify({ permissions }) },
    ),
  revokeServerPermissions: (userId: number, serverId: number) =>
    api<void>(`/admin/users/${userId}/server-permissions/${serverId}`, {
      method: 'DELETE',
    }),
  listServerPermissionsForUser: (userId: number) =>
    api<ServerPermissionsResponse[]>(`/admin/users/${userId}/server-permissions`),
}
