"""SMT 属性测试与一致性检查测试。"""

from __future__ import annotations

import z3

from ifp import parser
from ifp.smt import VerifyConfig, check_consistency, verify


def test_refund_example_finds_baseline_counterexample(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    report = verify(model, "RefundOrder", VerifyConfig(lower=0, upper=1000))

    assert report.counterexamples, "退款示例应能被 SMT 找到反例"
    ce = report.counterexamples[0]
    assert ce.action == "ExecuteRefund"
    assert ce.invariant == "totalRefundNotExceedPaid"
    assert ce.baseline is True
    # 反例应真实触发超额退款：动作后累计退款超过订单实付
    assert ce.args.get("amount")


def test_multi_step_counterexample_reports_full_path(refund_model_path) -> None:
    """超额退款需“先审核、再退款”两步才能触发，反例应给出完整动作序列。"""

    model = parser.load(refund_model_path)
    report = verify(model, "RefundOrder", VerifyConfig(lower=0, upper=1000, mode="light"))

    ce = report.counterexamples[0]
    assert ce.steps == 2
    assert [step.action for step in ce.path] == ["Audit", "ExecuteRefund"]
    # 每步都应带执行前后状态
    for step in ce.path:
        assert step.before and step.after
    assert ce.path[0].after["status"] == "Approved"
    assert ce.path[1].before["status"] == "Approved"
    assert "步骤 1" in ce.describe() and "步骤 2" in ce.describe()


def test_single_mode_cannot_find_two_step_bug(refund_model_path) -> None:
    """步数档位应真实生效：超额退款需要两步，单步档位找不到。"""

    model = parser.load(refund_model_path)
    report = verify(model, "RefundOrder", VerifyConfig(lower=0, upper=1000, mode="single"))

    assert report.counterexamples == []


def test_solver_timeout_degrades_gracefully(refund_model_path, monkeypatch) -> None:
    """求解超时应以 IFP3010 告警降级返回，不抛异常、不阻塞调用方。"""

    model = parser.load(refund_model_path)
    monkeypatch.setattr(z3.Solver, "check", lambda self, *a, **k: z3.unknown)
    report = verify(model, "RefundOrder", VerifyConfig(lower=0, upper=1000, timeout_ms=1))

    assert report.timed_out is True
    assert report.counterexamples == []
    assert any(d.code == "IFP3010" for d in report.diagnostics)


def test_consistent_model_has_no_contradiction(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    diags = check_consistency(model, "RefundOrder", VerifyConfig(lower=0, upper=1000))

    assert all(d.code != "IFP3001" for d in diags)


VO_MODEL = """
context VODemoContext {
    value Money {
        field amount: Decimal
        field currency: String
        invariant withinLimit {
            expr: amount <= 1000
            note: "单账户余额上限"
            owner: "finance-team"
        }
    }

    intent Wallet {
        field balance: Money
        field status: WalletStatus = WalletStatus::Open

        state WalletStatus { Open, Closed }
        initial: WalletStatus::Open

        action Deposit(amount: Decimal) {
            pre: status == WalletStatus::Open && amount >= 0
            post: balance.amount == old(balance.amount) + amount
        }
    }
}
"""


def test_value_object_field_is_inlined_not_unsupported() -> None:
    """含值对象字段的 Intent 不再抛 IFP3003，可正常进行属性测试。"""

    model = parser.parse(VO_MODEL)
    report = verify(model, "Wallet", VerifyConfig(lower=0, upper=5000))

    assert all(d.code != "IFP3003" for d in report.diagnostics)
    assert report.actions_checked == 1
    assert report.invariants_checked == 1


def test_value_object_invariant_break_is_found() -> None:
    """值对象自身的不变量（余额上限）在聚合内仍生效，可被打破并给出可读反例。"""

    model = parser.parse(VO_MODEL)
    report = verify(model, "Wallet", VerifyConfig(lower=0, upper=5000))

    ce = report.counterexamples[0]
    assert ce.invariant == "balance.withinLimit"
    # 值对象字段以可读的叶子键形式呈现
    assert "balance.amount" in ce.before
    assert "balance.amount" in ce.after
    assert "balance.amount" in ce.describe()


def test_value_object_nested_member_access_in_post() -> None:
    """post 中的成员访问 `balance.amount` 正确参与编码（动作只改成员，不影响其它字段）。"""

    model = parser.parse(VO_MODEL)
    diags = check_consistency(model, "Wallet", VerifyConfig(lower=0, upper=5000))

    assert all(d.code not in {"IFP3001", "IFP3002"} for d in diags)


def test_string_field_is_reported_as_unsupported() -> None:
    model = parser.parse(
        """
        context DemoContext {
            intent Demo {
                field name: String = "x"
                invariant nonEmpty { expr: name == "x" }
                action Noop {
                    pre: true
                    post: true
                }
            }
        }
        """
    )
    report = verify(model)
    assert any(d.code == "IFP3003" for d in report.diagnostics)


def test_intent_filter_limits_scope() -> None:
    model = parser.parse(
        """
        context DemoContext {
            intent A {
                field x: Decimal = 0
                invariant xPositive { expr: x > 0 }
                action Bump {
                    pre: true
                    post: x == old(x)
                }
            }
        }
        """
    )
    report = verify(model, intent_name="Missing")
    assert report.counterexamples == []
    assert report.intent == ""
