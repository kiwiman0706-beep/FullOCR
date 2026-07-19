"""FullOCR: 自炊PDF → 高精度Markdown 変換パイプライン。

設計の中心は IR（中間表現, fullocr.ir）。
    NDL-Lite ─(テキスト+座標)─┐
                              ├─▶ IR ─(任意: GUI補正)─▶ Markdown + images
    Marker ──(種別/図/読み順)─┘
"""

__version__ = "0.1.0"
