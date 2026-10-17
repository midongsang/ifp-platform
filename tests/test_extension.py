"""VS Code 扩展与语言服务器的契约测试。

扩展本体必须在 VS Code 里才能运行，本仓库跑不了；这里校验它的清单与语法文件，
并确认它拉起语言服务器的方式与 CLI 上的 ``ifp lsp`` 入口一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXTENSION = Path(__file__).resolve().parents[1] / "editors" / "vscode"


def _load_json(relative: str) -> dict[str, Any]:
    text = (EXTENSION / relative).read_text(encoding="utf-8")
    return json.loads(text)


def test_manifest_registers_ifp_language() -> None:
    manifest = _load_json("package.json")
    languages = manifest["contributes"]["languages"]

    assert manifest["main"] == "./extension.js"
    assert "onLanguage:ifp" in manifest["activationEvents"]
    assert [lang["id"] for lang in languages] == ["ifp"]
    assert languages[0]["extensions"] == [".ifp"]


def test_manifest_defaults_to_the_ifp_lsp_command() -> None:
    """扩展默认以 ``ifp lsp --stdio`` 拉起服务器，必须与 CLI 入口一致。"""

    props = _load_json("package.json")["contributes"]["configuration"]["properties"]

    assert props["ifp.server.command"]["default"] == "ifp"
    assert props["ifp.server.args"]["default"] == ["lsp", "--stdio"]


def test_extension_is_a_language_client() -> None:
    manifest = _load_json("package.json")
    script = (EXTENSION / "extension.js").read_text(encoding="utf-8")

    assert "vscode-languageclient" in manifest["dependencies"]
    assert "vscode-languageclient" in script


def test_manifest_registers_verify_and_simulate_commands() -> None:
    """扩展应提供一键运行 `ifp verify` / `ifp simulate` 的命令。"""

    manifest = _load_json("package.json")
    script = (EXTENSION / "extension.js").read_text(encoding="utf-8")

    commands = {c["command"] for c in manifest["contributes"]["commands"]}
    assert {"ifp.verify", "ifp.simulate"} <= commands
    assert "ifp.verify" in script and "ifp.simulate" in script


def test_grammar_and_language_configuration_parse() -> None:
    grammar = _load_json("syntaxes/ifp.tmLanguage.json")
    config = _load_json("language-configuration.json")

    assert grammar["scopeName"] == "source.ifp"
    assert grammar["patterns"]
    assert config["comments"]["lineComment"] == "//"
