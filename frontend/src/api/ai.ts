import { api, apiStream } from './client'

export interface AiProviderAdmin {
  id: number
  name: string
  /** Schluessel aus der Anbieterliste, z. B. "openrouter". */
  provider_kind: string
  /** Abgeleitet aus dem Anbieter — nur zur Anzeige, nicht editierbar. */
  base_url: string
  default_model: string
  enabled: boolean
  requires_api_key: boolean
  operator_key_configured: boolean
  operator_key_hint: string | null
  /**
   * Rückfallpreis je 1 Mio. Tokens, in US-Cent-Microunits (1 Cent = 10.000).
   *
   * Nur noch Rückfallebene: normalerweise wird gebucht, was der Anbieter
   * meldet. `null` = keine Preisquelle, Kosten bleiben 0. Die Oberfläche zeigt
   * hier eine Dezimalzahl in der Anzeigewährung (`utils/geld.ts`); in ganzen
   * Cent ließ sich „1,20 €" nicht eintragen.
   */
  token_price_micro_usd_per_million: number | null
  updated_at: string
}

export interface AiProviderAvailable {
  id: number
  name: string
  default_model: string
  requires_api_key: boolean
  operator_key_available: boolean
  available: boolean
  /** Ob bei diesem Modell ueberhaupt nachgedacht werden kann. */
  reasoning: boolean
  /**
   * Die waehlbaren Denkstufen, flach nach tief — **bereits auf die Rolle des
   * Benutzers geklemmt**. Leer heisst nicht "denkt nicht", sondern "kennt
   * keine Stufen": gemessen koennen 145 der 272 denkenden Modelle bei
   * OpenRouter nur an oder aus.
   */
  efforts: string[]
  /** Ob "aus" eine gueltige Wahl ist. Bei 82 der 402 Modelle nicht. */
  can_disable: boolean
  /** Was gilt, wenn nichts gewaehlt wird. */
  default_effort: string | null
}

/** Ein von MSM unterstuetzter Anbieter — die Auswahl im Einrichtungsformular. */
export interface AiProviderKind {
  kind: string
  label: string
  base_url: string
  key_url: string
  key_prefix: string | null
}

/** Ein Modell aus dem Katalog des Anbieters, mit seinen Denkfaehigkeiten. */
export interface AiCatalogModel {
  model_id: string
  name: string
  reasoning: boolean
  efforts: string[]
  default_effort: string | null
  mandatory: boolean
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

/**
 * Die Schreibwerkzeuge — vollstaendig, in der Reihenfolge von
 * `ai_tool_registry.WERKZEUGE`.
 *
 * Hier standen fuenf der elf. Das war kein harmloser Auslassungsfehler: was der
 * Typ nicht kennt, faellt beim Uebersetzen der Oberflaeche durch, und
 * `propose_server_delete` erschien im Bestaetigungsdialog deshalb als roher
 * Schluessel `ai.actions.confirm.propose_server_delete` — an genau der Stelle,
 * an der stehen muss, was gleich unwiderruflich passiert.
 *
 * Wer hier etwas ergaenzt, ergaenzt auch `ai.actions.tools.*` und
 * `ai.actions.confirm.*` in **allen** Sprachdateien und prueft, ob das Werkzeug
 * in `UNUMKEHRBAR` gehoert (AiActionProposalCard).
 */
export type AiWriteTool =
  | 'propose_server_lifecycle'
  | 'propose_backup'
  | 'propose_backup_restore'
  | 'propose_config_update'
  | 'propose_config_patch'
  | 'propose_mod_install'
  | 'propose_bind_ip_update'
  | 'propose_server_create'
  | 'propose_server_delete'
  | 'propose_blueprint_change'
  | 'propose_server_blueprint_switch'

/**
 * Ein Aktionsvorschlag — genau ein Vertrag für beide Wege.
 *
 * REST (`listActions`, `getAction`, `executeAction`) und das SSE-Ereignis
 * `proposal`/`action` liefern dasselbe Objekt aus derselben Serialisierung
 * (`ai_proposal_service.proposal_response`). Das ist keine Kosmetik: der Chat
 * **ersetzt** beim Wiederanhängen den Vorschlag aus der Liste durch den aus dem
 * Ereignis. Trägt das Ereignis weniger Felder, verschwinden Begründung und
 * erwartete Wirkung aus einer bereits gerenderten Karte — ausgerechnet auf der,
 * mit der ein Schreibvorgang freigegeben wird.
 */
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
  /**
   * `server` ist die **eigene** Notiz zu einer Anlage, `server_shared` das
   * Betriebswissen der Anlage selbst — sichtbar für jeden, der sie sehen darf,
   * und es überlebt den Kollegen, der es aufgeschrieben hat.
   */
  scope: 'user' | 'server' | 'server_shared' | 'team' | 'panel'
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

/**
 * Wie voll der Kontext dieses Gesprächs ist — die Zahlen hinter dem Ring.
 *
 * `known` trennt „kleines Fenster" von „über das Modell ist nichts bekannt".
 * Im zweiten Fall zeigt der Ring ausdrücklich keinen Prozentwert: ein
 * geschätzter sähe aus wie ein gemessener, und man würde ihm glauben.
 *
 * Alle Zahlen sind Schätzungen (vier Zeichen je Token). Für „noch viel Platz"
 * gegen „gleich wird zusammengefasst" reicht das — die Oberfläche sagt deshalb
 * „etwa".
 */
export interface AiContextStatus {
  known: boolean
  /** Das volle Fenster des Modells. `null`, wenn unbekannt. */
  window_tokens: number | null
  /** Was die Eingabe davon füllen darf; Antwort und Sicherheit sind ab. */
  usable_tokens: number
  /** Belegt. Darf `usable_tokens` überschreiten — dann wird gleich gefaltet. */
  used_tokens: number
  compaction_at_tokens: number
  compaction_percent: number
  summarized: boolean
}

/** Die panelweite Faltmarke, plus die Grenzen, die der Server zulässt. */
export interface AiContextPolicy {
  compaction_percent: number
  min_percent: number
  max_percent: number
}

/**
 * Wie Beträge angezeigt werden. Nicht, wie sie gebucht werden.
 *
 * Gebucht wird ausnahmslos in US-Cent-Microunits. Diese beiden Angaben machen
 * daraus einen Betrag in der Währung des Betreibers — angewandt genau einmal,
 * in `utils/geld.ts`.
 */
export interface AiCostPolicy {
  currency: string
  usd_rate: string
  available_currencies: string[]
  min_rate: string
  max_rate: string
}

/**
 * Der Verbrauch eines Benutzers in denselben Zeiträumen wie die Grenzen.
 *
 * Kosten kommen in **US-Cent-Microunits** (1 Cent = 10.000) — der Einheit, in
 * der auch gebucht wird. Hier standen einmal aufgerundete Cent; für eine
 * Monatssumme war das harmlos, für eine einzelne Anfrage nicht. Gerundet wird
 * jetzt erst beim Anzeigen, mit `betragFormatieren`.
 */
export interface AiUsageEntry {
  user_id: number
  username: string
  tokens_today: number
  tokens_week: number
  tokens_month: number
  cost_month_micro_usd: number
  requests_month: number
  /** Letzte Anfrage im ausgewerteten Zeitraum, nicht die letzte überhaupt. */
  last_request_at: string | null
}

/** Alle Benutzer mit Verbrauch. Wer nichts verbraucht hat, fehlt. */
export interface AiUsageOverview {
  entries: AiUsageEntry[]
  total_tokens_month: number
  total_cost_month_micro_usd: number
  cost_policy: AiCostPolicy
}

/**
 * Eine einzelne Anfrage, so wie der Anbieter sie gemeldet hat.
 *
 * Der Nachweis hinter den Summen — und die einzige Ansicht, mit der sich „das
 * kann nicht stimmen" prüfen lässt: Zeile für Zeile gegen das Dashboard des
 * Anbieters, mit `cost_source` als Auskunft, ob überhaupt gemessen wurde.
 */
export interface AiUsageEvent {
  id: number
  created_at: string
  user_id: number
  username: string
  model: string | null
  /** Die verbuchte Gesamtzahl — die, an der die Kontingente hängen. */
  tokens: number
  prompt_tokens: number | null
  completion_tokens: number | null
  /** Teilmenge von `prompt_tokens`. Wer sie addiert, zählt doppelt. */
  cached_tokens: number | null
  reasoning_tokens: number | null
  /** Eine Chatnachricht ist nicht eine Anfrage: jede Werkzeugrunde ruft neu. */
  provider_requests: number | null
  cost_micro_usd: number
  /** `null` bei Zeilen aus der Zeit vor der Aufschlüsselung. */
  cost_source: 'provider' | 'estimate' | 'none' | null
}

export interface AiUsageEvents {
  entries: AiUsageEvent[]
  has_more: boolean
  cost_policy: AiCostPolicy
}

/** Der eigene Verbrauch — mit den Grenzen daneben, gegen die er läuft. */
export interface AiUsageMine extends AiUsageEntry {
  cost_policy: AiCostPolicy
  limits: {
    daily_token_limit: number | null
    weekly_token_limit: number | null
    monthly_token_limit: number | null
    requests_per_minute: number | null
    concurrent_operations: number | null
    monthly_cost_limit_cents: number | null
    role_ids: number[]
  }
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

/**
 * Der erlaubte Zustandsvorrat eines Laufs — **eine** Liste, aus der sowohl der
 * Typ als auch die Frage „ruht der?" abgeleitet wird.
 *
 * Der Vorrat gehört dem Backend: `ai_runs` hält ihn als CheckConstraint
 * (backend/models/ai_run.py). Im Frontend stand er zuletzt dreimal — einmal als
 * Typ hier und zweimal als handgeschriebene Liste der ruhenden Zustände, in
 * AiChat und in AiRunNotice. Käme ein weiterer ruhender Zustand dazu, müsste er
 * an zwei unverbundenen Stellen nachgetragen werden; wer nur eine fände, hätte
 * entweder eine dauerhaft gesperrte Eingabe (AiChat gibt sie erst frei, wenn
 * der Lauf ruht) oder eine Glocke, die für diesen Lauf nie läutet
 * (AiRunNotice).
 */
export const AI_LAUFZUSTAENDE = [
  'running',
  'waiting_confirmation',
  'waiting_user',
  'completed',
  'failed',
  'cancelled',
] as const

export type AiRunStatus = (typeof AI_LAUFZUSTAENDE)[number]

/**
 * Zustände, in denen der Lauf nichts mehr von selbst tut.
 *
 * Bewusst abgeleitet statt aufgezählt: „ruht" heißt genau „arbeitet nicht".
 * Ein neuer Zustand oben ist damit automatisch ein ruhender, und niemand muss
 * daran denken.
 */
export const AI_RUHENDE_LAUFZUSTAENDE: readonly AiRunStatus[] =
  AI_LAUFZUSTAENDE.filter((zustand) => zustand !== 'running')

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
  provider_kind: string
  default_model: string
  enabled: boolean
  requires_api_key: boolean
  token_price_micro_usd_per_million?: number | null
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
  listProviderKinds: () => api<AiProviderKind[]>('/ai/settings/provider-kinds'),
  /** Die Modelle eines Anbieters. `refresh` umgeht den Zwischenspeicher. */
  listCatalogModels: (kind: string, refresh = false) => api<AiCatalogModel[]>(
    `/ai/settings/provider-kinds/${encodeURIComponent(kind)}/models${refresh ? '?refresh=true' : ''}`,
  ),
  testProvider: (id: number) => api<AiProviderTestResult>(`/ai/settings/providers/${id}/test`, {
    method: 'POST',
  }),
  /**
   * Der eigene Verbrauch. Ohne Sonderrecht — wer von der KI wegen des
   * Kontingents abgewiesen wird, muss nachsehen können, woran es lag.
   */
  getMyUsage: () => api<AiUsageMine>('/ai/usage/me'),
  /** Alle Benutzer. Verlangt `ai.usage.read.all`. */
  getUsageOverview: () => api<AiUsageOverview>('/ai/usage'),
  /** Die eigenen Anfragen einzeln — der Nachweis hinter den eigenen Summen. */
  getMyUsageEvents: (limit = 50, offset = 0) =>
    api<AiUsageEvents>(`/ai/usage/me/events?limit=${limit}&offset=${offset}`),
  /** Alle Anfragen einzeln. Verlangt `ai.usage.read.all`. */
  getUsageEvents: (limit = 50, offset = 0) =>
    api<AiUsageEvents>(`/ai/usage/events?limit=${limit}&offset=${offset}`),
  getCostPolicy: () => api<AiCostPolicy>('/ai/settings/cost'),
  /** `usdRate` darf bei USD fehlen — dort gibt es keinen Kurs. */
  setCostPolicy: (currency: string, usdRate: string | null) =>
    api<AiCostPolicy>('/ai/settings/cost', {
      method: 'PUT',
      body: JSON.stringify({ currency, usd_rate: usdRate }),
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
  /**
   * Freigabe anlegen oder ändern. Es gibt bewusst kein Löschen: abgeschaltet
   * wird über `enabled: false` (AiAutonomyButton), damit die einmal erteilte
   * Freigabe samt Stundenlimit sichtbar bleibt, statt spurlos zu verschwinden.
   */
  saveAutonomyGrant: (payload: { server_id: number | null; enabled: boolean; max_actions_per_hour: number }) =>
    api<AiAutonomyGrant>('/ai/autonomy', { method: 'PUT', body: JSON.stringify(payload) }),
  listMemory: (scope: AiMemoryEntry['scope'], serverId?: number, teamId?: number) => api<AiMemoryEntry[]>(
    `/ai/memory?scope=${scope}${serverId ? `&server_id=${serverId}` : ''}${teamId ? `&team_id=${teamId}` : ''}`,
  ),
  /**
   * Alles, was einem selbst gehört: persönlich **und** serverbezogen.
   *
   * `listMemory('server', …)` verlangt eine konkrete Server-ID — wer alle seine
   * Notizen sehen wollte, hätte die Server raten müssen. Genau deshalb waren
   * sie in der Oberfläche nicht auffindbar, obwohl die KI sie schreibt.
   */
  listPersonalMemory: () => api<AiMemoryEntry[]>('/ai/memory/personal'),
  saveMemory: (payload: { scope: AiMemoryEntry['scope']; server_id?: number; team_id?: number; key: string; value: string }) => api<AiMemoryEntry>('/ai/memory', {
    method: 'PUT', body: JSON.stringify(payload),
  }),
  deleteMemory: (id: string) => api(`/ai/memory/${id}`, { method: 'DELETE' }),
  /**
   * Leert einen ganzen Bereich und meldet, wie viele Einträge das waren.
   *
   * `serverId` fehlte hier als einzigem der Memory-Aufrufe. Ohne ihn löst das
   * Backend `scope=server_shared` gar nicht erst auf und antwortet 404 — der
   * Knopf „alles löschen" wäre am Serverreiter tot gewesen.
   */
  clearMemory: (scope: AiMemoryEntry['scope'], teamId?: number, serverId?: number) => api<{ removed: number }>(
    `/ai/memory?scope=${scope}${teamId ? `&team_id=${teamId}` : ''}${serverId ? `&server_id=${serverId}` : ''}`,
    { method: 'DELETE' },
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
  getContextPolicy: () => api<AiContextPolicy>('/ai/settings/context'),
  setContextPolicy: (percent: number) => api<AiContextPolicy>('/ai/settings/context', {
    method: 'PUT', body: JSON.stringify({ compaction_percent: percent }),
  }),
  /**
   * Der Provider steht im Query, weil die Frage schon vor der ersten Nachricht
   * beantwortet sein muss — und weil ein Modellwechsel die Antwort sofort
   * ändert, ohne dass jemand etwas gesendet hätte.
   */
  getContextStatus: (providerId: number) => api<AiContextStatus>(
    `/ai/conversation/context?provider_id=${providerId}`,
  ),
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
  payload: {
    content: string
    provider_id: number
    request_id: string
    reasoning: boolean
    /** Die gewuenschte Denkstufe. Der Server klemmt sie auf Modell und Rolle. */
    reasoning_effort?: string | null
  },
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
