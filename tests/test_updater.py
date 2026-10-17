"""版本更新（updater）单元测试：版本比较、格式化、发行数据解析与错误映射。"""

from __future__ import annotations

import urllib.error

import pytest

from ifp import updater


def _release(tag: str = "v1.1.0", assets: tuple[updater.Asset, ...] = ()) -> updater.ReleaseInfo:
    return updater.ReleaseInfo(
        tag=tag,
        name=f"IFP {tag}",
        published_at="2026-10-16T08:00:00Z",
        html_url="https://example.test/releases",
        notes="notes",
        assets=assets,
    )


# --- 版本比较 ---------------------------------------------------------------

def test_parse_version_normalizes_prefix_and_suffix() -> None:
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.2.3-rc1") == (1, 2, 3)
    assert updater.parse_version("1.2") == (1, 2)
    assert updater.parse_version("1") == (1,)


def test_is_newer_handles_padding_and_order() -> None:
    assert updater.is_newer("1.1.0", "1.0.0")
    assert updater.is_newer("1.0.1", "1.0")
    assert updater.is_newer("0.10.0", "0.9.9")
    assert not updater.is_newer("1.0.0", "1.0.0")
    assert not updater.is_newer("0.9.9", "1.0.0")


# --- 格式化 -----------------------------------------------------------------

def test_human_size() -> None:
    assert updater.human_size(0) == "0 B"
    assert updater.human_size(512) == "512 B"
    assert updater.human_size(2048) == "2.0 KB"
    assert updater.human_size(5 * 1024 * 1024) == "5.0 MB"


def test_format_time_localizes_and_tolerates_bad_input() -> None:
    assert updater.format_time("2026-10-16T08:00:00Z").startswith("2026-10-16")
    assert updater.format_time("not-a-time") == "not-a-time"
    assert updater.format_time("") == "未知"


def test_release_lines_include_time_and_size() -> None:
    release = _release(assets=(updater.Asset("pkg.whl", 2048, "https://d"),))
    text = "\n".join(release.lines())

    assert "v1.1.0" in text
    assert "2026-10-16" in text
    assert "2.0 KB" in text
    assert release.version == "1.1.0"


# --- 资产选择 ---------------------------------------------------------------

def test_pick_asset_prefers_wheel_over_sdist() -> None:
    release = _release(
        assets=(
            updater.Asset("pkg-1.0.tar.gz", 10, "https://s"),
            updater.Asset("pkg-1.0-py3-none-any.whl", 20, "https://w"),
        )
    )
    picked = updater.pick_asset(release)
    assert picked is not None and picked.name.endswith(".whl")


def test_pick_asset_falls_back_and_handles_empty() -> None:
    sdist = _release(assets=(updater.Asset("pkg-1.0.tar.gz", 10, "https://s"),))
    assert updater.pick_asset(sdist).name.endswith(".tar.gz")  # type: ignore[union-attr]
    assert updater.pick_asset(_release()) is None


# --- 发行数据解析 -----------------------------------------------------------

def test_fetch_latest_parses_payload(monkeypatch) -> None:
    payload = {
        "tag_name": "v1.1.0",
        "name": "IFP v1.1.0",
        "published_at": "2026-10-16T08:00:00Z",
        "html_url": "https://example.test/releases/tag/v1.1.0",
        "body": "release notes",
        "assets": [
            {"name": "ifp-1.1.0-py3-none-any.whl", "size": 2048, "browser_download_url": "https://d"}
        ],
    }
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout, token: payload)

    release = updater.fetch_latest("acme/ifp")

    assert release.tag == "v1.1.0"
    assert release.version == "1.1.0"
    assert release.notes == "release notes"
    assert release.assets[0].size == 2048


def test_fetch_latest_prefers_stable_over_prerelease(monkeypatch) -> None:
    payload = [
        {"tag_name": "v1.2.0-rc1", "prerelease": True, "assets": []},
        {"tag_name": "v1.1.0", "prerelease": False, "assets": []},
    ]
    monkeypatch.setattr(updater, "_get_json", lambda *args, **kwargs: payload)

    assert updater.fetch_latest("acme/ifp", include_prerelease=True).tag == "v1.1.0"


def test_fetch_latest_stable_uses_latest_endpoint(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake(url, timeout, token):
        seen["url"] = url
        return {"tag_name": "v1.0.0", "assets": []}

    monkeypatch.setattr(updater, "_get_json", fake)
    updater.fetch_latest("acme/ifp")

    assert seen["url"].endswith("/releases/latest")


def test_fetch_latest_prerelease_uses_list_endpoint(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake(url, timeout, token):
        seen["url"] = url
        return [{"tag_name": "v1.0.0", "prerelease": False, "assets": []}]

    monkeypatch.setattr(updater, "_get_json", fake)
    updater.fetch_latest("acme/ifp", include_prerelease=True)

    assert seen["url"].endswith("/releases")


def test_fetch_latest_rejects_bad_source() -> None:
    with pytest.raises(updater.UpdateError):
        updater.fetch_latest("not-a-valid-source")


def test_fetch_latest_rejects_bad_payload(monkeypatch) -> None:
    monkeypatch.setattr(updater, "_get_json", lambda *args, **kwargs: ["unexpected"])
    with pytest.raises(updater.UpdateError):
        updater.fetch_latest("acme/ifp")


# --- 无发行版本 / 无资产时的回退 -------------------------------------------

def test_fetch_latest_falls_back_to_tags(monkeypatch) -> None:
    def fake(url, timeout, token):
        if url.endswith("/releases/latest"):
            raise updater._NotFound(url)
        if url.endswith("/releases"):
            return []
        return [{"name": "v1.0.0"}, {"name": "v0.3.1"}]

    monkeypatch.setattr(updater, "_get_json", fake)
    release = updater.fetch_latest("acme/ifp")

    assert release.tag == "v1.0.0"
    assert release.published_at == ""
    assert release.assets == ()


def test_fetch_latest_prerelease_falls_back_to_tags(monkeypatch) -> None:
    def fake(url, timeout, token):
        return [{"name": "v2.0.0"}] if url.endswith("/tags") else []

    monkeypatch.setattr(updater, "_get_json", fake)
    assert updater.fetch_latest("acme/ifp", include_prerelease=True).tag == "v2.0.0"


def test_fetch_latest_raises_when_nothing_available(monkeypatch) -> None:
    def fake(url, timeout, token):
        raise updater._NotFound(url)

    monkeypatch.setattr(updater, "_get_json", fake)
    with pytest.raises(updater.UpdateError):
        updater.fetch_latest("acme/ifp")


def test_install_release_falls_back_to_tag_when_asset_missing(monkeypatch) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        updater, "install_from_git", lambda source, ref: seen.update(source=source, ref=ref)
    )

    got = updater.install_release(_release(tag="v1.0.0"), source="acme/ifp")

    assert got == "acme/ifp@v1.0.0"
    assert seen == {"source": "acme/ifp", "ref": "v1.0.0"}


def test_install_release_without_asset_or_source_raises() -> None:
    with pytest.raises(updater.UpdateError):
        updater.install_release(_release(tag="v1.0.0"))


# --- 错误映射 ---------------------------------------------------------------

def test_get_json_maps_404(monkeypatch) -> None:
    def boom(request, timeout):
        raise urllib.error.HTTPError("https://x", 404, "not found", {}, None)

    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    with pytest.raises(updater.UpdateError) as excinfo:
        updater._get_json("https://x", 1.0, None)
    assert "更新源" in str(excinfo.value)


def test_get_json_maps_403(monkeypatch) -> None:
    def boom(request, timeout):
        raise urllib.error.HTTPError("https://x", 403, "forbidden", {}, None)

    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    with pytest.raises(updater.UpdateError) as excinfo:
        updater._get_json("https://x", 1.0, None)
    assert "GITHUB_TOKEN" in str(excinfo.value)


def test_fetch_latest_maps_offline(monkeypatch) -> None:
    def boom(request, timeout):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    with pytest.raises(updater.UpdateError) as excinfo:
        updater.fetch_latest("acme/ifp")
    assert "无法访问更新源" in str(excinfo.value)


def test_get_json_maps_timeout(monkeypatch) -> None:
    def boom(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(updater.urllib.request, "urlopen", boom)
    with pytest.raises(updater.UpdateError) as excinfo:
        updater._get_json("https://x", 1.0, None)
    assert "超时" in str(excinfo.value)
