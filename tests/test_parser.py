"""parser 解析器测试：语法解析、文件加载、多文件合并。"""

from __future__ import annotations

import pytest

from ifp import ast, parser
from ifp.errors import IFPParseError


def test_parse_minimal_model(minimal_source: str) -> None:
    model = parser.parse(minimal_source)

    assert len(model.contexts) == 1
    ctx = model.contexts[0]
    assert ctx.name == "DemoContext"
    assert ctx.description == "最小示例"

    intent = ctx.intents[0]
    assert intent.name == "Demo"
    assert [f.name for f in intent.fields] == ["amount"]
    assert intent.initial == ast.EnumRef(enum="DemoStatus", member="Created")
    assert len(intent.invariants) == 1
    assert intent.invariants[0].name == "amountNonNegative"
    assert intent.actions[0].name == "Finish"


def test_parse_records_source_filename(minimal_source: str) -> None:
    model = parser.parse(minimal_source, filename="demo.ifp")

    assert model.source == "demo.ifp"


def test_parse_invalid_syntax_raises() -> None:
    with pytest.raises(IFPParseError):
        parser.parse("context Broken { this is not valid }", filename="broken.ifp")


def test_load_refund_example(refund_model_path) -> None:
    model = parser.load(refund_model_path)

    assert model.find_context("RefundContext") is not None
    assert model.find_intent("RefundOrder") is not None
    intent = model.find_intent("RefundOrder")
    assert intent is not None
    baseline = [i for i in intent.invariants if i.baseline]
    assert [i.name for i in baseline] == ["totalRefundNotExceedPaid"]


def test_merge_combines_contexts_and_scenarios(refund_dir) -> None:
    project = parser.load_project([refund_dir])

    # 目录下的 model.ifp 与 scenarios.ifp 合并后同时包含上下文与场景
    assert project.find_context("RefundContext") is not None
    assert len(project.scenarios) == 4


def test_merge_empty_project() -> None:
    merged = parser.merge([])
    assert merged.contexts == ()
    assert merged.scenarios == ()
