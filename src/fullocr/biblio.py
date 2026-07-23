"""書誌メタ情報の取得（ISBN・出版社・刊行年など）。

自炊PDFのファイル名から書名を推定し、書誌データベースAPIで**実在の**メタ情報を引く。
事実（ISBN/出版社/刊行年/ページ数）は必ずDB由来にする（LLMにISBN等を作らせない）。

流れ:
    ファイル名 → clean_query → Google Books 検索 → 最も一致する版を選ぶ
              → ISBN が取れれば openBD で和書の書影・出版社を補完

ネットワークはユーザーのPC上で実行される前提。取得系関数（fetcher/enricher）は
差し替え可能にしてあり、テストでは固定JSONを渡す（実APIは叩かない）。
標準ライブラリ(urllib)のみ使用（追加依存なし）。
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable

from pydantic import BaseModel

from .ir import Document

_UA = "FullOCR/1.0 (self-scan metadata lookup)"
_GOOGLE_BOOKS = "https://www.googleapis.com/books/v1/volumes"
_OPENBD = "https://api.openbd.jp/v1/get"

# ファイル名から取り除いて検索に使う版・巻の接頭/接尾（記録には別途残せる）。
# 長い表記から先にマッチさせる（改訂新版 を 改訂 より先に）。
_EDITION = re.compile(
    r"(改訂新版|増補改訂版|改訂版|増補版|新装版|新版|増補改訂|改訂|増補"
    r"|第\s*[0-9０-９]+\s*版|[上中下]巻|全\s*[0-9０-９]+\s*巻)"
)
_BRACKETS = re.compile(r"[【】\[\]（）()]")


class BiblioMeta(BaseModel):
    title: str | None = None
    authors: list[str] = []
    publisher: str | None = None
    published: str | None = None      # 刊行日（YYYY / YYYY-MM-DD）
    isbn13: str | None = None
    isbn10: str | None = None
    page_count: int | None = None
    categories: list[str] = []
    cover_url: str | None = None
    source: str | None = None         # "google_books" / "openbd" 等


def clean_query(filename_stem: str) -> str:
    """ファイル名（拡張子なし）を検索クエリに整える。"""
    s = _BRACKETS.sub(" ", filename_stem)
    s = _EDITION.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def _http_get_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https固定URL
        return json.loads(resp.read().decode("utf-8"))


def google_books_search(query: str, timeout: float = 8.0) -> list[dict]:
    """Google Books で検索して items（生JSON）を返す。失敗時は空。"""
    url = f"{_GOOGLE_BOOKS}?q={urllib.parse.quote(query)}&maxResults=5&country=JP"
    try:
        data = _http_get_json(url, timeout)
    except Exception:  # noqa: BLE001 - ネット失敗で全体を止めない
        return []
    return data.get("items", []) if isinstance(data, dict) else []


def openbd_by_isbn(isbn: str, timeout: float = 8.0) -> dict | None:
    """openBD で ISBN から和書詳細（書影・出版社）を引く。失敗/無しは None。"""
    if not isbn:
        return None
    try:
        data = _http_get_json(f"{_OPENBD}?isbn={urllib.parse.quote(isbn)}", timeout)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def parse_google_item(item: dict) -> BiblioMeta:
    """Google Books の1件を BiblioMeta に正規化する（純粋関数）。"""
    vi = (item or {}).get("volumeInfo", {}) or {}
    isbn13 = isbn10 = None
    for ident in vi.get("industryIdentifiers", []) or []:
        t, v = ident.get("type"), ident.get("identifier")
        if t == "ISBN_13":
            isbn13 = v
        elif t == "ISBN_10":
            isbn10 = v
    images = vi.get("imageLinks", {}) or {}
    return BiblioMeta(
        title=vi.get("title"),
        authors=list(vi.get("authors", []) or []),
        publisher=vi.get("publisher"),
        published=vi.get("publishedDate"),
        isbn13=isbn13,
        isbn10=isbn10,
        page_count=vi.get("pageCount"),
        categories=list(vi.get("categories", []) or []),
        cover_url=images.get("thumbnail") or images.get("smallThumbnail"),
        source="google_books",
    )


def _score(query: str, meta: BiblioMeta) -> int:
    """クエリと候補タイトルの一致度（雑な文字重なり）。高いほど良い。"""
    q, t = _norm(query), _norm(meta.title or "")
    if not q or not t:
        return 0
    common = sum(1 for ch in set(t) if ch in q)
    bonus = 5 if (t in q or q in t) else 0
    return common + bonus


def lookup(
    filename_stem: str,
    *,
    fetcher: Callable[[str], list[dict]] = google_books_search,
    enricher: Callable[[str], dict | None] = openbd_by_isbn,
) -> BiblioMeta | None:
    """ファイル名から書誌メタを引く。見つからなければ None。

    fetcher/enricher は差し替え可能（テストで固定データを渡す）。
    """
    query = clean_query(filename_stem)
    if not query:
        return None
    items = fetcher(query)
    if not items:
        return None
    candidates = [parse_google_item(it) for it in items]
    best = max(candidates, key=lambda m: _score(query, m))
    # ISBN があれば openBD で和書情報を補完（出版社・書影）。
    isbn = best.isbn13 or best.isbn10
    if isbn:
        ob = enricher(isbn)
        if ob:
            summary = (ob.get("summary") or {})
            if not best.publisher and summary.get("publisher"):
                best.publisher = summary["publisher"]
            if not best.cover_url and summary.get("cover"):
                best.cover_url = summary["cover"]
            if summary.get("title") and not best.title:
                best.title = summary["title"]
    return best


def apply_to_doc(doc: Document, meta: BiblioMeta) -> None:
    """取得した書誌メタを DocMeta に流し込む（front-matter に出る）。"""
    m = doc.meta
    if meta.title:
        m.title = meta.title
    if meta.authors:
        m.author = ", ".join(meta.authors)
    m.publisher = meta.publisher or m.publisher
    m.published = meta.published or m.published
    m.isbn = meta.isbn13 or meta.isbn10 or m.isbn
    m.page_count = meta.page_count or m.page_count
    if meta.categories:
        m.categories = meta.categories
    m.cover_url = meta.cover_url or m.cover_url
