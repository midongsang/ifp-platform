"""模型度量指标测试。"""

from __future__ import annotations

import pytest

from ifp import ast, parser
from ifp.metrics import collect, dead_invariants, referenced_fields

_DEAD = """
context DeadContext {
    intent Dead {
        field x: Decimal = 0
        field y: Decimal = 0
        invariant xPositive {
            expr: x >= 0
        }
        invariant yPositive {
            expr: y >= 0
        }
        action SetX {
            pre: true
            post: x == 1
        }
    }
}
"""

_COVER = """
context CoverContext {
    value Amount {
        field value: Decimal
        invariant nonNegative {
            expr: value >= 0
        }
    }
    intent WithRule {
        field total: Decimal = 0
        invariant totalNonNegative {
            expr: total >= 0
        }
        action Set {
            pre: total >= 0
            post: total == 1
        }
        action Reset {
            pre: total >= 0
        }
    }
    intent NoRule {
        field q: Decimal = 0
        action Bump {
            pre: q >= 0
        }
    }
}
"""

_REF = """
context RefContext {
    intent Ref {
        field total: Decimal = 0
        action Execute(amount: Decimal) {
            post: total == old(total) + amount && total >= 0
        }
    }
}
"""


def test_dead_invariants_detected() -> None:
    # y 字段没有任何动作修改它，故 yPositive 为死不变量
    metrics = collect(parser.parse(_DEAD))

    assert metrics.dead_invariants == 1
    assert len(metrics.per_intent) == 1
    assert metrics.per_intent[0].dead_invariants == ("yPositive",)


def test_dead_invariants_function() -> None:
    intent = parser.parse(_DEAD).contexts[0].intents[0]
    dead = dead_invariants(intent)

    assert [inv.name for inv in dead] == ["yPositive"]


def test_rule_and_action_coverage() -> None:
    metrics = collect(parser.parse(_COVER))

    # 规则覆盖率 = (含不变量的 Intent 1 + 含不变量的值对象 1) / (Intent 2 + 值对象 1)
    assert metrics.rule_coverage == pytest.approx(2 / 3)
    # 动作覆盖率 = 同时具备 pre/post 的动作 1 / 全部动作 3
    assert metrics.action_coverage == pytest.approx(1 / 3)
    assert metrics.actions == 3
    assert metrics.value_objects == 1


def test_model_metrics_as_dict() -> None:
    metrics = collect(parser.parse(_COVER))
    data = metrics.as_dict()

    assert set(data) == {
        "contexts",
        "intents",
        "value_objects",
        "invariants",
        "baseline_invariants",
        "dead_invariants",
        "actions",
        "contracts",
        "scenarios",
        "rule_coverage",
        "action_coverage",
        "per_intent",
    }
    assert isinstance(data["per_intent"], list)
    assert all(isinstance(item, dict) for item in data["per_intent"])
    assert set(data["per_intent"][0]) == {
        "context",
        "intent",
        "invariants",
        "baseline_invariants",
        "dead_invariants",
        "actions",
        "actions_with_pre",
        "actions_with_post",
    }


def test_summary_lines() -> None:
    metrics = collect(parser.parse(_COVER))
    lines = metrics.summary_lines()

    assert lines[0] == "模型度量："
    assert any("规则覆盖率 66.7%" in line for line in lines)
    assert any("动作覆盖率 33.3%" in line for line in lines)


def test_referenced_fields_with_old_and_and() -> None:
    intent = parser.parse(_REF).contexts[0].intents[0]
    post = intent.actions[0].post

    assert referenced_fields(post) == {"total", "amount"}
    assert referenced_fields(None) == set()


def test_referenced_fields_field_access_root() -> None:
    expr = ast.FieldAccess(path=("balance", "amount"))

    assert referenced_fields(expr) == {"balance"}
