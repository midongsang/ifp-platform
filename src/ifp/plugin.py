"""插件 SDK：允许第三方注册自定义 Lint 规则与导出器。

对应《IFP 建模规范》中的“插件 SDK，自定义 Lint、导出器、仿真钩子”。
本模块只提供注册与查询能力，具体实现由插件提供。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from . import ast
from .errors import Diagnostic


class LintRule(Protocol):
    """自定义 Lint 规则：接收模型，返回诊断列表。"""

    def __call__(self, model: ast.Model) -> list[Diagnostic]: ...


class Exporter(Protocol):
    """自定义导出器：接收模型与上下文名，返回字符串制品。"""

    def __call__(self, model: ast.Model, context: str | None) -> str: ...


@dataclass
class PluginRegistry:
    lint_rules: list[LintRule] = field(default_factory=list)
    exporters: dict[str, Exporter] = field(default_factory=dict)

    def register_lint_rule(self, rule: LintRule) -> None:
        self.lint_rules.append(rule)

    def register_exporter(self, name: str, exporter: Exporter) -> None:
        self.exporters[name] = exporter


registry = PluginRegistry()


def register_lint_rule(rule: LintRule) -> LintRule:
    """装饰器：注册自定义 Lint 规则。

    用法::

        @register_lint_rule
        def no_empty_context(model):
            ...
    """

    registry.register_lint_rule(rule)
    return rule


def register_exporter(name: str) -> Callable[[Exporter], Exporter]:
    """装饰器：注册自定义导出器。"""

    def wrapper(exporter: Exporter) -> Exporter:
        registry.register_exporter(name, exporter)
        return exporter

    return wrapper


def lint_rules() -> list[LintRule]:
    return list(registry.lint_rules)


def exporters() -> dict[str, Exporter]:
    return dict(registry.exporters)
