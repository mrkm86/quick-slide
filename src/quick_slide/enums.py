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
# 台本の版を誰が書いたか・どの LLM で書いたか
SCRIPT_WRITTEN_BY = ("writer", "screen", "conversation")
WRITER_BACKENDS = ("claude", "codex")
# 描いた回の状態（書く → 確かめる → 描く → 終わり）。ページごとの結果
RUN_STATES = ("queued", "writing", "checking", "rendering", "importing", "done", "failed")
RUN_ACTIVE_STATES = ("queued", "writing", "checking", "rendering", "importing")
RUN_PAGE_STATES = ("pending", "drawn", "ready", "failed")
# ワーカーの仕事。make は「原稿から 1 冊作る」（書く → 確かめる → 描く）、edit は P3 の「直す」
# （何をするかは jobs.request_json の op。EDIT_OPS）
JOB_KINDS = ("make", "edit")
# 直しの種類: ページを指定した直し・差し込み・複製・非表示/表示・並べ替え・版から戻す・描き直し（再開）
EDIT_OPS = ("revise", "insert", "duplicate", "visibility", "reorder", "restore", "render")
# 直しを頼んだ口
EDIT_SOURCES = ("screen", "conversation")
JOB_STATES = ("queued", "running", "done", "failed")

# --- DDL に CHECK が無く、アクセス層だけが持つ語彙 ---
DELETION_STATES = ("pending", "running", "trashed", "restored", "purged", "failed")

# イベント種別（events.kind）。
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
    # P2: 会話から 1 冊（取り込み → 台本係 → 検査 → 描き係）
    "intake_created",
    "job_started",
    "job_finished",
    "script_saved",
    "run_updated",     # 状態・進みが変わった（画面の実況が last_event_seq で気づく）
    "run_finished",
    "job_requeued",    # サーバが途中で止まった仕事を、安全な段から続ける
    # P3: 直す
    "edit_requested",  # 直しを受け付けた（仕事を作った）
    "script_accepted", # 台本の版をデッキのいまの台本にした
)


def require(value: object, allowed: tuple[str, ...], field: str) -> str:
    """列挙の検証。メッセージに値そのものは含めない（秘匿値が混ざる可能性を排除する）。"""
    from .errors import ValidationError

    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(f"{field} は {'/'.join(allowed)} のいずれか")
    return value
