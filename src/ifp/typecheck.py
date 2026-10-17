"""类型检查：校验 IFP 表达式类型安全与引用合法性。

设计原则：
- 全部问题以 :class:`Diagnostic` 累积返回，不抛异常，便于一次性报告。
- 资金类型统一 Decimal；浮点字面量会被推断为 Decimal。
"""

from __future__ import annotations

from typing import Optional

from . import ast
from .errors import Diagnostic, Severity

# 检查码
UNKNOWN_IDENTIFIER = "IFP1001"
CALL_NOT_ALLOWED = "IFP1002"
INVARIANT_NOT_BOOL = "IFP1003"
ACTION_CLAUSE_NOT_BOOL = "IFP1004"
UNKNOWN_ENUM = "IFP1005"
UNKNOWN_ENUM_MEMBER = "IFP1006"
OLD_OUTSIDE_POST = "IFP1007"
ARITH_TYPE_MISMATCH = "IFP1008"
COMPARE_TYPE_MISMATCH = "IFP1009"
LOGICAL_TYPE_MISMATCH = "IFP1010"
UNKNOWN_FIELD_TYPE = "IFP1011"
DUPLICATE_DECLARATION = "IFP1012"
UNKNOWN_VO_MEMBER = "IFP1023"

_NUMERIC = {"Decimal", "Int"}


def is_numeric(t: Optional[str]) -> bool:
    return t in _NUMERIC


def typecheck(model: ast.Model, project: Optional[ast.Model] = None) -> list[Diagnostic]:
    """对模型执行类型检查，返回诊断列表。

    当模型只是多文件项目中的一部分时，可传入合并后的 ``project``，
    用于跨文件解析状态机、值对象与 Intent 引用；诊断仍归属当前文件。
    """

    project = project or model
    diags: list[Diagnostic] = []
    enums: dict[str, tuple[str, ...]] = {}
    value_objects: dict[str, ast.ValueDecl] = {}

    for ctx in project.contexts:
        for vo in ctx.values:
            value_objects[vo.name] = vo
        for intent in ctx.intents:
            for state in intent.states:
                enums[state.name] = state.values

    for ctx in model.contexts:
        for vo in ctx.values:
            diags.extend(_check_value(vo, value_objects, enums, model.source))
        for intent in ctx.intents:
            diags.extend(_check_intent(intent, enums, value_objects, model.source))
        for contract in ctx.contracts:
            diags.extend(_check_contract(contract, ctx, model.source))

    diags.extend(_check_scenarios(model, enums, project, value_objects))
    diags.extend(_check_enum_refs(model, enums))
    return diags


def _sub_exprs(expr: ast.Expr) -> list[ast.Expr]:
    """返回表达式的直接子表达式。"""

    if isinstance(expr, ast.BinaryExpr):
        return [expr.left, expr.right]
    if isinstance(expr, (ast.UnaryExpr, ast.OldExpr)):
        return [expr.operand if isinstance(expr, ast.UnaryExpr) else expr.inner]
    if isinstance(expr, ast.CallExpr):
        return list(expr.args)
    return []


def _all_exprs(model: ast.Model):
    """遍历模型中出现的所有表达式。"""

    for ctx in model.contexts:
        for vo in ctx.values:
            for f in vo.fields:
                if f.default is not None:
                    yield f.default
            for inv in vo.invariants:
                if inv.expr is not None:
                    yield inv.expr
        for intent in ctx.intents:
            for f in intent.fields:
                if f.default is not None:
                    yield f.default
            if intent.initial is not None:
                yield intent.initial
            for inv in intent.invariants:
                if inv.expr is not None:
                    yield inv.expr
            for action in intent.actions:
                if action.pre is not None:
                    yield action.pre
                if action.post is not None:
                    yield action.post
        for contract in ctx.contracts:
            for inv in contract.invariants:
                if inv.expr is not None:
                    yield inv.expr
    for scenario in model.scenarios:
        for _, expr in scenario.given:
            yield expr
        for step in scenario.steps:
            if isinstance(step, ast.WhenStep):
                for _, expr in step.args:
                    yield expr
            else:
                yield step.expr


def _check_enum_refs(model: ast.Model, enums: dict[str, tuple[str, ...]]) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    seen: set[int] = set()
    for root in _all_exprs(model):
        stack = [root]
        while stack:
            node = stack.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, ast.EnumRef):
                values = enums.get(node.enum)
                if values is None:
                    diags.append(
                        Diagnostic(
                            UNKNOWN_ENUM,
                            f"引用未定义的状态机 {node.enum}",
                            Severity.ERROR, model.source, node.line,
                        )
                    )
                elif node.member not in values:
                    diags.append(
                        Diagnostic(
                            UNKNOWN_ENUM_MEMBER,
                            f"{node.enum} 中不存在状态 {node.member}",
                            Severity.ERROR, model.source, node.line,
                        )
                    )
            stack.extend(_sub_exprs(node))
    return diags


# ---------------------------------------------------------------------------
# value object / intent / contract
# ---------------------------------------------------------------------------

def _check_value(
    vo: ast.ValueDecl,
    value_objects: dict[str, ast.ValueDecl],
    enums: dict[str, tuple[str, ...]],
    source: str,
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    env = _env_of(vo.fields, source, diags, enums=enums, value_objects=value_objects)
    for inv in vo.invariants:
        _require_bool(
            inv.expr, _where("value", vo.name, inv.name), env, diags, source,
            enums, value_objects,
        )
    return diags


def _check_intent(
    intent: ast.IntentDecl,
    enums: dict[str, tuple[str, ...]],
    value_objects: dict[str, ast.ValueDecl],
    source: str,
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    env = _env_of(intent.fields, source, diags, enums=enums, value_objects=value_objects)

    for inv in intent.invariants:
        _require_bool(
            inv.expr, _where("intent", intent.name, inv.name), env, diags, source,
            enums, value_objects,
        )

    for action in intent.actions:
        scope = dict(env)
        for p in action.params:
            scope[p.name] = _resolve_type(p.type, enums, value_objects, source, diags, p.line)
        where = _where("intent", intent.name, action.name)
        if action.pre is None:
            diags.append(
                Diagnostic(
                    "IFP1020", f"动作 {action.name} 缺少前置条件 pre", Severity.WARNING,
                    source, action.line,
                )
            )
        if action.post is None:
            diags.append(
                Diagnostic(
                    "IFP1021", f"动作 {action.name} 缺少后置条件 post", Severity.WARNING,
                    source, action.line,
                )
            )
        if action.pre is not None:
            _require_bool(
                action.pre, where + " 的 pre", scope, diags, source, enums, value_objects,
                in_post=False,
            )
        if action.post is not None:
            _require_bool(
                action.post, where + " 的 post", scope, diags, source, enums, value_objects,
                in_post=True,
            )

    return diags


def _check_contract(contract: ast.ContractDecl, ctx: ast.ContextDecl, source: str) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    field_names = {f.name for intent in ctx.intents for f in intent.fields}
    for name in contract.exposes:
        if name not in field_names:
            diags.append(
                Diagnostic(
                    "IFP1022",
                    f"契约 {contract.name} 暴露了未定义的字段 {name}",
                    Severity.ERROR, source, contract.line,
                )
            )
    return diags


# ---------------------------------------------------------------------------
# 表达式
# ---------------------------------------------------------------------------

def _env_of(
    fields: tuple[ast.FieldDecl, ...],
    source: str,
    diags: list[Diagnostic],
    enums: Optional[dict[str, tuple[str, ...]]],
    value_objects: dict[str, ast.ValueDecl],
) -> dict[str, Optional[str]]:
    env: dict[str, Optional[str]] = {}
    for f in fields:
        if f.name in env:
            diags.append(
                Diagnostic(
                    "IFP1012", f"字段 {f.name} 重复定义", Severity.ERROR, source, f.line
                )
            )
        env[f.name] = _resolve_type(
            f.type, enums or {}, value_objects, source, diags, f.line
        )
    return env


def _resolve_type(
    t: ast.TypeRef,
    enums: dict[str, tuple[str, ...]],
    value_objects: dict[str, ast.ValueDecl],
    source: str,
    diags: list[Diagnostic],
    line: int,
) -> Optional[str]:
    if t.kind != "Named":
        return t.kind
    if t.name in enums:
        return f"Enum:{t.name}"
    if t.name in value_objects:
        return f"VO:{t.name}"
    diags.append(
        Diagnostic(
            UNKNOWN_FIELD_TYPE,
            f"字段类型 {t.name} 未定义（应为状态机或值对象）",
            Severity.ERROR, source, line,
        )
    )
    return None


def _leaf_names(fields: tuple[ast.FieldDecl, ...], value_objects: dict[str, ast.ValueDecl]) -> set[str]:
    """展开字段得到可寻址的叶子名（值对象按 ``field.member`` 展开）。"""

    names: set[str] = set()
    for f in fields:
        if f.type.kind == "Named" and f.type.name in value_objects:
            for leaf in _leaf_names(value_objects[f.type.name].fields, value_objects):
                names.add(f"{f.name}.{leaf}")
        else:
            names.add(f.name)
    return names


def _resolve_access(
    expr: ast.FieldAccess,
    env: dict[str, Optional[str]],
    value_objects: dict[str, ast.ValueDecl],
    diags: list[Diagnostic],
    source: str,
) -> Optional[str]:
    """解析值对象成员访问 ``balance.amount`` 的类型。"""

    path = expr.path
    dotted = expr.dotted
    base = path[0]
    if base not in env:
        diags.append(
            Diagnostic(
                UNKNOWN_IDENTIFIER, f"引用了未定义的标识符 {dotted}",
                Severity.ERROR, source, expr.line,
            )
        )
        return None
    base_type = env[base]
    if not base_type or not base_type.startswith("VO:"):
        diags.append(
            Diagnostic(
                UNKNOWN_VO_MEMBER,
                f"{base} 不是值对象，不能使用成员访问 {dotted}",
                Severity.ERROR, source, expr.line,
            )
        )
        return None

    fields = value_objects[base_type[3:]].fields
    for index, part in enumerate(path[1:]):
        field = next((f for f in fields if f.name == part), None)
        if field is None:
            diags.append(
                Diagnostic(
                    UNKNOWN_VO_MEMBER,
                    f"值对象成员访问 {dotted} 中不存在成员 {part}",
                    Severity.ERROR, source, expr.line,
                )
            )
            return None
        last = index == len(path[1:]) - 1
        if last:
            if field.type.kind == "Named" and field.type.name in value_objects:
                return f"VO:{field.type.name}"
            return field.type.kind
        if field.type.kind != "Named" or field.type.name not in value_objects:
            diags.append(
                Diagnostic(
                    UNKNOWN_VO_MEMBER,
                    f"{part} 不是值对象，不能继续访问成员 {dotted}",
                    Severity.ERROR, source, expr.line,
                )
            )
            return None
        fields = value_objects[field.type.name].fields
    return None


def _require_bool(
    expr: Optional[ast.Expr],
    where: str,
    env: dict[str, Optional[str]],
    diags: list[Diagnostic],
    source: str,
    enums: dict[str, tuple[str, ...]],
    value_objects: dict[str, ast.ValueDecl],
    in_post: bool = False,
) -> None:
    if expr is None:
        return
    t = _infer(expr, env, diags, source, in_post, enums, value_objects)
    if t is not None and t != "Bool":
        diags.append(
            Diagnostic(
                INVARIANT_NOT_BOOL if "invariant" in where else ACTION_CLAUSE_NOT_BOOL,
                f"{where} 必须是布尔表达式，实际类型为 {t}",
                Severity.ERROR, source, expr.line,
            )
        )


def _infer(
    expr: ast.Expr,
    env: dict[str, Optional[str]],
    diags: list[Diagnostic],
    source: str,
    in_post: bool,
    enums: dict[str, tuple[str, ...]],
    value_objects: dict[str, ast.ValueDecl],
) -> Optional[str]:
    if isinstance(expr, ast.NumberLit):
        return "Decimal" if "." in expr.value else "Int"
    if isinstance(expr, ast.StringLit):
        return "String"
    if isinstance(expr, ast.BoolLit):
        return "Bool"
    if isinstance(expr, ast.FieldAccess):
        return _resolve_access(expr, env, value_objects, diags, source)
    if isinstance(expr, ast.VarRef):
        if expr.name not in env:
            diags.append(
                Diagnostic(
                    UNKNOWN_IDENTIFIER,
                    f"引用了未定义的标识符 {expr.name}",
                    Severity.ERROR, source, expr.line,
                )
            )
            return None
        return env[expr.name]
    if isinstance(expr, ast.EnumRef):
        # 枚举存在性与成员合法性由 _check_enum_refs 统一校验
        return f"Enum:{expr.enum}"
    if isinstance(expr, ast.CallExpr):
        diags.append(
            Diagnostic(
                CALL_NOT_ALLOWED,
                f"表达式内不允许调用 {expr.name}()（调用只出现在场景 when/rejected）",
                Severity.ERROR, source, expr.line,
            )
        )
        return None
    if isinstance(expr, ast.OldExpr):
        if not in_post:
            diags.append(
                Diagnostic(
                    OLD_OUTSIDE_POST,
                    "old(...) 只能出现在动作的 post 条件中",
                    Severity.ERROR, source, expr.line,
                )
            )
        return _infer(expr.inner, env, diags, source, in_post, enums, value_objects)
    if isinstance(expr, ast.UnaryExpr):
        t = _infer(expr.operand, env, diags, source, in_post, enums, value_objects)
        if t is not None and not is_numeric(t):
            diags.append(
                Diagnostic(
                    ARITH_TYPE_MISMATCH,
                    f"一元运算符 - 需要数值类型，实际为 {t}",
                    Severity.ERROR, source, expr.line,
                )
            )
        return t
    if isinstance(expr, ast.BinaryExpr):
        return _infer_binary(expr, env, diags, source, in_post, enums, value_objects)
    return None


def _infer_binary(
    expr: ast.BinaryExpr,
    env: dict[str, Optional[str]],
    diags: list[Diagnostic],
    source: str,
    in_post: bool,
    enums: dict[str, tuple[str, ...]],
    value_objects: dict[str, ast.ValueDecl],
) -> Optional[str]:
    lt = _infer(expr.left, env, diags, source, in_post, enums, value_objects)
    rt = _infer(expr.right, env, diags, source, in_post, enums, value_objects)
    op = expr.op

    if op in {"&&", "||"}:
        if lt not in (None, "Bool") or rt not in (None, "Bool"):
            diags.append(
                Diagnostic(
                    LOGICAL_TYPE_MISMATCH,
                    f"逻辑运算 {op} 需要布尔操作数，实际为 {lt} / {rt}",
                    Severity.ERROR, source, expr.line,
                )
            )
        return "Bool"

    if op in {"+", "-", "*", "/"}:
        if lt is None or rt is None:
            return None
        if not is_numeric(lt) or not is_numeric(rt):
            diags.append(
                Diagnostic(
                    ARITH_TYPE_MISMATCH,
                    f"算术运算 {op} 需要数值操作数，实际为 {lt} / {rt}",
                    Severity.ERROR, source, expr.line,
                )
            )
            return None
        if "/" in op or "Decimal" in (lt, rt):
            return "Decimal"
        return "Int"

    # 比较运算
    if lt is None or rt is None:
        return "Bool"
    if is_numeric(lt) and is_numeric(rt):
        return "Bool"  # Decimal 与 Int 可混合比较
    if lt != rt:
        diags.append(
            Diagnostic(
                COMPARE_TYPE_MISMATCH,
                f"比较运算 {op} 两侧类型不一致：{lt} / {rt}",
                Severity.ERROR, source, expr.line,
            )
        )
    if op in {"<", "<=", ">", ">="} and not is_numeric(lt):
        diags.append(
            Diagnostic(
                COMPARE_TYPE_MISMATCH,
                f"大小比较 {op} 需要数值操作数，实际为 {lt}",
                Severity.ERROR, source, expr.line,
            )
        )
    return "Bool"


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

def _check_scenarios(
    model: ast.Model,
    enums: dict[str, tuple[str, ...]],
    project: ast.Model,
    value_objects: dict[str, ast.ValueDecl],
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for scenario in model.scenarios:
        if scenario.intent is None:
            diags.append(
                Diagnostic("IFP1030", f"场景 {scenario.name} 缺少 intent", Severity.ERROR, model.source, scenario.line)
            )
            continue
        intent = project.find_intent(scenario.intent)
        if intent is None:
            diags.append(
                Diagnostic("IFP1031", f"场景 {scenario.name} 引用了未定义的 intent {scenario.intent}", Severity.ERROR, model.source, scenario.line)
            )
            continue
        actions = {a.name: a for a in intent.actions}

        allowed = _leaf_names(intent.fields, value_objects)
        for name, _ in scenario.given:
            if name not in allowed:
                diags.append(
                    Diagnostic("IFP1032", f"场景 {scenario.name} given 引用了未定义字段 {name}", Severity.ERROR, model.source, scenario.line)
                )
        for step in scenario.steps:
            if isinstance(step, ast.WhenStep):
                action = actions.get(step.action)
                if action is None:
                    diags.append(
                        Diagnostic("IFP1033", f"场景 {scenario.name} 调用了未定义动作 {step.action}", Severity.ERROR, model.source, step.line)
                    )
                    continue
                provided = {n for n, _ in step.args}
                expected = {p.name for p in action.params}
                if provided != expected:
                    diags.append(
                        Diagnostic(
                            "IFP1034",
                            f"场景 {scenario.name} 调用 {step.action} 参数不匹配：传入 {sorted(provided)}，期望 {sorted(expected)}",
                            Severity.ERROR, model.source, step.line,
                        )
                    )
    return diags


def _where(kind: str, owner: str, member: str) -> str:
    return f"{kind} {owner} 的 {member}"
