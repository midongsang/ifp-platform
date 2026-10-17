"""IFP — 意图形式化平台（Intent Formalization Platform）参考实现。

对外主要 API：

    from ifp import parse, load, lint, typecheck, verify, simulate

详见 docs/dsl-spec.md 与 README。
"""

from __future__ import annotations

from .ast import Model
from .parser import load, parse

__version__ = "0.2.0"

__all__ = ["Model", "__version__", "load", "parse"]
