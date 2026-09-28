from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class PostgresInstance(Base):
    """Eigene PostgreSQL-Instanz eines Datenbankservers.

    Ein Datenbankserver ist ein normaler Server mit der Blueprint ``postgres``;
    Container, Ports, Limits und Lebenszyklus gehoeren dem Server. Hier steht
    nur, was es beim geteilten ``msm-postgres`` nicht gibt: das Superuser-
    Passwort dieser einen Instanz und ihre Netzwerkfreigabe.
    """

    __tablename__ = "postgres_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    # DIS, AAD ``msm:pg:instance:<server_id>`` — nie im Klartext, nie in Antworten.
    admin_password_encrypted: Mapped[str] = mapped_column(String(4096), nullable=False)
    # Von aussen erreichbar ist die Instanz, wenn ihr Port nicht auf 127.0.0.1
    # gebunden ist (``server.public_bind_ip``) — eine zweite Wahrheit dafuer gibt
    # es bewusst nicht. Wer dann hinein darf, sagt diese Liste: eine CIDR je
    # Zeile; leer heisst niemand von aussen.
    allowed_cidrs: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ssl_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    server = relationship("Server", back_populates="postgres_instance")
