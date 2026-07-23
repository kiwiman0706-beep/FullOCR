"""GUI サーバの軽い検証（バージョン表示・キャッシュ無効化）。

fastapi/httpx（gui extra）が無い環境ではスキップする。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from fullocr import __version__  # noqa: E402
from fullocr.gui.server import app  # noqa: E402


def test_version_endpoint_reports_package_version():
    client = TestClient(app)
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.json() == {"version": __version__}


def test_index_is_not_cached_and_shows_version_slot():
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    # 埋め込みブラウザに古いページが残らないよう no-store を返す
    assert r.headers.get("cache-control") == "no-store"
    # バージョン表示用のスロットがある
    assert 'id="appVersion"' in r.text


def test_preview_and_asset(tmp_path):
    book = "本X"
    out_book = tmp_path / book
    (out_book / "attachments").mkdir(parents=True)
    (out_book / "00_index.md").write_text("# 見出し\n\n![[attachments/a.png]]\n", encoding="utf-8")
    (out_book / "attachments" / "a.png").write_bytes(b"\x89PNG\r\n")

    client = TestClient(app)
    r = client.post("/api/preview", json={"book": book, "out_dir": str(tmp_path)})
    d = r.json()
    assert d["ok"] is True
    assert "00_index.md" in d["files"]
    assert "見出し" in d["content"]

    # 資産配信 + パストラバーサル拒否
    ok = client.get("/api/asset", params={"book": book, "out_dir": str(tmp_path), "path": "attachments/a.png"})
    assert ok.status_code == 200
    bad = client.get("/api/asset", params={"book": book, "out_dir": str(tmp_path), "path": "../../etc/passwd"})
    assert bad.status_code == 404
