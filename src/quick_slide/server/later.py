"""まだ使えない口。

画面（v1 から写したもの）が呼ぶ URL は残し、**黙って成功したふりをしない**。答えは v1 と同じエラーの形
（`{"error": {"code", "message"}}`）で、日本語の本文と安定した `code` を返す。画面は `capabilities` を見て
ボタンを押せなくしてあるので、ここへ来るのは会話や CLI から直接叩いたときか、画面の不具合のときだけ。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import ApiError

if TYPE_CHECKING:  # 実行時は app.py の末尾で遅延 import される（循環を避ける）
    from .app import QuickSlideServer, Request, Response

NOT_READY_CODE = "not_ready"
NOT_READY_STATUS = 503
# 画面にも出る 1 文（`capabilities[*].reason` と同じ）。押せない理由として読まれる
NOT_READY_REASON = "この版ではまだ使えません"
NOT_READY_MESSAGE = NOT_READY_REASON + "。"


def not_ready(server: "QuickSlideServer", req: "Request") -> "Response":
    raise ApiError(NOT_READY_STATUS, NOT_READY_CODE, NOT_READY_MESSAGE)
