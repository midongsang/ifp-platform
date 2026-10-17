"""任务调度器：把多个校验任务按并发度排队送进沙箱执行。

- 任务粒度由调用方决定，例如「一个 Intent 一次 verify」「一个 Scenario 一次 simulate」；
- 结果按提交顺序返回，便于稳定输出与快照对比；
- ``fail_fast`` 打开时，首个失败任务出现后不再调度剩余任务，未开始的任务标记为 ``skipped``；
- 每个任务都带调度元数据（``kind`` / ``metadata``），便于按模型、Intent、场景分组统计。
"""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from . import sandbox
from .sandbox import Limits

STATUS_SKIPPED = "skipped"

# 汇总输出时的固定顺序，保证同一批任务的报告可比对。
_STATUS_ORDER = (
    sandbox.STATUS_OK,
    sandbox.STATUS_FAILED,
    sandbox.STATUS_TIMEOUT,
    sandbox.STATUS_BLOCKED,
    STATUS_SKIPPED,
)


@dataclass(frozen=True)
class Task:
    """一个待调度的沙箱任务。"""

    name: str
    argv: tuple[str, ...]
    kind: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    def label(self) -> str:
        return f"{self.kind} {self.name}".strip()


@dataclass(frozen=True)
class TaskResult:
    """一个任务的执行结果。"""

    task: Task
    status: str
    exit_code: int
    duration_s: float
    stdout: str = ""
    stderr: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == sandbox.STATUS_OK

    def line(self) -> str:
        return f"[{self.status}] {self.task.label()}（{self.duration_s:.2f}s）"

    def tail(self, lines: int = 3) -> list[str]:
        """取失败输出的最后几行，用于在终端的紧凑展示。"""

        text = (self.stderr or self.stdout).strip()
        return text.splitlines()[-lines:] if text else []

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.task.name,
            "kind": self.task.kind,
            "metadata": dict(self.task.metadata),
            "argv": list(self.task.argv),
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "detail": self.detail,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class Summary:
    """一次调度的汇总统计。"""

    total: int
    counts: Mapping[str, int]
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.counts.get(sandbox.STATUS_OK, 0) == self.total

    def line(self) -> str:
        detail = " / ".join(
            f"{status} {self.counts[status]}" for status in _STATUS_ORDER if self.counts.get(status)
        )
        return f"调度完成：{self.total} 个任务（{detail}），耗时 {self.duration_s:.2f}s"

    def to_json(self) -> dict[str, object]:
        return {
            "total": self.total,
            "counts": dict(self.counts),
            "duration_s": round(self.duration_s, 3),
        }


def summarize(results: Sequence[TaskResult], duration_s: float) -> Summary:
    """统计一批任务结果。"""

    counts = dict.fromkeys(_STATUS_ORDER, 0)
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return Summary(total=len(results), counts=counts, duration_s=duration_s)


class Scheduler:
    """按并发度调度沙箱任务的简单调度器。"""

    def __init__(
        self,
        *,
        jobs: int = 1,
        limits: Optional[Limits] = None,
        fail_fast: bool = False,
    ) -> None:
        self.jobs = max(1, jobs)
        self.limits = limits or Limits()
        self.fail_fast = fail_fast
        self.duration_s = 0.0

    def run(self, tasks: Sequence[Task]) -> list[TaskResult]:
        """执行全部任务；返回结果保持与 ``tasks`` 相同的顺序。

        ``fail_fast`` 打开时采用有界滑动窗口：任一任务失败后不再提交新任务，
        因此在任务数多于并发度时确实能阻止后续任务开始；已经启动的任务仍会执行完成。
        """

        if not tasks:
            self.duration_s = 0.0
            return []

        results: list[Optional[TaskResult]] = [None] * len(tasks)
        workers = max(1, min(self.jobs, len(tasks)))
        started = time.monotonic()
        halted = False
        next_index = 0

        with ThreadPoolExecutor(max_workers=workers) as pool:
            inflight: dict[Future[Any], int] = {}

            def fill() -> None:
                nonlocal next_index
                while not halted and next_index < len(tasks) and len(inflight) < workers:
                    inflight[pool.submit(self._run_one, tasks[next_index])] = next_index
                    next_index += 1

            fill()
            while inflight:
                done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
                for future in done:
                    index = inflight.pop(future)
                    result = future.result()
                    results[index] = result
                    if self.fail_fast and not result.ok:
                        halted = True
                fill()

        self.duration_s = time.monotonic() - started
        for index, result in enumerate(results):
            if result is None:  # 未提交的任务（fail_fast 中止）
                results[index] = TaskResult(
                    tasks[index],
                    STATUS_SKIPPED,
                    -1,
                    0.0,
                    detail="前序任务失败，已跳过调度",
                )
        return [result for result in results if result is not None]

    def _run_one(self, task: Task) -> TaskResult:
        try:
            outcome = sandbox.run(task.argv, limits=self.limits)
        except Exception as exc:  # 调度层兜底：单个任务异常不应带走整批任务的报告
            return TaskResult(task, sandbox.STATUS_BLOCKED, -1, 0.0, detail=str(exc))
        return TaskResult(
            task=task,
            status=outcome.status,
            exit_code=outcome.exit_code,
            duration_s=outcome.duration_s,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            detail=outcome.detail,
        )
