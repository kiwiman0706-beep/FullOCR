"""自動更新チェック（updater）の検証。ネットには出ない（fetch を注入）。"""

from __future__ import annotations

import json

from fullocr import updater


def _release_json(tag, assets):
    return json.dumps({
        "tag_name": tag,
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "assets": [{"name": n, "browser_download_url": f"https://dl/{n}"} for n in assets],
    }).encode()


def test_parse_version():
    assert updater.parse_version("v0.11.0") == (0, 11, 0)
    assert updater.parse_version("0.11.0") == (0, 11, 0)
    assert updater.parse_version("v1.2") == (1, 2)
    assert updater.parse_version("") == (0,)
    assert updater.parse_version("v0.11.0") > updater.parse_version("v0.10.9")
    assert updater.parse_version("v0.11.0") > updater.parse_version("v0.2.0")


def test_has_update_true_and_picks_windows_exe():
    fetch = lambda url: _release_json("v0.12.0", ["FullOCR-Setup.exe", "FullOCR.dmg"])  # noqa: E731
    info = updater.check_for_update("0.11.0", fetch=fetch, system="Windows")
    assert info["has_update"] is True
    assert info["latest"] == "v0.12.0"
    assert info["download_url"] == "https://dl/FullOCR-Setup.exe"
    assert info["asset_name"] == "FullOCR-Setup.exe"
    assert info["system"] == "windows"
    assert info["error"] is None


def test_picks_dmg_on_mac():
    fetch = lambda url: _release_json("v0.12.0", ["FullOCR-Setup.exe", "FullOCR.dmg"])  # noqa: E731
    info = updater.check_for_update("0.11.0", fetch=fetch, system="Darwin")
    assert info["download_url"] == "https://dl/FullOCR.dmg"


def test_no_update_when_same_or_newer():
    fetch = lambda url: _release_json("v0.11.0", ["FullOCR-Setup.exe"])  # noqa: E731
    info = updater.check_for_update("0.11.0", fetch=fetch, system="Windows")
    assert info["has_update"] is False


def test_network_error_is_swallowed():
    def boom(url):
        raise OSError("no network")
    info = updater.check_for_update("0.11.0", fetch=boom, system="Linux")
    assert info["has_update"] is False
    assert info["error"] and "no network" in info["error"]
    assert info["latest"] is None


def test_download_installer_writes_file(tmp_path):
    fetch = lambda url: b"MZ-installer-bytes"  # noqa: E731
    path = updater.download_installer("https://dl/FullOCR-Setup.exe", tmp_path / "u", fetch=fetch)
    assert path.name == "FullOCR-Setup.exe"
    assert path.read_bytes() == b"MZ-installer-bytes"
