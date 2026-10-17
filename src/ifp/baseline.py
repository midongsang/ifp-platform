"""基线管理与变更风险评分。

对应《IFP 建模规范》第 6 / 11 节：
- 只有核心资金 / 合规类不变量才可标记 Baseline；
- 基线不变量修改必须触发强制审批；
- 用风险分值为审批优先级排序。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ast
from .diff import Change, DiffResult

# 审批阈值：风险分值 >= 该值必须人工审批
APPROVAL_THRESHOLD = 40


@dataclass(frozen=True)
class BaselineRef:
    context: str
    intent: str
    invariant: str
    owner: str | None
    risk: str | None

    def format(self) -> str:
        return f"{self.context}/{self.intent}/{self.invariant}（owner={self.owner or '未指定'}）"


def baseline_invariants(model: ast.Model) -> list[BaselineRef]:
    """列出模型中所有基线不变量。"""

    refs: list[BaselineRef] = []
    for ctx in model.contexts:
        for intent in ctx.intents:
            for inv in intent.invariants:
                if inv.baseline:
                    refs.append(
                        BaselineRef(
                            context=ctx.name,
                            intent=intent.name,
                            invariant=inv.name,
                            owner=inv.owner,
                            risk=inv.risk,
                        )
                    )
    return refs


def risk_score(diff: DiffResult) -> int:
    """基于语义差异给出 0~100 的变更风险分值。"""

    score = 0
    weights = {
        "context": 25,
        "contract": 20,
        "invariant": 25,
        "action": 15,
        "field": 10,
        "state": 15,
        "scenario": 5,
        "description": 0,
    }
    for change in diff.changes:
        key = _category(change.path)
        weight = weights.get(key, 5)
        if change.baseline:
            weight += 40
        if change.kind == "removed":
            weight += 10
        score += weight
    return min(score, 100)


def approval_required(diff: DiffResult, threshold: int = APPROVAL_THRESHOLD) -> bool:
    """是否需要强制审批：涉及基线变更或风险分值不低于阈值。"""

    return diff.has_baseline_change or risk_score(diff) >= threshold


def baseline_changes(diff: DiffResult) -> list[Change]:
    return [c for c in diff.changes if c.baseline]


def _category(path: str) -> str:
    if "/contract" in path or path.startswith("contract"):
        return "contract"
    if "/invariant" in path:
        return "invariant"
    if "/action" in path:
        return "action"
    if "/field" in path:
        return "field"
    if "/state" in path:
        return "state"
    if path.startswith("scenario"):
        return "scenario"
    if path.endswith("description"):
        return "description"
    if path.startswith("context"):
        return "context"
    return "other"
