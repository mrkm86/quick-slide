"""直す（P3）の口。どれも**仕事を 1 つ作ってワーカーを起こし、すぐ答える**（描き終えるのは後。画面の実況と
会話の `--wait` が待つ）。1 冊に走る仕事は 1 つだけで、続けて頼まれた直しは順に並ぶ。

状態を変える（POST。Host + Origin + セッションの 3 重検査は `app._route` が掛ける）:
- `POST /api/decks/<id>/instructions`  {scope: page|pages|deck, slide_id?, slide_ids?, text, edit_mode?, attachments?,
                                        kind?, options?, confirm_pages?, source?, origin_runtime?}  ページを指定した直し
                                        （kind・options は「何を直しますか？」。docs/adr/0008-edit-kinds.md）
- `POST /api/decks/<id>/adopt`         {choices: {slide_id: version_id}}  版スタックの前の姿に切り替える（描き直さない）
- `POST /api/decks/<id>/pages/insert`  {after_slide_id, text, attachments?}  差し込み
- `POST /api/decks/<id>/pages/<slide_id>/duplicate`・`/visibility` {hidden}・`/pages/reorder` {slide_ids}
- `POST /api/decks/<id>/resume`        いまの台本で描き直す（止まった回の再開）
- `POST /api/decks/<id>/rebuild`       {brand_id?, style_id?}  設定を替えて組み直す（P5。台本係は呼ばない）
- `POST /api/conversations/deck`      {origin: {runtime, conversation_key}}  その会話の一番新しいスライド
読み取り:
- `GET /api/decks/<id>/jobs/<job_id>`  仕事の進み（会話の `--wait` が読む。ページの見出し・提案）
- `GET /api/decks/<id>/look`           いまのブランド・スタイルと選べるもの（「設定を替えて組み直す」の材料）

`lock_version` は受けるが照らさない（直しはページの id で指すので、前の直しの上に重なる。v1 の画面が送る形は保つ）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import edit_kinds
from ..engines import engine_for
from ..enums import EDIT_SOURCES, ORIGIN_RUNTIMES
from ..errors import NotFoundError, ValidationError
from ..ids import PRESET_STYLE_STANDARD_ID, PRESET_STYLE_VISUAL_ID
from ..pins import PinRefused, pin_source
from ..scriptdoc import IMPORTED_TYPE, one_page, screen_pages, with_hidden, with_order, with_restored
from ..security.secret_scan import find_secret_like
from ..store.edits import TEMPLATE_REBUILD_REASON, TEMPLATE_RESTORE_REASON
from .deck_routes import NO_PAGES_REASON, _counts, _tidy, _deck_not_found, _json, _live_deck_row, card_public, deck_view
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
NO_IMAGES_REASON = "モーションの場面には画像を添えられません（図は場面の中で描きます）。指示の文だけで送ってください。"


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


def _attachments(store: Any, body: dict[str, Any], row: dict[str, Any] | None = None) -> list[str]:
    raw = body.get("attachments") or []
    if not isinstance(raw, list):
        raise ApiError(400, "validation_error", "attachments は配列で送ってください。")
    if raw and row is not None and not engine_for(row["mode"]).has_images:
        raise ApiError(400, "attachments_not_supported", NO_IMAGES_REASON)
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


IMPORTED_PAGE_REASON = ("PPTX から取り込んだページで直せるのは、文字・挿絵・画像・図解です（1 ページずつ）。"
                        "見る・発表する・書き出す・並べ替え・非表示・複製・ページを足すこともできます。")


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


def can_generate_images(server: "QuickSlideServer") -> bool:
    """この機械で絵を生成できるか（Codex がある）。確かめ方はピッチを作り始められるかと同じ（docs/adr/0005）。"""
    return "pitch" not in server.kind_refusals()


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
    can_generate = can_generate_images(server)   # エンジンに聞く（少し掛かる）ので、鍵の外で
    with server.store_lock:
        store = server.store
        row = _live_deck_row(store, deck_id)
        atts = _attachments(store, body, row)
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
        # 取り込んだページ（PPTX から）は、選べる直し方が違う（文字・挿絵・画像・図解を 1 ページずつ。
        # docs/adr/0013・0014・0015）。種類と選び方の確かめは、どのページを直すか決まってから
        imported = any(p.get("type") == IMPORTED_TYPE for p in chosen)
        if imported and scope != "page":
            raise ApiError(409, "imported_page", IMPORTED_PAGE_REASON)
        try:
            kind, options = edit_kinds.parse(row["mode"], body.get("kind"), body.get("options"), can_generate=can_generate,
                                             imported=imported)
        except ValueError as exc:
            raise ApiError(409 if imported else 400, "imported_page" if imported else "edit_kind_invalid",
                           IMPORTED_PAGE_REASON if imported else str(exc)) from None
        if kind is None and not text and not atts:
            raise ApiError(400, "text_required", TEXT_REQUIRED_REASON)
        lack = edit_kinds.missing(kind, options, text=text, attachments=len(atts), imported=imported,
                                  can_generate=can_generate)
        if lack:
            raise ApiError(400, "text_required" if kind == "text" else "attachment_required", lack)
        positions = [p["position"] for p in chosen]
        result = store.create_edit(deck_id, {
            "op": "revise", "scope": scope, "pages": [p["slide_id"] for p in chosen], "positions": positions,
            "text": text, "edit_mode": edit_mode, "attachments": atts, "source": source, "origin_runtime": runtime,
            "kind": kind, "options": options, "can_generate": can_generate,
        })
    what = "スライド全体" if scope == "deck" else f"{_numbers(positions)}ページ"
    return _accepted(server, deck_id, result, f"{what}の直しを受け付けました。直し終えると画面の絵が替わります。")


def route_adopt(server: "QuickSlideServer", req: "Request") -> "Response":
    """版スタックの前の姿を押した: そのページをその版に**切り替える**（描き直さない。docs/adr/0001-version-pins.md）。

    台本のそのページを前の版の中身に戻し、版の印（`_pin`）にその版を描いた回を書く。直しの仕事が無ければその場で当て
    （答えのページはもう切り替えた姿）、発表用の HTML と書き出しは裏の組み直し（tidy）が、印の指す回の部品で揃える。
    直しの仕事が待っている・走っているときは、これまでどおり仕事にする（ワーカーの restore が同じ印を書く）。"""
    deck_id = req.params["deck_id"]
    body = req.json_object()
    choices = body.get("choices")
    if not isinstance(choices, dict) or not choices:
        raise ApiError(400, "validation_error", "choices（ページ → 版）を送ってください。")
    applied = None
    made = []
    with server.store_lock:
        store = server.store
        _live_deck_row(store, deck_id)
        if store.deck_template_snapshot_ref(deck_id):
            raise ApiError(409, "template_restore_unsupported", TEMPLATE_RESTORE_REASON)
        pages, args = _head_pages(store, deck_id)
        if one_page(args):
            raise ApiError(409, "not_for_infographic", INFOGRAPHIC_REASON)
        picks = []
        for sid, vid in choices.items():
            page = _page(pages, sid)
            version = next((v for v in page["versions"] if v["id"] == vid), None)
            if version is None:
                raise ApiError(409, "version_not_found", "その版は見つかりません。画面を読み込み直してください。")
            if version["active"] and page["display"]["key"] == "ready":
                continue
            picks.append((page, vid))

        def change(data: dict[str, Any]) -> dict[str, Any]:
            for page, vid in picks:
                try:
                    old = pin_source(store, deck_id, vid, page["key"])
                except PinRefused as exc:
                    raise ApiError(409, "version_not_adoptable", exc.message) from None
                data = with_restored(data, page["key"], old, vid)
            return data

        if picks:
            applied = _apply_now(server, deck_id, "restore", change,
                                 {"pages": [p["slide_id"] for p, _ in picks], "runs": [v for _, v in picks]})
            if applied is None:
                made = [store.create_edit(deck_id, {"op": "restore", "page": p["slide_id"], "run": vid, "source": "screen",
                                                    "positions": [p["position"]]}) for p, vid in picks]
        view = deck_view(store, deck_id)
        card = card_public(store.deck_card(view["row"]))
    if applied is not None:
        return _applied(server, deck_id, applied, "前の版に切り替えました。", pages=view["pages"],
                        counts=_counts(view["pages"]))
    if not made:
        return _json(200, {"deck": card, "revision_id": view["revision"]["id"] if view["revision"] else None,
                           "pages": view["pages"], "counts": _counts(view["pages"]), "started": False,
                           "message": "そのページはもうこの版です。"})
    server.wake_worker()
    message = "前の版に切り替えています。終わると画面の絵が替わります。"
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
    can_generate = can_generate_images(server)   # エンジンに聞く（少し掛かる）ので、鍵の外で
    with server.store_lock:
        store = server.store
        row = _live_deck_row(store, deck_id)
        atts = _attachments(store, body, row)
        pages, args = _head_pages(store, deck_id)
        _no_structure(args)
        after = body.get("after_slide_id")
        if after is not None:
            _page(pages, after)
        result = store.create_edit(deck_id, {"op": "insert", "after": after, "text": text, "attachments": atts,
                                             "source": source, "origin_runtime": runtime, "can_generate": can_generate})
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


def _apply_now(server: "QuickSlideServer", deck_id: str, op: str, change: Any, detail: dict[str, Any]) -> dict[str, Any] | None:
    """描かない直し（非表示・並べ替え・版の切り替え）を、仕事を待たずに台本へ当てる。錠を取ってから呼ぶ。

    当てたら、発表用の HTML と書き出しを新しい並びに揃える組み直し（`render` の `reason: tidy`）を 1 つ入れる
    （待っているものがあれば足さない）。画面はその間も新しい並びと前の絵を出す（`page_view`）。
    デッキの直しの仕事が待っている・走っているときは当てずに None を返す（呼ぶ側はこれまでどおり仕事にする。
    走っている直しは古い台本を元に新しい台本を作るので、割り込むと、ここで当てた直しが消える）。
    組み直し（tidy）だけが動いているときは当てる: 組み直しは台本を書き換えないので消えない。走っている組み直しは
    古い並びで描き終えるが、待っている組み直しを 1 つ足すので、最後は新しい並びに揃う。
    """
    store = server.store
    if any(not _tidy(j) for j in store.open_jobs(deck_id)):
        return None
    head = store.head_script(deck_id)
    if head is None:
        raise ApiError(409, "no_pages", NO_PAGES_REASON)
    args = store.deck_run_args(deck_id)
    data = change(store.load_script(head))
    store.apply_screen_edit(deck_id, data, screen_pages(args, data), op=op, detail=detail)
    tidy = None if store.tidy_job_waiting(deck_id) else store.create_edit(
        deck_id, {"op": "render", "source": "screen", "reason": "tidy"})
    return {"tidy": tidy}


def _applied(server: "QuickSlideServer", deck_id: str, applied: dict[str, Any], message: str, **extra: Any) -> "Response":
    server.wake_worker()
    tidy = applied.get("tidy")
    with server.store_lock:
        card = card_public(server.store.deck_card(server.store.deck(deck_id)))
    return _json(200, {"applied": True, "job_id": tidy["job"]["id"] if tidy else None,
                       "run_id": tidy["run"]["id"] if tidy else None,
                       "revision_id": tidy["run"]["id"] if tidy else None,
                       "started": True, "queued": False, "message": message, "deck": card, **extra})


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
        key = page["key"]
        applied = _apply_now(server, deck_id, "visibility", lambda data: with_hidden(data, key, hidden),
                             {"page": slide_id, "hidden": hidden})
        if applied is None:
            result = store.create_edit(deck_id, {"op": "visibility", "page": slide_id, "hidden": hidden,
                                                 "source": "screen", "positions": [page["position"]]})
    if applied is not None:
        word = "非表示にしました。" if hidden else "表示に戻しました。"
        return _applied(server, deck_id, applied, f"{page['position']}ページを{word}")
    word = "非表示にしています。" if hidden else "表示に戻しています。"
    return _accepted(server, deck_id, result, f"{page['position']}ページを{word}")


def route_reorder(server: "QuickSlideServer", req: "Request") -> "Response":
    """並べ替え。送るのは全順序（v1 と同じ）。答えのページは**新しい並び**。

    仕事が無ければその場で台本に当てる（描かない直し）。あれば仕事にする（描き終えるまでは前の絵のまま）。"""
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
        by_id = {p["slide_id"]: p for p in pages}
        keys = [by_id[sid]["key"] for sid in ids]
        applied = _apply_now(server, deck_id, "reorder", lambda data: with_order(data, keys), {"pages": len(ids)})
        result = None if applied is not None else store.create_edit(deck_id, {"op": "reorder", "order": ids,
                                                                              "source": "screen"})
        ordered = [{**by_id[sid], "position": i} for i, sid in enumerate(ids, 1)]
        card = card_public(store.deck_card(store.deck(deck_id)))
    if applied is not None:
        return _applied(server, deck_id, applied, "並びを保存しました。", pages=ordered, counts=_counts(ordered))
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
        if store.head_script(deck_id) is None:   # 台本がまだ無い依頼のデッキは、下調べ（か台本係）から作り直す
            result = store.retry_request_intake(deck_id)
            message = "台本を書き直しています。" if result["researched"] else "もう一度下調べをしています。"
        else:
            result = store.create_edit(deck_id, {"op": "render", "source": "screen"})
            message = "いまの台本で描き直しています。"
    return _accepted(server, deck_id, result, message)


REBUILD_NOTICES = {
    "presentation": "いまの台本のまま、選んだブランド（色・ロゴ・書式）と見せ方で組み直します。文章は変わりません。"
                    "描いた絵はそのまま使います（画風が変わる見せ方を選んだときだけ、絵を描き直します）。",
    "pitch": "いまの台本のまま、選んだブランドと見せ方で描き直します。ピッチは色や見せ方が絵の描き方に入るので、"
             "変えたページの絵を描き直します（ChatGPT の枠を使います）。何も変えなければ描きません。",
    "motion": "いまの台本のまま、選んだブランドの色とロゴで組み直します。文章と動きは変わりません。"
              "全部の拍を開いて確かめ直すので、場面が多いと数分かかります（ChatGPT の枠は使いません）。",
}


# 組み直しで選べる見せ方（docs/adr/0009）。付属のスタイルのうち、あとから替えられる 2 つだけ。
# 1 枚にまとめる（infographic）はデッキの最初の回で決まり替えられない。モーションは見せ方を持たない（テーマとロゴだけ）
REBUILD_STYLES = {PRESET_STYLE_STANDARD_ID: "説明・報告（文字と図で説明する）",
                  PRESET_STYLE_VISUAL_ID: "ビジュアル（絵で印象を残す）"}


def _named(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "deleted": row["deleted_at"] is not None}


def _rebuild_styles(store: Any, mode: str) -> list[dict[str, Any]]:
    if not engine_for(mode).takes_style:
        return []
    rows = {t["id"]: t for t in store.list_styles()}
    return [{**_named(rows[sid]), "name": label} for sid, label in REBUILD_STYLES.items() if sid in rows]


def route_look(server: "QuickSlideServer", req: "Request") -> "Response":
    """デッキのいまのブランド・スタイルと、選べるもの（「設定を替えて組み直す」の材料）。"""
    deck_id = req.params["deck_id"]
    with server.store_lock:
        store = server.store
        row = _live_deck_row(store, deck_id)
        brand = store.brand(store.brand_revision(row["brand_revision_id"])["brand_id"])
        style = store.style(store.style_revision(row["style_revision_id"])["style_id"])
        brands = [_named(b) for b in store.list_brands()]
        styles = _rebuild_styles(store, row["mode"])
        caps = deck_view(store, deck_id)["capabilities"]
    current = {**_named(style), "name": REBUILD_STYLES.get(style["id"], style["name"])}
    return _json(200, {"deck_id": deck_id, "mode": row["mode"], "brand": _named(brand), "style": current,
                       "brands": brands, "styles": styles, "notice": REBUILD_NOTICES[row["mode"]],
                       "capability": caps["rebuild"]})


def _pick(store: Any, kind: str, value: Any) -> str | None:
    """選んだブランド・スタイルのいまの改訂（省けば None。消したもの・無いものは 400）。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, "validation_error", f"{kind}_id は文字列で送ってください。")
    try:
        row = store.brand(value) if kind == "brand" else store.style(value)
    except NotFoundError:
        row = None
    if row is None or row["deleted_at"] is not None or not row["current_revision_id"]:
        what = "ブランド" if kind == "brand" else "スタイル"
        raise ApiError(400, f"{kind}_not_found", f"その{what}は見つかりません（削除された可能性があります）。選び直してください。")
    return row["current_revision_id"]


def route_rebuild(server: "QuickSlideServer", req: "Request") -> "Response":
    """設定を替えて組み直す（P5）: いまの台本を、デッキのいまの（か選び直した）ブランド・スタイルで描き直す。
    台本係は呼ばない。描いた絵はキャッシュから使い回す（変わるのは組み方・色・ロゴ・書式）。"""
    deck_id = req.params["deck_id"]
    body = _body(req)
    with server.store_lock:
        store = server.store
        row = _live_deck_row(store, deck_id)
        if store.deck_template_snapshot_ref(deck_id):
            raise ApiError(409, "template_rebuild_unsupported", TEMPLATE_REBUILD_REASON)
        view = deck_view(store, deck_id)
        cap = view["capabilities"]["rebuild"]
        if not cap["available"]:
            raise ApiError(409, "no_pages", cap["reason"] or NO_PAGES_REASON)
        brand_rev = _pick(store, "brand", body.get("brand_id"))
        style_rev = _pick(store, "style", body.get("style_id"))
        changes = {}
        if brand_rev is not None and brand_rev != row["brand_revision_id"]:
            changes["brand_revision_id"] = brand_rev
        if style_rev is not None and style_rev != row["style_revision_id"]:
            changes["style_revision_id"] = style_rev
        if changes:
            store.change_deck_axes(deck_id, **changes)
        result = store.create_edit(deck_id, {"op": "rebuild", "source": "screen",
                                             "changed": sorted(k.split("_")[0] for k in changes)})
    return _accepted(server, deck_id, result, "いまの設定で組み直しています。")


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
