"""IFP 命令行入口（Typer + Rich）。

命令一览：
    ifp check    <paths...>   类型检查 + Lint
    ifp verify   <model>      属性测试（SMT 反例搜索）+ 一致性检查
    ifp simulate <paths...>   运行 Scenario 仿真
    ifp run      <paths...>   在隔离沙箱中按并发调度校验任务（check / verify / simulate）
    ifp diff     <old> <new>  语义 Diff + 风险评分 + 审批判定（文件或目录，支持 CI 门禁）
    ifp baseline <model>      列出基线不变量
    ifp rbac                  列出角色与权限矩阵
    ifp lsp                   语言服务器（LSP over stdio，供编辑器诊断 / 跳转 / 快速修复）
    ifp web                   启动 Web 编辑器（浏览器内在线编辑与可视化）
    ifp repo init/log         模型仓库（Git 兼容）：初始化 / 提交 / 历史
    ifp merge   <base> <ours> <theirs>  基于业务语义的三方合并
    ifp metrics <paths...>    模型度量指标（规则覆盖率 / 死不变量 / 基线数量）
    ifp align   <repo>        模型与代码版本对齐审计
    ifp update  [--check]     检查来源仓库的版本发行并按需更新（显示发行时间与资产大小）
    ifp doctor  [--online]    环境自检：依赖、内置资源与工具链
    ifp config  get/set/list  查看与修改本地偏好（更新源、自动检查）
    ifp export   <model>      导出制品（json / openapi / markdown / protobuf）
    ifp init     <dir>        生成新模型脚手架

退出码约定：
    0  成功（检查 / 验证 / 仿真 / 调度未发现失败）
    1  检查失败（存在 error、反例、仿真未通过、调度任务失败）
    2  用法或输入错误（未知命令参数、路径不存在、非法取值）
    3  审批拒绝（语义门禁未通过强制审批）
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence

import typer
from rich.console import Console
from rich.panel import Panel

from . import __version__, parser, plugin, prefs, rbac, sandbox
from .audit import AuditLog
from .baseline import APPROVAL_THRESHOLD, approval_required, baseline_invariants, risk_score
from .config import find_config, load_plan
from .diff import DiffResult, diff_paths
from .errors import Diagnostic, IFPConfigError, IFPParseError, Severity
from .exporter import to_json, to_markdown, to_openapi, to_protobuf
from .lint import lint as run_lint
from .lsp import serve
from .metrics import collect as collect_metrics
from .repo import ModelRepo, alignment_report
from .scheduler import STATUS_SKIPPED, Scheduler, Task, summarize
from .semantic_merge import merge_paths
from .semantic_merge import record as record_merge
from .simulate import simulate
from .typecheck import typecheck

if TYPE_CHECKING:  # 仅用于标注，运行时按需导入以保持 CLI 启动轻量
    from .updater import ReleaseInfo

app = typer.Typer(add_completion=False, help="IFP 意图形式化平台命令行工具")
console = Console()

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_DENIED = 3

_SEVERITY_STYLE = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
}


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _collect_paths(paths: Sequence[Path], *, keep_dirs: bool = False) -> list[Path]:
    """展开输入为具体的 .ifp 文件列表。

    - 支持 glob 模式（如 ``src/**/*.ifp``）；
    - 目录默认递归收集 ``*.ifp``，``keep_dirs=True`` 时目录作为一个整体保留；
    - 路径不存在时报可读错误并以退出码 2 结束；
    - 按绝对路径去重，避免同一文件被重复解析。
    """

    files: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        text = str(raw)
        if glob.has_magic(text):
            candidates = sorted(Path(match) for match in glob.glob(text, recursive=True))
            if not candidates:
                console.print(f"glob 未匹配到任何路径：{text}", style="bold red", markup=False)
                raise typer.Exit(code=EXIT_USAGE)
        elif not raw.exists():
            console.print(f"路径不存在：{raw}", style="bold red", markup=False)
            raise typer.Exit(code=EXIT_USAGE)
        else:
            candidates = [raw]

        for candidate in candidates:
            if candidate.is_dir():
                expanded = [candidate] if keep_dirs else sorted(candidate.rglob("*.ifp"))
            else:
                expanded = [candidate]
            for item in expanded:
                key = str(item.resolve())
                if key in seen:
                    continue
                seen.add(key)
                files.append(item)
    return files


def _parse_stdin() -> object:
    """从标准输入读取并解析模型；失败时渲染可读错误。"""

    try:
        return parser.parse(sys.stdin.read(), filename="<stdin>")
    except IFPParseError as exc:
        _print_parse_error(exc)
        raise typer.Exit(code=EXIT_FAILED) from exc


def _load_single(path: Path) -> object:
    """加载单个模型文件；``-`` 表示从 stdin 读取。失败时渲染可读错误并以退出码 1 结束。"""

    if str(path) == "-":
        return _parse_stdin()
    try:
        return parser.load(path)
    except IFPParseError as exc:
        _print_parse_error(exc)
        raise typer.Exit(code=EXIT_FAILED) from exc


def _load_inputs(paths: Sequence[Path], *, keep_dirs: bool = False) -> list[tuple[str, object]]:
    """加载模型，返回 ``[(显示名, model)]``；参数 ``-`` 表示从 stdin 读取。"""

    results: list[tuple[str, object]] = []
    file_args: list[Path] = []
    stdin_consumed = False
    for raw in paths:
        if str(raw) == "-":
            if stdin_consumed:
                continue
            stdin_consumed = True
            results.append(("<stdin>", _parse_stdin()))
        else:
            file_args.append(raw)

    for path in _collect_paths(file_args, keep_dirs=keep_dirs):
        try:
            results.append((str(path), parser.load(path)))
        except IFPParseError as exc:
            _print_parse_error(exc)
            raise typer.Exit(code=EXIT_FAILED) from exc
    return results


def _load_project(
    paths: Sequence[Path], *, keep_dirs: bool = False
) -> tuple[list[tuple[str, object]], object]:
    """加载全部文件，并返回合并后的项目模型（用于跨文件引用解析）。"""

    models = _load_inputs(paths, keep_dirs=keep_dirs)
    if not models:
        console.print("未找到任何 .ifp 模型文件。", style="yellow", markup=False)
        raise typer.Exit(code=EXIT_FAILED)
    project = parser.merge(
        [model for _, model in models],
        source=", ".join(name for name, _ in models),
    )
    return models, project


_source_cache: dict[str, list[str]] = {}


def _read_source_lines(file: str) -> list[str]:
    cached = _source_cache.get(file)
    if cached is None:
        try:
            cached = Path(file).read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            cached = []
        _source_cache[file] = cached
    return cached


def _source_snippet(file: str, line: int, column: int = 0) -> str:
    """渲染出错行与插入符，帮助用户快速定位。"""

    if not file or line <= 0:
        return ""
    lines = _read_source_lines(file)
    if not (1 <= line <= len(lines)):
        return ""
    gutter = f"{line:>4}"
    rows = [f"{gutter} | {lines[line - 1].rstrip()}"]
    if column:
        rows.append(f"{' ' * len(gutter)} | {' ' * (column - 1)}^")
    return "\n".join(rows)


def _print_parse_error(exc: IFPParseError) -> None:
    console.print(str(exc), style="bold red", markup=False)
    snippet = _source_snippet(exc.filename, exc.line, exc.column)
    if snippet:
        console.print(snippet, style="dim", markup=False)


def _print_diagnostics(diags: list[Diagnostic]) -> None:
    if not diags:
        return
    for diag in sorted(diags, key=lambda d: (d.file, d.line, d.column)):
        console.print(diag.format(), style=_SEVERITY_STYLE.get(diag.severity, "white"), markup=False)
        snippet = _source_snippet(diag.file, diag.line, diag.column)
        if snippet:
            console.print(snippet, style="dim", markup=False)


def _fail_if_errors(diags: list[Diagnostic], strict: bool = False) -> None:
    blocking = [d for d in diags if d.severity is Severity.ERROR]
    if strict:
        blocking += [d for d in diags if d.severity is Severity.WARNING]
    if blocking:
        raise typer.Exit(code=EXIT_FAILED)


# ---------------------------------------------------------------------------
# 沙箱与任务调度
# ---------------------------------------------------------------------------

def _sandbox_limits(
    timeout: Optional[float] = None,
    cpu: Optional[int] = None,
    memory: Optional[int] = None,
) -> sandbox.Limits:
    """按「命令行参数 > 本地偏好 > 内置默认」的顺序组装沙箱上限。"""

    return sandbox.Limits(
        timeout_s=float(timeout if timeout is not None else prefs.get("sandbox.timeout")),
        cpu_seconds=int(cpu if cpu is not None else prefs.get("sandbox.cpu")),
        memory_mb=int(memory if memory is not None else prefs.get("sandbox.memory")),
    )


def _sandbox_jobs(jobs: Optional[int]) -> int:
    """解析并发度；未指定时取偏好 ``sandbox.jobs``。"""

    if jobs is None:
        return max(1, int(prefs.get("sandbox.jobs")))
    if jobs < 1:
        console.print(f"并发度必须 >= 1（收到 {jobs}）。", style="bold red", markup=False)
        raise typer.Exit(code=EXIT_USAGE)
    return jobs


def _dispatch_to_sandbox(argv: Sequence[str]) -> None:
    """把当前子命令放进沙箱重跑，并透传子进程输出与退出码。"""

    limits = _sandbox_limits()
    console.print(
        f"在沙箱中执行：ifp {' '.join(argv)}（{limits.describe()}）", style="cyan", markup=False
    )
    result = sandbox.run(argv, limits=limits)
    for line in result.stdout.splitlines():
        console.print(line, markup=False)
    for line in result.stderr.splitlines():
        console.print(line, style="red", markup=False)
    if result.detail:
        console.print(result.detail, style="yellow", markup=False)
    if result.status != sandbox.STATUS_OK:
        raise typer.Exit(code=result.exit_code if result.exit_code > 0 else EXIT_FAILED)


def _build_tasks(job: str, paths: list[Path]) -> list[Task]:
    """按任务类型把输入拆成调度任务。

    路径统一转成绝对路径：沙箱子进程在独立临时目录中运行，相对路径在那里无法解析。
    粒度规则：``check`` 按输入的每个路径（目录作为一个整体检查，保证跨文件引用可解析）；
    ``verify`` 按「文件 × Intent」；``simulate`` 按「场景」。
    """

    kind = job.strip().lower()

    if kind == "check":
        return [
            Task(
                name=str(path),
                argv=("check", str(path.resolve())),
                kind="check",
                metadata={"path": str(path)},
            )
            for path in _collect_paths(paths, keep_dirs=True)
        ]

    if kind == "verify":
        tasks = []
        for name, model in _load_inputs(paths):
            for context in model.contexts:
                for intent in context.intents:
                    tasks.append(
                        Task(
                            name=f"{name}#{intent.name}",
                            argv=("verify", str(Path(name).resolve()), "--intent", intent.name),
                            kind="verify",
                            metadata={"path": name, "intent": intent.name},
                        )
                    )
        return tasks

    if kind == "simulate":
        models = _load_inputs(paths)
        if not models:
            return []
        files = [str(Path(name).resolve()) for name, _ in models]
        project = parser.merge(
            [model for _, model in models],
            source=", ".join(name for name, _ in models),
        )
        return [
            Task(
                name=scenario.name,
                argv=("simulate", *files, "--name", scenario.name),
                kind="simulate",
                metadata={"scenario": scenario.name},
            )
            for scenario in project.scenarios
        ]

    console.print(
        f"未知任务类型：{job}（可选 check / verify / simulate）", style="bold red", markup=False
    )
    raise typer.Exit(code=EXIT_USAGE)


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

_UPDATE_CHECK_TTL = 24 * 60 * 60  # 自动检查的最短间隔：一天


@app.command()
def version(
    check: bool = typer.Option(False, "--check", help="同时检查来源仓库是否有新版本"),
) -> None:
    """打印版本号；加 --check 或开启自动检查时可提示新版本。"""

    console.print(f"ifp-platform {__version__}")
    if check or prefs.get("update.auto_check"):
        _print_update_hint(force=check)


def _print_update_hint(*, force: bool) -> None:
    """在版本号后提示新版本；失败或未到检查间隔时静默返回。"""

    if os.environ.get("IFP_NO_UPDATE_CHECK"):
        return
    from . import updater

    if not force:
        last = float(prefs.get("update.last_check") or 0.0)
        if time.time() - last < _UPDATE_CHECK_TTL:
            return

    source = prefs.get("update.source") or updater.DEFAULT_SOURCE
    try:
        release = updater.fetch_latest(source, timeout=3.0)
    except updater.UpdateError:
        return  # 自动检查静默失败，不干扰正常使用
    prefs.set_value("update.last_check", time.time())
    if updater.is_newer(release.version, __version__):
        console.print(
            f"发现新版本 {release.tag}（发行于 {updater.format_time(release.published_at)}），"
            "执行 ifp update 可更新。",
            style="yellow",
            markup=False,
        )


def _update_payload(source: str, release: ReleaseInfo, newer: bool) -> dict[str, object]:
    """构造 `ifp update --json` 的输出。"""

    from . import updater

    assets = [
        {"name": asset.name, "size": asset.size, "size_human": asset.human_size()}
        for asset in release.assets
    ]
    return {
        "source": source,
        "current": __version__,
        "latest": {
            "tag": release.tag,
            "name": release.name,
            "published_at": release.published_at,
            "published_at_local": updater.format_time(release.published_at),
            "html_url": release.html_url,
            "assets": assets,
        },
        "update_available": newer,
    }


@app.command()
def update(
    check: bool = typer.Option(False, "--check", help="只检查是否有新版本，不执行更新"),
    yes: bool = typer.Option(False, "--yes", "-y", help="非交互模式，自动确认更新"),
    prerelease: bool = typer.Option(False, "--pre", help="把预发行版本也纳入检查"),
    source: Optional[str] = typer.Option(None, "--source", help="更新来源仓库，owner/repo 形式"),
    timeout: float = typer.Option(6.0, "--timeout", help="访问更新源超时（秒）"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出检查结果"),
    auto: Optional[bool] = typer.Option(
        None, "--auto/--no-auto", help="开启或关闭版本号的启动自动检查"
    ),
) -> None:
    """检查来源仓库的版本发行，并由你决定是否更新。

    展示最新版本的发行时间、发行资产大小与发行页；确认后才下载安装。
    可用 `--auto` / `--no-auto` 持久化「是否自动检查新版本」的偏好。
    """

    if auto is not None:
        prefs.set_value("update.auto_check", auto)
        state = "开启" if auto else "关闭"
        console.print(f"已{state}版本号自动检查（ifp version 时提示新版本）。", markup=False)
        return

    from . import updater

    resolved = (
        source
        or prefs.get("update.source")
        or os.environ.get("IFP_UPDATE_SOURCE")
        or updater.DEFAULT_SOURCE
    )
    try:
        release = updater.fetch_latest(resolved, include_prerelease=prerelease, timeout=timeout)
    except updater.UpdateError as exc:
        console.print(f"更新检查失败：{exc}", style="bold red", markup=False)
        raise typer.Exit(code=EXIT_FAILED) from exc

    prefs.set_value("update.last_check", time.time())
    if source:
        prefs.set_value("update.source", resolved)

    newer = updater.is_newer(release.version, __version__)

    if as_json:
        console.print(
            json.dumps(_update_payload(resolved, release, newer), ensure_ascii=False, indent=2),
            markup=False,
            soft_wrap=True,
        )
        return

    console.print(f"当前版本：{__version__}", markup=False)
    console.print(f"来源仓库：{resolved}", markup=False)
    for line in release.lines():
        console.print(line, markup=False)

    if not newer:
        console.print("已是最新版本。", style="green", markup=False)
        return

    console.print(f"发现新版本 {release.tag}。", style="yellow", markup=False)
    if check:
        console.print("（仅检查模式，未执行更新；执行 ifp update 或加 --yes 即可更新）", markup=False)
        return

    if not yes and not typer.confirm(f"是否更新到 {release.tag}？"):
        console.print("已跳过更新。", style="yellow", markup=False)
        return

    console.print(f"正在下载并安装 {release.tag} …", markup=False)
    try:
        target = updater.install_release(release, source=resolved, timeout=max(timeout, 30.0))
    except updater.UpdateError as exc:
        console.print(f"更新失败：{exc}", style="bold red", markup=False)
        raise typer.Exit(code=EXIT_FAILED) from exc
    console.print(f"已更新 {target}；重新运行 ifp version 确认版本。", style="green", markup=False)


@app.command()
def doctor(
    online: bool = typer.Option(False, "--online", help="同时检查更新源是否可达"),
    source: Optional[str] = typer.Option(None, "--source", help="指定更新来源仓库，owner/repo 形式"),
) -> None:
    """检查运行环境、依赖、内置资源与工具链，帮助快速上手与排障。"""

    from . import doctor as doctor_mod

    checks = doctor_mod.run_checks(online=online, source=source)
    failed = 0
    for item in checks:
        mark = "OK" if item.ok else "FAIL"
        console.print(
            f"[{mark}] {item.name}：{item.detail}",
            style="green" if item.ok else "bold red",
            markup=False,
        )
        if not item.ok:
            failed += 1
            if item.hint:
                console.print(f"        提示：{item.hint}", style="yellow", markup=False)

    if failed:
        console.print(f"自检发现 {failed} 项问题。", style="bold red", markup=False)
        raise typer.Exit(code=EXIT_FAILED)
    console.print("环境自检通过，开始建模：ifp init <dir>", style="green", markup=False)


config_app = typer.Typer(add_completion=False, help="查看与修改本地偏好（更新源、自动检查等）")
app.add_typer(config_app, name="config")


@config_app.command("list")
def config_list() -> None:
    """列出当前偏好（含内置默认值）。"""

    data = prefs.load() or prefs.DEFAULTS
    console.print(json.dumps(data, ensure_ascii=False, indent=2), markup=False, soft_wrap=True)


@config_app.command("get")
def config_get(key: str = typer.Argument(..., help="点分路径，例如 update.source")) -> None:
    """读取一项偏好。"""

    value = prefs.get(key)
    if value is None:
        console.print(f"未设置：{key}", style="yellow", markup=False)
        raise typer.Exit(code=EXIT_FAILED)
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    console.print(rendered, markup=False)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="点分路径，例如 update.auto_check"),
    value: str = typer.Argument(..., help="取值，例如 true / false / 1 / owner/repo"),
) -> None:
    """写入一项偏好，例如 `ifp config set update.auto_check true`。"""

    written = prefs.set_value(key, prefs.coerce(value))
    console.print(f"{key} = {json.dumps(written, ensure_ascii=False)}", markup=False)


@config_app.command("path")
def config_path() -> None:
    """打印偏好文件路径。"""

    console.print(str(prefs.path()), markup=False)


@app.command()
def check(
    paths: list[Path] = typer.Argument(..., help="模型文件或目录"),
    strict: bool = typer.Option(False, "--strict", help="把 warning 也视为失败"),
) -> None:
    """对模型执行类型检查与 Lint。"""

    diags: list[Diagnostic] = []
    models, project = _load_project(paths)
    for _, model in models:
        diags.extend(typecheck(model, project=project))
        diags.extend(run_lint(model, extra_rules=plugin.lint_rules()))
    _print_diagnostics(diags)
    errors = sum(1 for d in diags if d.severity is Severity.ERROR)
    warnings = sum(1 for d in diags if d.severity is Severity.WARNING)
    console.print(f"检查完成：{errors} error / {warnings} warning", markup=False)
    if not errors and not warnings:
        infos = sum(1 for d in diags if d.severity is Severity.INFO)
        if infos:
            console.print(f"检查通过：无 error / warning（{infos} 条提示）。", style="green", markup=False)
        else:
            console.print("检查通过，未发现问题。", style="green", markup=False)
    _fail_if_errors(diags, strict=strict)


@app.command("verify")
def verify_cmd(
    model_path: Path = typer.Argument(..., help="模型文件"),
    intent: Optional[str] = typer.Option(None, "--intent", help="只验证指定 Intent"),
    config_path: Optional[Path] = typer.Option(None, "--config", help="property_test.yaml 路径"),
    lower: Optional[int] = typer.Option(None, "--lower", help="数值下界"),
    upper: Optional[int] = typer.Option(None, "--upper", help="数值上界"),
    timeout_ms: Optional[int] = typer.Option(None, "--timeout", help="求解超时（毫秒）"),
    steps: Optional[int] = typer.Option(
        None, "--steps", help="多步路径搜索的最大动作步数（覆盖 mode 档位）"
    ),
    mode: Optional[str] = typer.Option(
        None, "--mode", help="步数档位：single(1 步) / light(2 步) / full(3 步)"
    ),
    audit_path: Optional[Path] = typer.Option(None, "--audit", help="写入审计日志（JSONL）"),
    sandboxed: bool = typer.Option(
        False, "--sandbox", help="在隔离沙箱子进程中执行（墙钟与资源上限取 sandbox.* 偏好）"
    ),
) -> None:
    """属性测试：自动搜索违反不变量的反例，并检查规则一致性。

    加 ``--sandbox`` 可把本次求解放进隔离沙箱：子进程有墙钟超时、CPU 与内存上限，
    并在独立临时目录中运行；上限可用 ``ifp config set sandbox.timeout 300`` 等调整。
    """

    if sandboxed:
        argv = ["verify", str(model_path.resolve())]
        for flag, value in (
            ("--intent", intent),
            ("--lower", lower),
            ("--upper", upper),
            ("--timeout", timeout_ms),
            ("--steps", steps),
            ("--mode", mode),
        ):
            if value is not None:
                argv += [flag, str(value)]
        if config_path is not None:
            argv += ["--config", str(config_path.resolve())]
        if audit_path is not None:
            argv += ["--audit", str(audit_path.resolve())]
        _dispatch_to_sandbox(argv)
        return

    # 按需导入：z3 / SMT 编码只在 verify 命令用到，避免拖慢其他命令的启动
    from .smt import VerifyConfig, check_consistency, verify

    model = _load_single(model_path)

    cfg_file = config_path or (find_config(model_path) if str(model_path) != "-" else None)
    plan_intent = None
    cfg = VerifyConfig()
    if cfg_file is not None:
        try:
            plan = load_plan(cfg_file)
        except IFPConfigError as exc:
            console.print(str(exc), style="bold red", markup=False)
            raise typer.Exit(code=EXIT_FAILED) from exc
        cfg = plan.config
        plan_intent = plan.intent
    if lower is not None:
        cfg.lower = lower
    if upper is not None:
        cfg.upper = upper
    if timeout_ms is not None:
        cfg.timeout_ms = timeout_ms
    if mode is not None:
        cfg.mode = mode
    if steps is not None:
        cfg.max_steps = steps
    target_intent = intent or plan_intent

    report = verify(model, target_intent, cfg)
    diags = list(report.diagnostics)
    diags.extend(check_consistency(model, target_intent, cfg))
    _print_diagnostics(diags)

    console.print(
        f"属性测试：检查 {report.actions_checked} 个动作 × {report.invariants_checked} 条不变量"
        f"（最多 {cfg.step_limit()} 步路径）",
        markup=False,
    )
    for ce in report.counterexamples:
        style = "bold red" if ce.baseline else "red"
        title = f"反例 {ce.action}" + (f"（{ce.steps} 步）" if ce.steps > 1 else "")
        console.print(Panel(ce.describe(), title=title, border_style=style, expand=False), markup=False)

    if audit_path is not None:
        log = AuditLog(audit_path)
        log.append(
            "verify",
            model=str(model_path),
            intent=target_intent or "*",
            counterexamples=len(report.counterexamples),
            baseline_counterexamples=sum(1 for c in report.counterexamples if c.baseline),
        )
        console.print(f"已写入审计日志：{audit_path}", style="green", markup=False)

    if report.counterexamples or any(d.severity is Severity.ERROR for d in diags):
        raise typer.Exit(code=EXIT_FAILED)
    console.print("未发现违反不变量的反例。", style="green", markup=False)


@app.command("simulate")
def simulate_cmd(
    paths: list[Path] = typer.Argument(..., help="包含 scenario 的模型文件或目录"),
    name: Optional[str] = typer.Option(None, "--name", help="只运行指定场景"),
    sandboxed: bool = typer.Option(
        False, "--sandbox", help="在隔离沙箱子进程中执行（墙钟与资源上限取 sandbox.* 偏好）"
    ),
) -> None:
    """运行 Scenario 仿真，验证行为符合业务预期。

    加 ``--sandbox`` 可把本次仿真放进隔离沙箱；上限可用
    ``ifp config set sandbox.timeout 300`` 等调整。
    """

    if sandboxed:
        argv = ["simulate", *(str(path.resolve()) for path in paths)]
        if name is not None:
            argv += ["--name", name]
        _dispatch_to_sandbox(argv)
        return

    results = []
    _, project = _load_project(paths)
    results.extend(simulate(project, name))
    if not results:
        console.print("未找到任何场景。", style="yellow", markup=False)
        raise typer.Exit(code=EXIT_FAILED)
    failed = 0
    for result in results:
        style = "green" if result.passed else "bold red"
        console.print(result.summary(), style=style, markup=False)
        failed += 0 if result.passed else 1
    console.print(f"仿真完成：{len(results) - failed} passed / {failed} failed", markup=False)
    if failed:
        raise typer.Exit(code=EXIT_FAILED)


@app.command("run")
def run_cmd(
    paths: list[Path] = typer.Argument(..., help="模型文件或目录"),
    job: str = typer.Option("verify", "--job", help="任务类型：check / verify / simulate"),
    jobs: Optional[int] = typer.Option(None, "--jobs", "-j", help="并发度（同时运行的沙箱任务数）"),
    timeout: Optional[float] = typer.Option(None, "--timeout", help="单个任务的墙钟超时（秒）"),
    cpu: Optional[int] = typer.Option(None, "--cpu", help="单个任务的 CPU 秒数上限"),
    memory: Optional[int] = typer.Option(None, "--memory", help="单个任务的内存上限（MB）"),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="首个任务失败后不再调度剩余任务"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出调度结果"),
) -> None:
    """在隔离沙箱中按并发调度校验任务，逐个报告状态与耗时。

    粒度：``check`` 按输入的每个路径（目录作为一个整体，保证跨文件引用可解析）；
    ``verify`` 按「文件 × Intent」；``simulate`` 按「场景」。
    并发度与资源上限默认取偏好（``ifp config set sandbox.jobs 8`` 等），可用参数临时覆盖。
    """

    if any(str(path) == "-" for path in paths):
        console.print(
            "ifp run 需要真实文件路径（沙箱子进程无法读取 stdin），请改用 ifp check / simulate。",
            style="bold red",
            markup=False,
        )
        raise typer.Exit(code=EXIT_USAGE)

    tasks = _build_tasks(job, paths)

    if not tasks:
        console.print(f"没有可调度的 {job} 任务。", style="yellow", markup=False)
        raise typer.Exit(code=EXIT_FAILED)

    limits = _sandbox_limits(timeout=timeout, cpu=cpu, memory=memory)
    resolved_jobs = _sandbox_jobs(jobs)
    scheduler = Scheduler(jobs=resolved_jobs, limits=limits, fail_fast=fail_fast)
    results = scheduler.run(tasks)
    summary = summarize(results, scheduler.duration_s)

    if as_json:
        # 直接写 stdout，保证输出是纯 JSON，不被终端装饰或换行干扰。
        payload = {
            "job": job,
            "jobs": resolved_jobs,
            "fail_fast": fail_fast,
            "limits": {
                "timeout_s": limits.timeout_s,
                "cpu_seconds": limits.cpu_seconds,
                "memory_mb": limits.memory_mb,
                "file_size_mb": limits.file_size_mb,
                "open_files": limits.open_files,
            },
            "tasks": [result.to_json() for result in results],
            "summary": summary.to_json(),
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        for result in results:
            if result.ok:
                style = "green"
            else:
                style = "yellow" if result.status == STATUS_SKIPPED else "bold red"
            console.print(result.line(), style=style, markup=False)
            if not result.ok:
                for line in result.tail():
                    console.print(f"    {line}", style="red", markup=False)
                if result.detail:
                    console.print(f"    {result.detail}", style="yellow", markup=False)
        console.print(summary.line(), style="green" if summary.ok else "bold red", markup=False)

    if not summary.ok:
        raise typer.Exit(code=EXIT_FAILED)


@app.command()
def diff(
    old_path: Path = typer.Argument(..., help="旧模型（文件或目录）"),
    new_path: Path = typer.Argument(..., help="新模型（文件或目录）"),
    threshold: int = typer.Option(
        APPROVAL_THRESHOLD, "--threshold", help="强制审批的风险分值阈值"
    ),
    json_report: Optional[Path] = typer.Option(None, "--json", help="写出机器可读报告（JSON）"),
    github: bool = typer.Option(
        False, "--github", help="输出 GitHub Actions 注解并写入作业输出"
    ),
    fail_on_approval: bool = typer.Option(
        False, "--fail-on-approval", help="需强制审批时以非零码退出（CI 门禁用）"
    ),
    audit_path: Optional[Path] = typer.Option(None, "--audit", help="写入审计日志（JSONL）"),
    actor: Optional[str] = typer.Option(
        None, "--actor", help="执行合并的主体名称；指定后启用 RBAC 权限判定"
    ),
    actor_roles: str = typer.Option(
        "", "--actor-roles", help="主体角色，逗号分隔：modeler, reviewer, approver, auditor"
    ),
) -> None:
    """比较两个模型的业务语义差异，输出风险分值与审批判定（支持目录与 CI 门禁）。

    退出码：``0`` 通过 / ``1`` 需强制审批（仅 ``--fail-on-approval``）/ ``2`` 路径或用法错误 /
    ``3`` 主体权限不足（仅指定 ``--actor`` 时）。
    """

    for path in (old_path, new_path):
        if not path.exists():
            console.print(f"路径不存在：{path}", style="bold red", markup=False)
            raise typer.Exit(code=EXIT_USAGE)

    result = diff_paths(old_path, new_path)
    score = risk_score(result)
    needs_approval = approval_required(result, threshold)

    if not result.changes:
        console.print("两个模型语义一致，无差异。", style="green", markup=False)
    else:
        for change in result.changes:
            style = "bold red" if change.baseline else "white"
            console.print(change.format(), style=style, markup=False)
        console.print(f"风险分值：{score}/100（阈值 {threshold}）", markup=False)
        console.print(
            f"需强制审批：{'是' if needs_approval else '否'}",
            style="bold red" if needs_approval else "green",
            markup=False,
        )

    if json_report is not None:
        report = _gate_report(old_path, new_path, result, score, threshold, needs_approval)
        json_report.parent.mkdir(parents=True, exist_ok=True)
        json_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        console.print(f"已写出门禁报告：{json_report}", style="green", markup=False)

    if github:
        _emit_github(result, score, threshold, needs_approval)

    decision: Optional[rbac.AccessDecision] = None
    if actor is not None:
        try:
            principal = rbac.Principal.parse(actor, actor_roles)
        except ValueError as exc:
            console.print(str(exc), style="bold red", markup=False)
            raise typer.Exit(code=EXIT_USAGE) from exc
        decision = rbac.authorize_merge(principal, result, threshold)
        console.print(
            decision.format(),
            style="green" if decision.allowed else "bold red",
            markup=False,
        )

    if audit_path is not None:
        log = AuditLog(audit_path)
        log.append(
            "diff",
            old=str(old_path),
            new=str(new_path),
            changes=len(result.changes),
            baseline_changes=sum(1 for c in result.changes if c.baseline),
            risk_score=score,
            threshold=threshold,
            approval_required=needs_approval,
        )
        if decision is not None:
            rbac.record(
                log,
                decision,
                old=str(old_path),
                new=str(new_path),
                risk_score=score,
                threshold=threshold,
                baseline_change=result.has_baseline_change,
            )
        console.print(f"已写入审计日志：{audit_path}", style="green", markup=False)

    if decision is not None and not decision.allowed:
        raise typer.Exit(code=EXIT_DENIED)

    if fail_on_approval and needs_approval:
        raise typer.Exit(code=EXIT_FAILED)


def _gate_report(
    old_path: Path,
    new_path: Path,
    result: DiffResult,
    score: int,
    threshold: int,
    needs_approval: bool,
) -> dict[str, object]:
    """构造 CI 语义门禁的机器可读报告。"""

    return {
        "old": str(old_path),
        "new": str(new_path),
        "risk_score": score,
        "threshold": threshold,
        "approval_required": needs_approval,
        "baseline_change": result.has_baseline_change,
        "counts": result.counts,
        "changes": [
            {
                "kind": c.kind,
                "file": c.file,
                "path": c.path,
                "detail": c.detail,
                "baseline": c.baseline,
            }
            for c in result.changes
        ],
    }


def _emit_github(
    result: DiffResult, score: int, threshold: int, needs_approval: bool
) -> None:
    """把门禁判定输出为 GitHub Actions 注解与作业输出。"""

    # 注解必须单行，故绕过 rich 的自动换行。
    for change in result.changes:
        if change.baseline:
            typer.echo(f"::warning::{change.format()}")
    level = "warning" if needs_approval else "notice"
    typer.echo(
        f"::{level}::语义门禁：风险分值 {score}/100（阈值 {threshold}），"
        f"需强制审批：{'是' if needs_approval else '否'}"
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as fh:
            fh.write(f"risk_score={score}\n")
            fh.write(f"approval_required={'true' if needs_approval else 'false'}\n")


@app.command("rbac")
def rbac_command() -> None:
    """列出平台角色与权限矩阵。"""

    console.print("角色与权限矩阵：", markup=False)
    for role in rbac.Role:
        perms = sorted(rbac.ROLE_PERMISSIONS[role], key=lambda item: item.value)
        detail = "，".join(f"{p.value}（{rbac.PERMISSION_LABELS[p]}）" for p in perms)
        console.print(f"- {role.value}（{rbac.ROLE_LABELS[role]}）：{detail}", markup=False)
    console.print(
        f"共 {len(rbac.Role)} 类角色；合并涉及基线变更时需 "
        f"{rbac.Permission.APPROVE_BASELINE.value} 权限。",
        markup=False,
    )


@app.command()
def baseline(
    model_path: Path = typer.Argument(..., help="模型文件"),
) -> None:
    """列出模型中的基线不变量。"""

    model = _load_single(model_path)
    refs = baseline_invariants(model)
    if not refs:
        console.print("模型中没有基线不变量。", style="yellow", markup=False)
        return
    for ref in refs:
        console.print(f"- {ref.format()}", markup=False)
    console.print(f"共 {len(refs)} 条基线不变量", markup=False)


@app.command()
def export(
    model_path: Path = typer.Argument(..., help="模型文件"),
    fmt: str = typer.Option("json", "--format", help="json | openapi | markdown | protobuf"),
    context: Optional[str] = typer.Option(None, "--context", help="只导出指定上下文"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="输出文件（默认打印到 stdout）"),
) -> None:
    """把模型导出为制品。"""

    model = _load_single(model_path)

    if fmt in plugin.exporters():
        text = plugin.exporters()[fmt](model, context)
    elif fmt == "json":
        text = to_json(model)
    elif fmt == "openapi":
        text = to_openapi(model, context)
    elif fmt == "markdown":
        text = to_markdown(model)
    elif fmt == "protobuf":
        text = to_protobuf(model, context)
    else:
        console.print(f"未知导出格式：{fmt}", style="bold red", markup=False)
        raise typer.Exit(code=EXIT_USAGE)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        console.print(f"已导出到 {output}", style="green", markup=False)
    else:
        console.print(text, markup=False)


_TEMPLATE = """// IFP 模型模板：context / value / intent
context ExampleContext {
    description: "示例限界上下文"

    intent ExampleAggregate {
        description: "示例聚合根"
        field amount: Decimal = 0
        field status: ExampleStatus = ExampleStatus::Created

        state ExampleStatus {
            Created,
            Done
        }

        initial: ExampleStatus::Created

        invariant amountNonNegative {
            expr: amount >= 0
            note: "金额不得为负"
            owner: "your-name"
        }

        action Finish {
            transition: ExampleStatus::Done
            pre: status == ExampleStatus::Created
            post: status == ExampleStatus::Done
        }
    }
}
"""

_CONFIG_TEMPLATE = """# IFP 属性测试配置
property_test:
  intent: ExampleAggregate
  lower: 0
  upper: 100000
  timeout_ms: 5000
  mode: light
  max_steps: 2
  invariants: []
"""


@app.command()
def init(directory: Path = typer.Argument(Path("."), help="目标目录")) -> None:
    """生成新模型脚手架（model.ifp + property_test.yaml）。"""

    directory.mkdir(parents=True, exist_ok=True)
    model_file = directory / "model.ifp"
    config_file = directory / "property_test.yaml"
    if model_file.exists() or config_file.exists():
        console.print("目标目录已存在模型文件，已取消。", style="bold red", markup=False)
        raise typer.Exit(code=EXIT_FAILED)
    model_file.write_text(_TEMPLATE, encoding="utf-8")
    config_file.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
    console.print(f"已生成 {model_file} 与 {config_file}", style="green", markup=False)


@app.command()
def lsp(
    stdio: bool = typer.Option(
        True, "--stdio", help="通过标准输入输出通信（当前唯一支持的传输方式）"
    ),
) -> None:
    """启动 IFP 语言服务器，供编辑器诊断 / 跳转 / 快速修复。"""

    # stdio 是唯一传输方式；保留该开关以兼容编辑器默认的 `ifp lsp --stdio` 调用。
    raise typer.Exit(code=serve(sys.stdin.buffer, sys.stdout.buffer))


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(8765, "--port", help="监听端口"),
) -> None:
    """启动 Web 编辑器：浏览器内在线编辑 `.ifp`、实时诊断与模型可视化。"""

    # 按需导入：http.server / SMT 相关依赖只在 web 命令用到
    from .web import serve as serve_web

    server = serve_web(host, port)
    console.print(f"IFP Web 编辑器已启动：http://{host}:{port}/", style="green", markup=False)
    console.print("按 Ctrl+C 停止。", markup=False)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# ---------------------------------------------------------------------------
# 模型仓库 / 语义合并 / 度量 / 对齐审计
# ---------------------------------------------------------------------------

repo_app = typer.Typer(add_completion=False, help="Git 兼容的模型仓库")
app.add_typer(repo_app, name="repo")

_ALIGNMENT_STYLE = {"aligned": "green", "drift": "yellow", "unknown": "red"}


@repo_app.command("init")
def repo_init(
    directory: Path = typer.Argument(..., help="模型仓库目录"),
) -> None:
    """初始化（或复用）一个模型仓库。"""

    repo = ModelRepo.init(directory)
    console.print(f"模型仓库已就绪：{repo.root}", style="green", markup=False)


@repo_app.command("commit")
def repo_commit(
    directory: Path = typer.Argument(..., help="模型仓库目录"),
    paths: list[Path] = typer.Argument(..., help="要提交的 .ifp 文件或目录"),
    message: str = typer.Option(..., "--message", "-m", help="提交说明"),
    business_change: str = typer.Option("", "--business-change", help="关联的业务变更说明"),
    code_revision: str = typer.Option("", "--code-revision", help="关联的代码版本（commit / tag）"),
    author: str = typer.Option(
        "ifp-bot", "--author", help="提交人，形如 '张三 <zhangsan@example.com>'"
    ),
) -> None:
    """把模型源码提交进仓库；业务变更说明与代码版本写入提交信息（trailer）。"""

    repo = ModelRepo(directory)
    root = repo.root.resolve()
    files: dict[str, str] = {}
    for path in _collect_paths(paths):
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            console.print(f"路径不在仓库内：{path}", style="bold red", markup=False)
            raise typer.Exit(code=EXIT_USAGE) from None
        files[relative] = path.read_text(encoding="utf-8")

    if not files:
        console.print("未找到任何 .ifp 模型文件。", style="yellow", markup=False)
        raise typer.Exit(code=EXIT_FAILED)

    written = repo.write(files)
    commit = repo.commit(
        message,
        business_change=business_change,
        code_revision=code_revision,
        author=author,
    )
    console.print(f"已提交 {commit.sha[:12]}（{len(written)} 个文件）", style="green", markup=False)
    if commit.business_change:
        console.print(f"业务变更：{commit.business_change}", markup=False)
    if commit.code_revision:
        console.print(f"代码版本：{commit.code_revision}", markup=False)


@repo_app.command("log")
def repo_log(
    directory: Path = typer.Argument(..., help="模型仓库目录"),
    path: Optional[str] = typer.Option(None, "--path", help="只看某个模型文件的提交"),
    limit: int = typer.Option(20, "--limit", help="最多显示多少条"),
) -> None:
    """列出模型提交历史（含业务变更说明与代码版本）。"""

    commits = ModelRepo(directory).log(path, limit=limit)
    if not commits:
        console.print("暂无提交。", style="yellow", markup=False)
        return
    for commit in commits:
        console.print(f"{commit.sha[:12]} {commit.timestamp} {commit.author}", markup=False)
        console.print(f"    {commit.message}", markup=False)
        if commit.business_change:
            console.print(f"    业务变更：{commit.business_change}", style="cyan", markup=False)
        if commit.code_revision:
            console.print(f"    代码版本：{commit.code_revision}", style="cyan", markup=False)


@app.command("merge")
def merge_cmd(
    base: Path = typer.Argument(..., help="共同祖先版本"),
    ours: Path = typer.Argument(..., help="我方版本"),
    theirs: Path = typer.Argument(..., help="对方版本"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出结果"),
    audit_path: Optional[Path] = typer.Option(None, "--audit", help="写入审计日志（JSONL）"),
) -> None:
    """基于业务语义做三方合并：冲突定位到具体规则，无法自动消解时要求人工评审。

    退出码：``0`` 可自动合并 / ``1`` 存在需人工评审的冲突 / ``2`` 路径或用法错误。
    """

    try:
        result = merge_paths(base, ours, theirs)
    except IFPParseError as exc:
        console.print(str(exc), style="bold red", markup=False)
        raise typer.Exit(code=EXIT_USAGE) from exc

    if as_json:
        payload = {
            "clean": result.clean,
            "requires_manual_review": result.requires_manual_review,
            "conflicts": [
                {
                    "path": c.path,
                    "kind": c.kind,
                    "ours": c.ours,
                    "theirs": c.theirs,
                    "detail": c.detail,
                }
                for c in result.conflicts
            ],
            "ours_changes": result.ours_changes,
            "theirs_changes": result.theirs_changes,
        }
        console.print(json.dumps(payload, ensure_ascii=False, indent=2), markup=False)
    else:
        for note in result.ours_changes:
            console.print(f"我方 {note}", style="cyan", markup=False)
        for note in result.theirs_changes:
            console.print(f"对方 {note}", style="cyan", markup=False)
        for conflict in result.conflicts:
            console.print(conflict.format(), style="bold red", markup=False)
        if result.clean:
            console.print("语义合并无冲突，可自动合并。", style="green", markup=False)
        else:
            console.print(
                f"存在 {len(result.conflicts)} 处需人工评审的语义冲突，已拒绝自动合并。",
                style="bold red",
                markup=False,
            )

    if audit_path is not None:
        log = AuditLog(audit_path)
        record_merge(log, result, base=str(base), ours=str(ours), theirs=str(theirs))
        console.print(f"已写入审计日志：{audit_path}", style="green", markup=False)

    if not result.clean:
        raise typer.Exit(code=EXIT_FAILED)


@app.command("metrics")
def metrics_cmd(
    paths: list[Path] = typer.Argument(..., help="模型文件或目录"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """统计模型度量指标：规则覆盖率、死不变量数量、基线数量等。"""

    _, project = _load_project(paths)
    stats = collect_metrics(project)
    if as_json:
        console.print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2), markup=False)
        return
    for line in stats.summary_lines():
        console.print(line, markup=False)


@app.command("align")
def align_cmd(
    directory: Path = typer.Argument(..., help="模型仓库目录"),
    code_revision: Optional[str] = typer.Option(
        None, "--code-revision", help="当前代码版本，用于漂移判定"
    ),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """审计模型与代码版本的对齐情况。

    退出码：``0`` 全部对齐 / ``1`` 存在漂移或未关联代码版本的模型。
    """

    entries = alignment_report(directory, code_revision)
    if as_json:
        payload = [
            {
                "path": e.path,
                "commit": e.commit,
                "business_change": e.business_change,
                "code_revision": e.code_revision,
                "status": e.status,
            }
            for e in entries
        ]
        console.print(json.dumps(payload, ensure_ascii=False, indent=2), markup=False)
    elif not entries:
        console.print("仓库中没有已纳入版本管理的模型文件。", style="yellow", markup=False)
    else:
        for entry in entries:
            style = _ALIGNMENT_STYLE.get(entry.status, "white")
            console.print(
                f"[{entry.status}] {entry.path}  commit={entry.commit[:12]}"
                f"  code={entry.code_revision or '-'}"
                f"  业务变更={entry.business_change or '-'}",
                style=style,
                markup=False,
            )

    drifted = [e for e in entries if e.status != "aligned"]
    if drifted:
        console.print(
            f"{len(drifted)} 个模型文件未与代码版本对齐。", style="bold red", markup=False
        )
        raise typer.Exit(code=EXIT_FAILED)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
