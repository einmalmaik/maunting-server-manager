/**
 * Zugangsdaten auf Benutzer- und Serverebene (Phase 7).
 *
 * Es gibt bewusst keinen Lesepfad fuer ein Geheimnis. Antworten enthalten nur
 * `secret_hint` — die letzten vier Zeichen, damit ein Benutzer seine Eintraege
 * auseinanderhalten kann.
 */
import { api } from './client'

export type CredentialKind = 'github_token' | 'steam_account'

export interface UserCredential {
  id: number
  kind: CredentialKind
  label: string
  username: string | null
  secret_hint: string | null
  updated_at: string
}

export interface UserCredentialWrite {
  kind: CredentialKind
  label: string
  username?: string | null
  secret: string
}

export interface ServerCredentialStatus {
  kind: CredentialKind
  /** Verlangt der Blueprint dieses Servers diese Art ueberhaupt? */
  required: boolean
  /** 'server' | 'env' | 'panel' | 'none' */
  source: string
  configured: boolean
  credential_id: number | null
  label: string | null
  username: string | null
  hint: string | null
}

export const credentialsApi = {
  listMine: () => api<UserCredential[]>('/credentials/me'),

  save: (payload: UserCredentialWrite) =>
    api<UserCredential>('/credentials/me', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  remove: (credentialId: number) =>
    api(`/credentials/me/${credentialId}`, { method: 'DELETE' }),

  listForServer: (serverId: number) =>
    api<ServerCredentialStatus[]>(`/servers/${serverId}/credentials`),

  bind: (serverId: number, kind: CredentialKind, credentialId: number | null) =>
    api<ServerCredentialStatus[]>(`/servers/${serverId}/credentials`, {
      method: 'PUT',
      body: JSON.stringify({ kind, credential_id: credentialId }),
    }),

  readPolicy: () => api<{ allow_panel_fallback: boolean }>('/credentials/policy'),

  updatePolicy: (allowPanelFallback: boolean) =>
    api<{ allow_panel_fallback: boolean }>('/credentials/policy', {
      method: 'PUT',
      body: JSON.stringify({ allow_panel_fallback: allowPanelFallback }),
    }),
}
