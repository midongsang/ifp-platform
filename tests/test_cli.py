"""CLI 端到端测试（Typer CliRunner）。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ifp.cli import app

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()
REFUND_DIR = EXAMPLES / "refund"
REFUND_FILE = REFUND_DIR / "refund.ifp"


def _run(*args: str, input: str | None = None):
    return runner.invoke(app, list(args), input=input)


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


def test_check_nonexistent_path_exits_with_usage_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.ifp"
    result = _run("check", str(missing))

    assert result.exit_code == 2
    assert "路径不存在" in result.stdout


def test_check_reads_from_stdin(tmp_path: Path) -> None:
    source = (
        "context DemoContext {\n"
        "  intent Demo { field a: Decimal = 0\n  invariant ok { expr: a >= 0 } }\n"
        "}\n"
    )
    result = _run("check", "-", input=source)

    assert result.exit_code == 0
    assert "0 error" in result.stdout


def test_check_supports_glob_pattern(tmp_path: Path) -> None:
    (tmp_path / "a.ifp").write_text(
        "context DemoContext { intent Demo { field a: Decimal = 0 } }", encoding="utf-8"
    )
    (tmp_path / "b.ifp").write_text(
        "context DemoContext2 { intent Demo2 { field a: Decimal = 0 } }", encoding="utf-8"
    )

    result = _run("check", str(tmp_path / "*.ifp"))

    assert result.exit_code == 0
    assert "检查完成" in result.stdout


def test_check_deduplicates_repeated_path(tmp_path: Path) -> None:
    """同一文件传入两次时不应产生重复诊断。"""

    src = "context DemoContext { intent Demo { field a: Decimal = 0 } }"
    f = tmp_path / "x.ifp"
    f.write_text(src, encoding="utf-8")

    once = _run("check", str(f))
    twice = _run("check", str(f), str(f))

    assert once.stdout == twice.stdout


def test_run_rejects_zero_jobs() -> None:
    result = _run("run", str(REFUND_DIR), "--job", "check", "-j", "0")

    assert result.exit_code == 2
    assert "并发度" in result.stdout


def test_run_rejects_stdin() -> None:
    result = _run("run", "-", "--job", "check", input="x")

    assert result.exit_code == 2
    assert "stdin" in result.stdout


def test_check_prints_positive_pass_message() -> None:
    result = _run("check", str(REFUND_FILE))

    assert result.exit_code == 0
    assert "检查通过" in result.stdout


def test_parse_error_renders_source_snippet(tmp_path: Path) -> None:
    bad = tmp_path / "bad.ifp"
    bad.write_text("context Broken {\n    intent X { field a: Unknown\n}\n", encoding="utf-8")
    result = _run("check", str(bad))

    assert result.exit_code == 1
    assert "| ^" in result.stdout  # 出错行的插入符指示
