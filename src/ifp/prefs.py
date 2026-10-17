"""用户偏好：更新源、自动检查开关、上次检查时间等，保存在本地 JSON 文件。

存放位置优先取环境变量 ``IFP_CONFIG_HOME``，否则为 ``~/.config/ifp/prefs.json``。
所有读取都做容错：文件缺失或损坏时回退为空配置，绝不抛错。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

_FILENAME = "prefs.json"

DEFAULTS: dict[str, Any] = {
    "update": {
        "source": "midongsang/ifp-platform",
        "auto_check": False,
        "last_check": 0.0,
    },
    "sandbox": {
        "jobs": 4,
        "timeout": 120,
        "cpu": 60,
        "memory": 4096,
    },
}


def config_dir() -> Path:
    """返回偏好目录。"""

    override = os.environ.get("IFP_CONFIG_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "ifp"


def path() -> Path:
    """返回偏好文件路径。"""

    return config_dir() / _FILENAME


def load() -> dict[str, Any]:
    """读取偏好；文件不存在或非法时返回空字典。"""

    file = path()
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict[str, Any]) -> Path:
    """写入偏好，返回文件路径。"""

    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return file


def get(key: str, default: Optional[Any] = None) -> Any:
    """按点分路径读取偏好，缺失时回退到内置默认值，再回退到 ``default``。"""

    node: Any = load()
    found = True
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            found = False
            break
    if found:
        return node

    node = DEFAULTS
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def set_value(key: str, value: Any) -> Any:
    """按点分路径写入偏好并持久化，返回写入值。"""

    data = load()
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value
    save(data)
    return value


def coerce(text: str) -> Any:
    """把命令行字符串转换为布尔 / 整数 / 字符串。"""

    lowered = text.strip().lower()
    if lowered in {"true", "yes", "on", "1"}:
        return True
    if lowered in {"false", "no", "off", "0"}:
        return False
    try:
        return int(text)
    except ValueError:
        return text
