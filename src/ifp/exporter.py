"""导出器：把 IFP 模型导出为其他格式制品。

对应《IFP 建模规范》第 12 / 15 节“制品导出：OpenAPI / Protobuf / 报表”。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from . import ast
from .render import render_expr

# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def to_json(model: ast.Model, indent: int = 2) -> str:
    """把模型导出为结构化 JSON（便于下游工具消费）。"""

    return json.dumps(_model_dict(model), ensure_ascii=False, indent=indent)


def _model_dict(model: ast.Model) -> dict[str, Any]:
    return {
        "source": model.source,
        "contexts": [_context_dict(c) for c in model.contexts],
        "scenarios": [_scenario_dict(s) for s in model.scenarios],
    }


def _context_dict(ctx: ast.ContextDecl) -> dict[str, Any]:
    return {
        "name": ctx.name,
        "description": ctx.description,
        "values": [_value_dict(v) for v in ctx.values],
        "intents": [_intent_dict(i) for i in ctx.intents],
        "contracts": [
            {"name": c.name, "description": c.description, "exposes": list(c.exposes)}
            for c in ctx.contracts
        ],
        "events": [_event_dict(e) for e in ctx.events],
    }


def _event_dict(event: ast.EventDecl) -> dict[str, Any]:
    return {
        "name": event.name,
        "description": event.description,
        "fields": [_field_dict(f) for f in event.fields],
        "produced_by": event.produced_by,
    }


def _value_dict(vo: ast.ValueDecl) -> dict[str, Any]:
    return {
        "name": vo.name,
        "fields": [_field_dict(f) for f in vo.fields],
        "invariants": [_invariant_dict(i) for i in vo.invariants],
    }


def _field_dict(f: ast.FieldDecl) -> dict[str, Any]:
    return {
        "name": f.name,
        "type": str(f.type),
        "default": render_expr(f.default) if f.default is not None else None,
    }


def _invariant_dict(inv: ast.InvariantDecl) -> dict[str, Any]:
    return {
        "name": inv.name,
        "expr": render_expr(inv.expr) if inv.expr is not None else None,
        "baseline": inv.baseline,
        "owner": inv.owner,
        "risk": inv.risk,
        "note": inv.note,
    }


def _intent_dict(intent: ast.IntentDecl) -> dict[str, Any]:
    return {
        "name": intent.name,
        "description": intent.description,
        "fields": [_field_dict(f) for f in intent.fields],
        "states": [{"name": s.name, "values": list(s.values)} for s in intent.states],
        "initial": render_expr(intent.initial) if intent.initial is not None else None,
        "invariants": [_invariant_dict(i) for i in intent.invariants],
        "actions": [
            {
                "name": a.name,
                "params": [{"name": p.name, "type": str(p.type)} for p in a.params],
                "pre": render_expr(a.pre) if a.pre is not None else None,
                "post": render_expr(a.post) if a.post is not None else None,
                "transition": render_expr(a.transition) if a.transition is not None else None,
            }
            for a in intent.actions
        ],
    }


def _scenario_dict(s: ast.ScenarioDecl) -> dict[str, Any]:
    return {
        "name": s.name,
        "context": s.context,
        "intent": s.intent,
        "given": {name: render_expr(expr) for name, expr in s.given},
        "steps": len(s.steps),
    }


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

def to_openapi(model: ast.Model, context: Optional[str] = None) -> str:
    """把模型中的动作导出为 OpenAPI 3.0 骨架。"""

    paths: dict[str, Any] = {}
    schemas: dict[str, Any] = {}
    for ctx in model.contexts:
        if context and ctx.name != context:
            continue
        for intent in ctx.intents:
            schemas[intent.name] = _openapi_schema(intent)
            for action in intent.actions:
                path = f"/{_kebab(intent.name)}/{_kebab(action.name)}"
                paths[path] = {
                    "post": {
                        "operationId": f"{intent.name}_{action.name}",
                        "summary": f"{intent.name}.{action.name}",
                        "tags": [ctx.name],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            p.name: _openapi_type(p.type)
                                            for p in action.params
                                        },
                                        "required": [p.name for p in action.params],
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "OK"}},
                        "x-ifp-pre": render_expr(action.pre) if action.pre else None,
                        "x-ifp-post": render_expr(action.post) if action.post else None,
                    }
                }
    doc = {
        "openapi": "3.0.3",
        "info": {
            "title": "IFP 导出制品",
            "version": "0.1.0",
            "description": "由 IFP 模型自动导出的接口骨架（x-ifp-* 为业务约束）",
        },
        "paths": paths,
        "components": {"schemas": schemas},
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _openapi_schema(intent: ast.IntentDecl) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {f.name: _openapi_type(f.type) for f in intent.fields},
    }


def _openapi_type(t: ast.TypeRef) -> dict[str, Any]:
    mapping = {
        "Decimal": {"type": "number"},
        "Int": {"type": "integer"},
        "Bool": {"type": "boolean"},
        "String": {"type": "string"},
    }
    if t.kind in mapping:
        return mapping[t.kind]
    return {"type": "string", "description": str(t)}


def _kebab(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append("-")
        out.append(ch.lower())
    return "".join(out)


# ---------------------------------------------------------------------------
# Markdown 报表
# ---------------------------------------------------------------------------

def to_markdown(model: ast.Model) -> str:
    """生成模型清单 / 评审报表（Markdown）。"""

    lines: list[str] = ["# IFP 模型报表", "", f"- 来源：`{model.source}`", ""]
    for ctx in model.contexts:
        lines.append(f"## 限界上下文 {ctx.name}")
        if ctx.description:
            lines.append(f"> {ctx.description}")
        lines.append("")
        for intent in ctx.intents:
            lines.append(f"### Intent {intent.name}")
            lines.append("")
            lines.append("| 字段 | 类型 | 默认值 |")
            lines.append("| --- | --- | --- |")
            for f in intent.fields:
                default = render_expr(f.default) if f.default is not None else "-"
                lines.append(f"| {f.name} | {f.type} | {default} |")
            lines.append("")
            for state in intent.states:
                lines.append(f"- 状态机 `{state.name}`：{' → '.join(state.values)}")
            lines.append("")
            if intent.invariants:
                lines.append("| 不变量 | 表达式 | 基线 | 负责人 |")
                lines.append("| --- | --- | --- | --- |")
                for inv in intent.invariants:
                    expr = render_expr(inv.expr) if inv.expr is not None else "-"
                    lines.append(
                        f"| {inv.name} | `{expr}` | {'是' if inv.baseline else '否'} "
                        f"| {inv.owner or '-'} |"
                    )
                lines.append("")
            for action in intent.actions:
                params = ", ".join(f"{p.name}: {p.type}" for p in action.params)
                lines.append(f"- 动作 `{action.name}({params})`")
                if action.pre is not None:
                    lines.append(f"  - pre: `{render_expr(action.pre)}`")
                if action.post is not None:
                    lines.append(f"  - post: `{render_expr(action.post)}`")
            lines.append("")
        for contract in ctx.contracts:
            lines.append(f"### 契约 {contract.name}")
            lines.append(f"- 暴露字段：{', '.join(contract.exposes) or '-'}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Protobuf
# ---------------------------------------------------------------------------

def to_protobuf(model: ast.Model, context: Optional[str] = None) -> str:
    """生成 .proto 骨架：状态机 -> enum，Intent / 参数 -> message。"""

    lines: list[str] = ['syntax = "proto3";', "", "package ifp.generated;", ""]
    for ctx in model.contexts:
        if context and ctx.name != context:
            continue
        lines.append(f"// ---- {ctx.name} ----")
        for intent in ctx.intents:
            for state in intent.states:
                lines.append(f"enum {state.name} {{")
                lines.append(f"  {state.name.upper()}_UNSPECIFIED = 0;")
                for idx, value in enumerate(state.values, start=1):
                    lines.append(f"  {state.name.upper()}_{value.upper()} = {idx};")
                lines.append("}")
                lines.append("")
            lines.append(f"message {intent.name} {{")
            for idx, f in enumerate(intent.fields, start=1):
                lines.append(f"  {_proto_type(f.type)} {f.name} = {idx};")
            lines.append("}")
            lines.append("")
            for action in intent.actions:
                if not action.params:
                    continue
                lines.append(f"message {intent.name}{action.name}Request {{")
                for idx, p in enumerate(action.params, start=1):
                    lines.append(f"  {_proto_type(p.type)} {p.name} = {idx};")
                lines.append("}")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _proto_type(t: ast.TypeRef) -> str:
    # Decimal 映射为 string，避免使用 double 造成资金精度丢失
    return {
        "Decimal": "string",
        "Int": "int64",
        "Bool": "bool",
        "String": "string",
    }.get(t.kind, "string")
