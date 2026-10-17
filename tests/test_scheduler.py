"""任务调度器测试：并发保序、失败快速停止、汇总统计与结果渲染。"""

from __future__ import annotations

from pathlib import Path

from ifp import sandbox
from ifp.scheduler import STATUS_SKIPPED, Scheduler, Task, TaskResult, summarize

BAD_MODEL = "context Broken { not valid }"


def _version_task(name: str) -> Task:
    return Task(name=name, argv=("version",), kind="version", metadata={"name": name})


def _bad_model_task(tmp_path: Path) -> Task:
    """一个必然失败的任务：语法错误的模型无法通过 check。"""

    path = tmp_path / "broken.ifp"
    path.write_text(BAD_MODEL, encoding="utf-8")
    return Task(name="broken", argv=("check", str(path)), kind="check")


def test_empty_batch_returns_no_results() -> None:
    scheduler = Scheduler(jobs=4)

    assert scheduler.run([]) == []
    assert scheduler.duration_s == 0.0


def test_results_keep_submission_order_under_concurrency() -> None:
    tasks = [_version_task(name) for name in ("alpha", "beta", "gamma", "delta")]

    results = Scheduler(jobs=4).run(tasks)

    assert [result.task.name for result in results] == ["alpha", "beta", "gamma", "delta"]
    assert all(result.ok for result in results)
    assert all(result.status == sandbox.STATUS_OK for result in results)


def test_failed_task_reports_status_and_exit_code(tmp_path: Path) -> None:
    results = Scheduler(jobs=1).run([_bad_model_task(tmp_path)])

    assert len(results) == 1
    assert results[0].status == sandbox.STATUS_FAILED
    assert results[0].exit_code == 1
    assert results[0].task.kind == "check"


def test_fail_fast_skips_remaining_tasks(tmp_path: Path) -> None:
    tasks = [_bad_model_task(tmp_path), _version_task("after-1"), _version_task("after-2")]

    results = Scheduler(jobs=1, fail_fast=True).run(tasks)

    assert [result.status for result in results] == [
        sandbox.STATUS_FAILED,
        STATUS_SKIPPED,
        STATUS_SKIPPED,
    ]
    assert all(result.detail for result in results if result.status == STATUS_SKIPPED)


def test_without_fail_fast_every_task_runs(tmp_path: Path) -> None:
    tasks = [_bad_model_task(tmp_path), _version_task("after-1"), _version_task("after-2")]

    results = Scheduler(jobs=1).run(tasks)

    assert [result.status for result in results] == [
        sandbox.STATUS_FAILED,
        sandbox.STATUS_OK,
        sandbox.STATUS_OK,
    ]


def test_jobs_is_clamped_to_at_least_one() -> None:
    assert Scheduler(jobs=0).jobs == 1
    assert Scheduler(jobs=-5).jobs == 1
    assert Scheduler(jobs=8).jobs == 8


def test_summarize_counts_every_status() -> None:
    ok_task = _version_task("ok")
    skipped_task = _version_task("skipped")
    results = [
        TaskResult(ok_task, sandbox.STATUS_OK, 0, 0.1),
        TaskResult(ok_task, sandbox.STATUS_FAILED, 1, 0.2),
        TaskResult(ok_task, sandbox.STATUS_TIMEOUT, -9, 5.0),
        TaskResult(skipped_task, STATUS_SKIPPED, -1, 0.0),
    ]

    summary = summarize(results, 5.5)

    assert summary.total == 4
    assert summary.counts[sandbox.STATUS_OK] == 1
    assert summary.counts[STATUS_SKIPPED] == 1
    assert not summary.ok
    assert summary.line().startswith("调度完成：4 个任务（ok 1 / failed 1 / timeout 1 / skipped 1）")
    assert summarize([TaskResult(ok_task, sandbox.STATUS_OK, 0, 0.1)], 0.1).ok


def test_task_result_line_tail_and_json() -> None:
    task = Task(name="RefundOrder", argv=("verify", "x.ifp"), kind="verify", metadata={"intent": "RefundOrder"})
    result = TaskResult(task, sandbox.STATUS_FAILED, 1, 0.25, stdout="out", stderr="l1\nl2\nl3\nl4")

    assert result.line() == "[failed] verify RefundOrder（0.25s）"
    assert result.tail(2) == ["l3", "l4"]
    payload = result.to_json()
    assert payload["name"] == "RefundOrder"
    assert payload["kind"] == "verify"
    assert payload["metadata"] == {"intent": "RefundOrder"}
    assert payload["argv"] == ["verify", "x.ifp"]
    assert payload["status"] == "failed"
    assert payload["duration_s"] == 0.25
