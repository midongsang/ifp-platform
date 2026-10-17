"""属性测试 / 验证配置加载。

配置样例见 examples/refund/property_test.yaml。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from .errors import IFPConfigError

if TYPE_CHECKING:  # 仅用于类型标注，避免在导入期拉起 z3
    from .smt import VerifyConfig

DEFAULT_CONFIG_NAME = "property_test.yaml"


@dataclass
class VerifyPlan:
    intent: Optional[str]
    config: VerifyConfig


def load_plan(path: str | Path) -> VerifyPlan:
    """从 YAML 文件加载验证计划。"""

    from .smt import VerifyConfig

    path = Path(path)
    if not path.exists():
        raise IFPConfigError(f"配置文件不存在：{path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise IFPConfigError(f"配置文件解析失败：{path}\n{exc}") from exc

    if not isinstance(raw, dict):
        raise IFPConfigError(f"配置文件根节点必须是映射：{path}")

    # 允许顶层直接配置，或嵌套在 property_test 下
    data = raw.get("property_test", raw)
    if not isinstance(data, dict):
        raise IFPConfigError("property_test 节点必须是映射")

    invariants = data.get("invariants") or ()
    if isinstance(invariants, str):
        invariants = (invariants,)

    raw_steps = data.get("max_steps")
    max_steps = int(raw_steps) if raw_steps is not None else None

    config = VerifyConfig(
        lower=int(data.get("lower", 0)),
        upper=int(data.get("upper", 100_000)),
        timeout_ms=int(data.get("timeout_ms", 5_000)),
        invariants=tuple(str(i) for i in invariants),
        mode=str(data.get("mode", "light")),
        max_steps=max_steps,
    )
    intent = data.get("intent")
    return VerifyPlan(intent=str(intent) if intent else None, config=config)


def find_config(model_path: str | Path) -> Optional[Path]:
    """在与模型同目录下寻找默认配置文件。"""

    candidate = Path(model_path).parent / DEFAULT_CONFIG_NAME
    return candidate if candidate.exists() else None
