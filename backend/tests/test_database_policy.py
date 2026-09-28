import pytest

from database_policy import validate_panel_database_url


def test_postgresql_is_the_only_runtime_database() -> None:
    url = "postgresql+psycopg2://msm:synthetic@127.0.0.1:5432/msm"
    assert validate_panel_database_url(url) == url


@pytest.mark.parametrize("url", ["sqlite:///./msm.db", "sqlite:///:memory:", "sqlite://"])
def test_sqlite_is_rejected_even_in_the_test_environment(url: str, monkeypatch) -> None:
    """`MSM_TESTING` oeffnete bis 09/2026 den Weg zu SQLite; das gibt es nicht mehr."""
    monkeypatch.setenv("MSM_TESTING", "true")
    with pytest.raises(RuntimeError, match="SQLite wird nicht unterstützt"):
        validate_panel_database_url(url)


@pytest.mark.parametrize("url", ["", "mysql://localhost/msm", "mariadb://localhost/msm"])
def test_missing_or_other_database_backends_are_rejected(url: str) -> None:
    with pytest.raises(RuntimeError):
        validate_panel_database_url(url)
