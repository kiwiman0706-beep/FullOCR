"""PyInstaller 用の GUI エントリポイント（凍結exeの起動スクリプト）。

PyInstaller は spec で指定したスクリプトを `__main__` として実行する。`__main__`
には親パッケージが無いため、そこで相対 import（`from .server import ...`）を使うと
`attempted relative import with no known parent package` で落ちる。

そこでエントリは「絶対 import で本体を呼ぶだけ」の薄いランチャーにしておく。
`fullocr.gui.app` はサブモジュールとして import されるので、その中の相対 import は
親パッケージ（`fullocr.gui`）が判明していて正常に解決される。
"""

from fullocr.gui.app import main

if __name__ == "__main__":
    main()
