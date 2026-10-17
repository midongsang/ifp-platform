"""语义合并测试。

覆盖三项：无冲突时两侧变更都被采纳、同一规则被双方以不同方式修改时判为冲突
（并能定位到具体业务规则）、冲突与判定写入审计日志留痕。
"""

from __future__ import annotations

from ifp import parser
from ifp.ast import Model
from ifp.audit import AuditLog
from ifp.render import render_expr
from ifp.semantic_merge import merge_models, merge_paths, record


def _model(source: str) -> Model:
    return parser.parse(source)


BASE = """context WalletContext {
    intent Wallet {
        field balance: Decimal = 0

        invariant balanceWithinLimit {
            expr: balance <= 1000
        }

        action Deposit(amount: Decimal) {
            pre: amount >= 0
            post: balance == old(balance) + amount
        }
    }
}
"""

# 我方：放宽余额上限
OURS_LIMIT = BASE.replace("balance <= 1000", "balance <= 2000")
# 对方：收紧余额上限（与我方冲突）
THEIRS_LIMIT = BASE.replace("balance <= 1000", "balance <= 3000")
# 对方：新增一条不变量（与我方不冲突）
THEIRS_ADD_INVARIANT = BASE.replace(
    "        action Deposit",
    """        invariant balanceNonNegative {
            expr: balance >= 0
        }

        action Deposit""",
)
# 我方：删除该不变量
OURS_WITHOUT_INVARIANT = """context WalletContext {
    intent Wallet {
        field balance: Decimal = 0

        action Deposit(amount: Decimal) {
            pre: amount >= 0
            post: balance == old(balance) + amount
        }
    }
}
"""

STATE_BASE = """context WalletContext {
    intent Wallet {
        field status: WalletStatus = WalletStatus::Active
        state WalletStatus { Active, Frozen }
        initial: WalletStatus::Active
    }
}
"""
STATE_OURS = STATE_BASE.replace("state WalletStatus { Active, Frozen }",
                                "state WalletStatus { Active, Frozen, Closed }")
STATE_THEIRS = STATE_BASE.replace("state WalletStatus { Active, Frozen }",
                                  "state WalletStatus { Active, Frozen, Suspended }")

CONTRACT_BASE = """context WalletContext {
    contract WalletAPI {
        description: "钱包域对外契约"
        expose balance
    }
}
"""
CONTRACT_OURS = CONTRACT_BASE.replace("expose balance", "expose balance, status")
CONTRACT_THEIRS = CONTRACT_BASE.replace("expose balance", "expose balance, owner")


# ---------------------------------------------------------------------------
# 自动合并
# ---------------------------------------------------------------------------

def test_clean_merge_combines_both_sides() -> None:
    result = merge_models(_model(BASE), _model(OURS_LIMIT), _model(THEIRS_ADD_INVARIANT))

    assert result.clean
    assert not result.requires_manual_review
    assert result.merged is not None

    intent = result.merged.find_intent("Wallet")
    assert intent is not None
    names = [inv.name for inv in intent.invariants]
    assert names == ["balanceNonNegative", "balanceWithinLimit"]

    limit = next(inv for inv in intent.invariants if inv.name == "balanceWithinLimit")
    assert render_expr(limit.expr) == "(balance <= 2000)"
    assert result.ours_changes
    assert result.theirs_changes


def test_identical_change_is_not_a_conflict() -> None:
    result = merge_models(_model(BASE), _model(OURS_LIMIT), _model(OURS_LIMIT))

    assert result.clean
    intent = result.merged.find_intent("Wallet")
    limit = next(inv for inv in intent.invariants if inv.name == "balanceWithinLimit")
    assert render_expr(limit.expr) == "(balance <= 2000)"


def test_removal_on_one_side_is_applied() -> None:
    result = merge_models(_model(BASE), _model(OURS_WITHOUT_INVARIANT), _model(BASE))

    assert result.clean
    intent = result.merged.find_intent("Wallet")
    assert [inv.name for inv in intent.invariants] == []
    assert any(note.startswith("- ") for note in result.ours_changes)


# ---------------------------------------------------------------------------
# 冲突定位
# ---------------------------------------------------------------------------

def test_conflicting_invariant_change_is_reported() -> None:
    result = merge_models(_model(BASE), _model(OURS_LIMIT), _model(THEIRS_LIMIT))

    assert not result.clean
    assert result.requires_manual_review
    assert result.merged is None
    assert len(result.conflicts) == 1

    conflict = result.conflicts[0]
    assert conflict.path == "context/WalletContext/intent/Wallet/invariant/balanceWithinLimit"
    assert conflict.kind == "invariant"
    assert "2000" in conflict.ours
    assert "3000" in conflict.theirs
    assert conflict.format().startswith("! context/WalletContext")


def test_remove_and_change_conflicts() -> None:
    result = merge_models(_model(BASE), _model(OURS_WITHOUT_INVARIANT), _model(THEIRS_LIMIT))

    assert not result.clean
    assert any(c.kind == "invariant" for c in result.conflicts)


def test_state_machine_conflict_is_located() -> None:
    result = merge_models(_model(STATE_BASE), _model(STATE_OURS), _model(STATE_THEIRS))

    assert not result.clean
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.kind == "state"
    assert conflict.path.endswith("/state/WalletStatus")
    assert "Closed" in conflict.ours
    assert "Suspended" in conflict.theirs


def test_contract_conflict_is_located() -> None:
    result = merge_models(_model(CONTRACT_BASE), _model(CONTRACT_OURS), _model(CONTRACT_THEIRS))

    assert not result.clean
    conflict = result.conflicts[0]
    assert conflict.kind == "contract"
    assert conflict.path.endswith("/contract/WalletAPI")
    assert "status" in conflict.ours
    assert "owner" in conflict.theirs


# ---------------------------------------------------------------------------
# 留痕与文件入口
# ---------------------------------------------------------------------------

def test_merge_decision_is_recorded_in_audit_log(tmp_path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    result = merge_models(_model(BASE), _model(OURS_LIMIT), _model(THEIRS_LIMIT))

    entry = record(log, result, actor="alice")

    assert entry.event == "merge.decision"
    assert entry.payload["requires_manual_review"] is True
    assert entry.payload["conflicts"] == [
        "context/WalletContext/intent/Wallet/invariant/balanceWithinLimit"
    ]
    assert log.verify() is None


def test_merge_paths_loads_files(tmp_path) -> None:
    base = tmp_path / "base.ifp"
    ours = tmp_path / "ours.ifp"
    theirs = tmp_path / "theirs.ifp"
    base.write_text(BASE, encoding="utf-8")
    ours.write_text(OURS_LIMIT, encoding="utf-8")
    theirs.write_text(THEIRS_ADD_INVARIANT, encoding="utf-8")

    result = merge_paths(base, ours, theirs)

    assert result.clean
    intent = result.merged.find_intent("Wallet")
    assert [inv.name for inv in intent.invariants] == [
        "balanceNonNegative",
        "balanceWithinLimit",
    ]
