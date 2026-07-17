import { api } from '@/api/client'

export const OAUTH_PRESETS = [
  'google',
  'discord',
  'github',
  'microsoft',
  'twitter',
  'custom_oidc',
  'custom_oauth2',
] as const

export type OAuthPreset = (typeof OAUTH_PRESETS)[number]

export interface OAuthProvider {
  id: number
  slug: string
  name: string
  preset: string
  enabled: boolean
  client_id: string
  client_secret: string
  issuer: string | null
  authorization_endpoint: string | null
  token_endpoint: string | null
  userinfo_endpoint: string | null
  scope: string | null
  claims_mapping_json: string | null
  position: number
  redirect_uri?: string
  created_at: string
  updated_at: string
}

export interface OAuthProviderPublic {
  slug: string
  name: string
  preset: string
  position: number
}

export interface OAuthSwitches {
  allow_registration: boolean
  allow_linking: boolean
  require_verified_email: boolean
}

export interface OAuthTestResult {
  ok: boolean
  message: string
}

export interface OAuthUserLink {
  id: number
  provider_id: number
  provider_slug: string
  provider_name: string
  provider_preset: string
  created_at: string
  last_used_at: string | null
}

export interface OAuthProviderCreate {
  slug: string
  name: string
  preset: string
  enabled?: boolean
  client_id: string
  client_secret?: string | null
  issuer?: string | null
  authorization_endpoint?: string | null
  token_endpoint?: string | null
  userinfo_endpoint?: string | null
  scope?: string | null
  claims_mapping_json?: string | null
  position?: number
}

export type OAuthProviderUpdate = Partial<OAuthProviderCreate>

export const oauthApi = {
  listPublicProviders: () =>
    api<OAuthProviderPublic[]>('/oauth/public/providers'),

  listProviders: () => api<OAuthProvider[]>('/oauth/providers'),
  getProvider: (id: number) => api<OAuthProvider>(`/oauth/providers/${id}`),
  createProvider: (body: OAuthProviderCreate) =>
    api<OAuthProvider>('/oauth/providers', { method: 'POST', body: JSON.stringify(body) }),
  updateProvider: (id: number, body: OAuthProviderUpdate) =>
    api<OAuthProvider>(`/oauth/providers/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteProvider: (id: number) =>
    api<{ message: string }>(`/oauth/providers/${id}`, { method: 'DELETE' }),
  updateSecret: (id: number, client_secret: string) =>
    api<{ message: string }>(`/oauth/providers/${id}/secret`, {
      method: 'POST',
      body: JSON.stringify({ client_secret }),
    }),
  testProvider: (id: number) =>
    api<OAuthTestResult>(`/oauth/providers/${id}/test`, { method: 'POST' }),

  getSwitches: () => api<OAuthSwitches>('/oauth/switches'),
  updateSwitches: (body: Partial<OAuthSwitches>) =>
    api<OAuthSwitches>('/oauth/switches', { method: 'PATCH', body: JSON.stringify(body) }),

  listMyLinks: () => api<OAuthUserLink[]>('/oauth/me/links'),
  unlinkProvider: (providerId: number) =>
    api<{ message: string }>(`/oauth/me/links/${providerId}`, { method: 'DELETE' }),
}
