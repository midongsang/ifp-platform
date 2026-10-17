"""事件重放（Event Replay）：按事件序列恢复聚合状态并校验不变量。

事件是聚合状态变化的事实记录。重放时不再校验动作前置条件（事件是已发生
的历史事实），而是逐步应用事件对状态的影响，并在每一步之后校验聚合不变量，
从而验证事件序列的整体合法性。

与 ``simulate`` 的分工：
- ``simulate`` 按场景构造的动作序列执行，覆盖前置条件拦截与后置状态断言；
- ``event_replay`` 按已发生的事件序列恢复状态，关注不变量是否始终成立。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

from . import ast
from .simulate import (
    Value,
    _check_invariants,
    _vo_defaults,
    apply_post,
    eval_expr,
)


@dataclass
class ReplayStepResult:
    """单个事件的重放结果。"""

    event_type: str
    applied: bool
    state_after: dict[str, Value] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    message: str = ""

    def summary(self) -> str:
        if not self.applied:
            return f"[FAIL] {self.event_type}: {self.message}"
        if self.violations:
            return f"[FAIL] {self.event_type}: 违反不变量 {', '.join(self.violations)}"
        return f"[OK]   {self.event_type}"


@dataclass
class ReplayResult:
    """一次事件重放的完整结果。"""

    passed: bool
    steps: list[ReplayStepResult] = field(default_factory=list)
    final_state: dict[str, Value] = field(default_factory=dict)
    message: str = ""

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        detail = "" if self.passed else f" | {self.message}" if self.message else ""
        return f"[{status}] 事件重放（{len(self.steps)} 步）{detail}"


# ---------------------------------------------------------------------------
# 事件数据加载与值转换
# ---------------------------------------------------------------------------

def load_events(path: str | Path) -> dict[str, Any]:
    """从 JSON 文件加载事件序列。

    期望结构::

        {
          "context": "RefundContext",
          "intent": "RefundOrder",
          "initial": {"orderPaidAmount": 100, ...},
          "events": [
            {"type": "RefundExecuted", "data": {"amount": 40}}
          ]
        }
    """

    return json.loads(Path(path).read_text(encoding="utf-8"))


def _coerce_value(raw: Any, type_ref: Optional[ast.TypeRef]) -> Value:
    """把 JSON 原始值按字段类型转换为运行时值。

    - Decimal/Int：数字或数字字符串；
    - Bool：布尔值或 "true"/"false" 字符串；
    - String：字符串；
    - Named（枚举）："Enum::Member" 字符串；
    - 类型未知时原样返回。
    """

    if type_ref is None:
        return raw
    kind = type_ref.kind
    if kind == "Decimal":
        try:
            return Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return raw
    if kind == "Int":
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw
    if kind == "Bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.lower() == "true"
        return bool(raw)
    if kind == "String":
        return str(raw)
    # Named（枚举 / 值对象）：保持字符串形式（"Enum::Member"）或原样
    return raw


def _event_field_types(event: ast.EventDecl) -> dict[str, ast.TypeRef]:
    return {f.name: f.type for f in event.fields}


def _intent_field_map(intent: ast.IntentDecl) -> dict[str, ast.FieldDecl]:
    return {f.name: f for f in intent.fields}


# ---------------------------------------------------------------------------
# 重放核心
# ---------------------------------------------------------------------------

def replay_events(
    model: ast.Model,
    events_payload: dict[str, Any],
) -> ReplayResult:
    """对模型执行事件重放，返回每步状态与不变量校验结果。"""

    context_name = events_payload.get("context")
    intent_name = events_payload.get("intent")
    if not context_name or not intent_name:
        return ReplayResult(False, message="事件序列缺少 context 或 intent 字段")

    ctx = model.find_context(context_name)
    if ctx is None:
        return ReplayResult(False, message=f"未找到 context {context_name}")
    intent = next((i for i in ctx.intents if i.name == intent_name), None)
    if intent is None:
        return ReplayResult(False, message=f"未找到 intent {intent_name}")

    value_objects = {vo.name: vo for vo in ctx.values}
    events_by_name = {e.name: e for e in ctx.events}
    actions_by_name = {a.name: a for a in intent.actions}

    state = _initial_state(intent, value_objects, events_payload.get("initial", {}))

    steps: list[ReplayStepResult] = []
    for raw_event in events_payload.get("events", []):
        event_type = raw_event.get("type")
        data = raw_event.get("data", {}) or {}
        if not event_type:
            steps.append(
                ReplayStepResult("", False, state, message="事件缺少 type 字段")
            )
            return ReplayResult(False, steps, state, "事件序列格式错误")

        event_decl = events_by_name.get(event_type)
        if event_decl is None:
            steps.append(
                ReplayStepResult(
                    event_type, False, state,
                    message=f"未定义的事件类型 {event_type}",
                )
            )
            return ReplayResult(
                False, steps, state, f"引用了未定义的事件 {event_type}"
            )

        # 把事件数据按声明类型转换
        field_types = _event_field_types(event_decl)
        params = {
            k: _coerce_value(v, field_types.get(k)) for k, v in data.items()
        }

        # 应用事件对状态的影响
        if event_decl.produced_by is not None:
            action = actions_by_name.get(event_decl.produced_by)
            if action is None:
                steps.append(
                    ReplayStepResult(
                        event_type, False, state,
                        message=f"produced_by 动作 {event_decl.produced_by} 未找到",
                    )
                )
                return ReplayResult(
                    False, steps, state,
                    f"事件 {event_type} 的 produced_by 动作不存在",
                )
            try:
                state = apply_post(action.post, state, params)
            except (KeyError, TypeError, ValueError) as exc:
                steps.append(
                    ReplayStepResult(
                        event_type, False, state,
                        message=f"应用动作 post 失败：{exc}",
                    )
                )
                return ReplayResult(
                    False, steps, state, f"事件 {event_type} 应用失败"
                )
        else:
            # 无 produced_by：按字段名直接写入状态
            state = dict(state)
            for k, v in params.items():
                state[k] = v

        violations = _check_invariants(intent, state, value_objects)
        steps.append(
            ReplayStepResult(
                event_type, True, dict(state), list(violations)
            )
        )
        if violations:
            return ReplayResult(
                False, steps, state,
                f"事件 {event_type} 应用后违反不变量：{', '.join(violations)}",
            )

    return ReplayResult(True, steps, state)


def _initial_state(
    intent: ast.IntentDecl,
    value_objects: dict[str, ast.ValueDecl],
    overrides: dict[str, Any],
) -> dict[str, Value]:
    """构建聚合初始状态：先取字段默认值，再用事件序列的 initial 覆盖。"""

    state: dict[str, Value] = {}
    for f in intent.fields:
        if f.type.kind == "Named" and f.type.name in value_objects:
            for key, expr in _vo_defaults(f.name, value_objects[f.type.name], value_objects):
                state[key] = eval_expr(expr, {}, {})
        elif f.default is not None:
            state[f.name] = eval_expr(f.default, {}, {})

    field_map = _intent_field_map(intent)
    for key, raw in overrides.items():
        if "." in key:
            # 值对象叶子字段：直接写入
            state[key] = raw
            continue
        fdecl = field_map.get(key)
        state[key] = _coerce_value(raw, fdecl.type if fdecl else None)

    return state


# ---------------------------------------------------------------------------
# CLI 辅助
# ---------------------------------------------------------------------------

def replay_from_file(
    model: ast.Model, events_path: str | Path
) -> ReplayResult:
    """从 JSON 文件加载事件序列并执行重放。"""

    payload = load_events(events_path)
    return replay_events(model, payload)


__all__ = [
    "ReplayResult",
    "ReplayStepResult",
    "load_events",
    "replay_events",
    "replay_from_file",
]
