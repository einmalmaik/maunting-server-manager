import { api, apiStream } from './client'

export interface AiProviderAdmin {
  id: number
  name: string
  base_url: string
  default_model: string
  enabled: boolean
  requires_api_key: boolean
  allow_private_network: boolean
  operator_key_configured: boolean
  operator_key_hint: string | null
  /** Preis in Cent je 1 Mio. Tokens. null = keine Preisquelle, Kosten bleiben 0. */
  token_price_cents_per_million: number | null
  updated_at: string
}

export interface AiProviderAvailable {
  id: number
  name: string
  default_model: string
  requires_api_key: boolean
  user_key_configured: boolean
  operator_key_available: boolean
  available: boolean
}

export interface AiCredentialStatus {
  provider_id: number
  configured: boolean
  key_hint: string | null
}

export interface AiConversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface AiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** Denkschritte des Modells, sofern es welche geliefert hat. */
  reasoning: string | null
  status: 'complete' | 'streaming' | 'failed'
  provider_id: number | null
  model: string | null
  created_at: string
}

export interface AiConversationDetail extends AiConversation {
  messages: AiMessage[]
}

/** Ein von der KI ausgefuehrtes Lesewerkzeug, sichtbar im Verlauf. */
export interface AiToolUse {
  tool_name: string
  server_id: number | null
}

export interface AiProviderTestResult {
  ok: boolean
  code: string | null
  detail: string | null
}

export type AiWriteTool =
  | 'propose_server_lifecycle'
  | 'propose_backup'
  | 'propose_config_update'
  | 'propose_mod_install'
  | 'propose_server_create'

export interface AiActionProposal {
  id: string
  conversation_id: string
  /** Null bei einem Erstellungsvorschlag — den Server gibt es dann noch nicht. */
  server_id: number | null
  tool_name: AiWriteTool
  preview: Record<string, unknown>
  expected_revision: string | null
  requires_confirmation: boolean
  /** True heisst: kein Mensch hat zugestimmt. Muss sichtbar anders aussehen. */
  autonomous: boolean
  reason: string | null
  expected_effect: string | null
  status: 'proposed' | 'confirmed' | 'executing' | 'succeeded' | 'failed' | 'expired'
  task_id: string | null
  error_code: string | null
  created_at: string
}

export interface AiAutonomyGrant {
  id: number
  /** Null = panelweit. */
  server_id: number | null
  enabled: boolean
  max_actions_per_hour: number
  used_last_hour: number
  created_at: string
  updated_at: string
}

export interface AiMemoryEntry {
  id: string
  scope: 'user' | 'server' | 'panel'
  server_id: number | null
  key: string
  value: string
  created_at: string
  updated_at: string
}

export interface AiSkillStep {
  tool_name: string
  arguments: Record<string, unknown>
}

export interface AiSkill {
  id: string
  skill_key: string
  version: number
  name: string
  description: string
  steps: AiSkillStep[]
  enabled: boolean
  created_by: number | null
  created_at: string
}

export interface AiAttachment {
  id: string
  conversation_id: string
  original_name: string
  media_type: string
  size_bytes: number
  status: 'quarantined' | 'ready' | 'rejected'
  rejection_code: string | null
  created_at: string
}

/** Verhindert, dass historische Skill-Versionen versehentlich auswählbar werden. */
export function latestAiSkillVersions(skills: AiSkill[]): AiSkill[] {
  const latest = new Map<string, AiSkill>()
  for (const skill of skills) {
    const current = latest.get(skill.skill_key)
    if (!current || skill.version > current.version) latest.set(skill.skill_key, skill)
  }
  return [...latest.values()]
}

export type AiStreamEvent =
  | { event: 'message'; data: { message_id: string; request_id: string } }
  | { event: 'delta'; data: { content: string } }
  // Denkschritte. Eigenes Ereignis, damit die Oberflaeche sie einklappen kann
  // und niemand sie fuer die Antwort haelt.
  | { event: 'reasoning'; data: { content: string } }
  // Ein gerade ausgefuehrtes Lesewerkzeug — macht sichtbar, worauf die Antwort
  // beruht.
  | { event: 'tool'; data: AiToolUse }
  | { event: 'proposal'; data: AiActionProposal }
  // Eine bereits ausgefuehrte autonome Aktion. Bewusst ein eigenes Ereignis:
  // sie ist keine Anfrage an den Benutzer, sondern eine Meldung.
  | { event: 'action'; data: AiActionProposal }
  | { event: 'done'; data: { message_id: string; replayed?: boolean } }
  | { event: 'error'; data: { code: string; message_key: string } }

const STREAM_EVENTS = ['message', 'delta', 'reasoning', 'tool', 'proposal', 'action', 'done', 'error']

export interface AiProviderWrite {
  name: string
  base_url: string
  default_model: string
  enabled: boolean
  requires_api_key: boolean
  allow_private_network: boolean
  token_price_cents_per_million?: number | null
  operator_api_key?: string
  clear_operator_api_key?: boolean
}

export const aiApi = {
  listProviderSettings: () => api<AiProviderAdmin[]>('/ai/settings/providers'),
  createProvider: (payload: AiProviderWrite) => api<AiProviderAdmin>('/ai/settings/providers', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  updateProvider: (id: number, payload: Partial<AiProviderWrite>) => api<AiProviderAdmin>(`/ai/settings/providers/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  }),
  deleteProvider: (id: number) => api(`/ai/settings/providers/${id}`, { method: 'DELETE' }),
  listProviders: () => api<AiProviderAvailable[]>('/ai/providers'),
  setCredential: (providerId: number, apiKey: string) => api<AiCredentialStatus>(`/ai/providers/${providerId}/credential`, {
    method: 'PUT',
    body: JSON.stringify({ api_key: apiKey }),
  }),
  deleteCredential: (providerId: number) => api(`/ai/providers/${providerId}/credential`, { method: 'DELETE' }),
  testProvider: (id: number) => api<AiProviderTestResult>(`/ai/settings/providers/${id}/test`, {
    method: 'POST',
  }),
  /** Die eine Unterhaltung. Wird beim ersten Aufruf serverseitig angelegt. */
  getConversation: () => api<AiConversationDetail>('/ai/conversation'),
  clearHistory: () => api('/ai/conversation/messages', { method: 'DELETE' }),
  listActions: () => api<AiActionProposal[]>('/ai/conversation/actions'),
  getAction: (proposalId: string) => api<AiActionProposal>(`/ai/actions/${proposalId}`),
  confirmAction: (proposalId: string) => api<{ proposal_id: string; confirmation_token: string; expires_at: string }>(`/ai/actions/${proposalId}/confirm`, {
    method: 'POST',
  }),
  executeAction: (proposalId: string, confirmationToken: string) => api<{ proposal: AiActionProposal; result: Record<string, unknown> }>(`/ai/actions/${proposalId}/execute`, {
    method: 'POST',
    body: JSON.stringify({ confirmation_token: confirmationToken }),
  }),
  listAutonomyGrants: () => api<AiAutonomyGrant[]>('/ai/autonomy'),
  saveAutonomyGrant: (payload: { server_id: number | null; enabled: boolean; max_actions_per_hour: number }) =>
    api<AiAutonomyGrant>('/ai/autonomy', { method: 'PUT', body: JSON.stringify(payload) }),
  deleteAutonomyGrant: (serverId: number | null) =>
    api(`/ai/autonomy${serverId === null ? '' : `?server_id=${serverId}`}`, { method: 'DELETE' }),
  listMemory: (scope: AiMemoryEntry['scope'], serverId?: number) => api<AiMemoryEntry[]>(`/ai/memory?scope=${scope}${serverId ? `&server_id=${serverId}` : ''}`),
  saveMemory: (payload: { scope: AiMemoryEntry['scope']; server_id?: number; key: string; value: string }) => api<AiMemoryEntry>('/ai/memory', {
    method: 'PUT', body: JSON.stringify(payload),
  }),
  deleteMemory: (id: string) => api(`/ai/memory/${id}`, { method: 'DELETE' }),
  getMemoryPreference: () => api<{ enabled: boolean }>('/ai/memory/preference'),
  setMemoryPreference: (enabled: boolean) => api<{ enabled: boolean }>('/ai/memory/preference', {
    method: 'PUT', body: JSON.stringify({ enabled }),
  }),
  listSkills: () => api<AiSkill[]>('/ai/skills'),
  listManagedSkills: () => api<AiSkill[]>('/ai/skills/manage'),
  createSkill: (payload: Omit<AiSkill, 'id' | 'version' | 'created_by' | 'created_at'> & { skill_key: string }) => api<AiSkill>('/ai/skills', {
    method: 'POST', body: JSON.stringify(payload),
  }),
  updateSkill: (skillKey: string, payload: Omit<AiSkill, 'id' | 'version' | 'created_by' | 'created_at'>) => api<AiSkill>(`/ai/skills/${skillKey}`, {
    method: 'PUT', body: JSON.stringify(payload),
  }),
  runSkill: (skillId: string, serverId: number) => api<{ skill_id: string; version: number; read_results: Array<Record<string, unknown>>; proposals: Array<{ id: string; tool_name: string; preview: Record<string, unknown>; status: string }> }>(`/ai/skills/${skillId}/run`, {
    method: 'POST', body: JSON.stringify({ server_id: serverId }),
  }),
  listAttachments: () => api<AiAttachment[]>('/ai/conversation/attachments'),
  uploadAttachment: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return api<AiAttachment>('/ai/conversation/attachments', { method: 'POST', body })
  },
  deleteAttachment: (id: string) => api(`/ai/attachments/${id}`, { method: 'DELETE' }),
}

/** Liest einen fragmentierten SSE-Stream, ohne unbekannte Providerdaten auszugeben. */
export async function streamAiMessage(
  payload: { content: string; provider_id: number; request_id: string; reasoning: boolean },
  onEvent: (event: AiStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiStream('/ai/conversation/messages/stream', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
  if (!response.body) throw new Error('AI_STREAM_UNAVAILABLE')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const consumeBlock = (block: string) => {
    let eventName = ''
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim()
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (!eventName || dataLines.length === 0) return
    let data: unknown
    try {
      data = JSON.parse(dataLines.join('\n'))
    } catch {
      throw new Error('AI_STREAM_INVALID')
    }
    if (!data || typeof data !== 'object' || !STREAM_EVENTS.includes(eventName)) {
      throw new Error('AI_STREAM_INVALID')
    }
    onEvent({ event: eventName, data } as AiStreamEvent)
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        consumeBlock(buffer.slice(0, boundary))
        buffer = buffer.slice(boundary + 2)
        boundary = buffer.indexOf('\n\n')
      }
      if (done) break
    }
    if (buffer.trim()) consumeBlock(buffer)
  } finally {
    reader.releaseLock()
  }
}
