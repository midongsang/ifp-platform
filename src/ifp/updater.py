"""版本更新：查询来源仓库的发行版本（GitHub Releases），展示发行时间与资产大小，
并按用户选择执行更新。

设计要点：
- 只依赖标准库（``urllib``），不新增运行时依赖，降低安装与兼容成本；
- 自动遵循 ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``NO_PROXY`` 环境变量；
- 私有仓库可通过 ``GITHUB_TOKEN`` / ``GH_TOKEN`` 提供访问令牌；
- 来源仓库没有发行版本时回退到标签列表，发行版本缺少资产时回退为按标签安装；
- 所有可预期失败（离线、超时、权限、限流、资产缺失、pip 失败）统一抛
  :class:`UpdateError`，由 CLI 渲染为可读提示，绝不使主流程崩溃。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .errors import IFPError

DEFAULT_SOURCE = "midongsang/ifp-platform"
_API_ROOT = "https://api.github.com/repos/{source}/releases"
_TAGS_ROOT = "https://api.github.com/repos/{source}/tags"
_USER_AGENT = "ifp-platform-updater"


class UpdateError(IFPError):
    """更新流程中可预期的错误。"""


class _NotFound(UpdateError):
    """更新源缺少请求的资源（HTTP 404）。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Asset:
    """发行版本中的一个可下载资产。"""

    name: str
    size: int
    url: str

    def human_size(self) -> str:
        return human_size(self.size)


@dataclass(frozen=True)
class ReleaseInfo:
    """来源仓库的一个发行版本。"""

    tag: str
    name: str
    published_at: str
    html_url: str
    notes: str
    assets: tuple[Asset, ...] = ()

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")

    def lines(self) -> list[str]:
        """返回不含颜色的展示行，便于 CLI 与测试复用。"""

        out = [f"最新版本：{self.tag}", f"发行时间：{format_time(self.published_at)}"]
        if self.assets:
            for asset in self.assets[:3]:
                out.append(f"发行资产：{asset.name}（{asset.human_size()}）")
        else:
            out.append("发行资产：无")
        out.append(f"发行页：{self.html_url}")
        return out


# ---------------------------------------------------------------------------
# 版本与格式化
# ---------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    """把 ``v1.2.3`` / ``1.2.3-rc1`` 解析为可比较的数字元组。"""

    core = re.split(r"[-+]", text.strip().lstrip("vV"), maxsplit=1)[0]
    parts: list[int] = []
    for chunk in core.split("."):
        match = re.match(r"\d+", chunk)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """判断 ``latest`` 是否比 ``current`` 更新。"""

    left, right = parse_version(latest), parse_version(current)
    width = max(len(left), len(right), 1)
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


def human_size(num: int) -> str:
    """把字节数格式化为人类可读的大小。"""

    size = float(max(num, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num} B"


def format_time(value: str) -> str:
    """把 ISO 时间戳格式化为本地时间；无法解析时原样返回。"""

    if not value:
        return "未知"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return value
    return moment.strftime("%Y-%m-%d %H:%M").strip()


# ---------------------------------------------------------------------------
# 网络访问
# ---------------------------------------------------------------------------

def _headers(token: Optional[str]) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": _USER_AGENT}
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _get_json(url: str, timeout: float, token: Optional[str]) -> Any:
    request = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _NotFound(f"更新源不存在或无权限访问：{url}") from exc
        if exc.code in (401, 403):
            raise UpdateError(
                "访问更新源被拒绝（可能是令牌无效或 API 频率受限）；私有仓库请设置 GITHUB_TOKEN"
            ) from exc
        raise UpdateError(f"更新源返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"无法访问更新源（离线或网络受限）：{exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise UpdateError(f"访问更新源超时或被网络策略拦截：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError("更新源返回的不是有效 JSON") from exc


def _to_release(payload: Any) -> ReleaseInfo:
    if not isinstance(payload, dict):
        raise UpdateError("更新源返回的发行数据格式不正确")
    assets: list[Asset] = []
    for item in payload.get("assets") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("browser_download_url") or ""
        name = item.get("name") or ""
        if not url or not name:
            continue
        assets.append(Asset(name=name, size=int(item.get("size") or 0), url=url))
    return ReleaseInfo(
        tag=str(payload.get("tag_name") or "").strip(),
        name=str(payload.get("name") or "").strip(),
        published_at=str(payload.get("published_at") or "").strip(),
        html_url=str(payload.get("html_url") or "").strip(),
        notes=str(payload.get("body") or "").strip(),
        assets=tuple(assets),
    )


def _latest_from_list(root: str, timeout: float, token: Optional[str]) -> Optional[ReleaseInfo]:
    """从发行列表取最新版本，优先非预发行；列表为空或缺失时返回 ``None``。"""

    try:
        payload = _get_json(root, timeout, token)
    except _NotFound:
        return None
    if not isinstance(payload, list) or not payload:
        return None
    candidates = [item for item in payload if isinstance(item, dict)]
    if not candidates:
        raise UpdateError("更新源返回的发行数据格式不正确")
    # GitHub 按发布时间倒序返回，第一个即最新；优先取非预发行版本
    stable = [item for item in candidates if not item.get("prerelease")]
    return _to_release(stable[0] if stable else candidates[0])


def _latest_release(
    root: str, *, include_prerelease: bool, timeout: float, token: Optional[str]
) -> Optional[ReleaseInfo]:
    """优先读取 release 端点，没有最新发行时回退到发行列表。"""

    if include_prerelease:
        return _latest_from_list(root, timeout, token)
    try:
        return _to_release(_get_json(f"{root}/latest", timeout, token))
    except _NotFound:
        return _latest_from_list(root, timeout, token)


def _latest_from_tags(
    source: str, timeout: float, token: Optional[str]
) -> Optional[ReleaseInfo]:
    """发行缺失时回退到标签：只给出版本号，发行时间与资产留空。"""

    try:
        payload = _get_json(_TAGS_ROOT.format(source=source), timeout, token)
    except _NotFound:
        return None
    if not isinstance(payload, list):
        return None
    for item in payload:
        if isinstance(item, dict) and item.get("name"):
            tag = str(item["name"]).strip()
            return ReleaseInfo(
                tag=tag,
                name=tag,
                published_at="",
                html_url=f"https://github.com/{source}/releases/tag/{tag}",
                notes="",
                assets=(),
            )
    return None


def fetch_latest(
    source: str = DEFAULT_SOURCE,
    *,
    include_prerelease: bool = False,
    timeout: float = 6.0,
    token: Optional[str] = None,
) -> ReleaseInfo:
    """查询来源仓库的最新发行版本。

    优先读取 GitHub Releases：``include_prerelease`` 为真时读取发行列表并优先取稳定版，
    否则只取 ``/releases/latest``。若来源仓库没有任何发行版本，则回退到标签列表，
    仅报告版本号（发行时间与资产为空），以便「没有发行版本」的仓库也能被跟进。
    """

    source = source.strip().strip("/")
    if source.count("/") != 1:
        raise UpdateError(f"更新源应为 owner/repo 形式，当前为：{source!r}")
    root = _API_ROOT.format(source=source)

    release = _latest_release(
        root, include_prerelease=include_prerelease, timeout=timeout, token=token
    )
    if release is None:
        release = _latest_from_tags(source, timeout, token)
    if release is None:
        raise UpdateError(f"更新源 {source} 暂无任何发行版本或标签")
    return release


def pick_asset(release: ReleaseInfo) -> Optional[Asset]:
    """选择可安装的资产：优先 wheel，其次源码包，最后任一资产。"""

    wheels = [a for a in release.assets if a.name.endswith(".whl")]
    if wheels:
        return wheels[0]
    sdists = [a for a in release.assets if a.name.endswith(".tar.gz")]
    if sdists:
        return sdists[0]
    return release.assets[0] if release.assets else None


def download(asset: Asset, dest_dir: Path, *, timeout: float = 30.0) -> Path:
    """下载资产到本地目录，返回落盘路径。"""

    request = urllib.request.Request(asset.url, headers=_headers(None))
    target = dest_dir / asset.name
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            target.write_bytes(response.read())
    except urllib.error.URLError as exc:
        raise UpdateError(f"下载发行资产失败：{exc.reason}") from exc
    except OSError as exc:
        raise UpdateError(f"写入发行资产失败：{exc}") from exc
    return target


def _run_pip(command: list[str]) -> str:
    """执行 pip 命令；失败统一映射为 :class:`UpdateError`。"""

    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:  # 解释器缺少 pip 模块时可能在更底层失败
        raise UpdateError(f"无法执行 pip：{exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise UpdateError(f"pip 安装失败：{detail}")
    return proc.stdout.strip()


def install_artifact(path: Path) -> str:
    """用当前解释器的 pip 安装/升级下载到的资产。"""

    return _run_pip([sys.executable, "-m", "pip", "install", "--upgrade", str(path)])


def install_from_git(source: str, ref: str) -> str:
    """发行版本没有可下载资产时，直接从来源仓库的标签安装。"""

    spec = f"ifp-platform @ git+https://github.com/{source}@{ref}"
    return _run_pip([sys.executable, "-m", "pip", "install", "--upgrade", spec])


def install_release(
    release: ReleaseInfo, *, source: Optional[str] = None, timeout: float = 30.0
) -> str:
    """下载并安装发行版本中选定的资产，返回被安装的标识。

    发行版本没有可下载资产时，若提供了 ``source``，则回退为从该仓库的标签直接安装，
    使「没有资产」的发行版本也能被跟进。
    """

    asset = pick_asset(release)
    if asset is not None:
        with tempfile.TemporaryDirectory(prefix="ifp-update-") as tmp:
            path = download(asset, Path(tmp), timeout=timeout)
            install_artifact(path)
        return asset.name
    if source:
        install_from_git(source, release.tag)
        return f"{source}@{release.tag}"
    raise UpdateError(f"发行版本 {release.tag} 没有可安装的资产")
