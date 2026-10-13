"""`POST /api/intakes` — 会話からの投入（payload の形は v1 と同じ。v2 は `deck_attributes` に mode・style・
image_look・images を足した）。デッキ（モードはここで決まり、あとから替えない）・取り込み・起動・描いた回・
仕事を作り、ワーカーを起こし、201 で `url=/slides/<デッキ>?launch=<id>&token=<t>` を返す。

- 秘密らしい形（秘密鍵・トークンの接頭辞など）を含む原稿は、書き込む前に 400 `secret_like_content` で断る
  （一致した文字列は返さない。パターンの名前だけ）
- 会話の識別子の生値が原稿やタイトルにも入っていたら 400（生値を永続化しない）
- まだ受けない項目（既存のデッキへの追加・会話抜粋・添付・書式）は 400 `not_ready_in_v2`
- その形式をこの機械でいま描けない（ピッチなのに Codex が無い・ログインしていない）なら 409 `engine_unavailable`
  （`QuickSlideServer.kind_refusals`。デッキは作らない）

**原稿か依頼か**（docs/adr/0002-create-from-screen.md）: `manuscript` と `request` は**ちょうどどちらか 1 つ**。
- `manuscript`（`origin.kind` は conversation など）: 呼ぶ側がもう材料を整えている。台本係へそのまま渡す（いままでどおり）
- `request`（`origin.kind=request`）: サーバが下調べ係で Web を調べて原稿を起こしてから、台本係に渡す
画面（入力欄）と会話（`skills/quick-slide` の `--request`）は同じ口・同じ形で送り、違いは `origin.runtime` だけ。
答えの `deck_url` はトークンの無い `/slides/<デッキ>`（画面はもう開いているので、こちらへ移る）。
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
    for field_name, label in (("manuscript", "原稿"), ("request", "依頼の文"), ("transcript_excerpt", "会話抜粋")):
        value = payload.get(field_name)
        if isinstance(value, str):
            hits = find_secret_like(value, extra)
            if hits:
                raise ApiError(400, "secret_like_content",
                               f"{label}に秘密情報らしい文字列が含まれています。除いて再投入してください。",
                               details={"field": field_name, "patterns": hits})
    _reject_echoed_conversation_key(payload)
    # その形式をこの機械で描けないと分かっているなら、デッキを作る前に断る（ピッチは Codex が要る。
    # 画面は札を押せなくしているが、会話の入口からも同じ口に来る）。入力の誤り（400）は機械の事情より先に言う
    with server.store_lock:
        assert server.store is not None
        try:
            mode = server.store.intake_mode(payload)
        except NotReadyError as exc:
            raise ApiError(400, NOT_READY_CODE, str(exc)) from None
    refusals = server.kind_refusals()
    refusal = refusals.get(mode)
    if refusal:
        raise ApiError(409, "engine_unavailable", refusal, details={"mode": mode})
    # この機械に絵を生成する Codex が無いなら、プレゼンは生成の画像の枠を置かずに作る（置くと描く段で止まる。
    # 確かめ方はピッチと同じ。docs/adr/0010-images-follow-the-machine.md）。図・形・アイコンと、渡された画像で組む
    if mode == "presentation" and "pitch" in refusals and isinstance(payload.get("deck_attributes"), dict):
        payload["deck_attributes"]["images"] = False
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
        "job_id": result["job_id"], "run_id": result["run_id"], "url": url, "deck_url": f"/slides/{result['deck_id']}",
        "resolution": result["resolution"], "appended": result["appended"],
        "resolution_reason": result["resolution_reason"],
    })
