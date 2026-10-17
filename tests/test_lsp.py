"""LSP 语言服务器测试。

覆盖：JSON-RPC 分帧、initialize 能力、didOpen/didChange/didClose 的诊断推送、
跨文件引用解析，以及 CLI 上的 ``ifp lsp`` 入口。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from ifp import lsp
from ifp.cli import app

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
runner = CliRunner()

VALID_MODEL = """context DemoContext {
    description: "示例限界上下文"

    intent Demo {
        field amount: Decimal = 0
        field status: DemoStatus = DemoStatus::Created

        state DemoStatus { Created, Done }
        initial: DemoStatus::Created

        invariant amountNonNegative {
            expr: amount >= 0
        }

        action Finish {
            transition: DemoStatus::Done
            pre: status == DemoStatus::Created
            post: status == DemoStatus::Done
        }
    }
}
"""

BAD_SYNTAX = "context Broken { not valid }"

UNKNOWN_TYPE_MODEL = """context DemoContext {
    intent Demo {
        field thing: UnknownType
    }
}
"""


def _did_open(uri: str, text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {"uri": uri, "languageId": "ifp", "version": 1, "text": text}
        },
    }


def _did_change(uri: str, text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "textDocument/didChange",
        "params": {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": text}],
        },
    }


def _diagnostics(messages: list[dict[str, Any]], uri: str) -> list[dict[str, Any]]:
    for message in messages:
        if message.get("method") != "textDocument/publishDiagnostics":
            continue
        params = message["params"]
        if params["uri"] == uri:
            return params["diagnostics"]
    raise AssertionError(f"没有为该文档推送诊断：{uri}")


# ---------------------------------------------------------------------------
# 分帧
# ---------------------------------------------------------------------------

def test_framing_round_trip() -> None:
    buffer = io.BytesIO()
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}

    lsp.write_message(buffer, payload)
    buffer.seek(0)

    assert lsp.read_message(buffer) == payload
    assert lsp.read_message(buffer) is None


def test_framing_rejects_truncated_body() -> None:
    buffer = io.BytesIO(b"Content-Length: 100\r\n\r\n{\"a\": 1}")

    assert lsp.read_message(buffer) is None


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

def test_initialize_reports_capabilities() -> None:
    server = lsp.LanguageServer()
    messages = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"rootUri": "file:///tmp"}}
    )

    result = messages[0]["result"]
    assert result["capabilities"]["textDocumentSync"] == 1
    assert result["serverInfo"]["name"] == "ifp-lsp"
    assert server.workspace_root == Path("/tmp")


def test_shutdown_and_exit_end_the_loop() -> None:
    server = lsp.LanguageServer()

    assert server.handle({"jsonrpc": "2.0", "id": 2, "method": "shutdown"})[0]["result"] is None
    assert server.exited is False
    assert server.handle({"jsonrpc": "2.0", "method": "exit"}) == []
    assert server.exited is True


# ---------------------------------------------------------------------------
# 诊断
# ---------------------------------------------------------------------------

def test_did_open_reports_syntax_error() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/bad.ifp"

    diags = _diagnostics(server.handle(_did_open(uri, BAD_SYNTAX)), uri)

    assert len(diags) == 1
    assert diags[0]["code"] == lsp.PARSE_ERROR_CODE
    assert diags[0]["severity"] == 1
    assert diags[0]["source"] == "ifp"
    assert diags[0]["range"]["start"]["line"] >= 0


def test_did_open_valid_model_is_clean() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"

    assert _diagnostics(server.handle(_did_open(uri, VALID_MODEL)), uri) == []


def test_unknown_custom_type_is_reported() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/unknown.ifp"

    diags = _diagnostics(server.handle(_did_open(uri, UNKNOWN_TYPE_MODEL)), uri)

    # Lint 也会给提示（如 IFP2005），此处只关心类型检查的错误。
    assert [d["code"] for d in diags if d["severity"] == 1] == ["IFP1011"]
    assert diags[0]["message"]


def test_did_change_clears_previous_diagnostics() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"

    server.handle(_did_open(uri, BAD_SYNTAX))
    messages = server.handle(_did_change(uri, VALID_MODEL))

    assert _diagnostics(messages, uri) == []


def test_did_close_clears_diagnostics() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"
    server.handle(_did_open(uri, BAD_SYNTAX))

    messages = server.handle(
        {"jsonrpc": "2.0", "method": "textDocument/didClose", "params": {"textDocument": {"uri": uri}}}
    )

    assert _diagnostics(messages, uri) == []


def test_scenario_file_resolves_context_from_workspace() -> None:
    """只打开场景文件，也要能解析到同目录其他文件里的 Context / Intent。"""

    root = EXAMPLES / "refund"
    scenario_file = next(p for p in sorted(root.glob("*.ifp")) if "scenario" in p.name)
    uri = scenario_file.as_uri()

    server = lsp.LanguageServer(root)
    diags = _diagnostics(
        server.handle(_did_open(uri, scenario_file.read_text(encoding="utf-8"))), uri
    )

    # 示例场景本身带 info 级 Lint 提示（IFP2007），只要没有 error 即说明跨文件解析成功。
    assert [d for d in diags if d["severity"] == 1] == []


# ---------------------------------------------------------------------------
# 结构大纲
# ---------------------------------------------------------------------------

def _request(server: lsp.LanguageServer, method: str, uri: str, msg_id: int) -> Any:
    messages = server.handle(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": {"textDocument": {"uri": uri}},
        }
    )
    return messages[0]["result"]


def test_initialize_advertises_document_symbols() -> None:
    server = lsp.LanguageServer()

    result = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )[0]["result"]

    assert result["capabilities"]["documentSymbolProvider"] is True


def test_document_symbol_outlines_the_aggregate() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"
    server.handle(_did_open(uri, VALID_MODEL))

    symbols = _request(server, "textDocument/documentSymbol", uri, 3)

    assert symbols[0]["name"] == "DemoContext"
    intent = next(child for child in symbols[0]["children"] if child["name"] == "Demo")
    child_names = {child["name"] for child in intent["children"]}

    assert {"amount", "status", "DemoStatus", "Finish", "amountNonNegative"} <= child_names
    state = next(child for child in intent["children"] if child["name"] == "DemoStatus")
    assert [v["name"] for v in state["children"]] == ["Created", "Done"]


def test_document_symbol_is_empty_on_syntax_error() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/bad.ifp"
    server.handle(_did_open(uri, BAD_SYNTAX))

    assert _request(server, "textDocument/documentSymbol", uri, 4) == []


# ---------------------------------------------------------------------------
# 跳转（Definition）
# ---------------------------------------------------------------------------

BASELINE_MODEL = """context DemoContext {
    intent Demo {
        field amount: Decimal = 0

        invariant amountNonNegative {
            expr: amount >= 0
            baseline: true
        }
    }
}
"""

NO_INITIAL_MODEL = """context DemoContext {
    intent Demo {
        field status: DemoStatus

        state DemoStatus { Created, Done }
    }
}
"""


def _position(text: str, needle: str, offset: int = 0) -> dict[str, int]:
    for number, row in enumerate(text.splitlines()):
        column = row.find(needle)
        if column >= 0:
            return {"line": number, "character": column + offset}
    raise AssertionError(f"测试文本中没有 {needle!r}")


def _definition(server: lsp.LanguageServer, uri: str, position: dict[str, int]) -> Any:
    messages = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "textDocument/definition",
            "params": {"textDocument": {"uri": uri}, "position": position},
        }
    )
    return messages[0]["result"]


def test_initialize_advertises_definition_and_code_actions() -> None:
    result = lsp.LanguageServer().handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )[0]["result"]

    assert result["capabilities"]["definitionProvider"] is True
    assert result["capabilities"]["codeActionProvider"] is True


def test_definition_jumps_to_enum_member_declaration() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"
    server.handle(_did_open(uri, VALID_MODEL))

    locations = _definition(server, uri, _position(VALID_MODEL, "Created"))

    state_line = VALID_MODEL.splitlines().index("        state DemoStatus { Created, Done }")
    assert locations[0]["uri"] == uri
    assert locations[0]["range"]["start"]["line"] == state_line


def test_definition_jumps_to_action_declaration() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"
    server.handle(_did_open(uri, VALID_MODEL))

    locations = _definition(server, uri, _position(VALID_MODEL, "Finish"))

    action_line = VALID_MODEL.splitlines().index("        action Finish {")
    assert locations[0]["range"]["start"]["line"] == action_line


def test_definition_resolves_across_files() -> None:
    """场景文件里引用的 Intent 声明在同目录的模型文件中，跳转应跨文件定位。"""

    root = EXAMPLES / "refund"
    scenario_file = next(p for p in sorted(root.glob("*.ifp")) if "scenario" in p.name)
    model_file = root / "refund.ifp"

    server = lsp.LanguageServer(root)
    text = scenario_file.read_text(encoding="utf-8")
    server.handle(_did_open(scenario_file.as_uri(), text))

    locations = _definition(server, scenario_file.as_uri(), _position(text, "RefundOrder"))

    assert locations[0]["uri"] == model_file.as_uri()


def test_definition_returns_none_for_unknown_symbol() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"
    server.handle(_did_open(uri, VALID_MODEL))

    assert _definition(server, uri, _position(VALID_MODEL, "description")) is None


def test_definition_returns_none_on_non_identifier() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"
    server.handle(_did_open(uri, VALID_MODEL))

    assert _definition(server, uri, {"line": 2, "character": 0}) is None


# ---------------------------------------------------------------------------
# 快速修复（CodeAction）
# ---------------------------------------------------------------------------

def _code_actions(
    server: lsp.LanguageServer, uri: str, diagnostics: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    messages = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "textDocument/codeAction",
            "params": {
                "textDocument": {"uri": uri},
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
                "context": {"diagnostics": diagnostics},
            },
        }
    )
    return messages[0]["result"]


def _titles(actions: list[dict[str, Any]]) -> set[str]:
    return {action["title"] for action in actions}


def _apply_edits(text: str, edits: list[dict[str, Any]]) -> str:
    rows = text.splitlines(keepends=True)
    for edit in sorted(edits, key=lambda e: e["range"]["start"]["line"], reverse=True):
        rows.insert(edit["range"]["start"]["line"], edit["newText"])
    return "".join(rows)


def test_code_action_adds_missing_baseline_owner_and_risk() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/baseline.ifp"
    diags = _diagnostics(server.handle(_did_open(uri, BASELINE_MODEL)), uri)

    actions = _code_actions(server, uri, diags)

    assert {"补充基线不变量 owner", "补充基线不变量 risk"} <= _titles(actions)
    owner = next(a for a in actions if a["title"] == "补充基线不变量 owner")
    assert owner["kind"] == "quickfix"
    assert 'owner: "TODO"' in owner["edit"]["changes"][uri][0]["newText"]


def test_baseline_quick_fixes_clear_the_diagnostics() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/baseline.ifp"
    diags = _diagnostics(server.handle(_did_open(uri, BASELINE_MODEL)), uri)

    actions = _code_actions(server, uri, diags)
    edits = [edit for action in actions for edit in action["edit"]["changes"][uri]]
    fixed = _apply_edits(BASELINE_MODEL, edits)

    remaining = _diagnostics(lsp.LanguageServer().handle(_did_open(uri, fixed)), uri)
    assert remaining == []


def test_code_action_declares_initial_state() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/initial.ifp"
    diags = _diagnostics(server.handle(_did_open(uri, NO_INITIAL_MODEL)), uri)

    actions = _code_actions(server, uri, diags)

    action = next(a for a in actions if a["title"] == "声明初始状态 initial")
    assert "initial: DemoStatus::Created" in action["edit"]["changes"][uri][0]["newText"]


def test_code_action_defines_missing_value_object() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/unknown.ifp"
    diags = _diagnostics(server.handle(_did_open(uri, UNKNOWN_TYPE_MODEL)), uri)

    actions = _code_actions(server, uri, diags)

    action = next(a for a in actions if a["title"] == "定义值对象 UnknownType")
    assert "value UnknownType {" in action["edit"]["changes"][uri][0]["newText"]


def test_code_action_applied_fix_resolves_unknown_type() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/unknown.ifp"
    diags = _diagnostics(server.handle(_did_open(uri, UNKNOWN_TYPE_MODEL)), uri)

    edits = [edit for a in _code_actions(server, uri, diags) for edit in a["edit"]["changes"][uri]]
    fixed = _apply_edits(UNKNOWN_TYPE_MODEL, edits)

    remaining = _diagnostics(lsp.LanguageServer().handle(_did_open(uri, fixed)), uri)
    assert [d for d in remaining if d["severity"] == 1] == []


def test_code_action_without_diagnostics_is_empty() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"
    server.handle(_did_open(uri, VALID_MODEL))

    assert _code_actions(server, uri, []) == []


def test_code_action_skips_unfixable_diagnostic() -> None:
    server = lsp.LanguageServer()
    uri = "file:///tmp/demo.ifp"
    server.handle(_did_open(uri, VALID_MODEL))

    diag = {
        "code": "IFP2007",
        "message": "场景 HappyPath 未覆盖非法操作拦截（缺少 rejected 步骤）",
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
    }

    assert _code_actions(server, uri, [diag]) == []


# ---------------------------------------------------------------------------
# 协议细节与进程入口
# ---------------------------------------------------------------------------

def test_unknown_request_reports_method_not_found() -> None:
    server = lsp.LanguageServer()

    messages = server.handle({"jsonrpc": "2.0", "id": 9, "method": "textDocument/hover"})

    assert messages[0]["error"]["code"] == -32601


def test_serve_processes_a_session() -> None:
    stdin = io.BytesIO()
    for message in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        _did_open("file:///tmp/demo.ifp", VALID_MODEL),
        {"jsonrpc": "2.0", "method": "exit"},
    ):
        lsp.write_message(stdin, message)
    stdin.seek(0)

    stdout = io.BytesIO()
    assert lsp.serve(stdin, stdout) == 0

    stdout.seek(0)
    outgoing = []
    while True:
        message = lsp.read_message(stdout)
        if message is None:
            break
        outgoing.append(message)

    assert outgoing[0]["result"]["serverInfo"]["name"] == "ifp-lsp"
    assert _diagnostics(outgoing, "file:///tmp/demo.ifp") == []


def test_cli_lsp_command_accepts_stdio_flag() -> None:
    """ClI 入口必须接受编辑器默认的 ``ifp lsp --stdio``；空输入时直接退出。"""

    result = runner.invoke(app, ["lsp", "--stdio"])

    assert result.exit_code == 0
