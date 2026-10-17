"""IFP DSL 抽象语法树（AST）。

AST 是解析器的输出、类型检查/Lint/SMT/仿真/导出器的输入。
所有节点均为不可变 dataclass，便于语义 Diff 时做结构化比较。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union

# ---------------------------------------------------------------------------
# 类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TypeRef:
    """字段 / 参数的类型引用。kind 取 Decimal/Int/Bool/String/Named。"""

    kind: str
    name: Optional[str] = None  # 仅 kind == "Named" 时有值（枚举名或 ValueObject 名）

    def __str__(self) -> str:  # pragma: no cover - 仅用于报错信息
        return self.name if self.kind == "Named" else self.kind


# ---------------------------------------------------------------------------
# 表达式
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Expr:
    """表达式节点基类。"""

    line: int = 0


@dataclass(frozen=True)
class NumberLit(Expr):
    value: str = ""  # 保持原始字面量，避免浮点精度丢失


@dataclass(frozen=True)
class StringLit(Expr):
    value: str = ""


@dataclass(frozen=True)
class BoolLit(Expr):
    value: bool = False


@dataclass(frozen=True)
class VarRef(Expr):
    name: str = ""


@dataclass(frozen=True)
class FieldAccess(Expr):
    """值对象成员访问：``balance.amount``（path 为完整点分路径）。

    仅允许访问值对象（value object）的成员，用于在聚合内引用 / 赋值
    值对象字段的底层叶子字段。
    """

    path: tuple[str, ...] = ()

    @property
    def root(self) -> str:
        return self.path[0] if self.path else ""

    @property
    def dotted(self) -> str:
        return ".".join(self.path)


@dataclass(frozen=True)
class EnumRef(Expr):
    enum: str = ""
    member: str = ""


@dataclass(frozen=True)
class CallExpr(Expr):
    name: str = ""
    args: tuple["Expr", ...] = ()


@dataclass(frozen=True)
class OldExpr(Expr):
    """`old(x)`：引用动作执行前的状态（用于 Post 条件）。"""

    inner: Expr = field(default_factory=Expr)


@dataclass(frozen=True)
class UnaryExpr(Expr):
    op: str = ""
    operand: Expr = field(default_factory=Expr)


@dataclass(frozen=True)
class BinaryExpr(Expr):
    op: str = ""
    left: Expr = field(default_factory=Expr)
    right: Expr = field(default_factory=Expr)


# ---------------------------------------------------------------------------
# 声明
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldDecl:
    name: str
    type: TypeRef
    default: Optional[Expr] = None
    line: int = 0


@dataclass(frozen=True)
class StateDecl:
    name: str
    values: tuple[str, ...] = ()
    line: int = 0


@dataclass(frozen=True)
class InvariantDecl:
    name: str
    expr: Optional[Expr] = None
    baseline: bool = False
    risk: Optional[str] = None
    owner: Optional[str] = None
    note: Optional[str] = None
    line: int = 0


@dataclass(frozen=True)
class Param:
    name: str
    type: TypeRef
    line: int = 0


@dataclass(frozen=True)
class ActionDecl:
    name: str
    params: tuple[Param, ...] = ()
    pre: Optional[Expr] = None
    post: Optional[Expr] = None
    transition: Optional[EnumRef] = None
    line: int = 0


@dataclass(frozen=True)
class IntentDecl:
    name: str
    description: Optional[str] = None
    fields: tuple[FieldDecl, ...] = ()
    states: tuple[StateDecl, ...] = ()
    initial: Optional[EnumRef] = None
    invariants: tuple[InvariantDecl, ...] = ()
    actions: tuple[ActionDecl, ...] = ()
    line: int = 0


@dataclass(frozen=True)
class ValueDecl:
    name: str
    description: Optional[str] = None
    fields: tuple[FieldDecl, ...] = ()
    invariants: tuple[InvariantDecl, ...] = ()
    line: int = 0


@dataclass(frozen=True)
class ContractDecl:
    name: str
    description: Optional[str] = None
    exposes: tuple[str, ...] = ()
    invariants: tuple[InvariantDecl, ...] = ()
    line: int = 0


@dataclass(frozen=True)
class EventDecl:
    """领域事件声明：聚合状态变化的事实记录。

    - ``fields``：事件载荷字段（如金额、新状态）。
    - ``produced_by``：产生该事件的动作名（可选）。重放时若存在则以对应动作
      的 post 条件推导状态；否则按字段名直接写入状态。
    """

    name: str
    description: Optional[str] = None
    fields: tuple[FieldDecl, ...] = ()
    produced_by: Optional[str] = None
    line: int = 0


@dataclass(frozen=True)
class ContextDecl:
    name: str
    description: Optional[str] = None
    values: tuple[ValueDecl, ...] = ()
    intents: tuple[IntentDecl, ...] = ()
    contracts: tuple[ContractDecl, ...] = ()
    events: tuple[EventDecl, ...] = ()
    line: int = 0


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WhenStep:
    action: str
    args: tuple[tuple[str, Expr], ...] = ()
    rejected: bool = False
    line: int = 0


@dataclass(frozen=True)
class ThenStep:
    expr: Expr = field(default_factory=Expr)
    line: int = 0


ScenarioStep = Union[WhenStep, ThenStep]


@dataclass(frozen=True)
class ScenarioDecl:
    name: str
    context: Optional[str] = None
    intent: Optional[str] = None
    given: tuple[tuple[str, Expr], ...] = ()
    steps: tuple[ScenarioStep, ...] = ()
    line: int = 0


# ---------------------------------------------------------------------------
# 顶层模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Model:
    """一个 `.ifp` 文件的解析结果。"""

    contexts: tuple[ContextDecl, ...] = ()
    scenarios: tuple[ScenarioDecl, ...] = ()
    source: str = ""  # 源文件名，用于报错

    def find_context(self, name: str) -> Optional[ContextDecl]:
        return next((c for c in self.contexts if c.name == name), None)

    def find_intent(self, name: str) -> Optional[IntentDecl]:
        for ctx in self.contexts:
            for intent in ctx.intents:
                if intent.name == name:
                    return intent
        return None

    def find_scenario(self, name: str) -> Optional[ScenarioDecl]:
        return next((s for s in self.scenarios if s.name == name), None)
