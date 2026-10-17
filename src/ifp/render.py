"""表达式渲染：把 AST 表达式还原为规范文本。

用于 CLI 报错信息、语义 Diff 展示和导出制品，保证同一表达式在任意阶段
渲染结果一致（便于结构化比较）。
"""

from __future__ import annotations

from . import ast


def render_expr(expr: ast.Expr) -> str:
    if isinstance(expr, ast.NumberLit):
        return expr.value
    if isinstance(expr, ast.StringLit):
        return f'"{expr.value}"'
    if isinstance(expr, ast.BoolLit):
        return "true" if expr.value else "false"
    if isinstance(expr, ast.VarRef):
        return expr.name
    if isinstance(expr, ast.FieldAccess):
        return expr.dotted
    if isinstance(expr, ast.EnumRef):
        return f"{expr.enum}::{expr.member}"
    if isinstance(expr, ast.CallExpr):
        args = ", ".join(render_expr(a) for a in expr.args)
        return f"{expr.name}({args})"
    if isinstance(expr, ast.OldExpr):
        return f"old({render_expr(expr.inner)})"
    if isinstance(expr, ast.UnaryExpr):
        return f"({expr.op}{render_expr(expr.operand)})"
    if isinstance(expr, ast.BinaryExpr):
        return f"({render_expr(expr.left)} {expr.op} {render_expr(expr.right)})"
    return "<expr>"


def flatten_and(expr: ast.Expr) -> list[ast.Expr]:
    """把 `a && b && c` 拆成 [a, b, c]，用于后置条件的效果提取。"""

    if isinstance(expr, ast.BinaryExpr) and expr.op == "&&":
        return flatten_and(expr.left) + flatten_and(expr.right)
    return [expr]
