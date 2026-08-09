"""Vertrag der Kubernetes-Manifeste (Phase 7).

Die Manifeste sind Betriebsdokumentation in ausfuehrbarer Form. Diese Tests
halten die Zusagen fest, die man beim Bearbeiten leicht versehentlich bricht —
insbesondere die, bei denen ein Fehler still zu doppelt ausgefuehrten
Serveraktionen oder zu offengelegten Geheimnissen fuehren wuerde.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

MANIFEST_DIR = Path(__file__).resolve().parents[2] / "deploy" / "kubernetes"


def _documents() -> list[dict]:
    docs: list[dict] = []
    for path in sorted(MANIFEST_DIR.glob("*.yaml")):
        if path.name == "10-secrets.example.yaml":
            # Reine Feldreferenz, wird nie angewendet.
            continue
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if doc:
                docs.append(doc)
    return docs


def _by_kind(kind: str) -> list[dict]:
    return [doc for doc in _documents() if doc.get("kind") == kind]


def _panel_deployment() -> dict:
    deployments = [d for d in _by_kind("Deployment") if d["metadata"]["name"] == "msm-panel"]
    assert len(deployments) == 1
    return deployments[0]


def _containers(doc: dict) -> list[dict]:
    return doc["spec"]["template"]["spec"]["containers"]


def test_manifests_exist_and_parse() -> None:
    assert MANIFEST_DIR.is_dir(), "deploy/kubernetes fehlt"
    assert _documents(), "keine Manifeste gefunden"


def test_panel_runs_exactly_once_and_never_overlaps_during_a_rollout() -> None:
    """Zwei gleichzeitige Panels wuerden Serveraktionen doppelt ausfuehren.

    Scheduler-Jobs, Lifecycle-Sperren und der Settings-Cache liegen im
    Prozessspeicher. `Recreate` stellt zusaetzlich sicher, dass sich alte und
    neue Instanz waehrend eines Updates nicht ueberlappen.
    """
    deployment = _panel_deployment()
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"


def test_dis_sidecar_is_in_the_panel_pod_and_has_no_service() -> None:
    """Der Sidecar bindet nur an 127.0.0.1.

    Er muss deshalb im selben Pod laufen. Ein Service dafuer waere ein
    Sicherheitsrueckschritt: die Krypto-Schnittstelle waere clusterweit
    erreichbar.
    """
    names = {c["name"] for c in _containers(_panel_deployment())}
    assert {"panel", "dis-sidecar"} <= names

    for service in _by_kind("Service"):
        selector = service["spec"].get("selector") or {}
        assert selector.get("app.kubernetes.io/component") != "dis-sidecar"
        for port in service["spec"].get("ports", []):
            assert port.get("port") != 9100, "DIS-Port darf nicht exponiert werden"


def test_panel_pod_never_acts_as_a_node_itself() -> None:
    """Im Pod gibt es keinen Rootless-Docker-Socket — also auch keinen lokalen Agent.

    MSM spricht auf dem Panel-Host mit dem Rootless-Docker-Socket des
    Panel-Users (`/run/user/<uid>/docker.sock`, siehe `docker_service`). Genau
    dieser Socket existiert in einem Pod nicht: er gehoert zu einer
    User-Systemd-Session auf einem Host.

    Steht `MSM_LOCAL_AGENT_ENABLED` auf `true`, versucht das Panel beim Start
    unter anderem den internen PostgreSQL-Container und die
    iptables-Baseline anzulegen — beides ueber genau diesen Socket. Der Fehler
    faellt erst zur Laufzeit auf und sieht aus wie ein Docker-Problem, nicht
    wie eine Fehlkonfiguration. Deshalb ist der Wert hier festgeschrieben.
    """
    config_maps = {
        cm["metadata"]["name"]: cm.get("data", {}) for cm in _by_kind("ConfigMap")
    }
    assert config_maps.get("msm-config", {}).get("MSM_LOCAL_AGENT_ENABLED") == "false"

    # Gegenprobe: kein Manifest darf einen Docker-Socket in den Pod mounten.
    for doc in _documents():
        spec = doc.get("spec", {}).get("template", {}).get("spec")
        if not spec:
            continue
        for volume in spec.get("volumes", []):
            host_path = (volume.get("hostPath") or {}).get("path", "")
            assert "docker.sock" not in host_path, (
                f"{volume['name']} mountet einen Docker-Socket in den Panel-Pod"
            )


def test_no_secret_value_is_inlined_in_any_manifest() -> None:
    """Geheimnisse kommen ausschliesslich per secretKeyRef aus dem Secret."""
    secret_names = {
        "MSM_SECRET_KEY",
        "MSM_DIS_SIDECAR_TOKEN",
        "MSM_DIS_SALT",
        "MSM_DATABASE_URL",
        "MSM_DATABASE_URL_ASYNC",
        "POSTGRES_PASSWORD",
    }
    for doc in _documents():
        assert doc.get("kind") != "Secret", "Kein Secret-Objekt im angewendeten Satz"
        spec = doc.get("spec", {}).get("template", {}).get("spec")
        if not spec:
            continue
        for container in spec.get("containers", []):
            for entry in container.get("env", []):
                if entry["name"] in secret_names:
                    assert "value" not in entry, (
                        f"{entry['name']} steht im Klartext im Manifest"
                    )
                    assert "secretKeyRef" in entry.get("valueFrom", {})

    # Auch die ConfigMap darf keinen geheimen Schluessel tragen.
    for config_map in _by_kind("ConfigMap"):
        assert not (secret_names & set(config_map.get("data", {})))


def test_every_container_declares_resource_limits() -> None:
    """Ohne Limits kann ein Container den Node fuer alle anderen verdraengen."""
    for doc in _documents():
        spec = doc.get("spec", {}).get("template", {}).get("spec")
        if not spec:
            continue
        for container in spec.get("containers", []):
            resources = container.get("resources", {})
            assert resources.get("limits", {}).get("memory"), container["name"]
            assert resources.get("limits", {}).get("cpu"), container["name"]
            assert resources.get("requests", {}).get("memory"), container["name"]


def test_workloads_run_unprivileged() -> None:
    for doc in _documents():
        spec = doc.get("spec", {}).get("template", {}).get("spec")
        if not spec:
            continue
        assert spec.get("securityContext", {}).get("runAsNonRoot") is True
        for container in spec.get("containers", []):
            security = container.get("securityContext", {})
            assert security.get("allowPrivilegeEscalation") is False, container["name"]
            assert security.get("capabilities", {}).get("drop") == ["ALL"], container["name"]


def test_panel_is_probed_on_the_real_health_endpoint() -> None:
    panel = next(c for c in _containers(_panel_deployment()) if c["name"] == "panel")
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert panel[probe]["httpGet"]["path"] == "/api/health", probe


def test_secret_reference_file_contains_no_usable_values() -> None:
    """Die Beispieldatei darf nie versehentlich echte Werte tragen."""
    example = MANIFEST_DIR / "10-secrets.example.yaml"
    doc = next(d for d in yaml.safe_load_all(example.read_text(encoding="utf-8")) if d)
    for key, value in (doc.get("stringData") or {}).items():
        assert value == "REPLACE_ME", f"{key} enthaelt einen konkreten Wert"
