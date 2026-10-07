"""状態・種別の語彙。

DDL の CHECK にある語彙はここと一致させる（`tests/test_db.py` が DDL 側の拒否も確かめる）。
DDL に CHECK が無い列（`deletion_tasks.state`）の語彙はアクセス層だけが持つので、ここが唯一の定義になる。
"""

from __future__ import annotations

from .logo import LOGO_POSITIONS

# --- DDL に CHECK がある語彙 ---
ASSET_STATES = ("ready", "quarantined", "trashed", "missing")
KINDS = ("preset", "user")
BRAND_ASSET_ROLES = ("logo_normal", "logo_white", "logo_transparent", "logo_symbol", "optional", "source")
LOGO_ROLES = ("logo_normal", "logo_white", "logo_transparent", "logo_symbol")
LOGO_VARIANTS = ("normal", "white", "transparent", "symbol", "none")
LOGO_CORNERS = LOGO_POSITIONS  # 正本は logo.py（エンジンの --logo-position と同じ 3 つ）
DECK_STATUSES = ("draft", "review", "final")
# デッキのモード。作るときに決め、あとから替えない（プレゼン＝html-slide・ピッチ＝image-slide）
DECK_MODES = ("presentation", "pitch")
LAUNCH_REQUESTED_VIA = ("start", "open", "ensure_external")
LAUNCH_STATES = ("pending", "attached", "expired", "cancelled")
ATTACHED_VIA = ("integrated", "external", "unknown")
ORIGIN_KINDS = ("conversation", "paste", "file", "headless")
ORIGIN_RUNTIMES = ("claude_code", "codex", "browser", "cli", "other")

# --- DDL に CHECK が無く、アクセス層だけが持つ語彙 ---
DELETION_STATES = ("pending", "running", "trashed", "restored", "purged", "failed")

# イベント種別（events.kind）。P2 以降で台本・描いた回の種別を足す。
EVENT_KINDS = (
    "brand_created",
    "brand_revision_added",
    "brand_deleted",
    "style_created",
    "style_revision_added",
    "style_deleted",
    "deck_created",
    "deck_updated",
    "deck_duplicated",
    "deck_trashed",    # ごみ箱への退避が終わった（専有ファイルを .trash/<deletion-id>/ へ）
    "deck_restored",   # ごみ箱から復元
    # ごみ箱から完全に削除した。**このデッキの行は 1 つも残らない**ので、
    # この記録の `deck_id` 列は NULL で、消した id は payload にだけ入る（`store/management.py`）
    "deck_purged",
    "launch_created",
    "launch_attached",
    "launch_attach_rejected",
    "launch_expired",
    "clipboard_copy",
)


def require(value: object, allowed: tuple[str, ...], field: str) -> str:
    """列挙の検証。メッセージに値そのものは含めない（秘匿値が混ざる可能性を排除する）。"""
    from .errors import ValidationError

    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(f"{field} は {'/'.join(allowed)} のいずれか")
    return value
