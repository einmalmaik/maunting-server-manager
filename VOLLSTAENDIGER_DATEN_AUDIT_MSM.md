# Umfassender Audit-Report: Sämtliche gespeicherten und verarbeiteten Benutzerdaten im MSM (Maunting Server Manager)

Dieser Audit liefert eine lückenlose, bis ins kleinste Detail reichende Gesamtanalyse aller im Maunting Server Manager (Backend, Frontend, Desktop Smart System, Sidecars, externe Dienste und Betriebssystem-Ebene) erfassten, gespeicherten und verarbeiteten Benutzerdaten: von Authentifizierungsdaten über Tracking, Metadaten, IP-Adressen, Verhaltens- und Rich-Presence-Erfassung bis hin zu KI-Gedächtnissen, biometrischen Sprachaufnahmen und kryptografischen Schlüsseln.

---

## 1. Benutzerkonten, Authentifizierung, Rollen & Zugangsdaten

### 1.1 Datenbank: Tabelle `users` ([`User`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/user.py#L54-L180))
* **`id`** (`Integer`, PK): Interne Benutzer-ID.
* **`username`** (`String(64)`, Unique, Index): Klartext-Benutzername.
* **`email_plain`** (`String(255)`, Spalte `email`): Legacy-Spalte; nach Migration enthält sie den Hash-Wert als Platzhalter, keine Klartext-E-Mail mehr.
* **`email_encrypted`** (`String(4096)`): E-Mail-Adresse, verschlüsselt über DIS-Sidecar (AES-256-GCM, AAD `msm:user:email`).
* **`email_hash`** (`String(64)`, Unique, Index): Deterministischer SHA-256-Hash der E-Mail + Pepper (`settings.secret_key`) für SQL-Lookups (`WHERE email_hash = ?`).
* **`password_hash`** (`String(255)`): Password-Hash (Argon2id via DIS-Sidecar oder Bcrypt).
* **`is_owner`** (`Boolean`): Flag für den Panel-Hauptadministrator.
* **`is_active`** (`Boolean`): Kontostatus.
* **`role_id`** (`Integer`, FK `roles.id`, ondelete `SET NULL`): Primäre RBAC-Rolle (Legacy-Kompatibilität).
* **`email_verified`** (`Boolean`): E-Mail-Verifizierungsstatus.
* **`two_factor_secret_encrypted`** (`String(255)`): DIS-verschlüsseltes Base32-TOTP-Secret (2FA-Schlüssel).
* **`two_factor_enabled`** (`Boolean`): Status der Zwei-Faktor-Authentifizierung.
* **`email_notifications`** (`Boolean`): Schalter für den Empfang von System- und Sicherheits-E-Mails.
* **`ai_notifications`** (`Boolean`): Schalter für In-Panel-Benachrichtigungen der KI (getrennt von E-Mails).
* **`device_notifications`** (`Boolean`): Schalter für native Pop-up-/Push-Benachrichtigungen auf Windows und Android.
* **`time_zone`** (`String(64)`): IANA-Zeitzone des Benutzers (z. B. `Europe/Berlin`) für Lageblock, Chat-Zeitstempel und APScheduler-Tasks.
* **`location_sharing_enabled`** (`Boolean`): Reine Einwilligung für Standortzugriff (Koordinaten werden zu keinem Zeitpunkt im Benutzerkonto persistiert).
* **`agent_name`** (`String(32)`): Vom Benutzer frei gewählter Rufname des Assistenten (z. B. „Jarvis“; Standard `Assistent`). Fließt in den Lageblock der KI ein.
* **`ai_provider_id`** (`Integer`, FK `ai_providers.id`, ondelete `SET NULL`): Vom Benutzer gewählter bevorzugter KI-Anbieter für Text und Sprache.
* **`ai_desktop_systembereich`** (`String(16)`): Erlaubte Verzeichnisgrenze der KI auf dem PC des Nutzers (`aus`, `lesen`, `schreiben`).
* **`password_reset_token`** (`String(255)`): Aktives Einmal-Token für die Passwortrücksetzung.
* **`password_reset_expires`** (`DateTime`): Ablaufzeitpunkt des Passwort-Reset-Tokens.
* **`social_privacy`** (`String(16)`): Sichtbarkeitsstufe des Profils (`private`, `friends`, `public`).
* **`avatar_url`** (`String(512)`): Pfad zum Profilbild (z. B. `/api/auth/avatar/avatar_1_ab12cd34ef56.png`).
* **`created_at`** (`DateTime`): Registrierungszeitpunkt (UTC).

### 1.2 Multi-Rollen & Granulare Berechtigungen
* **Tabelle `user_roles`** ([`UserRole`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/user_role.py#L16-L45)):
  * `id` (`Integer`, PK), `user_id` (FK `users.id`, CASCADE), `role_id` (FK `roles.id`, CASCADE), `assigned_at` (`DateTime`).
  * Ermöglicht die Zuweisung mehrerer globaler RBAC-Rollen pro Benutzer.
* **Tabelle `server_permissions`** ([`ServerPermission`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/server_permission.py#L9-L38)):
  * `id` (`Integer`, PK), `user_id` (FK `users.id`, CASCADE), `server_id` (FK `servers.id`, CASCADE).
  * `permission_key` (`String(64)`): Granulare Einzelberechtigung (z. B. `server.console.read`, `server.files.write`, `server.power`).
  * `granted_at` (`DateTime`), `granted_by` (FK `users.id`, SET NULL).

### 1.3 Sitzungs-, Token- und Geräteverwaltung
* **Tabelle `refresh_tokens`** ([`RefreshToken`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/refresh_token.py#L9-L34)):
  * `id`, `user_id` (FK `users.id`).
  * `token_hash` (`String(64)`, Unique, Index): SHA-256-Hash des Refresh-Tokens.
  * `family` (`String(64)`, Index): Sitzungs-Familie zur Geräte-Identifikation und Erkennung von Token-Wiederverwendung.
  * `geraet` (`String(16)`): Client-Typ (`desktop` für Smart-System-App, `NULL` für Web-Browser).
  * `expires_at`, `created_at`, `revoked_at`, `used_at`.
* **Tabelle `jwt_blacklist`** ([`JwtBlacklist`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/jwt_blacklist.py#L9-L19)):
  * `id`, `jti` (`String(64)`, Index): Eindeutige JWT-ID revokierter Access-Tokens.
  * `user_id` (FK `users.id`, Index), `expires_at`, `created_at`.
* **Tabelle `backup_codes`** ([`BackupCode`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/backup_code.py#L9-L22)):
  * `user_id`, `code_hash` (`String(64)`, SHA-256), `used_at`, `created_at`.
* **Tabelle `email_verifications`** ([`EmailVerification`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/email_verification.py#L9-L22)):
  * `email_hash`, `code_hash` (SHA-256 des 6-stelligen Zahlencodes), `purpose` (`setup` / `register`), `verified`, `expires_at`.
* **Tabelle `login_challenges`** ([`LoginChallenge`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/login_challenge.py#L10-L45)):
  * `token_hash`, `purpose` (`oauth_2fa`), `user_id`, `payload_json` (OAuth-Provider-Slug, Weiterleitungs-URL), `expires_at`, `consumed_at`.
* **Tabelle `device_pairings`** ([`DevicePairing`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/device_pairing.py#L30-L72)):
  * `user_id`, `code_hash` (SHA-256 des 12-Zeichen-Kopplungscodes), `label` (Gerätename), `family` (Refresh-Token-Familie).
  * `verlauf_blob` (`Text`): Ende-zu-Ende verschlüsseltes Archiv der Chat-Historie für den initialen Geräteabgleich.
  * `verlauf_abgelegt_am`, `expires_at`, `redeemed_at`.
* **Tabelle `node_enrollments`** ([`NodeEnrollment`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/node_enrollment.py#L9-L30)):
  * `id`, `claim_hash` (`String(64)`), `display_code` (`String(9)`), `name`, `host`, `tls_fingerprint`, `auth_token_enc`, `status`, `node_id`, `created_at`, `expires_at`, `claimed_at`.

### 1.4 Externe Konten & Hinterlegte Zugangsdaten
* **Tabelle `oauth_user_links`** ([`OAuthUserLink`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/oauth_user_link.py#L9-L95)):
  * `provider_id`, `user_id`.
  * `subject` (`String(64)`): SHA-256-Hash der externen IdP-Benutzerkennung (Google, GitHub, Microsoft).
  * `email_at_link_encrypted`, `username_at_link_encrypted`: DIS-verschlüsselte historische Identitätsdaten bei Verknüpfung.
  * `created_at`, `last_used_at`.
* **Tabelle `user_credentials`** ([`UserCredential`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/credential.py#L37-L70)):
  * `user_id`, `kind` (`github_token`, `steam_account`), `label`.
  * `username` (`String(256)`): Klartext-Steam-Benutzername.
  * `secret_encrypted` (`Text`): DIS-verschlüsseltes GitHub-Personal-Access-Token oder Steam-Passwort.
  * `secret_hint` (`String(16)`): Maskierter Hinweis (z. B. `ghp_...ab12`).
* **Tabelle `server_credential_bindings`** ([`ServerCredentialBinding`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/credential.py#L72-L105)):
  * `server_id`, `kind`, `credential_id` (FK `user_credentials.id`), `bound_at`. Verknüpft persönliche Credentials mit Servern.

---

## 2. Tracking, IP-Adressen, Hardware-Fingerprinting & Verhaltensmetadaten

### 2.1 IP-Adressen-Erfassung und Netzwerkübermittlung
* **Login-Sicherheits-Benachrichtigung** ([`backend/routers/auth.py:L398-L408`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/routers/auth.py#L398-L408)):
  * Bei jedem erfolgreichen Login werden `client_ip = request.client.host` und `user_agent = request.headers.get("user-agent")` ausgelesen und per SMTP in einer Benachrichtigungs-E-Mail an die E-Mail-Adresse des Nutzers geschickt.
* **Captcha-Validierung** ([`backend/services/captcha_service.py:L65-L76`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/services/captcha_service.py#L65-L76)):
  * Bei Registrierung, Login und Passwort-Reset wird die IP als `remoteip` per HTTP POST an Cloudflare Turnstile, hCaptcha oder Google reCAPTCHA übertragen.
* **Rate-Limiter (Slowapi & Redis)** ([`backend/middleware/rate_limit.py:L21-L47`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/middleware/rate_limit.py#L21-L47)):
  * `get_remote_address(request)`: Die Client-IP ist der Rate-Limit-Schlüssel. In Redis werden Keys im Format `LIMITER:<ip>:<route>` mit TTL gespeichert.
* **Webserver- & Reverse-Proxy-Logs**:
  * **Caddy** ([`Caddyfile.template`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/Caddyfile.template)): Speichert Zugriffslogs mit IP, User-Agent, URI, TLS-Version und Referer im systemd Journal.
  * **Uvicorn** ([`msm.service.template:L15-L19`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/msm.service.template#L15-L19)): Schreibt jede HTTP- und WebSocket-Anfrage mit IP und HTTP-Methode nach stdout/journald.

### 2.2 Verhaltens-Tracking & Routenüberwachung ([`usePresenceAndActivity.ts`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/hooks/usePresenceAndActivity.ts#L63-L260))
* **Hardware-Eingabe-Monitoring**:
  * Globale Listener auf `pointerdown`, `keydown`, `wheel`, `touchstart` und `pointermove` (gedrosselt alle 3s).
  * Spezifische Listener auf KI-Tippereignisse: `msm:ai-user-typing` und `msm:ai-message-sent`.
* **Tab- und Sichtbarkeitsstatus**:
  * `visibilitychange`: Schaltet bei Tab-Wechsel oder Fensterminimierung automatisch auf `away`.
  * Inaktivitäts-Timer: 5 Minuten (300.000 ms) ohne Eingabe setzen den Status auf `away`.
* **Routen- & Aufenthalts-Tracking**:
  * Erfasst `location.pathname` und überträgt Klartext-Aktivitätsbeschreibungen:
    * `/ai` → Aktivität: „Im KI-Chat“, Detail: „Singra Assistent“, Kategorie: `ai_chat`
    * `/servers/<id>` → Aktivität: „Auf Server“, Detail: „Server-Administration“, Kategorie: `server_admin`
    * `/servers` → Aktivität: „Server-Übersicht“, Detail: „Infrastruktur“, Kategorie: `server_admin`
    * `/chat` oder `/social` → Aktivität: „Im Chat“, Detail: „Messenger“, Kategorie: `general`
    * `/calendar` → Aktivität: „Im Kalender“, Detail: „Termine & Aufgaben“, Kategorie: `general`
    * `/notes` → Aktivität: „In den Notizen“, Detail: „Dokumentation“, Kategorie: `general`
    * `/settings` → Aktivität: „In den Einstellungen“, Detail: „Systemkonfiguration“, Kategorie: `general`
* **Geräte-Fingerprinting** ([`detectDeviceType()`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/hooks/usePresenceAndActivity.ts#L9-L24)):
  * Unterscheidung nach `desktop` (Tauri-Instanz, `MSM-Desktop`-User-Agent), `mobile` (Viewport < 768px oder Regex auf iOS/Android-User-Agents) oder `web`.
* **Persistente Datenbank-Speicherung der Aktivitäten**:
  * **Tabelle `user_presences`** ([`UserPresence`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/user_presence.py#L14-L43)): `user_id`, `status` (`online`, `away`, `invisible`), `device_type`, `custom_status`, `activity_label`, `activity_detail`, `updated_at`.
  * **Tabelle `user_activity_times`** ([`UserActivityTime`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/user_activity_time.py#L14-L38)): Alle 30 Sekunden Interaktion sendet der Client Ping-Requests (`/api/social/activity/ping`). Speichert kumulierte aktive Nutzungszeit in Sekunden (`active_seconds`) je Benutzer und Kategorie.
  * **Tabelle `user_achievements`** ([`UserAchievement`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/user_achievement.py#L14-L36)): Speichert erreichte Aktivitäts-Meilensteine (1h, 5h, 10h, 25h, 50h erfasste Aktivitätszeit).

---

## 3. KI-Systeme, Sprachmodus, Gedächtnis & Hintergrund-Agenten

### 3.1 Konversationen, Denkprozesse & Tool-Ergebnisse
* **Tabelle `ai_conversations`** ([`AiConversation`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_conversation.py#L50-L127)):
  * `id` (UUID), `kind` (`primary`, `guardian`, `worker`), `user_id`, `title`, `summary` (vom LLM generierte Zusammenfassung früherer Chat-Nachrichten), `summarized_until`, Timestamps.
* **Tabelle `ai_messages`** ([`AiMessage`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_conversation.py#L129-L229)):
  * `conversation_id`, `role` (`user`, `assistant`), `content` (**Klartext sämtlicher Benutzernachrichten und KI-Antworten**).
  * `reasoning` (`Text`): **Vollständige interne Denk- und Reasoning-Schritte** der KI-Modelle.
  * `question_json` (`Text`): Strukturierte Rückfragen der KI an den Benutzer mit Optionen.
  * `sections_json` (`Text`): Replay-Struktur der Antwort (Abschnitte, Werkzeugaufrufe, Denkschritte).
  * `intern` (`Boolean`): Interne Systemnachrichten (Worker-Meldungen, Guardian-Briefings).
  * `provider_id`, `model`, `request_id`, `status`.
* **Tabelle `ai_tool_results`** ([`AiToolResult`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_tool_result.py#L28-L50)):
  * Speichert Ausgaben von Werkzeugen (`tool_name`, `result_json`), z. B. gelesene Server-Logs, Dateiinhalte, Systemzustände, Terminal-Ausgaben.
* **Tabelle `ai_runs`** ([`AiRun`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_run.py#L53-L167)):
  * `state_json` (`Text`): **Vollständiges Arbeitsgedächtnis des Agentenlaufs** inklusive aller Nachrichten an den externen LLM-Provider und Tool-Ergebnissen.
  * `reasoning` (`Boolean`), `reasoning_effort` (`minimal`, `medium`, `high`), `stop_reason`, `last_server_id`.

### 3.2 KI-Gedächtnis & Vektor-Embeddings
* **Tabelle `ai_memory_preferences`** ([`AiMemoryPreference`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_memory.py#L22-L41)):
  * `user_id`, `enabled` (`Boolean`), `notice_last_shown_at`, `notice_hidden`.
* **Tabelle `ai_memory_entries`** ([`AiMemoryEntry`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_memory.py#L43-L175)):
  * `owner_user_id`, `scope` (`user`, `server`, `server_shared`, `team`, `panel`), `scope_identity`.
  * `key` (`String(64)`): Klartext-Schlüssel (z. B. `preferred_language`, `operating_system`).
  * `value_encrypted` (`Text`): DIS-verschlüsselter Inhalt des gemerkten Benutzerfaktums.
  * `origin` (`user` = vom Nutzer gesagt, `ai` = von der KI abgeleitet).
  * `use_count`, `last_used_at`.
  * `embedding_bytes` (`LargeBinary`): 256-Dimensionen float32-Vektor (mit AES-GCM verschlüsselt), berechnet über Schlüssel und Wert für semantische Ähnlichkeitssuche.

### 3.3 Hochgeladene KI-Dateianhänge
* **Tabelle `ai_attachments`** ([`AiAttachment`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_attachment.py#L11-L45)):
  * `user_id`, `conversation_id`, `message_id`, `original_name` (`String(128)`).
  * `media_type`, `size_bytes`, `sha256`.
  * `content_encrypted` (`Text`): DIS-verschlüsselter Dateiinhalt (wird in der Datenbank gespeichert, nicht im Dateisystem).
  * `extracted_text_encrypted` (`Text`): DIS-verschlüsselter extrahierter Text (z. B. aus PDF/Logs).
  * `redacted_spans` (`Integer`): Anzahl der automatisch geschwärzten sensiblen Zeichenketten (Passwörter, Tokens).

### 3.4 Selbstlernende KI-Fähigkeiten
* **Tabelle `ai_skills`** ([`AiSkill`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_skill.py#L40-L87)):
  * `id` (UUID), `scope_identity` (`global`, `team:{id}`), `team_id`, `skill_key`, `name`, `description`.
  * `body` (`Text`): **Vollständige Handlungsanweisung / erlernte Vorgehensweise in Prosa**.
  * `origin` (`operator` = vom Menschen verfasst, `ai` = von der KI selbst erlernt).
  * `status` (`active`, `pending`), `enabled`, `created_by` (FK `users.id`, SET NULL).
  * `embedding_json` (`Text`), `embedding_model` (`String(64)`): Vektor-Embedding des Skills.

### 3.5 Autonome Reparaturaufträge & Guardian-Notizen
* **Tabelle `ai_guardian_notices`** ([`AiGuardianNotice`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_guardian_notice.py#L50-L81)):
  * `incident_id` (FK `incidents.id`), `user_id` (FK `users.id`), `mode` (`briefed`, `healing`), `run_id` (FK `ai_runs.id`).
  * Dokumentiert, welcher Benutzer über welchen Servervorfall benachrichtigt wurde bzw. für welchen Vorfall eine autonome Reparatur gestartet wurde.
* **Tabelle `ai_guardian_repairs`** ([`AiGuardianRepair`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_guardian_repair.py#L95-L183)):
  * `id` (UUID), `incident_id`, `server_id`, `user_id`, `phase` (`diagnose`, `eingriff`, `beobachtung`, `erledigt`, `eskaliert`, `aufgegeben`, `abgebrochen`), `attempt`, `next_run_at`, `deadline_at`, `last_started_at`, `last_run_id`.
  * `erkenntnisse` (`Text`): **Persistente Zwischenerkenntnisse der KI** über Server-Fehler, Log-Analysen und Eingriffe über mehrere Durchläufe hinweg.

### 3.6 Geplante Aufgaben, E-Mails & Vorschläge
* **Tabelle `ai_tasks`** ([`AiTask`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_task.py#L89-L177)):
  * `user_id`, `title`, `instruction` (**Klartext-Prompt / Arbeitsauftrag des Nutzers**).
  * `kind` (`report`, `act`), `plan_kind` (`daily`, `interval`, `once`), `time_of_day`, `weekdays`, `interval_hours`, `time_zone`, `channel` (`chat`, `email`, `both`), `next_run_at`, `last_started_at`, `last_run_id`.
* **Tabelle `ai_mail_outbox`** ([`AiMailOutbox`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_mail_outbox.py#L75-L154)):
  * `user_id`, `anlass`, `betreff`, `text_body`, `html_body` (**Klartext der generierten E-Mails**), `fakten`, `rahmen_json`, `sent_at`.
* **Tabelle `ai_meldungen`** ([`AiMeldung`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_meldung.py#L38-L88)):
  * `user_id`, `worker_id`, `art` (`ergebnis`, `frage`), `kanal`, `text`, `question_json`, `zugestellt_at`.
* **Tabelle `ai_action_proposals` & `ai_action_approvals`** ([`AiActionProposal`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_action_proposal.py#L11-L106), [`AiActionApproval`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_action_approval.py#L48-L105)):
  * `user_id`, `tool_name`, `payload_encrypted` (DIS-verschlüsselt), `preview_json` (Diff-Vorschau), `reason`, `expected_effect`, `token_hash`, `decision` (`approved`, `rejected`), `confirmed_at`, `executed_at`.
* **Tabelle `ai_autonomy_grants`** ([`AiAutonomyGrant`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_autonomy_grant.py#L36-L70)):
  * `user_id`, `server_id`, `enabled`, `max_actions_per_hour`, `granted_by`.

### 3.7 Sprachmodus & Transkription (Realtime & STT/TTS)
* **WebSocket `/api/ai/voice/ws`** ([`backend/routers/ai_voice.py`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/routers/ai_voice.py)):
  * Nimmt kontinuierliche binäre PCM-Audio-Frames des Benutzermikrofons entgegen.
  * Realtime-Modus: Baut direkte WebRTC- / Sideband-Verbindung zu OpenAI Realtime oder Google Gemini Live (`GeminiLiveSitzung`) auf.
  * Legacy-Modus: Verarbeitet PCM-Audiopuffer via Whisper / STT (`/api/ai/voice/transcribe`), leitet Text an Chatlauf und synthetisiert Sprache über Pipecat / ElevenLabs / OpenAI TTS.
* **Tabelle `ai_usage_events`** ([`AiUsageEvent`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/ai_usage_event.py#L18-L129)):
  * `request_id`, `user_id`, `server_id`, `provider_id`, `model`, `status`.
  * `prompt_tokens`, `completion_tokens`, `cached_tokens`, `cache_write_tokens`, `reasoning_tokens`.
  * **`realtime_text_input_tokens`**, **`realtime_text_output_tokens`**, **`realtime_audio_input_tokens`**, **`realtime_audio_output_tokens`**.
  * **`dictation_seconds`**: Exakte Dauer von Spracheingaben und Diktaten in Sekunden.
  * `reserved_tokens`, `reserved_cost_microunits`, `accounted_tokens`, `accounted_cost_microunits`.

---

## 4. Smart System (Desktop Companion - Tauri/Rust)

Auf dem lokalen Rechner des Nutzers greift das Smart System tief in das Betriebssystem ein und persistiert sensible Daten:

### 4.1 Lokale Audioaufnahmen & Stimm-Biometrie ([`wakeword.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/wakeword.rs))
* **Echte Audio-WAV-Dateien**:
  * Beim Einrichten des Assistenten-Rufnamens spricht der Nutzer den Namen sechsmal ein.
  * Das System schneidet Spracheinsatz und -ende und **speichert 6 echte Mono-WAV-Audiodateien** auf der Festplatte des Nutzers unter:
    `%LOCALAPPDATA%\Singra\wakeword\aufnahme-01.wav` bis `aufnahme-06.wav`.
* **Biometrische Sprachschablone (`wakeword.rpw`)**:
  * Aus den WAV-Dateien erzeugt die Rustpotter-Engine über MFCC (Mel-Frequency Cepstral Coefficients) und Dynamic Time Warping (DTW) ein **biometrisches Sprach-Referenzmodell** des Benutzers und speichert es als `wakeword.rpw`.
  * Eine Versionsdatei `verfahren.txt` dokumentiert das Schnittverfahren.

### 4.2 Windows Hello Biometrie-Schlüssel ([`biometrie.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/biometrie.rs))
* **Windows Credential Manager Integration**:
  * Zur biometrischen Schnellentsperrung des Zero-Knowledge Passwort-Tresors via Windows Hello (Gesichtserkennung, Fingerabdruck oder Windows PIN) wird das Tresor-Geheimnis im Betriebssystem-Tresor abgelegt:
    * Dienst: `MauntingSmartSystem`
    * Konto: **`vault_biometric_key`**
  * Vor dem Zugriff erzwingt die App den nativen Windows Runtime Dialog `UserConsentVerifier::RequestVerificationAsync`.

### 4.3 Bildschirmüberwachung & Screenshots ([`bildschirm.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/bildschirm.rs), [`sichtfeld.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/sichtfeld.rs))
* **Jederzeitige Bildschirmaufnahme**:
  * Seit dem 23.08.2026 darf die KI jederzeit ohne vorherige Klick-Freigabe ein Bildschirmfoto des Hauptmonitors aufnehmen (`xcap`).
  * Das Bild wird auf maximal 1280 Pixel herunterskaliert und als JPEG (Qualität 75) komprimiert.
  * **Sichtfeld-Indikator**: Rust blendet während der Aufnahme ein rotes Indikator-Fenster (`sichtfeld`) auf dem Bildschirm ein. Der Indikator hat bewusst keinen Software-Schalter und steht in keinem Systemprompt.
  * **Tresor-Schutz**: `TRESOR_SCHUTZ_AKTIV` verhindert Screenshots, solange der Passwort-Tresor entsperrt ist.

### 4.4 Lokale Konfiguration & Pfadfreigaben ([`konfig.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/konfig.rs))
Persistiert in `%LOCALAPPDATA%\Singra\konfig.json`:
* `backend_url`: URL des MSM-Servers.
* `sandbox_pfad`: Standard `%USERPROFILE%\MSS-Sandbox`.
* `search_roots`: **Vom Benutzer freigegebene Suchpfade für Spiele und Software**.
* `hotkey_fenster` (Standard `Alt+Space`), `hotkey_sprache` (Standard `Alt+Shift+Space`).
* `audio_eingabe`, `audio_ausgabe`: Konkrete Namen der Mikrofone und Lautsprecher.
* `audio_echo`, `audio_rauschen`, `audio_autogain`, `audio_verstaerkung`.
* `wakeword_aktiv`, `wakeword_wort`, `wakeword_schwelle`.
* `computer_use_aktiv`: Freigabe für Computer-Steuerung (Maus/Tastatur).
* `artifact_install_aktiv`, `max_download_bytes` (10–100 GiB).
* `discord_rpc_aktiv`, `discord_client_id`, `discord_details`, `discord_state`.
* `autostart_aktiv`, `splash_gesehen`.

### 4.5 Discord Rich Presence ([`discord.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/discord.rs))
* Öffnet die lokale Windows Named Pipe `\\.\pipe\discord-ipc-0` bis `9` und sendet JSON-Pakete an den Discord-Client:
  * Application Client ID: `1512525013155057735` (oder benutzerdefiniert).
  * Status-Details und Aktivitäten werden im Discord-Profil des Nutzers angezeigt.

### 4.6 Desktop-Aufträge, Fernsteuerung & Dateiverwaltung
* **Tabelle `desktop_jobs`** ([`DesktopJob`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/desktop_job.py#L36-L105)):
  * `user_id`, `run_id`, `tool_call_id`, `tool_name`, `device_family`.
  * `payload_encrypted`: DIS-verschlüsselte Befehle (z. B. Ordner auflisten, Dateien lesen/schreiben, Maus klick/tippen, Programm starten).
  * `result_encrypted`: DIS-verschlüsselte Rückmeldungen (Dateiinhalte, Verzeichnisbäume, Screenshots, Defender-Ergebnisse, Terminal-Outputs).
* **Maus- und Tastatur-Steuerung** ([`uebernahme.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/uebernahme.rs)):
  * Erfordert explizite Benutzerbestätigung (Frist 1–30 Minuten). Simuliert Klicks (`enigo`) und Tastenanschläge.
* **Dateibereinigung & Papierkorb** ([`aufraeumen.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/aufraeumen.rs), [`zonen.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/zonen.rs)):
  * Misst Verzeichnisse bis zu 12 Ebenen tief.
  * Verschiebt Dateien in den Windows-Papierkorb, löscht Dateien endgültig oder leert den Papierkorb (`SHEmptyRecycleBin`).
* **Software-Artefakte & Quarantäne** ([`artefakt.rs`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/smart-system/src-tauri/src/artefakt.rs)):
  * Downloads bis 100 GiB landen in `%LOCALAPPDATA%\Singra\MSS-Quarantine`.
  * `manifest.json` protokolliert: `artifact_id`, Dateiname, Bytes, SHA-256-Prüfsumme, Defender-Befund, Sandbox-Bericht.
  * `%LOCALAPPDATA%\Singra\MSS-Snapshots`: Erstellt vor Änderungen Dateisystem-Snapshots für atomare Rollbacks.

---

## 5. Persönliche Produktivitäts- & Nutzerdaten

### 5.1 Notizen
* **Tabelle `notes`** ([`Note`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/note.py#L20-L71)):
  * `user_id`, `note_uid` (UUID), `title`, **`content`** (**Klartext-Inhalt der Notiz**).
  * `category` (`personal`, `shopping`, `todo`, `work`, `idea`, `meeting`), `color`, `is_pinned`, `is_archived`, `note_type` (`personal`, `team`), `team_id`.

### 5.2 Kalender & Termine
* **Tabelle `user_calendars`** ([`UserCalendar`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/user_calendar.py#L15-L77)):
  * `user_id`, `name`, `provider_type` (`caldav`, `oauth_google`, `oauth_microsoft`, `local_ics`), `is_default`, `caldav_url`, `caldav_username`.
  * `credentials_encrypted`: DIS-verschlüsseltes CalDAV-Passwort oder OAuth-Refresh-Token.
* **Tabelle `calendar_events`** ([`CalendarEvent`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/calendar_event.py#L20-L75)):
  * `calendar_id`, `user_id`, `event_uid`, `title`, **`description`** (**Klartext-Terminbeschreibung**), **`location`** (**Klartext-Ortsangabe**), `start_time`, `end_time`, `all_day`, `color`, `event_type`, `team_id`, `server_id`.

### 5.3 Externe E-Mail-Postfächer
* **Tabelle `user_mailboxes`** ([`UserMailbox`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/user_mailbox.py#L16-L110)):
  * `user_id`, `name`, **`email`** (**Klartext-E-Mail-Adresse des externen Postfachs**), `provider_type` (`imap_smtp`, `oauth_google`, `oauth_microsoft`).
  * `imap_host`, `imap_port`, `imap_use_ssl`, `imap_username`.
  * `smtp_host`, `smtp_port`, `smtp_use_tls`, `smtp_username`.
  * `credentials_encrypted`: DIS-verschlüsseltes E-Mail-Passwort oder OAuth-Token.
  * `sync_enabled`, `notify_filter_rules_json` (Regeln zur KI-Filterung).

### 5.4 Passwort-Tresor (Zero-Knowledge Vault)
* **Tabelle `vault_user_settings`** ([`VaultUserSetting`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/vault_user_setting.py#L15-L53)):
  * `user_id`, `bucket_id` (64-Zeichen Hex-Hash), `kdf_salt` (Argon2id/PBKDF2-Salt).
* **Tabelle `vault_hints`** ([`VaultHint`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/vault_hint.py#L15-L34)):
  * `user_id`, **`hint`** (`String(512)`): **Vollständiger Klartext-Passworthinweis des Masterschlüssels**! `last_requested_at`.
* **Tabelle `vault_entries`** ([`VaultEntry`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/vault_entry.py#L20-L48)):
  * `bucket_id`, `id`, `ciphertext` (clientseitig verschlüsselter AES-256-GCM Tresoreintrag), `revision`, `is_deleted`.
* **Tabelle `vault_blind_buckets`** ([`VaultBlindBucket`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/vault_blind_bucket.py#L15-L31)):
  * `bucket_id`, `auth_verifier` (SHA-256 des blind abgeleiteten Auth-Tokens).

### 5.5 Popup-Quittierungen
* **Tabelle `user_popup_states`** ([`UserPopupState`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/panel_popup.py#L43-L68)):
  * `user_id`, `popup_id`, `dismissed_permanently` (`Boolean`), `last_dismissed_at` (`DateTime`).
* **Tabelle `panel_popups`**: `created_by_user_id` (wer das Popup erstellt hat).

---

## 6. Social Graph, Messenger & Kommunikation

### 6.1 Freundschaften & Social Graph
* **Tabelle `user_friends`** ([`UserFriend`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/user_friend.py#L14-L45)):
  * `user_id` (FK `users.id`), `friend_id` (FK `users.id`).
  * `status` (`String(16)`): Beziehungsstatus (`pending`, `accepted`, `blocked`).
  * `created_at`, `updated_at`.

### 6.2 E2EE Messenger-Struktur & Schlüssel
* **Tabelle `user_e2ee_devices`** ([`UserE2eeDevice`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/user_e2ee_device.py#L30-L63)):
  * `user_id`, `device_id` (Hex-Zufalls-ID), `public_key_jwk` (Öffentlicher ECDH/X25519 Geräteschlüssel), `label`, `last_seen_at`.
* **Tabelle `direct_chats`** ([`DirectChat`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/direct_chat.py#L14-L51)):
  * `user_a_id`, `user_b_id`, `blind_mailbox_id`, `initiated_by_user_id`.
* **Tabelle `e2ee_blind_envelopes`** ([`E2eeBlindEnvelope`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/e2ee_blind_envelope.py#L14-L39)):
  * `blind_mailbox_id`, `ciphertext_envelope` (Ende-zu-Ende verschlüsselter Umschlag via Double Ratchet), `client_uuid`, `created_at`.
* **Tabelle `chat_groups` & `chat_group_members`** ([`ChatGroup`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/chat_group.py#L19-L68)):
  * Gruppen-Name, Beschreibung, Avatar-URL, Einladungscode (`invite_code`), `owner_user_id`.
  * Mitglieder: `user_id`, `role`, `permissions`, `joined_at`.
* **Tabelle `chat_media`** ([`ChatMedia`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/chat_media.py#L14-L51)):
  * `uploader_user_id`, `ciphertext_blob`, `media_type`, `file_name`, `size_bytes`, `sha256`, `expires_at`.
* **Tabelle `chat_stories` (24h-Status)** ([`ChatStory`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/chat_story.py#L14-L34)):
  * `user_id`, **`content`** (`Text`): **Klartext-Inhalt der Story**, `media_url`, `background`, `expires_at`.

### 6.3 LiveKit (Audio-, Video- & Bildschirmübertragung)
* [`livekit_service.zugangstoken`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/services/livekit_service.py#L243-L265) signiert JWTs mit:
  * `sub` (User-Identität), `name` (Klartext-Anzeigename), `room` (Raum-ID), Metadaten.
  * Audio-, Video- und Desktop-Streams (WebRTC) fließen über den LiveKit-SFU-Server.

---

## 7. Hoster-, Shop- & Mandanten-Identitäten

Das Modul [`hoster.py`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/hoster.py#L44-L260) bildet externe Hosting-Kunden und Shop-Systeme ab:
* **Tabelle `hoster_integrations`**: `name`, `slug`, `service_user_id` (FK `users.id`), `api_key_hash`, `api_key_hint`, `webhook_url`, `webhook_secret_encrypted`.
* **Tabelle `hoster_identities`**:
  * Verknüpft externe Kundenidentitäten dauerhaft mit MSM-Benutzerkonten:
  * `integration_id`, **`external_subject_hash`** (SHA-256 der externen Kunden-ID), **`external_subject_hint`** (z. B. letzte Ziffern der Kundennummer), `user_id` (FK `users.id`), `last_seen_at`.
* **Tabelle `hoster_services`**:
  * Gemietete Server-Instanzen: `external_service_id`, `identity_id`, `product_id`, `server_id`, `granted_role_id` (automatisch verliehene RBAC-Rolle), `desired_state`, `status`, `correlation_id`.
* **Tabelle `hoster_handoffs`**:
  * Kurzlebige Einmal-Auto-Login-Tokens aus dem Shop ins Panel: `integration_id`, `service_id`, `user_id` (FK `users.id`), `token_hash`, `target_path`, `expires_at`.

---

## 8. Audit-Logs, Operation Tasks & Webhooks

* **Tabelle `audit_logs`** ([`AuditLog`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/audit_log.py#L9-L45)):
  * Protokolliert dauerhaft privilegierte Aktionen: `user_id`, `action` (`user.login`, `server.files.read`, `server.console.read`, `server.logs.read`, `backup.restore`), `target_type`, `target_id`, `origin` (`direct`, `ai`, `external`, `system`), `correlation_id`, `details` (bis 500 Zeichen Parameter, Dateinamen, Einstellungen).
* **Tabelle `operation_tasks`** ([`OperationTask`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/operation_task.py#L19-L91)):
  * Speichert Hintergrundoperationen des Benutzers: `id`, `task_type`, `actor_user_id` (FK `users.id`), `origin`, `correlation_id`, `idempotency_key_hash`, `request_hash`, `status`, `phase`, `server_id`, `error_code`, `error_message`, Timestamps.
* **Tabelle `webhook_subscriptions` & `webhook_deliveries`** ([`WebhookSubscription`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/webhook_subscription.py#L23-L86)):
  * `server_id`, `label`, `target_url`, `secret_hash`, `secret_hint`, `secret_encrypted`, `event_filter`, `last_delivery_status`, `last_response_code`. Deliveries protokollieren gesendete Payloads und Response-Codes.
* **Tabelle `singra_webhook_events`** ([`SingraWebhookEvent`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/models/singra_webhook_event.py#L11-L18)):
  * Inbound Support-Widget Webhook Deduplikation (`event_id`, `received_at`).

---

## 9. Client-seitige Speicherung (Browser & App)

### 9.1 HTTP-Cookies ([`cookies.py`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/cookies.py#L9-L28))
* **`__Secure-access_token`**: HttpOnly, Secure, SameSite=Lax/None, Path `/api` (JWT mit `sub`, `user_id`, `jti`, `familie`, `geraet`).
* **`__Secure-refresh_token`**: HttpOnly, Secure, SameSite=Strict/None, Path `/api/auth`.
* **`__Secure-csrf_token`**: Secure, SameSite=Strict/None, Path `/` (nicht HttpOnly, für Double-Submit).
* **`__Secure-oauth_state`**: HttpOnly, Secure, SameSite=None, Path `/` (OAuth-State).

### 9.2 Browser LocalStorage
* **Offline-Caches (Unverschlüsselter Klartext!)**:
  * **`msm_offline_notes`**: **Vollständiger Klartext-Cache sämtlicher Notizen** des Benutzers (Titel, Inhalte, Kategorien, Timestamps) ([`offlineSync.ts:L20`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/lib/offlineSync.ts#L20)).
  * **`msm_offline_calendar`**: **Vollständiger Klartext-Cache sämtlicher Kalendertermine** (Titel, Beschreibungen, Orte, Zeiten).
  * **`msm_offline_outbox`**: Offline-Warteschlange aller noch ungesendeten Notizen- und Kalender-Mutationen inklusive Klartext-Payloads.
  * **`msm_offline_last_sync`**: ISO-Zeitstempel der letzten Synchronisation.
* **Benutzerkonto & Berechtigungen**:
  * **`msm_cached_user`**: **Vollständiges serialisiertes `User`-Objekt** im Klartext (ID, Username, E-Mail-Hash, Rollen-IDs, Zeitzone, KI-Präferenzen, Systembereich, Avatar-URL) ([`authStore.ts:L15-L39`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/stores/authStore.ts#L15-L39)).
  * **`msm_cached_permissions`**: Vollständiges RBAC-Rechteobjekt (`global_keys`, `server_keys`) ([`permissionsStore.ts:L6-L30`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/stores/permissionsStore.ts#L6-L30)).
* **Messenger-Metadaten & Sozialstruktur**:
  * **`msm:chat_mutes`**: Stummschaltungen von Chats inklusive Ablaufzeitstempel ([`messengerNotificationStore.ts:L16`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/stores/messengerNotificationStore.ts#L16)).
  * **`msm:chat_blocks`**: Liste aller blockierten Benutzer-IDs.
  * **`msm:chat_blocked_profiles`**: Zwischengespeicherte Profile blockierter Nutzer (`userId`, `username`, `avatarUrl`).
  * **`msm:chat_unread`**: Ungelesene Nachrichtenzähler je Chat-Mailbox.
  * **`msm:chat_mailbox_dir`**: Vollständiges Verzeichnis aller bekannten Mailboxen (`name`, `avatarUrl`, `isGroup`, `userId`, `groupId`).
  * `msm_read_receipts_enabled`: Schalter für Lesebestätigungen.
  * `msm_chat_wallpaper`: Gewähltes Hintergrundbild.
* **Datenschutz & Einwilligungen**:
  * `cookie_consent`: Cookie-Banner-Zustand (`{"optional": true/false}`).
  * `msm.privacyNotice.dismissed`: Quittierung der Datenschutzerklärung (`{"acknowledged": true, "version": "2.0.0", "at": "<ISO-Date>"}`).
  * `i18nextLng`: Sprachauswahl (nur wenn im Cookie-Consent erlaubt).
* **System- & SQL-Verlauf**:
  * `msm_sql_history`: **Vollständige Historie aller in der Datenbankkonsole ausgeführten SQL-Abfragen**.
  * `msm_sql_favorites`: Favorisierte SQL-Abfragen.
  * `msm_version_cache`: Installierte Serverversion + Zeitstempel.
  * `theme`: Farbdesign (`dark`, `light`, `cyberpunk`).
* **Audio-Hardware-Präferenzen**:
  * `msm_audio_preferred_mic_id`, `msm_audio_preferred_speaker_id`.
  * `msm_audio_noise_suppression`, `msm_audio_echo_cancellation`, `msm_audio_auto_gain_control`, `msm_audio_mic_gain`.
* **KI-Präferenzen**:
  * `msm_ai_chat:provider:<userId>`, `msm_ai_chat:reasoning:<userId>`, `msm_ai_chat:closedGeoAnalysis:<userId>`.
* **Passwort-Tresor Client-Cache**:
  * `mss:vault_salt`, `mss:vault_canary`, `mss:vault_server_bucket`.
  * `mss:vault_autolock_minutes`, `mss:vault_lock_on_blur`, `mss:vault_biometrics_enabled`.
  * `mss:vault_entries_cache:<bucketId>`: Lokaler Cache der verschlüsselten Tresoreinträge.
  * `mss:vault_pending_queue:<bucketId>`: Offline-Warteschlange für Tresor-Änderungen.

### 9.3 Browser SessionStorage
* **`msm:active_messenger_user_id`**: **Benutzer-ID des aktuell im Chat ausgewählten Kontakts** ([`pages/Messenger.tsx:L1086`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/pages/Messenger.tsx#L1086)).
* **`msm:active_messenger_group_id`**: Gruppen-ID der aktuell ausgewählten Chatgruppe.
* **`msm:delivered_envelope_ids`**: Bis zu 500 IDs zugestellter Nachrichten-Umschläge zur Quittungs-Deduplikation ([`deliveryReceiptService.ts:L14-L40`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/services/deliveryReceiptService.ts#L14-L40)).
* **`msm_alerted_incidents`**: Bis zu 100 Vorfall-UUIDs bereits gemeldeter Servervorfälle ([`ServerIncidentNotifier.tsx:L43-L60`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/components/notifications/ServerIncidentNotifier.tsx#L43-L60)).
* **`msm_alerted_reminders`**: Bis zu 100 IDs bereits angezeigter Kalender-Terminerinnerungen.

### 9.4 Browser IndexedDB (`msm_messenger_local`)
* **Store `messages`** ([`messengerLocalStore.ts:L14-L53`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/frontend/src/services/messengerLocalStore.ts#L14-L53)):
  * **Vollständiger entschlüsselter Nachrichtentext** (`text`, `originalText`).
  * `id`, `blindMailboxId`, `clientUuid`, `senderId`, `senderName`, `createdAt`, Flags (`isRead`, `isDelivered`, `isEdited`, `isDeleted`).
  * Anhänge (Notizen, Termine, Medien, Sticker, Story-Antworten).
* **Store `mailboxes`**: Synchronisationsfortschritt je Chat.
* **Store `envelope_plaintexts`**: Zwischengespeicherte Klartexte.
* **Kryptografische Ratchet-Schlüssel**: Privater Geräteschlüssel (ECDH), Double-Ratchet Session-Ketten und symmetrische Gruppenschlüssel.

---

## 10. Externe Drittsysteme & Datenweitergabe

| Empfänger / Dienst | Übertragene Benutzerdaten | Anlass / Zweck |
| :--- | :--- | :--- |
| **KI-Provider** (OpenAI, Anthropic, OpenRouter, DeepSeek, Google Gemini) | Vollständige Prompts, Klartext-Nachrichten, Server-Logs, Dateiinhalte, Benutzerfakten aus dem Gedächtnis, Zeitzone, Assistentenname, Audio-Streams (Realtime / STT) | Chat, Sprachmodus, Reparaturen, geplante Aufgaben |
| **S3 Object Storage** (AWS, Cloudflare R2, MinIO, Backblaze B2, Wasabi) | **Vollständige Server- und Panel-Backups** (enthält alle oben genannten Datenbanktabellen im Dump) | Geplante Cloud-Backups via [`s3_service.py`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/services/s3_service.py) |
| **Brave Search / SearXNG** | Suchbegriffe des Nutzers bzw. der KI | KI-Websuche ([`ai_web_search_service.py`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/services/ai_web_search_service.py#L48-L54)) |
| **OpenStreetMap & Open-Meteo** | Ortsnamen / Geokoordinaten | Wetter- und Ortsabfragen der KI ([`ai_geo_service.py`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/services/ai_geo_service.py#L27-L29)) |
| **MapTiler** | Karten-Kacheln & Geo-Koordinaten | Kartendarstellung bei Geo-Abfragen |
| **Copernicus / Sentinel** | Bounding-Boxes / Geokoordinaten | Satellitenbild-Abfragen der KI |
| **Cloudflare Turnstile / hCaptcha / reCAPTCHA** | Client-IP-Adresse, Captcha-Token | Registrierung, Login, Reset |
| **OAuth-Provider** (Google, GitHub, Microsoft) | IdP-Subjekt, E-Mail-Adresse, Benutzername | Social-Login / Account-Linking |
| **LiveKit-SFU-Server** | User-ID, Anzeigename, Raum-ID, Audio-, Video- und Screenshare-Streams | Audio-/Videoanrufe |
| **CurseForge API** | Mod-IDs, Spiel-Versionen | Mod-Downloads für Spieleserver |
| **Support-Widgets** (Crisp, Tawk.to, Singra) | Client-IP, Browser-Metadaten, Support-Chats | Wenn im Panel vom Admin aktiviert |
| **SMTP-Server** | Empfänger-E-Mail, Login-Benachrichtigung mit IP und User-Agent, generierte KI-Mails | System-Mails und Benachrichtigungen |
| **Discord (Lokal IPC)** | Status, Servername, MSS Desktop Status | Discord Rich Presence |
| **Ausgehende Webhooks** | Server-Status, Vorfälle, Spieler-Updates | Monitoring / Discord-Bots |

---

## 11. Dateisystem-Ablage auf dem Host-Server (`/opt/msm`)

* **`/opt/msm/data/avatars/`**: Profil- und Gruppenbilder (`avatar_<id>_<hash>.png/jpg/webp`).
* **`/opt/msm/servers/<id>/`**: Sämtliche Game-Server-Dateien, Spielstände, Konfigurationen, Chatprotokolle und Logdateien (z. B. ASA, Minecraft, Palworld).
* **`/opt/msm/backups/`**: Lokale Archive der Gameserver (.tar.gz / verschlüsselte .enc-Dateien).
* **`/opt/msm/backups/panel/`**: Vollständige Panel-Backups inklusive SQLite/Postgres-Datenbank-Dump (enthält sämtliche Benutzerdaten, Hashes, Chats) und `.env`-Konfiguration ([`panel_backup_service.py`](file:///C:/Users/einma/.gemini/antigravity/worktrees/maunting-server-manager/audit_stored_user_data/backend/services/panel_backup_service.py)).

---

## 12. Korrektur und Detail-Vergleich zum Vorbericht

| Bereich | Befund im Vorbericht | Tatsächlicher Code-Befund (Korrektur & Ergänzung) |
| :--- | :--- | :--- |
| **Freundschaften & Social Graph** | Nicht erwähnt. | **Tabelle `user_friends`** existiert: speichert Freundschaften, Anfragen und Blockierungen (`user_id`, `friend_id`, `status`). |
| **Globale Rollen (RBAC)** | Nur `users.role_id` erwähnt. | **Tabelle `user_roles`** existiert: speichert Multi-Rollen-Zuweisungen pro Benutzer (`user_id`, `role_id`, `assigned_at`). |
| **Granulare Server-Rechte** | Nicht erwähnt. | **Tabelle `server_permissions`** existiert: speichert feingranulare Pro-User-Pro-Server-Rechte (`granted_by`, `permission_key`). |
| **Session-Sicherheit** | Nur `refresh_tokens` erwähnt. | **Tabelle `jwt_blacklist`** existiert: speichert abgemeldete/revokierte JWT-Access-Tokens (`jti`, `user_id`, `expires_at`). |
| **Popup-Interaktionen** | Nicht erwähnt. | **Tabelle `user_popup_states`** existiert: speichert Quittierungsstatus von Admin-Popups je Benutzer. |
| **KI-Fähigkeiten & Reparaturen** | Nicht erwähnt. | **Tabellen `ai_skills`, `ai_guardian_notices` und `ai_guardian_repairs`**: Speichern erlernte Handlungsanweisungen, Reparaturphasen, Quarantäne-Aufhebungen und Vorfall-Erkenntnisse. |
| **Hoster- & Shop-Integration** | Nicht erwähnt. | **Tabellen `hoster_integrations`, `hoster_identities`, `hoster_services`, `hoster_handoffs`**: Verknüpfen externe Kundennummern dauerhaft mit MSM-Konten. |
| **Audioaufnahmen auf dem PC** | Nur als „flüchtiges Mikrofon-Lauschen“ bezeichnet. | **6 echte WAV-Audiodateien** (`aufnahme-01.wav` .. `06.wav`) sowie das biometrische DTW-Modell (`wakeword.rpw`) werden **dauerhaft auf der Festplatte des Nutzers** gespeichert. |
| **Windows Hello Biometrie** | Nicht erwähnt. | **`vault_biometric_key`** wird im Windows Credential Manager für biometrisches Entsperren des Tresors hinterlegt. |
| **Browser LocalStorage** | Nur Einstellungen/Themen genannt. | **Klartext-Speicherung**: `msm_offline_notes`, `msm_offline_calendar`, `msm_offline_outbox`, `msm_cached_user`, `msm_cached_permissions`, `msm:chat_mutes/blocks/mailbox_dir`. |
| **Browser SessionStorage** | Komplett gefehlt. | Speichert aktive Chatpartner (`msm:active_messenger_user_id`), Gruppen, bis zu 500 Empfangsbestätigungen (`msm:delivered_envelope_ids`) und Vorfälle. |
| **Cloud-Backups** | Nur lokale Pfade `/opt/msm` genannt. | Vollständige Datenbank-Dumps werden über **S3-Object-Storage** an externe Cloud-Anbieter übertragen. |

---

## Remaining Questions & Gaps

* **Verhalten bei Shared Multi-User Desktops**:
  * Da `msm_offline_notes` und `msm_offline_calendar` im Klartext im `localStorage` des Browsers liegen und erst bei explizitem Logout geräumt werden, können andere Benutzer desselben Betriebssystem-Accounts (bei geteilten Browser-Profilen) Notizen und Termine ohne erneute Passworteingabe im Browser-Entwicklertools-Speicher einsehen.
* **Klartext-Passworthinweis im Tresor (`vault_hints.hint`)**:
  * Während Tresoreinträge Zero-Knowledge-verschlüsselt sind, liegt der Passworthinweis des Masterschlüssels in `vault_hints.hint` unverschlüsselt in der Datenbank.
* **Chat-Stories Klartext (`chat_stories.content`)**:
  * Im Gegensatz zu Chatnachrichten (E2EE) und Anhängen liegt der Text von 24h-Stories (`chat_stories.content`) unverschlüsselt in der Datenbank.
* **Empfohlener Fokus für nächste Untersuchungen**:
  * Überprüfung der Bereinigungsroutinen von `localStorage` bei reinem Schließen des Browser-Tabs (ohne expliziten Klick auf „Abmelden“).
  * Validierung, ob der DIS-Sidecar bei stark ausgelasteten Servern Swap-Speicher nutzt und ob dort unverschlüsselte Keys im RAM-Abbild des Kernels verbleiben können.
