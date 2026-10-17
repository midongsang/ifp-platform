"""Web 编辑器测试。

覆盖：接口逻辑（分析 / 可视化 / 属性测试）与真实的 HTTP 往返（页面、静态资源、
``/api/analyze``、越权路径的 404），以及 CLI 上的 ``ifp web`` 入口。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from typer.testing import CliRunner

from ifp import web
from ifp.cli import app

runner = CliRunner()

VALID_MODEL = """context WalletContext {
    description: "钱包限界上下文"

    intent Wallet {
        field balance: Decimal = 0
        field status: WalletStatus = WalletStatus::Active

        state WalletStatus { Active, Frozen }
        initial: WalletStatus::Active

        invariant balanceNonNegative {
            expr: balance >= 0
            baseline: true
            owner: "finance-team"
        }

        action Freeze {
            transition: WalletStatus::Frozen
            pre: status == WalletStatus::Active
            post: status == WalletStatus::Frozen
        }
    }
}
"""

BUGGY_MODEL = """context WalletContext {
    intent Wallet {
        field balance: Decimal = 0

        invariant balanceWithinLimit {
            expr: balance <= 500
        }

        action Deposit(amount: Decimal) {
            pre: amount >= 0
            post: balance == old(balance) + amount
        }
    }
}
"""

BAD_SYNTAX = "context Broken { not valid }"


# ---------------------------------------------------------------------------
# 接口逻辑
# ---------------------------------------------------------------------------

def test_analyze_reports_clean_model_with_structure() -> None:
    result = web.analyze(VALID_MODEL)

    assert result["parseError"] is None
    assert [d for d in result["diagnostics"] if d["severity"] == "error"] == []

    intent = result["model"]["contexts"][0]["intents"][0]
    machine = intent["stateMachine"]
    assert machine["values"] == ["Active", "Frozen"]
    assert {"from": "Active", "action": "Freeze", "to": "Frozen"} in machine["transitions"]
    assert intent["invariants"][0]["baseline"] is True


def test_analyze_reports_parse_error() -> None:
    result = web.analyze(BAD_SYNTAX)

    assert result["model"] is None
    assert result["parseError"]
    assert result["diagnostics"][0]["code"] == "IFP0001"
    assert result["diagnostics"][0]["severity"] == "error"


def test_verification_finds_counterexample() -> None:
    result = web.run_verification(BUGGY_MODEL, mode="single")

    assert result["parseError"] is None
    assert result["counterexamples"], "应当找到超过余额上限的反例"
    ce = result["counterexamples"][0]
    assert ce["invariant"] == "balanceWithinLimit"
    assert ce["describe"]


# ---------------------------------------------------------------------------
# HTTP 往返
# ---------------------------------------------------------------------------

def _with_server(func) -> None:
    server = web.serve("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        func(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_serves_editor_page() -> None:
    def check(base: str) -> None:
        with urllib.request.urlopen(base + "/") as response:
            assert response.status == 200
            html = response.read().decode("utf-8")
        assert "IFP 模型编辑器" in html

        with urllib.request.urlopen(base + "/style.css") as response:
            assert response.headers["Content-Type"].startswith("text/css")

    _with_server(check)


def test_http_analyze_round_trip() -> None:
    def check(base: str) -> None:
        body = json.dumps({"source": VALID_MODEL, "filename": "wallet.ifp"}).encode("utf-8")
        request = urllib.request.Request(
            base + "/api/analyze", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))

        assert payload["model"]["contexts"][0]["name"] == "WalletContext"
        assert [d for d in payload["diagnostics"] if d["severity"] == "error"] == []

    _with_server(check)


def test_http_rejects_unknown_asset() -> None:
    def check(base: str) -> None:
        try:
            urllib.request.urlopen(base + "/etc/passwd")
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:  # pragma: no cover - 只有越权成功才会走到
            raise AssertionError("未知资源应返回 404")

    _with_server(check)


def test_cli_web_command_is_registered() -> None:
    result = runner.invoke(app, ["web", "--help"])

    assert result.exit_code == 0
    assert "编辑器" in result.output
