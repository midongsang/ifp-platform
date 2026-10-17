"""环境自检：帮助新用户快速确认依赖、内置资源与工具链可用，并给出排障提示。

设计要点：
- 只做只读探测，不改动用户环境；
- 任一检查失败都返回可读提示，供 CLI 汇总，而不是抛异常；
- 默认不触网，「更新源可达」检查仅在显式要求时执行。
"""

from __future__ import annotations

import importlib
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from . import parser, prefs
from .errors import Severity
from .typecheck import typecheck

_MIN_PYTHON = (3, 11)
_REQUIRED_DEPS = ("lark", "z3", "typer", "rich", "yaml")
_SAMPLE = """context DoctorContext {
    intent DoctorAggregate {
        field amount: Decimal = 0
        invariant amountNonNegative {
            expr: amount >= 0
        }
    }
}
"""


@dataclass(frozen=True)
class Check:
    """一项自检结果。"""

    name: str
    ok: bool
    detail: str
    hint: str = ""


def _check_python() -> Check:
    required = ".".join(str(part) for part in _MIN_PYTHON)
    ok = sys.version_info >= _MIN_PYTHON
    return Check(
        "Python 版本",
        ok,
        f"当前 {platform.python_version()}（要求 >= {required}）",
        "" if ok else f"请升级到 Python {required} 或更高版本",
    )


def _check_deps() -> Check:
    missing = []
    for name in _REQUIRED_DEPS:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    ok = not missing
    return Check(
        "运行时依赖",
        ok,
        "全部可导入" if ok else f"缺失：{', '.join(missing)}",
        "" if ok else '请安装依赖：pip install -e ".[dev]"',
    )


def _check_config_dir() -> Check:
    directory = prefs.config_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".ifp-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(
            "偏好目录可写",
            False,
            f"{directory}：{exc}",
            "请检查目录权限，或用 IFP_CONFIG_HOME 指定可写目录",
        )
    return Check("偏好目录可写", True, str(directory))


def _check_resources() -> Check:
    base = Path(__file__).resolve().parent
    missing = [name for name in ("grammar.lark", "webassets/index.html") if not (base / name).exists()]
    ok = not missing
    return Check(
        "内置资源",
        ok,
        "语法与 Web 资源完整" if ok else f"缺失：{', '.join(missing)}",
        "" if ok else "安装包可能不完整，请重新安装",
    )


def _check_toolchain() -> Check:
    try:
        model = parser.parse(_SAMPLE, filename="<doctor>")
    except Exception as exc:  # 解析层异常种类多，统一转为可读提示
        return Check("工具链自检", False, f"解析示例模型失败：{exc}", "环境异常，请提交 issue")
    errors = [diag for diag in typecheck(model) if diag.severity is Severity.ERROR]
    ok = not errors
    return Check(
        "工具链自检",
        ok,
        "解析与类型检查通过" if ok else f"类型检查发现 {len(errors)} 个错误",
        "" if ok else errors[0].format(),
    )


def _check_sandbox() -> Check:
    """在沙箱里跑一次 ``ifp version``，确认子进程调度与资源限制可用。"""

    from .sandbox import STATUS_OK, Limits, limits_supported, run

    _, detail = limits_supported()
    try:
        result = run(["version"], limits=Limits(cpu_seconds=20, memory_mb=1024, timeout_s=30))
    except Exception as exc:  # 沙箱探测的失败种类多，统一转为可读提示
        return Check("沙箱执行", False, f"沙箱子进程探测失败：{exc}", "请检查子进程与临时目录权限")
    if result.status != STATUS_OK:
        reason = result.detail or result.stderr.strip() or result.status
        return Check("沙箱执行", False, f"沙箱探测异常：{reason}", "请检查子进程与临时目录权限")
    version = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return Check("沙箱执行", True, f"{detail}；子进程自检 {version}".rstrip())


def _check_update_source(source: str, timeout: float) -> Check:
    from .updater import UpdateError, fetch_latest

    try:
        release = fetch_latest(source, timeout=timeout)
    except UpdateError as exc:
        return Check(
            "更新源可达",
            False,
            str(exc),
            "离线环境可忽略；必要时设置代理或 GITHUB_TOKEN",
        )
    return Check("更新源可达", True, f"{source} 最新发行：{release.tag}")


def run_checks(*, online: bool = False, source: str | None = None, timeout: float = 6.0) -> list[Check]:
    """运行全部自检，返回结果列表。"""

    from .updater import DEFAULT_SOURCE

    checks = [
        _check_python(),
        _check_deps(),
        _check_config_dir(),
        _check_resources(),
        _check_toolchain(),
        _check_sandbox(),
    ]
    if online:
        resolved = source or prefs.get("update.source") or DEFAULT_SOURCE
        checks.append(_check_update_source(str(resolved), timeout))
    return checks
