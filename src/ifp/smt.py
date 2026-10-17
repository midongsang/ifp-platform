"""SMT 求解验证：把 IFP 模型编码为一阶约束，搜索违反不变量的反例。

实现要点（对应《IFP 建模规范》第 5.4 / 9 节）：
- 字段编码：Decimal/Int -> Real（Int 额外加整数约束），Bool -> Bool，
  状态机 -> z3 EnumSort；资金一律按实数精确编码，避免浮点误差。
- 值对象字段：内联展开为底层叶子字段（形如 ``amount.amount``），
  值对象自身的不变量经前缀改写后对聚合生效（无需先手工展开）。
- 动作编码：``pre`` 在当前状态求值；``post`` 中普通字段引用表示“新状态”，
  ``old(x)`` 表示“旧状态”；``post`` 未提及的字段自动施加 frame（保持不变）。
- 属性测试：从 Intent 初始状态出发（`initial` 声明 + 字段默认值），应用若干动作
  组成的操作序列；每一步进入状态均假定合法，断言末次动作执行后目标不变量不成立；
  按步数由短到长搜索，若 SAT 则得到反例（最短路径优先）。
- 多步路径：动作序列长度上限由 ``max_steps`` / ``mode`` 决定（见 ROADMAP）。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

import z3

from . import ast
from .errors import Diagnostic, Severity
from .render import flatten_and

RULE_CONTRADICTION = "IFP3001"
ACTION_UNREACHABLE = "IFP3002"
SMT_UNSUPPORTED = "IFP3003"
SMT_TIMEOUT = "IFP3010"

# 单步 / 轻量 / 全量：mode 到最大步数的缺省映射（可由 max_steps 显式覆盖）。
_MODE_STEPS = {"single": 1, "one": 1, "one-step": 1, "light": 2, "full": 3, "deep": 3}

# z3 的 EnumSort 名称在同一个 context 内必须唯一；每次编码分配一个新的后缀。
_ENUM_SORT_SEQ = itertools.count()
# 多步搜索中不同步骤的动作实参必须互不相同，同样用递增后缀区分。
_PARAM_SEQ = itertools.count()


class UnsupportedForSMT(Exception):
    """模型中出现了暂不支持 SMT 编码的结构。"""


def _kv(items: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in items.items())


@dataclass(frozen=True)
class PathStep:
    """多步反例中的单步：动作名、实参、执行前后状态。"""

    action: str
    args: dict[str, str] = field(default_factory=dict)
    before: dict[str, str] = field(default_factory=dict)
    after: dict[str, str] = field(default_factory=dict)

    def describe(self, index: int) -> str:
        args = _kv(self.args)
        call = f"{self.action}({args})" if args else f"{self.action}()"
        return (
            f"    步骤 {index}: {call}\n"
            f"        动作前状态: {_kv(self.before)}\n"
            f"        动作后状态: {_kv(self.after)}"
        )


@dataclass(frozen=True)
class Counterexample:
    """反例：一组合法前置状态 + 一个（或多个）动作，导致目标不变量被打破。"""

    intent: str
    action: str
    invariant: str
    baseline: bool
    before: dict[str, str]
    after: dict[str, str]
    args: dict[str, str] = field(default_factory=dict)
    path: tuple[PathStep, ...] = ()

    @property
    def steps(self) -> int:
        return len(self.path) if self.path else 1

    def describe(self) -> str:
        tag = " [BASELINE]" if self.baseline else ""
        if len(self.path) > 1:
            body = "\n".join(step.describe(i + 1) for i, step in enumerate(self.path))
            return (
                f"不变量 {self.invariant}{tag} 可被多步操作序列打破：\n"
                f"{body}\n"
                f"    => 第 {len(self.path)} 步执行后不变量 {self.invariant} 不成立"
            )
        args = _kv(self.args)
        call = f"{self.action}({args})" if args else f"{self.action}()"
        return (
            f"不变量 {self.invariant}{tag} 可被打破：\n"
            f"    动作前状态: {_kv(self.before)}\n"
            f"    执行动作: {call}\n"
            f"    动作后状态: {_kv(self.after)}\n"
            f"    => 动作执行后不变量 {self.invariant} 不成立"
        )


@dataclass
class VerifyConfig:
    """属性测试配置（可由 property_test.yaml 覆盖）。"""

    lower: int = 0
    upper: int = 100_000
    timeout_ms: int = 5_000
    invariants: tuple[str, ...] = ()
    mode: str = "light"
    max_steps: Optional[int] = None

    def step_limit(self) -> int:
        """解析本次搜索允许的最大动作步数。"""

        if self.max_steps is not None:
            return max(1, self.max_steps)
        return _MODE_STEPS.get((self.mode or "light").lower(), 2)


@dataclass
class VerifyReport:
    intent: str = ""
    counterexamples: list[Counterexample] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    actions_checked: int = 0
    invariants_checked: int = 0
    timed_out: bool = False

    @property
    def max_steps_searched(self) -> int:
        return max((ce.steps for ce in self.counterexamples), default=0)


@dataclass(frozen=True)
class _EffectiveInvariant:
    """参与求解的不变量：既有 Intent 自身的，也有值对象内联展开得到的。"""

    name: str
    expr: ast.Expr
    baseline: bool
    line: int


# ---------------------------------------------------------------------------
# 编码器
# ---------------------------------------------------------------------------

@dataclass
class _StepVars:
    """多步搜索中某一步的状态变量与动作实参。"""

    cur: dict[str, z3.ExprRef] = field(default_factory=dict)
    nxt: dict[str, z3.ExprRef] = field(default_factory=dict)
    params: dict[str, z3.ExprRef] = field(default_factory=dict)
    param_kind: dict[str, str] = field(default_factory=dict)


class _Encoder:
    """把单个 Intent 编码为 z3 表达式（支持值对象内联与多步展开）。"""

    def __init__(
        self,
        intent: ast.IntentDecl,
        value_objects: Optional[dict[str, ast.ValueDecl]] = None,
    ) -> None:
        self.intent = intent
        self.value_objects: dict[str, ast.ValueDecl] = value_objects or {}
        self.state_machines: dict[str, tuple[str, ...]] = {
            s.name: s.values for s in intent.states
        }
        self.enum_consts: dict[tuple[str, str], z3.ExprRef] = {}
        self.kind: dict[str, str] = {}
        self.ignored: set[str] = set()
        self.vo_fields: dict[str, str] = {}

        for name, values in self.state_machines.items():
            sort_name = f"{name}#{next(_ENUM_SORT_SEQ)}"
            _sort, consts = z3.EnumSort(sort_name, list(values))
            for value, const in zip(values, consts, strict=True):
                self.enum_consts[(name, value)] = const

        self._build_fields(intent.fields, prefix="", vo_stack=())

    # -- 字段展开 ----------------------------------------------------------

    def _build_fields(
        self,
        fields: tuple[ast.FieldDecl, ...],
        prefix: str,
        vo_stack: tuple[str, ...],
    ) -> None:
        """递归展开字段；值对象按其成员字段内联为叶子键。"""

        for f in fields:
            t = f.type
            key = f"{prefix}{f.name}"
            if t.kind == "Decimal":
                self.kind[key] = "Real"
            elif t.kind == "Int":
                self.kind[key] = "Int"
            elif t.kind == "Bool":
                self.kind[key] = "Bool"
            elif t.kind == "String":
                if prefix:
                    # 值对象里的 String 成员（如 Money.currency）不参与数值求解
                    self.ignored.add(key)
                    continue
                raise UnsupportedForSMT(f"String 字段 {f.name} 暂不支持 SMT 编码")
            elif t.kind == "Named" and t.name in self.state_machines:
                self.kind[key] = f"Enum:{t.name}"
            elif t.kind == "Named" and t.name in self.value_objects:
                if t.name in vo_stack:
                    raise UnsupportedForSMT(
                        f"值对象 {t.name} 存在循环引用，无法内联展开（字段 {key}）"
                    )
                vo = self.value_objects[t.name]
                self.vo_fields[key] = t.name
                self._build_fields(vo.fields, prefix=key + ".", vo_stack=(*vo_stack, t.name))
                continue
            else:
                raise UnsupportedForSMT(
                    f"字段 {f.name} 的类型 {t} 暂不支持 SMT 编码"
                )

    def _declare(self, key: str, var_name: str) -> z3.ExprRef:
        kind = self.kind[key]
        if kind in {"Real", "Int"}:
            return z3.Real(var_name)
        if kind == "Bool":
            return z3.Bool(var_name)
        enum_name = kind.split(":", 1)[1]
        sort = self.enum_consts[(enum_name, self.state_machines[enum_name][0])].sort()
        return z3.Const(var_name, sort)

    # -- 步骤与参数 --------------------------------------------------------

    def new_step(self, index: int) -> _StepVars:
        step = _StepVars()
        for key in self.kind:
            step.cur[key] = self._declare(key, f"cur@{index}:{key}")
            step.nxt[key] = self._declare(key, f"next@{index}:{key}")
        return step

    def declare_action_params(self, step: _StepVars, action: ast.ActionDecl) -> None:
        step.params = {}
        step.param_kind = {}
        suffix = next(_PARAM_SEQ)
        for p in action.params:
            var_name = f"param#{suffix}:{p.name}"
            if p.type.kind in {"Decimal", "Int"}:
                step.params[p.name] = z3.Real(var_name)
                step.param_kind[p.name] = p.type.kind
            elif p.type.kind == "Bool":
                step.params[p.name] = z3.Bool(var_name)
                step.param_kind[p.name] = "Bool"
            elif p.type.kind == "Named" and p.type.name in self.state_machines:
                sort = self.enum_consts[
                    (p.type.name, self.state_machines[p.type.name][0])
                ].sort()
                step.params[p.name] = z3.Const(var_name, sort)
                step.param_kind[p.name] = f"Enum:{p.type.name}"
            elif p.type.kind == "Named" and p.type.name in self.value_objects:
                raise UnsupportedForSMT(
                    f"动作参数 {p.name} 的类型是值对象 {p.type.name}，暂不支持 SMT 编码"
                )
            else:
                raise UnsupportedForSMT(f"动作参数 {p.name} 的类型暂不支持 SMT 编码")

    def action_constraints(
        self,
        cfg: VerifyConfig,
        step: _StepVars,
        action: ast.ActionDecl,
    ) -> tuple[list[z3.BoolRef], dict[str, z3.ExprRef], dict[str, str]]:
        """编码单个动作在某一步的 pre / post / frame / 参数约束。

        返回 ``(constraints, params, param_kind)``；``step.params`` 与
        ``step.param_kind`` 在返回前恢复原状，调用方无需管理。供符号化合并搜索复用：
        每个动作的约束由动作选择器 guard，避免对动作序列做笛卡尔积枚举。
        """

        saved_p, saved_k = step.params, step.param_kind
        self.declare_action_params(step, action)
        params = dict(step.params)
        kind = dict(step.param_kind)
        cons: list[z3.BoolRef] = []
        cons.extend(self.param_constraints(cfg, step))
        cons.extend(self.frame_constraints(action, step))
        if action.pre is not None:
            cons.append(self.encode(action.pre, "pre", step))
        if action.post is not None:
            cons.append(self.encode(action.post, "post", step))
        step.params, step.param_kind = saved_p, saved_k
        return cons, params, kind

    def param_constraints(self, cfg: VerifyConfig, step: _StepVars) -> list[z3.BoolRef]:
        cons: list[z3.BoolRef] = []
        for name, kind in step.param_kind.items():
            var = step.params[name]
            if kind in {"Real", "Int"}:
                cons.append(var >= cfg.lower)
                cons.append(var <= cfg.upper)
                if kind == "Int":
                    cons.append(z3.ToInt(var) == var)
        return cons

    # -- 表达式 ------------------------------------------------------------

    def encode(self, expr: ast.Expr, phase: str, step: _StepVars) -> z3.ExprRef:
        if isinstance(expr, ast.NumberLit):
            return _real_lit(expr.value)
        if isinstance(expr, ast.BoolLit):
            return z3.BoolVal(expr.value)
        if isinstance(expr, ast.StringLit):
            raise UnsupportedForSMT("字符串字面量暂不支持 SMT 编码")
        if isinstance(expr, ast.VarRef):
            if expr.name in step.params:
                return step.params[expr.name]
            table = step.cur if phase == "pre" else step.nxt
            if expr.name not in table:
                if expr.name in self.ignored:
                    raise UnsupportedForSMT(
                        f"字段 {expr.name} 类型为 String，无法参与 SMT 编码"
                    )
                raise UnsupportedForSMT(f"未知字段 {expr.name}")
            return table[expr.name]
        if isinstance(expr, ast.FieldAccess):
            # 值对象成员已内联展开为叶子键 ``balance.amount``
            key = expr.dotted
            table = step.cur if phase == "pre" else step.nxt
            if key not in table:
                if key in self.ignored:
                    raise UnsupportedForSMT(
                        f"字段 {key} 类型为 String，无法参与 SMT 编码"
                    )
                raise UnsupportedForSMT(f"未知字段 {key}")
            return table[key]
        if isinstance(expr, ast.EnumRef):
            key = (expr.enum, expr.member)
            if key not in self.enum_consts:
                raise UnsupportedForSMT(f"未知状态 {expr.enum}::{expr.member}")
            return self.enum_consts[key]
        if isinstance(expr, ast.OldExpr):
            return self.encode(expr.inner, "pre", step)
        if isinstance(expr, ast.UnaryExpr):
            return -self.encode(expr.operand, phase, step)
        if isinstance(expr, ast.CallExpr):
            raise UnsupportedForSMT("表达式内不允许函数调用")
        if isinstance(expr, ast.BinaryExpr):
            return self._encode_binary(expr, phase, step)
        raise UnsupportedForSMT(f"无法编码的表达式：{expr!r}")

    def _encode_binary(
        self, expr: ast.BinaryExpr, phase: str, step: _StepVars
    ) -> z3.ExprRef:
        left = self.encode(expr.left, phase, step)
        right = self.encode(expr.right, phase, step)
        op = expr.op
        table = {
            "&&": z3.And, "||": z3.Or,
            "+": lambda a, b: a + b, "-": lambda a, b: a - b,
            "*": lambda a, b: a * b, "/": lambda a, b: a / b,
            "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
            "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
            ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        }
        if op not in table:
            raise UnsupportedForSMT(f"不支持的运算符 {op}")
        return table[op](left, right)

    # -- 约束 --------------------------------------------------------------

    def base_constraints(self, cfg: VerifyConfig, step: _StepVars) -> list[z3.BoolRef]:
        cons: list[z3.BoolRef] = []
        for key, kind in self.kind.items():
            if kind in {"Real", "Int"}:
                for var in (step.cur[key], step.nxt[key]):
                    cons.append(var >= cfg.lower)
                    cons.append(var <= cfg.upper)
                if kind == "Int":
                    cons.append(z3.ToInt(step.cur[key]) == step.cur[key])
                    cons.append(z3.ToInt(step.nxt[key]) == step.nxt[key])
        return cons

    def frame_constraints(
        self, action: ast.ActionDecl, step: _StepVars
    ) -> list[z3.BoolRef]:
        assigned = _assigned_fields(action.post) if action.post is not None else set()
        # 被赋值的叶子键保持不变，其余字段自动施加 frame
        return [
            step.nxt[key] == step.cur[key]
            for key in self.kind
            if key not in assigned
        ]

    def link_constraints(self, prev: _StepVars, nxt: _StepVars) -> list[z3.BoolRef]:
        """把前一步的后态与后一步的前态连接起来。"""

        return [nxt.cur[key] == prev.nxt[key] for key in self.kind]

    def initial_constraints(self, step: _StepVars) -> list[z3.BoolRef]:
        """把操作序列的起点约束到 Intent 的初始状态。

        起点由 ``initial`` 声明（状态机字段）与各字段默认值共同确定；未声明默认值的
        字段保持自由（受上下界与不变量约束）。这一步把搜索从“任意合法状态”改为
        “从初始状态出发的可达路径”，多步路径反例才有意义。
        """

        cons: list[z3.BoolRef] = []
        state_field: Optional[str] = None
        if self.intent.initial is not None:
            enum_name = self.intent.initial.enum
            state_field = next(
                (
                    f.name
                    for f in self.intent.fields
                    if f.type.kind == "Named"
                    and f.type.name == enum_name
                    and f.name in self.kind
                ),
                None,
            )
            if state_field is not None:
                cons.append(
                    step.cur[state_field] == self.encode(self.intent.initial, "pre", step)
                )
        for f in self.intent.fields:
            if f.default is None or f.name == state_field or f.name not in self.kind:
                continue
            try:
                cons.append(step.cur[f.name] == self.encode(f.default, "pre", step))
            except UnsupportedForSMT:
                continue
        return cons

    def value_object_invariants(self) -> list[_EffectiveInvariant]:
        """把每个值对象字段实例的不变量前缀改写为聚合内可求解的形式。"""

        out: list[_EffectiveInvariant] = []
        for key, vo_name in self.vo_fields.items():
            vo = self.value_objects[vo_name]
            members = {f.name for f in vo.fields}
            for inv in vo.invariants:
                if inv.expr is None:
                    continue
                out.append(
                    _EffectiveInvariant(
                        name=f"{key}.{inv.name}",
                        expr=_prefix_vars(inv.expr, key + ".", members),
                        baseline=inv.baseline,
                        line=inv.line,
                    )
                )
        return out


def _prefix_vars(expr: ast.Expr, prefix: str, members: set[str]) -> ast.Expr:
    """把值对象不变量中的成员引用改写为 ``<字段>.<成员>``。"""

    if isinstance(expr, ast.VarRef):
        if expr.name in members:
            return ast.VarRef(name=prefix + expr.name, line=expr.line)
        return expr
    if isinstance(expr, ast.BinaryExpr):
        return ast.BinaryExpr(
            op=expr.op,
            left=_prefix_vars(expr.left, prefix, members),
            right=_prefix_vars(expr.right, prefix, members),
            line=expr.line,
        )
    if isinstance(expr, ast.UnaryExpr):
        return ast.UnaryExpr(
            op=expr.op, operand=_prefix_vars(expr.operand, prefix, members), line=expr.line
        )
    if isinstance(expr, ast.OldExpr):
        return ast.OldExpr(inner=_prefix_vars(expr.inner, prefix, members), line=expr.line)
    return expr


def _assigned_fields(post: ast.Expr) -> set[str]:
    """从 post 的顶层合取中提取被赋值的字段键（形如 `field == expr` / `vo.member == expr`）。"""

    names: set[str] = set()
    for conjunct in flatten_and(post):
        if not (isinstance(conjunct, ast.BinaryExpr) and conjunct.op == "=="):
            continue
        if isinstance(conjunct.left, ast.VarRef):
            names.add(conjunct.left.name)
        elif isinstance(conjunct.left, ast.FieldAccess):
            names.add(conjunct.left.dotted)
    return names


def _real_lit(value: str) -> z3.ExprRef:
    frac = Fraction(value)
    return z3.RealVal(f"{frac.numerator}/{frac.denominator}")


def _effective_invariants(encoder: _Encoder) -> list[_EffectiveInvariant]:
    """Intent 自身的不变量 + 值对象内联展开得到的不变量。"""

    out = [
        _EffectiveInvariant(
            name=inv.name, expr=inv.expr, baseline=inv.baseline, line=inv.line
        )
        for inv in encoder.intent.invariants
        if inv.expr is not None
    ]
    out.extend(encoder.value_object_invariants())
    return out


def _value_objects(model: ast.Model) -> dict[str, ast.ValueDecl]:
    return {vo.name: vo for ctx in model.contexts for vo in ctx.values}


# ---------------------------------------------------------------------------
# 验证入口
# ---------------------------------------------------------------------------

def verify(
    model: ast.Model,
    intent_name: Optional[str] = None,
    cfg: Optional[VerifyConfig] = None,
) -> VerifyReport:
    """对模型执行属性测试，返回反例与诊断。"""

    cfg = cfg or VerifyConfig()
    report = VerifyReport()
    vos = _value_objects(model)
    for ctx in model.contexts:
        for intent in ctx.intents:
            if intent_name and intent.name != intent_name:
                continue
            report.intent = intent.name
            try:
                _verify_intent(intent, vos, cfg, report, model.source)
            except UnsupportedForSMT as exc:
                report.diagnostics.append(
                    Diagnostic(
                        SMT_UNSUPPORTED,
                        f"Intent {intent.name} 无法进行 SMT 验证：{exc}",
                        Severity.WARNING, model.source, intent.line,
                    )
                )
    return report


def _verify_intent(
    intent: ast.IntentDecl,
    value_objects: dict[str, ast.ValueDecl],
    cfg: VerifyConfig,
    report: VerifyReport,
    source: str,
) -> None:
    encoder = _Encoder(intent, value_objects)
    effective = _effective_invariants(encoder)
    if not effective or not intent.actions:
        return

    report.actions_checked += len(intent.actions)
    report.invariants_checked += len(effective)

    targets = [
        inv for inv in effective if not cfg.invariants or inv.name in cfg.invariants
    ]
    if not targets:
        return

    limit = cfg.step_limit()
    found: set[str] = set()

    actions = list(intent.actions)
    action_names = [a.name for a in actions]
    sel_sort, sel_consts = z3.EnumSort(f"Action#{next(_ENUM_SORT_SEQ)}", action_names)
    action_const = dict(zip(action_names, sel_consts, strict=True))

    # 由短到长搜索：先给出最小步数反例，步数不足时才扩展为多步。
    # 每一步长内用“动作选择器”把所有动作编码成 guard 后的蕴含式，单次求解即可
    # 判断是否存在破坏不变量的动作序列，避免对动作序列做笛卡尔积枚举（N^k）。
    for steps_count in range(1, limit + 1):
        remaining = [inv for inv in targets if inv.name not in found]
        if not remaining:
            break

        steps = [encoder.new_step(i) for i in range(steps_count)]
        selectors = [z3.Const(f"sel@{i}", sel_sort) for i in range(steps_count)]

        cons: list[z3.BoolRef] = []
        # 起点锚定到 Intent 初始状态：搜索“从初始状态出发、由若干动作组成的
        # 可达路径”。没有这条约束，多步搜索会退化为单步（末步破坏可由其前态
        # 单独复现），路径就没有意义。
        cons.extend(encoder.initial_constraints(steps[0]))

        # 每一步：每动作的约束由 sel == action guard；枚举变量天然保证恰选一。
        step_action_data: list[dict[str, tuple[list[z3.BoolRef], dict, dict]]] = []
        for index in range(steps_count):
            step = steps[index]
            sel = selectors[index]
            cons.extend(encoder.base_constraints(cfg, step))
            # 每一步的进入状态都必须合法（不变量成立）
            for inv in effective:
                cons.append(encoder.encode(inv.expr, "pre", step))

            data: dict[str, tuple[list[z3.BoolRef], dict, dict]] = {}
            one_of: list[z3.BoolRef] = []
            for action in actions:
                act_cons, params, kind = encoder.action_constraints(cfg, step, action)
                data[action.name] = (act_cons, params, kind)
                guard = sel == action_const[action.name]
                one_of.append(guard)
                if act_cons:
                    cons.append(z3.Implies(guard, z3.And(act_cons)))
            cons.append(z3.Or(*one_of))
            step_action_data.append(data)

            if index + 1 < steps_count:
                cons.extend(encoder.link_constraints(step, steps[index + 1]))

        last = steps[-1]
        inv_post = {inv.name: encoder.encode(inv.expr, "post", last) for inv in remaining}
        solver = z3.Solver()
        solver.set("timeout", cfg.timeout_ms)
        solver.add(*cons)
        # 末次动作之后，至少一个目标不变量不再成立
        solver.add(z3.Or(*[z3.Not(inv_post[inv.name]) for inv in remaining]))
        outcome = solver.check()
        if outcome == z3.unknown:
            report.timed_out = True
            for inv in remaining:
                report.diagnostics.append(
                    Diagnostic(
                        SMT_TIMEOUT,
                        f"属性测试求解超时（{cfg.timeout_ms}ms）：intent={intent.name} "
                        f"steps={steps_count} invariant={inv.name}",
                        Severity.WARNING, source, inv.line,
                    )
                )
            continue
        if outcome != z3.sat:
            continue

        model_ = solver.model()
        violated = [
            inv for inv in remaining
            if not z3.is_true(model_.eval(inv_post[inv.name], model_completion=True))
        ]
        if not violated:
            continue

        # 从选择器反推动作序列
        path: list[PathStep] = []
        for index in range(steps_count):
            action_name = str(model_.eval(selectors[index], model_completion=True))
            _, params, _ = step_action_data[index][action_name]
            path.append(
                PathStep(
                    action=action_name,
                    args=_snapshot(params, model_),
                    before=_snapshot(steps[index].cur, model_),
                    after=_snapshot(steps[index].nxt, model_),
                )
            )
        tail = path[-1]
        for inv in violated:
            if inv.name in found:
                continue
            found.add(inv.name)
            report.counterexamples.append(
                Counterexample(
                    intent=intent.name,
                    action=tail.action,
                    invariant=inv.name,
                    baseline=inv.baseline,
                    before=tail.before,
                    after=tail.after,
                    args=tail.args,
                    path=tuple(path),
                )
            )


def _snapshot(table: dict[str, z3.ExprRef], model_: z3.ModelRef) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, var in table.items():
        value = model_.eval(var, model_completion=True)
        out[name] = _format_value(value)
    return out


def _format_value(value: z3.ExprRef) -> str:
    if z3.is_true(value):
        return "true"
    if z3.is_false(value):
        return "false"
    try:
        return value.as_decimal(4).rstrip("?")
    except Exception:
        return str(value)


# ---------------------------------------------------------------------------
# 一致性检查：规则矛盾 / 动作不可触发
# ---------------------------------------------------------------------------

def check_consistency(
    model: ast.Model,
    intent_name: Optional[str] = None,
    cfg: Optional[VerifyConfig] = None,
) -> list[Diagnostic]:
    """检测“规则矛盾”（不变量集合不可满足）与“动作不可触发”。"""

    cfg = cfg or VerifyConfig()
    vos = _value_objects(model)
    diags: list[Diagnostic] = []
    for ctx in model.contexts:
        for intent in ctx.intents:
            if intent_name and intent.name != intent_name:
                continue
            try:
                diags.extend(_check_intent_consistency(intent, vos, cfg, model.source))
            except UnsupportedForSMT as exc:
                diags.append(
                    Diagnostic(
                        SMT_UNSUPPORTED, f"Intent {intent.name} 无法进行一致性检查：{exc}",
                        Severity.WARNING, model.source, intent.line,
                    )
                )
    return diags


def _check_intent_consistency(
    intent: ast.IntentDecl,
    value_objects: dict[str, ast.ValueDecl],
    cfg: VerifyConfig,
    source: str,
) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    encoder = _Encoder(intent, value_objects)
    step = encoder.new_step(0)
    base = encoder.base_constraints(cfg, step)
    invs = [
        encoder.encode(inv.expr, "pre", step) for inv in _effective_invariants(encoder)
    ]

    if invs:
        solver = z3.Solver()
        solver.set("timeout", cfg.timeout_ms)
        solver.add(*base)
        solver.add(*invs)
        if solver.check() == z3.unsat:
            diags.append(
                Diagnostic(
                    RULE_CONTRADICTION,
                    f"Intent {intent.name} 的不变量集合互相矛盾，不存在任何合法状态",
                    Severity.ERROR, source, intent.line,
                )
            )

    for action in intent.actions:
        if action.pre is None:
            continue
        encoder.declare_action_params(step, action)
        solver = z3.Solver()
        solver.set("timeout", cfg.timeout_ms)
        solver.add(*base)
        solver.add(*invs)
        solver.add(encoder.encode(action.pre, "pre", step))
        if solver.check() == z3.unsat:
            diags.append(
                Diagnostic(
                    ACTION_UNREACHABLE,
                    f"动作 {action.name} 的前置条件与不变量冲突，永远不会被触发",
                    Severity.ERROR, source, action.line,
                )
            )
    return diags


def render_counterexample(ce: Counterexample) -> str:
    """把反例渲染为可读文本（供 CLI / 评审记录使用）。"""

    return ce.describe()
