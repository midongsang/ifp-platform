"""隔离沙箱：把一次校验 / 仿真任务放进受限制的子进程执行。

设计要点：
- 只依赖标准库（``subprocess`` / ``resource`` / ``tempfile`` / ``json``）；
- 资源限制在子进程内部施加（``setrlimit``），因此不依赖 ``preexec_fn``：
  调度器用线程并发时也不会撞上 fork 后再 exec 的死锁风险；
- 子进程在独立临时目录中运行，并剥离环境变量里的访问令牌；
- 超时通过终止独立进程组实现，避免留下孤儿进程；
- 所有可预期失败（超时 / 非零退出 / 无法启动）都转成 :class:`SandboxResult`，
  由调用方渲染，不抛异常中断整批任务。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

try:  # POSIX 专有：用于施加资源限制，Windows 上不可用
    import resource
except ImportError:  # pragma: no cover - 非 POSIX 平台
    resource = None  # type: ignore[assignment]

# ----------------------------- 默认上限 ------------------------------------

DEFAULT_TIMEOUT_S = 120.0
DEFAULT_CPU_SECONDS = 60
DEFAULT_MEMORY_MB = 4096
DEFAULT_FILE_SIZE_MB = 256
DEFAULT_OPEN_FILES = 256
DEFAULT_MAX_OUTPUT_CHARS = 64 * 1024

# ----------------------------- 任务状态 ------------------------------------

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_BLOCKED = "blocked"

# 子进程会继承父进程环境，但凭证类变量一律剥离，避免模型读到令牌。
_SECRET_KEYS = ("GITHUB_TOKEN", "GH_TOKEN")

# 被信号终止时给出可读原因，避免只暴露一个负退出码。
_SIGNAL_HINTS = {
    "SIGXCPU": "超过 CPU 时间上限（sandbox.cpu）",
    "SIGKILL": "可能超过内存上限（sandbox.memory），或被系统强制回收",
    "SIGSEGV": "进程崩溃（段错误），通常是模型过大或环境异常",
    "SIGABRT": "进程异常中止",
    "SIGBUS": "进程内存访问异常",
}

_DEFAULT_PROGRAM: tuple[str, ...] = (sys.executable, "-m", "ifp")

_BOOTSTRAP = """\
import json, os, sys

try:
    import resource
except ImportError:
    resource = None

payload = json.loads(sys.argv[1])
if resource is not None:
    attempts = []
    if payload["cpu_seconds"] > 0:
        attempts.append(
            (resource.RLIMIT_CPU, (payload["cpu_seconds"], payload["cpu_seconds"] + 5))
        )
    if payload["memory_mb"] > 0:
        attempts.append((resource.RLIMIT_AS, (payload["memory_mb"] * 1024 * 1024,) * 2))
    if payload["file_size_mb"] > 0:
        attempts.append((resource.RLIMIT_FSIZE, (payload["file_size_mb"] * 1024 * 1024,) * 2))
    if payload["open_files"] > 0:
        attempts.append((resource.RLIMIT_NOFILE, (payload["open_files"],) * 2))
    for which, value in attempts:
        try:
            resource.setrlimit(which, value)
        except (OSError, ValueError):
            pass

command = payload["command"]
os.execvp(command[0], command)
"""


@dataclass(frozen=True)
class Limits:
    """单个任务的资源上限；``0`` 表示该项不限制。"""

    timeout_s: float = DEFAULT_TIMEOUT_S
    cpu_seconds: int = DEFAULT_CPU_SECONDS
    memory_mb: int = DEFAULT_MEMORY_MB
    file_size_mb: int = DEFAULT_FILE_SIZE_MB
    open_files: int = DEFAULT_OPEN_FILES
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS

    def describe(self) -> str:
        """渲染为一行可读的上限说明。"""

        parts = [f"超时 {self.timeout_s:g}s"]
        if self.cpu_seconds > 0:
            parts.append(f"CPU {self.cpu_seconds}s")
        if self.memory_mb > 0:
            parts.append(f"内存 {self.memory_mb}MB")
        if self.file_size_mb > 0:
            parts.append(f"单文件 {self.file_size_mb}MB")
        return " / ".join(parts)


@dataclass(frozen=True)
class SandboxResult:
    """一次沙箱执行的结果。"""

    argv: tuple[str, ...]
    status: str
    exit_code: int
    duration_s: float
    stdout: str = ""
    stderr: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def limits_supported() -> tuple[bool, str]:
    """返回（是否可施加 POSIX 资源限制，说明），供 ``ifp doctor`` 展示。"""

    if resource is None:
        return False, f"当前平台无 resource 模块，仅施加墙钟超时（{DEFAULT_TIMEOUT_S:g}s）"
    return True, "CPU / 内存 / 文件大小 / 句柄数限制可用，子进程在隔离临时目录中运行"


def run(
    argv: Sequence[str],
    *,
    limits: Optional[Limits] = None,
    program: Optional[Sequence[str]] = None,
    cwd: Optional[Path] = None,
    keep_workdir: bool = False,
) -> SandboxResult:
    """在沙箱子进程中执行 ``program + argv``。

    ``program`` 默认为 ``[sys.executable, "-m", "ifp"]``，即以当前 IFP CLI 作为被执行程序。
    """

    limits = limits or Limits()
    command = [*(program if program is not None else _DEFAULT_PROGRAM), *(str(item) for item in argv)]

    workdir = Path(cwd) if cwd is not None else Path(tempfile.mkdtemp(prefix="ifp-sandbox-"))
    workdir.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(
        {
            "cpu_seconds": limits.cpu_seconds,
            "memory_mb": limits.memory_mb,
            "file_size_mb": limits.file_size_mb,
            "open_files": limits.open_files,
            "command": command,
        }
    )
    spawn = [sys.executable, "-c", _BOOTSTRAP, payload]

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            spawn,
            cwd=str(workdir),
            env=_sanitized_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        duration = time.monotonic() - started
        _cleanup(workdir)
        return SandboxResult(
            tuple(command), STATUS_BLOCKED, -1, duration, detail=f"无法启动沙箱子进程：{exc}"
        )

    detail = ""
    try:
        out, err = proc.communicate(timeout=limits.timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        status = STATUS_TIMEOUT
        detail = f"超过墙钟超时 {limits.timeout_s:g}s，已终止；可用 sandbox.timeout 调高上限"
    else:
        code = proc.returncode if proc.returncode is not None else -1
        status = STATUS_OK if code == 0 else STATUS_FAILED
        if code < 0:  # 被信号终止：说明是哪一类限制 / 异常，而不是只给一个负退出码
            detail = _signal_detail(-code)
    finally:
        if not keep_workdir:
            _cleanup(workdir)

    duration = time.monotonic() - started
    return SandboxResult(
        argv=tuple(command),
        status=status,
        exit_code=proc.returncode if proc.returncode is not None else -1,
        duration_s=duration,
        stdout=_truncate(out or "", limits.max_output_chars),
        stderr=_truncate(err or "", limits.max_output_chars),
        detail=detail,
    )


def _sanitized_env() -> dict[str, str]:
    """复制父进程环境，剥离令牌类变量，并标记沙箱身份。"""

    env = {key: value for key, value in os.environ.items() if key not in _SECRET_KEYS}
    env["IFP_SANDBOX_TASK"] = "1"
    return env


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """终止子进程所在进程组；平台不支持时退化为终止单个进程。"""

    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except OSError:
            pass
    proc.kill()


def _cleanup(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)


def _signal_detail(signum: int) -> str:
    """把负退出码（被信号终止）翻译为可读原因。"""

    try:
        name = signal.Signals(signum).name
    except ValueError:  # pragma: no cover - 未知信号
        name = f"信号 {signum}"
    hint = _SIGNAL_HINTS.get(name, "")
    text = f"子进程被信号终止：{name}"
    return f"{text}（{hint}）" if hint else text


def _truncate(text: str, limit: int) -> str:
    """截断过长输出，保留「已截断」提示而不是让内存被日志撑爆。"""

    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}\n…（输出已截断，共 {len(text)} 字符）"
