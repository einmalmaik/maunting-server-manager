# Blueprint Runtime

Blueprints sind die Single Source of Truth für native und Community-Server.
Native Unterstützung bedeutet, dass MSM eine Blueprint-Datei unter
`backend/blueprints/native` mitliefert. Community-Server nutzen dieselbe
Runtime über importierte Blueprints.

Es gibt keine spiel-spezifischen Python-Plugins für einzelne Server-Typen.
Der generische `BlueprintPlugin` führt alle Blueprints aus.

## Sichere Grenzen

Blueprints sind Daten, keine Skripte:

- keine Shell-Hooks
- keine Python-Hooks
- keine absoluten Host-Pfade
- keine `..`-Pfade
- keine freien Kommandos nach Installation oder vor Start

Erlaubt sind nur whitelisted Runtime-Fähigkeiten.

## Workshop-Mods

Steam Workshop wird über `mods` aktiviert:

```json
{
  "supportsMods": true,
  "supportsSteamWorkshop": true,
  "workshopAppId": "221100",
  "modInjection": "startupArg",
  "modStartupArgumentFormat": "-mod={mods};",
  "modListFilePath": null,
  "modListContent": "workshopIds",
  "postInstall": []
}
```

`modInjection=startupArg` setzt aktive Workshop-IDs in `{MOD_ARG}` ein.

`modInjection=file` schreibt eine Modliste nach `modListFilePath`.

`modListContent` steuert den Inhalt der Modliste:

- `workshopIds`: eine Workshop-ID pro Zeile
- `postInstallTargetBasenames`: Dateinamen der Ziele aus `postInstall`

## Workshop-Dateiaktionen

`mods.postInstall` beschreibt, was nach einem erfolgreichen Workshop-Download
mit Dateien im Server-Verzeichnis passieren soll.

Erlaubte Operationen:

- `copy`
- `symlink`

Erlaubte Tokens in `source` und `target`:

- `{WORKSHOP_APP_ID}`
- `{WORKSHOP_ID}`
- `{BASENAME}`

Beispiel DayZ:

```json
{
  "operation": "symlink",
  "source": "steamapps/workshop/content/{WORKSHOP_APP_ID}/{WORKSHOP_ID}",
  "target": "{WORKSHOP_ID}",
  "required": true
}
```

Beispiel Conan Exiles:

```json
{
  "operation": "copy",
  "source": "steamapps/workshop/content/{WORKSHOP_APP_ID}/{WORKSHOP_ID}/**/*.pak",
  "target": "ConanSandbox/Mods/{BASENAME}",
  "required": true
}
```

Wenn `source` ein Glob ist, muss `target` `{BASENAME}` enthalten.

## Runtime-Config-Patches

`runtime.configPatches` patcht Dateien vor jedem Containerstart. Aktuell ist
`type=ini` unterstützt.

Erlaubte Tokens in `value`:

- `{GAME_PORT}`
- `{QUERY_PORT}`
- `{RCON_PORT}`
- `{VOICE_PORT}`
- `{WEB_PORT}`

Beispiel:

```json
{
  "type": "ini",
  "file": "ConanSandbox/Saved/Config/LinuxServer/Engine.ini",
  "section": "URL",
  "key": "Port",
  "value": "{GAME_PORT}"
}
```

Wenn ein Port-Token leer ist, wird dieser Patch übersprungen.
