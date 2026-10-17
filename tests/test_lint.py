"""模型静态检查（Lint）测试。"""

from __future__ import annotations

from ifp import parser
from ifp.lint import lint


def _codes(model) -> list[str]:
    return [d.code for d in lint(model)]


def test_baseline_missing_owner_is_error() -> None:
    model = parser.parse(
        """
        context DemoContext {
            intent Demo {
                field amount: Decimal = 0
                state DemoStatus { Created, Done }
                initial: DemoStatus::Created
                invariant guide {
                    expr: amount >= 0
                    baseline: true
                }
            }
        }
        """
    )
    diags = lint(model)
    assert any(d.code == "IFP2001" for d in diags)


def test_context_naming_convention() -> None:
    model = parser.parse(
        """
        context Refund {
            intent Demo {
                field amount: Decimal = 0
                invariant ok { expr: amount >= 0 }
            }
        }
        """
    )
    assert "IFP2006" in _codes(model)


def test_intent_without_invariant_warns() -> None:
    model = parser.parse(
        """
        context DemoContext {
            intent Demo {
                field amount: Decimal = 0
                state DemoStatus { Created, Done }
                initial: DemoStatus::Created
                action Finish {
                    transition: DemoStatus::Done
                    pre: status == DemoStatus::Created
                    post: status == DemoStatus::Done
                }
            }
        }
        """
    )
    assert "IFP2005" in _codes(model)


def test_scenario_without_reject_is_info() -> None:
    model = parser.parse(
        """
        context DemoContext {
            intent Demo {
                field amount: Decimal = 0
                invariant ok { expr: amount >= 0 }
            }
        }
        scenario Happy {
            context: DemoContext
            intent: Demo
            given { amount = 0 }
            then amount == 0
        }
        """
    )
    diags = [d for d in lint(model) if d.code == "IFP2007"]
    assert diags and diags[0].severity.value == "info"


def test_refund_example_has_no_lint_errors(refund_model_path) -> None:
    model = parser.load(refund_model_path)
    errors = [d for d in lint(model) if d.severity.value == "error"]
    assert errors == []
