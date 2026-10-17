"""不可篡改的追加式审计日志（Hash Chain）。

每条记录包含前一条记录的哈希，任一记录被篡改都会导致链校验失败，
满足《IFP 建模规范》“审计日志不可篡改”的要求。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    timestamp: str
    event: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "event": self.event,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


class AuditLog:
    """JSONL 格式的追加式审计日志。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- 读 ---------------------------------------------------------------

    def read(self) -> list[AuditEntry]:
        if not self.path.exists():
            return []
        entries: list[AuditEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            entries.append(
                AuditEntry(
                    seq=data["seq"],
                    timestamp=data["timestamp"],
                    event=data["event"],
                    payload=data["payload"],
                    prev_hash=data["prev_hash"],
                    hash=data["hash"],
                )
            )
        return entries

    # -- 写 ---------------------------------------------------------------

    def append(self, event: str, **payload: Any) -> AuditEntry:
        entries = self.read()
        seq = entries[-1].seq + 1 if entries else 0
        prev_hash = entries[-1].hash if entries else GENESIS
        timestamp = datetime.now(timezone.utc).isoformat()
        body = {
            "seq": seq,
            "timestamp": timestamp,
            "event": event,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        digest = _hash(body)
        entry = AuditEntry(
            seq=seq, timestamp=timestamp, event=event,
            payload=payload, prev_hash=prev_hash, hash=digest,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return entry

    # -- 校验 -------------------------------------------------------------

    def verify(self) -> Optional[int]:
        """校验哈希链完整性；返回第一条损坏记录的 seq，完好则返回 None。"""

        prev = GENESIS
        for entry in self.read():
            if entry.prev_hash != prev:
                return entry.seq
            body = {
                "seq": entry.seq,
                "timestamp": entry.timestamp,
                "event": entry.event,
                "payload": entry.payload,
                "prev_hash": entry.prev_hash,
            }
            if _hash(body) != entry.hash:
                return entry.seq
            prev = entry.hash
        return None


def _hash(body: dict[str, Any]) -> str:
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
