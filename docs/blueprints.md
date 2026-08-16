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

## Workshop- und CurseForge-Mods

Steam Workshop und CurseForge werden über `mods` im Blueprint konfiguriert:

### Steam Workshop
```json
{
  "supportsMods": true,
  "supportsSteamWorkshop": true,
  "workshopAppId": "221100",
  "filterTags": ["Enhanced"],
  "modInjection": "startupArg",
  "modStartupArgumentFormat": "-mod={mods};",
  "modStartupArgumentSeparator": ";",
  "modListFilePath": null,
  "modListContent": "workshopIds",
  "postInstall": []
}
```

### CurseForge
```json
{
  "supportsMods": true,
  "supportsCurseForge": true,
  "curseforgeGameId": "83374",
  "curseforgeClassId": null,
  "curseforgeInstallPath": "mods",
  "modInjection": "startupArg",
  "modStartupArgumentFormat": "-mods={mods}",
  "modStartupArgumentSeparator": ","
}
```

`curseforgeGameId` ist die offizielle CurseForge Game-ID (z. B. `83374` für ARK: Survival Ascended, `432` für Minecraft).
`curseforgeClassId` (optional) filtert auf bestimmte Kategorien (z. B. `6` für Minecraft Mods, `4552` für Bukkit Plugins).
`curseforgeInstallPath` (optional) definiert das relative Zielverzeichnis für heruntergeladene .jar/.zip-Dateien (z. B. `mods` oder `plugins`).
`modStartupArgumentSeparator` (optional, default `;`) steuert das Trennzeichen zwischen Mod-IDs in `{MOD_ARG}` (z. B. `,` für ARK: Survival Ascended).

`filterTags` (optional, Liste von Strings, max. 10 Tags) definiert Tags, nach denen die Mod-Suche und -Auflistung im Steam Workshop gefiltert wird. Das Feld verhindert, dass inkompatible Versionen gemischt angezeigt werden (z. B. Legacy- und Enhanced-Mods bei Conan Exiles). Erlaubte Zeichen in Tags sind Alphanumerisch, Leerzeichen, `_`, `-` und `+` (max. 64 Zeichen pro Tag).

`modInjection=startupArg` setzt aktive Mod-IDs in `{MOD_ARG}` ein.

`modInjection=file` schreibt eine Modliste nach `modListFilePath`.

`modListContent` steuert den Inhalt der Modliste:

- `workshopIds`: eine Workshop-/CurseForge-ID pro Zeile
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

`runtime.seedFiles` legt Default-Dateien **nur dann** an, wenn sie im
Server-Verzeichnis noch fehlen (seed-once). Das ist für Spiele gedacht, die
keine Default-Config ausliefern (z. B. Enshrouded ohne `enshrouded_server.json`).
Ohne Seed würde der erste Start oft mit Spiel-Default-Ports laufen, während MSM
andere Ports published. Bestehende User-Dateien werden **nie** überschrieben.
`seedFiles` laufen nach `ensureDirs` und vor `configPatches`, damit Patches
sofort greifen können.

Jedes Element braucht `file` (sicherer relativer Pfad) und `content` (Text,
max. 64 KiB). In `content` sind dieselben Port-Tokens wie in Config-Patches
erlaubt (`{GAME_PORT}`, `{QUERY_PORT}`, …). Fehlt ein benötigter Port, wird
dieser Seed übersprungen.

Beispiel:
```json
{
  "file": "enshrouded_server.json",
  "content": "{\n  \"name\": \"Enshrouded Server\",\n  \"queryPort\": {QUERY_PORT},\n  \"slotCount\": 16\n}\n"
}
```

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

### 3. Zusammenspiel von `seedFiles` und `configPatches` (z. B. 7 Days to Die)
Fehlt eine Konfigurationsdatei vor dem ersten Start (z. B. `platform.cfg` oder `steam_appid.txt`), legt MSM sie über `seedFiles` an. Lädt Steam oder das Spiel die Datei im Anschluss herunter oder überschreibt sie, wendet MSM bei jedem weiteren Start die `configPatches` an. Dadurch bleiben Einstellungen (z. B. Deaktivieren fehlerhafter Crossplay-Module oder Setzen der App-ID) dauerhaft erhalten:

```json
"seedFiles": [
  {
    "file": "platform.cfg",
    "content": "platform=Steam\ncrossplatform=\nserverplatforms=Steam,LAN,\n"
  },
  {
    "file": "steam_appid.txt",
    "content": "294420\n"
  }
],
"configPatches": [
  {
    "type": "regex",
    "file": "platform.cfg",
    "regex": "(?m)^crossplatform=.*$",
    "value": "crossplatform="
  },
  {
    "type": "regex",
    "file": "steam_appid.txt",
    "regex": "^.*$",
    "value": "294420"
  }
]
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

## Guardian Autonomous Engine (Autopilot)

Guardian ist pro Blueprint opt-in. Sobald mindestens einer der Blöcke `health`,
`logs`, `diagnostics`, `recovery` oder `backups` vorhanden ist, synchronisiert
das Panel den Guardian-Vertrag zum zuständigen Node. Fehlen alle Blöcke, meldet
der Agent den Zustand `disabled` und verändert den Container nicht. Das Beispiel
[`generic_github_bot.blueprint.json`](templates/generic_github_bot.blueprint.json)
zeigt diesen bewussten Opt-out.

Der Agent erzwingt den im Backend autorisierten Sollzustand, serialisiert
Lifecycle- und Recovery-Aktionen pro Server und erkennt einen neuen
Container-Start anhand von Dockers `started_at`. Ein normaler Docker-Autorestart
beginnt deshalb wieder mit Startup-Grace und verbraucht nicht sofort ein
Recovery-Budget. Ein `stopped`-Sollzustand stoppt einen trotzdem laufenden
Container; ein fehlender Container mit `running`-Sollzustand wird durch das Panel
über den normalen Lifecycle-Pfad neu erstellt.

### `health`

Es gibt höchstens je einen Prozess-, Port- und Anwendungs-Check. Die IDs aller
aktivierten Checks müssen eindeutig sein.

Gemeinsame Check-Felder:

- `id`: `^[a-z][a-z0-9_-]{0,63}$`
- `interval`: Dauer wie `500ms`, `15s` oder `2m`
- `failure_threshold`: 1–20 aufeinanderfolgende Fehler
- `success_threshold`: 1–20 aufeinanderfolgende Erfolge
- `required_for_startup`: muss während der Startphase erfolgreich sein
- `required_for_verification`: muss nach einer Recovery stabil erfolgreich sein

`process` ergänzt `required` (Default `true`). Der Prozesszustand bleibt auch
ohne weitere Checks die Container-Basisinvariante.

`port` ergänzt `protocol` (`tcp` oder `udp`), `port` und `timeout`. TCP baut eine
echte Verbindung auf. UDP kann protokollbedingt nur verifizieren, dass Docker den
erwarteten Host-Port veröffentlicht. Portwerte verwenden ausschließlich
`{{SERVER_PORT}}`, `{{GAME_PORT}}`, `{{QUERY_PORT}}`, `{{RCON_PORT}}`,
`{{VOICE_PORT}}`, `{{WEB_PORT}}` oder `{{PORT:<rolle>}}`.

`application.type` ist exakt einer der eingebauten Typen:

- `tcp`
- `http-ping`
- `minecraft-status`
- `minecraft-query`
- `source-query`

Zusätzliche Felder sind `timeout`, optional `port`, `expected_statuses` (100–599),
`max_response_bytes` (1–1.048.576) und die gemeinsamen Check-Felder.
`path` ist nur bei `http-ping` erlaubt und beginnt mit `/`. Redirects werden aus
SSRF-Sicherheitsgründen nicht verfolgt; `follow_redirects` muss `false` bleiben.
Freie/custom Probe-Typen sind absichtlich nicht Teil des Blueprint-Vertrags und
können weder über JSON noch über den Webeditor aktiviert werden.

`startup`:

- `grace_period_seconds`: 0–600
- `timeout_seconds`: 1–3600 und größer als die Grace Period
- `success_patterns`, `failure_patterns`: je maximal 16 sichere reguläre
  Ausdrücke; Backreferences, Lookarounds und riskante verschachtelte
  Quantifizierer werden abgelehnt.

### `logs` und `diagnostics`

`logs.sources` enthält maximal 16 Einträge: `stdout` oder sichere relative Pfade
mit höchstens einem einfachen Dateinamen-Wildcard, beispielsweise
`logs/*.log`. `max_tail_bytes` liegt zwischen 1.024 und 1.048.576.

Vor Persistierung oder Übertragung werden die in `logs.redact` gewählten
Redactoren angewendet. Erlaubt sind `discord_token`, `api_key`,
`authorization_header`, `database_url`, `jwt` sowie geprüfte Einträge mit dem
Präfix `regex:`.

`diagnostics.parsers` erlaubt ausschließlich `linux-oom`, `java-stacktrace`,
`nodejs-stacktrace`, `port-conflict`, `missing-runtime`, `corrupted-config` und
`startup-pattern`.

### `recovery`

Eine Policy verbindet einen exakten `match` mit einer registrierten `action`.
Freitext ist nicht zulässig, weil ein Tippfehler sonst eine scheinbar
konfigurierte, aber wirkungslose Recovery erzeugen würde.

Erlaubte Aktionen:

- `restart`: Docker-Restart des vorhandenen Containers
- `graceful_restart`: Stop mit Grace Period, danach Start
- `clear_declared_lock_files`: ausschließlich explizit deklarierte reguläre
  Lockdateien entfernen; Symlinks, Traversal, Globs und geschützte Pfade werden
  abgelehnt
- `quarantine`: Autonomie stoppen und eine explizite Admin-Freigabe verlangen

Erlaubte Matches:

`process_not_running`, `tcp_connect_failed`, `udp_mapping_missing`,
`http_redirect_rejected`, `http_response_too_large`, `http_unexpected_status`,
`http_request_failed`, `minecraft_query_failed`, `minecraft_status_failed`,
`source_query_failed`, `linux-oom`, `port-conflict`, `java-stacktrace`,
`nodejs-stacktrace`, `missing-runtime`, `corrupted-config`, `startup-pattern`
und `probe_failed`.

Mehrere Policies mit demselben Match bilden dessen kleine Eskalationsleiter.
`max_attempts` (1–10), `attempt_window_seconds` (60–86.400) und
`cooldown_seconds` (1–3.600) begrenzen Schleifen. Ein neuer, unabhängiger
Fehlertyp erhält ein frisches Budget. Nach spontaner Heilung oder erfolgreicher
Verification werden Incident, Stufe und Budget zurückgesetzt.

`verification` verlangt eine stabile Erholung:

- `minimum_healthy_duration_seconds`: 0–600
- `required_consecutive_successes`: 1–20
- `verification_timeout_seconds`: 5–3600

`safe_lock_files` enthält maximal 32 Paare aus sicherem relativen `path` und
verständlichem `reason`. Nur diese Pfade darf
`clear_declared_lock_files` anfassen.

### `backups`

`protected_paths` sind sichere relative Pfade, die keine autonome
Lockfile-Aktion berühren darf. Bei `before_risky_action: true` sichert der Agent
jede vorhandene deklarierte Lockdatei vor dem Löschen bytegenau unter
`/var/lib/msm-agent/guardian/recovery-backups/<server-id>/<backup-id>/`. Dateien
über 1 MiB werden abgelehnt; pro Server bleiben die zehn neuesten Snapshots.
Die Verzeichnisse sind nur für den Agent-Betreiber lesbar. Wiederherstellung
erfolgt bewusst manuell nach Prüfung des Incidents.

### Übersteuerung je Server

Ein Blueprint gilt für **jeden** Server seines Spiels. Er kann nicht wissen,
dass auf einer bestimmten Node zwölf Instanzen um acht Gigabyte streiten und
deshalb keine davon in dreißig Sekunden hochkommt — Guardian sieht dort einen
Server, der die Startfrist reißt, startet ihn neu, sieht es wieder, und nach
drei Anläufen steht er in Quarantäne, obwohl nichts kaputt ist außer der
Erwartung.

Für genau diesen Fall lässt sich eine Handvoll Zahlen **je Server** übersteuern.
Sie werden **nach** der Ableitung aus dem Blueprint darübergelegt; alles, was
nicht genannt ist, bleibt wie der Blueprint es sagt. Ein Nachtrag, keine zweite
Konfiguration.

Erlaubt ist eine geschlossene Menge von Skalaren mit Ober- und Untergrenze:

| Feld | Bereich | Wirkung |
| --- | --- | --- |
| `startup_grace_period_seconds` | 1–3600 | Ruhe nach dem Start, bevor Proben zählen |
| `startup_timeout_seconds` | 10–7200 | Wann ein Start als gescheitert gilt |
| `probe_interval_seconds` | 1–600 | Abstand zwischen zwei Proben |
| `probe_timeout_seconds` | 1–120 | Geduld einer Probe (nicht für `process`) |
| `probe_failure_threshold` | 1–20 | Fehlschläge bis zum Alarm |
| `probe_success_threshold` | 1–20 | Erfolge bis zur Entwarnung |
| `recovery_max_attempts` | 0–20 | Guardians eigene Leiter; `0` heißt „nur melden" |
| `recovery_attempt_window_seconds` | 60–86400 | Zeitfenster für die Versuche |
| `recovery_cooldown_seconds` | 0–86400 | Pause zwischen zwei Versuchen |
| `verification_min_healthy_seconds` | 0–3600 | Mindestdauer gesund nach einer Heilung |
| `verification_required_successes` | 1–20 | Nötige Erfolge in Folge |
| `verification_timeout_seconds` | 10–7200 | Frist der Verifikation |

Keine Listen, keine Regexe, keine Probentypen — dieselbe Begründung, aus der der
Blueprint-Editor listenwertige Pfade ausschließt: was sich in einer Zahl mit
Deckel ausdrücken lässt, kann keine Struktur zerstören. Eine übersteuerte
Probenliste dagegen könnte Guardian für diesen Server blind machen, ohne dass es
irgendwo als „abgeschaltet" stünde.

Geklemmt wird beim Lesen, unabhängig davon, wie der Wert in die Zeile kam.
Unbekannte Schlüssel fallen weg, unlesbares JSON gilt als „keine
Übersteuerung" — diese Funktion läuft in jedem Reconcile-Takt über jeden Server
und darf die Synchronisation einer ganzen Node nicht anhalten.

Gesetzt wird sie im Autopilot-Reiter des Servers oder von der KI während einer
Reparatur (`propose_guardian_tuning`, Recht `server.config.write`). Was gilt,
steht sichtbar im Reiter samt Herkunft; ein Knopf setzt auf den Blueprint
zurück. Die Übersteuerung geht in den Konfigurations-Hash ein und bewegt damit
die `desired_state_generation` — ohne das stünde sie in der Datenbank und wirkte
nie.

### Beispiele und Webeditor-Parität

Alle Guardian-Felder sind im Blueprint-Webeditor verfügbar; Änderungen an
einzelnen Feldern erhalten unbekannte Geschwisterwerte verlustfrei. Die
gemeinsame Maximal-Fixture wird sowohl vom Backend-Schema als auch vom
Frontend-Roundtrip-Test geprüft.

- [`guardian_autonomous_bot.blueprint.json`](templates/guardian_autonomous_bot.blueprint.json):
  vollständige autonome Konfiguration
- [`generic_github_bot.blueprint.json`](templates/generic_github_bot.blueprint.json):
  bewusster Betrieb ohne Guardian
- [`standard_steam_server.blueprint.json`](templates/standard_steam_server.blueprint.json):
  konservativer Standard mit Prozess-/Portüberwachung und begrenztem Restart

Ein `updates`- oder freier Custom-Treiber-Block wird nicht akzeptiert. MSM
verspricht damit weder automatische Update-Rollbacks noch hostseitige
Codeausführung, die in der Runtime nicht sicher implementiert ist.

