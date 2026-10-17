"""IFP Web 编辑器：浏览器内在线编辑 `.ifp`、实时诊断与模型可视化。

只用标准库 ``http.server`` 提供本地服务，页面是自包含的静态资源（无外部依赖、无构建
步骤），方便业务同学直接打开浏览器参与建模。提供的接口：

- ``GET /``           编辑器页面
- ``POST /api/analyze``  解析 + 类型检查 + Lint，返回诊断与模型结构（供可视化）
- ``POST /api/verify``   运行属性测试，返回反例

诊断与 CLI / 语言服务器同源，均来自 :mod:`ifp.typecheck` 与 :mod:`ifp.lint`。
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from . import ast, parser, plugin
from .errors import Diagnostic, IFPParseError
from .lint import lint as run_lint
from .lsp import _parse_error_diagnostic
from .render import render_expr
from .smt import VerifyConfig, check_consistency, verify
from .typecheck import typecheck

_ASSETS = Path(__file__).parent / "webassets"

#: 静态资源与 Content-Type 的映射（只允许白名单文件，避免路径穿越）。
_CONTENT_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}


# ---------------------------------------------------------------------------
# 分析 / 可视化的数据结构
# ---------------------------------------------------------------------------

def _diag_dict(diag: Diagnostic) -> dict[str, Any]:
    return {
        "code": diag.code,
        "message": diag.message,
        "severity": diag.severity.value,
        "line": diag.line,
        "file": diag.file,
    }


def _enum_members(expr: Optional[ast.Expr], enum_name: str) -> list[str]:
    """收集表达式中属于 ``enum_name`` 的状态成员（用于推导状态机迁移起点）。"""

    found: list[str] = []
    stack = [expr] if expr is not None else []
    while stack:
        node = stack.pop()
        if isinstance(node, ast.EnumRef) and node.enum == enum_name:
            found.append(node.member)
        stack.extend(_expr_children(node))
    return found


def _expr_children(expr: ast.Expr) -> list[ast.Expr]:
    if isinstance(expr, ast.OldExpr):
        return [expr.inner]
    if isinstance(expr, ast.UnaryExpr):
        return [expr.operand]
    if isinstance(expr, ast.BinaryExpr):
        return [expr.left, expr.right]
    if isinstance(expr, ast.CallExpr):
        return list(expr.args)
    return []


def _transitions(intent: ast.IntentDecl) -> list[dict[str, Any]]:
    """由动作的 ``pre``（起点状态）与 ``transition``（目标状态）推导状态机迁移。"""

    transitions: list[dict[str, Any]] = []
    for action in intent.actions:
        if action.transition is None:
            continue
        enum = action.transition.enum
        target = action.transition.member
        sources = _enum_members(action.pre, enum)
        if sources:
            transitions.extend(
                {"from": src, "action": action.name, "to": target} for src in sources
            )
        else:
            transitions.append({"from": None, "action": action.name, "to": target})
    return transitions


def _invariant_dict(inv: ast.InvariantDecl) -> dict[str, Any]:
    return {
        "name": inv.name,
        "expr": render_expr(inv.expr) if inv.expr is not None else None,
        "baseline": inv.baseline,
        "owner": inv.owner,
        "note": inv.note,
    }


def _intent_view(intent: ast.IntentDecl) -> dict[str, Any]:
    states = [{"name": s.name, "values": list(s.values)} for s in intent.states]
    machine: Optional[dict[str, Any]] = None
    if states:
        machine = {
            "name": states[0]["name"],
            "values": states[0]["values"],
            "initial": render_expr(intent.initial) if intent.initial is not None else None,
            "transitions": _transitions(intent),
        }
    return {
        "name": intent.name,
        "description": intent.description,
        "fields": [
            {
                "name": f.name,
                "type": str(f.type),
                "default": render_expr(f.default) if f.default is not None else None,
            }
            for f in intent.fields
        ],
        "stateMachine": machine,
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


def model_view(model: ast.Model) -> dict[str, Any]:
    """把模型整理成前端可视化用的结构（对应 Markdown 报表的分节）。"""

    return {
        "contexts": [
            {
                "name": ctx.name,
                "description": ctx.description,
                "values": [
                    {
                        "name": v.name,
                        "fields": [{"name": f.name, "type": str(f.type)} for f in v.fields],
                        "invariants": [_invariant_dict(i) for i in v.invariants],
                    }
                    for v in ctx.values
                ],
                "intents": [_intent_view(i) for i in ctx.intents],
                "contracts": [
                    {"name": c.name, "exposes": list(c.exposes)} for c in ctx.contracts
                ],
            }
            for ctx in model.contexts
        ],
        "scenarios": [
            {
                "name": s.name,
                "context": s.context,
                "intent": s.intent,
                "given": {k: render_expr(v) for k, v in s.given},
                "steps": len(s.steps),
            }
            for s in model.scenarios
        ],
    }


# ---------------------------------------------------------------------------
# 接口逻辑
# ---------------------------------------------------------------------------

def analyze(source: str, filename: str = "<editor>") -> dict[str, Any]:
    """解析 + 类型检查 + Lint，返回诊断与模型结构。"""

    try:
        model = parser.parse(source, filename=filename)
    except IFPParseError as exc:
        diag = _parse_error_diagnostic(exc)
        return {"diagnostics": [_diag_dict(diag)], "model": None, "parseError": exc.message}

    diags: list[Diagnostic] = list(typecheck(model, project=model))
    diags.extend(run_lint(model, extra_rules=plugin.lint_rules()))
    return {
        "diagnostics": [_diag_dict(d) for d in diags],
        "model": model_view(model),
        "parseError": None,
    }


def run_verification(
    source: str, filename: str = "<editor>", intent: Optional[str] = None, mode: str = "light"
) -> dict[str, Any]:
    """运行属性测试与一致性检查，返回反例。"""

    try:
        model = parser.parse(source, filename=filename)
    except IFPParseError as exc:
        diag = _parse_error_diagnostic(exc)
        return {"counterexamples": [], "diagnostics": [_diag_dict(diag)], "parseError": exc.message}

    config = VerifyConfig(mode=mode)
    report = verify(model, intent, config)
    diags: list[Diagnostic] = list(report.diagnostics)
    diags.extend(check_consistency(model, intent, config))
    return {
        "counterexamples": [_counterexample_dict(ce) for ce in report.counterexamples],
        "diagnostics": [_diag_dict(d) for d in diags],
        "actionsChecked": report.actions_checked,
        "invariantsChecked": report.invariants_checked,
        "parseError": None,
    }


def _counterexample_dict(ce: Any) -> dict[str, Any]:
    return {
        "intent": ce.intent,
        "action": ce.action,
        "invariant": ce.invariant,
        "baseline": ce.baseline,
        "steps": ce.steps,
        "before": ce.before,
        "after": ce.after,
        "args": ce.args,
        "path": [
            {"action": s.action, "args": s.args, "before": s.before, "after": s.after}
            for s in ce.path
        ],
        "describe": ce.describe(),
    }


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "ifp-web"

    # -- 工具方法 -----------------------------------------------------------
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_asset(self, name: str) -> None:
        path = _ASSETS / name
        if name not in _CONTENT_TYPES or not path.is_file():
            self._send_json({"error": "not found"}, status=404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES[name])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        return payload if isinstance(payload, dict) else {}

    # -- 路由 ---------------------------------------------------------------
    def do_GET(self) -> None:
        name = "index.html" if self.path in ("/", "/index.html") else self.path.lstrip("/")
        self._send_asset(name)

    def do_POST(self) -> None:
        payload = self._read_json()
        source = str(payload.get("source", ""))
        filename = str(payload.get("filename") or "<editor>")
        if self.path == "/api/analyze":
            self._send_json(analyze(source, filename))
        elif self.path == "/api/verify":
            mode = str(payload.get("mode") or "light")
            self._send_json(run_verification(source, filename, mode=mode))
        else:
            self._send_json({"error": "not found"}, status=404)

    def log_message(self, *args: Any) -> None:  # 保持 CLI 输出干净
        return


def serve(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """启动 Web 编辑器服务（后台线程可用）。返回值可直接 ``serve_forever``。"""

    return ThreadingHTTPServer((host, port), _Handler)
