## Conan Exiles (UE5) – Mod-Listing & Server-Stabilität

- **`ServerModList=modlist.txt`** in der UE5-Config und **`*`-Präfix** für jede `.pak`-Zeile in `ConanSandbox/Mods/modlist.txt`. Mods werden jetzt zuverlässig an Clients gemeldet; Mod-Abgleich und Modded-Server-Listing wieder aktiv.
- **`update_modlist`** läuft beim Start, damit frische `*.pak` automatisch in die Mod-Liste kommen.
- **`-MULTIHOME={BIND_IP}`** aus dem Blueprint entfernt. Im Docker-Bridge-Modus existiert die Public-IP auf keinem Interface → Bind schlug mit `EADDRNOTAVAIL` fehl und führte zu `SIGSEGV` (Crash) statt stabilem Game-Server.
- **`net.AllowEncryption=0`** aus dem Blueprint entfernt. Der Server-Patch erzeugte einen Mismatch zum Standard-Client (Verschlüsselung an) und wirkte wie „falsches Passwort / keine Verbindung“. Conan-Server läuft jetzt mit Standard-Verschlüsselung; Spieler müssen nichts am Client ändern.

## Workshop-Mods – Panel-Stabilität

- **QueuePool-Exhaustion behoben:** Mod-Background-Tasks (`install_mod_bg`, `reinstall_all_mods_bg`) erwerben den Install/Update-Lock **vor** dem Öffnen der DB-Session. Wartende Jobs blockieren keine Connection mehr, parallele Subscribes auf einem frisch erstellten Server laufen sauber durch.
- **`GET /api/mods/{id}`** überspringt den schweren Workshop-Update-Refresh, solange Mods `pending`/`installing` sind (Polling wird billig).
- **DB-Pool** auf `pool_size=10, max_overflow=20, pool_timeout=60` (für SQLite unverändert – Singleton-Pool).
- **Fortschritts-Parser:** SteamCMD-Zeilen wie `Update state (0x61) downloading, progress: 4.43 (209952633 / 4736388301)` werden jetzt korrekt geparst (akzeptiert `progress, NN` und `progress: NN`). UI-Balken füllt sich, ETA erscheint nach den ersten Zeilen.

## Conan-Mods: Workshop-Copy

- **`shutil.copy2` Fallback** auf `copyfile`, wenn unter Rootless Docker `EPERM` (Bind-Mount / `utime`) auftritt. SteamCMD-Download bleibt erfolgreich, das `*.pak` landet trotzdem im `ConanSandbox/Mods/`-Ordner – kein endloses „Update ausstehend“ mehr nach Mod-Updates.

## Tests

- `test_mods_pool_stability.py`: Lock-vor-Session, `list_mods` überspringt Refresh bei aktiven Jobs, Refresh läuft weiter wenn idle.
- `test_mod_progress_parser.py`: 6 Cases gegen reale SteamCMD-Zeilen (Update-State + Bytes + Klammer-%).
- Bestehende Tests für Conan-Modlist/Blueprint-Plugin/Mods-Router weiterhin grün.

## Upgrade / Deploy

- `git pull` auf `main` (Tag `v1.2.7`), `npm run build` (Frontend), `systemctl restart msm-panel`.
- Bestandsserver: einmaliger Container-Neustart aktiviert den neuen Startup ohne MULTIHOME; `Engine.ini`/`ServerSettings.ini` werden durch `prepare_runtime` korrekt geschrieben (`ServerModList=modlist.txt`, `*`-Modlist).
- Keine Secrets in Logs/Diffs/UI; keine neuen Dependencies.

**Full Changelog**: https://github.com/einmalmaik/maunting-server-manager/compare/v1.2.6...v1.2.7