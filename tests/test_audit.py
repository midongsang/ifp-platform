"""审计日志（Hash Chain）测试。"""

from __future__ import annotations

import json
from pathlib import Path

from ifp.audit import GENESIS, AuditLog


def test_append_and_read(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.append("verify", model="m.ifp", counterexamples=1)
    log.append("diff", old="a.ifp", new="b.ifp")

    entries = log.read()
    assert [e.seq for e in entries] == [0, 1]
    assert entries[0].prev_hash == GENESIS
    assert entries[1].prev_hash == entries[0].hash


def test_chain_is_valid(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        log.append("event", i=i)

    assert log.verify() is None


def test_tampering_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append("verify", counterexamples=0)
    log.append("verify", counterexamples=3)

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["payload"]["counterexamples"] = 999  # 篡改第二条记录
    lines[1] = json.dumps(record, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert log.verify() == 1


def test_empty_log_is_valid(tmp_path: Path) -> None:
    assert AuditLog(tmp_path / "missing.jsonl").verify() is None
