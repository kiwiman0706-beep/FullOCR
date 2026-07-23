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


def test_biblio_search_and_apply(tmp_path, monkeypatch):
    from fullocr import biblio as biblio_mod
    from fullocr.ir import DocMeta, Document, Page

    book = "サンプル書"
    (tmp_path / "work" / book).mkdir(parents=True)
    (tmp_path / "out" / book).mkdir(parents=True)
    Document(meta=DocMeta(title="旧題"), pages=[Page(number=1, width=10, height=10)]).save(
        tmp_path / "work" / book / "ir.json"
    )
    # base_dir を tmp に向ける（work/out の既定を差し替え）
    monkeypatch.setattr("fullocr.gui.server._base_dir", lambda: tmp_path)
    # ネットを叩かない: 候補検索と openBD を固定
    cand = biblio_mod.BiblioMeta(title="正式書名", isbn13="9784000000000", source="google_books")
    monkeypatch.setattr(biblio_mod, "search_candidates", lambda name: [cand])
    monkeypatch.setattr(biblio_mod, "enrich_with_openbd", lambda m: m)

    client = TestClient(app)
    s = client.post("/api/biblio_search", json={"book": book}).json()
    assert s["ok"] is True
    assert s["candidates"][0]["title"] == "正式書名"

    a = client.post("/api/biblio_apply", json={"book": book, "candidate": s["candidates"][0]}).json()
    assert a["ok"] is True
    # front-matter に ISBN が反映されている
    idx = (tmp_path / "out" / book / "00_index.md").read_text(encoding="utf-8")
    assert "9784000000000" in idx and "正式書名" in idx
