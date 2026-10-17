"""IFP 错误与诊断模型。

- :class:`IFPError`：致命错误（解析失败、IO 失败等），直接抛出。
- :class:`Diagnostic`：可累积的检查结果（类型错误、Lint 告警、反例等），
  由 CLI 统一渲染为报告。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Diagnostic:
    """一条检查结果。``code`` 形如 ``IFP1001``，便于文档化与豁免。"""

    code: str
    message: str
    severity: Severity = Severity.ERROR
    file: str = ""
    line: int = 0

    def format(self) -> str:
        location = self.file or "<model>"
        if self.line:
            location = f"{location}:{self.line}"
        return f"{location}: {self.severity.value}[{self.code}] {self.message}"


class IFPError(Exception):
    """IFP 致命错误基类。"""


class IFPParseError(IFPError):
    """`.ifp` 语法错误。"""

    def __init__(self, filename: str, message: str) -> None:
        self.filename = filename
        self.message = message
        super().__init__(f"{filename}: 解析失败\n{message}")


class IFPConfigError(IFPError):
    """配置文件（property_test.yaml 等）非法。"""
