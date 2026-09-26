/**
 * Schnittstelle des PostgreSQL-Studios (backend/routers/postgres_studio.py).
 *
 * Strukturänderungen gehen nur als Spezifikation (`StudioOperation`) raus —
 * das Backend übersetzt sie, für die Vorschau und die Ausführung gleich.
 */
import { api, apiUrl, getCsrfToken } from '@/api/client'

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }

export interface StudioPermissions {
  read: boolean
  write: boolean
  admin: boolean
}

export interface StudioSchema {
  name: string
  owner: string
  can_create: boolean
  table_count: number
}

export interface StudioOverview {
  kind: 'shared' | 'dedicated'
  server: { version_num: number; version: string; role: string; database: string; size_bytes: number }
  schemas: StudioSchema[]
  permissions: StudioPermissions
}

export type RelationKind =
  | 'table'
  | 'partitioned_table'
  | 'view'
  | 'materialized_view'
  | 'sequence'
  | 'foreign_table'

export interface StudioRelation {
  oid: number
  name: string
  kind: RelationKind
  estimated_rows: number
  size_bytes: number | null
  is_partition: boolean
  rls_enabled: boolean
  comment: string | null
  parent: string | null
}

export interface StudioSequence {
  name: string
  last_value: number | null
  start_value: number
  increment_by: number
  min_value: number
  max_value: number
  cycle: boolean
}

export interface StudioEnum {
  name: string
  values: string[]
}

export interface StudioFunction {
  oid: number
  name: string
  arguments: string
  returns: string
  language: string
  kind: string
  volatility: string
  security_definer: boolean
}

export interface StudioTrigger {
  name: string
  table?: string
  enabled: boolean
  definition: string
  function_schema?: string
  function_name?: string
}

export interface StudioObjects {
  schema: string
  relations: StudioRelation[]
  sequences: StudioSequence[]
  enums: StudioEnum[]
  functions: StudioFunction[]
  triggers: StudioTrigger[]
}

export interface StudioColumn {
  position: number
  name: string
  type: string
  nullable: boolean
  default: string | null
  identity: 'always' | 'by_default' | null
  generated: boolean
  comment: string | null
  category: string
  enum_values: string[] | null
}

export type FkAction = 'no_action' | 'restrict' | 'cascade' | 'set_null' | 'set_default'

export interface StudioConstraint {
  name: string
  type: 'primary_key' | 'foreign_key' | 'unique' | 'check' | 'exclusion' | 'trigger' | 'not_null'
  definition: string
  columns: string[]
  ref_schema: string | null
  ref_table: string | null
  ref_columns: string[]
  on_delete: FkAction | null
  on_update: FkAction | null
  deferrable: boolean
}

export interface StudioIndex {
  name: string
  definition: string
  unique: boolean
  primary: boolean
  valid: boolean
  method: string
  size_bytes: number
  scans: number | null
}

export interface StudioPolicy {
  name: string
  command: 'all' | 'select' | 'insert' | 'update' | 'delete'
  permissive: boolean
  roles: string[]
  using: string | null
  with_check: string | null
}

export interface StudioGrant {
  grantee: string
  privilege: string
  grantable: boolean
}

export interface StudioTableDetails {
  schema: string
  table: string
  oid: number
  kind: string
  rls_enabled: boolean
  rls_forced: boolean
  comment: string | null
  estimated_rows: number
  owner: string
  partition_key: string | null
  partition_bound: string | null
  view_definition: string | null
  size_bytes: number | null
  index_size_bytes: number | null
  primary_key: string[]
  columns: StudioColumn[]
  constraints: StudioConstraint[]
  referenced_by: { name: string; schema: string; table: string; columns: string[]; ref_columns: string[] }[]
  indexes: StudioIndex[]
  triggers: StudioTrigger[]
  policies: StudioPolicy[]
  partitions: { name: string; bound: string; estimated_rows: number }[]
  grants: StudioGrant[]
  stats: {
    live: number
    dead: number
    last_vacuum: string | null
    last_autovacuum: string | null
    last_analyze: string | null
    last_autoanalyze: string | null
    seq_scan: number
    idx_scan: number | null
  } | null
}

export interface StudioExtension {
  name: string
  default_version: string
  installed_version: string | null
  comment: string | null
  trusted: boolean
  superuser: boolean
  schema: string | null
  installable: boolean
}

export interface StudioRole {
  name: string
  superuser: boolean
  inherit: boolean
  createrole: boolean
  createdb: boolean
  login: boolean
  replication: boolean
  bypassrls: boolean
  connection_limit: number
  valid_until: string | null
  member_of: string[]
  managed: boolean
  system: boolean
}

export interface StudioSchemaGrants {
  schema: string
  objects: { object: string; kind: string; grantee: string; privilege: string; grantable: boolean }[]
  schema_grants: StudioGrant[]
}

export interface StudioParameter {
  name: string
  setting: string
  unit: string | null
  category: string
  description: string
  context: string
  vartype: string
  min_val: string | null
  max_val: string | null
  enumvals: string[] | null
  boot_val: string | null
  reset_val: string | null
  source: string
  pending_restart: boolean
  editable: boolean
}

export interface StudioSession {
  pid: number
  database: string
  role: string
  application_name: string
  client: string | null
  backend_start: string
  xact_start: string | null
  query_start: string | null
  state: string | null
  wait_event_type: string | null
  wait_event: string | null
  query: string
  blocked_by: number[]
  query_seconds: number | null
}

export interface StudioLock {
  pid: number
  locktype: string
  mode: string
  granted: boolean
  relation: string | null
  role: string
  database: string
  state: string | null
  query: string
  blocked_by: number[]
}

export interface StudioHealth {
  database: Record<string, number | string | null>
  cache_hit_ratio: number | null
  index_hit_ratio: number | null
  tables: {
    schema: string
    table: string
    live: number
    dead: number
    dead_ratio: number | null
    last_vacuum: string | null
    last_autovacuum: string | null
    last_analyze: string | null
    last_autoanalyze: string | null
    seq_scan: number
    idx_scan: number | null
    size_bytes: number
  }[]
  unused_indexes: { schema: string; table: string; index: string; scans: number; size_bytes: number }[]
}

export interface StudioRunResult {
  columns: string[]
  rows: Json[][]
  row_count: number
  status: string | null
  truncated: boolean
  duration_ms: number
}

export interface StudioSqlResponse {
  results: StudioRunResult[]
  notices: string[]
  duration_ms: number
  rolled_back: boolean
}

export interface StudioPlan {
  statements: string[]
  mode: 'tx' | 'autocommit'
  identity: 'owner' | 'admin'
  scope: 'database' | 'instance'
  destructive: boolean
  permission: string
}

export interface StudioExecuteResponse {
  plan: StudioPlan
  results: StudioRunResult[]
  notices: string[]
  duration_ms: number
}

export type FilterOperator =
  | 'eq' | 'neq' | 'lt' | 'lte' | 'gt' | 'gte'
  | 'like' | 'ilike' | 'not_like' | 'is_null' | 'not_null' | 'in' | 'contains'

export interface RowFilter {
  column: string
  operator: FilterOperator
  value?: Json
}

export interface RowSort {
  column: string
  descending: boolean
}

export interface StudioRows {
  columns: string[]
  types: string[]
  rows: Json[][]
  total: number
  total_is_estimate: boolean
  key: string[]
  editable: boolean
}

export interface StudioRowKey {
  values: Record<string, Json>
}

export interface PendingDump {
  database: string
  size_bytes: number
  modified: number
}

// Spezifikationen — Spiegel von backend/schemas/postgres_studio.py.
export interface ColumnSpec {
  name: string
  type: string
  nullable?: boolean
  default?: string | null
  identity?: 'always' | 'by_default' | null
  comment?: string | null
}

export interface ForeignKeySpec {
  name?: string | null
  columns: string[]
  ref_schema: string
  ref_table: string
  ref_columns: string[]
  on_delete: FkAction
  on_update: FkAction
  deferrable?: boolean
}

export type StudioOperation = { op: string } & Record<string, unknown>

export const CTID_KEY = '__msm_ctid'

export function studioApi(serverId: number, databaseId: number) {
  const base = `/servers/${serverId}/databases/${databaseId}/studio`
  const post = <T>(path: string, body: unknown) =>
    api<T>(`${base}${path}`, { method: 'POST', body: JSON.stringify(body ?? {}) })
  const q = (params: Record<string, string>) => `?${new URLSearchParams(params).toString()}`

  return {
    overview: () => api<StudioOverview>(base),
    objects: (schema: string) => api<StudioObjects>(`${base}/objects${q({ schema })}`),
    table: (schema: string, table: string) => api<StudioTableDetails>(`${base}/table${q({ schema, table })}`),
    functionDefinition: (oid: number) =>
      api<{ oid: number; schema: string; name: string; arguments: string; definition: string }>(`${base}/functions/${oid}`),
    extensions: () => api<StudioExtension[]>(`${base}/extensions`),
    roles: () => api<StudioRole[]>(`${base}/roles`),
    grants: (schema: string) => api<StudioSchemaGrants>(`${base}/grants${q({ schema })}`),
    health: () => api<StudioHealth>(`${base}/health`),
    parameters: () => api<StudioParameter[]>(`${base}/parameters`),
    sessions: () => api<StudioSession[]>(`${base}/sessions`),
    locks: () => api<StudioLock[]>(`${base}/locks`),
    sessionAction: (pid: number, action: 'cancel' | 'terminate') =>
      post<{ ok: boolean }>(`/sessions/${pid}`, { action }),
    sql: (body: { sql: string; rollback?: boolean; autocommit?: boolean; row_limit?: number }) =>
      post<StudioSqlResponse>('/sql', body),
    explain: (sql: string, analyze: boolean) =>
      post<{ plan: Json; analyzed: boolean; duration_ms: number }>('/explain', { sql, analyze }),
    preview: (operation: StudioOperation) => post<StudioPlan>('/preview', { operation }),
    execute: (operation: StudioOperation) => post<StudioExecuteResponse>('/execute', { operation }),
    rows: (body: {
      schema_name: string
      table: string
      filters?: RowFilter[]
      sort?: RowSort[]
      limit?: 25 | 50 | 100 | 500
      offset?: number
    }) => post<StudioRows>('/rows', body),
    insertRow: (schema_name: string, table: string, values: Record<string, Json>) =>
      post<{ columns: string[]; row: Json[] | null }>('/rows/insert', { schema_name, table, values }),
    updateRow: (schema_name: string, table: string, key: StudioRowKey, changes: Record<string, Json>) =>
      post<{ columns: string[]; row: Json[] | null }>('/rows/update', { schema_name, table, key, changes }),
    deleteRows: (schema_name: string, table: string, keys: StudioRowKey[]) =>
      post<{ deleted: number }>('/rows/delete', { schema_name, table, keys }),
    importRows: (schema_name: string, table: string, columns: string[], rows: Json[][]) =>
      post<{ inserted: number }>('/rows/import', { schema_name, table, columns, rows }),
    exportRows: (body: { schema_name: string; table: string; format: 'csv' | 'json' | 'sql'; filters?: RowFilter[]; sort?: RowSort[] }) =>
      download(`${base}/rows/export`, body),
    dump: (body: {
      format: 'plain' | 'custom' | 'tar'
      schema_only?: boolean
      data_only?: boolean
      schemas?: string[]
      tables?: { schema_name: string; name: string }[]
    }) => download(`${base}/backup/dump`, body),
    restore: (body: { format: 'plain' | 'custom' | 'tar'; data_b64: string; clean: boolean; confirm_name: string }) =>
      post<{ ok: boolean; database: string; duration_ms: number; sha256: string }>('/backup/restore', body),
    pending: () => api<PendingDump[]>(`${base}/backup/pending`),
    applyPending: () => post<{ ok: boolean }>('/backup/pending/apply', {}),
    discardPending: () => post<{ ok: boolean }>('/backup/pending/discard', {}),
  }
}

export type StudioApi = ReturnType<typeof studioApi>

export interface Download {
  blob: Blob
  filename: string
  headers: Headers
}

/** POST, der eine Datei liefert. Fehler kommen als lesbare Meldung zurück. */
async function download(path: string, body: unknown): Promise<Download> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const csrf = getCsrfToken()
  if (csrf) headers['X-CSRF-Token'] = csrf
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    credentials: 'include',
    headers,
    body: JSON.stringify(body ?? {}),
    cache: 'no-store',
  })
  if (!res.ok) {
    let message = res.statusText
    try {
      const data = await res.json()
      message = typeof data?.detail === 'string' ? data.detail : message
    } catch {
      /* kein JSON */
    }
    throw new Error(message)
  }
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  return { blob: await res.blob(), filename: match?.[1] || 'download', headers: res.headers }
}

export function saveDownload({ blob, filename }: Download): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export async function fileToBase64(file: File): Promise<string> {
  const buffer = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < buffer.length; i += chunk) {
    binary += String.fromCharCode(...buffer.subarray(i, i + chunk))
  }
  return btoa(binary)
}

export function formatBytes(value: number | null | undefined, locale: string): string {
  if (value == null) return '–'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toLocaleString(locale, { maximumFractionDigits: unit === 0 ? 0 : 1 })} ${units[unit]}`
}

export function cellText(value: Json | undefined): string {
  if (value === null || value === undefined) return 'NULL'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
