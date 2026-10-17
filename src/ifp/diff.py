"""语义 Diff：比较两个 IFP 模型在业务语义层面的差异。

对应《IFP 建模规范》第 11 / 13 节。与文本 diff 的区别：这里比较的是
业务规则结构（不变量、状态机、前后置条件、契约），而非源码字符。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from . import ast, parser
from .render import render_expr


@dataclass(frozen=True)
class Change:
    kind: str  # added | removed | changed
    path: str
    detail: str
    baseline: bool = False
    file: str = ""  # 变更所属模型文件（目录 Diff 时相对于根目录）

    def format(self) -> str:
        mark = {"added": "+", "removed": "-", "changed": "~"}.get(self.kind, "?")
        tag = " [BASELINE]" if self.baseline else ""
        prefix = f"{self.file}: " if self.file else ""
        return f"{mark} {prefix}{self.path}{tag}: {self.detail}"


@dataclass
class DiffResult:
    changes: list[Change] = field(default_factory=list)

    @property
    def has_baseline_change(self) -> bool:
        return any(c.baseline for c in self.changes)

    @property
    def counts(self) -> dict[str, int]:
        out = {"added": 0, "removed": 0, "changed": 0}
        for c in self.changes:
            out[c.kind] = out.get(c.kind, 0) + 1
        return out


def diff_models(old: ast.Model, new: ast.Model) -> DiffResult:
    """比较两个模型，返回结构化差异。"""

    result = DiffResult()
    old_ctxs = {c.name: c for c in old.contexts}
    new_ctxs = {c.name: c for c in new.contexts}

    for name in sorted(set(old_ctxs) | set(new_ctxs)):
        if name not in old_ctxs:
            result.changes.append(Change("added", f"context {name}", "新增限界上下文"))
            continue
        if name not in new_ctxs:
            result.changes.append(Change("removed", f"context {name}", "删除限界上下文"))
            continue
        _diff_context(old_ctxs[name], new_ctxs[name], result)

    _diff_scenarios(old, new, result)
    return result


# ---------------------------------------------------------------------------

def _diff_context(old: ast.ContextDecl, new: ast.ContextDecl, result: DiffResult) -> None:
    if old.description != new.description:
        result.changes.append(
            Change("changed", f"{old.name}/description", "上下文描述变更")
        )
    _diff_intents(old, new, result)
    _diff_values(old, new, result)
    _diff_contracts(old, new, result)


def _diff_intents(old: ast.ContextDecl, new: ast.ContextDecl, result: DiffResult) -> None:
    old_intents = {i.name: i for i in old.intents}
    new_intents = {i.name: i for i in new.intents}
    for name in sorted(set(old_intents) | set(new_intents)):
        path = f"{old.name}/{name}"
        if name not in old_intents:
            result.changes.append(Change("added", f"intent {path}", "新增聚合根"))
            continue
        if name not in new_intents:
            result.changes.append(Change("removed", f"intent {path}", "删除聚合根"))
            continue
        _diff_intent_body(old_intents[name], new_intents[name], path, result)


def _diff_intent_body(
    old: ast.IntentDecl, new: ast.IntentDecl, path: str, result: DiffResult
) -> None:
    # 字段
    old_fields = {f.name: f for f in old.fields}
    new_fields = {f.name: f for f in new.fields}
    for name in sorted(set(old_fields) | set(new_fields)):
        loc = f"{path}/field {name}"
        if name not in old_fields:
            result.changes.append(Change("added", loc, f"新增字段 {new_fields[name].type}"))
        elif name not in new_fields:
            result.changes.append(Change("removed", loc, "删除字段"))
        else:
            o, n = old_fields[name], new_fields[name]
            if str(o.type) != str(n.type):
                result.changes.append(
                    Change("changed", loc, f"类型 {o.type} -> {n.type}")
                )

    # 状态机
    old_states = {s.name: s.values for s in old.states}
    new_states = {s.name: s.values for s in new.states}
    for name in sorted(set(old_states) | set(new_states)):
        loc = f"{path}/state {name}"
        if name not in old_states:
            result.changes.append(Change("added", loc, f"新增状态机 {list(new_states[name])}"))
        elif name not in new_states:
            result.changes.append(Change("removed", loc, "删除状态机"))
        else:
            o, n = set(old_states[name]), set(new_states[name])
            if o != n:
                detail = []
                if n - o:
                    detail.append(f"新增状态 {sorted(n - o)}")
                if o - n:
                    detail.append(f"删除状态 {sorted(o - n)}")
                result.changes.append(Change("changed", loc, "；".join(detail)))

    # 不变量
    old_inv = {v.name: v for v in old.invariants}
    new_inv = {v.name: v for v in new.invariants}
    for name in sorted(set(old_inv) | set(new_inv)):
        loc = f"{path}/invariant {name}"
        if name not in old_inv:
            n = new_inv[name]
            result.changes.append(
                Change("added", loc, f"新增不变量 {_render(n.expr)}", n.baseline)
            )
        elif name not in new_inv:
            o = old_inv[name]
            result.changes.append(
                Change("removed", loc, f"删除不变量 {_render(o.expr)}", o.baseline)
            )
        else:
            o, n = old_inv[name], new_inv[name]
            if _render(o.expr) != _render(n.expr):
                result.changes.append(
                    Change(
                        "changed", loc,
                        f"表达式 {_render(o.expr)} -> {_render(n.expr)}",
                        o.baseline or n.baseline,
                    )
                )
            if o.baseline != n.baseline:
                result.changes.append(
                    Change(
                        "changed", loc,
                        f"基线标记 {o.baseline} -> {n.baseline}",
                        o.baseline or n.baseline,
                    )
                )

    # 动作
    old_act = {a.name: a for a in old.actions}
    new_act = {a.name: a for a in new.actions}
    for name in sorted(set(old_act) | set(new_act)):
        loc = f"{path}/action {name}"
        if name not in old_act:
            result.changes.append(Change("added", loc, "新增动作"))
        elif name not in new_act:
            result.changes.append(Change("removed", loc, "删除动作"))
        else:
            o, n = old_act[name], new_act[name]
            if _render(o.pre) != _render(n.pre):
                result.changes.append(
                    Change("changed", f"{loc}/pre", f"{_render(o.pre)} -> {_render(n.pre)}")
                )
            if _render(o.post) != _render(n.post):
                result.changes.append(
                    Change("changed", f"{loc}/post", f"{_render(o.post)} -> {_render(n.post)}")
                )


def _diff_values(old: ast.ContextDecl, new: ast.ContextDecl, result: DiffResult) -> None:
    old_values = {v.name: v for v in old.values}
    new_values = {v.name: v for v in new.values}
    for name in sorted(set(old_values) | set(new_values)):
        loc = f"{old.name}/value {name}"
        if name not in old_values:
            result.changes.append(Change("added", loc, "新增值对象"))
        elif name not in new_values:
            result.changes.append(Change("removed", loc, "删除值对象"))


def _diff_contracts(old: ast.ContextDecl, new: ast.ContextDecl, result: DiffResult) -> None:
    old_c = {c.name: c for c in old.contracts}
    new_c = {c.name: c for c in new.contracts}
    for name in sorted(set(old_c) | set(new_c)):
        loc = f"{old.name}/contract {name}"
        if name not in old_c:
            result.changes.append(Change("added", loc, "新增跨域契约"))
        elif name not in new_c:
            result.changes.append(Change("removed", loc, "删除跨域契约"))
        else:
            if set(old_c[name].exposes) != set(new_c[name].exposes):
                result.changes.append(
                    Change("changed", f"{loc}/expose", "契约暴露字段变更")
                )


def _diff_scenarios(old: ast.Model, new: ast.Model, result: DiffResult) -> None:
    old_s = {s.name: s for s in old.scenarios}
    new_s = {s.name: s for s in new.scenarios}
    for name in sorted(set(old_s) | set(new_s)):
        loc = f"scenario {name}"
        if name not in old_s:
            result.changes.append(Change("added", loc, "新增仿真场景"))
        elif name not in new_s:
            result.changes.append(Change("removed", loc, "删除仿真场景"))
        elif len(old_s[name].steps) != len(new_s[name].steps):
            result.changes.append(Change("changed", loc, "场景步骤数变化"))


def diff_paths(old: str | Path, new: str | Path) -> DiffResult:
    """比较两个模型路径。

    - 两个**文件**：按“旧 -> 新”成对比较（与 :func:`diff_models` 等价）。
    - 涉及**目录**：按**相对路径**逐一匹配 `.ifp` 文件；仅存在于一侧的文件
      等价于与空模型比较（整体新增 / 删除）。

    目录模式下每处变更都记录其所属文件，便于 CI 语义门禁在 PR 上定位具体模型。
    """

    old_root, new_root = Path(old), Path(new)
    if old_root.is_file() and new_root.is_file():
        return diff_models(parser.load(old_root), parser.load(new_root))

    old_files = _collect_ifp(old_root)
    new_files = _collect_ifp(new_root)

    result = DiffResult()
    for rel in sorted(set(old_files) | set(new_files)):
        old_model = parser.load(old_files[rel]) if rel in old_files else ast.Model()
        new_model = parser.load(new_files[rel]) if rel in new_files else ast.Model()
        for change in diff_models(old_model, new_model).changes:
            result.changes.append(replace(change, file=rel))
    return result


def _collect_ifp(root: Path) -> dict[str, Path]:
    """把单个文件或目录收集为 ``相对路径 -> 文件`` 的映射。"""

    if root.is_file():
        return {root.name: root}
    if root.is_dir():
        return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*.ifp"))}
    return {}


def _render(expr: ast.Expr | None) -> str:
    return render_expr(expr) if expr is not None else "∅"
