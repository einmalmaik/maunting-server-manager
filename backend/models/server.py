from datetime import datetime, timezone

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    game_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    install_dir: Mapped[str] = mapped_column(String(512), nullable=False)

    # Node Zuordnung
    node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nodes.id"), nullable=True, index=True
    )
    node: Mapped["Node"] = relationship("Node", back_populates="servers")

    # Docker-Runtime: stabiler Container-Name (msm-srv-<id>) wird zur Laufzeit
    # vom Plugin via `container_name_for(server.id)` generiert. Hier wird der
    # konkret zuletzt verwendete Name gecached für Debug-/Audit-Zwecke.
    container_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Status
    # Freie Strings: stopped, running, installing, updating, error, awaiting_files
    status: Mapped[str] = mapped_column(String(32), default="stopped")
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Auto-Restart
    # WICHTIG: Nur EIN Modus aktiv (Intervall oder feste Zeiten).
    # Die _normalize_server_restart_mode im Router stellt Exklusivitaet sicher.
    # Intervall hat Vorrang. Siehe sync_server_restart_schedule und AGENTS.md (KISS + Rootless).
    auto_restart: Mapped[bool] = mapped_column(Boolean, default=False)

    # Auth-Setup-Recovery: True waehrend der Container im TTY-Modus laeuft und
    # auf User-Interaktion wartet (z.B. Hytale OAuth-Refresh). Wird vom
    # Auth-Setup-Recovery-Thread gesetzt und vom Cancel-Endpoint zurueckgesetzt.
    # UI rendert einen Warn-Banner solange True. Default False (kein laufender Auth-Flow).
    auth_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    restart_interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    restart_time_utc: Mapped[str | None] = mapped_column(String(8), nullable=True)  # HH:MM
    restart_times_utc: Mapped[str | None] = mapped_column(String(256), nullable=True)  # HH:MM,HH:MM
    last_auto_restart_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_auto_restart_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_auto_restart_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Backup-Scheduling
    # Hinweis für zukünftige Erweiterung (analog zu Restart):
    # - Aktuell nur backup_interval_hours (IntervalTrigger).
    # - Später kann backup_times_utc (String, "HH:MM,HH:MM") hinzugefügt werden,
    #   symmetrisch zu restart_times_utc.
    # - Die Zeitangaben sind als UTC-intendierte HH:MM gespeichert (wie bei Restart).
    # - time_format (globales Panel-Setting) ist reine UI-Anzeigepräferenz (12h/24h)
    #   und beeinflusst nicht die Speicherung/Scheduling-Logik.
    # - Beide Systeme (Restart + Backup) behandeln Zeiten konsistent über UTC-Strings.
    backup_on_start: Mapped[bool] = mapped_column(Boolean, default=False)
    backup_interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 24=daily, 168=weekly, 720=monthly
    backup_retention_count: Mapped[int] = mapped_column(Integer, default=5)

    # Ressourcen
    cpu_limit_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ram_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_limit_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Netzwerk — Ports (automatisch vergeben, aber überschreibbar)
    ports: Mapped[list["ServerPort"]] = relationship(
        "ServerPort", back_populates="server", cascade="all, delete-orphan"
    )

    def set_port(self, role: str, port: int | None, protocol: str = "udp") -> None:
        if port is None:
            self.ports = [p for p in self.ports if p.role != role]
            return
        for p in self.ports:
            if p.role == role:
                p.port = port
                p.protocol = protocol
                return
        from models.server_port import ServerPort
        self.ports.append(ServerPort(role=role, port=port, protocol=protocol))

    @property
    def game_port(self) -> int | None:
        for p in self.ports:
            if p.role == "game":
                return p.port
        return None

    @game_port.setter
    def game_port(self, val: int | None) -> None:
        self.set_port("game", val, "udp")

    @property
    def query_port(self) -> int | None:
        for p in self.ports:
            if p.role == "query":
                return p.port
        return None

    @query_port.setter
    def query_port(self, val: int | None) -> None:
        self.set_port("query", val, "udp")

    @property
    def rcon_port(self) -> int | None:
        for p in self.ports:
            if p.role == "rcon":
                return p.port
        return None

    @rcon_port.setter
    def rcon_port(self, val: int | None) -> None:
        self.set_port("rcon", val, "tcp")

    # Optional: bestimmte Host-IP, an die Container-Ports gebunden werden.
    # None = alle Interfaces (Docker-Default 0.0.0.0). Empfehlung im UI:
    # NUR setzen, wenn der Host mehrere externe IPs hat.
    public_bind_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Soft-Disk-Limit-Tracking (in MB, wird vom Scheduler aktualisiert)
    disk_usage_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    server_permissions: Mapped[list["ServerPermission"]] = relationship(
        "ServerPermission", back_populates="server", cascade="all, delete-orphan"
    )
    backups: Mapped[list["Backup"]] = relationship("Backup", back_populates="server", cascade="all, delete-orphan")
    mods: Mapped[list["Mod"]] = relationship("Mod", back_populates="server", cascade="all, delete-orphan")
    postgres_databases: Mapped[list["PostgresDatabase"]] = relationship(
        "PostgresDatabase", back_populates="server", cascade="all, delete-orphan"
    )
    postgres_users: Mapped[list["PostgresUser"]] = relationship(
        "PostgresUser", back_populates="server", cascade="all, delete-orphan"
    )
    incidents: Mapped[list["Incident"]] = relationship(
        "Incident", back_populates="server", cascade="all, delete-orphan"
    )
    change_events: Mapped[list["ChangeEvent"]] = relationship(
        "ChangeEvent", back_populates="server", cascade="all, delete-orphan"
    )

    @property
    def started_at(self) -> datetime | None:
        return self.last_started_at

    @property
    def uptime_seconds(self) -> int | None:
        if self.status != "running" or not self.last_started_at:
            return None
        started = self.last_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))

    @property
    def next_auto_restart_at(self) -> datetime | None:
        if not self.auto_restart:
            return None
        try:
            from services.scheduler_service import get_next_restart_run_time
            return get_next_restart_run_time(self.id)
        except Exception:
            return None
