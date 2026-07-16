"""HTTP/WS client for MSM Agent nodes.

Decrypts node.auth_token_enc via DIS (AAD msm:node:auth_token) in memory only.
Never logs, persists, or returns the plaintext token to callers beyond request headers.

Phase 5: TLS fingerprint pinning for remote/self-signed agents.
"""

from __future__ import annotations

import logging
import ssl
from typing import Any, Iterator
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx

from services.dis_client import DisClient, DisSidecarError
from services.tls_pinning import build_pinned_ssl_context, normalize_fingerprint

logger = logging.getLogger(__name__)

NODE_TOKEN_AAD = "msm:node:auth_token"

# Default timeouts — agent ops can include image pull / large uploads
_DEFAULT_TIMEOUT = 30.0
_LONG_TIMEOUT = 600.0


class NodeClientError(Exception):
    """Agent unreachable or returned an error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NodeClient:
    """Thin HTTP client for one agent instance.

    KISS: one class, no manager registry. Construct per request/operation.
    """

    def __init__(
        self,
        host: str,
        token: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        tls_fingerprint: str | None = None,
        require_tls_pin: bool = False,
    ) -> None:
        base = (host or "").strip().rstrip("/")
        if not base:
            raise NodeClientError("Node host is empty")
        if "://" not in base:
            # Remote pins imply HTTPS; otherwise default http for local dev
            scheme = "https" if (tls_fingerprint or require_tls_pin) else "http"
            base = f"{scheme}://{base}"
        self._base = base
        self._token = token
        self._timeout = timeout
        self._tls_fingerprint = normalize_fingerprint(tls_fingerprint) or None
        self._require_tls_pin = require_tls_pin
        self._ssl_context: ssl.SSLContext | bool | None = None
        self._validate_tls_policy()

    def _validate_tls_policy(self) -> None:
        """Remote agents must use HTTPS + fingerprint pin (MITM protection)."""
        parsed = urlparse(self._base)
        scheme = (parsed.scheme or "").lower()
        if self._require_tls_pin:
            if scheme != "https":
                raise NodeClientError(
                    "Remote nodes require HTTPS (self-signed TLS + fingerprint pin)"
                )
            if not self._tls_fingerprint:
                raise NodeClientError(
                    "Remote nodes require a TLS certificate fingerprint (SHA-256)"
                )
        if scheme == "https" and self._tls_fingerprint:
            # Pin will be applied when building client
            return
        if scheme == "https" and not self._tls_fingerprint and self._require_tls_pin:
            raise NodeClientError("TLS fingerprint required for remote HTTPS agent")

    def _verify(self) -> ssl.SSLContext | bool:
        """httpx verify= argument: pinned SSLContext, True (CA), or False (dev only)."""
        if self._ssl_context is not None:
            return self._ssl_context
        parsed = urlparse(self._base)
        if (parsed.scheme or "").lower() != "https":
            self._ssl_context = True
            return self._ssl_context
        if self._tls_fingerprint:
            try:
                self._ssl_context = build_pinned_ssl_context(self._base, self._tls_fingerprint)
            except ValueError as exc:
                raise NodeClientError(str(exc) or "TLS fingerprint check failed") from exc
            return self._ssl_context
        # HTTPS without pin: system CAs only (public certs). Self-signed will fail.
        self._ssl_context = True
        return self._ssl_context

    # ── Factory ──────────────────────────────────────────────────────────

    @classmethod
    def from_node(cls, node: Any, *, timeout: float = _DEFAULT_TIMEOUT) -> "NodeClient":
        """Build client from a Node ORM object (decrypts token in-memory)."""
        if node is None:
            raise NodeClientError("Node is required")
        enc = getattr(node, "auth_token_enc", None) or ""
        if not enc:
            raise NodeClientError("Node has no auth token")
        try:
            token = DisClient.decrypt(enc, aad=NODE_TOKEN_AAD)
        except (DisSidecarError, Exception) as exc:
            logger.warning("node token decrypt failed for node_id=%s", getattr(node, "id", "?"))
            raise NodeClientError("Could not decrypt node auth token") from exc
        is_local = bool(getattr(node, "is_local", False))
        fp = getattr(node, "tls_fingerprint", None)
        host = getattr(node, "host", "") or ""
        # Remote production policy: non-local nodes must pin TLS.
        # Loopback http fixtures/tests without is_local stay pin-optional.
        host_l = host.lower()
        loopback_http = host_l.startswith("http://127.") or host_l.startswith(
            "http://localhost"
        )
        require_pin = (not is_local) and (not loopback_http)
        return cls(
            host=host,
            token=token,
            timeout=timeout,
            tls_fingerprint=fp,
            require_tls_pin=require_pin,
        )

    # ── Internals ────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, path: str) -> str:
        return urljoin(self._base + "/", path.lstrip("/"))

    def _ws_url(self, path: str) -> str:
        parsed = urlparse(self._base)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        netloc = parsed.netloc or parsed.path
        return urlunparse((scheme, netloc, path, "", "", ""))

    def _httpx_client(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=timeout, verify=self._verify())

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | list | None = None,
        params: dict | None = None,
        content: bytes | None = None,
        files: Any = None,
        timeout: float | None = None,
        expect_json: bool = True,
    ) -> Any:
        t = timeout if timeout is not None else self._timeout
        try:
            with self._httpx_client(t) as client:
                resp = client.request(
                    method,
                    self._url(path),
                    headers=self._headers(),
                    json=json,
                    params=params,
                    content=content,
                    files=files,
                )
        except NodeClientError:
            raise
        except httpx.HTTPError as exc:
            logger.warning("node agent request failed path=%s", path)
            raise NodeClientError("Agent not reachable") from exc

        if resp.status_code == 401:
            raise NodeClientError("Agent authentication failed", status_code=401)
        if resp.status_code >= 400:
            detail = _safe_detail(resp)
            raise NodeClientError(detail or f"Agent error HTTP {resp.status_code}", status_code=resp.status_code)

        if not expect_json:
            return resp.content
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except Exception:
            return {}

    # ── Health / metrics ─────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        """Unauthenticated health — does not send bearer token."""
        try:
            with self._httpx_client(5.0) as client:
                resp = client.get(self._url("/health"))
            if resp.status_code != 200:
                raise NodeClientError(f"Agent health HTTP {resp.status_code}", status_code=resp.status_code)
            data = resp.json()
            if data.get("status") != "ok" or data.get("docker_connected") is not True:
                raise NodeClientError("Agent Docker runtime is unavailable", status_code=503)
            return data
        except NodeClientError:
            raise
        except httpx.HTTPError as exc:
            raise NodeClientError("Agent not reachable") from exc

    def metrics(self) -> dict[str, Any]:
        return self._request("GET", "/metrics")

    # ── Containers ───────────────────────────────────────────────────────

    def list_containers(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/containers")
        return data if isinstance(data, list) else []

    def create_container(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/containers", json=body, timeout=_LONG_TIMEOUT)

    def run_ephemeral_container(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/containers/ephemeral/run", json=body, timeout=_LONG_TIMEOUT)

    def install_http_source(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/sources/http", json=body, timeout=_LONG_TIMEOUT)

    def install_github_source(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/sources/github", json=body, timeout=_LONG_TIMEOUT)

    def start_container(self, name: str) -> dict[str, Any]:
        return self._request("POST", f"/containers/{quote(name, safe='')}/start")

    def stop_container(self, name: str, timeout: int | None = None) -> dict[str, Any]:
        body = {"timeout": timeout} if timeout is not None else None
        return self._request("POST", f"/containers/{quote(name, safe='')}/stop", json=body)

    def restart_container(self, name: str, timeout: int | None = None) -> dict[str, Any]:
        body = {"timeout": timeout} if timeout is not None else None
        return self._request("POST", f"/containers/{quote(name, safe='')}/restart", json=body)

    def remove_container(self, name: str) -> dict[str, Any]:
        return self._request("DELETE", f"/containers/{quote(name, safe='')}")

    def container_stats(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"/containers/{quote(name, safe='')}/stats")

    def container_logs(self, name: str, tail: int = 200) -> str:
        data = self._request("GET", f"/containers/{quote(name, safe='')}/logs", params={"tail": tail})
        return str(data.get("logs", ""))

    def exec_in_container(self, name: str, command: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/containers/{quote(name, safe='')}/exec",
            json={"command": command},
            timeout=_LONG_TIMEOUT,
        )

    def update_container_resources(self, name: str, updates: dict[str, int | None]) -> dict[str, Any]:
        return self._request("PATCH", f"/containers/{quote(name, safe='')}/resources", json=updates)

    def send_container_stdin(self, name: str, data: str) -> dict[str, Any]:
        return self._request("POST", f"/containers/{quote(name, safe='')}/stdin", json={"data": data})

    # ── Files ────────────────────────────────────────────────────────────

    def files_list(self, server_id: int | str, path: str = "") -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "/files/list",
            params={"server_id": str(server_id), "path": path or ""},
        )
        return data if isinstance(data, list) else []

    def files_read(self, server_id: int | str, path: str) -> str:
        data = self._request(
            "GET",
            "/files/read",
            params={"server_id": str(server_id), "path": path},
        )
        return str(data.get("content", ""))

    def files_write(self, server_id: int | str, path: str, content: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/files/write",
            params={"server_id": str(server_id), "path": path},
            json={"content": content},
        )

    def files_delete(self, server_id: int | str, path: str) -> dict[str, Any]:
        return self._request(
            "DELETE",
            "/files/delete",
            params={"server_id": str(server_id), "path": path},
        )

    def files_rename(self, server_id: int | str, old_path: str, new_path: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/files/rename",
            params={"server_id": str(server_id)},
            json={"old_path": old_path, "new_path": new_path},
        )

    def files_mkdir(self, server_id: int | str, path: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/files/create-dir",
            params={"server_id": str(server_id), "path": path},
        )

    def files_ensure_server_root(self, server_id: int | str) -> dict[str, Any]:
        return self._request(
            "PUT",
            "/files/server-root",
            params={"server_id": str(server_id)},
        )

    def files_delete_server_root(self, server_id: int | str) -> dict[str, Any]:
        return self._request(
            "DELETE",
            "/files/server-root",
            params={"server_id": str(server_id)},
        )

    def files_prepare_runtime(self, server_id: int | str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/files/prepare-runtime",
            params={"server_id": str(server_id)},
            json=body,
        )

    def files_disk_info(self, server_id: int | str) -> dict[str, int]:
        return self._request("GET", "/files/disk", params={"server_id": str(server_id)})

    def ports_available(self, ports: list[tuple[int, str, str]], bind_ip: str) -> dict[str, Any]:
        return self._request("POST", "/runtime/ports/check", json={
            "ports": [{"port": port, "protocol": protocol, "role": role} for port, protocol, role in ports],
            "bind_ip": bind_ip or "0.0.0.0",
        })

    def firewall_update(self, action: str, server_name: str, ports: list[tuple[int, str, str]]) -> dict[str, Any]:
        if action not in {"open", "close"}:
            raise ValueError("Invalid firewall action")
        return self._request("POST", f"/runtime/firewall/{action}", json={
            "server_name": server_name,
            "ports": [{"port": port, "protocol": protocol, "role": role} for port, protocol, role in ports],
        })

    def files_search(self, server_id: int | str, query: str) -> dict[str, Any]:
        return self._request("GET", "/files/search", params={"server_id": str(server_id), "q": query})

    def files_workshop(self, server_id: int | str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/files/workshop",
            params={"server_id": str(server_id)},
            json=body,
            timeout=_LONG_TIMEOUT,
        )

    def files_move(self, server_id: int | str, source_path: str, target_path: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/files/move",
            params={"server_id": str(server_id)},
            json={"source_path": source_path, "target_path": target_path},
        )

    def files_extract(self, server_id: int | str, path: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/files/extract",
            params={"server_id": str(server_id), "path": path},
        )

    def files_upload_init(self, server_id: int | str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/files/upload/init", params={"server_id": str(server_id)}, json=body)

    def files_upload_chunk(self, server_id: int | str, upload_id: str, data: bytes) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/files/upload/{quote(upload_id, safe='')}/chunk",
            params={"server_id": str(server_id)},
            files={"chunk": ("chunk", data)},
            timeout=_LONG_TIMEOUT,
        )

    def files_upload_status(self, server_id: int | str, upload_id: str) -> dict[str, Any]:
        return self._request("GET", f"/files/upload/{quote(upload_id, safe='')}/status", params={"server_id": str(server_id)})

    def files_upload_finalize(self, server_id: int | str, upload_id: str) -> dict[str, Any]:
        return self._request("POST", f"/files/upload/{quote(upload_id, safe='')}/finalize", params={"server_id": str(server_id)})

    def files_upload_abort(self, server_id: int | str, upload_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/files/upload/{quote(upload_id, safe='')}", params={"server_id": str(server_id)})

    def files_cache_configs(self, server_id: int | str, patterns: list[str]) -> dict[str, Any]:
        return self._request("POST", "/files/config-cache/create", params={"server_id": str(server_id)}, json={"patterns": patterns})

    def files_restore_configs(self, server_id: int | str) -> dict[str, Any]:
        return self._request("POST", "/files/config-cache/restore", params={"server_id": str(server_id)})

    def files_clear_config_cache(self, server_id: int | str) -> dict[str, Any]:
        return self._request("DELETE", "/files/config-cache", params={"server_id": str(server_id)})

    def files_upload(self, server_id: int | str, path: str, data: bytes, filename: str = "upload") -> dict[str, Any]:
        return self._request(
            "POST",
            "/files/upload",
            params={"server_id": str(server_id), "path": path},
            files={"file": (filename, data)},
            timeout=_LONG_TIMEOUT,
        )

    def files_download(self, server_id: int | str, path: str) -> bytes:
        return self._request(
            "GET",
            "/files/download",
            params={"server_id": str(server_id), "path": path},
            timeout=_LONG_TIMEOUT,
            expect_json=False,
        )

    def files_archive(
        self,
        server_id: int | str,
        *,
        postgres: dict[str, Any] | None = None,
    ) -> Iterator[bytes]:
        """Stream a tar.gz of the server directory from the agent."""
        try:
            with self._httpx_client(_LONG_TIMEOUT) as client:
                with client.stream(
                    "POST" if postgres else "GET",
                    self._url("/files/archive"),
                    headers=self._headers(),
                    params={"server_id": str(server_id)},
                    json={"postgres": postgres} if postgres else None,
                ) as resp:
                    if resp.status_code >= 400:
                        detail = resp.read().decode("utf-8", errors="replace")[:200]
                        raise NodeClientError(
                            detail or f"Agent archive HTTP {resp.status_code}",
                            status_code=resp.status_code,
                        )
                    for chunk in resp.iter_bytes(64 * 1024):
                        if chunk:
                            yield chunk
        except NodeClientError:
            raise
        except httpx.HTTPError as exc:
            logger.warning("node agent archive stream failed")
            raise NodeClientError("Agent not reachable") from exc

    def files_restore_archive(self, server_id: int | str, archive_path: str) -> dict[str, Any]:
        try:
            with open(archive_path, "rb") as archive:
                return self._request(
                    "POST",
                    "/files/restore-archive",
                    params={"server_id": str(server_id)},
                    files={"archive": ("backup.tar.gz", archive, "application/gzip")},
                    timeout=_LONG_TIMEOUT,
                )
        except OSError as exc:
            raise NodeClientError("Backup archive cannot be read") from exc

    def files_finalize_restore(self, server_id: int | str) -> dict[str, Any]:
        return self._request("POST", "/files/restore-archive/finalize", params={"server_id": str(server_id)})

    def files_rollback_restore(self, server_id: int | str) -> dict[str, Any]:
        return self._request("POST", "/files/restore-archive/rollback", params={"server_id": str(server_id)})

    def console_ws_url(self, container_name: str) -> str:
        return self._ws_url(f"/console/{quote(container_name, safe='')}/ws")

    # ── Phase 6: agent-direct S3 backup/restore ───────────────────────────

    def backup_create_s3(
        self,
        server_id: int | str,
        *,
        s3_config: dict[str, Any],
        encryption_key_b64: str,
        s3_key: str,
        timeout: float = _LONG_TIMEOUT,
        postgres: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trigger encrypted backup on agent → direct S3 upload (no panel data path)."""
        return self._request(
            "POST",
            "/backup/create",
            json={
                "server_id": str(server_id),
                "s3_config": s3_config,
                "encryption_key": encryption_key_b64,
                "s3_key": s3_key,
                "postgres": postgres,
            },
            timeout=timeout,
        )

    def backup_restore_s3(
        self,
        server_id: int | str,
        *,
        s3_config: dict[str, Any],
        encryption_key_b64: str,
        s3_key: str,
        timeout: float = _LONG_TIMEOUT,
        postgres: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Trigger S3 download + decrypt + extract on agent."""
        return self._request(
            "POST",
            "/backup/restore",
            json={
                "server_id": str(server_id),
                "s3_config": s3_config,
                "encryption_key": encryption_key_b64,
                "s3_key": s3_key,
                "postgres": postgres,
            },
            timeout=timeout,
        )

    # ── Phase 7: managed Postgres on the node ─────────────────────────────
    # Passwords only in request body (TLS to agent). Never log payloads.

    def postgres_ensure(self, *, admin_password: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/postgres/ensure",
            json={"admin_password": admin_password},
            timeout=_LONG_TIMEOUT,
        )

    def postgres_provision(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/postgres/provision",
            json=payload,
            timeout=_LONG_TIMEOUT,
        )

    def postgres_create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/postgres/users/create", json=payload)

    def postgres_rotate_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/postgres/users/rotate", json=payload)

    def postgres_drop(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/postgres/drop", json=payload)

    def postgres_query(self, payload: dict[str, Any]) -> Any:
        return self._request(
            "POST",
            "/postgres/query",
            json=payload,
            timeout=_LONG_TIMEOUT,
        )

    def postgres_promote(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/postgres/roles/promote", json=payload)

    def postgres_demote(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/postgres/roles/demote", json=payload)

    def postgres_rotate_owner(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/postgres/roles/rotate-owner", json=payload)

    def postgres_dump(
        self, *, admin_password: str, database_names: list[str]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/postgres/dump",
            json={"admin_password": admin_password, "database_names": database_names},
            timeout=_LONG_TIMEOUT,
        )

    def postgres_restore(
        self,
        *,
        admin_password: str,
        dumps: dict[str, str],
        owners: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/postgres/restore",
            json={"admin_password": admin_password, "dumps": dumps, "owners": owners or {}},
            timeout=_LONG_TIMEOUT,
        )

    @property
    def bearer_token(self) -> str:
        """In-memory token for WS upgrade only — caller must not log/store."""
        return self._token


def _safe_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, str) and detail:
            return detail[:300]
    except Exception:
        pass
    return f"Agent error HTTP {resp.status_code}"
