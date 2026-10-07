"""「誰がこれを頼んだか」を読む 1 か所（画面 / 会話 / headless）。

会話から送られたもの（投入・修正指示）は `origin.kind='conversation'` と会話識別子を持つ。
**生の会話識別子を受け取るのはこのモジュールの戻り値までで**、呼び側は即 HMAC 化して捨てる。
生値は DB にもログにも残らない。P1 ではまだ呼ぶ口が無い（会話からの投入は P2）。
"""

from __future__ import annotations

from typing import Any

from ..enums import ORIGIN_RUNTIMES
from .errors import RELOAD_AND_RETRY_MESSAGE, ApiError

ORIGIN_FIELDS = frozenset({"kind", "runtime", "conversation_key"})
# 修正指示の出所の 3 値。`enums.ORIGIN_KINDS`（投入の出所）とは別物
INSTRUCTION_ORIGIN_KINDS = ("screen", "conversation", "headless")

SCREEN_ORIGIN: dict[str, Any] = {"kind": "screen", "runtime": None, "conversation_key": None}


def read_origin(body: dict[str, Any], *, key_required: bool = False) -> dict[str, Any]:
    """`origin` を読む。無ければ画面から。

    `key_required` は「会話を名指しできないと話が始まらない」口（会話に結びついたスライド探し）のため。
    """
    raw = body.get("origin")
    if raw is None:
        if key_required:
            raise ApiError(400, "validation_error", RELOAD_AND_RETRY_MESSAGE)
        return dict(SCREEN_ORIGIN)
    if not isinstance(raw, dict) or set(raw) - ORIGIN_FIELDS:
        raise ApiError(400, "validation_error", RELOAD_AND_RETRY_MESSAGE)
    kind = raw.get("kind")
    runtime = raw.get("runtime")
    key = raw.get("conversation_key")
    if kind not in INSTRUCTION_ORIGIN_KINDS:
        raise ApiError(400, "validation_error", RELOAD_AND_RETRY_MESSAGE)
    if runtime is not None and (not isinstance(runtime, str) or runtime not in ORIGIN_RUNTIMES):
        raise ApiError(400, "validation_error", RELOAD_AND_RETRY_MESSAGE)
    if key is not None:
        # 鍵だけ来てランタイムが無いと、**同じ会話が別の鍵に化ける**（HMAC はランタイムを混ぜる）
        if not isinstance(key, str) or runtime is None:
            raise ApiError(400, "validation_error", RELOAD_AND_RETRY_MESSAGE)
    elif key_required:
        raise ApiError(400, "validation_error", RELOAD_AND_RETRY_MESSAGE)
    return {"kind": kind, "runtime": runtime, "conversation_key": key}


def hashed(store: Any, origin: dict[str, Any]) -> str | None:
    """会話識別子を HMAC 化する（無ければ None）。**生値はこの 1 行から先へ行かない。**"""
    if origin["conversation_key"] is None:
        return None
    return store.conversation_hash_for(origin["runtime"], origin["conversation_key"])
