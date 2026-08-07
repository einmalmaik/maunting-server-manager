# MSM auf Kubernetes

Reproduzierbare Bereitstellung der **MSM Control Plane** (Panel, DIS-Sidecar,
optional PostgreSQL) mit kontrollierten Rollouts, Wiederanlauf nach Ausfällen,
Ressourcengrenzen und Secrets, die nie im Klartext in Manifesten oder Logs
landen.

## Was das ist — und was ausdrücklich nicht

**Kubernetes betreibt hier die Control Plane, nicht die Gameserver.**

MSM führt Gameserver als Docker-Container über den `msm-agent` auf angebundenen
Hosts (Nodes) aus. Guardian, Lifecycle, Portvergabe, Bind-Mounts und die
Node-Isolation setzen genau darauf auf. Gameserver in Pods zu verwandeln wäre
kein Deployment-Thema, sondern ein Umbau dieser gesamten Schicht — und würde die
bestehende Self-Hosted-Installation brechen.

Deshalb gilt:

| | Läuft wo |
| --- | --- |
| Panel (API + Frontend) | Kubernetes-Pod |
| DIS-Sidecar (Krypto) | Container **im selben Pod** wie das Panel |
| PostgreSQL | Kubernetes-StatefulSet **oder** extern/managed (empfohlen) |
| Gameserver | Docker auf den angebundenen MSM-Nodes, wie bisher |
| `msm-agent` | Auf den Node-Hosts, wie bisher |

Kapazität wächst also weiterhin über **weitere MSM-Nodes**, nicht über weitere
Panel-Replicas. Das Panel bleibt die zentrale Steuerung; die Nodes führen aus.

## Warum genau eine Panel-Replica

`replicas: 1` und `strategy: Recreate` sind **keine Vorsichtsmaßnahme, sondern
eine Korrektheitsbedingung**. Das Panel hält prozesslokalen Zustand, der bei
zwei gleichzeitigen Instanzen doppelt oder widersprüchlich wirken würde:

- **APScheduler-Jobs** (Auto-Restart, Backups, Guardian-Reconciliation,
  Hoster-Webhooks, Node-Heartbeat) würden doppelt feuern.
- **Lifecycle-Job-Sperren** liegen im Prozessspeicher; eine zweite Instanz sähe
  laufende Operationen nicht und könnte parallel starten oder stoppen.
- **Panel-Settings-Cache** wird nicht prozessübergreifend invalidiert.
- **Startup-Reconciliation** würde die Aufgaben der jeweils anderen Instanz als
  unterbrochen bewerten.

`Recreate` stellt sicher, dass während eines Rollouts nie zwei Panels
gleichzeitig laufen. Der Preis ist eine kurze Downtime beim Update — bewusst
gewählt gegenüber doppelt ausgeführten Serveraktionen.

## Voraussetzungen

- Kubernetes ≥ 1.27
- Ein Ingress-Controller mit TLS (das Panel setzt `__Secure-`-Cookies und
  funktioniert nur über HTTPS)
- Panel-Image, das Backend **und** gebautes Frontend enthält, sowie ein
  DIS-Sidecar-Image (siehe `docs/self-hosting.md` zu den Release-Artefakten)
- Netzwerkweg vom Panel zu den `msm-agent`-Hosts (ausgehend, TLS)

## Installation

```bash
kubectl apply -f deploy/kubernetes/00-namespace.yaml
```

Secrets **nicht** aus einer Datei im Repository anlegen. Entweder über einen
externen Secret-Manager (External Secrets Operator, Vault, SOPS) oder direkt:

```bash
kubectl -n msm create secret generic msm-secrets \
  --from-literal=MSM_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=MSM_DIS_SIDECAR_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=MSM_DIS_SALT="$(openssl rand -hex 16)" \
  --from-literal=MSM_DATABASE_URL='postgresql+psycopg2://msm:PASSWORT@msm-postgres:5432/msm' \
  --from-literal=MSM_DATABASE_URL_ASYNC='postgresql+asyncpg://msm:PASSWORT@msm-postgres:5432/msm' \
  --from-literal=POSTGRES_PASSWORD='PASSWORT'
```

`10-secrets.example.yaml` ist nur eine **Feldreferenz** und enthält bewusst
keine verwendbaren Werte.

Danach:

```bash
kubectl apply -f deploy/kubernetes/20-postgres.yaml     # entfällt bei externer DB
kubectl apply -f deploy/kubernetes/30-config.yaml
kubectl apply -f deploy/kubernetes/40-panel.yaml
kubectl apply -f deploy/kubernetes/50-ingress.yaml
kubectl apply -f deploy/kubernetes/60-networkpolicy.yaml
```

Oder gesammelt:

```bash
kubectl apply -k deploy/kubernetes
```

## Nodes anbinden

Unverändert gegenüber der klassischen Installation: Der `msm-agent` wird auf dem
Host installiert und im Panel unter **Administration → Nodes** verbunden. Siehe
[`docs/self-hosting.md`](../../docs/self-hosting.md#einen-neuen-node-verbinden).
Kubernetes ändert daran nichts — die Node-Hosts müssen keine Cluster-Mitglieder
sein.

## Updates und Rollback

```bash
# Update
kubectl -n msm set image deployment/msm-panel panel=<registry>/msm-panel:<neue-version>
kubectl -n msm rollout status deployment/msm-panel

# Rollback
kubectl -n msm rollout undo deployment/msm-panel
```

**Wichtig:** Datenbankmigrationen laufen beim Panel-Start. Ein Rollback auf ein
älteres Image nach einer bereits angewendeten Migration ist nicht automatisch
sicher. Vor einem Update ein Panel-Backup ziehen
(Administration → Panel-Backups) und die Migrationshinweise der Release-Notes
lesen.

## Secrets im Betrieb

- Alle Geheimnisse kommen als `secretKeyRef` aus dem Secret, nie als
  Literal in einem Manifest, nie in einer ConfigMap.
- Der DIS-Sidecar lauscht ausschließlich auf `127.0.0.1`. Er ist deshalb ein
  Container **im selben Pod** — es gibt bewusst keinen Service und keinen
  ClusterIP für ihn. Damit ist die Krypto-Schnittstelle für andere Pods
  unerreichbar.
- Rotation von `MSM_SECRET_KEY` ist **nicht** ohne Weiteres möglich: damit
  verschlüsselte Werte (E-Mail-Adressen, gespeicherte Zugangsdaten) wären danach
  nicht mehr lesbar. Siehe `docs/self-hosting.md` zur Secret-Rotation.

## Beobachtbarkeit

`GET /api/health` liefert `{"status": "ok"}`, sobald der Prozess bereit ist, und
wird als Readiness- und Liveness-Probe verwendet. Der Serverzustand selbst
gehört in das Panel und in die Guardian-Ansicht — nicht in Kubernetes-Probes:
ein fehlgeschlagener Gameserver ist kein ungesunder Panel-Pod.
