"""制品导出测试。"""

from __future__ import annotations

import json

from ifp import parser
from ifp.exporter import to_json, to_markdown, to_openapi, to_protobuf


def test_json_export_is_structured(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    data = json.loads(to_json(model))

    assert data["contexts"][0]["name"] == "RefundContext"
    intent = data["contexts"][0]["intents"][0]
    assert intent["name"] == "RefundOrder"
    assert {i["name"] for i in intent["invariants"]} >= {"totalRefundNotExceedPaid"}


def test_openapi_export_contains_paths(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    doc = json.loads(to_openapi(model))

    assert doc["openapi"].startswith("3.0")
    assert any("refund-order" in p for p in doc["paths"])
    assert "RefundOrder" in doc["components"]["schemas"]


def test_markdown_export_mentions_baseline(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    text = to_markdown(model)

    assert "# IFP 模型报表" in text
    assert "totalRefundNotExceedPaid" in text


def test_protobuf_export_maps_decimal_to_string(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    text = to_protobuf(model)

    assert "syntax = \"proto3\";" in text
    assert "enum RefundStatus" in text
    # Decimal 映射为 string，避免资金精度丢失
    assert "string orderPaidAmount" in text


def test_context_filter(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    text = to_protobuf(model, context="OtherContext")

    assert "RefundContext" not in text
