"""自宅ルーター（Yamaha RTX810）のクライアント一覧を収集・配信する。

- collector: RTX810 へ SSH で繋いで取得し、JSON へ上書き保存するワンショット処理
- api: その JSON を返す HTTP サーバー
"""

__version__ = "0.1.0"
