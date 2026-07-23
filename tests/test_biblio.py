"""書誌メタ取得の検証（ネットは叩かず、fetcher/enricher を注入）。"""

from __future__ import annotations

from fullocr import biblio
from fullocr.ir import DocMeta, Document, Page


def test_clean_query_strips_edition_and_brackets():
    assert biblio.clean_query("新版 脳神経外科診療のためのポケット分類集") == "脳神経外科診療のためのポケット分類集"
    assert "改訂" not in biblio.clean_query("改訂第2版 内科学 上巻")


def test_parse_google_item_extracts_isbn_and_fields():
    item = {"volumeInfo": {
        "title": "極論で語る神経内科",
        "authors": ["河合 真", "香坂 俊"],
        "publisher": "丸善出版",
        "publishedDate": "2016-03",
        "pageCount": 240,
        "categories": ["Medical"],
        "industryIdentifiers": [
            {"type": "ISBN_13", "identifier": "9784000000000"},
            {"type": "ISBN_10", "identifier": "4000000000"},
        ],
        "imageLinks": {"thumbnail": "http://img/cover.jpg"},
    }}
    m = biblio.parse_google_item(item)
    assert m.title == "極論で語る神経内科"
    assert m.isbn13 == "9784000000000"
    assert m.publisher == "丸善出版"
    assert m.page_count == 240
    assert m.cover_url == "http://img/cover.jpg"


def test_lookup_picks_best_match_and_enriches():
    items = [
        {"volumeInfo": {"title": "無関係な本", "industryIdentifiers": []}},
        {"volumeInfo": {"title": "極論で語る神経内科",
                        "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9784111111111"}]}},
    ]
    calls = {}

    def fake_fetch(q):
        calls["q"] = q
        return items

    def fake_enrich(isbn):
        calls["isbn"] = isbn
        return {"summary": {"publisher": "丸善出版", "cover": "http://c/x.jpg"}}

    m = biblio.lookup("極論で語る神経内科 河合 真", fetcher=fake_fetch, enricher=fake_enrich)
    assert m is not None
    assert m.title == "極論で語る神経内科"       # 一致度の高い方を選ぶ
    assert m.isbn13 == "9784111111111"
    assert m.publisher == "丸善出版"              # openBD で補完
    assert calls["isbn"] == "9784111111111"


def test_lookup_none_when_no_results():
    assert biblio.lookup("なにか", fetcher=lambda q: [], enricher=lambda i: None) is None


_NDL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <channel>
  <item>
    <title>極論で語る神経内科</title>
    <dc:creator>河合真</dc:creator>
    <dc:publisher>丸善出版</dc:publisher>
    <dcterms:issued>2016</dcterms:issued>
    <dc:identifier xsi:type="dcndl:ISBN">978-4-621-30000-0</dc:identifier>
  </item>
 </channel>
</rss>"""


def test_parse_ndl_xml_extracts_fields():
    cands = biblio.parse_ndl_xml(_NDL_XML)
    assert len(cands) == 1
    m = cands[0]
    assert m.title == "極論で語る神経内科"
    assert m.publisher == "丸善出版"
    assert m.isbn13 == "9784621300000"
    assert m.source == "ndl"
    assert "河合真" in m.authors


def test_search_candidates_combines_and_dedups():
    g = biblio.BiblioMeta(title="極論で語る神経内科", isbn13="9784621300000", source="google_books")
    n = biblio.BiblioMeta(title="極論で語る神経内科", isbn13="9784621300000", source="ndl")  # dup
    other = biblio.BiblioMeta(title="別の本", isbn13="9780000000000", source="ndl")
    cands = biblio.search_candidates(
        "極論で語る神経内科",
        providers=[lambda q: [g, other], lambda q: [n]],
    )
    titles = [c.title for c in cands]
    assert titles.count("極論で語る神経内科") == 1  # 重複は1件に
    assert "別の本" in titles
    assert cands[0].title == "極論で語る神経内科"  # 一致度が高い方が先頭


def test_apply_to_doc_writes_front_matter_fields():
    doc = Document(meta=DocMeta(title="旧題"), pages=[Page(number=1, width=10, height=10)])
    m = biblio.BiblioMeta(title="正式書名", authors=["著者A", "著者B"], publisher="出版社",
                          published="2020", isbn13="9784222222222", page_count=300,
                          categories=["医学"], cover_url="http://c")
    biblio.apply_to_doc(doc, m)
    assert doc.meta.title == "正式書名"
    assert doc.meta.author == "著者A, 著者B"
    assert doc.meta.isbn == "9784222222222"
    assert doc.meta.publisher == "出版社"
    assert doc.meta.categories == ["医学"]
