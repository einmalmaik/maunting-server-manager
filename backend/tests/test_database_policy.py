import pytest

from database_policy import validate_panel_database_url


def test_postgresql_is_the_only_runtime_database() -> None:
    url = "postgresql+psycopg2://msm:synthetic@127.0.0.1:5432/msm"
    assert validate_panel_database_url(url) == url


def test_sqlite_runtime_is_rejected_with_actionable_error() -> None:
    with pytest.raises(RuntimeError, match="SQLite.*keine unterstützte"):
        validate_panel_database_url("sqlite:///./msm.db")


def test_sqlite_is_limited_to_tests_or_explicit_migration() -> None:
    url = "sqlite:///:memory:"
    assert validate_panel_database_url(url, testing=True) == url
    with pytest.raises(RuntimeError, match="SQLite"):
        validate_panel_database_url(url, sqlite_migration=True)


@pytest.mark.parametrize("url", ["", "mysql://localhost/msm", "mariadb://localhost/msm"])
def test_missing_or_other_database_backends_are_rejected(url: str) -> None:
    with pytest.raises(RuntimeError):
        validate_panel_database_url(url)
