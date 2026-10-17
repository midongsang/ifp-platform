"""IFP 命令行入口（Typer + Rich）。

命令一览：
    ifp check    <paths...>   类型检查 + Lint
    ifp verify   <model>      属性测试（SMT 反例搜索）+ 一致性检查
    ifp simulate <paths...>   运行 Scenario 仿真
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
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from . import __version__, parser, plugin, prefs, rbac
from .audit import AuditLog
from .baseline import APPROVAL_THRESHOLD, approval_required, baseline_invariants, risk_score
from .config import find_config, load_plan
from .diff import DiffResult, diff_paths
from .errors import Diagnostic, IFPParseError, Severity
from .exporter import to_json, to_markdown, to_openapi, to_protobuf
from .lint import lint as run_lint
from .lsp import serve
from .metrics import collect as collect_metrics
from .repo import ModelRepo, alignment_report
from .semantic_merge import merge_paths
from .semantic_merge import record as record_merge
from .simulate import simulate
from .typecheck import typecheck

if TYPE_CHECKING:  # 仅用于标注，运行时按需导入以保持 CLI 启动轻量
    from .updater import ReleaseInfo

app = typer.Typer(add_completion=False, help="IFP 意图形式化平台命令行工具")
console = Console()

_SEVERITY_STYLE = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
}


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _collect_paths(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.ifp")))
        else:
            files.append(path)
    return files


def _load_models(paths: list[Path]) -> list[tuple[Path, object]]:
    models = []
    for path in _collect_paths(paths):
        try:
            models.append((path, parser.load(path)))
        except IFPParseError as exc:
            console.print(str(exc), style="bold red", markup=False)
            raise typer.Exit(code=1) from exc
    return models


def _load_project(paths: list[Path]) -> tuple[list[tuple[Path, object]], object]:
    """加载全部文件，并返回合并后的项目模型（用于跨文件引用解析）。"""

    models = _load_models(paths)
    if not models:
        console.print("未找到任何 .ifp 模型文件。", style="yellow", markup=False)
        raise typer.Exit(code=1)
    project = parser.merge(
        [model for _, model in models],
        source=", ".join(str(path) for path, _ in models),
    )
    return models, project


def _print_diagnostics(diags: list[Diagnostic]) -> None:
    if not diags:
        return
    for diag in sorted(diags, key=lambda d: (d.file, d.line)):
        console.print(diag.format(), style=_SEVERITY_STYLE.get(diag.severity, "white"), markup=False)


def _fail_if_errors(diags: list[Diagnostic], strict: bool = False) -> None:
    blocking = [d for d in diags if d.severity is Severity.ERROR]
    if strict:
        blocking += [d for d in diags if d.severity is Severity.WARNING]
    if blocking:
        raise typer.Exit(code=1)


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
        raise typer.Exit(code=1) from exc

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
        raise typer.Exit(code=1) from exc
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
        raise typer.Exit(code=1)
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
        raise typer.Exit(code=1)
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
) -> None:
    """属性测试：自动搜索违反不变量的反例，并检查规则一致性。"""

    # 按需导入：z3 / SMT 编码只在 verify 命令用到，避免拖慢其他命令的启动
    from .smt import VerifyConfig, check_consistency, verify

    model = parser.load(model_path)

    cfg_file = config_path or find_config(model_path)
    plan_intent = None
    cfg = VerifyConfig()
    if cfg_file is not None:
        plan = load_plan(cfg_file)
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
        raise typer.Exit(code=1)
    console.print("未发现违反不变量的反例。", style="green", markup=False)


@app.command("simulate")
def simulate_cmd(
    paths: list[Path] = typer.Argument(..., help="包含 scenario 的模型文件或目录"),
    name: Optional[str] = typer.Option(None, "--name", help="只运行指定场景"),
) -> None:
    """运行 Scenario 仿真，验证行为符合业务预期。"""

    results = []
    _, project = _load_project(paths)
    results.extend(simulate(project, name))
    if not results:
        console.print("未找到任何场景。", style="yellow", markup=False)
        raise typer.Exit(code=1)
    failed = 0
    for result in results:
        style = "green" if result.passed else "bold red"
        console.print(result.summary(), style=style, markup=False)
        failed += 0 if result.passed else 1
    console.print(f"仿真完成：{len(results) - failed} passed / {failed} failed", markup=False)
    if failed:
        raise typer.Exit(code=1)


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
            raise typer.Exit(code=2)

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
            raise typer.Exit(code=2) from exc
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
        raise typer.Exit(code=3)

    if fail_on_approval and needs_approval:
        raise typer.Exit(code=1)


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

    model = parser.load(model_path)
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

    model = parser.load(model_path)

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
        raise typer.Exit(code=2)

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
        raise typer.Exit(code=1)
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
            raise typer.Exit(code=2) from None
        files[relative] = path.read_text(encoding="utf-8")

    if not files:
        console.print("未找到任何 .ifp 模型文件。", style="yellow", markup=False)
        raise typer.Exit(code=1)

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
        raise typer.Exit(code=2) from exc

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
        raise typer.Exit(code=1)


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
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
