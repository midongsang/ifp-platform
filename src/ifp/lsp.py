"""IFP 语言服务器（LSP over stdio）。

把 :mod:`ifp.typecheck` 与 :mod:`ifp.lint` 的诊断接到 LSP 协议上，让编辑器获得 `.ifp`
实时诊断、结构大纲、跳转与快速修复。实现只用标准库，按 Content-Length 分帧走 stdin/stdout，
可由编辑器作为子进程拉起：

    ifp lsp --stdio

支持的方法：``initialize`` / ``initialized`` / ``shutdown`` / ``exit``、
``textDocument/didOpen`` / ``didChange`` / ``didClose``、
``textDocument/documentSymbol`` / ``definition`` / ``codeAction``。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, BinaryIO, Optional
from urllib.parse import unquote, urlparse

from . import __version__, parser, plugin
from .ast import (
    ContextDecl,
    ContractDecl,
    FieldDecl,
    IntentDecl,
    InvariantDecl,
    Model,
    ValueDecl,
)
from .errors import Diagnostic, IFPParseError, Severity
from .lint import BASELINE_MISSING_OWNER, BASELINE_MISSING_RISK, MISSING_INITIAL_STATE
from .lint import lint as run_lint
from .typecheck import UNKNOWN_FIELD_TYPE, typecheck

#: LSP 诊断严重级：1=Error，2=Warning，3=Information。
_SEVERITY_CODE = {Severity.ERROR: 1, Severity.WARNING: 2, Severity.INFO: 3}

_LINE_RE = re.compile(r"line\s+(\d+)")

#: 语法错误的诊断码，独立于类型检查（IFP1xxx）与 Lint（IFP2xxx）。
PARSE_ERROR_CODE = "IFP0001"


# ---------------------------------------------------------------------------
# JSON-RPC 分帧
# ---------------------------------------------------------------------------

def read_message(stream: BinaryIO) -> Optional[dict[str, Any]]:
    """读取一条 LSP 消息；流结束或帧非法时返回 ``None``。"""

    length: Optional[int] = None
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"content-length:"):
            try:
                length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                return None
    if length is None or length <= 0:
        return None
    body = stream.read(length)
    if body is None or len(body) < length:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    """按 Content-Length 分帧写出一条 LSP 消息。"""

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    stream.write(body)
    stream.flush()


def uri_to_path(uri: str) -> Path:
    """``file://`` URI -> 本地路径。"""

    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return Path(unquote(uri))
    return Path(unquote(parsed.path))


# ---------------------------------------------------------------------------
# 诊断转换
# ---------------------------------------------------------------------------

def _first_line(message: str) -> str:
    for line in message.splitlines():
        if line.strip():
            return line.strip()
    return message.strip()


def _parse_error_diagnostic(exc: IFPParseError) -> Diagnostic:
    """把一个解析异常转成 LSP 可用的诊断（带上 lark 报告的行号）。"""

    match = _LINE_RE.search(exc.message)
    return Diagnostic(
        code=PARSE_ERROR_CODE,
        message=_first_line(exc.message),
        severity=Severity.ERROR,
        file=exc.filename,
        line=int(match.group(1)) if match else 0,
    )


def to_lsp_diagnostic(diag: Diagnostic, text: str) -> dict[str, Any]:
    """把一条 :class:`Diagnostic` 转成 LSP 诊断（诊断只有行号，故整行高亮）。"""

    lines = text.splitlines() or [""]
    index = max(0, min(diag.line - 1 if diag.line > 0 else 0, len(lines) - 1))
    return {
        "range": {
            "start": {"line": index, "character": 0},
            "end": {"line": index, "character": len(lines[index])},
        },
        "severity": _SEVERITY_CODE.get(diag.severity, 1),
        "code": diag.code,
        "source": "ifp",
        "message": diag.message,
    }


def _root_path(params: dict[str, Any]) -> Optional[Path]:
    """从 initialize 参数里取工作区根目录。"""

    for key in ("rootUri", "rootPath"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return uri_to_path(value) if value.startswith("file:") else Path(value)
    folders = params.get("workspaceFolders") or []
    if isinstance(folders, list) and folders:
        uri = folders[0].get("uri") if isinstance(folders[0], dict) else None
        if isinstance(uri, str) and uri:
            return uri_to_path(uri)
    return None


# ---------------------------------------------------------------------------
# 结构大纲（DocumentSymbol）
# ---------------------------------------------------------------------------

#: LSP SymbolKind 取值（仅列出用到的）。
_SYMBOL_KIND = {
    "context": 3,       # Namespace
    "value": 5,         # Class
    "intent": 5,        # Class
    "contract": 11,     # Interface
    "field": 8,         # Field
    "state": 10,        # Enum
    "state_value": 22,  # EnumMember
    "action": 6,        # Method
    "invariant": 7,     # Property
    "scenario": 12,     # Function
}


def _symbol(
    name: str,
    kind: str,
    line: int,
    detail: Optional[str] = None,
    children: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """构造一个 DocumentSymbol（诊断只带行号，故 range 收敛为单行）。"""

    point = {"line": max(0, line - 1), "character": 0}
    symbol: dict[str, Any] = {
        "name": name,
        "kind": _SYMBOL_KIND[kind],
        "range": {"start": dict(point), "end": dict(point)},
        "selectionRange": {"start": dict(point), "end": dict(point)},
    }
    if detail is not None:
        symbol["detail"] = detail
    if children:
        symbol["children"] = children
    return symbol


def _field_symbol(field: FieldDecl) -> dict[str, Any]:
    return _symbol(field.name, "field", field.line, detail=str(field.type))


def _invariant_symbol(invariant: InvariantDecl) -> dict[str, Any]:
    return _symbol(invariant.name, "invariant", invariant.line, detail="baseline"
                   if invariant.baseline else None)


def _value_symbol(value: ValueDecl) -> dict[str, Any]:
    children = [_field_symbol(f) for f in value.fields]
    children.extend(_invariant_symbol(i) for i in value.invariants)
    return _symbol(value.name, "value", value.line, children=children)


def _intent_symbol(intent: IntentDecl) -> dict[str, Any]:
    children = [_field_symbol(f) for f in intent.fields]
    for state in intent.states:
        children.append(
            _symbol(
                state.name,
                "state",
                state.line,
                children=[_symbol(v, "state_value", state.line) for v in state.values],
            )
        )
    children.extend(_symbol(a.name, "action", a.line) for a in intent.actions)
    children.extend(_invariant_symbol(i) for i in intent.invariants)
    return _symbol(intent.name, "intent", intent.line, children=children)


def _contract_symbol(contract: ContractDecl) -> dict[str, Any]:
    children = [_invariant_symbol(i) for i in contract.invariants]
    return _symbol(contract.name, "contract", contract.line, children=children)


def document_symbols(model: Any) -> list[dict[str, Any]]:
    """把模型转成 LSP DocumentSymbol 树，供编辑器结构大纲使用。"""

    symbols: list[dict[str, Any]] = []
    for ctx in model.contexts:
        children: list[dict[str, Any]] = []
        children.extend(_value_symbol(v) for v in ctx.values)
        children.extend(_intent_symbol(i) for i in ctx.intents)
        children.extend(_contract_symbol(c) for c in ctx.contracts)
        symbols.append(_symbol(ctx.name, "context", ctx.line, children=children))
    symbols.extend(_symbol(s.name, "scenario", s.line) for s in model.scenarios)
    return symbols


# ---------------------------------------------------------------------------
# 跳转（Definition）
# ---------------------------------------------------------------------------

_IDENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)


def _token_keys(text: str, line: int, character: int) -> list[str]:
    """取光标处的标识符，返回可查询的候选键（限定名优先，如 ``State::Member``）。"""

    rows = text.splitlines()
    if not 0 <= line < len(rows):
        return []
    row = rows[line]
    if not row:
        return []

    index = min(max(character, 0), len(row))
    if index == len(row) or row[index] not in _IDENT_CHARS:
        index -= 1
    if index < 0 or row[index] not in _IDENT_CHARS:
        return []

    start = index
    while start > 0 and row[start - 1] in _IDENT_CHARS:
        start -= 1
    end = index
    while end < len(row) and row[end] in _IDENT_CHARS:
        end += 1
    word = row[start:end]

    keys: list[str] = []
    if start >= 2 and row[start - 2:start] == "::":
        prefix_end = start - 2
        prefix_start = prefix_end
        while prefix_start > 0 and row[prefix_start - 1] in _IDENT_CHARS:
            prefix_start -= 1
        enum = row[prefix_start:prefix_end]
        if enum:
            keys.append(f"{enum}::{word}")
    keys.append(word)
    return keys


def _iter_declarations(model: Model):
    """遍历模型中所有“可被引用”的声明，产出 ``(名称, 行号)``。"""

    for ctx in model.contexts:
        yield ctx.name, ctx.line
        for value in ctx.values:
            yield value.name, value.line
            for field in value.fields:
                yield field.name, field.line
            for inv in value.invariants:
                yield inv.name, inv.line
        for intent in ctx.intents:
            yield intent.name, intent.line
            for field in intent.fields:
                yield field.name, field.line
            for state in intent.states:
                yield state.name, state.line
                for value in state.values:
                    yield f"{state.name}::{value}", state.line
            for inv in intent.invariants:
                yield inv.name, inv.line
            for action in intent.actions:
                yield action.name, action.line
        for contract in ctx.contracts:
            yield contract.name, contract.line
            for inv in contract.invariants:
                yield inv.name, inv.line
    for scenario in model.scenarios:
        yield scenario.name, scenario.line


def _location(uri: str, line: int) -> dict[str, Any]:
    """构造一个单行 Location（诊断/大纲只带行号，故 range 收敛为该行行首）。"""

    point = {"line": max(0, line - 1), "character": 0}
    return {"uri": uri, "range": {"start": dict(point), "end": dict(point)}}


# ---------------------------------------------------------------------------
# 快速修复（CodeAction）
# ---------------------------------------------------------------------------

_MISSING_TYPE_RE = re.compile(r"字段类型\s+([A-Za-z_][A-Za-z0-9_]*)\s+未定义")


def _diagnostic_line(diag: dict[str, Any]) -> int:
    """LSP 诊断的行号（0 起）转 1 起行号。"""

    start = (diag.get("range") or {}).get("start") or {}
    return int(start.get("line", 0)) + 1


def _indent_at(text: str, line: int) -> str:
    """返回第 ``line`` 行（1 起）的行首缩进。"""

    rows = text.splitlines()
    if not 1 <= line <= len(rows):
        return ""
    row = rows[line - 1]
    return row[: len(row) - len(row.lstrip())]


def _insert_after(text: str, header_line: int, new_text: str) -> Optional[dict[str, Any]]:
    """在以 ``{`` 结尾的块首行（``header_line``，1 起）之后插入 ``new_text``。"""

    rows = text.splitlines()
    if not 1 <= header_line <= len(rows):
        return None
    if not rows[header_line - 1].rstrip().endswith("{"):
        return None
    point = {"line": header_line, "character": 0}
    return {"range": {"start": dict(point), "end": dict(point)}, "newText": new_text}


def _block_close_line(text: str, header_line: int) -> Optional[int]:
    """返回块首行（1 起）所对应的闭合 ``}`` 所在行（1 起）。"""

    rows = text.splitlines()
    if not 1 <= header_line <= len(rows):
        return None
    base = _indent_at(text, header_line)
    for number in range(header_line + 1, len(rows) + 1):
        if rows[number - 1].strip() == "}" and len(_indent_at(text, number)) <= len(base):
            return number
    return None


def _find_invariant(model: Model, line: int) -> Optional[InvariantDecl]:
    for ctx in model.contexts:
        for value in ctx.values:
            for inv in value.invariants:
                if inv.line == line:
                    return inv
        for intent in ctx.intents:
            for inv in intent.invariants:
                if inv.line == line:
                    return inv
        for contract in ctx.contracts:
            for inv in contract.invariants:
                if inv.line == line:
                    return inv
    return None


def _find_intent(model: Model, line: int) -> Optional[IntentDecl]:
    for ctx in model.contexts:
        for intent in ctx.intents:
            if intent.line == line:
                return intent
    return None


def _enclosing_context(model: Model, line: int) -> Optional[ContextDecl]:
    """定位 ``line`` 所属的限界上下文（取最近的前置声明）。"""

    best: Optional[ContextDecl] = None
    for ctx in model.contexts:
        if ctx.line <= line and (best is None or ctx.line > best.line):
            best = ctx
    return best


def _action(title: str, diag: dict[str, Any], uri: str, edit: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """构造一个 quickfix CodeAction；编辑无法生成时返回 ``None``。"""

    if edit is None:
        return None
    return {
        "title": title,
        "kind": "quickfix",
        "diagnostics": [diag],
        "edit": {"changes": {uri: [edit]}},
    }


def _quick_fixes(
    diag: dict[str, Any], model: Optional[Model], text: str, uri: str
) -> list[dict[str, Any]]:
    """按诊断码生成快速修复（诊断无法自动修复时返回空列表）。"""

    code = diag.get("code")
    line = _diagnostic_line(diag)

    if code in (BASELINE_MISSING_OWNER, BASELINE_MISSING_RISK):
        if model is None or _find_invariant(model, line) is None:
            return []
        field = "owner" if code == BASELINE_MISSING_OWNER else "risk"
        edit = _insert_after(text, line, f'{_indent_at(text, line)}    {field}: "TODO"\n')
        action = _action(f"补充基线不变量 {field}", diag, uri, edit)
        return [action] if action else []

    if code == MISSING_INITIAL_STATE:
        intent = _find_intent(model, line) if model is not None else None
        state = next((s for s in intent.states if s.values), None) if intent else None
        if intent is None or state is None:
            return []
        indent = _indent_at(text, intent.line) + "    "
        edit = _insert_after(text, intent.line, f"{indent}initial: {state.name}::{state.values[0]}\n")
        action = _action("声明初始状态 initial", diag, uri, edit)
        return [action] if action else []

    if code == UNKNOWN_FIELD_TYPE:
        match = _MISSING_TYPE_RE.search(str(diag.get("message", "")))
        ctx = _enclosing_context(model, line) if model is not None else None
        close = _block_close_line(text, ctx.line) if ctx is not None else None
        if match is None or ctx is None or close is None:
            return []
        indent = _indent_at(text, ctx.line) + "    "
        name = match.group(1)
        new_text = (
            f"{indent}value {name} {{\n"
            f"{indent}    // TODO: 补充字段与不变量\n"
            f"{indent}    field value: String\n"
            f"{indent}}}\n"
        )
        point = {"line": close - 1, "character": 0}
        edit = {"range": {"start": dict(point), "end": dict(point)}, "newText": new_text}
        action = _action(f"定义值对象 {name}", diag, uri, edit)
        return [action] if action else []

    return []


# ---------------------------------------------------------------------------
# 服务器
# ---------------------------------------------------------------------------

class LanguageServer:
    """把 IFP 诊断接入 LSP。

    维护两个文本来源：编辑器已打开的文档（内存为准）与工作区磁盘上的 `.ifp` 文件。
    每次重算都把两者合并成一个项目模型，这样 Context / Intent / Scenario 分散在多个
    文件时也能解析到相互引用。
    """

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        self.workspace_root = workspace_root
        self.documents: dict[str, str] = {}
        self.exited = False

    # -- 消息分发 -----------------------------------------------------------
    def handle(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """处理一条消息，返回需要写回的消息列表。"""

        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            root = _root_path(params)
            if root is not None:
                self.workspace_root = root
            return [self._response(msg_id, {
                "capabilities": {
                    "textDocumentSync": 1,
                    "documentSymbolProvider": True,
                    "definitionProvider": True,
                    "codeActionProvider": True,
                },
                "serverInfo": {"name": "ifp-lsp", "version": __version__},
            })]
        if method == "initialized":
            return []
        if method == "shutdown":
            return [self._response(msg_id, None)]
        if method == "exit":
            self.exited = True
            return []
        if method == "textDocument/didOpen":
            self._open(params)
            return self._publish_diagnostics()
        if method == "textDocument/didChange":
            self._change(params)
            return self._publish_diagnostics()
        if method == "textDocument/didClose":
            self.documents.pop(self._uri(params), None)
            uri = self._uri(params)
            return [self._notification(
                "textDocument/publishDiagnostics", {"uri": uri, "diagnostics": []}
            )]
        if method == "textDocument/documentSymbol":
            return [self._response(msg_id, self._document_symbols(params))]
        if method == "textDocument/definition":
            return [self._response(msg_id, self._definition(params))]
        if method == "textDocument/codeAction":
            return [self._response(msg_id, self._code_actions(params))]
        if msg_id is not None:
            return [self._error(msg_id, -32601, f"不支持的方法：{method}")]
        return []

    # -- 文档维护 -----------------------------------------------------------
    @staticmethod
    def _uri(params: dict[str, Any]) -> str:
        doc = params.get("textDocument") or {}
        return str(doc.get("uri", ""))

    def _open(self, params: dict[str, Any]) -> None:
        uri = self._uri(params)
        doc = params.get("textDocument") or {}
        if uri:
            self.documents[uri] = str(doc.get("text", ""))

    def _change(self, params: dict[str, Any]) -> None:
        """全量同步：取最后一次 contentChanges 的文本（能力里声明 textDocumentSync=1）。"""

        uri = self._uri(params)
        if not uri:
            return
        changes = params.get("contentChanges") or []
        if changes:
            self.documents[uri] = str(changes[-1].get("text", ""))
        else:
            self.documents.setdefault(uri, "")

    def _entries(self) -> list[tuple[str, Path, str]]:
        """列出参与分析的文件：先已打开文档，再工作区磁盘文件（去重）。"""

        entries: list[tuple[str, Path, str]] = []
        seen: set[str] = set()
        for uri, text in self.documents.items():
            path = uri_to_path(uri)
            entries.append((uri, path, text))
            seen.add(str(path))
        if self.workspace_root is not None and self.workspace_root.is_dir():
            for path in sorted(self.workspace_root.rglob("*.ifp")):
                if str(path) in seen:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                entries.append((path.as_uri(), path, text))
        return entries

    # -- 结构大纲 -----------------------------------------------------------
    def _document_symbols(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """计算某个文档的结构大纲；文本优先取编辑器内存，其次取磁盘。"""

        uri = self._uri(params)
        text = self.documents.get(uri)
        if text is None:
            try:
                text = uri_to_path(uri).read_text(encoding="utf-8")
            except OSError:
                return []
        try:
            model = parser.parse(text, filename=uri_to_path(uri).name)
        except IFPParseError:
            return []
        return document_symbols(model)

    # -- 跳转与快速修复 -----------------------------------------------------
    def _text_of(self, uri: str) -> Optional[str]:
        """取文档文本：优先编辑器内存，其次磁盘；都取不到时返回 ``None``。"""

        text = self.documents.get(uri)
        if text is not None:
            return text
        try:
            return uri_to_path(uri).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None

    def _declaration_index(self) -> dict[str, list[tuple[str, int]]]:
        """扫描已打开文档与工作区文件，建立「声明名 -> [来源 URI, 行号]」索引。"""

        index: dict[str, list[tuple[str, int]]] = {}
        for uri, path, text in self._entries():
            try:
                model = parser.parse(text, filename=str(path))
            except IFPParseError:
                continue
            for name, line in _iter_declarations(model):
                index.setdefault(name, []).append((uri, line))
        return index

    def _definition(self, params: dict[str, Any]) -> Any:
        """返回光标处标识符的声明位置（跨文件）；找不到时返回 ``None``。"""

        uri = self._uri(params)
        text = self._text_of(uri)
        if text is None:
            return None
        position = params.get("position") or {}
        keys = _token_keys(
            text, int(position.get("line", 0)), int(position.get("character", 0))
        )
        if not keys:
            return None
        index = self._declaration_index()
        for key in keys:
            locations = index.get(key)
            if locations:
                return [_location(loc_uri, line) for loc_uri, line in locations]
        return None

    def _code_actions(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """根据请求携带的诊断生成快速修复列表。"""

        uri = self._uri(params)
        text = self._text_of(uri)
        if text is None:
            return []
        diagnostics = (params.get("context") or {}).get("diagnostics") or []
        if not diagnostics:
            return []
        try:
            model: Optional[Model] = parser.parse(text, filename=uri_to_path(uri).name)
        except IFPParseError:
            model = None

        actions: list[dict[str, Any]] = []
        for diag in diagnostics:
            if isinstance(diag, dict):
                actions.extend(_quick_fixes(diag, model, text, uri))
        return actions

    # -- 诊断计算 -----------------------------------------------------------
    def collect(self) -> dict[str, list[dict[str, Any]]]:
        """计算所有已知文件的诊断，按 URI 归组。"""

        entries = self._entries()
        per_uri: dict[str, list[Diagnostic]] = {uri: [] for uri, _, _ in entries}
        parsed: list[tuple[str, Any]] = []

        for uri, path, text in entries:
            try:
                model = parser.parse(text, filename=str(path))
            except IFPParseError as exc:
                per_uri[uri].append(_parse_error_diagnostic(exc))
                continue
            parsed.append((uri, model))

        if parsed:
            project = parser.merge([m for _, m in parsed], source="<workspace>")
            for uri, model in parsed:
                diags = list(typecheck(model, project=project))
                diags.extend(run_lint(model, extra_rules=plugin.lint_rules()))
                per_uri[uri].extend(diags)

        texts = {uri: text for uri, _, text in entries}
        return {
            uri: [to_lsp_diagnostic(d, texts[uri]) for d in diags]
            for uri, diags in per_uri.items()
        }

    def _publish_diagnostics(self) -> list[dict[str, Any]]:
        """只为已打开的文档推送诊断（工作区其他文件仅用于跨文件解析）。"""

        computed = self.collect()
        return [
            self._notification(
                "textDocument/publishDiagnostics",
                {"uri": uri, "diagnostics": computed.get(uri, [])},
            )
            for uri in self.documents
        ]

    # -- 消息构造 -----------------------------------------------------------
    @staticmethod
    def _response(msg_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "method": method, "params": params}


def serve(stdin: BinaryIO, stdout: BinaryIO, workspace_root: Optional[Path] = None) -> int:
    """跑 LSP 主循环，直到收到 ``exit`` 或输入结束。"""

    server = LanguageServer(workspace_root)
    while True:
        message = read_message(stdin)
        if message is None:
            return 0
        for outgoing in server.handle(message):
            write_message(stdout, outgoing)
        if server.exited:
            return 0


def main() -> int:
    return serve(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
