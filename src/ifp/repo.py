"""Git 兼容的模型仓库：`.ifp` 源码的多版本存取与代码版本对齐审计。

对应「模型仓库与语义合并服务」的验收标准 1 与 5：

- 验收标准 1：模型仓库支持多版本 `.ifp` 源码存取，``commit`` 可关联业务变更说明；
- 验收标准 5：提供模型与代码版本对齐审计能力（:func:`alignment_report`）。

本模块只做仓库与版本/对齐能力，不做语义合并。后端直接使用系统 ``git``
（通过 ``subprocess`` 调用），实现「Git 兼容」。**不调用 ``git config``**：
提交身份一律通过环境变量注入（``GIT_AUTHOR_NAME`` / ``GIT_AUTHOR_EMAIL`` /
``GIT_COMMITTER_NAME`` / ``GIT_COMMITTER_EMAIL``），并附加
``-c commit.gpgsign=false`` 以避免签名问题。

业务变更说明与代码版本以 **trailer** 形式附加在提交信息末尾（空值则不加该行）::

    <message>

    Business-Change: <business_change>
    Code-Revision: <code_revision>

:meth:`ModelRepo.log` 会把 trailer 解析回 :class:`ModelCommit` 的
``business_change`` / ``code_revision``，``message`` 为去掉 trailer 后的正文
（``strip()`` 后）。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import IFPError

_BUSINESS_TRAILER = "Business-Change:"
_CODE_TRAILER = "Code-Revision:"
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"
_LOG_FORMAT = "%H%x1f%an <%ae>%x1f%aI%x1f%B%x1e"
_DEFAULT_EMAIL_DOMAIN = "ifp.local"


@dataclass(frozen=True)
class ModelCommit:
    """一次模型提交。

    - ``message``：去掉 trailer 后的提交正文；
    - ``business_change``：业务变更说明（来自 ``Business-Change`` trailer）；
    - ``code_revision``：代码版本（来自 ``Code-Revision`` trailer）；
    - ``author``：形如 ``姓名 <email>``；
    - ``timestamp``：作者时间的 ISO 8601 字符串。
    """

    sha: str
    message: str
    business_change: str
    code_revision: str
    author: str
    timestamp: str


class ModelRepo:
    """基于系统 ``git`` 的模型仓库。

    所有 git 调用统一为
    ``subprocess.run(["git", "-C", str(root), ...], capture_output=True, text=True, check=True)``；
    命令失败会包装为 :class:`IFPError` 并在消息中带上 stderr。
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @classmethod
    def init(cls, root: str | Path) -> "ModelRepo":
        """初始化仓库；已存在则复用，不报错。"""

        repo = cls(root)
        repo._root.mkdir(parents=True, exist_ok=True)
        repo._run("init")
        return repo

    def write(self, files: Mapping[str, str]) -> list[str]:
        """把 ``相对路径 -> 源码`` 写入工作区，返回写入的相对路径（已排序）。"""

        written: list[str] = []
        for rel, source in files.items():
            target = self._root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
            written.append(rel)
        return sorted(written)

    def commit(
        self,
        message: str,
        business_change: str = "",
        code_revision: str = "",
        author: str = "ifp-bot",
    ) -> ModelCommit:
        """暂存全部改动并提交，返回该次提交。

        ``business_change`` / ``code_revision`` 以 trailer 形式附加在 ``message``
        末尾（空值则不加该行）。``author`` 可为 ``姓名 <email>`` 或只有名字；
        只有名字时补默认邮箱 ``name@ifp.local``。
        """

        name, email = _split_author(author)
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
        }
        full = _compose_message(message, business_change, code_revision)
        self._run("add", "-A", env=env)
        self._run("commit", "-m", full, env=env)
        return self.log(limit=1)[0]

    def log(self, path: str | None = None, limit: int = 0) -> list[ModelCommit]:
        """返回提交列表（新 → 旧）。

        ``path`` 为 ``None`` 时返回整仓提交；否则只返回触及该路径的提交。
        ``limit > 0`` 时截断为最新的 ``limit`` 条。
        """

        args = ["log", f"--format={_LOG_FORMAT}"]
        if limit > 0:
            args += ["-n", str(limit)]
        if path is not None:
            args += ["--", path]
        return _parse_records(self._run(*args))

    def read(self, path: str, rev: str = "HEAD") -> str:
        """读取某个版本（默认 ``HEAD``）的文件内容。

        通过 ``git show <rev>:<path>`` 实现；路径在该版本不存在时抛
        :class:`IFPError`（消息中带上 git 的 stderr）。
        """

        return self._run("show", f"{rev}:{path}")

    def revisions(self, path: str) -> list[str]:
        """返回该文件的全部提交 sha（新 → 旧）。"""

        raw = self._run("log", "--format=%H", "--", path)
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def dirty(self) -> bool:
        """工作区是否存在未提交改动。"""

        return bool(self._run("status", "--porcelain").strip())

    def _run(self, *args: str, env: Mapping[str, str] | None = None) -> str:
        """执行 git 命令，返回 stdout；失败抛 :class:`IFPError`。"""

        try:
            result = subprocess.run(
                ["git", "-C", str(self._root), "-c", "commit.gpgsign=false", *args],
                capture_output=True,
                text=True,
                check=True,
                env=dict(env) if env is not None else None,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip() or (exc.stdout or "").strip()
            raise IFPError(f"git {' '.join(args)} 执行失败：{detail}") from exc
        return result.stdout


@dataclass(frozen=True)
class AlignmentEntry:
    """单个模型文件与代码版本的对齐审计结果。

    ``status`` 取值：``aligned`` | ``drift`` | ``unknown``。
    """

    path: str
    commit: str
    business_change: str
    code_revision: str
    status: str


def alignment_report(root: str | Path, code_revision: str | None = None) -> list[AlignmentEntry]:
    """审计每个模型文件与代码版本的对齐情况。

    对 ``git ls-files '*.ifp'`` 列出的每个模型文件，取其在 ``git log -1``
    的最近一次提交，读出 trailer 里的 ``Code-Revision``：

    - trailer 为空 → ``status = "unknown"``；
    - 传入了 ``code_revision`` 且与该 trailer 不一致 → ``status = "drift"``；
    - 否则 → ``status = "aligned"``。

    结果按 ``path`` 排序；仓库为空（无提交）时返回空列表，不报错。
    """

    repo = ModelRepo(root)
    paths = [line.strip() for line in repo._run("ls-files", "*.ifp").splitlines() if line.strip()]
    entries: list[AlignmentEntry] = []
    for path in sorted(paths):
        commits = repo.log(path=path, limit=1)
        if not commits:
            continue
        commit = commits[0]
        if not commit.code_revision:
            status = "unknown"
        elif code_revision is not None and code_revision != commit.code_revision:
            status = "drift"
        else:
            status = "aligned"
        entries.append(
            AlignmentEntry(
                path=path,
                commit=commit.sha,
                business_change=commit.business_change,
                code_revision=commit.code_revision,
                status=status,
            )
        )
    return entries


def _compose_message(message: str, business_change: str, code_revision: str) -> str:
    """把 trailer 附加到提交信息末尾（空值不加对应行）。"""

    trailers = []
    if business_change:
        trailers.append(f"{_BUSINESS_TRAILER} {business_change}")
    if code_revision:
        trailers.append(f"{_CODE_TRAILER} {code_revision}")
    if not trailers:
        return message
    return f"{message}\n\n" + "\n".join(trailers)


def _split_author(author: str) -> tuple[str, str]:
    """把 ``姓名 <email>`` 或只含名字的作者拆为 ``(name, email)``；

    只有名字时补默认邮箱 ``name@ifp.local``，兼容输入已含 ``<...>`` 的情况。
    """

    text = author.strip()
    if "<" in text and text.endswith(">"):
        name, _, email = text.rpartition("<")
        return name.strip(), email[:-1].strip()
    if "@" in text:
        return text.split("@", 1)[0], text
    return text, f"{text}@{_DEFAULT_EMAIL_DOMAIN}"


def _parse_records(raw: str) -> list[ModelCommit]:
    """解析 ``git log`` 输出为提交列表。"""

    commits: list[ModelCommit] = []
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        sha, author, timestamp, body = record.split(_FIELD_SEP, 3)
        message, business_change, code_revision = _parse_body(body)
        commits.append(
            ModelCommit(
                sha=sha,
                message=message,
                business_change=business_change,
                code_revision=code_revision,
                author=author,
                timestamp=timestamp,
            )
        )
    return commits


def _parse_body(body: str) -> tuple[str, str, str]:
    """从提交正文解析出 ``message`` 与两个 trailer 值。"""

    business_change = ""
    code_revision = ""
    message_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith(_BUSINESS_TRAILER):
            business_change = line[len(_BUSINESS_TRAILER):].strip()
        elif line.startswith(_CODE_TRAILER):
            code_revision = line[len(_CODE_TRAILER):].strip()
        else:
            message_lines.append(line)
    return "\n".join(message_lines).strip(), business_change, code_revision
