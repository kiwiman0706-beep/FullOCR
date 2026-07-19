"""FullOCR のローカル GUI（pywebview + FastAPI）。

`fullocr gui` から起動する。中身は `fullocr.batch` の既存パイプラインをそのまま
呼ぶだけで、GUI はドラッグ&ドロップ・オプションのチェックボックス・進捗表示の
皮を被せているだけ（CLI と処理ロジックを共有する）。
"""
