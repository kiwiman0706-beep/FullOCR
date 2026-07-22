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
