"""类型检查测试：标识符、枚举、跨文件场景引用。"""

from __future__ import annotations

from ifp import parser
from ifp.errors import Severity
from ifp.typecheck import typecheck

_BAD_IDENTIFIER = """
context DemoContext {
    intent Demo {
        field amount: Decimal = 0
        state DemoStatus { Created, Done }
        initial: DemoStatus::Created

        invariant bad {
            expr: missingField >= 0
        }

        action Finish {
            transition: DemoStatus::Done
            pre: status == DemoStatus::Created
            post: status == DemoStatus::Done
        }
    }
}
"""

_UNKNOWN_ENUM = """
context DemoContext {
    intent Demo {
        field amount: Decimal = 0
        state DemoStatus { Created, Done }
        initial: DemoStatus::Created

        action Finish {
            transition: DemoStatus::Done
            pre: status == OtherStatus::Created
            post: status == DemoStatus::Done
        }
    }
}
"""


def _codes(model) -> set[str]:
    return {d.code for d in typecheck(model)}


def test_unknown_identifier_reported() -> None:
    model = parser.parse(_BAD_IDENTIFIER)
    diags = typecheck(model)

    assert any(d.code == "IFP1001" for d in diags)
    assert all(d.severity is not Severity.INFO for d in diags if d.code == "IFP1001")


def test_unknown_enum_reported() -> None:
    model = parser.parse(_UNKNOWN_ENUM)
    assert "IFP1005" in _codes(model)


def test_refund_example_typechecks_clean(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    errors = [d for d in typecheck(model) if d.severity is Severity.ERROR]

    assert errors == []


def test_scenario_resolves_intent_across_files() -> None:
    model = parser.parse(
        """
        context DemoContext {
            intent Demo {
                field amount: Decimal = 0
                field status: DemoStatus = DemoStatus::Created
                state DemoStatus { Created, Done }
                initial: DemoStatus::Created
                invariant amountNonNegative { expr: amount >= 0 }
                action Finish {
                    transition: DemoStatus::Done
                    pre: status == DemoStatus::Created
                    post: status == DemoStatus::Done
                }
            }
        }
        """
    )
    scenario_model = parser.parse(
        """
        scenario HappyPath {
            context: DemoContext
            intent: Demo
            given {
                amount = 0
                status = DemoStatus::Created
            }
            when Finish()
            then status == DemoStatus::Done
        }
        """,
        filename="scenarios.ifp",
    )
    project = parser.merge([model, scenario_model])

    # 单文件检查：跨文件引用会被误报
    single = [d for d in typecheck(scenario_model) if d.severity is Severity.ERROR]
    assert any(d.code == "IFP1031" for d in single)

    # 合并项目检查：跨文件引用可被解析，无错误
    merged = [d for d in typecheck(scenario_model, project=project) if d.severity is Severity.ERROR]
    assert merged == []


_VO_MEMBER = """
context VOContext {
    value Money {
        field amount: Decimal
        invariant nonNegative { expr: amount >= 0 }
    }

    intent Wallet {
        field balance: Money
        action Deposit(amount: Decimal) {
            pre: amount >= 0
            post: balance.amount == old(balance.amount) + amount
        }
    }
}
"""

_VO_BAD_MEMBER = """
context VOContext {
    value Money {
        field amount: Decimal
        invariant nonNegative { expr: amount >= 0 }
    }

    intent Wallet {
        field balance: Money
        invariant badMember { expr: balance.missing >= 0 }
        action Deposit(amount: Decimal) {
            pre: amount >= 0
            post: balance.amount == old(balance.amount) + amount
        }
    }
}
"""


def test_value_object_member_access_typechecks_clean() -> None:
    model = parser.parse(_VO_MEMBER)
    errors = [d for d in typecheck(model) if d.severity is Severity.ERROR]

    assert errors == []


def test_unknown_value_object_member_reported() -> None:
    model = parser.parse(_VO_BAD_MEMBER)
    assert "IFP1023" in _codes(model)
