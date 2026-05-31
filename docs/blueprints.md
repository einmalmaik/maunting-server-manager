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
  "filterTags": ["Enhanced"],
  "modInjection": "startupArg",
  "modStartupArgumentFormat": "-mod={mods};",
  "modListFilePath": null,
  "modListContent": "workshopIds",
  "postInstall": []
}
```

`filterTags` (optional, Liste von Strings, max. 10 Tags) definiert Tags, nach denen die Mod-Suche und -Auflistung im Steam Workshop gefiltert wird. Das Feld verhindert, dass inkompatible Versionen gemischt angezeigt werden (z. B. Legacy- und Enhanced-Mods bei Conan Exiles). Erlaubte Zeichen in Tags sind Alphanumerisch, Leerzeichen, `_`, `-` und `+` (max. 64 Zeichen pro Tag).

`modInjection=startupArg` setzt aktive Workshop-IDs in `{MOD_ARG}` ein.

`modInjection=file` schreibt eine Modliste nach `modListFilePath`.

`modListContent` steuert den Inhalt der Modliste:

- `workshopIds`: eine Workshop-ID pro Zeile
- `postInstallTargetBasenames`: Dateinamen der Ziele aus `postInstall`

## Runtime-Startup

`runtime.startup` ist der Startbefehl des Containers. MSM tokenisiert den
String zu einer argv-Liste und führt ihn nicht über eine Shell aus.

Erlaubte Platzhalter:

- `{GAME_PORT}`
- `{QUERY_PORT}`
- `{RCON_PORT}`
- `{VOICE_PORT}`
- `{WEB_PORT}`
- `{CUSTOM_PORT_1}`, `{CUSTOM_PORT_2}`, ... `{CUSTOM_PORT_<N>}` (für zusätzliche custom Ports in Blueprints)
- `{INSTALL_DIR}`
- `{MOD_ARG}`
- `{ENV.<KEY>}` für eigene Werte aus `runtime.env`, z. B. `{ENV.SERVER_NAME}`

`runtime.env`-Werte dürfen nur Port-Platzhalter nutzen:

- `{GAME_PORT}`
- `{QUERY_PORT}`
- `{RCON_PORT}`
- `{VOICE_PORT}`
- `{WEB_PORT}`
- `{CUSTOM_PORT_<N>}`

`{INSTALL_DIR}`, `{MOD_ARG}` und `{ENV.<KEY>}` sind in `runtime.env` bewusst
nicht erlaubt.

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

`runtime.configPatches` patcht Dateien vor jedem Containerstart. Jeder Patch
braucht die Pflichtfelder `type`, `file`, `section`, `key` und `value`.
Aktuell ist nur `type=ini` unterstützt.

Erlaubte Tokens in `value`:

- `{GAME_PORT}`
- `{QUERY_PORT}`
- `{RCON_PORT}`
- `{VOICE_PORT}`
- `{WEB_PORT}`
- `{CUSTOM_PORT_<N>}`

Nicht erlaubt in `value` sind `{INSTALL_DIR}`, `{MOD_ARG}` und `{ENV.<KEY>}`.
Diese Tokens gelten nur für `runtime.startup` beziehungsweise gar nicht für
Config-Patches.

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

## Ports und Protokolle

`ports` beschreibt fachliche Port-Rollen und das Protokoll, das Docker und UFW
öffnen müssen:

```json
{
  "ports": [
    { "name": "game", "protocol": "udp" },
    { "name": "query", "protocol": "udp" },
    { "name": "query", "protocol": "tcp" },
    { "name": "rcon", "protocol": "tcp" }
  ]
}
```

`name` ist die fachliche Rolle, nicht das Protokoll. `protocol` ist verbindlich.
Wenn ein Spiel denselben Port über UDP und TCP braucht, deklariere dieselbe Rolle
zweimal mit unterschiedlichen Protokollen. MSM legt daraus intern eindeutige
Port-Rollen an: der erste `query`-Eintrag bleibt `query`, der zweite wird
`query_2`. Bei gleicher fachlicher Rolle teilt die automatische Vergabe den Port
über unterschiedliche Protokolle, z. B. `28015/udp` und `28015/tcp`.

Wichtig:

- Gleiche Rolle + gleiches Protokoll ist für Standardrollen nicht erlaubt.
- Gleiche Rolle + anderes Protokoll ist erlaubt und wird getrennt in Docker und
  UFW freigegeben.
- Das Netzwerk-Panel darf das Protokoll nachträglich ändern; der gespeicherte
  Serverzustand gewinnt dann gegenüber dem Blueprint-Default.
- `custom`-Ports behalten ihre eigene Nummerierung: `custom_1`, `custom_2`, ...

Für Platzhalter gilt weiter: `{QUERY_PORT}` referenziert die erste fachliche
`query`-Rolle. Zusätzliche Standardrollen wie `query_2` sind für Docker/UFW und
das Netzwerk-Panel relevant; Startup-Argumente müssen bei mehreren getrennten
CLI-Parametern aktuell über passende Standard- oder `custom`-Ports modelliert
werden.

## Wine-Kompatibilität für Windows-Server

Viele Windows-basierte Game-Server (wie z.B. *SCUM*, *Space Engineers*, etc.) benötigen eine Wine-Kompatibilitätsschicht unter Linux und oft zusätzliche Ports (z.B. für Voice, Query2, RCON2).

### 1. Custom Ports in Blueprints deklarieren

In der Blueprint-Definition unter `ports` können beliebig viele Custom Ports hinzugefügt werden:

```json
  "ports": [
    { "name": "game", "protocol": "udp" },
    { "name": "query", "protocol": "udp" },
    { "name": "rcon", "protocol": "tcp" },
    { "name": "custom", "protocol": "udp" },
    { "name": "custom", "protocol": "tcp" }
  ]
```

Im Startup-Befehl und in Config-Patches werden diese dynamischen Ports über die Platzhalter `{CUSTOM_PORT_1}`, `{CUSTOM_PORT_2}` usw. (aufsteigend indiziert basierend auf ihrer Reihenfolge der Definition) referenziert.

Die Reihenfolge der `custom`-Ports ist auch die Reihenfolge der Docker-Publishes:
der erste `custom`-Eintrag wird `custom_1`, der zweite `custom_2` usw. Das gilt
unabhängig davon, ob mehrere Custom-Ports dasselbe Protokoll nutzen.

### 2. Wine-Umgebungsvariablen konfigurieren

Die Kompatibilitätsschicht wird klassisch über Umgebungsvariablen (`runtime.env`) konfiguriert. Ein typisches Blueprint-Beispiel für ein Wine-Spiel:

```json
  "runtime": {
    "image": "ghcr.io/einmalmaik/msm-wine:latest",
    "env": {
      "WINEDEBUG": "-all",
      "WINEPREFIX": "/server/.wine",
      "DISPLAY": ":0"
    },
    "startup": "wine64 /server/ScumSystem/Binaries/Win64/SCUM.exe -port={GAME_PORT} -queryport={QUERY_PORT}"
  }
```

Es wird kein spezifisches Wine-Token benötigt; alle Parameter können direkt über die standardmäßigen Umgebungsvariablen konfiguriert werden.

## Install-/Update-Serialisierung

MSM führt serverweite Installations- und Update-Jobs seriell aus. Dazu gehören
Blueprint-Installationen, Reinstallationen, Server-Datei-Updates vor einem
Restart und Workshop-Downloads, die über den Server-Start/Restart oder
Mod-Subscribe ausgelöst werden.

Wenn bereits ein Install-/Update-Job läuft, antwortet die API mit dem
strukturierten Fehlercode `install_update_already_running`. Die UI übersetzt
diesen Code i18n-fähig. Die Sperre ist generisch und nicht SteamCMD-spezifisch;
sie schützt auch HTTP-Source- und künftige Blueprint-Update-Pfade.

SteamCMD-Fehler wie `Missing Configuration` oder `state is 0x202 after update
job` werden als strukturierte Fehler klassifiziert. Die genannten Ursachen im
Status/Console-Log sind bewusst als mögliche Ursachen markiert und nicht als
bewiesen: ohne Host-Metriken und vollständige SteamCMD-/Docker-Runtime kann MSM
nicht sicher unterscheiden, ob App-Metadaten, Account/Lizenz, Plattform,
Plattenplatz/Quota, Berechtigungen oder paralleler Zugriff die Ursache waren.
