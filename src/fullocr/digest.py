"""AI要約・マインドマップ・一問一答カードの生成（ソースリンク付き）。

変換済みの IR（fullocr.ir.Document）から、章/節(or ページ窓)単位で要約し、各項目に
「原文PDFの該当ページ(#page=N)」「生成MDの該当見出し(wikilink)」へのリンクを付ける。

設計の肝（精度）:
    要約文はLLMに書かせるが、**引用ページはLLMに言わせない**。各ユニットのページ範囲は
    こちらが IR から把握しているので、それをリンクに使う（＝ページ番号を捏造しない）。

出力:
    render_digest_md() → Markdown（Obsidian: wikilink + Mermaid mindmap）
    build_epub()       → 本文同梱・内部アンカーで自己完結する EPUB（ebooklib、任意依存）

LLM は structure_llm と同じクライアント（make_client）を使う。APIキーが無ければ空の
Digest を返す（＝オフでも落ちない）。パース/組み立ては fake client で単体テスト可能。
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from .config import DigestConfig, PipelineConfig
from .ir import BlockType, Document
from .llm import LLMClient
from .render.markdown import _page_offset
from .render.toc import slugify

_SYSTEM = (
    "あなたは専門書の編集者です。与えられた節の本文だけを根拠に、"
    "正確で簡潔な日本語の要約素材を作ります。推測や本文に無い情報は足さない。"
    "出力は指定された JSON のみ。"
)


class QA(BaseModel):
    q: str
    a: str


class SectionDigest(BaseModel):
    heading: str
    page_start: int
    page_end: int
    summary: list[str] = Field(default_factory=list)
    qa: list[QA] = Field(default_factory=list)
    map_label: str = ""
    map_children: list[str] = Field(default_factory=list)


class BookDigest(BaseModel):
    title: str
    sections: list[SectionDigest] = Field(default_factory=list)


# ---- セグメント分割 ------------------------------------------------------

class _Section(BaseModel):
    heading: str
    page_start: int
    page_end: int
    text: str


_BODYISH = {BlockType.BODY, BlockType.LIST, BlockType.CAPTION, BlockType.FOOTNOTE}


def _heading_sections(doc: Document) -> list[_Section]:
    out: list[dict] = []
    cur: dict | None = None
    for page in doc.pages:
        for b in page.ordered_blocks():
            if b.type is BlockType.HEADING and b.text.strip():
                if cur:
                    out.append(cur)
                cur = {"heading": b.text.strip(), "page_start": page.number,
                       "page_end": page.number, "texts": []}
            else:
                if cur is None:
                    cur = {"heading": "（冒頭）", "page_start": page.number,
                           "page_end": page.number, "texts": []}
                if b.type in _BODYISH and b.text.strip():
                    cur["texts"].append(b.text.strip())
            if cur is not None:
                cur["page_end"] = page.number
    if cur:
        out.append(cur)
    return [_Section(heading=s["heading"], page_start=s["page_start"],
                     page_end=s["page_end"], text="\n".join(s["texts"])) for s in out]


def _window_sections(doc: Document, pages_per: int) -> list[_Section]:
    out: list[_Section] = []
    buf: list[str] = []
    start = None
    last = None
    for i, page in enumerate(doc.pages):
        if start is None:
            start = page.number
        last = page.number
        for b in page.ordered_blocks():
            if b.type in _BODYISH and b.text.strip():
                buf.append(b.text.strip())
        if (i + 1) % pages_per == 0:
            out.append(_Section(heading=f"pp. {start}–{last}", page_start=start,
                                page_end=last, text="\n".join(buf)))
            buf, start = [], None
    if buf and start is not None:
        out.append(_Section(heading=f"pp. {start}–{last}", page_start=start,
                            page_end=last, text="\n".join(buf)))
    return out


def segment(doc: Document, cfg: DigestConfig) -> list[_Section]:
    """見出しが実用的な数あればそれで、乏しければページ窓で分割する。"""
    hs = _heading_sections(doc)
    # 本文を持つ見出し節が2つ以上あり、過剰分割(ページ数の半分超)でなければ採用。
    good = [s for s in hs if s.text.strip()]
    if 2 <= len(good) <= max(2, len(doc.pages) // 2):
        return hs
    return _window_sections(doc, max(1, cfg.segment_pages))


# ---- LLM 呼び出し --------------------------------------------------------

def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit * 2 // 3]
    tail = text[-limit // 3:]
    return f"{head}\n…(中略)…\n{tail}"


def _prompt(sec: _Section, kinds: list[str]) -> str:
    want = []
    if "summary" in kinds:
        want.append('"summary": ["要点を3〜6個、各1文、本文の語彙で"]')
    if "mindmap" in kinds:
        want.append('"map": {"label": "この節の主題(10字程度の短語)", '
                    '"children": ["下位の概念やキーワードを2〜5個(各短語)"]}')
    if "qa" in kinds:
        want.append('"qa": [{"q": "本文で答えられる問い", "a": "簡潔な答え"}] (2〜4個)')
    shape = "{\n  " + ",\n  ".join(want) + "\n}"
    return (
        f"次の節「{sec.heading}」の本文を読み、下記 JSON だけを返してください。\n"
        f"本文に書かれていないことは書かないでください。\n\n"
        f"=== 本文 ===\n{sec.text}\n=== 本文ここまで ===\n\n"
        f"出力する JSON の形:\n{shape}\n"
    )


def _parse_json(resp: str) -> dict:
    if not resp:
        return {}
    m = re.search(r"\{.*\}", resp, re.DOTALL)  # コードフェンス等を無視して本体を取る
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _digest_section(sec: _Section, client: LLMClient, cfg: DigestConfig) -> SectionDigest:
    sd = SectionDigest(heading=sec.heading, page_start=sec.page_start, page_end=sec.page_end)
    if not sec.text.strip():
        return sd
    prompt = _prompt(sec.model_copy(update={"text": _truncate(sec.text, cfg.max_chars_per_section)}),
                     cfg.kinds)
    try:
        data = _parse_json(client.generate(prompt, system=_SYSTEM))
    except Exception:  # noqa: BLE001 - 1節の失敗で全体を止めない
        return sd
    sd.summary = [str(x).strip() for x in (data.get("summary") or []) if str(x).strip()]
    for item in data.get("qa") or []:
        if isinstance(item, dict) and item.get("q") and item.get("a"):
            sd.qa.append(QA(q=str(item["q"]).strip(), a=str(item["a"]).strip()))
    mp = data.get("map") or {}
    if isinstance(mp, dict):
        sd.map_label = str(mp.get("label") or sec.heading).strip()
        sd.map_children = [str(x).strip() for x in (mp.get("children") or []) if str(x).strip()]
    return sd


def build_digest(doc: Document, client: LLMClient, cfg: DigestConfig | None = None) -> BookDigest:
    cfg = cfg or DigestConfig()
    title = doc.meta.title or "document"
    sections = [_digest_section(s, client, cfg) for s in segment(doc, cfg)]
    return BookDigest(title=title, sections=sections)


# ---- ソースリンク --------------------------------------------------------

def _printed_label(page: int, offset: int | None) -> str:
    """表示用のページ番号。offset が分かれば印刷ページ、無ければPDF通し番号。"""
    if offset is not None:
        p = page - offset
        if p >= 1:
            return str(p)
    return str(page)


def _md_target(doc: Document) -> str:
    """生成MD本文ファイル名（既定の単一ファイル=slugify(title)）。"""
    return slugify(doc.meta.title or "document")


def _links(sec: SectionDigest, doc: Document, cfg: DigestConfig, offset: int | None) -> str:
    parts: list[str] = []
    if cfg.link_pdf and doc.meta.pdf_name:
        lab = _printed_label(sec.page_start, offset)
        parts.append(f"[[{doc.meta.pdf_name}#page={sec.page_start}|📄p.{lab}]]")
    if cfg.link_md and sec.heading and not sec.heading.startswith(("（", "pp.")):
        parts.append(f"[[{_md_target(doc)}#{sec.heading}|§{sec.heading}]]")
    return ("  " + " ・".join(parts)) if parts else ""


# ---- Markdown 出力 -------------------------------------------------------

def _mermaid_safe(label: str) -> str:
    # mindmap ノードで問題になる記号を除く。
    return re.sub(r"[()\[\]{}\"|#]", " ", label).strip() or "・"


def render_digest_md(digest: BookDigest, doc: Document, cfg: DigestConfig | None = None) -> str:
    cfg = cfg or DigestConfig()
    offset = _page_offset(doc)
    out: list[str] = [
        f"# {digest.title} — ダイジェスト",
        "> FullOCR が生成。各項目のリンクは原文PDF(#page)と生成MDの見出しを指します。"
        "要約文はAI、引用ページは原データに基づきます。\n",
    ]

    if "summary" in cfg.kinds:
        out.append("## 📖 構造化要約\n")
        for sec in digest.sections:
            if not sec.summary:
                continue
            link = _links(sec, doc, cfg, offset)
            out.append(f"### {sec.heading}{link}")
            out.extend(f"- {s}" for s in sec.summary)
            out.append("")

    if "mindmap" in cfg.kinds:
        out.append("## 🧠 マインドマップ\n")
        out.append("```mermaid")
        out.append("mindmap")
        out.append(f"  root(({_mermaid_safe(digest.title)}))")
        for sec in digest.sections:
            if not (sec.map_label or sec.map_children):
                continue
            lab = _mermaid_safe(sec.map_label or sec.heading)
            out.append(f"    {lab} ·p.{_printed_label(sec.page_start, offset)}")
            for ch in sec.map_children:
                out.append(f"      {_mermaid_safe(ch)}")
        out.append("```")
        out.append("")

    if "qa" in cfg.kinds:
        out.append("## 🎴 一問一答\n")
        for sec in digest.sections:
            if not sec.qa:
                continue
            link = _links(sec, doc, cfg, offset)
            out.append(f"### {sec.heading}{link}")
            for qa in sec.qa:
                out.append(f"- **Q.** {qa.q}  \n  **A.** {qa.a}")
            out.append("")

    return "\n".join(out).strip() + "\n"


# ---- EPUB 出力（任意依存: ebooklib） -------------------------------------

def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _body_xhtml(doc: Document, offset: int | None) -> str:
    """本文を、各ページ頭に内部アンカー <span id="pdf-N"> を打って XHTML 化。"""
    parts = ['<h1>本文</h1>']
    for page in doc.pages:
        lab = _printed_label(page.number, offset)
        parts.append(f'<p><span id="pdf-{page.number}"></span>'
                     f'<small>— p.{lab} —</small></p>')
        for b in page.ordered_blocks():
            t = b.text.strip()
            if not t:
                continue
            if b.type is BlockType.HEADING:
                parts.append(f"<h2>{_esc(t)}</h2>")
            elif b.type in _BODYISH:
                parts.append(f"<p>{_esc(t)}</p>")
    return "\n".join(parts)


def _digest_xhtml(digest: BookDigest, doc: Document, cfg: DigestConfig, offset: int | None) -> str:
    """要約/マインドマップ(入れ子<ul>)/カード。リンクは本文章の #pdf-N へ内部ジャンプ。"""
    def src(sec: SectionDigest) -> str:
        lab = _printed_label(sec.page_start, offset)
        return f' <a href="text.xhtml#pdf-{sec.page_start}">→ p.{lab}</a>'

    parts = [f"<h1>{_esc(digest.title)} — ダイジェスト</h1>"]
    if "summary" in cfg.kinds:
        parts.append("<h2>構造化要約</h2>")
        for sec in digest.sections:
            if not sec.summary:
                continue
            parts.append(f"<h3>{_esc(sec.heading)}{src(sec)}</h3><ul>")
            parts.extend(f"<li>{_esc(s)}</li>" for s in sec.summary)
            parts.append("</ul>")
    if "mindmap" in cfg.kinds:
        parts.append("<h2>マインドマップ</h2><ul>")
        for sec in digest.sections:
            if not (sec.map_label or sec.map_children):
                continue
            parts.append(f"<li>{_esc(sec.map_label or sec.heading)}{src(sec)}")
            if sec.map_children:
                parts.append("<ul>" + "".join(f"<li>{_esc(c)}</li>"
                                               for c in sec.map_children) + "</ul>")
            parts.append("</li>")
        parts.append("</ul>")
    if "qa" in cfg.kinds:
        parts.append("<h2>一問一答</h2>")
        for sec in digest.sections:
            if not sec.qa:
                continue
            parts.append(f"<h3>{_esc(sec.heading)}{src(sec)}</h3><ul>")
            parts.extend(f"<li><b>Q.</b> {_esc(qa.q)}<br/><b>A.</b> {_esc(qa.a)}</li>"
                         for qa in sec.qa)
            parts.append("</ul>")
    return "\n".join(parts)


def build_epub(digest: BookDigest, doc: Document, out_path, cfg: DigestConfig | None = None):
    """ダイジェスト＋本文を1冊の EPUB に（本文内アンカーで自己完結ジャンプ）。

    ebooklib が無ければ ImportError（CLI 側で導入を促す）。
    """
    from pathlib import Path

    import ebooklib  # noqa: F401
    from ebooklib import epub

    cfg = cfg or DigestConfig()
    offset = _page_offset(doc)
    book = epub.EpubBook()
    book.set_identifier(f"fullocr-digest-{slugify(digest.title)}")
    book.set_title(f"{digest.title} — ダイジェスト")
    book.set_language("ja")

    c_digest = epub.EpubHtml(title="ダイジェスト", file_name="digest.xhtml", lang="ja")
    c_digest.content = f"<html><body>{_digest_xhtml(digest, doc, cfg, offset)}</body></html>"
    c_text = epub.EpubHtml(title="本文", file_name="text.xhtml", lang="ja")
    c_text.content = f"<html><body>{_body_xhtml(doc, offset)}</body></html>"
    book.add_item(c_digest)
    book.add_item(c_text)
    book.toc = (c_digest, c_text)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", c_digest, c_text]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out_path), book)
    return out_path


# ---- 保存済みブックに対する一括処理（CLI/GUI 用） -----------------------

def digest_book(cfg: PipelineConfig, book: str, *, make_epub: bool = False) -> dict:
    """work/<book>/ir.json を読み、out/<book>/_digest.md（と任意で .epub）を書く。"""
    from .llm import make_client

    ir_path = cfg.work_dir / book / "ir.json"
    if not ir_path.exists():
        raise FileNotFoundError(f"ir.json がありません: {ir_path}")
    doc = Document.load(ir_path)

    client = make_client(cfg.structure_llm)
    if client is None:
        raise RuntimeError("LLM APIキーが未設定です（structure_llm.api_key か GEMINI_API_KEY）。")

    digest = build_digest(doc, client, cfg.digest)
    out_book = cfg.out_dir / book
    out_book.mkdir(parents=True, exist_ok=True)
    md_path = out_book / "_digest.md"
    md_path.write_text(render_digest_md(digest, doc, cfg.digest), encoding="utf-8")
    result = {"book": book, "md": str(md_path), "sections": len(digest.sections)}
    if make_epub:
        epub_path = build_epub(digest, doc, out_book / f"{slugify(digest.title)}_digest.epub",
                               cfg.digest)
        result["epub"] = str(epub_path)
    return result
