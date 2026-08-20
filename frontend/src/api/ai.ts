import { api, apiStream } from './client'

export interface AiProviderAdmin {
  id: number
  name: string
  /** Schluessel aus der Anbieterliste, z. B. "openrouter". */
  provider_kind: string
  /**
   * Abgeleitet aus dem Anbieter — nur zur Anzeige, nicht editierbar.
   * `null`, wenn das Panel diesen Anbieter nicht (mehr) kennt.
   */
  base_url: string | null
  default_model: string | null
  /**
   * Die Stimm-Kennung eines Stimmzugangs, aus dem Konto des Betreibers.
   *
   * `null` heisst „nichts hinterlegt" und loest sich **nicht** auf: es gibt
   * keine Standardstimme. Ohne sie gibt es ueber diesen Zugang keinen
   * Sprachmodus — eine geratene Stimme stuende auf der Rechnung des Betreibers.
   *
   * Hier stand bis zum 16.08.2026 einer von acht festen Namen. Seit die
   * Kennung aus einem fremden Konto stammt, gibt es keine Liste mehr, gegen die
   * sich pruefen liesse — und deshalb ein Textfeld statt eines Auswahlfelds.
   *
   * Bei einem Chatzugang bleibt das Feld unbeachtet.
   */
  default_voice: string | null
  /**
   * Das hoerende Modell eines Chatzugangs — was Gesprochenes zu Text macht.
   *
   * `null` heisst „nichts hinterlegt": dann gibt es ueber diesen Zugang keinen
   * Sprachmodus. Hier gehoert ein **Transkriptionsmodell** hinein —
   * `openai/gpt-transcribe`, `openai/whisper-large-v3` — und kein Chatmodell.
   * Der Ton geht an OpenRouters `/audio/transcriptions`.
   *
   * Solche Modelle stehen **nicht** in der Modellauswahl: die liest `/models`,
   * und dort fuehrt OpenRouter Chatmodelle. Deshalb ist das Feld hier ein
   * Textfeld und keine Liste.
   *
   * Bei einem Stimmzugang bleibt das Feld unbeachtet.
   */
  transcription_model: string | null
  /**
   * Das Arbeitsmodell der Worker — die zweite Hälfte der Provider-Zweiteilung
   * (docs/agentic-framework.md, §5). `null` heisst: kein Hintergrund-Betrieb
   * über diesen Zugang, es gilt der heutige Ein-Modell-Betrieb. Kein Fehler,
   * sondern der dokumentierte Fallback.
   */
  worker_model: string | null
  /**
   * Die **feste** Denkstufe der Worker. Der Betreiber wählt sie, nie der
   * Kunde und nie das Modell selbst — er zahlt die Arbeit im Hintergrund.
   * `null` heisst: nicht nachdenken.
   */
  worker_reasoning_effort: string | null
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
  /**
   * Wofür dieser Zugang taugt. `chat_completions` ist das Modell, das denkt
   * (und im Sprachmodus zusaetzlich zuhoert), `tts` die Stimme, die vorliest —
   * zwei verschiedene APIs, die sich nicht gegenseitig vertreten. Ein
   * Stimmzugang erscheint deshalb gar nicht erst in der Modellauswahl des
   * Chats.
   *
   * Das Feld entscheidet ausserdem, welches Zusatzfeld das Formular zeigt: das
   * hoerende Modell beim einen, die Stimm-Kennung beim anderen.
   */
  protokoll: 'chat_completions' | 'tts'
  /**
   * Ob der Modellkatalog dieses Anbieters den Schlüssel braucht. Wenn ja,
   * bleibt die Modelliste leer, bis der Zugang mit Schlüssel gespeichert ist —
   * das ist kein Fehler, sondern die Reihenfolge.
   */
  katalog_braucht_schluessel: boolean
}

/** Ein Modell aus dem Katalog des Anbieters, mit seinen Denkfaehigkeiten. */
export interface AiCatalogModel {
  model_id: string
  name: string
  reasoning: boolean
  efforts: string[]
  default_effort: string | null
  mandatory: boolean
  /**
   * Das von MSM empfohlene Modell — die einzige Angabe hier, die nicht vom
   * Anbieter stammt. Hoechstens eines je Katalog, und keines, wenn der Anbieter
   * die empfohlene Kennung nicht mehr fuehrt.
   */
  recommended: boolean
}

/**
 * Welches Fenster. `primary` ist der Dauerchat, in den der Mensch tippt;
 * `guardian` sammelt die Reparaturen, die eine Störung ausgelöst hat;
 * `worker` ist ein Hintergrund-Auftrag, den das Gehirn gestartet hat.
 *
 * `primary` und `guardian` sind feste Anlässe — je Benutzer genau eine
 * Unterhaltung, erzwungen über den (partiellen) `uq_ai_conversations_user_kind`.
 * Worker-Fenster gibt es dagegen **viele**: je Auftrag eines. Sie werden
 * deshalb nie über die Art geladen, sondern über ihre Kennung
 * (`getWorkerConversation`, `listWorkers`).
 */
export type AiConversationKind = 'primary' | 'guardian' | 'worker'

export interface AiConversation {
  id: string
  kind: AiConversationKind
  title: string
  created_at: string
  updated_at: string
}

export interface AiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /**
   * Denkschritte des Modells, sofern es welche geliefert hat — **die ganze
   * Antwort am Stück**.
   *
   * Gezeichnet wird aus `sections`: dort steht der Denktext je Runde an seiner
   * Stelle. Dieses Feld bleibt für Nachrichten aus der Zeit vor den
   * Denkabschnitten (dann gibt es sie nur hier) und weil es dieselbe
   * Zeichenkette ist, die serverseitig in die Berichtsmail geht.
   */
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
  /**
   * Die Gliederung dieser Antwort: Text und Werkzeuge in der Reihenfolge, in
   * der sie entstanden sind.
   *
   * Sie ersetzt `content` nicht, sie ordnet es. `content` ist der reine Text —
   * er geht an den Anbieter zurueck und wird durchsucht; die Abschnitte sind,
   * was gezeichnet wird. Aus `content` allein liesse sich nicht herstellen, an
   * welcher Stelle ein Werkzeug lief.
   *
   * `null` heisst „aus der Zeit vor dieser Spalte". Solche Nachrichten werden
   * wie immer als reiner Text dargestellt.
   */
  sections?: AiSection[] | null
  status: 'complete' | 'streaming' | 'failed'
  provider_id: number | null
  model: string | null
  created_at: string
}

/**
 * Ein Abschnitt einer Antwort — Text, ein Werkzeugaufruf oder ein Denkblock.
 *
 * Drei Formen in einem Typ statt dreier Listen, weil die **Reihenfolge
 * zwischen ihnen** die Information ist. „Ich sehe mir den Status an" —
 * Werkzeug — „der laeuft, jetzt die Logs" — Werkzeug ist etwas anderes als
 * derselbe Text mit denselben Werkzeugen davor, und genau so sah es aus,
 * solange beides getrennt gefuehrt wurde.
 *
 * `denken` kam zuletzt dazu, aus demselben Anlass: der Denktext lag als flaches
 * Feld daneben, und die Oberfläche konnte ihn deshalb nur als **einen** Kasten
 * über allem zeichnen — die Gedanken der dritten Runde standen dann über dem
 * Text der ersten. `AiMessage.reasoning` bleibt daneben, aber als Ableitung.
 */
export interface AiSection {
  art: 'text' | 'tool' | 'denken'
  inhalt?: string | null
  werkzeug?: AiToolUse | null
}

export interface AiConversationDetail extends AiConversation {
  messages: AiMessage[]
  /**
   * Gibt es ältere Nachrichten als die älteste gelieferte? Dann lädt
   * `getConversation` mit `before` die Seite davor nach.
   *
   * Der Dauerchat brauchte das nie — zweihundert Nachrichten sind mehr, als
   * jemand zurückliest. Eine Reparatur über Stunden schreibt sie in einem
   * Anlauf voll.
   */
  has_more: boolean
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
  /**
   * Der Aufruf ist gescheitert.
   *
   * Das Feld stand seit jeher im SSE-Payload und fehlte hier — ein
   * fehlgeschlagener Werkzeugaufruf sah im Verlauf damit exakt aus wie ein
   * geglückter. Für „Dokumentation gelesen" ist das der schlimmste Fall: die
   * Zeile behauptet einen Beleg, und die Antwort darunter ist geraten.
   */
  failed?: boolean
  /**
   * Themengruppe aus `ai_tool_registry` (`memory`, `skill`, `docs`).
   *
   * Steuert allein das Symbol. Vorher riet das Frontend sie an einem
   * hartkodierten `tool_name === 'remember'` nach und lag bei `search_memory`
   * und `forget_memory` daneben.
   */
  gruppe?: string | null
}

/**
 * Ein Werkzeug, das gleich läuft — angekündigt, bevor es losgeht.
 *
 * Das Gegenstück zu `AiToolUse`: dort steht, was **war**, hier, was **kommt**.
 * `call_id` ist der Schlüssel und nicht `tool_name`, weil bis zu acht Werkzeuge
 * gleichzeitig laufen und dasselbe Werkzeug in einer Runde zweimal vorkommen
 * kann (zwei `read_config` für zwei Dateien).
 *
 * Die Argumente stehen bewusst **nicht** darin: Serverpfade, Dateinamen und IPs
 * gehören nicht in eine Statuszeile.
 */
export interface AiToolPlanAufruf {
  call_id: string
  tool_name: string
  /** Null, wenn das Werkzeug keinen Server betrifft. */
  server_id: number | null
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
 * Was der Sprachmodus braucht, um überhaupt angeboten zu werden.
 *
 * `available` ist `false`, solange nicht **beide** Zugänge stehen: ein
 * Chatzugang mit hinterlegtem Modell für Gesprochenes und ein Stimmzugang mit
 * Voice ID, jeder mit eigenem Schlüssel. Fehlt einer, erscheint der Sprachknopf
 * gar nicht — kein ausgegrauter Schalter für etwas, das der Betreiber nicht
 * bestellt hat.
 */
export interface AiVoiceConfig {
  available: boolean
  /** Nur zur Anzeige. `null`, solange nichts eingerichtet ist. */
  model: string | null
  /**
   * Die Voice ID des Stimmzugangs, nur zur Anzeige. `null`, solange keiner
   * eingerichtet ist — aufgelöst wird nichts, eine Standardstimme gibt es
   * nicht. Sinnvoll belegt nur, solange `available` wahr ist.
   */
  voice: string | null
  sample_rate: number
  max_seconds: number
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
 * Ein `const`-Array und kein reiner Union-Typ: ein Typ ist zur Laufzeit weg,
 * gegen ihn kann kein Test pruefen. Diese Liste ist inzwischen dreimal
 * gedriftet — zuletzt fehlte `propose_guardian_tuning`, davor
 * `propose_file_delete` und `propose_server_repair` — und jedes Mal fiel es
 * erst in der Oberflaeche auf. Jetzt gleicht `actionTexts.test.ts` das Array
 * gegen seine Abschrift der Registry ab, und die vierte Drift bricht einen
 * Test statt einer Karte.
 *
 * Wer hier etwas ergaenzt, ergaenzt auch `ai.actions.tools.*` und
 * `ai.actions.confirm.*` in **allen** Sprachdateien und prueft, ob das Werkzeug
 * in `UNUMKEHRBAR` gehoert (AiActionProposalCard).
 */
export const SCHREIBWERKZEUGE = [
  'propose_server_lifecycle',
  'propose_backup',
  'propose_backup_restore',
  'propose_config_update',
  'propose_config_patch',
  'propose_config_set',
  'propose_mod_install',
  'propose_bind_ip_update',
  'propose_server_create',
  'propose_server_delete',
  'propose_blueprint_change',
  'propose_blueprint_delete',
  'propose_server_blueprint_switch',
  'propose_hoster_integration',
  'propose_hoster_product',
  'propose_ai_tarif_role',
  'propose_task_set',
  'propose_task_delete',
  'propose_server_repair',
  'propose_guardian_tuning',
  'propose_restart_schedule_set',
  'propose_backup_schedule_set',
  'propose_file_delete',
] as const

export type AiWriteTool = (typeof SCHREIBWERKZEUGE)[number]

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

/**
 * Ein Ausschnitt der eigenen Erinnerungen — und was daneben steht.
 *
 * Der Vorrat darf bis 5.000 Einträge groß werden, und jede Zeile kostet beim
 * Öffnen einen eigenen Aufruf an den DIS-Sidecar. Alles auf einmal wären
 * gemessen rund zehn Sekunden. Die Seite kommt deshalb in Stücken; die drei
 * Zahlen sorgen dafür, dass ein Stück nicht wie das Ganze aussieht.
 */
/**
 * Eine Seite einer Erinnerungsliste — dieselbe Form für das eigene Profil und
 * für einen einzelnen Bereich (Team, Panel, Anlage).
 *
 * Zwei Formen wären zwei Rechnungen für Seitenzahl und nächsten Offset, und die
 * Oberfläche müsste beide führen.
 */
export interface AiMemoryPage {
  entries: AiMemoryEntry[]
  /** Alle Einträge dieser Ansicht, nicht nur die dieser Seite. */
  total: number
  /**
   * Davon das, was „Alle löschen" wirklich mitnimmt. Im Profil sind das nur die
   * allgemeinen Einträge — die Notizen zu einzelnen Servern stehen in derselben
   * Liste und bleiben stehen; in der Ansicht eines Bereichs sind es alle. Die
   * Zahl kommt vom Server, weil die Oberfläche seit der Seitenweise nur noch
   * einen Ausschnitt sieht und sie nicht mehr selbst zählen kann.
   */
  clearable: number
  /** Wie groß eine Seite ist. Bestimmt der Server, denn er bezahlt sie. */
  limit: number
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
  /** Ab wie viel Prozent Belegung zusammengefasst wird. */
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
  /** Die Gegenzahl dazu: was in den Zwischenspeicher geschrieben wurde. */
  cache_write_tokens: number | null
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
  /**
   * Abdruck über Name+Beschreibung+Text. Die Freigabe schickt ihn zurück und
   * bestätigt damit den gelesenen Inhalt — wurde der Skill zwischen Lesen und
   * Klick überschrieben, antwortet das Backend mit 409 statt fremden Text
   * panelweit freizugeben.
   */
  fingerprint: string
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
  // Was gleich läuft, gemeldet **bevor** es läuft. Flüchtig: das Ereignis
  // landet nicht im Abzug, weil es keine Tatsache über die Antwort ist,
  // sondern eine Anzeige während der Arbeit. Nach einem Neuladen mitten im
  // Lauf fehlt es also — das ist bewusst so.
  | { event: 'tool_plan'; data: { aufrufe: AiToolPlanAufruf[] } }
  // Rückfrage mit Vorschlägen. Beendet den Zug — ab hier ist der Mensch dran.
  | { event: 'question'; data: AiQuestion }
  // Der aeltere Teil des Verlaufs wurde zu einer Zusammenfassung gefaltet.
  | { event: 'compacted'; data: { conversation_id: string } }
  | { event: 'proposal'; data: AiActionProposal }
  // Eine bereits ausgefuehrte autonome Aktion. Bewusst ein eigenes Ereignis:
  // sie ist keine Anfrage an den Benutzer, sondern eine Meldung.
  | { event: 'action'; data: AiActionProposal }
  | { event: 'done'; data: { message_id: string; replayed?: boolean } }
  // Nur der Code, kein Wortlaut. Hier stand kurzzeitig ein `detail` mit dem
  // Satz des Anbieters; es ist bewusst wieder weg, weil dieser Satz das Konto
  // des Betreibers beschreibt und der Lauf einem Benutzer gehoert
  // (`ai_stream_service`, Ausnahmezweig). Der Code traegt die Erklaerung.
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
  // Die dritte Parkstelle: ein Langläufer schläft bis `wake_at` (nur in
  // Worker-Fenstern). Ruhend wie die anderen Parkstellen — er tut nichts von
  // selbst, bis der Takt ihn weckt.
  'waiting_wake',
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
  /**
   * Die Gliederung des laufenden Segments. Hier stand `tools: AiToolUse[]`
   * neben `content` — zwei Toepfe ohne Beziehung zueinander. Solange die KI
   * erst alle Werkzeuge rief und danach redete, liess sich die Anordnung raten
   * (alle Werkzeuge vor die Blase); sobald sie **waehrend** der Arbeit spricht,
   * geht das nicht mehr.
   */
  sections: AiSection[]
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
  /**
   * In welchem Fenster dieser Lauf arbeitet.
   *
   * Ohne diese Angabe hängt sich der Chat an den nächstbesten aktiven Lauf —
   * und zeichnet eine Guardian-Reparatur in das Fenster des Menschen. Genau
   * das ist der Zustand, den die Trennung beseitigt. Die Glocke braucht es
   * ebenso, sonst meldet sie „die KI ist mit deinem Auftrag fertig" für etwas,
   * das niemand beauftragt hat.
   */
  kind: AiConversationKind
  conversation_id: string | null
  /** Um welchen Server es zuletzt ging — nur aus nachgewiesenem Zugriff. */
  server_id: number | null
}

/**
 * Die Ereignisse, die dieser Client versteht.
 *
 * An die Union gebunden, damit ein neues Ereignis in `AiStreamEvent` hier
 * auffällt, statt still zu fehlen — ohne die Bindung war die Liste eine
 * handgepflegte Kopie.
 */
const STREAM_EVENTS: readonly AiStreamEvent['event'][] = [
  'message', 'delta', 'reasoning', 'tool', 'tool_plan', 'question', 'compacted',
  'proposal', 'action', 'done', 'error', 'snapshot', 'segment', 'run',
]

export interface AiProviderWrite {
  name: string
  provider_kind: string
  default_model?: string | null
  enabled: boolean
  requires_api_key: boolean
  token_price_micro_usd_per_million?: number | null
  /**
   * Nur ein Sprachzugang schickt das Feld mit; ein Chatzugang laesst es weg,
   * statt `null` zu senden. Der Unterschied zaehlt, weil `PATCH` in die
   * vorhandene Zeile mischt: „nicht genannt" laesst die Stimme stehen,
   * ausdrueckliches `null` nimmt sie zurueck.
   */
  default_voice?: string | null
  transcription_model?: string | null
  /**
   * Wie `default_voice`: „nicht genannt" lässt den Stand stehen,
   * ausdrückliches `null` schaltet den Hintergrund-Betrieb ab.
   */
  worker_model?: string | null
  worker_reasoning_effort?: string | null
  operator_api_key?: string
  clear_operator_api_key?: boolean
}

/**
 * Ein Hintergrund-Auftrag, wie die Worker-Leiste des Chats ihn zeigt.
 *
 * Bewusst nur das Nötigste: Kennung, geschwärzter Titel, Laufzustand, Beginn.
 * Kein Auftragstext, keine Serverdaten — die Leiste ist eine Übersicht, kein
 * zweiter Verlauf.
 */
export interface AiWorkerInfo {
  conversation_id: string
  title: string
  /** `running` oder eine Parkstelle (`waiting_*`); Beendete stehen nie hier. */
  status: AiRunStatus
  created_at: string
}

/**
 * Ein stehender Auftrag, wie `GET /ai/tasks` ihn liefert — dieselbe Form, die
 * auch das Chat-Werkzeug `list_tasks` sieht (`ai_task_service.eintrag`).
 *
 * `plan` ist der fertige Satz des Backends (deutsch, mit Zeitzone); die
 * Aufgabenliste baut ihre Anzeige aus den strukturierten Feldern daneben, um
 * beide Sprachen bedienen zu können. `conversation_id` ist das
 * Hintergrundfenster der Aufgabe — der Link auf `?ansicht=worker&id=…`.
 */
export interface AiTaskEntry {
  task_id: string
  title: string
  instruction: string
  /** `act` heisst: darf Schreibwerkzeuge nutzen — setzt den autonomen Modus voraus. */
  kind: 'report' | 'act'
  plan: string
  plan_kind: 'daily' | 'interval' | 'once'
  /** ``"HH:MM"`` in der Zeitzone der Aufgabe; nur bei `daily`. */
  time_of_day: string | null
  /** ISO-Wochentage als ``"1,3,5"`` (Montag = 1); `null` heisst täglich. */
  weekdays: string | null
  interval_hours: number | null
  /** ISO-8601 in UTC; nur bei `once`. */
  once_at: string | null
  timezone: string
  channel: 'chat' | 'email' | 'both'
  enabled: boolean
  conversation_id: string | null
  next_run: string | null
  last_started: string | null
}

/**
 * Was beim Anlegen oder Ändern übertragen wird — Teilangaben sind erlaubt.
 *
 * `JSON.stringify` lässt `undefined`-Felder weg, und genau darauf baut das
 * Backend (`exclude_unset`): nur was genannt ist, wird angefasst. Wer eine
 * Aufgabe nur pausieren will, schickt allein `enabled`.
 */
export interface AiTaskWrite {
  title?: string
  instruction?: string
  kind?: 'report' | 'act'
  plan_kind?: 'daily' | 'interval' | 'once'
  time_of_day?: string
  weekdays?: number[]
  interval_hours?: number
  once_at?: string
  timezone?: string
  channel?: 'chat' | 'email' | 'both'
  enabled?: boolean
}

/** Die Betreiber-Deckel der Worker: wie viele je Benutzer, wie viele Runden je Lauf. */
export interface AiWorkerPolicy {
  max_parallel_workers: number
  rounds_per_worker: number
  min_workers: number
  max_workers: number
  min_rounds: number
  max_rounds: number
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
  /**
   * Die Modelle eines Anbieters. `refresh` umgeht den Zwischenspeicher.
   *
   * `providerId` nennt den Zugang, dessen Schlüssel den Katalog holt. Nur
   * nötig bei Anbietern mit `katalog_braucht_schluessel` (OpenAI); OpenRouter
   * gibt seine Liste offen heraus. Fehlt der Schlüssel, kommt eine leere
   * Liste — beim Anlegen eines Zugangs gibt es ihn noch gar nicht.
   */
  listCatalogModels: (kind: string, refresh = false, providerId?: number) => {
    const frage = new URLSearchParams()
    if (refresh) frage.set('refresh', 'true')
    if (providerId !== undefined) frage.set('provider_id', String(providerId))
    const anhang = frage.toString()
    return api<AiCatalogModel[]>(
      `/ai/settings/provider-kinds/${encodeURIComponent(kind)}/models${anhang ? `?${anhang}` : ''}`,
    )
  },
  testProvider: (id: number) => api<AiProviderTestResult>(`/ai/settings/providers/${id}/test`, {
    method: 'POST',
  }),
  /**
   * Ob gesprochen werden kann. Braucht nur `ai.voice.use` — die Antwort nennt
   * keinen Schlüssel und keinen Zugang, nur die Modellkennung.
   */
  getVoiceConfig: (providerId?: number | null) =>
    api<AiVoiceConfig>(providerId ? `/ai/voice/config?provider_id=${providerId}` : '/ai/voice/config'),
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
  /**
   * Eine Unterhaltung. Wird beim ersten Aufruf serverseitig angelegt.
   *
   * Ohne `kind` der Dauerchat — so fragt der Chat, und so hat er immer
   * gefragt. `before` ist die Kennung der ältesten bereits geladenen
   * Nachricht und liefert die Seite davor.
   */
  getConversation: (kind: AiConversationKind = 'primary', before?: string) => {
    const suche = new URLSearchParams({ kind })
    if (before) suche.set('before', before)
    return api<AiConversationDetail>(`/ai/conversation?${suche.toString()}`)
  },
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
  /**
   * Die Vorschläge eines Fensters. Ohne `kind` die des Dauerchats.
   *
   * Ein Reparaturlauf, der auf eine Bestätigung wartet, legt seine Vorschläge
   * unter **seiner** Unterhaltung an — ohne `kind` bekäme man sie nie zu
   * sehen, und die Karte, auf deren Klick der Lauf wartet, wäre im ganzen
   * Panel nirgends.
   */
  listActions: (kind: AiConversationKind = 'primary') =>
    api<AiActionProposal[]>(`/ai/conversation/actions?kind=${kind}`),
  /**
   * Die Vorschläge eines Worker-Fensters — über die Kennung, denn
   * `kind=worker` ist mehrdeutig: es gibt je Auftrag ein Fenster.
   */
  listWorkerActions: (conversationId: string) =>
    api<AiActionProposal[]>(
      `/ai/conversation/actions?conversation_id=${encodeURIComponent(conversationId)}`,
    ),
  /**
   * Die lebenden Hintergrund-Aufträge — die Worker-Leiste des Chats.
   * Beendete fallen beim nächsten Blick heraus („räumt sich auf");
   * ihre Unterhaltung bleibt über `getWorkerConversation` lesbar.
   */
  listWorkers: () => api<AiWorkerInfo[]>('/ai/conversation/workers'),
  /** Ein Worker-Fenster, nur lesend. Fremde und Unbekannte sind dasselbe 404. */
  getWorkerConversation: (conversationId: string, before?: string) => {
    const suche = new URLSearchParams()
    if (before) suche.set('before', before)
    const rest = suche.toString()
    return api<AiConversationDetail>(
      `/ai/conversation/worker/${encodeURIComponent(conversationId)}${rest ? `?${rest}` : ''}`,
    )
  },
  /** Der aktive Lauf eines bestimmten Fensters — der Weg der Worker-Ansicht. */
  getWorkerRun: (conversationId: string) =>
    api<AiRunInfo | null>(
      `/ai/conversation/run?conversation_id=${encodeURIComponent(conversationId)}`,
    ),
  /**
   * Die Aufgabenliste — dieselben Dienstfunktionen, durch die auch die
   * Chat-Werkzeuge gehen. Alles, was die KI kann, kann der Benutzer hier auch;
   * alle vier verlangen `ai.tasks.manage`.
   */
  listTasks: () => api<AiTaskEntry[]>('/ai/tasks'),
  createTask: (payload: AiTaskWrite) =>
    api<AiTaskEntry>('/ai/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  updateTask: (taskId: string, payload: AiTaskWrite) =>
    api<AiTaskEntry>(`/ai/tasks/${encodeURIComponent(taskId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteTask: (taskId: string) =>
    api<{ deleted: boolean; title: string }>(`/ai/tasks/${encodeURIComponent(taskId)}`, {
      method: 'DELETE',
    }),
  /**
   * Das Tipp-Signal der Ruhe-Regel: „der Mensch schreibt gerade".
   * Übertragen wird nur der Zeitpunkt — nie Text und nicht einmal seine Länge.
   */
  typing: () => api('/ai/conversation/typing', { method: 'POST' }),
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
  /**
   * Ein ganzer Bereich auf einmal. Die Oberfläche liest so nicht mehr — sie
   * nimmt `listScopeMemory`, seit ein Teambereich 5.000 Einträge fassen darf
   * und jede Zeile beim Öffnen eine Entschlüsselung kostet.
   */
  listMemory: (scope: AiMemoryEntry['scope'], serverId?: number, teamId?: number) => api<AiMemoryEntry[]>(
    `/ai/memory?scope=${scope}${serverId ? `&server_id=${serverId}` : ''}${teamId ? `&team_id=${teamId}` : ''}`,
  ),
  /**
   * Eine Seite eines Bereichs: Teamwissen, panelweites Wissen, das Wissen einer
   * Anlage.
   *
   * Gebraucht wird das Blättern heute nur vom Team — es hängt am Rollenlimit
   * seines Gründers und darf 5.000 Einträge fassen, während Panel und Anlage
   * bei hundert fest gedeckelt sind. Trotzdem gehen alle drei denselben Weg:
   * eine Ansicht mit zwei Leseroutinen ist eine Gelegenheit, sie auseinander
   * laufen zu lassen. Wo es nichts zu blättern gibt, meldet der Server eine
   * Seite und die Leiste bleibt weg.
   */
  listScopeMemory: (
    scope: AiMemoryEntry['scope'], serverId?: number, teamId?: number, offset = 0,
  ) => api<AiMemoryPage>(
    `/ai/memory/page?scope=${scope}${serverId ? `&server_id=${serverId}` : ''}`
    + `${teamId ? `&team_id=${teamId}` : ''}&offset=${offset}`,
  ),
  /**
   * Eine Seite von allem, was einem selbst gehört: persönlich **und**
   * serverbezogen.
   *
   * Ein eigener Aufruf neben `listScopeMemory` und nicht dessen Sonderfall:
   * diesen Bereich gibt es als Kennung nicht. Er spannt zwei (`user` und
   * `server`) und geht deshalb über den Besitzer — `listMemory('server', …)`
   * verlangt dagegen eine konkrete Server-ID, und wer alle seine Notizen sehen
   * wollte, hätte die Server raten müssen.
   *
   * Übergeben wird nur der Offset. Wie groß eine Seite ist, entscheidet der
   * Server und sagt es in `limit` — er bezahlt sie in Entschlüsselungen, und
   * eine Grenze, die der Client setzen darf, ist keine.
   */
  listPersonalMemory: (offset = 0) => api<AiMemoryPage>(`/ai/memory/personal?offset=${offset}`),
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
  approveSkill: (skillId: string, fingerprint: string) => api<AiSkillManaged>(`/ai/skills/${skillId}/approve`, {
    method: 'POST', body: JSON.stringify({ fingerprint }),
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
  getWorkerPolicy: () => api<AiWorkerPolicy>('/ai/settings/worker'),
  setWorkerPolicy: (maxParallelWorkers: number, roundsPerWorker: number) =>
    api<AiWorkerPolicy>('/ai/settings/worker', {
      method: 'PUT',
      body: JSON.stringify({
        max_parallel_workers: maxParallelWorkers,
        rounds_per_worker: roundsPerWorker,
      }),
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
  /**
   * Laeuft gerade noch etwas von vorhin? `null`, wenn nicht.
   *
   * `kind` grenzt auf ein Fenster ein. `null` fragt über alle — das tut die
   * Glocke, die nur wissen will, ob überhaupt etwas läuft, und danach über
   * `AiRunInfo.kind` entscheidet, wohin sie zeigt.
   */
  getActiveRun: (kind: AiConversationKind | null = 'primary') =>
    api<AiRunInfo | null>(`/ai/conversation/run?kind=${kind ?? ''}`),
  /**
   * Ein Mensch übernimmt: die laufenden Reparaturen dieses Menschen enden.
   *
   * Beendet wird der **Auftrag**, nicht nur der Lauf. Nur den Lauf abzubrechen
   * hieße, dass der Takt neunzig Sekunden später den nächsten startet.
   */
  takeOverGuardian: () => api<{ aborted: number }>(
    '/ai/conversation/guardian/takeover', { method: 'POST' },
  ),
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
    if (!data || typeof data !== 'object') throw new Error('AI_STREAM_INVALID')
    // Ein unbekannter Ereignisname ist kein kaputter Strom, sondern ein
    // Bündel, das älter ist als das Backend. Übergehen statt werfen: sonst
    // reißt ein neu eingeführtes Ereignis die halb geschriebene Antwort ab
    // und markiert sie als fehlgeschlagen, während der Lauf weiterläuft.
    if (!(STREAM_EVENTS as readonly string[]).includes(eventName)) return
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
