"""Scenario 仿真：按人工构造的操作序列执行模型，校验行为符合业务预期。

对应《IFP 建模规范》第 8 节。与 PropertyTest 的分工：
- Scenario 覆盖正常流程 / 边界正向 / 非法操作拦截；
- PropertyTest 负责大规模边界挖掘（smt.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from . import ast
from .render import flatten_and

# 状态值：Decimal -> Decimal，Int -> int，Bool -> bool，String -> str，
#        枚举 -> "Enum::Member" 字符串
Value = Any


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    messages: list[str] = field(default_factory=list)
    final_state: dict[str, Value] = field(default_factory=dict)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        detail = "" if self.passed else " | " + "; ".join(self.messages)
        return f"[{status}] {self.name}{detail}"


# ---------------------------------------------------------------------------
# 表达式求值
# ---------------------------------------------------------------------------

def eval_expr(expr: ast.Expr, state: dict[str, Value], params: dict[str, Value]) -> Value:
    if isinstance(expr, ast.NumberLit):
        return _literal(expr.value)
    if isinstance(expr, ast.StringLit):
        return expr.value
    if isinstance(expr, ast.BoolLit):
        return expr.value
    if isinstance(expr, ast.VarRef):
        if expr.name in params:
            return params[expr.name]
        if expr.name in state:
            return state[expr.name]
        raise KeyError(f"未初始化的字段 {expr.name}")
    if isinstance(expr, ast.FieldAccess):
        key = expr.dotted
        if key in params:
            return params[key]
        if key in state:
            return state[key]
        raise KeyError(f"未初始化的字段 {key}")
    if isinstance(expr, ast.EnumRef):
        return f"{expr.enum}::{expr.member}"
    if isinstance(expr, ast.OldExpr):
        return eval_expr(expr.inner, state, params)
    if isinstance(expr, ast.UnaryExpr):
        return -eval_expr(expr.operand, state, params)
    if isinstance(expr, ast.BinaryExpr):
        return _eval_binary(expr, state, params)
    raise ValueError(f"场景中无法求值的表达式：{expr!r}")


def _literal(raw: str) -> Value:
    if "." in raw:
        try:
            return Decimal(raw)
        except InvalidOperation:  # pragma: no cover - 语法层已保证
            return float(raw)
    return int(raw)


def _eval_binary(expr: ast.BinaryExpr, state: dict[str, Value], params: dict[str, Value]) -> Value:
    left = eval_expr(expr.left, state, params)
    right = eval_expr(expr.right, state, params)
    op = expr.op
    if op == "&&":
        return bool(left) and bool(right)
    if op == "||":
        return bool(left) or bool(right)
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        return left / right
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    raise ValueError(f"不支持的运算符 {op}")


def apply_post(
    post: Optional[ast.Expr], state: dict[str, Value], params: dict[str, Value]
) -> dict[str, Value]:
    """按 post 中的等式合取推导新状态；未提及字段保持不变（frame）。"""

    new_state = dict(state)
    if post is None:
        return new_state
    for conjunct in flatten_and(post):
        if not (isinstance(conjunct, ast.BinaryExpr) and conjunct.op == "=="):
            continue
        if isinstance(conjunct.left, ast.VarRef):
            new_state[conjunct.left.name] = eval_expr(conjunct.right, state, params)
        elif isinstance(conjunct.left, ast.FieldAccess):
            new_state[conjunct.left.dotted] = eval_expr(conjunct.right, state, params)
    return new_state


# ---------------------------------------------------------------------------
# 场景执行
# ---------------------------------------------------------------------------

def simulate(model: ast.Model, name: Optional[str] = None) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for scenario in model.scenarios:
        if name and scenario.name != name:
            continue
        results.append(run_scenario(model, scenario))
    return results


def run_scenario(model: ast.Model, scenario: ast.ScenarioDecl) -> ScenarioResult:
    messages: list[str] = []
    intent = model.find_intent(scenario.intent) if scenario.intent else None
    if intent is None:
        return ScenarioResult(scenario.name, False, [f"未找到 intent {scenario.intent}"])

    value_objects = {vo.name: vo for ctx in model.contexts for vo in ctx.values}
    state: dict[str, Value] = {}
    for f in intent.fields:
        if f.type.kind == "Named" and f.type.name in value_objects:
            # 值对象字段以其成员（``balance.amount``）承载状态
            for key, expr in _vo_defaults(f.name, value_objects[f.type.name], value_objects):
                state[key] = eval_expr(expr, {}, {})
        elif f.default is not None:
            state[f.name] = eval_expr(f.default, {}, {})

    for field_name, expr in scenario.given:
        try:
            state[field_name] = eval_expr(expr, state, {})
        except KeyError as exc:
            return ScenarioResult(scenario.name, False, [str(exc)])

    missing = sorted(_leaf_names(intent.fields, value_objects) - set(state))
    if missing:
        return ScenarioResult(
            scenario.name, False, [f"初始状态缺少字段赋值：{', '.join(missing)}"]
        )

    actions = {a.name: a for a in intent.actions}
    for step in scenario.steps:
        if isinstance(step, ast.WhenStep):
            action = actions.get(step.action)
            if action is None:
                messages.append(f"未定义动作 {step.action}")
                return ScenarioResult(scenario.name, False, messages, state)
            try:
                params = {n: eval_expr(e, state, {}) for n, e in step.args}
            except KeyError as exc:
                messages.append(str(exc))
                return ScenarioResult(scenario.name, False, messages, state)

            pre_ok = (
                bool(eval_expr(action.pre, state, params)) if action.pre is not None else True
            )
            if step.rejected:
                if pre_ok:
                    messages.append(
                        f"期望动作 {action.name} 被前置条件拒绝，但前置条件成立"
                    )
                    return ScenarioResult(scenario.name, False, messages, state)
                continue

            if not pre_ok:
                messages.append(f"动作 {action.name} 前置条件不满足，无法执行")
                return ScenarioResult(scenario.name, False, messages, state)

            state = apply_post(action.post, state, params)
            violations = _check_invariants(intent, state, value_objects)
            if violations:
                messages.append(
                    f"动作 {action.name} 执行后违反不变量：{', '.join(violations)}"
                )
                return ScenarioResult(scenario.name, False, messages, state)
        else:
            try:
                value = eval_expr(step.expr, state, {})
            except KeyError as exc:
                messages.append(str(exc))
                return ScenarioResult(scenario.name, False, messages, state)
            if value is not True:
                messages.append("then 断言不成立")
                return ScenarioResult(scenario.name, False, messages, state)

    return ScenarioResult(scenario.name, True, messages, state)


def _check_invariants(
    intent: ast.IntentDecl,
    state: dict[str, Value],
    value_objects: dict[str, ast.ValueDecl],
) -> list[str]:
    """校验 Intent 自身不变量，以及值对象字段实例内联展开后的不变量。"""

    violated: list[str] = []
    for inv in intent.invariants:
        if inv.expr is None:
            continue
        try:
            if eval_expr(inv.expr, state, {}) is not True:
                violated.append(inv.name)
        except KeyError:
            continue
    for f in intent.fields:
        if f.type.kind == "Named" and f.type.name in value_objects:
            _check_value_object(f.name, value_objects[f.type.name], state, value_objects, violated)
    return violated


def _check_value_object(
    prefix: str,
    vo: ast.ValueDecl,
    state: dict[str, Value],
    value_objects: dict[str, ast.ValueDecl],
    violated: list[str],
) -> None:
    """按前缀剥离出值对象的局部状态，校验其不变量（递归处理嵌套值对象）。"""

    marker = prefix + "."
    local = {key[len(marker):]: value for key, value in state.items() if key.startswith(marker)}
    for inv in vo.invariants:
        if inv.expr is None:
            continue
        try:
            if eval_expr(inv.expr, local, {}) is not True:
                violated.append(f"{prefix}.{inv.name}")
        except KeyError:
            continue
    for f in vo.fields:
        if f.type.kind == "Named" and f.type.name in value_objects:
            _check_value_object(
                f"{prefix}.{f.name}", value_objects[f.type.name], state, value_objects, violated
            )


def _leaf_names(
    fields: tuple[ast.FieldDecl, ...], value_objects: dict[str, ast.ValueDecl]
) -> set[str]:
    """展开字段得到可寻址的叶子名（值对象按 ``field.member`` 展开）。"""

    names: set[str] = set()
    for f in fields:
        if f.type.kind == "Named" and f.type.name in value_objects:
            for leaf in _leaf_names(value_objects[f.type.name].fields, value_objects):
                names.add(f"{f.name}.{leaf}")
        else:
            names.add(f.name)
    return names


def _vo_defaults(
    prefix: str,
    vo: ast.ValueDecl,
    value_objects: dict[str, ast.ValueDecl],
) -> list[tuple[str, ast.Expr]]:
    """收集值对象实例的叶子字段默认值（形如 ``money.amount``）。"""

    out: list[tuple[str, ast.Expr]] = []
    for f in vo.fields:
        key = f"{prefix}.{f.name}"
        if f.type.kind == "Named" and f.type.name in value_objects:
            out.extend(_vo_defaults(key, value_objects[f.type.name], value_objects))
        elif f.default is not None:
            out.append((key, f.default))
    return out
