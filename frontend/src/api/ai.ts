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
  server_id: number | null
  title: string
  created_at: string
  updated_at: string
}

export interface AiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status: 'complete' | 'streaming' | 'failed'
  provider_id: number | null
  model: string | null
  created_at: string
}

export interface AiConversationDetail extends AiConversation {
  messages: AiMessage[]
}

export interface AiActionProposal {
  id: string
  conversation_id: string
  server_id: number
  tool_name: 'propose_server_lifecycle' | 'propose_backup' | 'propose_config_update'
  preview: Record<string, unknown>
  expected_revision: string | null
  requires_confirmation: boolean
  status: 'proposed' | 'confirmed' | 'executing' | 'succeeded' | 'failed' | 'expired'
  task_id: string | null
  error_code: string | null
  created_at: string
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
  | { event: 'proposal'; data: AiActionProposal }
  | { event: 'done'; data: { message_id: string; replayed?: boolean } }
  | { event: 'error'; data: { code: string; message_key: string } }

export interface AiProviderWrite {
  name: string
  base_url: string
  default_model: string
  enabled: boolean
  requires_api_key: boolean
  allow_private_network: boolean
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
  listConversations: (serverId?: number) => api<AiConversation[]>(
    `/ai/conversations${serverId ? `?server_id=${serverId}` : ''}`,
  ),
  createConversation: (title: string, serverId?: number) => api<AiConversation>('/ai/conversations', {
    method: 'POST',
    body: JSON.stringify({ title, server_id: serverId ?? null }),
  }),
  getConversation: (id: string) => api<AiConversationDetail>(`/ai/conversations/${id}`),
  deleteConversation: (id: string) => api(`/ai/conversations/${id}`, { method: 'DELETE' }),
  listActions: (conversationId: string) => api<AiActionProposal[]>(`/ai/conversations/${conversationId}/actions`),
  getAction: (proposalId: string) => api<AiActionProposal>(`/ai/actions/${proposalId}`),
  confirmAction: (proposalId: string) => api<{ proposal_id: string; confirmation_token: string; expires_at: string }>(`/ai/actions/${proposalId}/confirm`, {
    method: 'POST',
  }),
  executeAction: (proposalId: string, confirmationToken: string) => api<{ proposal: AiActionProposal; result: Record<string, unknown> }>(`/ai/actions/${proposalId}/execute`, {
    method: 'POST',
    body: JSON.stringify({ confirmation_token: confirmationToken }),
  }),
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
  runSkill: (skillId: string, conversationId: string) => api<{ skill_id: string; version: number; read_results: Array<Record<string, unknown>>; proposals: Array<{ id: string; tool_name: string; preview: Record<string, unknown>; status: string }> }>(`/ai/skills/${skillId}/run`, {
    method: 'POST', body: JSON.stringify({ conversation_id: conversationId }),
  }),
  listAttachments: (conversationId: string) => api<AiAttachment[]>(`/ai/conversations/${conversationId}/attachments`),
  uploadAttachment: (conversationId: string, file: File) => {
    const body = new FormData()
    body.append('file', file)
    return api<AiAttachment>(`/ai/conversations/${conversationId}/attachments`, { method: 'POST', body })
  },
  deleteAttachment: (id: string) => api(`/ai/attachments/${id}`, { method: 'DELETE' }),
}

/** Liest einen fragmentierten SSE-Stream, ohne unbekannte Providerdaten auszugeben. */
export async function streamAiMessage(
  conversationId: string,
  payload: { content: string; provider_id: number; request_id: string },
  onEvent: (event: AiStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiStream(`/ai/conversations/${conversationId}/messages/stream`, {
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
    if (!data || typeof data !== 'object' || !['message', 'delta', 'proposal', 'done', 'error'].includes(eventName)) {
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
