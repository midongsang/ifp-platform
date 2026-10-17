"""语义合并（Semantic Merge）：基于业务语义对模型分支做三方合并。

对应「模型仓库与语义合并服务」的验收标准 2 / 3：冲突基于**业务规则**（不变量、状态机、契约、
字段、动作）判定并能定位到具体规则；无法自动消解时要求人工评审并留痕。

与文本三方合并的区别：这里先把模型按业务规则拆成「规则单元」（限界上下文、
值对象、Intent、字段、状态机、不变量、动作、契约、场景），再对每个单元做三方
比较（base / ours / theirs），**只有同一规则被双方以不同方式修改时才判为冲突**，
其余变更各自自动采纳。

说明：比较前会消除 AST 节点的行号（``line``）与容器节点的子节点，避免因源码
排版差异产生伪冲突；因此合并结果中的规则按名称排序，且行号统一为 0。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Optional

from . import ast, parser
from .audit import AuditEntry, AuditLog
from .render import render_expr

#: 规则单元类型（用于从规则路径推导冲突类型）。
_RULE_KINDS = (
    "field",
    "state",
    "invariant",
    "action",
    "value",
    "intent",
    "contract",
    "context",
    "scenario",
)


@dataclass(frozen=True)
class SemanticConflict:
    """一条语义冲突：同一业务规则被双方以不同方式修改。"""

    path: str
    kind: str
    ours: str
    theirs: str
    detail: str = ""

    def format(self) -> str:
        return f"! {self.path} [{self.kind}]: {self.detail}"


@dataclass
class MergeResult:
    """三方合并结果。``merged`` 仅在无冲突时给出。"""

    conflicts: list[SemanticConflict] = field(default_factory=list)
    ours_changes: list[str] = field(default_factory=list)
    theirs_changes: list[str] = field(default_factory=list)
    merged: Optional[ast.Model] = None

    @property
    def clean(self) -> bool:
        """是否无冲突（可自动合并）。"""

        return not self.conflicts

    @property
    def requires_manual_review(self) -> bool:
        """是否必须人工评审（存在无法自动消解的冲突）。"""

        return bool(self.conflicts)


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def merge_models(base: ast.Model, ours: ast.Model, theirs: ast.Model) -> MergeResult:
    """对三个版本的模型做语义三方合并。"""

    base_units = _flatten(base)
    ours_units = _flatten(ours)
    theirs_units = _flatten(theirs)

    result = MergeResult()
    merged_units: dict[str, Any] = {}

    for key in sorted(set(base_units) | set(ours_units) | set(theirs_units)):
        before = base_units.get(key)
        mine = ours_units.get(key)
        other = theirs_units.get(key)

        if mine == other:  # 双方一致（含双方都未改动）
            if mine is not None:
                merged_units[key] = mine
            continue

        if mine == before:  # 仅对方修改
            result.theirs_changes.append(_change_note(key, before, other))
            if other is not None:
                merged_units[key] = other
            continue

        if other == before:  # 仅我方修改
            result.ours_changes.append(_change_note(key, before, mine))
            if mine is not None:
                merged_units[key] = mine
            continue

        # 双方以不同方式修改同一业务规则 → 冲突，需人工评审
        detail = f"我方 {_describe_node(key, mine)}；对方 {_describe_node(key, other)}"
        result.conflicts.append(
            SemanticConflict(
                path=key,
                kind=_rule_kind(key),
                ours=_describe_node(key, mine),
                theirs=_describe_node(key, other),
                detail=detail,
            )
        )

    if result.clean:
        result.merged = _unflatten(merged_units)
    return result


def merge_paths(base: str | Path, ours: str | Path, theirs: str | Path) -> MergeResult:
    """按文件路径加载三个版本并做语义三方合并。"""

    return merge_models(parser.load(base), parser.load(ours), parser.load(theirs))


def record(log: AuditLog, result: MergeResult, **context: object) -> AuditEntry:
    """把一次语义合并的判定写入审计日志（事件名 ``merge.decision``）。"""

    return log.append(
        "merge.decision",
        clean=result.clean,
        requires_manual_review=result.requires_manual_review,
        conflicts=[c.path for c in result.conflicts],
        ours_changes=len(result.ours_changes),
        theirs_changes=len(result.theirs_changes),
        **context,
    )


# ---------------------------------------------------------------------------
# 规则单元拆分 / 还原
# ---------------------------------------------------------------------------

def _flatten(model: ast.Model) -> dict[str, Any]:
    """把模型拆成 ``规则路径 -> 规则单元`` 的映射（容器节点仅保留自身属性）。"""

    units: dict[str, Any] = {}
    for ctx in model.contexts:
        ckey = f"context/{ctx.name}"
        units[ckey] = _strip(replace(ctx, values=(), intents=(), contracts=()))

        for value in ctx.values:
            vkey = f"{ckey}/value/{value.name}"
            units[vkey] = _strip(replace(value, fields=(), invariants=()))
            for item in value.fields:
                units[f"{vkey}/field/{item.name}"] = _strip(item)
            for invariant in value.invariants:
                units[f"{vkey}/invariant/{invariant.name}"] = _strip(invariant)

        for intent in ctx.intents:
            ikey = f"{ckey}/intent/{intent.name}"
            units[ikey] = _strip(
                replace(intent, fields=(), states=(), invariants=(), actions=())
            )
            for item in intent.fields:
                units[f"{ikey}/field/{item.name}"] = _strip(item)
            for state in intent.states:
                units[f"{ikey}/state/{state.name}"] = _strip(state)
            for invariant in intent.invariants:
                units[f"{ikey}/invariant/{invariant.name}"] = _strip(invariant)
            for action in intent.actions:
                units[f"{ikey}/action/{action.name}"] = _strip(action)

        for contract in ctx.contracts:
            units[f"{ckey}/contract/{contract.name}"] = _strip(contract)

    for scenario in model.scenarios:
        units[f"scenario/{scenario.name}"] = _strip(scenario)
    return units


def _unflatten(units: Mapping[str, Any]) -> ast.Model:
    """把 ``规则路径 -> 规则单元`` 还原为模型（规则按名称排序）。"""

    scenarios = tuple(_children(units, "", "scenario"))
    contexts: list[ast.ContextDecl] = []

    for ckey in sorted(units):
        if not ckey.startswith("context/") or ckey.count("/") != 1:
            continue
        container: ast.ContextDecl = units[ckey]
        prefix = f"context/{container.name}"

        values = []
        for value in _children(units, prefix, "value"):
            vprefix = f"{prefix}/value/{value.name}"
            values.append(
                replace(
                    value,
                    fields=tuple(_children(units, vprefix, "field")),
                    invariants=tuple(_children(units, vprefix, "invariant")),
                )
            )

        intents = []
        for intent in _children(units, prefix, "intent"):
            iprefix = f"{prefix}/intent/{intent.name}"
            intents.append(
                replace(
                    intent,
                    fields=tuple(_children(units, iprefix, "field")),
                    states=tuple(_children(units, iprefix, "state")),
                    invariants=tuple(_children(units, iprefix, "invariant")),
                    actions=tuple(_children(units, iprefix, "action")),
                )
            )

        contexts.append(
            replace(
                container,
                values=tuple(values),
                intents=tuple(intents),
                contracts=tuple(_children(units, prefix, "contract")),
            )
        )

    return ast.Model(contexts=tuple(contexts), scenarios=scenarios, source="<merged>")


def _children(units: Mapping[str, Any], prefix: str, kind: str) -> list[Any]:
    """取某容器下 ``kind`` 类型的直接子规则（按名称排序）。"""

    segment = f"{prefix}/{kind}/" if prefix else f"{kind}/"
    return [
        units[key]
        for key in sorted(units)
        if key.startswith(segment) and "/" not in key[len(segment) :]
    ]


# ---------------------------------------------------------------------------
# 比较与描述辅助
# ---------------------------------------------------------------------------

def _strip(node: Any) -> Any:
    """递归消除节点及其子表达式的行号，避免排版差异导致的伪冲突。"""

    if isinstance(node, tuple):
        return tuple(_strip(item) for item in node)
    if isinstance(node, list):
        return [_strip(item) for item in node]
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        kwargs = {f.name: _strip(getattr(node, f.name)) for f in dataclasses.fields(node)}
        if "line" in kwargs:
            kwargs["line"] = 0
        return type(node)(**kwargs)
    return node


def _rule_kind(key: str) -> str:
    segments = key.split("/")
    for segment in reversed(segments):
        if segment in _RULE_KINDS:
            return segment
    return "rule"


def _change_note(key: str, before: Any, after: Any) -> str:
    if before is None:
        return f"+ {key}"
    if after is None:
        return f"- {key}"
    return f"~ {key}: {_describe_node(key, before)} -> {_describe_node(key, after)}"


def _describe_node(key: str, node: Any) -> str:
    """把规则单元渲染成一句可读描述，用于冲突定位与变更说明。"""

    if node is None:
        return "（不存在）"
    kind = _rule_kind(key)
    if kind == "invariant":
        expr = render_expr(node.expr) if node.expr is not None else "∅"
        return f"表达式 {expr}" + ("（基线）" if node.baseline else "")
    if kind == "action":
        pre = render_expr(node.pre) if node.pre is not None else "∅"
        post = render_expr(node.post) if node.post is not None else "∅"
        return f"pre={pre}, post={post}"
    if kind == "state":
        return f"状态 [{' → '.join(node.values) or '-'}]"
    if kind == "field":
        default = render_expr(node.default) if node.default is not None else "-"
        return f"类型 {node.type}，默认 {default}"
    if kind == "contract":
        return f"暴露字段 [{', '.join(node.exposes) or '-'}]"
    if kind in ("context", "intent", "value"):
        return f"描述 {node.description or '-'}"
    if kind == "scenario":
        return f"步骤 {len(node.steps)}"
    return getattr(node, "name", "<规则>")
