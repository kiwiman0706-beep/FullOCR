"""Theta.FullOCR アプリアイコン生成。

コンセプト: 自炊PDF → OCR → 構造化ドキュメント。
  ティールのタイル / ドキュメント(折れ角=PDF) / 見出し+本文行(=構造化MD) / スキャンビーム(=OCR)。
出力: packaging/icon.png (1024), packaging/icon.ico (multi), packaging/icon.icns。
依存: Pillow のみ。再生成可能なようにスクリプトも同梱する。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
S = 1024

# ---- 配色（アプリのテーマに合わせる） ----
TEAL_TOP = (54, 104, 110)     # #36686e
TEAL_BOT = (33, 68, 72)       # #214448
PAPER = (252, 251, 247)       # 紙
FOLD = (223, 216, 205)        # 折れ角の陰
HEADING = (196, 106, 51)      # 見出しバー（アクセント）
BODY = (201, 196, 186)        # 本文行
BEAM = (120, 220, 200)        # スキャンビーム


def _grad(w: int, h: int, top, bot) -> Image.Image:
    g = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        g.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    return g.resize((w, h))


def _rounded_mask(size, radius) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=255)
    return m


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 背景タイル（角丸・ティールのグラデ）
    tile = _grad(S, S, TEAL_TOP, TEAL_BOT).convert("RGBA")
    img.paste(tile, (0, 0), _rounded_mask((S, S), int(S * 0.225)))

    d = ImageDraw.Draw(img)

    # ドキュメント本体（白・わずかに傾けて貼る）
    dw, dh = int(S * 0.50), int(S * 0.60)
    doc = Image.new("RGBA", (dw, dh), (0, 0, 0, 0))
    dd = ImageDraw.Draw(doc)
    fold = int(dw * 0.26)  # 右上の折れ角
    # ページ（折れ角を除いた多角形）
    dd.polygon([(0, 0), (dw - fold, 0), (dw, fold), (dw, dh), (0, dh)], fill=PAPER)
    # 折れ角の三角
    dd.polygon([(dw - fold, 0), (dw, fold), (dw - fold, fold)], fill=FOLD)
    # 角を少し丸める用マスク
    dmask = Image.new("L", (dw, dh), 0)
    ImageDraw.Draw(dmask).rounded_rectangle([0, 0, dw - 1, dh - 1], int(dw * 0.06), fill=255)
    doc.putalpha(Image.composite(doc.split()[3], Image.new("L", (dw, dh), 0), dmask))

    # ページ内の行（見出し＋本文＝構造化Markdown）
    pad = int(dw * 0.14)
    x0 = pad
    y = int(dh * 0.20)
    bar_h = int(dh * 0.055)
    # 見出しバー（アクセント色・太め短め）
    dd.rounded_rectangle([x0, y, x0 + int(dw * 0.46), y + int(bar_h * 1.5)],
                         radius=bar_h, fill=HEADING)
    y += int(bar_h * 3.4)
    # 本文行（濃淡なしのグレー・幅をばらす）
    for w_frac in (0.72, 0.72, 0.58, 0.72, 0.44):
        dd.rounded_rectangle([x0, y, x0 + int(dw * w_frac), y + bar_h],
                             radius=bar_h // 2, fill=BODY)
        y += int(bar_h * 2.1)

    doc = doc.rotate(-6, expand=True, resample=Image.BICUBIC)
    img.alpha_composite(doc, (int(S * 0.255), int(S * 0.205)))

    # スキャンビーム（半透明の横帯＋明るいエッジ＝OCR）
    beam = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(beam)
    by = int(S * 0.52)
    bh = int(S * 0.045)
    bx0, bx1 = int(S * 0.24), int(S * 0.78)
    bd.rounded_rectangle([bx0, by, bx1, by + bh], radius=bh // 2, fill=BEAM + (70,))
    bd.rounded_rectangle([bx0, by, bx1, by + int(bh * 0.28)], radius=bh // 4, fill=BEAM + (190,))
    img.alpha_composite(beam)

    return img


def main() -> None:
    master = build()
    png = OUT / "icon.png"
    master.save(png)
    print("wrote", png)

    # .ico（Windows exe / Inno）
    ico = OUT / "icon.ico"
    master.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", ico)

    # .icns（macOS .app）
    icns = OUT / "icon.icns"
    try:
        master.save(icns, format="ICNS")
        print("wrote", icns)
    except Exception as e:  # noqa: BLE001
        print("ICNS 書き出し失敗:", e)


if __name__ == "__main__":
    main()
