# MSM v1.7.10 — Palworld-Server & zuverlässige Steam-Einstellungen

Kleines Patch-Release: neuer **Palworld**-Servertyp im Panel und Steam-Einstellungen,
die nach dem Speichern nicht mehr „vergessen“ wirken.

## Highlights

### Palworld (Linux, nativ)
- Neuer Blueprint **Palworld** in der Spielauswahl beim Server anlegen.
- Dedicated Server über Steam (Linux, ohne Wine/Proton im Blueprint).
- **Steam Workshop** für Server-Mods (Palworld 1.0) — Mods im Panel verwalten,
  Updates wie bei anderen Workshop-Spielen.
- Backups umfassen Savegames und die wichtigsten Server-Einstellungsdateien.

**Hinweis:** Script-/UE4SS-Mods, die nur auf der Windows-Server-Edition laufen,
sind mit diesem nativen Linux-Blueprint nicht abgedeckt — Workshop-Inhalte im
offiziellen Linux-Pfad schon.

### Steam-Einstellungen bleiben sichtbar
Wenn unter **Einstellungen → Steam** trotz Speichern wieder „nicht konfiguriert“
stand:

- **Steam Web API:** Der Key wird zusätzlich sicher in der Panel-Datenbank
  gespeichert (nicht nur in einer `.env`-Datei, die bei Updates fehlen kann).
- **Steam-Account (z. B. DayZ):** Alte Einträge unter veralteten Namen werden
  beim Öffnen der Einstellungen einmalig übernommen.

**Für dich:** Einmal **API-Key speichern** bzw. Account speichern klicken —
danach sollte der grüne Status bleiben. Workshop-Suche und Mod-Status nutzen
den API-Key.

### MSM Backup Recovery (Desktop)
Die **MSM Backup Recovery** App (Entschlüsselung/Restore von Backups) ist
weiterhin **v0.2.0** — unverändert, aber wie gewohnt an diesem Release
angehängt (Windows/Linux-Installer), damit du sie direkt von „Latest Release“
laden kannst.

## Upgrade

```bash
sudo bash update.sh --force
```

Danach **`msm-panel` neu starten** (oder das Update-Skript macht das bereits),
damit der neue Blueprint und die Steam-Logik aktiv sind.

Neuen Palworld-Server: **Server erstellen → Spiel „Palworld“** wählen.

## Verifikation (kurz)

- Spielauswahl enthält **Palworld**.
- **Einstellungen → Steam:** API und ggf. Account als konfiguriert.
- Panel-Update-Anzeige zeigt nach Update **v1.7.10** (ggf. einmal Hard-Reload).

— Maunting Studios