## GitHub-PAT im Panel — Private Repos für `source.type=github`

- **Neuer Tab „GitHub"** unter *Einstellungen*. Hinterlegung eines panel-weiten
  **Personal Access Tokens** (PAT) für Blueprints vom Typ `source.type=github`.
- **Reihenfolge** der Token-Auflösung (KISS):
  1. `MSM_GITHUB_CLONE_TOKEN` (ENV) — gewinnt
  2. Panel-Datenbank (`settings.github_clone_token`)
  3. *kein Token* — Public Repos funktionieren weiterhin ohne Token
- **Endpunkte** (alle unter `panel.settings.read` / `.write`, CSRF-geschützt):
  - `GET /api/settings/github-token` → Status (`{configured, source}`), **niemals** Token-Echo
  - `POST /api/settings/github-token` → setzen (leer/Control-Chars/>512 abgewiesen)
  - `DELETE /api/settings/github-token` → Panel-Token entfernen
  - `POST /api/settings/github-token/test` → Live-Check via GitHub `/user` API
- **Funktioniert sofort** in `blueprints/github_source._clone_url` und
  `remote_branch_sha` → auch das **Update-Detection** (`ls-remote`) sieht jetzt
  private Repos korrekt.
- **UI:** `GitHubTab.tsx` analog zu `SteamTab` — Status-LED, Quellen-Label
  („ENV" vs. „Panel-DB"), Passwort-Eingabe, Test/Save/Remove mit RBAC.
- **Doku:** `BlueprintsDocs.tsx` zeigt einen blauen „Private Repositories"-
  Hinweis direkt unter dem `source.github`-Field, DE + EN angepasst.

## Sicherheit

- Token wird **nie** ans Frontend zurückgegeben — weder im Status noch in
  Responses.
- Trim + Längenlimit (≤512) + Reject bei `\n`/`\r`/`\0`.
- Errors maskieren Token in URLs (`x-access-token:***@…`).
- Token liegt in der DB im gleichen `panel_settings`-Bucket wie SMTP/Resend.

## Tests

- **18 neue Tests**, alle grün:
  - `test_github_token_service.py` — 5 Service-Unit-Tests (Resolution-Reihenfolge,
    ENV schlägt Panel, Whitespace, Clear).
  - `test_panel_settings_router.py` — 11 neue `TestGitHubTokenEndpoints`
    (RBAC, CSRF, Persistenz, Validierung, ENV-Wins, kein Token im Response).
  - `test_blueprint_github_source.py` — 2 neue Tests (`_clone_url` mit/ohne
    Panel-Token).
- Bestehende Backend-Tests, die vor v1.2.9 grün waren, bleiben grün.
- Frontend: `tsc` + `vite build` grün; **135 Vitest-Tests in 25 Files** grün.

## Upgrade / Deploy

- `git pull` auf `main` (Tag `v1.2.9`), `npm run build` (Frontend),
  `systemctl restart msm-panel`.
- Bestehende `MSM_GITHUB_CLONE_TOKEN`-Setups funktionieren unverändert
  (ENV schlägt Panel).
- Keine neuen Dependencies, keine Migrations, keine Schema-Änderungen an
  `panel_settings` (DB nutzt bestehende `key/value`-Tabelle).

**Full Changelog**: https://github.com/einmalmaik/maunting-server-manager/compare/v1.2.8...v1.2.9
