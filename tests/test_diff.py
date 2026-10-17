"""语义 Diff 与基线治理测试。"""

from __future__ import annotations

from ifp import parser
from ifp.baseline import approval_required, baseline_invariants, risk_score
from ifp.diff import diff_models

_OLD = """
context DemoContext {
    intent Demo {
        field x: Decimal = 0
        invariant bound {
            expr: x <= 10
            baseline: true
            owner: "team"
            risk: "超限"
        }
        action Set {
            pre: true
            post: x == 1
        }
    }
}
"""

_NEW = """
context DemoContext {
    intent Demo {
        field x: Decimal = 0
        invariant bound {
            expr: x <= 20
            baseline: true
            owner: "team"
            risk: "超限"
        }
        action Set {
            pre: true
            post: x == 1
        }
    }
}
"""


def test_diff_detects_invariant_change() -> None:
    old = parser.parse(_OLD)
    new = parser.parse(_NEW)
    result = diff_models(old, new)

    assert result.changes
    changed = result.changes[0]
    assert "invariant bound" in changed.path
    assert changed.baseline is True


def test_baseline_change_requires_approval() -> None:
    old = parser.parse(_OLD)
    new = parser.parse(_NEW)
    result = diff_models(old, new)

    assert risk_score(result) > 0
    assert approval_required(result) is True


def test_identical_models_have_no_changes() -> None:
    model = parser.parse(_OLD)
    result = diff_models(model, model)

    assert result.changes == []
    assert approval_required(result) is False


def test_baseline_invariants_listing(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    refs = baseline_invariants(model)

    assert len(refs) == 1
    ref = refs[0]
    assert ref.intent == "RefundOrder"
    assert ref.invariant == "totalRefundNotExceedPaid"
    assert ref.owner == "finance-team"
