"""LLM による構造化パス（見出し階層の付与 ＋ 構成の評価レポート）。

Markdown 化の主目的である「目次・構造化」の品質を上げる任意ステップ。merge 後・
render 前に走る。

- 見出し階層付与: Docling/MinerU は見出しレベルを一律にしか返さない（アウトラインが
  平坦になる）。抽出済みの見出し列に「直後の本文スニペット」と「巻頭テキスト（印刷
  された目次が載っていることが多い）」を添えて LLM に渡し、H1〜H6 を割り当てる。
  実際には見出しでない誤検出（Docling の過検出）は本文へ降格させる。
- 評価レポート: 出来上がったアウトラインを、巻頭の実目次と突き合わせて「一致して
  いるか」「Markdown 構成として適切か」を LLM に評価させ、_structure_review.md に出す。

LLM 依存部は llm.LLMClient(generate) だけ。プロバイダは差し替え可能。API キーが
無ければ run_structure_llm は何もしない（＝オフでも従来通り動く）。
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

from ..config import StructureLLMConfig
from ..ir import Block, BlockType, Document
from ..llm import LLMClient, make_client


def _heading_blocks(doc: Document) -> list[Block]:
    """全ページを (ページ番号, 読み順) で走査した見出しブロック列。"""
    result: list[Block] = []
    for page in doc.pages:
        for b in page.ordered_blocks():
            if b.type is BlockType.HEADING and b.text.strip():
                result.append(b)
    return result


def _ordered_blocks(doc: Document) -> list[Block]:
    return [b for page in doc.pages for b in page.ordered_blocks()]


def _heading_entries(doc: Document, snippet_chars: int) -> list[dict]:
    """LLM に渡す見出しエントリ [{i, text, page, snippet}] を作る。

    snippet は各見出し直後に続く本文(BODY)テキストの先頭 snippet_chars 文字。
    「本当に見出しか」「章か節か」を判断する材料になる。
    """
    blocks = _ordered_blocks(doc)
    # ブロック→ページ番号の対応
    page_of: dict[int, int] = {}
    for page in doc.pages:
        for b in page.ordered_blocks():
            page_of[id(b)] = page.number

    entries: list[dict] = []
    hi = 0
    for idx, b in enumerate(blocks):
        if b.type is not BlockType.HEADING or not b.text.strip():
            continue
        # 直後の本文を集める
        snippet_parts: list[str] = []
        total = 0
        for nb in blocks[idx + 1 :]:
            if nb.type is BlockType.HEADING:
                break
            if nb.type is BlockType.BODY and nb.text.strip():
                snippet_parts.append(nb.text.strip())
                total += len(nb.text)
                if total >= snippet_chars:
                    break
        snippet = "".join(snippet_parts)[:snippet_chars]
        entries.append(
            {"i": hi, "text": b.text.strip(), "page": page_of.get(id(b), 0), "snippet": snippet}
        )
        hi += 1
    return entries


def _reference_head_text(doc: Document, max_chars: int) -> str:
    """巻頭から読み順に本文テキストを連結（印刷された目次を拾う狙い）。"""
    parts: list[str] = []
    total = 0
    for b in _ordered_blocks(doc):
        t = b.text.strip()
        if not t:
            continue
        parts.append(t)
        total += len(t)
        if total >= max_chars:
            break
    return "\n".join(parts)[:max_chars]


_SYSTEM = (
    "あなたは日本語の専門書のレイアウトと目次構造の専門家です。"
    "OCR/レイアウト解析で抽出された見出し候補に、本の実際の階層（部・章・節・項）に"
    "沿った見出しレベルを割り当てます。必ず指定された JSON のみを出力します。"
)

_TOC_IMAGE_PROMPT = (
    "これは書籍の「目次」ページの画像です。印刷されている目次を、階層（部・章・節・項の"
    "深さ）を保ったままテキストに書き起こしてください。各項目はインデントで階層を表し、"
    "分かる場合は行末にページ番号を付けてください。前置き・説明は不要、目次本体のみ。"
)


def _toc_page_images(doc: Document, max_pages: int = 4) -> list[bytes]:
    """DocMeta.toc_pages のページ画像を PNG バイト列で返す（マルチモーダル送付用）。"""
    try:
        from PIL import Image
    except ImportError:
        return []
    wanted = set(doc.meta.toc_pages or [])
    if not wanted:
        return []
    out: list[bytes] = []
    for page in doc.pages:
        if page.number not in wanted or not page.image_path:
            continue
        if not Path(page.image_path).exists():
            continue
        try:
            with Image.open(page.image_path) as im:
                buf = io.BytesIO()
                im.convert("RGB").save(buf, format="PNG")
                out.append(buf.getvalue())
        except Exception:  # noqa: BLE001 - 画像1枚の失敗で全体を止めない
            continue
        if len(out) >= max_pages:
            break
    return out


def extract_toc_from_images(doc: Document, client: LLMClient) -> str | None:
    """印刷目次ページの画像を LLM に読ませ、目次テキストを書き起こす。

    toc_pages が無い / ページ画像が無い / クライアントが画像非対応 なら None。
    """
    if not hasattr(client, "generate_with_images"):
        return None
    images = _toc_page_images(doc)
    if not images:
        return None
    try:
        text = client.generate_with_images(_TOC_IMAGE_PROMPT, images, system=_SYSTEM)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None
    return (text or "").strip() or None


def build_heading_prompt(title: str, entries: list[dict], reference: str) -> str:
    """見出し階層付与プロンプト。JSON のみを返すよう指示する。"""
    lines = [
        f"書名: {title}",
        "",
        "# 参考: 本の巻頭テキスト（印刷された目次が含まれることがある。見出しの"
        "正式表記や階層の手掛かりに使う）",
        reference or "（なし）",
        "",
        "# 見出し候補（読み順）。各行 i / text（見出し文） / page / snippet（直後の本文冒頭）",
    ]
    for e in entries:
        lines.append(
            f"- i={e['i']} | text={e['text']!r} | page={e['page']} | snippet={e['snippet']!r}"
        )
    lines += [
        "",
        "# 指示",
        "- 各 i について、本の階層に沿った見出しレベル level(1-6) を割り当てる"
        "（部=1, 章=1〜2, 節=2〜3, 項=3〜4 のように相対的な深さで）。",
        "- 番号付け（第N章 / N.N / 極論N / ■ 等）・巻頭目次・snippet を根拠にする。",
        "- 実際には見出しでない誤検出（本文の一部、柱・ノンブル、図表キャプション等）は "
        'keep=false にする。それ以外は keep=true。',
        "- 出力は次の JSON のみ。前後に文章やコードフェンスを付けない。",
        '{"headings":[{"i":0,"level":1,"keep":true}, ...]}',
    ]
    return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    """LLM 出力から JSON オブジェクトを取り出す（コードフェンス等に耐える）。"""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # 最初の { から最後の } までを試す
    start, end = s.find("{"), s.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(s[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def parse_heading_response(text: str, n: int) -> dict[int, tuple[int | None, bool]]:
    """LLM 応答を {i: (level, keep)} に。壊れていれば空 dict（＝適用なし）。"""
    obj = _extract_json(text)
    if not obj or not isinstance(obj.get("headings"), list):
        return {}
    out: dict[int, tuple[int | None, bool]] = {}
    for item in obj["headings"]:
        if not isinstance(item, dict):
            continue
        try:
            i = int(item["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= i < n):
            continue
        keep = bool(item.get("keep", True))
        level = item.get("level")
        try:
            level = int(level) if level is not None else None
        except (TypeError, ValueError):
            level = None
        out[i] = (level, keep)
    return out


def apply_hierarchy(doc: Document, decisions: dict[int, tuple[int | None, bool]]) -> int:
    """decisions を見出しブロックへ適用。level を設定、keep=false は本文へ降格。

    戻り値: 変更した見出し数。
    """
    blocks = _heading_blocks(doc)
    changed = 0
    for i, b in enumerate(blocks):
        if i not in decisions:
            continue
        level, keep = decisions[i]
        if not keep:
            b.type = BlockType.BODY
            b.heading_level = None
            changed += 1
        elif level is not None:
            b.heading_level = min(6, max(1, level))
            changed += 1
    return changed


def assign_hierarchy(doc: Document, client: LLMClient, cfg: StructureLLMConfig) -> int:
    """見出し階層を LLM で付与し直す。失敗時は 0（no-op）。

    cfg.toc_image_reference が真なら、印刷目次ページの画像を LLM に読ませた目次を
    参照に使う（OCR誤りに強い）。取得できなければ従来の巻頭テキストにフォールバック。
    """
    entries = _heading_entries(doc, cfg.snippet_chars)
    if not entries:
        return 0
    reference = None
    if cfg.toc_image_reference:
        toc = extract_toc_from_images(doc, client)
        if toc:
            reference = "（印刷目次ページの画像から読み取り）\n" + toc
    if reference is None:
        reference = _reference_head_text(doc, cfg.reference_head_chars)
    prompt = build_heading_prompt(doc.meta.title or "（無題）", entries, reference)
    try:
        resp = client.generate(prompt, system=_SYSTEM)
    except Exception:  # noqa: BLE001 - LLM失敗でパイプラインを止めない
        return 0
    decisions = parse_heading_response(resp, len(entries))
    return apply_hierarchy(doc, decisions)


def outline_text(doc: Document) -> str:
    """現在の見出し（階層付き）をインデント表示に。評価プロンプト用。"""
    lines: list[str] = []
    for b in _heading_blocks(doc):
        lvl = min(6, max(1, b.heading_level or 1))
        lines.append(f"{'  ' * (lvl - 1)}- (H{lvl}) {b.text.strip()}")
    return "\n".join(lines)


def build_review_prompt(title: str, outline: str, reference: str) -> str:
    return "\n".join(
        [
            f"書名: {title}",
            "",
            "# 生成された見出しアウトライン（Markdown の #/##… に対応）",
            outline or "（見出しなし）",
            "",
            "# 参考: 本の巻頭テキスト（印刷された目次が含まれることがある）",
            reference or "（なし）",
            "",
            "# 依頼",
            "上のアウトラインについて、日本語で簡潔に評価してください:",
            "1. 巻頭の実目次と一致しているか（抜け・重複・順序・レベルのズレ）。",
            "2. Markdown の構成として適切か（階層の深さ、見出しの粒度、誤検出の残り）。",
            "3. 具体的な修正提案（どの見出しをどのレベルに、何を削除/追加すべきか）。",
            "Markdown の箇条書きで、要点を絞って出力してください。",
        ]
    )


def review_structure(doc: Document, client: LLMClient, cfg: StructureLLMConfig) -> str | None:
    """構成評価レポート(Markdown文字列)を返す。失敗時 None。"""
    prompt = build_review_prompt(
        doc.meta.title or "（無題）",
        outline_text(doc),
        _reference_head_text(doc, cfg.reference_head_chars),
    )
    try:
        return client.generate(prompt, system=_SYSTEM) or None
    except Exception:  # noqa: BLE001
        return None


# --- 本文の異常検出（変な改行・ページ飛び等） -----------------------------

def _page_texts(doc: Document) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for page in doc.pages:
        t = "".join(
            b.text for b in page.ordered_blocks()
            if b.type in (BlockType.BODY, BlockType.HEADING) and b.text.strip()
        )
        out.append((page.number, t))
    return out


def _boundary_contexts(doc: Document, n: int = 80) -> str:
    """ページ境界の前後だけを抜き出す（全文より遥かに安いのに、ページ飛び検出に有効）。"""
    pts = _page_texts(doc)
    lines: list[str] = []
    for i in range(len(pts) - 1):
        (pa, ta), (pb, tb) = pts[i], pts[i + 1]
        lines.append(f"[p{pa}末] …{ta[-n:]}  ‖  [p{pb}頭] {tb[:n]}…")
    return "\n".join(lines)


def _full_text(doc: Document) -> str:
    return "\n".join(f"[p{no}] {t}" for no, t in _page_texts(doc))


def check_anomalies(doc: Document, client: LLMClient, cfg: StructureLLMConfig) -> str | None:
    """本文の異常（不自然な改行、ページ飛び、脱落など）を検出したレポートを返す。"""
    if cfg.anomaly_full_text:
        payload = _full_text(doc)
        scope = "本文全体（[pN] はページ番号）"
    else:
        payload = _boundary_contexts(doc)
        scope = "各ページ境界の前後（‖ の左が前ページ末、右が次ページ頭）"
    prompt = "\n".join([
        f"書名: {doc.meta.title or '（無題）'}",
        "",
        f"# 点検対象（{scope}）",
        payload or "（本文なし）",
        "",
        "# 依頼",
        "日本語で簡潔に、次の異常だけを箇条書きで指摘してください（無ければ「異常なし」）:",
        "- 文の途中で不自然に切れている／ページ境界で文がつながっていない箇所",
        "- 内容が飛んでいる（ページや段落が抜け落ちていそう）箇所",
        "- 重複・明らかな誤認識の混入",
        "指摘には該当ページ番号を添えてください。修正文の全文出力は不要です。",
    ])
    try:
        return client.generate(prompt, system=_SYSTEM) or None
    except Exception:  # noqa: BLE001
        return None


# --- 本文リライト（構造・画像は保持し、段落テキストだけ整える） --------------

def _body_blocks(doc: Document) -> list[Block]:
    return [b for page in doc.pages for b in page.ordered_blocks()
            if b.type is BlockType.BODY and b.text.strip()]


def rewrite_body(doc: Document, client: LLMClient, cfg: StructureLLMConfig, batch: int = 40) -> int:
    """本文段落の不自然な改行・空白・明らかな誤字を LLM で整える（意味は変えない）。

    見出し・図・表・キャプションは対象外（構造と画像配置を保持）。段落ブロック単位で
    番号付けして送り、JSON で返させて突き合わせる。戻り値: 書き換えた段落数。
    """
    blocks = _body_blocks(doc)
    changed = 0
    for start in range(0, len(blocks), batch):
        chunk = blocks[start : start + batch]
        listing = "\n".join(f"[{i}] {b.text}" for i, b in enumerate(chunk))
        prompt = "\n".join([
            "次の各段落は、自炊本OCRの本文です。OCR由来の不自然な改行・余分な空白・"
            "明らかな誤認識だけを整え、意味・語彙・数値は一切変えないでください。",
            "内容の要約・追記・削除はしないこと。",
            "",
            listing,
            "",
            '出力は次の JSON のみ: {"items":[{"i":0,"text":"整えた段落"}, ...]}',
        ])
        try:
            resp = client.generate(prompt, system=_SYSTEM)
        except Exception:  # noqa: BLE001
            continue
        obj = _extract_json(resp)
        if not obj or not isinstance(obj.get("items"), list):
            continue
        for item in obj["items"]:
            if not isinstance(item, dict):
                continue
            try:
                i = int(item["i"])
            except (KeyError, TypeError, ValueError):
                continue
            text = item.get("text")
            if 0 <= i < len(chunk) and isinstance(text, str) and text.strip():
                chunk[i].text = text.strip()
                changed += 1
    return changed


# --- 挿絵の代替テキスト生成（マルチモーダル） -------------------------------

_ALT_PROMPT = (
    "これは書籍中の挿絵・図です。内容が検索や理解に役立つよう、日本語で1〜2文の"
    "簡潔な説明（代替テキスト）を書いてください。前置き・引用符は不要、説明文のみ。"
)


def _figure_crop_png(page, block: Block) -> bytes | None:
    """ページ画像から図領域を切り出して PNG バイト列にする。"""
    if not page.image_path or not Path(page.image_path).exists():
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(page.image_path) as im:
        sx = im.width / page.width if page.width else 1.0
        sy = im.height / page.height if page.height else 1.0
        box = (
            max(0, int(block.bbox.x0 * sx)),
            max(0, int(block.bbox.y0 * sy)),
            min(im.width, int(block.bbox.x1 * sx)),
            min(im.height, int(block.bbox.y1 * sy)),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        buf = io.BytesIO()
        im.crop(box).convert("RGB").save(buf, format="PNG")
        return buf.getvalue()


def describe_figures(doc: Document, client: LLMClient, cfg: StructureLLMConfig) -> int:
    """挿絵ブロックに代替テキストを付与（block.text に格納）。戻り値: 付与数。"""
    if not hasattr(client, "describe_image"):
        return 0
    count = 0
    for page in doc.pages:
        for b in page.blocks:
            if b.type is not BlockType.FIGURE or b.text.strip():
                continue
            data = _figure_crop_png(page, b)
            if not data:
                continue
            try:
                alt = client.describe_image(data, _ALT_PROMPT)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                continue
            if alt:
                b.text = alt.strip()
                count += 1
    return count


def run_structure_llm(doc: Document, cfg: StructureLLMConfig) -> str | None:
    """構造化パスの入口。doc を(必要なら)その場で更新し、レビュー文字列を返す。

    - cfg.enabled が False、または API キーが無ければ何もしない（None）。
    - 各処理はフラグで個別に実行。レビューと異常検出は _structure_review.md 用に結合。
    """
    if not cfg.enabled:
        return None
    client = make_client(cfg)
    if client is None:  # APIキー無し → スキップ
        return None
    if cfg.assign_hierarchy:
        assign_hierarchy(doc, client, cfg)
    if cfg.rewrite_text:
        rewrite_body(doc, client, cfg)
    if cfg.figure_alt_text:
        describe_figures(doc, client, cfg)

    sections: list[str] = []
    if cfg.review:
        r = review_structure(doc, client, cfg)
        if r:
            sections.append("## 構成レビュー\n\n" + r)
    if cfg.anomaly_check:
        a = check_anomalies(doc, client, cfg)
        if a:
            sections.append("## 本文の異常チェック\n\n" + a)
    return "\n\n".join(sections) if sections else None
