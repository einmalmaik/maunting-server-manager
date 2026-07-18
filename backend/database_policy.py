"""Central database backend policy for the MSM control plane."""

from __future__ import annotations


def validate_panel_database_url(
    database_url: str,
    *,
    testing: bool = False,
    sqlite_migration: bool = False,
) -> str:
    """Return a normalized URL or reject unsupported runtime databases.

    PostgreSQL is the only supported control-plane database. SQLite may only
    be opened by tests or by the explicit one-time migration tool.
    """

    url = (database_url or "").strip()
    if not url:
        raise RuntimeError(
            "MSM_DATABASE_URL fehlt. MSM benötigt PostgreSQL als Panel-Datenbank."
        )
    if url.startswith(("postgresql://", "postgresql+psycopg2://")):
        return url
    # The migration CLI opens its SQLite source through a dedicated read-only
    # engine; it must never turn the full panel runtime into SQLite mode.
    if url.startswith("sqlite") and testing:
        return url
    if url.startswith("sqlite"):
        raise RuntimeError(
            "SQLite ist keine unterstützte Panel-Betriebsdatenbank mehr. "
            "Führe zuerst den einmaligen SQLite-nach-PostgreSQL-Import aus."
        )
    raise RuntimeError("MSM unterstützt als Panel-Datenbank ausschließlich PostgreSQL.")
