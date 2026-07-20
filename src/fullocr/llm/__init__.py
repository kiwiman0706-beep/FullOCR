"""LLM クライアント抽象化。

構造化パス(pipeline/structure_llm.py)から使う。プロバイダ差し替え可能な
`LLMClient` プロトコルと、設定から実体を作る `make_client()` を提供する。
"""

from .base import LLMClient, make_client

__all__ = ["LLMClient", "make_client"]
