"""アプリの自動更新チェック（GitHub Releases）。

リポジトリ公開を前提に、認証なしで最新リリースの tag/アセットを取得する。
「最新版は何か・ダウンロードURL」という事実は GitHub API からのみ得る（LLMは使わない）。
fetch を差し替え可能にしてあるのでテストはネットに出ない（biblio.py と同じ方針）。

GUI の使い方:
  GET  /api/update_check → {current, latest, has_update, download_url, system, ...}
  POST /api/update_apply → Windows は最新インストーラをDL&起動（上書き更新）、
                           それ以外は html_url を返してフロントがリリースページを開く。
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from urllib.request import Request, urlopen

from . import GITHUB_REPO

_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_UA = "Theta.FullOCR-updater"


def _default_fetch(url: str) -> bytes:
    req = Request(  # noqa: S310 - 固定の https GitHub API のみ
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": _UA}
    )
    with urlopen(req, timeout=10) as r:  # noqa: S310
        return r.read()


def parse_version(tag: str) -> tuple[int, ...]:
    """'v0.11.0' / '0.11.0' → (0, 11, 0)。数字以外は無視し、比較可能なタプルにする。"""
    tag = (tag or "").strip().lstrip("vV")
    parts: list[int] = []
    for p in tag.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _pick_asset(assets: list[dict], suffix: str) -> dict | None:
    for a in assets:
        if str(a.get("name", "")).lower().endswith(suffix):
            return a
    return None


def check_for_update(current: str, *, fetch=_default_fetch, system: str | None = None) -> dict:
    """最新リリースを調べる。戻り値は常に同じ形（失敗時は error に理由）。"""
    sysname = (system or platform.system()).lower()  # 'windows' / 'darwin' / 'linux'
    out: dict = {
        "current": current, "latest": None, "has_update": False,
        "html_url": None, "download_url": None, "asset_name": None,
        "system": sysname, "error": None,
    }
    try:
        data = json.loads(fetch(_API))
    except Exception as e:  # noqa: BLE001 - ネット断/未公開でもGUIを止めない
        out["error"] = str(e)
        return out

    tag = data.get("tag_name") or ""
    out["latest"] = tag
    out["html_url"] = data.get("html_url")
    out["has_update"] = parse_version(tag) > parse_version(current)

    assets = data.get("assets") or []
    asset = None
    if sysname == "windows":
        asset = _pick_asset(assets, ".exe")
    elif sysname == "darwin":
        asset = _pick_asset(assets, ".dmg")
    if asset:
        out["download_url"] = asset.get("browser_download_url")
        out["asset_name"] = asset.get("name")
    return out


def download_installer(url: str, dest_dir: str | Path, *, fetch=_default_fetch) -> Path:
    """インストーラを dest_dir に保存してパスを返す。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1] or "installer"
    path = dest_dir / name
    path.write_bytes(fetch(url))
    return path


def launch_installer(path: str | Path) -> None:
    """インストーラを起動する（Windows）。Inno Setup が上書き更新を行う。"""
    import os

    os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606 - Windows専用
