"""模型度量指标：规则覆盖率、死不变量数量、基线数量等。

对应「模型仓库与语义合并服务」的验收标准：为模型仓库与语义合并
提供可统计、可展示的度量指标，用于评估模型规则完备度与动作约束完备度。

术语说明：
- 规则覆盖率（rule_coverage）：有多少 Intent / 值对象真正定义了不变量；
- 动作覆盖率（action_coverage）：有多少动作同时具备 ``pre`` 与 ``post``；
- 基线数量（baseline_invariants）：标记为 Baseline 的不变量数量；
- 死不变量（dead_invariants）：``expr`` 缺失，或所引用字段从未被任何动作修改。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from . import ast


@dataclass(frozen=True)
class IntentMetrics:
    """单个 Intent 的度量指标。"""

    context: str
    intent: str
    invariants: int
    baseline_invariants: int
    dead_invariants: tuple[str, ...]
    actions: int
    actions_with_pre: int
    actions_with_post: int


@dataclass(frozen=True)
class ModelMetrics:
    """整个模型的度量指标。"""

    contexts: int
    intents: int
    value_objects: int
    invariants: int
    baseline_invariants: int
    dead_invariants: int
    actions: int
    contracts: int
    scenarios: int
    rule_coverage: float
    action_coverage: float
    per_intent: tuple[IntentMetrics, ...]

    def as_dict(self) -> dict[str, Any]:
        """返回可 JSON 序列化的字典（``per_intent`` 为 ``list[dict]``）。"""

        data: dict[str, Any] = asdict(self)
        data["per_intent"] = [asdict(item) for item in self.per_intent]
        return data

    def summary_lines(self) -> list[str]:
        """返回中文摘要文本行（不带 emoji），便于 CLI / 报表展示。"""

        actions_with_post = sum(item.actions_with_post for item in self.per_intent)
        return [
            "模型度量：",
            f"- 限界上下文 {self.contexts} · Intent {self.intents} · 值对象 {self.value_objects}",
            f"- 不变量 {self.invariants}（基线 {self.baseline_invariants}）"
            f" · 死不变量 {self.dead_invariants}",
            f"- 动作 {self.actions}（pre/post 齐备 {actions_with_post}）",
            f"- 规则覆盖率 {self.rule_coverage:.1%} · 动作覆盖率 {self.action_coverage:.1%}",
        ]


def referenced_fields(expr: ast.Expr | None) -> set[str]:
    """收集表达式引用的字段根名集合。

    遍历表达式树，收集 :class:`ast.VarRef` 的 ``name`` 与
    :class:`ast.FieldAccess` 的 ``path[0]``（即字段根名）。
    递归处理 :class:`ast.BinaryExpr`（left/right）、
    :class:`ast.UnaryExpr`（operand）、:class:`ast.OldExpr`（inner）与
    :class:`ast.CallExpr`（args）；``expr`` 为 ``None`` 时返回空集。
    """

    if expr is None:
        return set()
    if isinstance(expr, ast.VarRef):
        return {expr.name}
    if isinstance(expr, ast.FieldAccess):
        return {expr.root}
    if isinstance(expr, ast.BinaryExpr):
        return referenced_fields(expr.left) | referenced_fields(expr.right)
    if isinstance(expr, ast.UnaryExpr):
        return referenced_fields(expr.operand)
    if isinstance(expr, ast.OldExpr):
        return referenced_fields(expr.inner)
    if isinstance(expr, ast.CallExpr):
        fields: set[str] = set()
        for arg in expr.args:
            fields |= referenced_fields(arg)
        return fields
    return set()


def _modified_fields(intent: ast.IntentDecl) -> set[str]:
    """收集该 Intent 内所有动作修改的字段根名集合。

    - 动作 ``post`` 中出现的字段名，视为被该动作修改的根字段；
    - 该 Intent 中类型为 ``Named``（``TypeRef.kind == "Named"``）且类型名
      等于任一动作 ``transition.enum`` 的字段名（状态机流转修改状态字段）。
    """

    modified: set[str] = set()
    for action in intent.actions:
        modified |= referenced_fields(action.post)
        if action.transition is not None:
            for decl in intent.fields:
                if decl.type.kind == "Named" and decl.type.name == action.transition.enum:
                    modified.add(decl.name)
    return modified


def dead_invariants(intent: ast.IntentDecl) -> tuple[ast.InvariantDecl, ...]:
    """返回某 Intent 中的死不变量。

    死不变量定义：不变量 ``expr`` 为 ``None``，或
    ``referenced_fields(expr)`` 与该 Intent 内所有动作修改的字段集合
    （见 :func:`_modified_fields`）交集为空，即所引用字段从未被任何动作修改。
    """

    modified = _modified_fields(intent)
    dead: list[ast.InvariantDecl] = []
    for inv in intent.invariants:
        if inv.expr is None or not (referenced_fields(inv.expr) & modified):
            dead.append(inv)
    return tuple(dead)


def collect(model: ast.Model) -> ModelMetrics:
    """统计整个模型的度量指标。

    - ``invariants`` / ``baseline_invariants`` / ``dead_invariants`` 覆盖
      Intent 与值对象内定义的不变量；值对象无动作，故其不变量一律计入死不变量；
    - ``rule_coverage`` = 至少含 1 条不变量的 Intent 数与值对象数之和
      /（Intent 总数 + 值对象总数）；
    - ``action_coverage`` = 同时定义 ``pre`` 与 ``post`` 的动作数 / 全部动作数；
    - ``per_intent`` 按 ``(context 名, intent 名)`` 排序。
    """

    contexts = len(model.contexts)
    intents = 0
    value_objects = 0
    invariants = 0
    baseline_invariants = 0
    dead_invariants_total = 0
    actions_total = 0
    contracts = 0
    intents_with_invariant = 0
    values_with_invariant = 0
    actions_with_pre_total = 0
    actions_with_post_total = 0
    per_intent: list[IntentMetrics] = []

    for ctx in model.contexts:
        contracts += len(ctx.contracts)
        for value in ctx.values:
            value_objects += 1
            value_invariants = len(value.invariants)
            invariants += value_invariants
            baseline_invariants += sum(1 for inv in value.invariants if inv.baseline)
            # 值对象无动作，其不变量一律计入死不变量
            dead_invariants_total += value_invariants
            if value_invariants:
                values_with_invariant += 1
        for intent in ctx.intents:
            intents += 1
            intent_invariants = len(intent.invariants)
            invariants += intent_invariants
            baseline_invariants += sum(1 for inv in intent.invariants if inv.baseline)
            if intent_invariants:
                intents_with_invariant += 1
            count = len(intent.actions)
            actions_total += count
            with_pre = sum(1 for a in intent.actions if a.pre is not None)
            with_both = sum(
                1 for a in intent.actions if a.pre is not None and a.post is not None
            )
            actions_with_pre_total += with_pre
            actions_with_post_total += with_both
            dead = dead_invariants(intent)
            dead_invariants_total += len(dead)
            per_intent.append(
                IntentMetrics(
                    context=ctx.name,
                    intent=intent.name,
                    invariants=intent_invariants,
                    baseline_invariants=sum(
                        1 for inv in intent.invariants if inv.baseline
                    ),
                    dead_invariants=tuple(inv.name for inv in dead),
                    actions=count,
                    actions_with_pre=with_pre,
                    actions_with_post=with_both,
                )
            )

    scenarios = len(model.scenarios)
    rule_denominator = intents + value_objects
    rule_coverage = (
        (intents_with_invariant + values_with_invariant) / rule_denominator
        if rule_denominator
        else 0.0
    )
    action_coverage = actions_with_post_total / actions_total if actions_total else 0.0
    per_intent.sort(key=lambda m: (m.context, m.intent))

    return ModelMetrics(
        contexts=contexts,
        intents=intents,
        value_objects=value_objects,
        invariants=invariants,
        baseline_invariants=baseline_invariants,
        dead_invariants=dead_invariants_total,
        actions=actions_total,
        contracts=contracts,
        scenarios=scenarios,
        rule_coverage=rule_coverage,
        action_coverage=action_coverage,
        per_intent=tuple(per_intent),
    )
