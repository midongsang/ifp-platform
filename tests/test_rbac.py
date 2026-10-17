"""RBAC 权限模型与 CI 门禁集成测试（路线图验收标准）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ifp.audit import AuditLog
from ifp.cli import app
from ifp.diff import diff_paths
from ifp.rbac import (
    ROLE_PERMISSIONS,
    AccessDecision,
    Permission,
    PermissionDenied,
    Principal,
    Role,
    authorize_merge,
    decide,
    record,
    require,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
REFUND_FILE = EXAMPLES / "refund" / "refund.ifp"
BASELINE_EXPR = "totalRefundedAmount <= orderPaidAmount"
FIELD_DECL = "field orderPaidAmount: Decimal"

runner = CliRunner()


def _write_model_dirs(tmp_path: Path, old_text: str, new_text: str) -> tuple[Path, Path]:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    source = REFUND_FILE.read_text(encoding="utf-8")
    assert old_text in source and new_text != old_text
    (old_dir / "refund.ifp").write_text(source, encoding="utf-8")
    (new_dir / "refund.ifp").write_text(source.replace(old_text, new_text), encoding="utf-8")
    return old_dir, new_dir


def _baseline_change_diff(tmp_path: Path):
    """改动基线不变量的表达式，触发基线变更。"""

    old_dir, new_dir = _write_model_dirs(tmp_path, BASELINE_EXPR, "totalRefundedAmount < orderPaidAmount")
    return diff_paths(old_dir, new_dir)


def _field_change_diff(tmp_path: Path):
    """改动一个普通字段的类型：非基线变更，风险分值低。"""

    old_dir, new_dir = _write_model_dirs(tmp_path, FIELD_DECL, "field orderPaidAmount: Int")
    return diff_paths(old_dir, new_dir)


# -- 角色与权限 -------------------------------------------------------------

def test_four_roles_have_distinct_permissions() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)
    assert {role.value for role in Role} == {"modeler", "reviewer", "approver", "auditor"}
    assert ROLE_PERMISSIONS[Role.MODELER] == frozenset({Permission.SUBMIT_MODEL})
    assert ROLE_PERMISSIONS[Role.REVIEWER] == frozenset({Permission.REVIEW_MODEL})
    assert ROLE_PERMISSIONS[Role.APPROVER] == frozenset(
        {Permission.APPROVE_CHANGE, Permission.APPROVE_BASELINE}
    )
    assert ROLE_PERMISSIONS[Role.AUDITOR] == frozenset({Permission.READ_AUDIT})


def test_role_permissions_do_not_overlap_across_roles() -> None:
    """四类角色职责互不重叠：任一权限点只归属于一个角色。"""

    for left in Role:
        for right in Role:
            if left is right:
                continue
            assert not (ROLE_PERMISSIONS[left] & ROLE_PERMISSIONS[right])


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知角色"):
        Principal.parse("alice", "superuser")


def test_multi_role_principal_unions_permissions() -> None:
    principal = Principal.parse("alice", "reviewer, approver")
    assert principal.has(Permission.REVIEW_MODEL)
    assert principal.has(Permission.APPROVE_BASELINE)
    assert not principal.has(Permission.SUBMIT_MODEL)


# -- 判定 -------------------------------------------------------------------

def test_decide_allows_permission_holder() -> None:
    decision = decide(Principal.parse("bob", "approver"), Permission.APPROVE_BASELINE)
    assert decision.allowed
    assert decision.permission is Permission.APPROVE_BASELINE


def test_require_raises_permission_denied() -> None:
    with pytest.raises(PermissionDenied) as exc:
        require(Principal.parse("alice", "modeler"), Permission.APPROVE_BASELINE)
    assert "拒绝" in str(exc.value)
    assert exc.value.decision.allowed is False


def test_principal_without_roles_holds_nothing() -> None:
    principal = Principal.parse("ghost")
    assert principal.permissions() == frozenset()
    assert not principal.has(Permission.SUBMIT_MODEL)


# -- 基线变更的角色约束（验收标准 2） -----------------------------------------

def test_baseline_change_requires_approve_baseline(tmp_path: Path) -> None:
    result = _baseline_change_diff(tmp_path)
    assert result.has_baseline_change

    modeler = authorize_merge(Principal.parse("alice", "modeler"), result)
    assert not modeler.allowed
    assert modeler.permission is Permission.APPROVE_BASELINE

    approver = authorize_merge(Principal.parse("bob", "approver"), result)
    assert approver.allowed


def test_reviewer_cannot_merge_high_risk_plain_change(tmp_path: Path) -> None:
    """非基线变更但风险分值达阈值时，需要 approve_change 权限。"""

    result = _field_change_diff(tmp_path)
    assert not result.has_baseline_change

    reviewer = authorize_merge(Principal.parse("carol", "reviewer"), result, threshold=0)
    assert not reviewer.allowed
    assert reviewer.permission is Permission.APPROVE_CHANGE

    approver = authorize_merge(Principal.parse("bob", "approver"), result, threshold=0)
    assert approver.allowed


def test_low_risk_change_needs_no_role(tmp_path: Path) -> None:
    """未触及基线且风险低于阈值时，任何角色（含只读审计）都允许合并。"""

    result = _field_change_diff(tmp_path)
    decision = authorize_merge(Principal.parse("dave", "auditor"), result)
    assert isinstance(decision, AccessDecision)
    assert decision.allowed
    assert decision.permission is None


def test_baseline_permission_does_not_leak_to_plain_approval() -> None:
    """只读审计角色不能审批任何变更。"""

    auditor = Principal.parse("dave", "auditor")
    assert not decide(auditor, Permission.APPROVE_CHANGE).allowed
    assert not decide(auditor, Permission.APPROVE_BASELINE).allowed


# -- 审计日志（验收标准 3） --------------------------------------------------

def test_permission_decision_is_recorded_in_audit_log(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    result = _baseline_change_diff(tmp_path)
    decision = authorize_merge(Principal.parse("alice", "modeler"), result)

    record(log, decision, risk_score=65)

    entries = log.read()
    assert len(entries) == 1
    assert entries[0].event == "rbac.decision"
    assert entries[0].payload["actor"] == "alice"
    assert entries[0].payload["roles"] == ["modeler"]
    assert entries[0].payload["permission"] == "approve_baseline"
    assert entries[0].payload["allowed"] is False
    assert entries[0].payload["risk_score"] == 65
    assert log.verify() is None


# -- CLI --------------------------------------------------------------------

def test_cli_rbac_lists_roles_and_permissions() -> None:
    result = runner.invoke(app, ["rbac"])
    assert result.exit_code == 0
    for role in ("modeler", "reviewer", "approver", "auditor"):
        assert role in result.stdout
    assert "approve_baseline" in result.stdout


def test_cli_diff_denies_baseline_change_for_modeler(tmp_path: Path) -> None:
    old_dir, new_dir = _write_model_dirs(tmp_path, BASELINE_EXPR, "totalRefundedAmount < orderPaidAmount")
    result = runner.invoke(
        app,
        ["diff", str(old_dir), str(new_dir), "--actor", "alice", "--actor-roles", "modeler"],
    )
    assert result.exit_code == 3
    assert "拒绝" in result.stdout


def test_cli_diff_allows_baseline_change_for_approver(tmp_path: Path) -> None:
    old_dir, new_dir = _write_model_dirs(tmp_path, BASELINE_EXPR, "totalRefundedAmount < orderPaidAmount")
    result = runner.invoke(
        app,
        ["diff", str(old_dir), str(new_dir), "--actor", "bob", "--actor-roles", "approver"],
    )
    assert result.exit_code == 0
    assert "允许" in result.stdout


def test_cli_diff_without_actor_keeps_previous_behaviour(tmp_path: Path) -> None:
    old_dir, new_dir = _write_model_dirs(tmp_path, BASELINE_EXPR, "totalRefundedAmount < orderPaidAmount")
    result = runner.invoke(app, ["diff", str(old_dir), str(new_dir)])
    assert result.exit_code == 0
    assert "需强制审批：是" in result.stdout


def test_cli_diff_writes_rbac_decision_to_audit_log(tmp_path: Path) -> None:
    old_dir, new_dir = _write_model_dirs(tmp_path, BASELINE_EXPR, "totalRefundedAmount < orderPaidAmount")
    audit = tmp_path / "out" / "audit.jsonl"
    result = runner.invoke(
        app,
        [
            "diff", str(old_dir), str(new_dir),
            "--actor", "alice", "--actor-roles", "modeler",
            "--audit", str(audit),
        ],
    )
    assert result.exit_code == 3

    entries = AuditLog(audit).read()
    assert [entry.event for entry in entries] == ["diff", "rbac.decision"]
    assert entries[1].payload["allowed"] is False
    assert entries[1].payload["permission"] == "approve_baseline"
    assert AuditLog(audit).verify() is None


def test_cli_diff_rejects_unknown_role_as_usage_error(tmp_path: Path) -> None:
    old_dir, new_dir = _write_model_dirs(tmp_path, BASELINE_EXPR, "totalRefundedAmount < orderPaidAmount")
    result = runner.invoke(
        app,
        ["diff", str(old_dir), str(new_dir), "--actor", "alice", "--actor-roles", "superuser"],
    )
    assert result.exit_code == 2
    assert "未知角色" in result.stdout
