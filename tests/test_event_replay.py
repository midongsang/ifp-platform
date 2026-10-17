"""事件声明与事件重放测试。"""

from __future__ import annotations

from decimal import Decimal

from ifp import parser
from ifp.event_replay import replay_events
from ifp.typecheck import typecheck

REFUND_MODEL = """
context RefundContext {
    intent RefundOrder {
        field orderPaidAmount: Decimal
        field totalRefundedAmount: Decimal = 0
        field status: RefundStatus = RefundStatus::Pending

        state RefundStatus { Pending, Approved, Refunded, Rejected }
        initial: RefundStatus::Pending

        invariant totalRefundNotExceedPaid {
            expr: totalRefundedAmount <= orderPaidAmount
            baseline: true
        }
        invariant refundAmountNotNegative {
            expr: totalRefundedAmount >= 0
        }

        action Audit {
            transition: RefundStatus::Approved
            pre: status == RefundStatus::Pending
            post: status == RefundStatus::Approved
        }

        action ExecuteRefund(amount: Decimal) {
            transition: RefundStatus::Refunded
            pre: status == RefundStatus::Approved && amount >= 0
            post: status == RefundStatus::Refunded
                  && totalRefundedAmount == old(totalRefundedAmount) + amount
        }
    }

    event Audited {
        description: "退款单已审核"
        produced_by: Audit
    }

    event RefundExecuted {
        description: "退款资金已执行"
        field amount: Decimal
        produced_by: ExecuteRefund
    }

    event StatusOverridden {
        field status: RefundStatus
    }
}
"""


def _payload(**overrides) -> dict:
    base = {
        "context": "RefundContext",
        "intent": "RefundOrder",
        "initial": {
            "orderPaidAmount": 100,
            "totalRefundedAmount": 0,
            "status": "RefundStatus::Pending",
        },
        "events": [
            {"type": "Audited"},
            {"type": "RefundExecuted", "data": {"amount": 40}},
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def test_event_declaration_parses() -> None:
    model = parser.parse(REFUND_MODEL)
    ctx = model.contexts[0]
    assert [e.name for e in ctx.events] == ["Audited", "RefundExecuted", "StatusOverridden"]

    audited = next(e for e in ctx.events if e.name == "Audited")
    assert audited.produced_by == "Audit"
    assert audited.fields == ()

    executed = next(e for e in ctx.events if e.name == "RefundExecuted")
    assert executed.produced_by == "ExecuteRefund"
    assert [(f.name, str(f.type)) for f in executed.fields] == [("amount", "Decimal")]

    overridden = next(e for e in ctx.events if e.name == "StatusOverridden")
    assert overridden.produced_by is None
    assert [(f.name, str(f.type)) for f in overridden.fields] == [("status", "RefundStatus")]


# ---------------------------------------------------------------------------
# 类型检查
# ---------------------------------------------------------------------------

def test_event_typecheck_ok() -> None:
    model = parser.parse(REFUND_MODEL)
    diags = typecheck(model)
    errors = [d for d in diags if d.severity.value == "error"]
    assert errors == [], [d.format() for d in errors]


def test_event_unknown_produced_by_is_error() -> None:
    src = REFUND_MODEL.replace(
        "        produced_by: Audit",
        "        produced_by: NoSuchAction",
    )
    model = parser.parse(src)
    diags = typecheck(model)
    codes = {d.code for d in diags}
    assert "IFP1040" in codes


def test_event_field_mismatch_is_warning() -> None:
    src = REFUND_MODEL.replace(
        "        field amount: Decimal\n        produced_by: ExecuteRefund",
        "        field wrongName: Decimal\n        produced_by: ExecuteRefund",
    )
    model = parser.parse(src)
    diags = typecheck(model)
    codes = {d.code for d in diags}
    assert "IFP1041" in codes


# ---------------------------------------------------------------------------
# 重放
# ---------------------------------------------------------------------------

def test_replay_valid_sequence_passes() -> None:
    model = parser.parse(REFUND_MODEL)
    result = replay_events(model, _payload())

    assert result.passed
    assert len(result.steps) == 2
    assert result.steps[0].event_type == "Audited"
    assert result.steps[1].event_type == "RefundExecuted"
    assert result.final_state["totalRefundedAmount"] == Decimal("40")
    assert result.final_state["status"] == "RefundStatus::Refunded"


def test_replay_invariant_violation_detected() -> None:
    model = parser.parse(REFUND_MODEL)
    payload = _payload(
        events=[
            {"type": "Audited"},
            {"type": "RefundExecuted", "data": {"amount": 150}},
        ]
    )
    result = replay_events(model, payload)

    assert not result.passed
    assert "totalRefundNotExceedPaid" in result.message
    assert result.steps[-1].violations == ["totalRefundNotExceedPaid"]


def test_replay_unknown_event_type_fails() -> None:
    model = parser.parse(REFUND_MODEL)
    payload = _payload(events=[{"type": "NeverHappened"}])
    result = replay_events(model, payload)

    assert not result.passed
    assert "未定义的事件" in result.message


def test_replay_missing_context_intent_fails() -> None:
    model = parser.parse(REFUND_MODEL)
    result = replay_events(model, {"events": []})
    assert not result.passed
    assert "context" in result.message or "intent" in result.message


def test_replay_direct_field_event_without_produced_by() -> None:
    """无 produced_by 的事件按字段名直接写入状态。"""
    model = parser.parse(REFUND_MODEL)
    payload = _payload(
        events=[
            {"type": "StatusOverridden", "data": {"status": "RefundStatus::Rejected"}},
        ]
    )
    result = replay_events(model, payload)

    assert result.passed
    assert result.final_state["status"] == "RefundStatus::Rejected"


def test_replay_uses_intent_defaults_when_no_initial() -> None:
    """未提供 initial 时，使用 intent 字段默认值构建初始状态。"""
    model = parser.parse(REFUND_MODEL)
    payload = {
        "context": "RefundContext",
        "intent": "RefundOrder",
        "initial": {"orderPaidAmount": 50},
        "events": [],
    }
    result = replay_events(model, payload)

    assert result.passed
    # totalRefundedAmount 默认 0，status 默认 Pending
    assert result.final_state["totalRefundedAmount"] == Decimal("0")
    assert result.final_state["status"] == "RefundStatus::Pending"


def test_replay_empty_events_passes() -> None:
    model = parser.parse(REFUND_MODEL)
    result = replay_events(model, _payload(events=[]))
    assert result.passed
    assert result.steps == []


def test_replay_step_results_are_immutable_snapshots() -> None:
    model = parser.parse(REFUND_MODEL)
    result = replay_events(model, _payload())
    # 每步保存独立状态副本，后续步骤不影响前一步快照
    assert result.steps[0].state_after["status"] == "RefundStatus::Approved"
    assert result.steps[1].state_after["status"] == "RefundStatus::Refunded"
