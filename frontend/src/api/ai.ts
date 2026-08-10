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
  operator_key_available: boolean
  available: boolean
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
  /**
   * Die Rueckfrage, die diese Nachricht gestellt hat.
   *
   * Sie haengt an der Nachricht und nicht an einem fluechtigen Ereignis:
   * frueher lebte sie nur im SSE-Strom, war nach einem Neuladen weg, und die
   * KI sah ihre eigene Frage im Verlauf nicht mehr — auf eine Antwort folgte
   * dieselbe Frage erneut.
   */
  question: AiQuestion | null
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
  /**
   * Bei `read_skill` und `learn_skill` gesetzt. Ohne diese Felder stünde im
   * Verlauf nur „read_skill" — der Betreiber will aber sehen, *welche*
   * erlernte Vorgehensweise gegriffen hat, sonst wirkt eine daraus entstandene
   * Antwort wie geraten.
   */
  skill_key?: string | null
  skill_name?: string | null
  /** „pending" heißt: gelernt, aber bis zur Freigabe des Betreibers wirkungslos. */
  skill_status?: string | null
  skill_learned?: boolean
}

/** Nur der Zustand — der Suchschluessel verlaesst das Backend nie. */
export interface AiWebSearchStatus {
  configured: boolean
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
  /**
   * Der Lauf, der auf diesen Vorschlag wartet.
   *
   * Nach dem Bestaetigen haengt sich der Chat daran — sonst waere die Aktion
   * ausgefuehrt und die KI stumm, und man muesste eine neue Nachricht
   * schreiben, damit es weitergeht. Genau die Beschwerde aus dem Betrieb.
   */
  run_id: string | null
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
  scope: 'user' | 'server' | 'team' | 'panel'
  server_id: number | null
  team_id: number | null
  key: string
  value: string
  /** "user" = selbst hinterlegt, "ai" = von der KI gemerkt. */
  origin: 'user' | 'ai'
  use_count: number
  last_used_at: string | null
  created_at: string
  updated_at: string
}

export interface AiMemoryPreference {
  enabled: boolean
  /**
   * Ob der Hinweis vor der naechsten Nachricht gezeigt werden soll. Die
   * 24-Stunden-Regel entscheidet das Backend — sonst muesste jeder Client sie
   * nachbauen, und zwei Nachbauten weichen irgendwann voneinander ab.
   */
  notice_due: boolean
  notice_hidden: boolean
}

/**
 * Ein Skill ist Text, kein Programm.
 *
 * `scope` sagt, woher er kommt: `shipped` liegt als Datei im Repo und kommt mit
 * jedem MSM-Update, `global` gilt panelweit, `team` nur im jeweiligen Team.
 * Mitgelieferte Skills sind nicht änderbar — aber überschreibbar, indem man
 * einen globalen mit demselben Schlüssel anlegt.
 */
export interface AiSkillSummary {
  /** Nur bei Datenbank-Skills gesetzt; mitgelieferte haben keine Zeile. */
  id: string | null
  skill_key: string
  name: string
  description: string
  scope: 'shipped' | 'global' | 'team'
  origin: 'shipped' | 'operator' | 'ai'
  team_id: number | null
  status: 'active' | 'pending'
  enabled: boolean
  editable: boolean
}

export interface AiSkillDetail extends AiSkillSummary {
  body: string
}

/**
 * Ob und wie die KI panelweit gültige Skills anlegen darf.
 *
 * `off` = nur der Betreiber. `review` = Personal sofort, Kundengespräche in die
 * Warteschlange. `instant` = jedes Gespräch wirkt sofort panelweit.
 */
export interface AiLearningPolicy {
  policy: 'off' | 'review' | 'instant'
  pending_count: number
}

/** Eine Datenbankzeile in der Verwaltung — auch abgeschaltete und wartende. */
export interface AiSkillManaged {
  id: string
  skill_key: string
  name: string
  description: string
  body: string
  scope: 'global' | 'team'
  origin: 'operator' | 'ai'
  team_id: number | null
  status: 'active' | 'pending'
  enabled: boolean
  created_by: number | null
  created_at: string
  updated_at: string
}

/**
 * Eine Rückfrage der KI mit anklickbaren Vorschlägen.
 *
 * Sie beendet den Zug: das Modell wartet auf die nächste Nachricht. Ein Klick
 * schickt die Beschriftung als ganz normale Benutzernachricht — kein
 * Sonderweg, kein zweiter Zustand im Backend.
 */
export interface AiQuestion {
  question: string
  options: Array<{ label: string; hint: string | null }>
}

export interface AiAttachment {
  id: string
  conversation_id: string
  original_name: string
  media_type: string
  size_bytes: number
  status: 'quarantined' | 'ready' | 'rejected'
  rejection_code: string | null
  /**
   * Die Nachricht, mit der dieser Anhang abgeschickt wurde.
   *
   * `null` heißt: hochgeladen, aber noch nicht gesendet. Genau daran hängt die
   * Darstellung — Ungesendetes steht als Chip über dem Eingabefeld, alles
   * andere in seiner Nachricht.
   */
  message_id: string | null
  /** Wie viele Stellen beim Aufnehmen unkenntlich gemacht wurden. */
  redacted_spans: number | null
  created_at: string
}

export type AiStreamEvent =
  | {
      event: 'message'
      data: {
        message_id: string
        request_id: string
        /** Die Kennung der Benutzernachricht — ersetzt die optimistisch vergebene. */
        user_message_id?: string | null
      }
    }
  | { event: 'delta'; data: { content: string } }
  // Denkschritte. Eigenes Ereignis, damit die Oberflaeche sie einklappen kann
  // und niemand sie fuer die Antwort haelt.
  | { event: 'reasoning'; data: { content: string } }
  // Ein gerade ausgefuehrtes Lesewerkzeug — macht sichtbar, worauf die Antwort
  // beruht.
  | { event: 'tool'; data: AiToolUse }
  // Rückfrage mit Vorschlägen. Beendet den Zug — ab hier ist der Mensch dran.
  | { event: 'question'; data: AiQuestion }
  // Der aeltere Teil des Verlaufs wurde zu einer Zusammenfassung gefaltet.
  | { event: 'compacted'; data: { conversation_id: string } }
  | { event: 'proposal'; data: AiActionProposal }
  // Eine bereits ausgefuehrte autonome Aktion. Bewusst ein eigenes Ereignis:
  // sie ist keine Anfrage an den Benutzer, sondern eine Meldung.
  | { event: 'action'; data: AiActionProposal }
  | { event: 'done'; data: { message_id: string; replayed?: boolean } }
  | { event: 'error'; data: { code: string; message_key: string } }
  // Der vollstaendige Stand eines Laufs beim Anhaengen. Kommt immer zuerst.
  //
  // Ohne ihn saehe jemand, der sich spaeter anhaengt — nach einem Seitenwechsel,
  // nach einem Neustart des Browsers —, nur den Rest der Antwort. Der Client
  // **ersetzt** damit seinen Stand, er haengt ihn nicht an.
  | { event: 'snapshot'; data: AiRunSnapshot }
  // Der Lauf beginnt eine neue Nachricht (Fortsetzung nach einer Bestaetigung).
  // Der Text davor gehoert zur abgeschlossenen Nachricht und darf nicht
  // weiterwachsen.
  | { event: 'segment'; data: { run_id: string } }
  // Zustandswechsel des Laufs: laeuft, wartet, fertig.
  | { event: 'run'; data: { run_id: string; status: AiRunStatus; stop_reason?: string | null; live?: boolean } }

export type AiRunStatus =
  | 'running'
  | 'waiting_confirmation'
  | 'waiting_user'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface AiRunSnapshot {
  run_id: string
  status: AiRunStatus
  message_id: string | null
  content: string
  reasoning: string
  tools: AiToolUse[]
  question: AiQuestion | null
  proposals: AiActionProposal[]
  stop_reason: string | null
}

export interface AiRunInfo {
  id: string
  status: AiRunStatus
  stop_reason: string | null
  message_id: string | null
  live: boolean
  created_at: string
}

const STREAM_EVENTS = [
  'message', 'delta', 'reasoning', 'tool', 'question', 'compacted',
  'proposal', 'action', 'done', 'error', 'snapshot', 'segment', 'run',
]

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
  testProvider: (id: number) => api<AiProviderTestResult>(`/ai/settings/providers/${id}/test`, {
    method: 'POST',
  }),
  getWebSearchStatus: () => api<AiWebSearchStatus>('/ai/settings/web-search'),
  /** Leerer Schluessel entfernt ihn — dann verschwindet auch das Werkzeug. */
  setWebSearchKey: (apiKey: string) => api<AiWebSearchStatus>('/ai/settings/web-search', {
    method: 'PUT',
    body: JSON.stringify({ api_key: apiKey || null }),
  }),
  /** Die eine Unterhaltung. Wird beim ersten Aufruf serverseitig angelegt. */
  getConversation: () => api<AiConversationDetail>('/ai/conversation'),
  clearHistory: () => api('/ai/conversation/messages', { method: 'DELETE' }),
  /**
   * Nimmt eine eigene Nachricht zurück: sie und alles Spätere verschwinden.
   * Gesendet wird **nicht** — das übernimmt danach der gewohnte Streamweg.
   * Zwei Schritte, weil das Senden Kontingent, Anbieterwahl und Stream braucht.
   */
  editMessage: (messageId: string, content: string) => api<{ removed: number }>(
    `/ai/conversation/messages/${messageId}`,
    { method: 'PUT', body: JSON.stringify({ content }) },
  ),
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
  listMemory: (scope: AiMemoryEntry['scope'], serverId?: number, teamId?: number) => api<AiMemoryEntry[]>(
    `/ai/memory?scope=${scope}${serverId ? `&server_id=${serverId}` : ''}${teamId ? `&team_id=${teamId}` : ''}`,
  ),
  saveMemory: (payload: { scope: AiMemoryEntry['scope']; server_id?: number; team_id?: number; key: string; value: string }) => api<AiMemoryEntry>('/ai/memory', {
    method: 'PUT', body: JSON.stringify(payload),
  }),
  deleteMemory: (id: string) => api(`/ai/memory/${id}`, { method: 'DELETE' }),
  /** Leert einen ganzen Bereich und meldet, wie viele Einträge das waren. */
  clearMemory: (scope: AiMemoryEntry['scope'], teamId?: number) => api<{ removed: number }>(
    `/ai/memory?scope=${scope}${teamId ? `&team_id=${teamId}` : ''}`, { method: 'DELETE' },
  ),
  getMemoryPreference: () => api<AiMemoryPreference>('/ai/memory/preference'),
  setMemoryPreference: (enabled: boolean) => api<AiMemoryPreference>('/ai/memory/preference', {
    method: 'PUT', body: JSON.stringify({ enabled }),
  }),
  /**
   * Antwort auf den Hinweis vor der ersten Nachricht. Bewusst getrennt von
   * `setMemoryPreference`: ein "Nein" ist hier keine Einstellung, sondern eine
   * Terminverschiebung — es setzt nur den Zeitpunkt, ab dem wieder gefragt wird.
   */
  answerMemoryNotice: (enable: boolean, hideFuture: boolean) => api<AiMemoryPreference>('/ai/memory/notice', {
    method: 'POST', body: JSON.stringify({ enable, hide_future: hideFuture }),
  }),
  /** Das Verzeichnis ohne Texte — dasselbe, das auch die KI im Prompt sieht. */
  listSkills: () => api<AiSkillSummary[]>('/ai/skills'),
  listManagedSkills: () => api<AiSkillManaged[]>('/ai/skills/manage'),
  listPendingSkills: () => api<AiSkillManaged[]>('/ai/skills/pending'),
  readSkill: (skillKey: string) => api<AiSkillDetail>(`/ai/skills/${encodeURIComponent(skillKey)}`),
  saveSkill: (payload: {
    skill_key: string; name: string; description: string; body: string
    team_id: number | null; enabled: boolean
  }) => api<AiSkillManaged>('/ai/skills', { method: 'PUT', body: JSON.stringify(payload) }),
  toggleSkill: (skillId: string, enabled: boolean) => api<AiSkillManaged>(`/ai/skills/${skillId}/enabled`, {
    method: 'PUT', body: JSON.stringify({ enabled }),
  }),
  approveSkill: (skillId: string) => api<AiSkillManaged>(`/ai/skills/${skillId}/approve`, {
    method: 'POST',
  }),
  deleteSkill: (skillId: string) => api(`/ai/skills/${skillId}`, { method: 'DELETE' }),
  getLearningPolicy: () => api<AiLearningPolicy>('/ai/settings/learning'),
  setLearningPolicy: (policy: AiLearningPolicy['policy']) => api<AiLearningPolicy>('/ai/settings/learning', {
    method: 'PUT', body: JSON.stringify({ policy }),
  }),
  listAttachments: () => api<AiAttachment[]>('/ai/conversation/attachments'),
  uploadAttachment: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return api<AiAttachment>('/ai/conversation/attachments', { method: 'POST', body })
  },
  deleteAttachment: (id: string) => api(`/ai/attachments/${id}`, { method: 'DELETE' }),
  /** Laeuft gerade noch etwas von vorhin? `null`, wenn nicht. */
  getActiveRun: () => api<AiRunInfo | null>('/ai/conversation/run'),
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
  await leseStrom(response, onEvent)
}

/**
 * Haengt sich an einen bereits laufenden Lauf an.
 *
 * Der Gegenpart zu "der Lauf haengt an nichts": wer die Seite verlassen hat
 * oder den Browser neu gestartet hat, findet die Arbeit hier wieder. Der Lauf
 * schickt zuerst einen `snapshot` mit allem, was bisher passiert ist, und
 * danach die Fortsetzung live.
 */
export async function attachAiRun(
  runId: string,
  onEvent: (event: AiStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiStream(`/ai/conversation/run/${runId}/stream`, {
    method: 'GET',
    signal,
  })
  await leseStrom(response, onEvent)
}

async function leseStrom(
  response: Response,
  onEvent: (event: AiStreamEvent) => void,
): Promise<void> {
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
