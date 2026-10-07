"""アクセス層の例外。

上位（サーバ・CLI）は sqlite3 の例外ではなくこれらを受ける。CLI の終了コード体系に
写像できるよう `exit_code` を持たせる（2=入力/設定不正、3=起動/権限、4=処理失敗、5=中断）。
例外メッセージには秘匿値（会話キー・原文抜粋・トークン平文）を絶対に含めない。
"""

from __future__ import annotations


class QuickSlideError(Exception):
    """全ての基底。"""

    exit_code = 4


class ValidationError(QuickSlideError):
    """入力・設定の不正。終了コード 2。"""

    exit_code = 2


class NotFoundError(QuickSlideError):
    """対象が存在しない（削除済みも含む）。"""

    exit_code = 2


class StateError(QuickSlideError):
    """現在の状態では許されない操作（不変対象の変更、逆行する遷移など）。"""

    exit_code = 4


class ConflictError(QuickSlideError):
    """楽観ロック（lock_version）や lease 世代の不一致。採用してはいけない古い結果。"""

    exit_code = 4


class StartupError(QuickSlideError):
    """起動できない（鍵が読めない、スキーマが将来版、マイグレーションが改変された等）。終了コード 3。"""

    exit_code = 3
