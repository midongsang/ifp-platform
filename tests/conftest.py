"""pytest 共享夹具与路径辅助。"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


@pytest.fixture
def refund_model_path() -> Path:
    return EXAMPLES / "refund" / "refund.ifp"


@pytest.fixture
def refund_dir() -> Path:
    return EXAMPLES / "refund"


MINIMAL_MODEL = """
context DemoContext {
    description: "最小示例"

    intent Demo {
        field amount: Decimal = 0

        state DemoStatus { Created, Done }
        initial: DemoStatus::Created

        invariant amountNonNegative {
            expr: amount >= 0
            note: "金额非负"
            owner: "demo-team"
        }

        action Finish {
            transition: DemoStatus::Done
            pre: status == DemoStatus::Created
            post: status == DemoStatus::Done
        }
    }
}
"""


@pytest.fixture
def minimal_source() -> str:
    return MINIMAL_MODEL
