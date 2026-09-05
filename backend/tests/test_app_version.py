from main import app_version


def test_app_version_returns_correct_version():
    res = app_version()
    assert isinstance(res, dict)
    assert res["version"] == "4.2.6"
