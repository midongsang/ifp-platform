"""用户偏好（prefs）测试。"""

from __future__ import annotations

from ifp import prefs


def test_path_follows_config_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("IFP_CONFIG_HOME", str(tmp_path))
    assert prefs.path() == tmp_path / "prefs.json"
    assert prefs.config_dir() == tmp_path


def test_defaults_when_unset(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("IFP_CONFIG_HOME", str(tmp_path))
    assert prefs.get("update.auto_check") is False
    assert prefs.get("update.source") == prefs.DEFAULTS["update"]["source"]
    assert prefs.get("update.last_check") == 0.0
    assert prefs.get("no.such.key") is None
    assert prefs.get("no.such.key", "fallback") == "fallback"


def test_set_value_persists_and_creates_nested(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("IFP_CONFIG_HOME", str(tmp_path))

    assert prefs.set_value("update.auto_check", True) is True
    assert prefs.get("update.auto_check") is True

    prefs.set_value("update.source", "acme/ifp")
    assert prefs.get("update.source") == "acme/ifp"

    prefs.set_value("a.b.c", 3)
    assert prefs.get("a.b.c") == 3

    # 重新读取（不依赖缓存）后仍保持一致
    assert prefs.load()["a"]["b"]["c"] == 3


def test_load_corrupt_file_returns_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("IFP_CONFIG_HOME", str(tmp_path))
    (tmp_path / "prefs.json").write_text("{ not valid json", encoding="utf-8")

    assert prefs.load() == {}
    assert prefs.get("update.auto_check") is False


def test_load_non_dict_returns_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("IFP_CONFIG_HOME", str(tmp_path))
    (tmp_path / "prefs.json").write_text("[1, 2, 3]", encoding="utf-8")

    assert prefs.load() == {}


def test_coerce_values() -> None:
    assert prefs.coerce("TRUE") is True
    assert prefs.coerce("on") is True
    assert prefs.coerce("off") is False
    assert prefs.coerce("no") is False
    assert prefs.coerce("42") == 42
    assert prefs.coerce("owner/repo") == "owner/repo"
