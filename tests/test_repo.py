"""模型仓库（Git 兼容）测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ifp.errors import IFPError
from ifp.repo import ModelRepo, alignment_report

A_V1 = "context Demo { intent Demo { field amount: Decimal = 0 } }"
A_V2 = "context Demo { intent Demo { field amount: Decimal = 1 } }"
B_V1 = "context Other { intent Other { field total: Decimal = 0 } }"


def test_commit_parses_trailers(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    repo.write({"a.ifp": A_V1})
    commit = repo.commit("首版模型", business_change="新增退款规则", code_revision="abc123")

    assert commit.sha
    assert commit.message == "首版模型"
    assert commit.business_change == "新增退款规则"
    assert commit.code_revision == "abc123"

    latest = repo.log()[0]
    assert latest.sha == commit.sha
    assert latest.message == "首版模型"
    assert latest.business_change == "新增退款规则"
    assert latest.code_revision == "abc123"


def test_commit_without_trailers(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    repo.write({"a.ifp": A_V1})
    commit = repo.commit("无 trailer")

    assert commit.message == "无 trailer"
    assert commit.business_change == ""
    assert commit.code_revision == ""


def test_author_default_email(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    repo.write({"a.ifp": A_V1})
    assert repo.commit("默认邮箱", author="ifp-bot").author == "ifp-bot <ifp-bot@ifp.local>"

    repo.write({"a.ifp": A_V2})
    assert repo.commit("显式邮箱", author="张三 <zhang@example.com>").author == "张三 <zhang@example.com>"


def test_revisions_are_newest_first(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    repo.write({"a.ifp": A_V1})
    first = repo.commit("v1", business_change="b1", code_revision="c1")
    repo.write({"a.ifp": A_V2})
    second = repo.commit("v2", business_change="b2", code_revision="c2")

    assert repo.revisions("a.ifp") == [second.sha, first.sha]


def test_read_specific_revision(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    repo.write({"a.ifp": A_V1})
    first = repo.commit("v1", business_change="b1", code_revision="c1")
    repo.write({"a.ifp": A_V2})
    repo.commit("v2", business_change="b2", code_revision="c2")

    assert repo.read("a.ifp", rev=first.sha) == A_V1
    assert repo.read("a.ifp") == A_V2


def test_read_missing_path_raises(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    repo.write({"a.ifp": A_V1})
    repo.commit("v1")

    with pytest.raises(IFPError):
        repo.read("missing.ifp")


def test_alignment_report_statuses(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    repo.write({"a.ifp": A_V1})
    repo.commit("aligned", business_change="b", code_revision="rev-1")
    repo.write({"b.ifp": B_V1})
    repo.commit("unknown", business_change="b2")

    report = alignment_report(repo.root)
    entries = {entry.path: entry for entry in report}
    assert [entry.path for entry in report] == ["a.ifp", "b.ifp"]
    assert entries["a.ifp"].status == "aligned"
    assert entries["b.ifp"].status == "unknown"

    drift = {entry.path: entry for entry in alignment_report(repo.root, code_revision="rev-2")}
    assert drift["a.ifp"].status == "drift"
    assert drift["b.ifp"].status == "unknown"


def test_alignment_report_empty_repo(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")

    assert alignment_report(repo.root) == []


def test_log_filters_by_path(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    repo.write({"a.ifp": A_V1})
    first = repo.commit("a1", business_change="ba")
    repo.write({"b.ifp": B_V1})
    second = repo.commit("b1", business_change="bb")
    repo.write({"a.ifp": A_V2})
    third = repo.commit("a2", business_change="ba2")

    a_log = [commit.sha for commit in repo.log(path="a.ifp")]
    assert a_log == [third.sha, first.sha]
    assert second.sha not in a_log

    assert len(repo.log(limit=2)) == 2


def test_dirty(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    assert repo.dirty() is False

    repo.write({"a.ifp": A_V1})
    assert repo.dirty() is True

    repo.commit("v1")
    assert repo.dirty() is False


def test_init_is_idempotent(tmp_path: Path) -> None:
    repo = ModelRepo.init(tmp_path / "repo")
    again = ModelRepo.init(repo.root)

    assert again.root == repo.root
