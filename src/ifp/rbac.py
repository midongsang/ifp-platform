"""基于角色的访问控制（RBAC）。

对应《IFP 建模规范》第 14 节：

- 区分四类角色：建模提交（modeler）、评审（reviewer）、审批（approver）、只读审计（auditor）；
- 涉及基线变更（或风险分值达到阈值）的合并受角色约束，未获授权不得合并；
- 每次权限判定都写入审计日志（Hash Chain），便于事后追溯“谁在什么时候批了什么”。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from .audit import AuditEntry, AuditLog
from .baseline import APPROVAL_THRESHOLD, approval_required
from .diff import DiffResult
from .errors import IFPError


class Role(str, Enum):
    """平台角色。"""

    MODELER = "modeler"      # 建模提交：编辑并提交模型变更
    REVIEWER = "reviewer"    # 评审：对模型变更给出评审意见
    APPROVER = "approver"    # 审批：批准变更，含基线变更
    AUDITOR = "auditor"      # 只读审计：查看审计日志，无写权限


class Permission(str, Enum):
    """权限点。"""

    SUBMIT_MODEL = "submit_model"          # 提交模型变更
    REVIEW_MODEL = "review_model"          # 评审模型变更
    APPROVE_CHANGE = "approve_change"      # 审批普通变更
    APPROVE_BASELINE = "approve_baseline"  # 审批基线变更
    READ_AUDIT = "read_audit"              # 查看审计日志


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.MODELER: frozenset({Permission.SUBMIT_MODEL}),
    Role.REVIEWER: frozenset({Permission.REVIEW_MODEL}),
    Role.APPROVER: frozenset({Permission.APPROVE_CHANGE, Permission.APPROVE_BASELINE}),
    Role.AUDITOR: frozenset({Permission.READ_AUDIT}),
}

ROLE_LABELS: dict[Role, str] = {
    Role.MODELER: "建模提交",
    Role.REVIEWER: "评审",
    Role.APPROVER: "审批（含基线变更）",
    Role.AUDITOR: "只读审计",
}

PERMISSION_LABELS: dict[Permission, str] = {
    Permission.SUBMIT_MODEL: "提交模型",
    Permission.REVIEW_MODEL: "评审模型",
    Permission.APPROVE_CHANGE: "审批普通变更",
    Permission.APPROVE_BASELINE: "审批基线变更",
    Permission.READ_AUDIT: "查看审计日志",
}


def coerce_role(value: str) -> Role:
    """把字符串解析为 :class:`Role`，未知角色报错并提示可选值。"""

    try:
        return Role(value)
    except ValueError as exc:
        allowed = ", ".join(role.value for role in Role)
        raise ValueError(f"未知角色：{value}（可选：{allowed}）") from exc


@dataclass(frozen=True)
class Principal:
    """一次操作的主体：一个人或一个服务账号，及其被授予的角色。"""

    name: str
    roles: tuple[Role, ...] = ()

    @classmethod
    def parse(cls, name: str, roles: str | Iterable[str] = ()) -> "Principal":
        """构造主体；``roles`` 可以是逗号分隔的字符串，或角色名序列。"""

        if isinstance(roles, str):
            items = [item.strip() for item in roles.split(",") if item.strip()]
        else:
            items = [str(item).strip() for item in roles if str(item).strip()]
        return cls(name=name, roles=tuple(coerce_role(item) for item in items))

    def permissions(self) -> frozenset[Permission]:
        """主体实际持有的权限集合（其所有角色权限的并集）。"""

        granted: set[Permission] = set()
        for role in self.roles:
            granted |= ROLE_PERMISSIONS[role]
        return frozenset(granted)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions()

    def label(self) -> str:
        if not self.roles:
            return f"{self.name}（未分配角色）"
        return f"{self.name}（{', '.join(role.value for role in self.roles)}）"


@dataclass(frozen=True)
class AccessDecision:
    """一次权限判定结果。``permission`` 为空表示该操作本身不需要审批。"""

    allowed: bool
    actor: str
    roles: tuple[Role, ...]
    permission: Optional[Permission]
    reason: str

    def format(self) -> str:
        verdict = "允许" if self.allowed else "拒绝"
        roles = ", ".join(role.value for role in self.roles) or "无角色"
        action = self.permission.value if self.permission else "合并"
        return f"[{verdict}] {self.actor}（{roles}）执行 {action}：{self.reason}"


class PermissionDenied(IFPError):
    """权限不足：主体未持有完成该操作所需的权限。"""

    def __init__(self, decision: AccessDecision) -> None:
        self.decision = decision
        super().__init__(decision.format())


def decide(principal: Principal, permission: Permission) -> AccessDecision:
    """判定主体是否持有某项权限。"""

    if principal.has(permission):
        return AccessDecision(
            allowed=True,
            actor=principal.name,
            roles=principal.roles,
            permission=permission,
            reason="具备所需权限",
        )
    granted = ", ".join(sorted(item.value for item in principal.permissions())) or "无"
    return AccessDecision(
        allowed=False,
        actor=principal.name,
        roles=principal.roles,
        permission=permission,
        reason=f"缺少权限 {permission.value}（当前持有：{granted}）",
    )


def require(principal: Principal, permission: Permission) -> AccessDecision:
    """同 :func:`decide`，但拒绝时抛出 :class:`PermissionDenied`。"""

    decision = decide(principal, permission)
    if not decision.allowed:
        raise PermissionDenied(decision)
    return decision


def required_permission(
    result: DiffResult, threshold: int = APPROVAL_THRESHOLD
) -> Optional[Permission]:
    """合并这份 Diff 所需的审批权限；不需要审批时返回 ``None``。"""

    if not approval_required(result, threshold):
        return None
    if result.has_baseline_change:
        return Permission.APPROVE_BASELINE
    return Permission.APPROVE_CHANGE


def authorize_merge(
    principal: Principal, result: DiffResult, threshold: int = APPROVAL_THRESHOLD
) -> AccessDecision:
    """判定主体是否有权合并这份模型变更。"""

    needed = required_permission(result, threshold)
    if needed is None:
        return AccessDecision(
            allowed=True,
            actor=principal.name,
            roles=principal.roles,
            permission=None,
            reason="未触及基线且风险低于阈值，无需审批",
        )
    return decide(principal, needed)


def record(log: AuditLog, decision: AccessDecision, **context: object) -> AuditEntry:
    """把权限判定写入审计日志（事件名 ``rbac.decision``）。"""

    return log.append(
        "rbac.decision",
        actor=decision.actor,
        roles=[role.value for role in decision.roles],
        permission=decision.permission.value if decision.permission else None,
        allowed=decision.allowed,
        reason=decision.reason,
        **context,
    )
