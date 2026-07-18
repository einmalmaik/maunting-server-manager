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

## Für Einsteiger: Was trägst du wo ein?

Eine Blueprint beschreibt einen Server-Typ, nicht einen einzelnen Server. Der
Nutzer erstellt später eine konkrete Server-Instanz daraus und vergibt dort
Ports, Limits und eine Bind-IP.

Die wichtigsten Blöcke:

- `meta`: Name, ID und Kategorie für die UI.
- `runtime`: Docker-Image, Arbeitsverzeichnis, Umgebungsvariablen und
  Startbefehl im Container.
- `ports`: Welche Port-Rollen der Server braucht. Hier stehen Rollen wie
  `game` oder `query`, keine konkreten Portnummern.
- `source`: Woher die Server-Dateien kommen: Steam, HTTPS-Archiv, fertiges
  Docker-Image oder manueller Upload.
- `mods`: Ob MSM den Mod-Manager und optional Steam Workshop aktivieren soll.

Faustregel: Wenn der Server schon komplett im Docker-Image steckt, nutze
`source.type=dockerOnly`. Wenn MSM ein ZIP/TAR herunterladen soll, nutze
`source.type=http`. Für **Git-Repos (Discord-Bots, Node/Python-Apps)** mit Branch und Auto-Pull: `source.type=github`. Wenn SteamCMD die Dateien holen soll, nutze
`source.type=steam`. Wenn der Nutzer Dateien selbst hochladen muss, nutze
`source.type=manualUpload`.

### Beispiel: Nicht-Steam-Game mit HTTPS-Download (Hytale)

Hytale ist kein Steam-Spiel. MSM lädt zuerst den offiziellen Hytale-Downloader
per HTTPS. Beim ersten Start zeigt die Console den OAuth-Link an, den der
Serverbetreiber mit seinem Hytale-Account bestätigen muss.

```json
{
  "version": 1,
  "meta": {
    "id": "hytale",
    "name": "Hytale (Dedicated)",
    "category": "non_steam_game"
  },
  "runtime": {
    "image": "ghcr.io/natroutter/egg-hytale:latest",
    "workdir": "/home/container",
    "env": {
      "STARTUP": "./start.sh",
      "SERVER_PORT": "{GAME_PORT}",
      "AUTH_MODE": "AUTHENTICATED",
      "AUTOMATIC_UPDATE": "1",
      "PATCHLINE": "release"
    },
    "startup": "/entrypoint.sh"
  },
  "ports": [
    { "name": "game", "protocol": "udp" }
  ],
  "source": {
    "type": "http",
    "http": {
      "url": "https://downloader.hytale.com/hytale-downloader.zip",
      "archiveType": "zip"
    }
  }
}
```

### Beispiel: Steam-Spiel

Bei Steam-Spielen lädt MSM die Dateien über SteamCMD. `requiresLogin=true`
bedeutet: Unter den Panel-Einstellungen muss ein globaler Steam-Account
konfiguriert sein.

```json
{
  "version": 1,
  "meta": {
    "id": "dayz",
    "name": "DayZ",
    "category": "steam_game"
  },
  "runtime": {
    "image": "ghcr.io/parkervcp/steamcmd:debian",
    "workdir": "/data",
    "env": {},
    "startup": "/data/DayZServer -config=serverDZ.cfg -port={GAME_PORT} -BEpath=battleye -profiles=profiles -dologs -adminlog -netlog -freezecheck",
    "ensureDirs": ["profiles"]
  },
  "ports": [
    { "name": "game", "protocol": "udp" },
    { "name": "query", "protocol": "udp" },
    { "name": "rcon", "protocol": "tcp" }
  ],
  "source": {
    "type": "steam",
    "steam": {
      "appId": "223350",
      "platform": "linux",
      "compatibility": "native",
      "requiresLogin": true
    }
  }
}
```

#### `source.steam.branch` (optional)

Steam-**Depot-Branch** für Dedicated-Server-Install und -Updates (nicht Workshop).

| Wert | Bedeutung |
|------|-----------|
| weggelassen / `null` | **`public`** — Standard-Release-Branch |
| z. B. `conan-exiles-legacy` | Beta-/Legacy-Branch; SteamCMD erhält `-beta <name>` |

Der passive **Server-Datei-Update-Check** (`check_server_file_update`) vergleicht die
lokale `buildid` aus `steamapps/appmanifest_<appId>.acf` mit der **gleichen**
Branch-Metadaten auf api.steamcmd.net. Bei Abweichung und `updateStrategy: checkBased`
läuft vor Start/Restart `+app_update` (optional mit `validate`, siehe `validate`).

```json
"steam": {
  "appId": "443030",
  "platform": "linux",
  "branch": "conan-exiles-legacy",
  "validate": false
}
```

**Workshop-Mods** nutzen weiterhin `mods.workshopAppId` — unabhängig vom Server-Branch.

#### Server-Binaries vs. Workshop-Mods (Update-Verhalten)

| Was | Erkennung | Wann installiert | Auto-Neustart |
|-----|-----------|------------------|---------------|
| **Workshop-Mods** | Steam Web API `time_updated` (Scheduler, ~6 h) | Beim **Restart**, wenn Mod `outdated` | Nur Mods mit `auto_update` und Server **gestoppt** (Scheduler) |
| **Spiel-Binaries (Steam App)** | `buildid`-Vergleich pro Blueprint-**branch** | Beim **Start/Restart**, wenn Check `update` oder `alwaysValidate` | **Nein** — MSM startet den Server nicht allein wegen eines Game-Updates neu; Betreiber startet/restartet manuell oder nutzt geplantes `auto_restart` (ohne Update-Trigger) |

Badge **Server-Update** in der UI kommt vom gleichen Check (`server_file_update_available`). Auf der **Server-Detailseite** gibt es zusätzlich **„Spiel-Updates prüfen“** (wie im Mod-Manager), um den Check ohne 5-Minuten-Cache auszulösen.

**Reinstall:** Lädt aktuelle Spiel-Binaries von Steam/HTTP; manuelle Configs werden gesichert und wiederhergestellt. Workshop-Mods werden **nicht** neu installiert (dafür Mod-Manager).

### Beispiel: Discord-Bot aus einem ZIP

Für Bots gibt es oft keine Ports. Das ZIP muss eine Startdatei enthalten, die
zum `runtime.startup` passt.

```json
{
  "version": 1,
  "meta": {
    "id": "custom_discord_bot",
    "name": "Custom Discord Bot",
    "category": "bot"
  },
  "runtime": {
    "image": "node:20-alpine",
    "workdir": "/data",
    "env": {
      "NODE_ENV": "production"
    },
    "startup": "npm start"
  },
  "ports": [],
  "source": {
    "type": "http",
    "http": {
      "url": "https://github.com/owner/repo/archive/refs/heads/main.zip",
      "archiveType": "zip"
    }
  }
}
```

### Beispiel: Open-Source-Voice-Server

Mumble ist ein Beispiel für einen Voice-Server, der direkt aus einem Docker-Image
starten kann. Deshalb ist `source.type=dockerOnly` ausreichend.

```json
{
  "version": 1,
  "meta": {
    "id": "mumble_server",
    "name": "Mumble Server",
    "category": "voice_server"
  },
  "runtime": {
    "image": "mumblevoip/mumble-server:latest",
    "workdir": "/data",
    "env": {},
    "startup": "mumble-server"
  },
  "ports": [
    { "name": "voice", "protocol": "tcp" },
    { "name": "voice", "protocol": "udp" }
  ],
  "source": {
    "type": "dockerOnly"
  }
}
```

### Typische Fehler bei eigenen Blueprints

- `source.type=http`, aber kein Archivtyp erkannt: Setze `source.http.archiveType`
  explizit auf `zip`, `tar.gz`, `tar.xz` oder ein anderes unterstütztes Format.
- `ports[].name` falsch verstanden: In `name` kommt eine Rolle wie `game`,
  `query`, `rcon`, `voice`, `web` oder `custom`, nicht die Portnummer.
- Hytale startet und zeigt einen OAuth-Link: Das ist beim ersten Start erwartet.
  Der Serverbetreiber muss den Link öffnen und den Code mit einem Hytale-Account
  bestätigen, der Zugriff auf den Serverdownload hat.
- Hytale meldet `403 Forbidden` oder `Unauthorized`: Der verwendete Hytale-Account
  hat wahrscheinlich keinen Zugriff, der Code ist abgelaufen oder die lokal
  gespeicherten Hytale-Downloader-Credentials müssen erneuert werden.

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

## Runtime-Verzeichnisse und Config-Patches

`runtime.ensureDirs` legt vor jedem Containerstart relative Ordner innerhalb
des Server-Verzeichnisses an. Das ist für Spiele gedacht, die Profile-, Log-,
Cache- oder Runtime-Verzeichnisse per Startargument erwarten, aber nicht immer
selbst zuverlässig anlegen. Pfade sind strikt relativ, absolute Pfade und `..`
werden abgelehnt.

`runtime.configPatches` patcht Dateien vor jedem Containerstart. Es unterstützt zwei Typen:

### 1. Sektion-basiert (`type=ini`)
Für klassische INI-Dateien. Jeder Patch braucht die Felder `type`, `file`, `section`, `key` und `value`.

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

### 2. Regex-basiert (`type=regex`)
Für alle anderen Textdateien (z. B. Bohemia-`.cfg`, `.properties`, `.txt`, `.json`). Sucht und ersetzt Muster per regulärem Ausdruck. Jeder Patch braucht die Felder `type`, `file`, `regex` und `value` (`section` und `key` dürfen hier nicht angegeben werden). 

Im `value`-Feld können reguläre Backreferences (z. B. `\\g<1>`) und Port-Platzhalter verwendet werden.

Beispiel (DayZ `serverDZ.cfg`):
```json
{
  "type": "regex",
  "file": "serverDZ.cfg",
  "regex": "(steamQueryPort\\s*=\\s*)\\d+;",
  "value": "\\g<1>{QUERY_PORT};"
}
```

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

Wenn ein Port-Token leer ist, wird dieser Patch übersprungen.

## Stop-Grace-Period und Update-Strategie (runtime + source)

Diese beiden Felder sind provider-neutral und gelten für **alle** Blueprint-Quellen
(Steam, HTTP, GitHub, dockerOnly, manualUpload, custom). Steam und Workshop sind
optionale Provider — der Blueprint-Core bleibt generisch.

### stopGracePeriodSeconds (unter `runtime`)

Legt fest, wie viele Sekunden Docker dem Container beim Stop (`docker stop --time N`)
für einen sauberen Shutdown gibt, bevor SIGKILL folgt.

- **Default**: 30
- **Erlaubter Bereich** (Schema): 5 bis 600 Sekunden
- **Verwendung**: Für Server mit persistenter Welt (z. B. DayZ, Conan) oft höher
  setzen, damit Save- oder Snapshot-Operationen abgeschlossen werden können.
  Zu kleiner Wert → Datenverlust-Risiko. Zu großer Wert → Restart dauert länger.

Beispiel:

```json
"runtime": {
  "image": "cm2network/steamcmd:root",
  "startup": "...",
  "stopGracePeriodSeconds": 120,
  "ensureDirs": ["profiles"]
}
```

**Kompatibilität**: Blueprints ohne das Feld verwenden den Default 30 s
(Pydantic-Default). Kein Breaking-Change.

### updateStrategy (unter `source`)

Steuert, ob und wann vor einem Start oder Restart ein Server-Datei-Update
durchgeführt wird (vor `plugin.start`, mit Schutz manueller Configs).

Mögliche Werte:

- `alwaysValidate`: Update wird bei jedem Start/Restart **unbedingt** ausgeführt
  (bei Steam: `+app_update ... validate`). Garantiert frische Binaries, kann
  auch ein Update erzwingen, wenn der passive Check "none" meldet.
- `checkBased`: Nur updaten, wenn der passive Check (`updater.check_server_file_update`)
  ein Update meldet. Bei **Steam**: Vergleich lokale vs. Remote-`buildid` für
  `source.steam.branch` (Default `public`). Spart SteamCMD-Läufe, wenn der Build aktuell ist.
- `none`: Kein Auto-Update durch MSM (z. B. dockerOnly, custom, manualUpload oder
  wenn der Betreiber manuell pflegt).

**Defaults pro Source-Typ** (wenn nicht explizit gesetzt):

| Source-Typ       | Default          | Begründung |
|------------------|------------------|------------|
| steam            | checkBased       | Standard: buildid-Check + SteamCMD nur bei Bedarf; `alwaysValidate` erzwingt Validate bei jedem Start. |
| http             | checkBased       | HEAD + Last-Modified vs. lokale mtime (siehe `games/updater.py`). |
| dockerOnly / custom / manualUpload | none | MSM verwaltet keine Dateien; Verantwortung liegt beim Image oder User. |

Beispiele (explizites Override):

```json
"source": {
  "type": "steam",
  "steam": { "appId": "223350", "platform": "linux", "requiresLogin": true },
  "updateStrategy": "checkBased"
}
```

```json
"source": {
  "type": "http",
  "http": { "url": "https://example.com/server.tar.gz", "archiveType": "tar.gz" },
  "updateStrategy": "alwaysValidate"
}
```

**Verhalten im Lifecycle**:
- Start und Restart rufen `_source_update_strategy` (delegiert an
  `BlueprintSource.effective_update_strategy`).
- ALWAYS → force `{"action": "update"}`.
- CHECK_BASED → nutze Ergebnis von `check_server_file_update`.
- NONE → überspringe komplett.
- Das eigentliche Update (falls nötig) läuft **vor** dem Container-Start,
  mit Cache/Restore manueller Configs (siehe `games/updater.py:perform_install_with_protection`
  und `apply_server_file_update`).

**Fehler / Kompatibilität**:
- Ungültiger Wert → `BlueprintValidationError` beim Laden (früh, bevor ein Job startet).
- Alte Blueprints ohne Feld: exakt vorheriges Verhalten (rückwärts-kompatibel).
- Explizites `alwaysValidate` auf einem `dockerOnly`-Blueprint: `perform` liefert
  ein No-Op-Ergebnis ("nicht vorgesehen") — unschädlich, aber ungewöhnlich.
- Der Core enthält **keinen** Steam-only-Hardcode mehr; alle Entscheidungen
  gehen über die Blueprint-Daten (siehe `server_lifecycle_service._source_update_strategy`).

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
