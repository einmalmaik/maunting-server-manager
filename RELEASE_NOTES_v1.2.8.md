## ARK: Survival Ascended – Native Blueprint

- **Neuer nativer Blueprint** für **ARK: Survival Ascended** (Steam AppId `2430930`, UE5, Island/TheIsland auf Linux-Server).
- Nutzt den vorhandenen **`ue5_steamcmd_bridge`**-Container und die geteilten Conan-UE5-Fixes (`ServerModList=modlist.txt`, `*`-Modlist-Prefix, ohne `-MULTIHOME`, ohne `net.AllowEncryption=0`-Patch) — keine neuen Container, keine neuen Dependencies.
- Workshop-Mods funktionieren über den bereits eingebauten Steam-Workshop-Pfad.
- Backups sind **blueprint-scoped** (nur `Config/`, `Saved/`, `ShooterGame/Saved/SavedArks/` + Manifest) — keine `Engine/Binaries/Linux`-Müll-Snapshots.
- Auto-Restart (Interval **oder** Fixed-Times, exklusiv) und Pool-Tuning aus v1.2.7 greifen auch für ASA.

## Tests

- Bestehende Tests weiterhin grün (UE5-Bridge, Conan-Modlist, Mods-Pool, Mod-Progress-Parser, Auto-Restart-Integration, SCUM 0x226-Recovery).
- ASA-Blueprint validiert gegen `schema.py` (gleiche Struktur wie Conan-UE5/Conan-Exiles).

## Upgrade / Deploy

- `git pull` auf `main` (Tag `v1.2.8`), `npm run build` (Frontend), `systemctl restart msm-panel`.
- Neue ASA-Server können direkt im Panel unter *Blueprints → ARK: Survival Ascended* erstellt werden.
- Keine Secrets in Logs/Diffs/UI; keine neuen Dependencies.

**Full Changelog**: https://github.com/einmalmaik/maunting-server-manager/compare/v1.2.7...v1.2.8
