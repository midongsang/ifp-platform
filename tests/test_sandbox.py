"""沙箱层测试：资源限制、超时、环境清洗、隔离工作目录与真实子命令执行。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from ifp import sandbox


def _run_python(code: str, **kwargs) -> sandbox.SandboxResult:
    """以当前解释器作为被执行程序，运行一段 Python 代码。"""

    return sandbox.run(["-c", code], program=[sys.executable], **kwargs)


def test_run_captures_stdout_and_reports_ok() -> None:
    result = _run_python("print('hello sandbox')")

    assert result.status == sandbox.STATUS_OK
    assert result.ok
    assert result.exit_code == 0
    assert result.stdout.strip() == "hello sandbox"
    assert result.duration_s >= 0


def test_nonzero_exit_maps_to_failed() -> None:
    result = _run_python("import sys; sys.exit(3)")

    assert result.status == sandbox.STATUS_FAILED
    assert not result.ok
    assert result.exit_code == 3


def test_unstartable_program_is_reported_not_raised() -> None:
    result = sandbox.run([], program=["/nonexistent/ifp-binary"])

    assert result.status == sandbox.STATUS_FAILED
    assert result.exit_code != 0
    assert "No such file" in result.stderr or "FileNotFoundError" in result.stderr


@pytest.mark.skipif(sandbox.resource is None, reason="POSIX 资源限制不可用")
def test_limits_are_applied_inside_the_child() -> None:
    result = _run_python(
        "import resource; print("
        "resource.getrlimit(resource.RLIMIT_CPU)[0],"
        "resource.getrlimit(resource.RLIMIT_AS)[0],"
        "resource.getrlimit(resource.RLIMIT_NOFILE)[0])",
        limits=sandbox.Limits(cpu_seconds=7, memory_mb=512, open_files=64),
    )

    assert result.stdout.split() == ["7", str(512 * 1024 * 1024), "64"]


def test_timeout_terminates_the_child() -> None:
    result = _run_python(
        "import time; time.sleep(30)",
        limits=sandbox.Limits(timeout_s=0.4, cpu_seconds=0, memory_mb=0),
    )

    assert result.status == sandbox.STATUS_TIMEOUT
    assert "0.4" in result.detail
    assert result.duration_s < 10


def test_secrets_are_stripped_from_child_env(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "super-secret")

    result = _run_python(
        "import os; print(os.environ.get('GITHUB_TOKEN'), os.environ.get('IFP_SANDBOX_TASK'))"
    )

    assert result.stdout.split() == ["None", "1"]


def test_child_runs_in_an_isolated_temp_dir_that_is_removed() -> None:
    result = _run_python("import os; print(os.getcwd())")

    workdir = Path(result.stdout.strip())
    assert str(workdir).startswith(tempfile.gettempdir())
    assert workdir.name.startswith("ifp-sandbox-")
    assert not workdir.exists()


def test_output_is_truncated() -> None:
    result = _run_python("print('x' * 400)", limits=sandbox.Limits(max_output_chars=20))

    assert result.stdout.startswith("x" * 20)
    assert "输出已截断" in result.stdout


def test_limits_describe_lists_enabled_caps() -> None:
    assert sandbox.Limits(timeout_s=30, cpu_seconds=5, memory_mb=256).describe() == (
        "超时 30s / CPU 5s / 内存 256MB / 单文件 256MB"
    )
    assert sandbox.Limits(cpu_seconds=0, memory_mb=0).describe() == "超时 120s / 单文件 256MB"


def test_limits_supported_matches_platform() -> None:
    supported, detail = sandbox.limits_supported()

    assert supported is (sandbox.resource is not None)
    assert detail


def test_ifp_command_runs_inside_the_sandbox(refund_dir: Path) -> None:
    result = sandbox.run(["check", str(refund_dir)], limits=sandbox.Limits(timeout_s=120))

    assert result.status == sandbox.STATUS_OK, result.stderr
    assert "检查完成" in result.stdout
