"""Central database backend policy for the MSM control plane."""

from __future__ import annotations


def validate_panel_database_url(database_url: str) -> str:
    """Return the URL or reject every database other than PostgreSQL.

    PostgreSQL is the only control-plane database — in operation and in the
    test suite alike. There is no exception for SQLite, not even for tests.
    """

    url = (database_url or "").strip()
    if not url:
        raise RuntimeError(
            "MSM_DATABASE_URL fehlt. MSM benötigt PostgreSQL als Panel-Datenbank."
        )
    if url.startswith(("postgresql://", "postgresql+psycopg2://")):
        return url
    if url.startswith("sqlite"):
        raise RuntimeError(
            "SQLite wird nicht unterstützt. MSM läuft ausschließlich auf PostgreSQL; "
            "eine alte SQLite-Installation muss zuerst mit einer älteren MSM-Version "
            "nach PostgreSQL umziehen."
        )
    raise RuntimeError("MSM unterstützt als Panel-Datenbank ausschließlich PostgreSQL.")
