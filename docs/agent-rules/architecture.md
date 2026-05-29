# Agenten-Regeln: Architektur

Stand: 2026-05-06  
Ergänzt die Root-`AGENTS.md`. Diese Datei ist zu lesen, wenn UI-Struktur, Contexts, Hooks, Services, Orchestratoren, Routing, Tauri/Web-Pfade, Modulidentität, Refactoring oder größere Dateischnitte betroffen sind.

---

## 1. Architekturziel

Das Projekt braucht eine Architektur, die auch in mehreren Jahren noch verständlich, prüfbar und sicher erweiterbar ist.

Architektur ist gut, wenn:

- Verantwortlichkeiten klar sind
- Datenflüsse nachvollziehbar sind
- Security-Entscheidungen zentral und testbar sind
- UI und Fachlogik getrennt bleiben
- Laufzeitunterschiede zwischen Web und Tauri explizit sind
- Dateien klein genug bleiben, um Fehler zu sehen
- Abstraktion ein reales Problem löst
- Tests echte Invarianten prüfen

Architektur ist schlecht, wenn:

- ein Quickfix eine Sicherheitsregel versteckt
- UI-Code Fachlogik übernimmt
- Contexts zu Monolithen werden
- globale Zustände ohne zwingenden Grund entstehen
- Web/Tauri-Unterschiede wegabstrahiert werden, obwohl sie sicherheitsrelevant sind
- neue Framework-Schichten nur gebaut werden, weil sie sauber wirken
- Importpfade doppelte Modulidentität erzeugen

---

## 2. Schichten

Erlaubte Verantwortlichkeiten:

### UI-Komponenten

- Anzeige
- Nutzerinteraktion
- einfache UI-Zustände
- Texte und Layout
- Aufruf öffentlicher Hooks/Fassaden

UI-Komponenten dürfen keine Vault-, Device-Key-, Recovery-, Integrity- oder Crypto-Policy definieren.

### Hooks

- UI-nahe Orchestrierung
- Lifecycle
- stabile Callback-Bindings
- Mapping von UI-Ereignissen auf Services oder Orchestratoren

Hooks dürfen Fachlogik koordinieren, aber nicht selbst zum Policy-Monolithen werden.

### Contexts

- öffentliche Fassade
- State-Gateway
- Provider
- Hook-Exports
- stabile öffentliche API

Contexts dürfen keine wachsenden Fachlogik-Monolithen werden.

### Services

- fachliche Operationen
- Storage-Zugriff
- Crypto-Aufrufe über Adapter/Fassaden
- Validierung
- Policy-Entscheidungen
- typsichere Fehler

Services müssen testbar sein und dürfen keine UI-Annahmen enthalten.

### Orchestratoren

- mehrstufige Flows
- Setup
- Unlock
- Recovery
- Device-Key-Aktivierung
- Passkey-Flows
- Quarantäne- und Integrity-Abläufe
- Cleanup

Orchestratoren machen die Reihenfolge sichtbar. Sie verstecken Security-Schritte nicht in generischen Pipelines.

### Tests

- Invarianten
- Regressionen
- negative Pfade
- Runtime-kritische Pfade
- Web/Tauri-Unterschiede
- Modulidentitätsprobleme

---

## 3. VaultContext-Regel

`src/contexts/VaultContext.tsx` bleibt Gateway/Fassade.

Erlaubt:

- Context erstellen
- Provider exportieren
- Hook exportieren
- öffentliche API stabil halten
- Komposition vorhandener Provider-/Action-Hooks

Verboten:

- neue Unlock-Fachlogik
- neue Device-Key-Fachlogik
- neue Passkey-Fachlogik
- neue Recovery-Fachlogik
- neue Integrity- oder Quarantäne-Fachlogik
- neue Cleanup-Fachlogik
- komplexe Storage- oder Crypto-Aufrufe
- wachsende lokale Hilfsfunktionen mit Security-Policy

Grenzen:

- `src/contexts/VaultContext.tsx` bleibt unter 150 Zeilen.
- `src/contexts/vault/useVaultProviderActions.tsx` bleibt unter 700 Zeilen.
- Wenn diese Grenzen nicht reichen, ist der Schnitt falsch.
- Neue Fachlogik gehört in Services, Orchestratoren oder fokussierte Hooks unter `src/contexts/vault/`.

---

## 4. Scope-Regeln

Wähle den kleinsten sinnvollen Scope.

Lokaler Scope ist richtig, wenn:

- die Logik nur in einer Komponente gebraucht wird
- keine Security-Policy betroffen ist
- keine Wiederverwendung absehbar ist
- kein zentraler Vertrag nötig ist

Globaler oder zentraler Scope ist richtig, wenn:

- eine Security-Policy betroffen ist
- Web und Tauri dieselbe Regel brauchen
- mehrere Flows dieselbe Entscheidung treffen müssen
- ein Test die Invariante zentral absichern soll
- eine öffentliche API stabil bleiben muss

Schlecht: lokale Security-Policy in UI-Code.

```ts
function UnlockPanel() {
  const canUnlock = state.hasMasterPassword;
  return <button disabled={!canUnlock}>Entsperren</button>;
}
```

Gut: zentrale Policy.

```ts
const canUnlock = canUseMasterPasswordUnlock(unlockPolicy);
return <button disabled={!canUnlock}>Entsperren</button>;
```

---

## 5. Abstraktionsregeln

Abstraktion ist nur erlaubt, wenn sie ein reales Projektproblem löst.

Gute Abstraktion:

- reduziert echte Duplikation
- macht Security-Regeln zentral testbar
- kapselt eine riskante Bibliothek
- schützt Fachlogik vor Plattformdetails
- hält öffentliche APIs klein
- macht Datenflüsse klarer

Schlechte Abstraktion:

- erzeugt Manager-, Pipeline- oder Framework-Klassen ohne Bedarf
- versteckt die fachliche Reihenfolge
- macht Debugging schwerer
- erhöht Importfläche
- verschleiert Security-Entscheidungen
- generalisiert für hypothetische zukünftige Anforderungen

Schlecht:

```ts
class VaultFlowPipeline<T> {
  constructor(private steps: Array<(input: T) => Promise<T>>) {}

  async run(input: T): Promise<T> {
    let current = input;
    for (const step of this.steps) {
      current = await step(current);
    }
    return current;
  }
}
```

Warum schlecht, wenn der Flow wenige stabile Schritte hat: Die Reihenfolge ist nicht fachlich sichtbar und Security-Schritte verschwinden in einer generischen Mechanik.

Gut:

```ts
export async function unlockVault(input: UnlockInput): Promise<UnlockResult> {
  const policy = await unlockPolicyService.load(input.accountId);
  assertUnlockAllowed(policy, input);

  const key = await vaultKeyService.deriveForUnlock(input, policy);
  return vaultOpenService.openWithKey({ accountId: input.accountId, key });
}
```

Warum gut: Reihenfolge, Security-Prüfung und Schlüsselverwendung sind sichtbar und testbar.

---

## 6. Refactoring-Regeln

Erlaubt:

- Verantwortlichkeiten trennen
- monolithische Dateien entlang echter Fachgrenzen schneiden
- doppelte Security-Policies zentralisieren
- Typen stärken
- Tests ergänzen
- Side Effects sichtbar machen
- Dependencies kapseln
- Runtime-Unterschiede explizit machen

Verboten:

- große Umbenennungen ohne Nutzen
- generische Framework-Schichten ohne aktuellen Bedarf
- Security-Code "vereinfachen", indem Prüfungen entfernt werden
- mehrere Flows gleichzeitig umbauen, obwohl nur einer betroffen ist
- Tests löschen oder abschwächen
- Public APIs ohne Migrationsplan brechen
- Web/Tauri-Spezifika in gemeinsame Logik mischen
- temporäre Fallbacks in Security-Pfaden hinterlassen

---

## 7. Monolithen vermeiden

Schlecht:

```ts
class VaultManager {
  setup() {}
  unlock() {}
  recover() {}
  sync() {}
  quarantine() {}
  cleanup() {}
  renderToast() {}
}
```

Warum schlecht: vermischt UI und Fachlogik, wird zum Monolithen, ist schwer testbar und versteckt Security-Grenzen.

Gut:

```ts
setupOrchestrator.startSetup();
unlockOrchestrator.unlock();
recoveryOrchestrator.recover();
quarantineService.applyIntegrityResult();
vaultCleanupService.clearRuntimeSecrets();
```

Warum gut: klare Zuständigkeiten, gezielte Tests, verständliche Datenflüsse.

---

## 8. Tauri/Web

Web und Tauri sind nicht dieselbe Sicherheitsumgebung.

Regeln:

- Plattformunterschiede gehören in explizite Adapter oder Services.
- Gemeinsame Fachlogik darf keine Tauri-only APIs direkt importieren.
- Tauri-spezifische Pfade dürfen keine Web-Fallbacks erzeugen, die Security schwächen.
- Web-spezifische Storage- oder Origin-Annahmen dürfen nicht in Tauri übernommen werden.
- Passkey/WebAuthn immer pro RP-ID/Origin bewerten.
- Runtime-Tests müssen die betroffene Oberfläche wirklich öffnen.
- Plattformadapter müssen klein und gezielt testbar bleiben.

Schlecht:

```ts
const storage = window.localStorage;
```

Warum schlecht, wenn der Code in gemeinsamer Fachlogik liegt: Web-Annahme sickert in Tauri- oder Service-Code.

Gut:

```ts
const storage = secureStorageAdapter.forRuntime(runtime);
```

Warum gut: Laufzeitunterschiede sind explizit und können je Plattform abgesichert werden.

---

## 9. Import- und Modulidentität

Regeln:

- Keine doppelten Importpfade für dieselbe Core-Datei.
- Keine Mischung aus `/@fs/` und `/src/` für Core-Module.
- Keine relativen Tiefimporte, wenn ein stabiler Public Entry existiert.
- Keine neuen Barrels, wenn sie Modulidentität, Tree-Shaking oder Laufzeitpfade unklar machen.
- Premium/Core-Importe müssen so bleiben, dass Contexts und Hooks nur eine Modulinstanz sehen.

Runtime-Probleme, auf die geprüft werden muss:

- `must be used within a ...Provider`
- `Invalid hook call`
- doppelte Context-Instanzen
- unterschiedliche Modulpfade für dieselbe Datei
- doppelte `/@fs/` vs `/src/` Pfade

---

## 10. Deutsche UI-Texte

Regeln:

- Deutsche UI-Texte immer mit korrekten Umlauten und ß schreiben.
- Keine ASCII-Umschreibungen wie `ae`, `oe`, `ue`, `ss`, wenn der Text für Nutzer sichtbar ist.
- Neue deutsche Texte im Browser oder in Tauri kurz gegenprüfen.
- Nutzertexte dürfen keine internen Security-Details, Secrets oder kryptografischen Rohwerte zeigen.
- Security-relevante Fehlermeldungen müssen Handlungsoptionen geben, aber keine Angriffsoberfläche erklären.

---

## 11. Architektur-Review-Checkliste

- [ ] Verantwortung der geänderten Dateien klar?
- [ ] Datenfluss nachvollziehbar?
- [ ] Security-Policy zentral statt lokal versteckt?
- [ ] `VaultContext.tsx` nicht aufgebläht?
- [ ] Services/Orchestratoren passend geschnitten?
- [ ] Web/Tauri-Unterschiede explizit?
- [ ] Keine unnötige Abstraktion?
- [ ] Kein neuer Manager-/Pipeline-Monolith?
- [ ] Keine doppelten Core-Importpfade?
- [ ] Runtime-Pfade wirklich geöffnet, wenn betroffen?

---

## 12. Game-Server-Runtime (Phase 6 — Rootless Docker)

Stand: 2026-05-29. Diese Sektion ist MSM-spezifisch und gilt für alle Game-/Voice-Server, die das Panel verwaltet.

### 12.1 Invarianten

- Game-Server laufen ausschließlich in Rootless-Docker-Containern des `msm`-Users. Kein systemd-pro-Server, kein POSIX-User-pro-Server, kein direktes Binary auf dem Host.
- Das Panel selbst (FastAPI) bindet weiterhin an `127.0.0.1:8000` und wird ausschließlich über Caddy nach außen gereicht. Container-Ports werden direkt von Docker auf der Host-Schnittstelle gepublisht (Option: spezifische `public_bind_ip`).
- Container-Lifecycle wird über `services/docker_service.py` abgewickelt — eine kleine Fassade um das Python Docker SDK. Der Adapter spricht ausschließlich mit `DOCKER_HOST=unix:///run/user/<msm_uid>/docker.sock`.
- Der `msm`-User darf nicht Mitglied der globalen `docker`-Gruppe sein. `/var/run/docker.sock` ist für MSM tabu.
- Jeder Server erhält einen stabilen Container-Namen `msm-srv-<server_id>`. Das ist auch `server.container_name` in der DB.
- Container starten mit `--cap-drop=ALL --security-opt=no-new-privileges --restart=on-failure:5 --log-driver=json-file --log-opt max-size=10m --log-opt max-file=3`.
- Bind-Mounts: Host `<install_dir>` → Container `/data`. Der Container läuft mit derselben UID/GID wie der Panel-User (`msm`), damit Schreibrechte konsistent sind.
- Game-Server-Ports müssen `>1024` bleiben. Rootless Docker bekommt keinen `setcap`-Workaround für privilegierte Ports.

### 12.2 Pflichtmethoden für Game-Plugins

Jedes Plugin (`backend/games/<game>/plugin.py`) erbt von `GamePlugin` und implementiert mindestens:

- `build_container_command(server) -> list[str]` — argv des Containers (Pfade INNERHALB des Containers, z. B. `/data/DayZServer`).
- `build_port_publishes(server) -> list[PortPublish]` — Welche Ports nach außen.
- `docker_image: str` — Container-Image (Default: `cm2network/steamcmd:root`).

Standard-Implementationen für `start/stop/get_status/install` in `games/base.py`. Override nur, wenn das Spiel etwas Game-spezifisches braucht.

### 12.3 Resource-Limits (CPU, RAM, Disk)

- `cpu_limit_percent` → `--cpus=<percent/100>` (200 = 2 Cores).
- `ram_limit_mb` → `--memory=<mb>m --memory-swap=<mb>m` (swap=RAM verhindert Thrashing).
- `disk_limit_gb` → **Soft-Limit**. Kein Docker-natives Quota; stattdessen prüft der globale Scheduler-Job (`services/scheduler_service._disk_soft_limit_task`) alle 15 Minuten den Verbrauch via `du -sb`. Bei ≥ 80 % schreibt er eine Warnung in `server.status_message`. Bei ≥ 100 % stoppt er den Container hart und setzt `status="error"`.

Hartes Disk-Quota (XFS-Projekt-Quota oder Overlay-Quota) ist explizit Phase-2-Material.

### 12.4 SteamCMD

Es gibt kein Host-`steamcmd` mehr. Installs und Workshop-Downloads laufen in ephemeren Containern (`cm2network/steamcmd:root`), die in `games/base.run_steamcmd_install()` und `run_steamcmd_workshop_download()` gekapselt sind. Das nutzt das gleiche Bind-Mount-Layout (`<install_dir>` → `/data`).

**Ausführungsmodell:**
- Container läuft explizit als `--user 0:0` (Container-Root), weil das `:root`-Image `/home/steam` mit Mode 700 hat und SteamCMD sonst seinen eigenen Wrapper nicht ausführen kann.
- Entrypoint ist `bash`, der eigentliche SteamCMD-Aufruf läuft als `bash -c '<steamcmd> <args>; rc=$?; chown -R <uid>:<gid> /data; exit $rc'`. Damit landen Dateien am Ende auf der msm-Host-UID, nicht auf 0:0.
- `HOME=/data` lenkt SteamCMDs Auth-/Cache-Verzeichnis (`~/Steam/`) in den Bind-Mount um. Persistent zwischen Runs, kein Schreibversuch auf `/home/steam` (root-owned).
- Hardening bleibt aktiv (`--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--rm`). Nach dem ALL-Drop werden **gezielt** vier Caps wieder zugefügt (`--cap-add`): `DAC_OVERRIDE`, `DAC_READ_SEARCH`, `CHOWN`, `FOWNER`. Ohne diese Caps kann selbst Container-Root keine Mode-700-Verzeichnisse traversieren oder die Bind-Mount-Files zurück-chown'en. Kein Risiko für Host-Escape, weil userns- und no-new-privileges-Schutz unverändert greifen und das einzige Schreibziel der Bind-Mount bleibt.
- Alle SteamCMD-Argumente werden mit `shlex.quote()` escaped, bevor sie in den `bash -c`-String eingesetzt werden — Shell-Injection über `extra_args` ist nicht möglich.

Intelligentes Mod-Update bleibt erhalten: SteamCMD selbst entscheidet, ob ein `workshop_download_item` einen Refresh braucht — wir starten den Container einfach jedes Mal, und SteamCMD validiert die lokalen Dateien gegen das Manifest. Kein Voll-Redownload, wenn nichts geändert hat.

### 12.5 Install-Status-Callback (Background-Thread → DB)

`Plugin.install()` startet SteamCMD in einem Daemon-Thread und kehrt sofort mit `"Installation gestartet"` zurück. Der Request-Endpoint setzt `server.status = "installing"`. Nach Abschluss des SteamCMD-Containers MUSS der Thread `games.base.finish_install(server_id, result)` aufrufen — sonst bleibt der Server für immer auf `"installing"` und die Frontend-UI lässt Start/Stop/Restart-Buttons gesperrt.

`finish_install()` öffnet eine FRISCHE `SessionLocal()` (die Request-Session ist längst geschlossen) und setzt:
- `status="stopped"` + `status_message=None` bei `result["ok"]`
- `status="error"` + gekürzten Fehlertext bei Fehler

Der `/status`-Endpoint überschreibt `"installing"`/`"updating"`/`"error"` bewusst NICHT mit dem Plugin-Live-Status — diese Werte sind reserviert für Background-Operationen, die ihren eigenen Zustand selbst zurücksetzen.

### 12.6 Pre-Start-Config-Patch (prepare_runtime)

Bei vielen Unreal-Engine-/Bohemia-Spielen werden CLI-Argumente wie `-Port=` / `-QueryPort=` IGNORIERT, weil das Game seine Ports aus INI-/CFG-Files liest. Damit der vom MSM-Port-Manager zugewiesene Port wirklich genutzt wird, hat `GamePlugin.prepare_runtime(server)` als Hook vor jedem Container-Start die Pflicht, die nötigen Config-Files zu patchen.

Beispiele:

- **Conan Exiles UE5**: `Engine.ini [URL] Port=`, `Engine.ini [OnlineSubsystemNull] GameServerQueryPort=`, `Game.ini [RconPlugin] RconPort=`/`RconEnabled=True`.
- Pterodactyl-Egg-Startscript dient als Referenz-Implementierung für das Mapping.

Werkzeug: `games.ini_utils.set_ini_value(path, section, key, value)`. KISS, zeilenorientiert — User-Edits an anderen Keys/Sektionen werden NIE überschrieben. Nur die exakten Port-Werte vom MSM-Modell werden bei jedem Start neu gesetzt (idempotent).

KEIN `configparser`, weil UE-INIs Duplikat-Keys und multi-line-Werte erlauben, die `configparser` zerstören würde.

### 12.7 Server-Delete (Cleanup-Reihenfolge)

`DELETE /api/servers/{id}` ist die zentrale, vollständige Lösch-Funktion. Reihenfolge ist verbindlich:

1. `docker_service.remove(name, force=True)` — stoppt + entfernt Container (idempotent, force killt auch laufende).
2. `close_ports(...)` — UFW-Regeln entfernen.
3. `shutil.rmtree(install_dir)` — Bind-Mount-Quelle vom Host löschen.
4. `shutil.rmtree("/opt/msm/backups/<id>")` — alle Backup-TARs entfernen (DB-Cascade räumt die Backup-Records).
5. `shutil.rmtree("backend/logs/<id>")` — MSM-Console-Logs entfernen.
6. `db.delete(server) + commit` — Cascade löscht `Permissions`, `Mods`, `Backups`.

Restore (`POST /api/backups/{id}/restore/{backup_id}`) stoppt + entfernt den Container VOR `tar -xzf`, damit der laufende Server keine Files mehr offen hält. Server-Status nach Restore: `"stopped"` (Nutzer startet manuell neu).

### 12.8 Rootless Docker

Rootless Docker ist Pflicht. Installation/Migration:

- `uidmap` und `dbus-user-session` müssen installiert sein.
- `/etc/subuid` und `/etc/subgid` müssen für `msm` mindestens `65536` IDs enthalten.
- Rootless-Setup läuft als `msm` über `dockerd-rootless-setuptool.sh install`.
- `loginctl enable-linger msm` hält den User-Daemon über Logouts und Reboots hinweg aktiv.
- Das Backend nutzt `MSM_DOCKER_HOST`/`DOCKER_HOST=unix:///run/user/<msm_uid>/docker.sock`.
- Bei Re-Install stoppt `install.sh` alte rootful `msm-srv-*` Container, löscht sie aber nicht automatisch.

Keine Game-Server-User-Units in Phase 6: Der direkte Container-Lifecycle bleibt der KISS-Pfad.

### 12.9 Keine 0.0.0.0-Bindings im Panel

Die Panel-API darf nur an `127.0.0.1` binden. Container-Port-Publishes dürfen `0.0.0.0` nutzen (Docker-Default), wenn die Game-Spielebene das benötigt. Optional pro Server: `server.public_bind_ip` setzt einen explizit gebundenen Host-Interface — empfohlen bei Multi-IP-Hosts.

Phase-2-Port-Manager wird das absichern (UFW-Regeln nur öffnen, wenn Container läuft).
