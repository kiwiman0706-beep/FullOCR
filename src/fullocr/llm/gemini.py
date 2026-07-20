"""Gemini (google-genai) 実装の LLMClient。

`google-genai` は任意依存（`.[llm]` extra）。この import はクラス内で遅延させ、
未インストールでもモジュール import 自体は通るようにする（他プロバイダ選択時や
テスト時にここを読み込むだけで落ちないため）。
"""

from __future__ import annotations


class GeminiClient:
    """Google Gemini。`generate(prompt)` で1ターン生成する。"""

    def __init__(self, model: str = "gemini-2.5-flash", api_key: str = "") -> None:
        self.model = model
        self._api_key = api_key
        self._client = None

    def _ensure(self):
        if self._client is None:
            try:
                from google import genai  # 遅延 import（任意依存）
            except ImportError as e:  # pragma: no cover - 環境依存
                raise RuntimeError(
                    "google-genai が見つかりません。'uv pip install -e \".[llm]\"' で導入してください。"
                ) from e
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        client = self._ensure()
        contents = prompt if system is None else f"{system}\n\n{prompt}"
        resp = client.models.generate_content(model=self.model, contents=contents)
        return (getattr(resp, "text", "") or "").strip()
