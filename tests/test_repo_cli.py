"""模型仓库 / 语义合并 / 度量 / 对齐审计的 CLI 端到端测试。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ifp.audit import AuditLog
from ifp.cli import app

runner = CliRunner()


def _run(*args: str):
    return runner.invoke(app, list(args))


BASE = """context WalletContext {
    intent Wallet {
        field balance: Decimal = 0

        invariant balanceWithinLimit {
            expr: balance <= 1000
        }
    }
}
"""
OURS = BASE.replace("balance <= 1000", "balance <= 2000")
THEIRS_ADD = BASE.replace(
    "    }\n}\n",
    """        invariant balanceNonNegative {
            expr: balance >= 0
        }
    }
}
""",
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 模型仓库
# ---------------------------------------------------------------------------

def test_repo_commit_and_log(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    model = _write(repo_dir / "wallet.ifp", OURS)

    assert _run("repo", "init", str(repo_dir)).exit_code == 0

    commit = _run(
        "repo", "commit", str(repo_dir), str(model),
        "-m", "钱包域首版",
        "--business-change", "支持余额上限调整",
        "--code-revision", "rev1",
        "--author", "张三 <zhangsan@example.com>",
    )
    assert commit.exit_code == 0
    assert "已提交" in commit.stdout
    assert "支持余额上限调整" in commit.stdout

    log = _run("repo", "log", str(repo_dir))
    assert log.exit_code == 0
    assert "钱包域首版" in log.stdout
    assert "rev1" in log.stdout

    scoped = _run("repo", "log", str(repo_dir), "--path", "wallet.ifp")
    assert scoped.exit_code == 0
    assert "钱包域首版" in scoped.stdout


def test_align_reports_drift_and_unknown(tmp_path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    first = _write(repo_dir / "a.ifp", BASE)
    _run("repo", "init", str(repo_dir))
    _run("repo", "commit", str(repo_dir), str(first), "-m", "a", "--code-revision", "rev1")

    second = _write(repo_dir / "b.ifp", OURS)
    _run("repo", "commit", str(repo_dir), str(second), "-m", "b")

    matched = _run("align", str(repo_dir), "--code-revision", "rev1")
    assert matched.exit_code == 1
    assert "aligned" in matched.stdout
    assert "unknown" in matched.stdout

    drifted = _run("align", str(repo_dir), "--code-revision", "rev2")
    assert drifted.exit_code == 1
    assert "drift" in drifted.stdout


# ---------------------------------------------------------------------------
# 语义合并
# ---------------------------------------------------------------------------

def test_merge_clean_allows_auto_merge(tmp_path) -> None:
    base = _write(tmp_path / "base.ifp", BASE)
    ours = _write(tmp_path / "ours.ifp", OURS)
    theirs = _write(tmp_path / "theirs.ifp", THEIRS_ADD)

    result = _run("merge", str(base), str(ours), str(theirs))

    assert result.exit_code == 0
    assert "可自动合并" in result.stdout


def test_merge_conflict_requires_review_and_audits(tmp_path) -> None:
    base = _write(tmp_path / "base.ifp", BASE)
    ours = _write(tmp_path / "ours.ifp", OURS)
    theirs = _write(tmp_path / "theirs.ifp", BASE.replace("balance <= 1000", "balance <= 3000"))
    audit = tmp_path / "audit.jsonl"

    result = _run("merge", str(base), str(ours), str(theirs), "--audit", str(audit))

    assert result.exit_code == 1
    assert "balanceWithinLimit" in result.stdout
    assert "人工评审" in result.stdout
    assert audit.exists()
    assert AuditLog(audit).verify() is None
    assert AuditLog(audit).read()[0].event == "merge.decision"


def test_merge_json_reports_review_flag(tmp_path) -> None:
    base = _write(tmp_path / "base.ifp", BASE)
    ours = _write(tmp_path / "ours.ifp", OURS)
    theirs = _write(tmp_path / "theirs.ifp", BASE.replace("balance <= 1000", "balance <= 3000"))

    result = _run("merge", str(base), str(ours), str(theirs), "--json")

    assert result.exit_code == 1
    assert '"requires_manual_review": true' in result.stdout


def test_merge_parse_error_exits_two(tmp_path) -> None:
    bad = _write(tmp_path / "bad.ifp", "context Broken { not valid }")
    good = _write(tmp_path / "good.ifp", BASE)

    result = _run("merge", str(good), str(good), str(bad))

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# 度量
# ---------------------------------------------------------------------------

def test_metrics_reports_coverage_and_dead_invariants(tmp_path) -> None:
    model = _write(tmp_path / "wallet.ifp", BASE)

    result = _run("metrics", str(model))

    assert result.exit_code == 0
    assert "规则覆盖率" in result.stdout
    assert "死不变量" in result.stdout


def test_metrics_json_output(tmp_path) -> None:
    model = _write(tmp_path / "wallet.ifp", BASE)

    result = _run("metrics", str(model), "--json")

    assert result.exit_code == 0
    assert '"rule_coverage"' in result.stdout
