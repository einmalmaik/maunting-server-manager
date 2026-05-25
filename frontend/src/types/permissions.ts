/** RBAC-Typen (siehe backend/services/permission_catalog.py). */

export interface PermissionDef {
  key: string
  group: 'users' | 'panel' | 'servers' | 'server' | 'system' | string
  label: string
}

export interface PermissionCatalog {
  global_permissions: PermissionDef[]
  server_permissions: PermissionDef[]
}

export interface MePermissions {
  is_owner: boolean
  role_id: number | null
  role_name: string | null
  global_keys: string[]
  /** server_id -> erlaubte Server-Keys (nur via Delegation, nicht via Rolle) */
  server_keys: Record<string, string[]>
}

export interface Role {
  id: number
  name: string
  description: string | null
  is_system: boolean
  permissions: string[]
  created_at: string
}

export interface RoleCreate {
  name: string
  description?: string | null
  permissions: string[]
}

export interface RoleUpdate {
  name?: string | null
  description?: string | null
  permissions?: string[] | null
}

export interface ServerPermissionsResponse {
  server_id: number
  permissions: string[]
}
