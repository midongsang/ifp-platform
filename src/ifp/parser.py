"""IFP DSL 解析器：`.ifp` 源码 -> AST。

基于 Lark（LALR），语法定义见 ``grammar.lark``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Sequence

from lark import Lark, Token, Transformer, Tree
from lark.exceptions import LarkError, UnexpectedInput

from . import ast
from .errors import IFPParseError

_GRAMMAR_PATH = Path(__file__).with_name("grammar.lark")


class _ToAst(Transformer):
    """Lark parse tree -> IFP AST。"""

    # -- 顶层 ---------------------------------------------------------------
    def start(self, children: list[Any]) -> ast.Model:
        contexts = tuple(c for c in children if isinstance(c, ast.ContextDecl))
        scenarios = tuple(s for s in children if isinstance(s, ast.ScenarioDecl))
        return ast.Model(contexts=contexts, scenarios=scenarios)

    def description(self, children: Sequence[Any]) -> tuple[str, str]:
        return ("__description__", _unquote(children[0]))

    def context_item(self, children: Sequence[Any]) -> Any:
        return children[0]

    # -- context ------------------------------------------------------------
    def context_decl(self, children: Sequence[Any]) -> ast.ContextDecl:
        name_tok = children[0]
        values, intents, contracts, desc = [], [], [], None
        for child in children[1:]:
            if isinstance(child, tuple):
                desc = child[1]
            elif isinstance(child, ast.ValueDecl):
                values.append(child)
            elif isinstance(child, ast.IntentDecl):
                intents.append(child)
            elif isinstance(child, ast.ContractDecl):
                contracts.append(child)
        return ast.ContextDecl(
            name=str(name_tok),
            description=desc,
            values=tuple(values),
            intents=tuple(intents),
            contracts=tuple(contracts),
            line=name_tok.line,
        )

    # -- value object -------------------------------------------------------
    def value_item(self, children: Sequence[Any]) -> Any:
        return children[0]

    def value_decl(self, children: Sequence[Any]) -> ast.ValueDecl:
        name_tok = children[0]
        fields, invariants, desc = [], [], None
        for child in children[1:]:
            if isinstance(child, tuple):
                desc = child[1]
            elif isinstance(child, ast.FieldDecl):
                fields.append(child)
            elif isinstance(child, ast.InvariantDecl):
                invariants.append(child)
        return ast.ValueDecl(
            name=str(name_tok),
            description=desc,
            fields=tuple(fields),
            invariants=tuple(invariants),
            line=name_tok.line,
        )

    # -- intent -------------------------------------------------------------
    def intent_item(self, children: Sequence[Any]) -> Any:
        return children[0]

    def intent_decl(self, children: Sequence[Any]) -> ast.IntentDecl:
        name_tok = children[0]
        fields, states, invariants, actions = [], [], [], []
        initial: Optional[ast.EnumRef] = None
        desc = None
        for child in children[1:]:
            if isinstance(child, tuple):
                desc = child[1]
            elif isinstance(child, ast.FieldDecl):
                fields.append(child)
            elif isinstance(child, ast.StateDecl):
                states.append(child)
            elif isinstance(child, ast.EnumRef):
                initial = child
            elif isinstance(child, ast.InvariantDecl):
                invariants.append(child)
            elif isinstance(child, ast.ActionDecl):
                actions.append(child)
        return ast.IntentDecl(
            name=str(name_tok),
            description=desc,
            fields=tuple(fields),
            states=tuple(states),
            initial=initial,
            invariants=tuple(invariants),
            actions=tuple(actions),
            line=name_tok.line,
        )

    def initial_decl(self, children: Sequence[Any]) -> ast.EnumRef:
        return children[0]

    # -- 字段与类型 ---------------------------------------------------------
    def t_decimal(self, _c: Sequence[Any]) -> ast.TypeRef:
        return ast.TypeRef("Decimal")

    def t_int(self, _c: Sequence[Any]) -> ast.TypeRef:
        return ast.TypeRef("Int")

    def t_bool(self, _c: Sequence[Any]) -> ast.TypeRef:
        return ast.TypeRef("Bool")

    def t_string(self, _c: Sequence[Any]) -> ast.TypeRef:
        return ast.TypeRef("String")

    def t_named(self, children: Sequence[Any]) -> ast.TypeRef:
        return ast.TypeRef("Named", str(children[0]))

    def field_decl(self, children: Sequence[Any]) -> ast.FieldDecl:
        name_tok = children[0]
        type_ref = children[1]
        default = children[2] if len(children) > 2 else None
        return ast.FieldDecl(
            name=str(name_tok), type=type_ref, default=default, line=name_tok.line
        )

    # -- 状态机 -------------------------------------------------------------
    def state_value(self, children: Sequence[Any]) -> str:
        return str(children[0])

    def state_decl(self, children: Sequence[Any]) -> ast.StateDecl:
        name_tok = children[0]
        return ast.StateDecl(
            name=str(name_tok), values=tuple(children[1:]), line=name_tok.line
        )

    # -- 不变量 -------------------------------------------------------------
    def invariant_decl(self, children: Sequence[Any]) -> ast.InvariantDecl:
        name_tok = children[0]
        expr: Optional[ast.Expr] = None
        baseline = False
        risk = owner = note = None
        for child in children[1:]:
            key, value = child
            if key == "expr":
                expr = value
            elif key == "baseline":
                baseline = value
            elif key == "risk":
                risk = value
            elif key == "owner":
                owner = value
            elif key == "note":
                note = value
        return ast.InvariantDecl(
            name=str(name_tok),
            expr=expr,
            baseline=baseline,
            risk=risk,
            owner=owner,
            note=note,
            line=name_tok.line,
        )

    def inv_expr(self, children: Sequence[Any]) -> tuple[str, ast.Expr]:
        return ("expr", children[0])

    def inv_baseline(self, children: Sequence[Any]) -> tuple[str, bool]:
        return ("baseline", bool(children[0]))

    def inv_risk(self, children: Sequence[Any]) -> tuple[str, str]:
        return ("risk", _unquote(children[0]))

    def inv_owner(self, children: Sequence[Any]) -> tuple[str, str]:
        return ("owner", _unquote(children[0]))

    def inv_note(self, children: Sequence[Any]) -> tuple[str, str]:
        return ("note", _unquote(children[0]))

    # -- 动作 ---------------------------------------------------------------
    def param(self, children: Sequence[Any]) -> ast.Param:
        name_tok = children[0]
        return ast.Param(name=str(name_tok), type=children[1], line=name_tok.line)

    def action_params(self, children: Sequence[Any]) -> list[ast.Param]:
        return list(children)

    def action_decl(self, children: Sequence[Any]) -> ast.ActionDecl:
        name_tok = children[0]
        idx = 1
        params: tuple[ast.Param, ...] = ()
        if idx < len(children) and isinstance(children[idx], list):
            params = tuple(children[idx])
            idx += 1
        pre = post = None
        transition: Optional[ast.EnumRef] = None
        for child in children[idx:]:
            key, value = child
            if key == "pre":
                pre = value
            elif key == "post":
                post = value
            elif key == "transition":
                transition = value
        return ast.ActionDecl(
            name=str(name_tok),
            params=params,
            pre=pre,
            post=post,
            transition=transition,
            line=name_tok.line,
        )

    def act_pre(self, children: Sequence[Any]) -> tuple[str, ast.Expr]:
        return ("pre", children[0])

    def act_post(self, children: Sequence[Any]) -> tuple[str, ast.Expr]:
        return ("post", children[0])

    def act_transition(self, children: Sequence[Any]) -> tuple[str, ast.EnumRef]:
        return ("transition", children[0])

    # -- 跨域契约 -----------------------------------------------------------
    def contract_item(self, children: Sequence[Any]) -> Any:
        return children[0]

    def expose_decl(self, children: Sequence[Any]) -> tuple[str, tuple[str, ...]]:
        return ("expose", tuple(str(c) for c in children))

    def contract_decl(self, children: Sequence[Any]) -> ast.ContractDecl:
        name_tok = children[0]
        exposes: tuple[str, ...] = ()
        invariants: list[ast.InvariantDecl] = []
        desc = None
        for child in children[1:]:
            if isinstance(child, tuple) and child[0] == "__description__":
                desc = child[1]
            elif isinstance(child, tuple) and child[0] == "expose":
                exposes = child[1]
            elif isinstance(child, ast.InvariantDecl):
                invariants.append(child)
        return ast.ContractDecl(
            name=str(name_tok),
            description=desc,
            exposes=exposes,
            invariants=tuple(invariants),
            line=name_tok.line,
        )

    # -- 场景 ---------------------------------------------------------------
    def scenario_decl(self, children: Sequence[Any]) -> ast.ScenarioDecl:
        name_tok = children[0]
        context = intent = None
        given: tuple[tuple[str, ast.Expr], ...] = ()
        steps: list[ast.ScenarioStep] = []
        for child in children[1:]:
            key, value = child
            if key == "context":
                context = value
            elif key == "intent":
                intent = value
            elif key == "given":
                given = value
            elif key == "step":
                steps.append(value)
        return ast.ScenarioDecl(
            name=str(name_tok),
            context=context,
            intent=intent,
            given=given,
            steps=tuple(steps),
            line=name_tok.line,
        )

    def s_context(self, children: Sequence[Any]) -> tuple[str, str]:
        return ("context", str(children[0]))

    def s_intent(self, children: Sequence[Any]) -> tuple[str, str]:
        return ("intent", str(children[0]))

    def s_given(self, children: Sequence[Any]) -> tuple[str, tuple[Any, ...]]:
        return ("given", tuple(children))

    def s_when(self, children: Sequence[Any]) -> tuple[str, Any]:
        args = tuple(children[1]) if len(children) > 1 else ()
        return (
            "step",
            ast.WhenStep(action=str(children[0]), args=args, line=children[0].line),
        )

    def s_rejected(self, children: Sequence[Any]) -> tuple[str, Any]:
        args = tuple(children[1]) if len(children) > 1 else ()
        return (
            "step",
            ast.WhenStep(
                action=str(children[0]),
                args=args,
                rejected=True,
                line=children[0].line,
            ),
        )

    def s_then(self, children: Sequence[Any]) -> tuple[str, Any]:
        return ("step", ast.ThenStep(expr=children[0], line=_line_of(children[0])))

    def field_assign(self, children: Sequence[Any]) -> tuple[str, ast.Expr]:
        return (str(children[0]), children[1])

    def field_target(self, children: Sequence[Any]) -> str:
        return ".".join(str(c) for c in children)

    def named_arg(self, children: Sequence[Any]) -> tuple[str, ast.Expr]:
        return (str(children[0]), children[1])

    def named_args(self, children: Sequence[Any]) -> list[Any]:
        return list(children)

    # -- 表达式 -------------------------------------------------------------
    def enum_ref(self, children: Sequence[Any]) -> ast.EnumRef:
        return ast.EnumRef(enum=str(children[0]), member=str(children[1]))

    def var(self, children: Sequence[Any]) -> ast.VarRef:
        tok = children[0]
        return ast.VarRef(name=str(tok), line=tok.line)

    def field_path(self, children: Sequence[Any]) -> list[str]:
        return [str(c) for c in children]

    def field_access(self, children: Sequence[Any]) -> ast.FieldAccess:
        tok = children[0]
        return ast.FieldAccess(path=(str(tok), *children[1]), line=tok.line)

    def call(self, children: Sequence[Any]) -> ast.CallExpr:
        tok = children[0]
        args = tuple(children[1]) if len(children) > 1 else ()
        return ast.CallExpr(name=str(tok), args=args, line=tok.line)

    def number(self, children: Sequence[Any]) -> ast.NumberLit:
        tok = children[0]
        return ast.NumberLit(value=str(tok), line=tok.line)

    def string(self, children: Sequence[Any]) -> ast.StringLit:
        return ast.StringLit(value=_unquote(children[0]))

    def bool_lit(self, children: Sequence[Any]) -> ast.BoolLit:
        return ast.BoolLit(value=str(children[0]) == "true")

    def neg(self, children: Sequence[Any]) -> ast.UnaryExpr:
        return ast.UnaryExpr(op="-", operand=children[0], line=_line_of(children[0]))

    def old(self, children: Sequence[Any]) -> ast.OldExpr:
        return ast.OldExpr(inner=children[0], line=_line_of(children[0]))

    def or_op(self, children: Sequence[Any]) -> ast.BinaryExpr:
        return _binary("||", children)

    def and_op(self, children: Sequence[Any]) -> ast.BinaryExpr:
        return _binary("&&", children)

    def compare(self, children: Sequence[Any]) -> ast.BinaryExpr:
        return _binary(str(children[1]), [children[0], children[2]])

    def add(self, children: Sequence[Any]) -> ast.BinaryExpr:
        return _binary("+", children)

    def sub(self, children: Sequence[Any]) -> ast.BinaryExpr:
        return _binary("-", children)

    def mul(self, children: Sequence[Any]) -> ast.BinaryExpr:
        return _binary("*", children)

    def div(self, children: Sequence[Any]) -> ast.BinaryExpr:
        return _binary("/", children)


def _binary(op: str, children: Sequence[Any]) -> ast.BinaryExpr:
    left, right = children[0], children[1]
    return ast.BinaryExpr(op=op, left=left, right=right, line=_line_of(left))


def _line_of(node: ast.Expr) -> int:
    return getattr(node, "line", 0) or 0


def _unquote(token: Token) -> str:
    raw = str(token)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw[1:-1] if len(raw) >= 2 else raw


_parser: Optional[Lark] = None


def get_parser() -> Lark:
    """返回单例 Lark 解析器（延迟构建，避免导入时开销）。"""

    global _parser
    if _parser is None:
        _parser = Lark(
            _GRAMMAR_PATH.read_text(encoding="utf-8"),
            parser="lalr",
            start="start",
            propagate_positions=False,
            maybe_placeholders=False,
        )
    return _parser


def parse(source: str, filename: str = "<string>") -> ast.Model:
    """解析 IFP DSL 源码，返回 :class:`ast.Model`。"""

    try:
        tree: Tree = get_parser().parse(source)
    except UnexpectedInput as exc:  # 语法 / 词法错误，携带出错行列
        line = getattr(exc, "line", 0) or 0
        column = getattr(exc, "column", 0) or 0
        raise IFPParseError(filename, str(exc), line=line, column=column) from exc
    except LarkError as exc:  # 其他 Lark 层错误
        raise IFPParseError(filename, str(exc)) from exc
    try:
        model: ast.Model = _ToAst().transform(tree)
    except Exception as exc:  # AST 转换过程中出现的非预期异常，仍归为解析失败
        raise IFPParseError(filename, f"AST 转换失败：{exc}") from exc
    return ast.Model(
        contexts=model.contexts, scenarios=model.scenarios, source=filename
    )


def load(path: str | Path) -> ast.Model:
    """从文件加载并解析 `.ifp` 模型。"""

    path = Path(path)
    return parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def merge(models: Sequence[ast.Model], source: str = "<project>") -> ast.Model:
    """把多个 `.ifp` 文件合并为一个模型视图。

    用于让独立存放的 Scenario 文件能够解析到 Context / Intent / 状态机。
    """

    contexts: list[ast.ContextDecl] = []
    scenarios: list[ast.ScenarioDecl] = []
    for model in models:
        contexts.extend(model.contexts)
        scenarios.extend(model.scenarios)
    return ast.Model(contexts=tuple(contexts), scenarios=tuple(scenarios), source=source)


def load_project(paths: Sequence[str | Path]) -> ast.Model:
    """加载并合并多个模型文件 / 目录。"""

    files: list[Path] = []
    for path in paths:
        p = Path(path)
        files.extend(sorted(p.rglob("*.ifp")) if p.is_dir() else [p])
    models = [load(f) for f in files]
    return merge(models, source=", ".join(str(f) for f in files))
