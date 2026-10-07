"""`POST /api/intakes` — 会話からの投入（payload の形は v1 と同じ。v2 は `deck_attributes` に mode・style・
image_look・images を足した）。デッキ（モードはここで決まり、あとから替えない）・取り込み・起動・描いた回・
仕事を作り、ワーカーを起こし、201 で `url=/slides/<デッキ>?launch=<id>&token=<t>` を返す。

- 秘密らしい形（秘密鍵・トークンの接頭辞など）を含む原稿は、書き込む前に 400 `secret_like_content` で断る
  （一致した文字列は返さない。パターンの名前だけ）
- 会話の識別子の生値が原稿やタイトルにも入っていたら 400（生値を永続化しない）
- まだ受けない項目（既存のデッキへの追加・会話抜粋・添付・書式）は 400 `not_ready_in_v2`
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..security.secret_scan import find_secret_like
from ..store.intakes import NotReadyError
from .errors import ApiError
from .later import NOT_READY_CODE

if TYPE_CHECKING:
    from .app import QuickSlideServer, Request, Response


def _reject_echoed_conversation_key(payload: dict[str, Any]) -> None:
    origin = payload.get("origin")
    key = origin.get("conversation_key") if isinstance(origin, dict) else None
    if not isinstance(key, str) or len(key) < 8:
        return
    rest = {k: v for k, v in payload.items() if k != "origin"}
    rest["origin"] = {k: v for k, v in origin.items() if k != "conversation_key"}
    if key in json.dumps(rest, ensure_ascii=False):
        raise ApiError(400, "conversation_key_echoed",
                       "会話の識別子が原稿やタイトルなど他の項目にも含まれています。識別子は origin.conversation_key にだけ入れてください。")


def route_intake(server: "QuickSlideServer", req: "Request") -> "Response":
    from .app import Response

    payload = req.json_object()
    extra = server.settings.intake.extra_secret_patterns
    for field_name, label in (("manuscript", "原稿"), ("transcript_excerpt", "会話抜粋")):
        value = payload.get(field_name)
        if isinstance(value, str):
            hits = find_secret_like(value, extra)
            if hits:
                raise ApiError(400, "secret_like_content",
                               f"{label}に秘密情報らしい文字列が含まれています。除いて再投入してください。",
                               details={"field": field_name, "patterns": hits})
    _reject_echoed_conversation_key(payload)
    with server.store_lock:
        assert server.store is not None
        try:
            result = server.store.create_intake(payload)
        except NotReadyError as exc:
            raise ApiError(400, NOT_READY_CODE, str(exc)) from None
    server.wake_worker()
    token = result.pop("launch_token", None)
    url = server.launch_url(result["deck_id"], result["launch_id"], token)
    del token
    return Response.json(201, {
        "intake_id": result["intake_id"], "deck_id": result["deck_id"], "deck_title": result["deck_title"],
        "mode": result["mode"], "style": result["style"], "launch_id": result["launch_id"],
        "job_id": result["job_id"], "run_id": result["run_id"], "url": url,
        "resolution": result["resolution"], "appended": result["appended"],
        "resolution_reason": result["resolution_reason"],
    })
