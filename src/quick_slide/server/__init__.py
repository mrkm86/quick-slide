"""常駐 HTTP サーバ。

標準ライブラリだけで書く（`http.server.ThreadingHTTPServer`）。アクセス層 `store/` の公開メソッドだけを使い、
SQL は書かない。画面（`web/app/`）が呼ぶ URL と、画面が読む項目は v1 の形を保つ。

入口:
- `python -m quick_slide.server serve`   前面で起動（`start` が裏で呼ぶ）
- `python -m quick_slide.server start`   デタッチ起動して health を待つ
- `python -m quick_slide.server stop`    `runtime/server.json` のインスタンスに停止を頼む
- `python -m quick_slide.server status`  生存の証拠つきの状態（server.json の存在だけで「稼働中」と言わない）
"""

from .app import QuickSlideServer
from .runtime import ServerRecord, read_record, status

__all__ = ["QuickSlideServer", "ServerRecord", "read_record", "status"]
