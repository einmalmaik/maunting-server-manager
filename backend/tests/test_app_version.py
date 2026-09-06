from main import app_version


def test_app_version_returns_correct_version():
    res = app_version()
    assert res["name"] == "Maunting Server Manager"
    assert res["version"] == "4.3.4"
