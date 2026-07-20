"""LLM クライアントの共通インタフェースとファクトリ。

プロバイダ差し替えの要（かなめ）。`structure_llm.py` は具体プロバイダを知らず、
`LLMClient.generate(prompt)` だけに依存する。新プロバイダは `generate` を実装した
クラスを1つ足し、`make_client` に分岐を追加するだけでよい（GUI からは
`StructureLLMConfig.provider` で選べる）。
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from ..config import StructureLLMConfig


@runtime_checkable
class LLMClient(Protocol):
    """テキスト入力→テキスト出力の最小インタフェース。"""

    def generate(self, prompt: str, *, system: str | None = None) -> str: ...


def resolve_api_key(cfg: StructureLLMConfig) -> str:
    """環境変数優先、無ければ config の api_key（GUI 入力）を使う。"""
    return os.environ.get(cfg.api_key_env, "").strip() or (cfg.api_key or "").strip()


def make_client(cfg: StructureLLMConfig) -> LLMClient | None:
    """設定からクライアントを作る。APIキーが無ければ None（＝機能スキップ）。

    未知のプロバイダは ValueError。実 SDK の import は各実装の中で遅延させ、
    未インストール環境でもこのモジュールの import は失敗しないようにする。
    """
    key = resolve_api_key(cfg)
    if not key:
        return None
    provider = (cfg.provider or "gemini").lower()
    if provider == "gemini":
        from .gemini import GeminiClient

        return GeminiClient(model=cfg.model, api_key=key)
    # 将来ここに openai / anthropic などを追加する。
    raise ValueError(f"未対応の LLM プロバイダ: {cfg.provider}")
