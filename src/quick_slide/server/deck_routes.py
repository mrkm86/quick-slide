"""デッキ API。ハンドラは `(server, req) -> Response` の関数で、`app.ROUTES` に並ぶ。

読み取り（GET。Host 検査のみ）:
- `GET /api/vocabulary`                      状態名の対応表（正本は `vocabulary.py`）
- `GET /api/decks/home`                      ピン枠 + 最近 10 件 + 一覧へ送る残り件数
- `GET /api/decks?q=&status=&mode=&archived=&pinned=&tag=&limit=&offset=`  一覧・検索（件数付き）
- `GET /api/decks/<id>`                      スライドの画面の材料
- `GET /api/decks/<id>/progress`             実況の取り直し用の状態
- `GET /api/trash`                           ごみ箱
- `GET /api/tags`

状態を変える（POST。Host + Origin + セッションの 3 重検査は `app._route` が自動で掛ける）:
- `POST /api/decks/<id>/title`    {title, lock_version?}
- `POST /api/decks/<id>/pin`      {pinned, lock_version?}
- `POST /api/decks/<id>/archive`  {archived, lock_version?}  画面からは使わない（口は残す）
- `POST /api/decks/<id>/duplicate` {title?}
- `POST /api/decks/<id>/delete`   ごみ箱へ（専有ファイルだけ退避）
- `POST /api/decks/<id>/restore`  ごみ箱から戻す
- `POST /api/decks/<id>/purge`    ごみ箱から完全に削除（戻せない）
- `POST /api/trash/purge`         ごみ箱を空にする

画面が表示する状態は**すべてここが DB から読んだもの**。画像は asset ID の URL だけを返し、パスは返さない。
まだ無い操作（描く・直す・書き出す・ページの操作）は `capabilities` に「使えない」と理由を載せ、画面はそれを見て
無効化する。**P1 のデッキはページを持たない**ので、スライドの画面の材料は「ページ 0 枚・動いていない」の形で返す
（画面が読む項目はすべてそろえる。P2 で描いた回から埋める）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from ..errors import NotFoundError
from .errors import RELOAD_AND_RETRY_MESSAGE, ApiError
from .later import NOT_READY_REASON
from .vocabulary import (
    GROUP_LABELS,
    PURGE_ALL_LABEL,
    PURGE_WORDS,
    deck_mode_label,
    deck_status_label,
    page_count_label,
    progress_headline,
    purge_all_confirm_label,
    purge_all_done_message,
    reclaimable_label,
    size_label,
    vocabulary,
)

if TYPE_CHECKING:  # 実行時は app.py の末尾で遅延 import される（循環を避ける）
    from .app import QuickSlideServer, Request, Response

# 修正指示と描くことは**スライドの画面でだけ**できる（一覧・ホーム・ごみ箱には送る先のページが無い）。
DECK_SCREEN_ONLY_INSTRUCTION_REASON = "修正指示はこの画面では送れません。スライドの画面で送ってください。"
DECK_SCREEN_ONLY_GENERATE_REASON = "画像の生成はこの画面ではできません。スライドの画面で始めてください。"
NO_PAGES_REASON = "まだページがありません。ページができるまでお待ちください。"
ADOPT_LABEL = "この版を使う"
# スライドの名前（タイトル）の長さ。ブランド・スタイルの名前と同じ 1〜200 文字に揃える
TITLE_MAX_LENGTH = 200
TITLE_REQUIRED_REASON = f"タイトルを1〜{TITLE_MAX_LENGTH}文字で入力してください。"
EXPORT_LABEL = "書き出し"
PURGE_LABEL = "完全に削除"
INSERT_PAGE_LABEL = "ページを追加"
REORDER_LABEL = "並べ替え"
RESUME_LABEL = "生成を再開"

# `label` は画面のボタン文言（画面側に書かず、ここから渡す）
CAPABILITIES: dict[str, Any] = {
    "generate": {"available": False, "label": "画像を生成", "reason": DECK_SCREEN_ONLY_GENERATE_REASON},
    "page_instruction": {"available": False, "label": "送信", "reason": DECK_SCREEN_ONLY_INSTRUCTION_REASON},
    "deck_instruction": {"available": False, "label": "送信", "reason": DECK_SCREEN_ONLY_INSTRUCTION_REASON},
    "regenerate": {"available": False, "label": "作り直す", "reason": DECK_SCREEN_ONLY_INSTRUCTION_REASON},
    "regenerate_failed": {"available": False, "label": f"{GROUP_LABELS['failed']}のページを全部作り直す",
                          "reason": NOT_READY_REASON},
    "insert_page": {"available": False, "label": INSERT_PAGE_LABEL, "reason": DECK_SCREEN_ONLY_INSTRUCTION_REASON},
    "pause": {"available": False, "label": "一時停止", "reason": NOT_READY_REASON},
    "resume": {"available": False, "label": "再開", "reason": NOT_READY_REASON},
    "adopt_version": {"available": False, "label": ADOPT_LABEL,
                      "reason": "版の切り替えはこの画面ではできません。スライドの画面で選んでください。"},
    "export": {"available": False, "label": EXPORT_LABEL, "reason": "書き出しはスライドの画面から行えます。"},
    "purge": {"available": False, "label": PURGE_LABEL, "reason": "完全に削除できるのはごみ箱の中のスライドだけです。"},
}


def _not_ready(label: str) -> dict[str, Any]:
    return {"available": False, "label": label, "reason": NOT_READY_REASON}


def deck_capabilities(row: dict[str, Any]) -> dict[str, Any]:
    """**スライドの画面に返る `capabilities` の正本。** 静的な表の上に、スライドの画面での答えを重ねる。

    P1 では描く・直す・版・書き出しは全部「まだ使えない」（理由は 1 文にそろえ、画面には 1 行だけ出る）。
    並べ替えと差し込みは「ページが無い」が先に効く（ページが無ければ v2 でも押せないので、そちらを言う）。
    """
    return {
        **CAPABILITIES,
        "generate": _not_ready("画像を生成"),
        "page_instruction": _not_ready("送信"),
        "deck_instruction": _not_ready("送信"),
        "regenerate": _not_ready("作り直す"),
        "adopt_version": _not_ready(ADOPT_LABEL),
        "export": _not_ready(EXPORT_LABEL),
        "resume": _not_ready(RESUME_LABEL),
        "reorder_pages": {"available": False, "label": REORDER_LABEL, "reason": NO_PAGES_REASON},
        # 使えないときは画面がボタンごと消す（隙間に「ここに足せます」を出さない）
        "insert_page": {"available": False, "label": INSERT_PAGE_LABEL, "reason": NO_PAGES_REASON},
    }


def purge_capability(store: Any, row: dict[str, Any]) -> dict[str, Any]:
    """完全に削除できるか（ごみ箱の 1 行ごとに変わるので静的な表には置かない）。

    P2 で「描いている途中のスライドは消せない」を足す（`store.purge_deck_from_trash` と同じ 1 文で断る）。
    """
    return {"available": True, "label": PURGE_LABEL, "reason": None}


def _response():
    from .app import Response  # 遅延 import（循環回避）

    return Response


def _json(status: int, obj: Any) -> "Response":
    return _response().json(status, obj)


def asset_url(asset_id: str | None) -> str | None:
    return None if asset_id is None else f"/api/assets/{asset_id}/file"


def _query(req: "Request") -> dict[str, str]:
    return {k: v[-1] for k, v in parse_qs(req.query, keep_blank_values=True).items()}


def _bool_param(raw: str | None, name: str) -> bool | None:
    if raw is None or raw == "":
        return None
    low = raw.lower()
    if low in ("1", "true", "yes"):
        return True
    if low in ("0", "false", "no"):
        return False
    if low == "all":
        return None
    raise ApiError(400, "validation_error", f"{name} は 1 / 0 / all のいずれかで指定してください。")


def _int_param(raw: str | None, name: str, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ApiError(400, "validation_error", f"{name} は整数で指定してください。") from None


def _lock_version(body: dict[str, Any]) -> int | None:
    v = body.get("lock_version")
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ApiError(400, "validation_error", RELOAD_AND_RETRY_MESSAGE)
    return v


def _optional_body(req: "Request") -> dict[str, Any]:
    if not req.body or req.body.strip() in (b"", b"{}"):
        return {}
    return req.json_object()


def _deck_not_found() -> ApiError:
    return ApiError(404, "deck_not_found", "そのスライドは見つかりません。削除済みか、URL が違う可能性があります。")


def _live_deck_row(store, deck_id: str) -> dict[str, Any]:
    """削除済みでないデッキの行（`Store.deck()`）。無ければ 404 `deck_not_found`。"""
    try:
        row = store.deck(deck_id)
    except NotFoundError:
        raise _deck_not_found() from None
    if row["deleted_at"] is not None:
        raise _deck_not_found()
    return row


def _card_of(store, deck_id: str) -> dict[str, Any]:
    return card_public(store.deck_card(store.deck(deck_id)))


def card_public(card: dict[str, Any]) -> dict[str, Any]:
    out = dict(card)
    out["status_label"] = deck_status_label(card["status"])
    out["mode_label"] = deck_mode_label(card["mode"])
    out["thumbnail_url"] = asset_url(card.get("thumbnail_asset_id"))
    # カードに出す枚数（「11枚」）。**数を入れるのはここだけ**（画面は数えない）
    out["page_count_label"] = page_count_label(card["page_count"])
    if card["page_count"] == 0:
        out["progress_label"] = "ページなし"
    else:
        out["progress_label"] = f"{GROUP_LABELS['ready']} {card['ready_count']}/{card['page_count']}"
    return out


def _counts(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """`pages` から件数を数える。非表示は数えない。"""
    displays = [p["display"] for p in pages if not p.get("hidden")]
    counts = {g: 0 for g in GROUP_LABELS}
    for d in displays:
        counts[d["group"]] = counts.get(d["group"], 0) + 1
    total = len(displays)
    return {"total": total, **counts, "labels": GROUP_LABELS,
            "headline": progress_headline(total, counts["ready"], counts["generating"])}


def limit_state(row: dict[str, Any]) -> dict[str, Any]:
    """止まっている帯（枠切れ・失敗）の答え。P1 は描かないので、いつも「止まっていない・再開できない」。

    形は v1 の画面が読むものと同じ（`stopped` / `reached` / `message` / `hint_at` / `resume`）。
    P2 でエンジンの rc=4 と枠切れの待ちをここへ写す。
    """
    return {
        "reached": False, "stopped": False, "paused": False, "cause": None, "since": None,
        "retry_not_before": None, "hint_at": None, "hint_source": None, "hint_source_label": None,
        "message": None, "remaining": 0,
        "resume": {"available": False, "label": RESUME_LABEL, "reason": NOT_READY_REASON},
    }


# 企画（台本を書く係）の帯。P1 では動いていない
PLANNING_IDLE: dict[str, Any] = {"state": None, "active": False, "message": None}
# 「まだ働いているか」の答え。画面はこの 1 つを読んで実況を続けるか決める
LIVE_IDLE: dict[str, Any] = {"working": False, "reason": "idle"}


# --- 読み取り ---
def route_vocabulary(server: "QuickSlideServer", req: "Request") -> "Response":
    return _json(200, vocabulary())


def route_home(server: "QuickSlideServer", req: "Request") -> "Response":
    with server.store_lock:
        assert server.store is not None
        data = server.store.home_decks()
    data["pinned"] = [card_public(c) for c in data["pinned"]]
    data["recent"] = [card_public(c) for c in data["recent"]]
    data["capabilities"] = CAPABILITIES
    return _json(200, data)


def route_list(server: "QuickSlideServer", req: "Request") -> "Response":
    q = _query(req)
    archived = _bool_param(q.get("archived"), "archived")
    if "archived" not in q:
        archived = False  # 既定はアーカイブ済みを含めない（`archived=all` で両方）
    pinned = _bool_param(q.get("pinned"), "pinned")
    status = q.get("status") or None
    mode = q.get("mode") or None
    limit = _int_param(q.get("limit"), "limit", 50)
    offset = _int_param(q.get("offset"), "offset", 0)
    with server.store_lock:
        assert server.store is not None
        data = server.store.search_decks(q=q.get("q") or None, status=status, mode=mode, archived=archived,
                                         pinned=pinned, brand_id=q.get("brand") or None, tag=q.get("tag") or None,
                                         limit=limit, offset=offset)
    data["items"] = [card_public(c) for c in data["items"]]
    data["capabilities"] = CAPABILITIES
    return _json(200, data)


def route_tags(server: "QuickSlideServer", req: "Request") -> "Response":
    with server.store_lock:
        assert server.store is not None
        return _json(200, {"items": server.store.all_tags()})


def route_trash(server: "QuickSlideServer", req: "Request") -> "Response":
    with server.store_lock:
        assert server.store is not None
        rows = server.store.trashed_decks()
        items = []
        for r in rows:
            card = card_public(server.store.deck_card(r))
            card["deletion_task_id"] = r["deletion_task_id"]
            card["deletion_state"] = r["deletion_state"]
            # 完全削除の可否は行ごとに違う
            cap = purge_capability(server.store, r)
            card["capabilities"] = {**CAPABILITIES, "purge": cap}
            # 空く容量も行ごとに出す。**判断点は `store.purge_estimate` の 1 つ**で、合計はここで足すだけ
            freed = server.store.purge_estimate(r["id"])["bytes"] if cap["available"] else 0
            card["reclaimable_bytes"] = freed
            card["reclaimable_size"] = size_label(freed)
            items.append(card)
        total = sum(c["reclaimable_bytes"] for c in items)
        ready = [c for c in items if c["capabilities"]["purge"]["available"]]
    return _json(200, {
        "items": items, "capabilities": {**CAPABILITIES, "purge_all": _purge_all_capability(len(ready))},
        "reclaimable_bytes": total, "reclaimable_label": reclaimable_label(total),
        "purge_all_confirm_label": purge_all_confirm_label(len(ready)),
    })


def _purge_all_capability(ready: int) -> dict[str, Any]:
    """ごみ箱を空にできるか。**いま完全に削除できる行が 1 つでもあるか**だけを見る。"""
    if ready == 0:
        return {"available": False, "label": PURGE_ALL_LABEL, "reason": "いま完全に削除できるスライドがありません。"}
    return {"available": True, "label": PURGE_ALL_LABEL, "reason": None}


def route_deck(server: "QuickSlideServer", req: "Request") -> "Response":
    deck_id = req.params["deck_id"]
    with server.store_lock:
        assert server.store is not None
        store = server.store
        row = _live_deck_row(store, deck_id)
        card = card_public(store.deck_card(row))
        seq = store.last_event_seq(deck_id)
    pages: list[dict[str, Any]] = []
    return _json(200, {
        "deck": card,
        "revision": None,      # 構成（台本の版＋採用した描いた回）。P2 で入る
        "pages": pages,
        "counts": _counts(pages),
        "batch": None,
        "limit": limit_state(row),
        "pending_inserts": [],
        "planning": dict(PLANNING_IDLE),
        "live": dict(LIVE_IDLE),
        "last_event_seq": seq,
        "capabilities": deck_capabilities(row),
    })


def route_progress(server: "QuickSlideServer", req: "Request") -> "Response":
    deck_id = req.params["deck_id"]
    with server.store_lock:
        assert server.store is not None
        store = server.store
        row = _live_deck_row(store, deck_id)
        seq = store.last_event_seq(deck_id)
        now = store.now()
    pages: list[dict[str, Any]] = []
    return _json(200, {
        "deck_id": deck_id,
        "candidates": [],
        "revision_id": None,
        "server_time": now,
        "last_event_seq": seq,
        "batch": None,
        "limit": limit_state(row),
        "planning": dict(PLANNING_IDLE),
        "live": dict(LIVE_IDLE),
        "pages": pages,
        "counts": _counts(pages),
        "pending_inserts": [],
        "capabilities": deck_capabilities(row),
    })


# --- 状態を変える ---
def route_title(server: "QuickSlideServer", req: "Request") -> "Response":
    """スライドの名前だけを変える。**受けるのはタイトル 1 項目だけ**（それ以外はそもそも読まない）。"""
    body = req.json_object()
    raw = body.get("title")
    title = raw.strip() if isinstance(raw, str) else ""
    if not title or len(title) > TITLE_MAX_LENGTH:
        raise ApiError(400, "title_required", TITLE_REQUIRED_REASON)
    deck_id = req.params["deck_id"]
    with server.store_lock:
        assert server.store is not None
        _live_deck_row(server.store, deck_id)
        server.store.update_deck_metadata(deck_id, expected_lock_version=_lock_version(body), title=title)
        card = _card_of(server.store, deck_id)
    return _json(200, {"deck": card})


def route_pin(server: "QuickSlideServer", req: "Request") -> "Response":
    body = req.json_object()
    pinned = body.get("pinned")
    if not isinstance(pinned, bool):
        raise ApiError(400, "validation_error", "pinned は真偽値で送ってください。")
    with server.store_lock:
        assert server.store is not None
        _live_deck_row(server.store, req.params["deck_id"])
        server.store.pin_deck(req.params["deck_id"], pinned, expected_lock_version=_lock_version(body))
        card = _card_of(server.store, req.params["deck_id"])
    return _json(200, {"deck": card})


def route_archive(server: "QuickSlideServer", req: "Request") -> "Response":
    body = req.json_object()
    archived = body.get("archived")
    if not isinstance(archived, bool):
        raise ApiError(400, "validation_error", "archived は真偽値で送ってください。")
    with server.store_lock:
        assert server.store is not None
        _live_deck_row(server.store, req.params["deck_id"])
        server.store.archive_deck(req.params["deck_id"], archived, expected_lock_version=_lock_version(body))
        card = _card_of(server.store, req.params["deck_id"])
    return _json(200, {"deck": card})


def route_duplicate(server: "QuickSlideServer", req: "Request") -> "Response":
    body = _optional_body(req)
    title = body.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise ApiError(400, "validation_error", "title は空でない文字列にしてください。")
    with server.store_lock:
        assert server.store is not None
        _live_deck_row(server.store, req.params["deck_id"])
        new = server.store.duplicate_deck(req.params["deck_id"], title=title)
        card = _card_of(server.store, new["id"])
    return _json(201, {"deck": card, "origin_deck_id": req.params["deck_id"]})


def route_delete(server: "QuickSlideServer", req: "Request") -> "Response":
    with server.store_lock:
        assert server.store is not None
        _live_deck_row(server.store, req.params["deck_id"])
        result = server.store.delete_deck_to_trash(req.params["deck_id"])
    return _json(200, {"deck_id": req.params["deck_id"], **result,
                       "message": "ごみ箱へ移動しました。ごみ箱から元に戻せます。"})


def route_restore(server: "QuickSlideServer", req: "Request") -> "Response":
    with server.store_lock:
        assert server.store is not None
        try:
            server.store.deck(req.params["deck_id"])  # 存在確認（削除済みでも可）
        except NotFoundError:
            raise _deck_not_found() from None
        server.store.restore_deck_from_trash(req.params["deck_id"])
        card = _card_of(server.store, req.params["deck_id"])
    return _json(200, {"deck": card, "message": "ごみ箱から戻しました。"})


def route_purge(server: "QuickSlideServer", req: "Request") -> "Response":
    """`POST /api/decks/<id>/purge`。**戻せない。** 画面は押す前に一度だけ押し直させる（`web/app/home.js`）。"""
    with server.store_lock:
        assert server.store is not None
        try:
            server.store.deck(req.params["deck_id"])  # 存在確認（削除済みでも可）
        except NotFoundError:
            raise _deck_not_found() from None
        result = server.store.purge_deck_from_trash(req.params["deck_id"])
    return _json(200, {**result, "message": PURGE_WORDS["done"]})


def route_trash_purge(server: "QuickSlideServer", req: "Request") -> "Response":
    """`POST /api/trash/purge`。ごみ箱を空にする。**戻せない。** 1 件ずつ独立に消す。"""
    with server.store_lock:
        assert server.store is not None
        result = server.store.empty_trash()
    return _json(200, {**result, "message": purge_all_done_message(
        result["purged"], result["bytes_freed"], result["skipped"])})
