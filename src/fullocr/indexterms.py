"""巻末索引 → 見出し語リスト抽出（Stage 1）。

自炊PDFの巻末索引から、かな漢字変換辞書（Mozc/Gboard）用の「見出し語（表記のみ）」を
1行1語で取り出す。読みの付与（Stage 2）は別スレッド(Claude)に任せる設計なので、ここは
表記の抽出だけを行う。

抽出は index_pages（tocdetect が検出）のOCRテキストを LLM に渡して行い、機械的な保険
フィルタを後段にかける。LLM が無い/キー無しなら何もしない（空リスト）。
"""

from __future__ import annotations

import re

from .config import StructureLLMConfig
from .ir import BlockType, Document
from .llm import LLMClient

# ラテン文字・% を含む語は捨てる（KRAS遺伝子…, AIDS, 5-FU, A00-B99 等）。
_HAS_LATIN = re.compile(r"[A-Za-z%]")
# 日本語（ひらがな・カタカナ・漢字）を1文字も含まない行は捨てる（数字/記号のみ等）。
# ※仕様の「かなを含まない行を捨てる」は、意識障害など全漢字語まで落とすため、
#   「日本語文字を1つも含まない行を捨てる」に解釈を広げている（医学用語は漢字主体）。
_HAS_JA = re.compile(r"[぀-ヿ㐀-鿿ぁ-んァ-ヶ一-龯々〆ヵヶ]")

_SYSTEM = (
    "あなたは書籍の巻末索引テキストから、かな漢字変換辞書に登録する価値のある「見出し語」"
    "だけを抜き出す抽出器です。表記（見出し語の文字列）を1行1語で出力します。"
    "読み・ページ番号・記号・説明は出力しません。純粋な語のリストだけを返します。"
)

_PROMPT = """\
次の書籍の巻末索引テキスト（1ページ分）から、辞書に登録する価値のある見出し語だけを、
表記（見出し語の文字列）で1行1語で出力してください。読み・ページ番号・記号・説明は不要。

# 採用
- 各項目の見出し語（医学・専門用語の単語やフレーズ）。
- カタカナ外来語・人名由来の症候群名も採用（例: アシュネル眼球現象、ワレンベルグ症候群）。

# 除外
1. 副項目（サブエントリ）: 行頭が「，」「,」「──」「—」「-」で始まり上位語の修飾になるもの、
   および見出しより深くインデントされた下位項目。
2. 説明的フレーズ: 「の」「および」「あるいは」「または」「を伴う」「を伴わない」「による」
   「に関連した」「に伴う」「を主とする」「を含む」や「特定不能」「他に分類」等の分類注記を含む行。
   ※例外: それ自体が確立した固有名（「人形の目現象」「ワニの涙症候群」等）は採用可。
3. 半角ラテン文字を含む表記（KRAS遺伝子…、AIDS、HIV、5-FU、ICDコード A00-B99 等）。
   ※数字は可（「1型糖尿病」「13トリソミー」「第7頚椎」は採用）。

# 正規化
- 末尾のページ番号・参照番号（「… 158」「57, 61」）を除去。
- 丸括弧・角括弧の中身を削除:「社会［社交］恐怖［症］」→「社会恐怖症」。
- 中黒・ハイフンの連結は詰める:「シャイ・ドレーガー症候群」→「シャイドレーガー症候群」。
- 同一表記は1回だけ。

# 入力（索引テキスト）
{text}

# 出力（表記のみ・1行1語）
"""


def index_page_texts(doc: Document) -> list[str]:
    """index_pages 各ページの本文を読み順に連結した文字列のリスト。"""
    wanted = set(doc.meta.index_pages or [])
    out: list[str] = []
    for page in doc.pages:
        if page.number not in wanted:
            continue
        lines = [b.text.strip() for b in page.ordered_blocks()
                 if b.type in (BlockType.BODY, BlockType.LIST, BlockType.HEADING) and b.text.strip()]
        if lines:
            out.append("\n".join(lines))
    return out


def _accept(term: str) -> bool:
    """機械的な保険フィルタ（LLM出力の後段）。"""
    if not term or _HAS_LATIN.search(term):
        return False
    if not _HAS_JA.search(term):
        return False
    return True


def extract_index_terms(doc: Document, client: LLMClient, cfg: StructureLLMConfig) -> list[str]:
    """索引ページから見出し語（表記のみ）を抽出して返す。重複は除去、順序は保持。"""
    pages = index_page_texts(doc)
    if not pages:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for text in pages:
        try:
            resp = client.generate(_PROMPT.format(text=text), system=_SYSTEM)
        except Exception:  # noqa: BLE001 - 1ページ失敗で全体を止めない
            continue
        for line in (resp or "").splitlines():
            t = line.strip().lstrip("-•・ 　").strip()
            if not _accept(t) or t in seen:
                continue
            seen.add(t)
            terms.append(t)
    return terms
