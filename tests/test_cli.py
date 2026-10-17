"""CLI 端到端测试（Typer CliRunner）。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ifp.cli import app

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()
REFUND_DIR = EXAMPLES / "refund"
REFUND_FILE = REFUND_DIR / "refund.ifp"


def _run(*args: str):
    return runner.invoke(app, list(args))


def test_version() -> None:
    result = _run("version")
    assert result.exit_code == 0
    assert "ifp-platform" in result.stdout


def test_check_directory_succeeds() -> None:
    result = _run("check", str(REFUND_DIR))
    assert result.exit_code == 0
    assert "0 error" in result.stdout


def test_verify_finds_counterexample_and_exits_nonzero() -> None:
    result = _run("verify", str(REFUND_FILE))
    assert result.exit_code == 1
    assert "totalRefundNotExceedPaid" in result.stdout


def test_simulate_all_pass() -> None:
    result = _run("simulate", str(REFUND_DIR))
    assert result.exit_code == 0
    assert "4 passed" in result.stdout


def test_baseline_lists_invariants() -> None:
    result = _run("baseline", str(REFUND_FILE))
    assert result.exit_code == 0
    assert "totalRefundNotExceedPaid" in result.stdout


def test_export_to_file(tmp_path: Path) -> None:
    out = tmp_path / "refund.json"
    result = _run("export", str(REFUND_FILE), "--format", "json", "-o", str(out))

    assert result.exit_code == 0
    assert out.exists()
    assert "RefundContext" in out.read_text(encoding="utf-8")


def test_init_scaffolds_files(tmp_path: Path) -> None:
    target = tmp_path / "newmodel"
    result = _run("init", str(target))

    assert result.exit_code == 0
    assert (target / "model.ifp").exists()
    assert (target / "property_test.yaml").exists()


def test_scaffold_passes_check(tmp_path: Path) -> None:
    """脚手架生成的模型必须能直接通过类型检查（无 error）。"""

    target = tmp_path / "newmodel"
    _run("init", str(target))
    result = _run("check", str(target))

    assert result.exit_code == 0
    assert "0 error" in result.stdout


def test_check_reports_parse_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.ifp"
    bad.write_text("context Broken { not valid }", encoding="utf-8")
    result = _run("check", str(bad))

    assert result.exit_code == 1
