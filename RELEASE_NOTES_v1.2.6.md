## Blueprints: GitHub-Quelle

- **Neu:** `source.type: github` mit Branch, Auto-Pull und optionalen **Startup-Profilen** (verschiedene Startbefehle pro Profil).
- Download-Template und Validierung im Blueprint-Schema; Tests für GitHub-Source.

## Workshop-Mods

- **Start/Restart:** veraltete Workshop-Mods werden beim Lifecycle mitinstalliert (`only_auto_update` nur noch für Scheduler-Hintergrundjobs).
- **Conan UE5:** nach `.pak`-Kopie wird **ExtractedMods**-Cache bereinigt; **installed** nur wenn Workshop **und** Runtime-Paks (`postInstall`) vorhanden sind.
- **Stale `pending`:** wenn Workshop-Dateien schon da sind, kein hängender Job → Status wird auf **installed** gesetzt.
- **Mod-Manager:** **Alle neu installieren** (sequenziell), Live-Fortschritt aus SteamCMD (`[ 96%]`), **Installation abbrechen** bei hängenden Jobs.
- **Reconcile** nach Neustart des Panels bei feststeckendem `installing`.

## Backups

- **Blueprint-scoped:** `backup.includePaths` sichert nur Config/Saves (z. B. Conan `Saved/Config`, `SaveGames`, `game.db`; DayZ `serverDZ.cfg`, `profiles`).
- Archiv enthält `.msm/backup-manifest.json`; **Restore** selective vs. volles Verzeichnis je nach Manifest.
- **Panel:** Blueprint-Hilfe (DE/EN) inkl. Backup-Abschnitt; Backup-Dialog-Hinweis.

## Conan Exiles (UE5)

- Start: **`-MULTIHOME={BIND_IP}`** und **`net.AllowEncryption=0`** im Blueprint (Direct Connect / UE5-Join-Probleme).
- **Permission-Repair** beim Start bricht nicht mehr ab, wenn einzelne `chmod` unter Rootless Docker fehlschlagen; **chown** nach Mod-Kopie (best effort).

## Lifecycle & Panel

- **Stuck `starting`** nach Panel-Neustart wird bereinigt; bessere UX vor Start-Backup.
- Blueprint-Doku: Updates, Backups, Troubleshooting (Permission-Repair).

## Für Betrieb

- Nach Deploy: `npm run build` (Frontend), **`systemctl restart msm-panel`** (User `msm`, Dateien **644**).
- Conan mit vielen Mods: bei fehlenden `.pak` in `ConanSandbox/Mods` **Reinstall all** oder einzelne Mods; Root-owned Paks ggf. auf Container-UID chownen.
- Alte Voll-Backups unter `/opt/msm/backups/` werden nicht automatisch gelöscht.

**Full Changelog**: https://github.com/einmalmaik/maunting-server-manager/compare/v1.2.5...v1.2.6