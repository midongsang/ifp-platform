"""Scenario 仿真测试。"""

from __future__ import annotations

from ifp import parser
from ifp.simulate import eval_expr, simulate


def test_refund_scenarios_all_pass(refund_dir) -> None:
    project = parser.load_project([refund_dir])
    results = simulate(project)

    assert len(results) == 4
    failed = [r for r in results if not r.passed]
    assert failed == [], failed


def test_rejected_step_intercepts_illegal_action(refund_dir) -> None:
    project = parser.load_project([refund_dir])
    results = {r.name: r for r in simulate(project)}

    assert results["AuditRejectedWhenNotPending"].passed
    assert results["RefundRejectedWhenNotApproved"].passed


def test_then_assertion_failure_is_detected() -> None:
    model = parser.parse(
        """
        context DemoContext {
            intent Demo {
                field amount: Decimal = 0
                invariant ok { expr: amount >= 0 }
                action Set {
                    pre: true
                    post: amount == 5
                }
            }
        }
        scenario Wrong {
            context: DemoContext
            intent: Demo
            given { amount = 0 }
            when Set()
            then amount == 7
        }
        """
    )
    result = simulate(model)[0]

    assert not result.passed
    assert "then" in result.messages[-1]


def test_eval_decimal_literal_uses_decimal() -> None:
    from decimal import Decimal

    from ifp import ast

    value = eval_expr(ast.NumberLit(value="1.25"), {}, {})
    assert value == Decimal("1.25")


VO_SCENARIO_MODEL = """
context SimVOContext {
    value Money {
        field amount: Decimal
        field currency: String = "CNY"
        invariant nonNegative {
            expr: amount >= 0
            note: "金额不允许为负数"
            owner: "finance-team"
        }
    }

    intent Wallet {
        field balance: Money
        field status: WalletStatus = WalletStatus::Open

        state WalletStatus { Open, Closed }
        initial: WalletStatus::Open

        action Withdraw(amount: Decimal) {
            pre: status == WalletStatus::Open && amount >= 0 && amount <= old(balance.amount)
            post: balance.amount == old(balance.amount) - amount
        }

        action ForceDebit(amount: Decimal) {
            pre: status == WalletStatus::Open && amount >= 0
            post: balance.amount == old(balance.amount) - amount
        }
    }
}

scenario WalletWithdraw {
    context: SimVOContext
    intent: Wallet
    given { balance.amount = 100 }
    when Withdraw(amount = 40)
    then balance.amount == 60
}

scenario WalletWithdrawRejected {
    context: SimVOContext
    intent: Wallet
    given { balance.amount = 100 }
    rejected Withdraw(amount = 200)
}

scenario WalletValueObjectInvariantBroken {
    context: SimVOContext
    intent: Wallet
    given { balance.amount = 10 }
    when ForceDebit(amount = 30)
    then balance.amount >= 0
}
"""


def test_value_object_field_scenario_passes() -> None:
    """值对象成员可作为 given 目标，仿真按 ``balance.amount`` 维护状态。"""

    results = {r.name: r for r in simulate(parser.parse(VO_SCENARIO_MODEL))}

    assert results["WalletWithdraw"].passed, results["WalletWithdraw"].messages
    assert results["WalletWithdraw"].final_state["balance.amount"] == 60
    assert results["WalletWithdraw"].final_state["balance.currency"] == "CNY"
    assert results["WalletWithdrawRejected"].passed


def test_value_object_invariant_violation_fails_scenario() -> None:
    """仿真应检出值对象自身不变量被打破。"""

    results = {r.name: r for r in simulate(parser.parse(VO_SCENARIO_MODEL))}
    broken = results["WalletValueObjectInvariantBroken"]

    assert not broken.passed
    assert "balance.nonNegative" in broken.messages[-1]
