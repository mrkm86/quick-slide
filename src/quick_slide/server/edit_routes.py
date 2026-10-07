"""直す（P3）の口。どれも**仕事を 1 つ作ってワーカーを起こし、すぐ答える**（描き終えるのは後。画面の実況と
会話の `--wait` が待つ）。1 冊に走る仕事は 1 つだけで、続けて頼まれた直しは順に並ぶ。

状態を変える（POST。Host + Origin + セッションの 3 重検査は `app._route` が掛ける）:
- `POST /api/decks/<id>/instructions`  {scope: page|pages|deck, slide_id?, slide_ids?, text, edit_mode?, attachments?,
                                        confirm_pages?, source?, origin_runtime?}  ページを指定した直し
- `POST /api/decks/<id>/adopt`         {choices: {slide_id: version_id}}  版スタックの前の姿に戻す
- `POST /api/decks/<id>/pages/insert`  {after_slide_id, text, attachments?}  差し込み
- `POST /api/decks/<id>/pages/<slide_id>/duplicate`・`/visibility` {hidden}・`/pages/reorder` {slide_ids}
- `POST /api/decks/<id>/resume`        いまの台本で描き直す（止まった回の再開）
- `POST /api/conversations/deck`      {origin: {runtime, conversation_key}}  その会話の一番新しいスライド
読み取り:
- `GET /api/decks/<id>/jobs/<job_id>`  仕事の進み（会話の `--wait` が読む。ページの見出し・提案）

`lock_version` は受けるが照らさない（直しはページの id で指すので、前の直しの上に重なる。v1 の画面が送る形は保つ）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..enums import EDIT_SOURCES, ORIGIN_RUNTIMES
from ..errors import NotFoundError, ValidationError
from ..scriptdoc import one_page
from ..security.secret_scan import find_secret_like
from .deck_routes import NO_PAGES_REASON, _counts, _deck_not_found, _json, _live_deck_row, card_public, deck_view
from .errors import ApiError
from .vocabulary import (
    INSERT_TEXT_REQUIRED_REASON,
    MAX_ATTACHMENTS,
    MAX_INSTRUCTION_TEXT,
    TEXT_REQUIRED_REASON,
    TEXT_TOO_LONG_REASON,
    TOO_MANY_ATTACHMENTS_REASON,
)

if TYPE_CHECKING:
    from .app import QuickSlideServer, Request, Response

STRUCTURE_CHANGED = "このスライドの構成が、ほかの場所で変わりました。再読み込みしてから、もう一度操作してください。"
INFOGRAPHIC_REASON = "1 枚にまとめるスタイル（infographic）では、ページを足す・並べ替える・隠す・複製することはできません。"
QUEUED_TAIL = "前の直しが終わってから始めます。"


def _body(req: "Request") -> dict[str, Any]:
    if not req.body or req.body.strip() in (b"", b"{}"):
        return {}
    return req.json_object()


def _source(body: dict[str, Any]) -> tuple[str, str | None]:
    source = body.get("source") or "screen"
    if source not in EDIT_SOURCES:
        raise ApiError(400, "validation_error", "source は screen か conversation です。")
    runtime = body.get("origin_runtime")
    if runtime is not None and runtime not in ORIGIN_RUNTIMES:
        raise ApiError(400, "validation_error", "origin_runtime が分かりません。")
    return source, runtime


def _text(server: "QuickSlideServer", body: dict[str, Any], *, required: str) -> str:
    text = body.get("text") or ""
    if not isinstance(text, str):
        raise ApiError(400, "validation_error", "text は文字列で送ってください。")
    if len(text) > MAX_INSTRUCTION_TEXT:
        raise ApiError(400, "text_too_long", TEXT_TOO_LONG_REASON)
    hits = find_secret_like(text, server.settings.intake.extra_secret_patterns)
    if hits:
        raise ApiError(400, "secret_like_content", "指示に秘密情報らしい文字列が含まれています。除いてから送ってください。",
                       details={"field": "text", "patterns": hits})
    return text.strip()


def _attachments(store: Any, body: dict[str, Any]) -> list[str]:
    raw = body.get("attachments") or []
    if not isinstance(raw, list):
        raise ApiError(400, "validation_error", "attachments は配列で送ってください。")
    if len(raw) > MAX_ATTACHMENTS:
        raise ApiError(400, "too_many_attachments", TOO_MANY_ATTACHMENTS_REASON)
    out = []
    for a in raw:
        aid = a.get("asset_id") if isinstance(a, dict) else a
        try:
            asset = store.asset(aid) if isinstance(aid, str) else None
        except NotFoundError:
            asset = None
        if asset is None or asset["state"] != "ready" or not str(asset["media_type"]).startswith("image/"):
            raise ApiError(400, "attachment_not_found", "添えた画像が見つかりません。添え直してください。")
        out.append(aid)
    return out


def _head_pages(store: Any, deck_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """いまのページ（画面の並び）とデッキの描き方。ページが無ければ 409。"""
    view = deck_view(store, deck_id)
    if not view["pages"]:
        raise ApiError(409, "no_pages", NO_PAGES_REASON)
    return view["pages"], store.deck_run_args(deck_id)


def _page(pages: list[dict[str, Any]], slide_id: Any) -> dict[str, Any]:
    page = next((p for p in pages if p["slide_id"] == slide_id), None)
    if page is None:
        raise ApiError(409, "page_not_found", STRUCTURE_CHANGED)
    return page


def _no_structure(args: dict[str, Any]) -> None:
    if one_page(args):
        raise ApiError(409, "not_for_infographic", INFOGRAPHIC_REASON)


def _accepted(server: "QuickSlideServer", deck_id: str, result: dict[str, Any], message: str, **extra: Any) -> "Response":
    server.wake_worker()
    with server.store_lock:
        card = card_public(server.store.deck_card(server.store.deck(deck_id)))
    if result["ahead"]:
        message = f"{message}{QUEUED_TAIL}"
    return _json(200, {"instruction_id": result["job"]["id"], "job_id": result["job"]["id"], "run_id": result["run"]["id"],
                       "revision_id": result["run"]["id"], "started": True, "queued": bool(result["ahead"]),
                       "message": message, "deck": card, **extra})


def _numbers(positions: list[int]) -> str:
    return "・".join(str(n) for n in positions)


def route_instructions(server: "QuickSlideServer", req: "Request") -> "Response":
    """ページを指定した直し（1 ページ・選んだ数ページ・スライド全体）。台本係が振り分ける。"""
    deck_id = req.params["deck_id"]
    body = req.json_object()
    scope = body.get("scope") or "page"
    if scope not in ("page", "pages", "deck"):
        raise ApiError(400, "validation_error", "scope は page・pages・deck のどれかです。")
    edit_mode = body.get("edit_mode") or "minor"
    if edit_mode not in ("minor", "remake"):
        raise ApiError(400, "validation_error", "edit_mode は minor か remake です。")
    source, runtime = _source(body)
    text = _text(server, body, required=TEXT_REQUIRED_REASON)
    with server.store_lock:
        store = server.store
        _live_deck_row(store, deck_id)
        atts = _attachments(store, body)
        if not text and not atts:
            raise ApiError(400, "text_required", TEXT_REQUIRED_REASON)
        pages, _ = _head_pages(store, deck_id)
        if scope == "page":
            chosen = [_page(pages, body.get("slide_id"))]
        elif scope == "pages":
            ids = body.get("slide_ids")
            if not isinstance(ids, list) or not ids:
                raise ApiError(400, "validation_error", "直すページを 1 つ以上選んでください。")
            chosen = []
            for sid in ids:
                p = _page(pages, sid)
                if p not in chosen:
                    chosen.append(p)
            chosen.sort(key=lambda p: p["position"])
        else:
            chosen = list(pages)
            confirm = body.get("confirm_pages")
            if confirm is not None and confirm != len(pages):
                raise ApiError(409, "page_count_changed", STRUCTURE_CHANGED)
        positions = [p["position"] for p in chosen]
        result = store.create_edit(deck_id, {
            "op": "revise", "scope": scope, "pages": [p["slide_id"] for p in chosen], "positions": positions,
            "text": text, "edit_mode": edit_mode, "attachments": atts, "source": source, "origin_runtime": runtime,
        })
    what = "スライド全体" if scope == "deck" else f"{_numbers(positions)}ページ"
    return _accepted(server, deck_id, result, f"{what}の直しを受け付けました。直し終えると画面の絵が替わります。")


def route_adopt(server: "QuickSlideServer", req: "Request") -> "Response":
    """版スタックの前の姿を押した: 「そのページをその姿に戻す」直し（戻すのも前に進む 1 回）。"""
    deck_id = req.params["deck_id"]
    body = req.json_object()
    choices = body.get("choices")
    if not isinstance(choices, dict) or not choices:
        raise ApiError(400, "validation_error", "choices（ページ → 版）を送ってください。")
    with server.store_lock:
        store = server.store
        _live_deck_row(store, deck_id)
        pages, args = _head_pages(store, deck_id)
        if one_page(args):
            raise ApiError(409, "not_for_infographic", INFOGRAPHIC_REASON)
        made = []
        same = 0
        for sid, vid in choices.items():
            page = _page(pages, sid)
            version = next((v for v in page["versions"] if v["id"] == vid), None)
            if version is None:
                raise ApiError(409, "version_not_found", "その版は見つかりません。画面を読み込み直してください。")
            if version["active"] and page["display"]["key"] == "ready":
                same += 1
                continue
            made.append(store.create_edit(deck_id, {"op": "restore", "page": sid, "run": vid, "source": "screen",
                                                    "positions": [page["position"]]}))
        view = deck_view(store, deck_id)
        card = card_public(store.deck_card(view["row"]))
    if not made:
        return _json(200, {"deck": card, "revision_id": view["revision"]["id"] if view["revision"] else None,
                           "pages": view["pages"], "counts": _counts(view["pages"]), "started": False,
                           "message": "そのページはもうこの版です。"})
    server.wake_worker()
    message = "前の版に戻しています。戻し終えると画面の絵が替わります。"
    if made[0]["ahead"]:
        message += QUEUED_TAIL
    return _json(200, {"deck": card, "revision_id": made[-1]["run"]["id"], "pages": view["pages"],
                       "counts": _counts(view["pages"]), "started": True, "job_ids": [m["job"]["id"] for m in made],
                       "job_id": made[-1]["job"]["id"], "message": message})


def route_insert(server: "QuickSlideServer", req: "Request") -> "Response":
    deck_id = req.params["deck_id"]
    body = req.json_object()
    text = _text(server, body, required=INSERT_TEXT_REQUIRED_REASON)
    if not text:
        raise ApiError(400, "text_required", INSERT_TEXT_REQUIRED_REASON)
    source, runtime = _source(body)
    with server.store_lock:
        store = server.store
        _live_deck_row(store, deck_id)
        atts = _attachments(store, body)
        pages, args = _head_pages(store, deck_id)
        _no_structure(args)
        after = body.get("after_slide_id")
        if after is not None:
            _page(pages, after)
        result = store.create_edit(deck_id, {"op": "insert", "after": after, "text": text, "attachments": atts,
                                             "source": source, "origin_runtime": runtime})
    return _accepted(server, deck_id, result, "ページを足しています。できると画面に出ます。", task_id=result["job"]["id"])


def route_duplicate(server: "QuickSlideServer", req: "Request") -> "Response":
    deck_id, slide_id = req.params["deck_id"], req.params["slide_id"]
    _body(req)
    with server.store_lock:
        store = server.store
        _live_deck_row(store, deck_id)
        pages, args = _head_pages(store, deck_id)
        _no_structure(args)
        page = _page(pages, slide_id)
        result = store.create_edit(deck_id, {"op": "duplicate", "page": slide_id, "source": "screen",
                                             "positions": [page["position"]]})
    return _accepted(server, deck_id, result, f"{page['position']}ページを複製しています。")


def route_visibility(server: "QuickSlideServer", req: "Request") -> "Response":
    deck_id, slide_id = req.params["deck_id"], req.params["slide_id"]
    body = req.json_object()
    hidden = body.get("hidden")
    if not isinstance(hidden, bool):
        raise ApiError(400, "validation_error", "hidden は真偽値で送ってください。")
    with server.store_lock:
        store = server.store
        _live_deck_row(store, deck_id)
        pages, args = _head_pages(store, deck_id)
        _no_structure(args)
        page = _page(pages, slide_id)
        if hidden and not page["hidden"] and sum(1 for p in pages if not p["hidden"]) <= 1:
            raise ApiError(409, "last_visible_page", "最後の 1 ページは非表示にできません。")
        result = store.create_edit(deck_id, {"op": "visibility", "page": slide_id, "hidden": hidden, "source": "screen",
                                             "positions": [page["position"]]})
    word = "非表示にしています。" if hidden else "表示に戻しています。"
    return _accepted(server, deck_id, result, f"{page['position']}ページを{word}")


def route_reorder(server: "QuickSlideServer", req: "Request") -> "Response":
    """並べ替え。送るのは全順序（v1 と同じ）。答えのページは**新しい並び**（描き終えるまでは前の絵のまま）。"""
    deck_id = req.params["deck_id"]
    body = req.json_object()
    ids = body.get("slide_ids")
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        raise ApiError(400, "validation_error", "slide_ids（全ページの並び）を送ってください。")
    with server.store_lock:
        store = server.store
        _live_deck_row(store, deck_id)
        pages, args = _head_pages(store, deck_id)
        _no_structure(args)
        if sorted(ids) != sorted(p["slide_id"] for p in pages) or len(set(ids)) != len(ids):
            raise ApiError(409, "structure_changed", STRUCTURE_CHANGED)
        result = store.create_edit(deck_id, {"op": "reorder", "order": ids, "source": "screen"})
        by_id = {p["slide_id"]: p for p in pages}
        ordered = [{**by_id[sid], "position": i} for i, sid in enumerate(ids, 1)]
        card = card_public(store.deck_card(store.deck(deck_id)))
    server.wake_worker()
    return _json(200, {"deck": card, "revision_id": result["run"]["id"], "pages": ordered, "counts": _counts(ordered),
                       "job_id": result["job"]["id"], "started": True, "message": "並びを保存しました。"})


def route_resume(server: "QuickSlideServer", req: "Request") -> "Response":
    deck_id = req.params["deck_id"]
    with server.store_lock:
        store = server.store
        _live_deck_row(store, deck_id)
        view = deck_view(store, deck_id)
        cap = view["capabilities"]["resume"]
        if not cap["available"]:
            raise ApiError(409, "nothing_to_resume", cap["reason"] or "描き直すものはありません。")
        result = store.create_edit(deck_id, {"op": "render", "source": "screen"})
    return _accepted(server, deck_id, result, "いまの台本で描き直しています。")


def route_conversation_deck(server: "QuickSlideServer", req: "Request") -> "Response":
    """その会話から作った一番新しいスライド（会話からの直しの既定の行き先）。識別子は URL に載せない（POST）。"""
    body = req.json_object()
    origin = body.get("origin")
    if not isinstance(origin, dict):
        raise ApiError(400, "validation_error", "origin を送ってください。")
    runtime, key = origin.get("runtime"), origin.get("conversation_key")
    if runtime not in ORIGIN_RUNTIMES or not isinstance(key, str) or len(key) < server.settings.intake.min_conversation_key_chars:
        raise ApiError(400, "validation_error", "会話の識別子が読めません。スライドを指定してください。")
    with server.store_lock:
        try:
            deck = server.store.conversation_deck(runtime, key)
        except ValidationError:
            deck = None
        del key
        if deck is None:
            raise ApiError(404, "deck_not_found", "この会話から作ったスライドが見つかりません。スライドを指定してください。")
        card = card_public(server.store.deck_card(deck))
    return _json(200, {"deck": card, "url": f"{server.url}/slides/{deck['id']}"})


def route_job(server: "QuickSlideServer", req: "Request") -> "Response":
    """仕事の進み。終わったら、そのときのページの見出しと、台本係の提案を返す（会話の `--wait` が読む）。"""
    deck_id, job_id = req.params["deck_id"], req.params["job_id"]
    with server.store_lock:
        store = server.store
        try:
            job = store.job_for_deck(deck_id, job_id)
            run = store.run(job["run_id"])
        except NotFoundError:
            raise ApiError(404, "job_not_found", "その仕事は見つかりません。") from None
        try:
            view = deck_view(store, deck_id)
        except ApiError:
            raise _deck_not_found() from None
        edit = (run.get("record") or {}).get("edit") or {}
        pages = [{"no": p["position"], "title": p["title"], "hidden": p["hidden"], "state": p["display"]["label"]}
                 for p in view["pages"]]
    finished = job["state"] in ("done", "failed")
    return _json(200, {
        "job": {"id": job["id"], "kind": job["kind"], "op": (job.get("request") or {}).get("op") or job["kind"],
                "state": job["state"]},
        "run": {"id": run["id"], "state": run["state"], "message": run.get("message"),
                "failure_kind": run.get("failure_kind")},
        "finished": finished, "ok": run["state"] == "done",
        "summary": edit.get("summary") or None, "suggestions": edit.get("suggestions") or [],
        "routes": edit.get("routes") or {}, "pages": pages if finished else [],
        "planning": view["planning"],
    })
