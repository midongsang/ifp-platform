"""`ifp run` 与 `--sandbox` 的 CLI 端到端测试：真实启动沙箱子进程。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ifp.cli import app

runner = CliRunner()

BAD_MODEL = "context Broken { not valid }"

SCENARIOS = (
    "PartialRefundOnce",
    "FullRefundBoundary",
    "AuditRejectedWhenNotPending",
    "RefundRejectedWhenNotApproved",
)


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IFP_CONFIG_HOME", str(tmp_path))


def _bad_model(tmp_path: Path) -> Path:
    path = tmp_path / "broken.ifp"
    path.write_text(BAD_MODEL, encoding="utf-8")
    return path


# --- ifp run ----------------------------------------------------------------

def test_run_simulate_schedules_one_task_per_scenario(monkeypatch, tmp_path, refund_dir) -> None:
    _isolate(monkeypatch, tmp_path)

    result = runner.invoke(app, ["run", str(refund_dir), "--job", "simulate", "-j", "2"])

    assert result.exit_code == 0
    for name in SCENARIOS:
        assert name in result.stdout
    assert "ok 4" in result.stdout


def test_run_check_keeps_a_directory_as_one_task(monkeypatch, tmp_path, refund_dir) -> None:
    _isolate(monkeypatch, tmp_path)

    result = runner.invoke(app, ["run", str(refund_dir), "--job", "check"])

    assert result.exit_code == 0
    assert "ok 1" in result.stdout


def test_run_verify_reports_known_counterexample(monkeypatch, tmp_path, refund_dir) -> None:
    _isolate(monkeypatch, tmp_path)

    result = runner.invoke(app, ["run", str(refund_dir), "--job", "verify"])

    assert result.exit_code == 1
    assert "failed 1" in result.stdout


def test_run_json_payload_shape(monkeypatch, tmp_path, refund_dir) -> None:
    _isolate(monkeypatch, tmp_path)

    result = runner.invoke(app, ["run", str(refund_dir), "--job", "check", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["job"] == "check"
    assert payload["jobs"] == 4
    assert payload["limits"]["timeout_s"] == 120
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["counts"]["ok"] == 1
    assert payload["summary"]["counts"]["failed"] == 0
    assert payload["tasks"][0]["status"] == "ok"
    assert payload["tasks"][0]["kind"] == "check"
    assert payload["tasks"][0]["argv"][0] == "check"


def test_run_rejects_unknown_job(monkeypatch, tmp_path, refund_dir) -> None:
    _isolate(monkeypatch, tmp_path)

    result = runner.invoke(app, ["run", str(refund_dir), "--job", "nope"])

    assert result.exit_code == 2
    assert "未知任务类型" in result.stdout


def test_run_fail_fast_skips_remaining_tasks(monkeypatch, tmp_path, refund_model_path) -> None:
    _isolate(monkeypatch, tmp_path)
    bad = _bad_model(tmp_path)

    result = runner.invoke(
        app,
        ["run", str(bad), str(refund_model_path), "--job", "check", "--fail-fast", "-j", "1"],
    )

    assert result.exit_code == 1
    assert "failed 1" in result.stdout
    assert "skipped 1" in result.stdout


def test_run_respects_sandbox_prefs(monkeypatch, tmp_path, refund_dir) -> None:
    _isolate(monkeypatch, tmp_path)
    assert runner.invoke(app, ["config", "set", "sandbox.timeout", "240"]).exit_code == 0

    result = runner.invoke(app, ["run", str(refund_dir), "--job", "check", "--json"])

    assert json.loads(result.stdout)["limits"]["timeout_s"] == 240


def test_run_overrides_limits_from_flags(monkeypatch, tmp_path, refund_dir) -> None:
    _isolate(monkeypatch, tmp_path)

    result = runner.invoke(
        app, ["run", str(refund_dir), "--job", "check", "--json", "--timeout", "9", "--cpu", "3"]
    )

    limits = json.loads(result.stdout)["limits"]
    assert limits["timeout_s"] == 9
    assert limits["cpu_seconds"] == 3


# --- --sandbox 单跑 ---------------------------------------------------------

def test_simulate_sandbox_flag_runs_in_child(monkeypatch, tmp_path, refund_dir) -> None:
    _isolate(monkeypatch, tmp_path)

    result = runner.invoke(app, ["simulate", str(refund_dir), "--sandbox"])

    assert result.exit_code == 0
    assert "在沙箱中执行" in result.stdout
    assert "4 passed / 0 failed" in result.stdout


def test_verify_sandbox_flag_passes_through_child_output(monkeypatch, tmp_path, refund_model_path) -> None:
    _isolate(monkeypatch, tmp_path)

    result = runner.invoke(app, ["verify", str(refund_model_path), "--sandbox"])

    assert result.exit_code == 1  # 示例按预期存在反例
    assert "在沙箱中执行" in result.stdout
    assert "反例" in result.stdout


def test_sandbox_timeout_is_enforced(monkeypatch, tmp_path, refund_model_path) -> None:
    _isolate(monkeypatch, tmp_path)
    assert runner.invoke(app, ["config", "set", "sandbox.timeout", "0.05"]).exit_code == 0

    result = runner.invoke(app, ["verify", str(refund_model_path), "--sandbox"])

    assert result.exit_code == 1
    assert "超时" in result.stdout
