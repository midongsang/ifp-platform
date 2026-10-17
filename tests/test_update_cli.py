"""`ifp update` / `ifp config` / `ifp doctor` 的 CLI 端到端测试（网络全部打桩）。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from ifp import __version__, updater
from ifp.cli import app

runner = CliRunner()


def _release(tag: str = "v9.9.9") -> updater.ReleaseInfo:
    return updater.ReleaseInfo(
        tag=tag,
        name=f"IFP {tag}",
        published_at="2026-10-16T08:00:00Z",
        html_url="https://example.test/releases",
        notes="",
        assets=(updater.Asset("ifp-9.9.9-py3-none-any.whl", 2048, "https://d"),),
    )


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("IFP_CONFIG_HOME", str(tmp_path))


# --- config -----------------------------------------------------------------

def test_config_set_get_list_path(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)

    assert runner.invoke(app, ["config", "set", "update.auto_check", "true"]).exit_code == 0

    got = runner.invoke(app, ["config", "get", "update.auto_check"])
    assert got.exit_code == 0 and "true" in got.stdout

    listed = runner.invoke(app, ["config", "list"])
    assert listed.exit_code == 0 and "auto_check" in listed.stdout

    where = runner.invoke(app, ["config", "path"])
    assert where.exit_code == 0 and "prefs.json" in where.stdout


def test_config_get_missing_key_exits_nonzero(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    result = runner.invoke(app, ["config", "get", "no.such.key"])
    assert result.exit_code == 1


# --- update -----------------------------------------------------------------

def test_update_check_reports_time_and_size(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "fetch_latest", lambda *args, **kwargs: _release())

    result = runner.invoke(app, ["update", "--check"])

    assert result.exit_code == 0
    assert "v9.9.9" in result.stdout
    assert "2026-10-16" in result.stdout
    assert "2.0 KB" in result.stdout


def test_update_up_to_date(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "fetch_latest", lambda *args, **kwargs: _release(tag=f"v{__version__}"))

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert "已是最新版本" in result.stdout


def test_update_yes_installs(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "fetch_latest", lambda *args, **kwargs: _release())
    monkeypatch.setattr(updater, "install_release", lambda release, **kwargs: "ifp.whl")

    result = runner.invoke(app, ["update", "--yes"])

    assert result.exit_code == 0
    assert "已更新" in result.stdout


def test_update_declined_skips(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "fetch_latest", lambda *args, **kwargs: _release())

    result = runner.invoke(app, ["update"], input="n\n")

    assert result.exit_code == 0
    assert "已跳过更新" in result.stdout


def test_update_offline_exits_one(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)

    def boom(*args, **kwargs):
        raise updater.UpdateError("无法访问更新源（离线或网络受限）：network down")

    monkeypatch.setattr(updater, "fetch_latest", boom)

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 1
    assert "更新检查失败" in result.stdout


def test_update_install_failure_exits_one(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "fetch_latest", lambda *args, **kwargs: _release())

    def boom(release, **kwargs):
        raise updater.UpdateError("pip 安装失败：boom")

    monkeypatch.setattr(updater, "install_release", boom)

    result = runner.invoke(app, ["update", "--yes"])

    assert result.exit_code == 1
    assert "更新失败" in result.stdout


def test_update_json_output(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "fetch_latest", lambda *args, **kwargs: _release())

    result = runner.invoke(app, ["update", "--check", "--json"])
    payload = json.loads(result.stdout)

    assert payload["update_available"] is True
    assert payload["current"] == __version__
    assert payload["latest"]["tag"] == "v9.9.9"
    assert payload["latest"]["assets"][0]["size_human"] == "2.0 KB"


def test_update_auto_toggle_persists(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)

    off = runner.invoke(app, ["update", "--no-auto"])
    assert off.exit_code == 0 and "关闭" in off.stdout

    got = runner.invoke(app, ["config", "get", "update.auto_check"])
    assert "false" in got.stdout


def test_update_source_override_persists(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "fetch_latest", lambda *args, **kwargs: _release())

    result = runner.invoke(app, ["update", "--check", "--source", "acme/ifp"])
    assert result.exit_code == 0 and "acme/ifp" in result.stdout

    got = runner.invoke(app, ["config", "get", "update.source"])
    assert "acme/ifp" in got.stdout


# --- version / doctor -------------------------------------------------------

def test_version_check_hints_new_release(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(updater, "fetch_latest", lambda *args, **kwargs: _release())

    result = runner.invoke(app, ["version", "--check"])

    assert result.exit_code == 0
    assert f"ifp-platform {__version__}" in result.stdout
    assert "发现新版本" in result.stdout


def test_version_auto_check_disabled_by_default(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(updater, "fetch_latest", lambda *args, **kwargs: calls.append(1) or _release())

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert calls == []  # 默认不自动联网


def test_version_auto_check_silent_on_failure(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    runner.invoke(app, ["update", "--auto"])

    def boom(*args, **kwargs):
        raise updater.UpdateError("offline")

    monkeypatch.setattr(updater, "fetch_latest", boom)

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "发现新版本" not in result.stdout


def test_doctor_passes(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "工具链自检" in result.stdout
    assert "环境自检通过" in result.stdout
