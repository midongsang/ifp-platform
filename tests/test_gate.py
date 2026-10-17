"""CI 语义门禁测试：`ifp diff` 的目录 / JSON / GitHub / 退出码能力。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ifp.cli import app
from ifp.diff import diff_paths

runner = CliRunner()

# 含基线不变量：修改后累积风险 >= 40，且带 BASELINE 标记 -> 需强制审批
_OLD = """
context DemoContext {
    intent Demo {
        field x: Decimal = 0
        invariant bound {
            expr: x <= 10
            baseline: true
            owner: "team"
            risk: "超限"
        }
        action Set {
            pre: true
            post: x == 1
        }
    }
}
"""

_NEW = """
context DemoContext {
    intent Demo {
        field x: Decimal = 0
        invariant bound {
            expr: x <= 20
            baseline: true
            owner: "team"
            risk: "超限"
        }
        action Set {
            pre: true
            post: x == 1
        }
    }
}
"""

# 非基线不变量：修改风险 25 < 40，默认阈值下不需强制审批
_PLAIN_OLD = _OLD.replace("            baseline: true\n", "")
_PLAIN_NEW = _NEW.replace("            baseline: true\n", "")

_EXTRA = """
context ExtraContext {
    intent Extra {
        field y: Decimal = 0
        invariant nonneg {
            expr: y >= 0
            owner: "extra-team"
        }
    }
}
"""


def _write(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(text, encoding="utf-8")
    return target


def test_diff_paths_matches_files_by_relative_path(tmp_path: Path) -> None:
    """目录 Diff 按相对路径匹配，新增文件被识别并带上文件来源。"""

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    _write(old_dir, "a.ifp", _OLD)
    _write(new_dir, "a.ifp", _NEW)
    _write(new_dir, "b.ifp", _EXTRA)

    result = diff_paths(old_dir, new_dir)
    files = {c.file for c in result.changes}

    assert "a.ifp" in files
    assert "b.ifp" in files
    assert any(c.path.startswith("context ExtraContext") for c in result.changes)


def test_cli_diff_directory_reports_baseline_change(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    _write(old_dir, "a.ifp", _OLD)
    _write(new_dir, "a.ifp", _NEW)

    result = runner.invoke(app, ["diff", str(old_dir), str(new_dir)])

    assert result.exit_code == 0
    assert "a.ifp: DemoContext/Demo/invariant bound" in result.stdout
    assert "需强制审批：是" in result.stdout


def test_cli_diff_writes_machine_readable_report(tmp_path: Path) -> None:
    old_file = _write(tmp_path, "old.ifp", _OLD)
    new_file = _write(tmp_path, "new.ifp", _NEW)
    report_path = tmp_path / "out" / "gate.json"

    result = runner.invoke(
        app, ["diff", str(old_file), str(new_file), "--json", str(report_path)]
    )

    assert result.exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["approval_required"] is True
    assert report["baseline_change"] is True
    assert report["risk_score"] >= 40
    assert report["threshold"] == 40
    assert report["changes"][0]["baseline"] is True


def test_cli_diff_github_writes_job_outputs(tmp_path: Path, monkeypatch) -> None:
    old_file = _write(tmp_path, "old.ifp", _OLD)
    new_file = _write(tmp_path, "new.ifp", _NEW)
    output_file = tmp_path / "github_output"
    output_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    result = runner.invoke(
        app, ["diff", str(old_file), str(new_file), "--github"]
    )

    assert result.exit_code == 0
    written = output_file.read_text(encoding="utf-8")
    assert "approval_required=true" in written
    assert "risk_score=" in written
    assert "::warning::" in result.stdout


def test_cli_diff_fail_on_approval_exits_nonzero(tmp_path: Path) -> None:
    old_file = _write(tmp_path, "old.ifp", _OLD)
    new_file = _write(tmp_path, "new.ifp", _NEW)

    result = runner.invoke(
        app, ["diff", str(old_file), str(new_file), "--fail-on-approval"]
    )

    assert result.exit_code == 1


def test_cli_diff_below_threshold_needs_no_approval(tmp_path: Path) -> None:
    """非基线、低风险变更在默认阈值下无需强制审批。"""

    old_file = _write(tmp_path, "old.ifp", _PLAIN_OLD)
    new_file = _write(tmp_path, "new.ifp", _PLAIN_NEW)

    result = runner.invoke(
        app, ["diff", str(old_file), str(new_file), "--fail-on-approval"]
    )

    assert result.exit_code == 0
    assert "需强制审批：否" in result.stdout


def test_cli_diff_writes_audit_log(tmp_path: Path) -> None:
    old_file = _write(tmp_path, "old.ifp", _OLD)
    new_file = _write(tmp_path, "new.ifp", _NEW)
    audit_path = tmp_path / "audit.jsonl"

    result = runner.invoke(
        app, ["diff", str(old_file), str(new_file), "--audit", str(audit_path)]
    )

    assert result.exit_code == 0
    entry = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert entry["event"] == "diff"
    assert entry["payload"]["approval_required"] is True
