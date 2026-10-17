"""模型静态检查（Lint）：校验建模规范与最佳实践。

对应《IFP 建模规范》第 10 节“模型静态检查（Lint）规则”。
与 typecheck 的分工：typecheck 关注“类型 / 引用是否合法”，
lint 关注“是否符合建模规范与最佳实践”。
"""

from __future__ import annotations

from typing import Callable, Iterable

from . import ast
from .errors import Diagnostic, Severity
from .render import render_expr

BASELINE_MISSING_OWNER = "IFP2001"
BASELINE_MISSING_RISK = "IFP2002"
UNREACHABLE_STATE = "IFP2003"
DEAD_INVARIANT = "IFP2004"
NO_INVARIANT = "IFP2005"
CONTEXT_NAMING = "IFP2006"
SCENARIO_NO_REJECT = "IFP2007"
MISSING_INITIAL_STATE = "IFP2008"
EMPTY_STATE_MACHINE = "IFP2009"

LintRule = Callable[[ast.Model], list[Diagnostic]]


def lint(model: ast.Model, extra_rules: Iterable[LintRule] | None = None) -> list[Diagnostic]:
    """执行全部 Lint 规则，返回诊断列表。"""

    diags: list[Diagnostic] = []
    for ctx in model.contexts:
        diags.extend(_lint_context(ctx, model.source))
        for intent in ctx.intents:
            diags.extend(_lint_intent(intent, model.source))
    for scenario in model.scenarios:
        diags.extend(_lint_scenario(scenario, model.source))

    for rule in extra_rules or ():
        diags.extend(rule(model))
    return diags


# ---------------------------------------------------------------------------

def _lint_context(ctx: ast.ContextDecl, source: str) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    if not ctx.name.endswith("Context"):
        diags.append(
            Diagnostic(
                CONTEXT_NAMING,
                f"限界上下文命名建议以 'Context' 结尾，当前为 {ctx.name}",
                Severity.WARNING, source, ctx.line,
            )
        )
    return diags


def _lint_intent(intent: ast.IntentDecl, source: str) -> list[Diagnostic]:
    diags: list[Diagnostic] = []

    # 基线注释完整性
    for inv in intent.invariants:
        if inv.baseline and not inv.owner:
            diags.append(
                Diagnostic(
                    BASELINE_MISSING_OWNER,
                    f"基线不变量 {inv.name} 缺少 business owner 注释",
                    Severity.ERROR, source, inv.line,
                )
            )
        if inv.baseline and not inv.risk:
            diags.append(
                Diagnostic(
                    BASELINE_MISSING_RISK,
                    f"基线不变量 {inv.name} 缺少风险描述（risk）",
                    Severity.WARNING, source, inv.line,
                )
            )

    # 死不变量：结构完全重复 / 恒真比较
    seen: dict[str, str] = {}
    for inv in intent.invariants:
        if inv.expr is None:
            continue
        key = render_expr(inv.expr)
        if key in seen:
            diags.append(
                Diagnostic(
                    DEAD_INVARIANT,
                    f"不变量 {inv.name} 与 {seen[key]} 表达式完全重复（死不变量）",
                    Severity.WARNING, source, inv.line,
                )
            )
        else:
            seen[key] = inv.name
        if _is_trivially_true(inv.expr):
            diags.append(
                Diagnostic(
                    DEAD_INVARIANT,
                    f"不变量 {inv.name} 为恒真表达式，不产生约束效果（死不变量）",
                    Severity.WARNING, source, inv.line,
                )
            )

    if not intent.invariants:
        diags.append(
            Diagnostic(
                NO_INVARIANT,
                f"Intent {intent.name} 未定义任何不变量，无法约束业务规则",
                Severity.WARNING, source, intent.line,
            )
        )

    # 状态机检查
    for state in intent.states:
        if not state.values:
            diags.append(
                Diagnostic(
                    EMPTY_STATE_MACHINE,
                    f"状态机 {state.name} 没有任何状态值",
                    Severity.ERROR, source, state.line,
                )
            )
    if intent.states:
        if intent.initial is None:
            diags.append(
                Diagnostic(
                    MISSING_INITIAL_STATE,
                    f"Intent {intent.name} 定义了状态机但未声明初始状态（initial）",
                    Severity.WARNING, source, intent.line,
                )
            )
        else:
            reachable = {f"{intent.initial.enum}::{intent.initial.member}"}
            for action in intent.actions:
                if action.transition is not None:
                    reachable.add(f"{action.transition.enum}::{action.transition.member}")
            for state in intent.states:
                for value in state.values:
                    if f"{state.name}::{value}" not in reachable:
                        diags.append(
                            Diagnostic(
                                UNREACHABLE_STATE,
                                f"状态 {state.name}::{value} 无任何动作可达（不可达状态）",
                                Severity.WARNING, source, state.line,
                            )
                        )
    return diags


def _lint_scenario(scenario: ast.ScenarioDecl, source: str) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    has_reject = any(isinstance(s, ast.WhenStep) and s.rejected for s in scenario.steps)
    if not has_reject:
        diags.append(
            Diagnostic(
                SCENARIO_NO_REJECT,
                f"场景 {scenario.name} 未覆盖非法操作拦截（缺少 rejected 步骤）",
                Severity.INFO, source, scenario.line,
            )
        )
    return diags


def _is_trivially_true(expr: ast.Expr) -> bool:
    if isinstance(expr, ast.BoolLit):
        return expr.value
    return (
        isinstance(expr, ast.BinaryExpr)
        and expr.op in {"<=", ">=", "=="}
        and render_expr(expr.left) == render_expr(expr.right)
    )
