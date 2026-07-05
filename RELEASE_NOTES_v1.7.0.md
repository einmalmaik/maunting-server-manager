# MSM v1.7.0 — Database Admin Console + Backup-Optimierung + Recovery-App v0.2.0

Release mit umfassender Database Admin Console Überarbeitung, Backup-Ausschluss-Logik,
Recovery-App Performance-Fix und ZIP-Save, sowie Favicon- und UI-Korrekturen.

## Highlights

### Database Admin Console: Cleanup + neue Features
Die gemeinsame `DatabaseConsole.tsx` (genutzt von Panel-Datenbank und Server-PostgreSQL)
wurde überarbeitet: tote Tabs entfernt, neue Toolbar-Features hinzugefügt, DNA-Komponenten
integriert und DB-User-Management eingebaut.

**Entfernt (keine Funktion, irreführend oder redundant):**
- Backups-Tab (redundant mit separater Panel-Backups-Seite)
- Logs-Tab (keine Funktion, keine Backend-Unterstützung)
- Monitoring-Tab (keine Live-Daten, Stats bereits als Metric-Cards oben)
- Einstellungen-Tab (keine Verbindungseinstellungen, keine Backend-Unterstützung)
- Benutzer-Tab als statischer Platzhalter (durch funktionales DB-User-Management ersetzt)

**Neu hinzugefügt:**
- Filter-, Sortieren- und Spalten-Buttons in der Tabellen-Toolbar (client-seitig auf geladenen Daten)
- Multi-Select für Tabellenzeilen (Select-All, individuelle Auswahl, Auswahl-Indikator)
- Custom `Dropdown`-Komponente aus der Design-DNA ersetzt alle native `<select>`-Elemente
  (Datenbank-Selector, Filter-/Sort-Spalten-Selektor, Benutzer-Seite, Server-Permissions)
- DB-User-Management für Server-PostgreSQL: Benutzer erstellen, Passwort rotieren, löschen
  (mit Bestätigung und One-Time-Credential-Anzeige)
- Datenbank-Löschung im Dropdown (mit Bestätigung + Namen-Typ-Check)

**Repariert:**
- Superuser-Sektion wird nur gerendert, wenn `canManagePowerUser=true` (nur Server-DBs mit Admin)
- "Leeren"-Button umbenannt in "Tabelle löschen" (Klarheit, da es DROP TABLE macht)
- Checkbox-Spalte ohne Funktion entfernt, durch funktionales Multi-Select ersetzt
- Nicht-funktionaler Filter-Button aus der Sidebar entfernt

### Backup-System: Ausschluss-Logik
Server-Backups (`create_full_backup_tar`) packen jetzt nicht mehr `node_modules`,
`__pycache__`, `.cache`, `.npm`, `.git`, `.pytest_cache`, `dist`, `build`, `target` und
`.log`/`.pyc`-Dateien mit ein. Dies reduziert die Backup-Größe deutlich und verhindert
das Backuppen von regenerierbaren Caches und Build-Artifacts.

### Recovery App v0.2.0: Performance + Progress + ZIP-Save
- **Performance-Fix**: `Array.from(data)`-Flaschenhals in `writeTempFile` ersetzt durch
  `@tauri-apps/plugin-fs` `writeFile`. Binary-Daten gehen direkt durch, ohne JSON-
  Serialisierung von 50M+ Zahlen. Reduziert "Minuten oder Stunden" auf Sekunden.
- **Fortschrittsanzeige**: Echte prozentuale Fortschrittsanzeige während Entschlüsselung
  (Stage 1: Schlüssel-Ableitung, Stage 2: Frame-by-Frame-Entschlüsselung mit %,
  Stage 3: Extraction indeterminate).
- **ZIP-Save**: "Dateien speichern" erstellt jetzt eine `.zip`-Datei statt einzelne
  entpackte Dateien. File-Save-Dialog mit ZIP-Filter.

### Favicon-Korrektur
`favicon.ico` war ein falsches 32x32-Platzhalter-Icon (6 VGA-Farben, 66% schwarz).
Neu generiert aus `logo.png` (16x16, 32x32, 48x48 Multi-Size-ICO). Service Worker
Cache von `msm-v3` auf `msm-v4` gebumpt.

### Architektur: Zentrale Design-Komponenten
`AGENTS.md` aktualisiert: `frontend/src/Singra/UI/` als zentrales Verzeichnis für
projektübergreifend wiederverwendbare Design-Komponenten deklariert. Vor jeder
Frontend-Arbeit ist dort zuerst nachzuschauen. `DatabaseConsole.tsx` in die
MauntingStudios Design-DNA kopiert als Referenz für andere Projekte.

## Geänderte Bereiche

### Frontend
- `Singra/UI/DatabaseConsole.tsx`: Tabs cleanup, Toolbar (Filter/Sort/Spalten),
  Multi-Select, `Dropdown`-Integration, `UsersPanel`, `onDeleteDatabase`-Prop
- `components/server/DatabaseManager.tsx`: `deleteDatabase`, `createUser`,
  `rotateUser`, `deleteUser` Handler, neue Props durchgereicht
- `components/ui/Checkbox.tsx` (neu): aus Design-DNA adaptiert
- `components/ui/Dropdown.tsx`: unverändert (bereits vorhanden, jetzt breiter genutzt)
- `pages/Users.tsx`: native `<select>` → `Dropdown` (Server- + Rollen-Selector)
- `components/ServerPermissionsPanel.tsx`: native `<select>` → `Dropdown`
- `pages/PanelDatabase.tsx`: unverändert (kein DB-User-Management für Panel-DB)
- `public/favicon.ico`: neu generiert aus `logo.png`
- `public/sw.js`: Cache-Version `msm-v3` → `msm-v4`

### Backend
- `services/backup_paths.py`: `_BACKUP_EXCLUDE_DIRS` + `_BACKUP_EXCLUDE_SUFFIXES`,
  Filterung in `create_full_backup_tar()` und `create_selective_backup_tar()`
- `tests/test_backup_paths.py`: 2 neue Tests für Ausschluss-Logik

### Recovery App (v0.1.0 → v0.2.0)
- `src-tauri/tauri.conf.json`: Version `0.1.0` → `0.2.0`
- `src-tauri/Cargo.toml`: `zip = "2"` Crate hinzugefügt
- `src-tauri/src/commands.rs`: `save_as_zip` Command + `add_dir_to_zip` Helper
- `src-tauri/src/lib.rs`: `save_as_zip` registriert
- `src-tauri/capabilities/default.json`: `fs:allow-temp-write-recursive` Permission
- `src/lib/tauri-commands.ts`: `writeTempFile` mit `@tauri-apps/plugin-fs`,
  `saveAsZip` Wrapper
- `src/lib/decrypt.ts`: `onProgress` Callback für `decryptFrames` + `decryptBackup`
- `src/components/ProgressBar.tsx`: Determinate-Mode mit Prozent-Anzeige
- `src/components/SaveButton.tsx`: File-Save-Dialog + `saveAsZip`
- `src/App.tsx`: Progress-State verdrahtet
- `src/styles.css`: `.msm-progress-fill-determinate` Klasse
- `src/i18n/de.ts` + `en.ts`: `progress.deriving`, `save.dialog.title`, `save.success`

### Build & Deploy
- `.github/workflows/recovery-release.yml`: unverändert (bereits vorhanden seit v1.6.0)
- Build via `recovery-v*` Tag trigger

## Bug Fixes

- `fix(ui)`: Superuser-Sektion in Panel-Datenbank zeigte "Nicht verfügbar" statt
  gar nicht zu erscheinen
- `fix(ui)`: "Leeren"-Button war missverständlich (DROP TABLE, nicht TRUNCATE)
- `fix(ui)`: Checkbox-Spalte in Datentabelle hatte keine Funktion
- `fix(favicon)`: Favicon war falsches Platzhalter-Icon, nicht MSM-Logo
- `fix(backup)`: `node_modules` und andere Caches wurden in Server-Backups gepackt
- `fix(recovery)`: `Array.from(data)` in `writeTempFile` verursachte extrem lange
  Wartezeiten bei großen Backups
- `fix(recovery)`: Keine Fortschrittsanzeige während Entschlüsselung

## Breaking Changes

Keine. Alle Änderungen sind additiv oder UI-Korrekturen. Bestehende Backups und
Datenbanken funktionieren unverändert.

## Upgrade-Hinweise

```bash
# 1. Pull
cd /opt/msm && git pull

# 2. venv synchronisieren (keine neuen Python-Pakete in diesem Release)
sudo bash scripts/sync-venv.sh

# 3. Panel restarten
sudo systemctl restart msm-panel
```

Recovery-App v0.2.0 Build:

```bash
# Tag pushen (triggert recovery-release.yml)
git tag recovery-v0.2.0
git push origin recovery-v0.2.0
```

— Maunting Studios
