"""HTTP 応答に載せるエラー（I7）。

想定利用者は非エンジニアなので、クライアントに返すのは **日本語の本文** と **安定した機械可読 `code`** の対だけ。
トレースバック・Python の例外名・英語のメッセージは応答に出さない。予期しない例外は 500 と一般的な文面にし、
相関 ID をローカルログと応答の両方に置く（利用者がログの行を指せるようにするため。本文は応答に出さない）。

アクセス層の例外（`quick_slide.errors`）はここで HTTP の状態と code に写像する。メッセージは既に日本語で、
秘匿値を含まない設計（errors.py のモジュール docstring）なので、そのまま本文に使う。

**ただし `ConflictError` の本文だけは差し替える。** 楽観ロックの不一致は「他のタブ/プロセスが先に更新した」
という実装の言い方（`lock_version` という列名を含む）でアクセス層に書かれている。列名や API の引数名は
利用者に見せる語ではないので、ここで `CONFLICT_MESSAGE` に置き換える。元の本文は例外として残るので
ログや開発時の追跡には使える。**なぜ断ったかはサーバが言い、何をしたか（並びを元に戻した等）は画面が言う**
という切り方にしているので、場面ごとの文をサーバ側に増やす必要はない。
"""

from __future__ import annotations

from typing import Any

from ..errors import ConflictError, NotFoundError, QuickSlideError, SelectionError, StartupError, StateError, ValidationError

GENERIC_500_MESSAGE = "サーバ内部で予期しない問題が起きました。しばらくしてからもう一度お試しください。"
# 楽観ロックの不一致（誰かが先に変えていた）。場面によらず 1 文にする
CONFLICT_MESSAGE = "ほかの場所で先に変更がありました。画面を読み込み直してからやり直してください。"
# 画面が送ってきた内容を受け付けられない（形が違う・古い）。利用者にできることだけを言う
RELOAD_AND_RETRY_MESSAGE = "送信された内容を受け付けられませんでした。画面を読み込み直してからやり直してください。"


class ApiError(Exception):
    """ルート処理が投げる、そのまま JSON になるエラー。"""

    def __init__(self, status: int, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def body(self, correlation_id: str | None = None) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            err["details"] = self.details
        if correlation_id is not None:
            err["correlation_id"] = correlation_id
        return {"error": err}


def from_store_error(exc: QuickSlideError) -> ApiError:
    """アクセス層の例外 → HTTP。順序は具体的なものから。"""
    message = str(exc) or "入力を受け付けられませんでした。"
    if isinstance(exc, SelectionError):
        return ApiError(exc.status, exc.code, message, details=exc.details)
    if isinstance(exc, NotFoundError):
        return ApiError(404, "not_found", message)
    if isinstance(exc, ValidationError):
        return ApiError(400, "validation_error", message)
    if isinstance(exc, ConflictError):
        return ApiError(409, "conflict", CONFLICT_MESSAGE)  # 元の本文は列名を含むので出さない（上の docstring）
    if isinstance(exc, StateError):
        return ApiError(409, "state_conflict", message)
    if isinstance(exc, StartupError):
        return ApiError(503, "not_ready", message)
    return ApiError(500, "internal_error", GENERIC_500_MESSAGE)


# よく使う 4xx
def bad_json() -> ApiError:
    return ApiError(400, "invalid_json", RELOAD_AND_RETRY_MESSAGE)


def not_found() -> ApiError:
    return ApiError(404, "not_found", "指定されたものは見つかりませんでした。")


def method_not_allowed() -> ApiError:
    return ApiError(405, "method_not_allowed", "この URL ではその操作はできません。")
