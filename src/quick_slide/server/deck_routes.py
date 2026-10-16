"""デッキ API。ハンドラは `(server, req) -> Response` の関数で、`app.ROUTES` に並ぶ。

読み取り（GET。Host 検査のみ）:
- `GET /api/vocabulary`                      状態名の対応表（正本は `vocabulary.py`）
- `GET /api/decks/home?mode=`                ピン枠 + 最近 10 件 + 一覧へ送る残り件数
- `GET /api/decks?q=&status=&mode=&archived=&pinned=&tag=&limit=&offset=`  一覧・検索（件数付き）
  どちらも `modes`（形式ごとの件数。形式の絞り込みを外した同じ条件で数える。並びは `vocabulary.DECK_KINDS`）を返す
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
まだ無い操作は `capabilities` に「使えない」と理由を載せ、画面はそれを見て無効化する。書き出しは `server/export_routes.py`。
直す・版から戻す・ページの操作（P3）は `server/edit_routes.py`。

ページの並びはデッキのいまの台本、絵は「見せる回」（描き終えた一番新しい回）のもの（`server/page_view.py`）。
台本係・描き係がいま何をしているかは `planning.message`、まだ働いているかは `live.working`
（仕事が待っているか動いている間は真）。
画面の実況は `last_event_seq` が動いたときだけ描き直す（ワーカーは状態が変わるたびに出来事を書く）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from ..enums import DECK_MODES
from ..errors import NotFoundError
from ..store.management import WORKING_PURGE_REASON
from .errors import RELOAD_AND_RETRY_MESSAGE, ApiError
from .later import NOT_READY_REASON
from .vocabulary import (
    DECK_KINDS,
    GROUP_LABELS,
    PURGE_ALL_LABEL,
    PURGE_WORDS,
    deck_mode_label,
    deck_status_label,
    page_count_label,
    planning_message,
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
# スライドの画面の操作の語（`capabilities[*].label`）
SCREEN_LABELS = {"page_instruction": "送信", "pages_instruction": "選んだページに送る", "deck_instruction": "送信",
                 "regenerate": "作り直す", "adopt_version": ADOPT_LABEL, "insert_page": INSERT_PAGE_LABEL,
                 "reorder_pages": REORDER_LABEL, "duplicate_page": "このページを複製",
                 "page_visibility": "表示・非表示", "export": EXPORT_LABEL, "generate": "画像を生成",
                 "present": "発表", "present_from_here": "ここから発表",
                 "rebuild": "設定を替えて組み直す"}

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
    "rebuild": {"available": False, "label": "設定を替えて組み直す", "reason": "スライドの画面から行えます。"},
    "purge": {"available": False, "label": PURGE_LABEL, "reason": "完全に削除できるのはごみ箱の中のスライドだけです。"},
}


def _not_ready(label: str) -> dict[str, Any]:
    return {"available": False, "label": label, "reason": NOT_READY_REASON}


def deck_capabilities(row: dict[str, Any], *, pages: list[dict[str, Any]] | None = None,
                      args: dict[str, Any] | None = None, resume: dict[str, Any] | None = None) -> dict[str, Any]:
    """**スライドの画面に返る `capabilities` の正本。** 静的な表の上に、スライドの画面での答えを重ねる。

    直す・版から戻す・ページの操作・書き出し・設定を替えて組み直すは、ページがあれば使える（仕事が走っていても
    受け付けて順に並べる。書き出しは見せる回のファイルを配る）。差し込み・並べ替えは「ページが無い」が先に効く。
    """
    from .page_view import edit_capabilities

    return {
        **CAPABILITIES,
        **edit_capabilities(row, pages or [], args or {}, labels=SCREEN_LABELS),
        "resume": resume or _not_ready(RESUME_LABEL),
    }


def purge_capability(store: Any, row: dict[str, Any]) -> dict[str, Any]:
    """完全に削除できるか（ごみ箱の 1 行ごとに変わるので静的な表には置かない）。

    描いている途中のスライドは消せない（`store.purge_deck_from_trash` と同じ 1 文で断る）。
    """
    if store.deck_working(row["id"]):
        return {"available": False, "label": PURGE_LABEL, "reason": WORKING_PURGE_REASON}
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


# 止まっている帯に出す回の終わり方。ログイン切れ・利用枠（台本係・下調べ係の CLI・エンジンの Codex）
STOPPED_KINDS = {"auth": "auth", "dependency": "missing", "writer_auth": "auth", "writer_rate": "rate_limited",
                 "writer_missing": "missing", "research_auth": "auth", "research_rate": "rate_limited",
                 "research_missing": "missing"}


RESUME_NOTHING_REASON = "描き直すものはありません。"
RESUME_WORKING_REASON = "いま作っています。終わるまでお待ちください。"


def resume_capability(store, deck_id: str, latest: dict[str, Any] | None, shown: dict[str, Any] | None) -> dict[str, Any]:
    """再開（いまの台本で描き直す）: 一番新しい回が失敗していて、いまの台本があるときだけ押せる。"""
    if store.deck_working(deck_id):
        return {"available": False, "label": RESUME_LABEL, "reason": RESUME_WORKING_REASON}
    if store.deck_template_snapshot_ref(deck_id):   # 素材の準備がつながるまで、再開しても同じ所で止まる
        from ..store.edits import TEMPLATE_RESUME_REASON

        return {"available": False, "label": RESUME_LABEL, "reason": TEMPLATE_RESUME_REASON}
    head = store.head_script(deck_id)
    behind = head is not None and (shown is None or shown.get("script_id") != head["id"])
    if head is not None and latest is not None and (latest["state"] == "failed" or behind):
        return {"available": True, "label": RESUME_LABEL, "reason": None}
    # 台本がまだ無くても、依頼から作るデッキなら下調べ（か台本係）からやり直せる（docs/adr/0002-create-from-screen.md）
    if head is None and latest is not None and latest["state"] == "failed" and store.retryable_request_intake(deck_id):
        return {"available": True, "label": RESUME_LABEL, "reason": None}
    return {"available": False, "label": RESUME_LABEL, "reason": RESUME_NOTHING_REASON}


def limit_state(row: dict[str, Any], latest: dict[str, Any] | None = None,
                resume: dict[str, Any] | None = None) -> dict[str, Any]:
    """止まっている帯（ログイン切れ・枠切れ）の答え。形は v1 の画面が読むものと同じ
    （`stopped` / `reached` / `message` / `hint_at` / `resume`）。

    一番新しい描いた回が、ログイン切れ（エンジンの rc=4・台本係の CLI）か枠切れで終わったときだけ帯を出す。
    再開（いまの台本で描き直す。P3）は `resume_capability`。文は回が残した 1 文（どうすればよいかを言う）をそのまま出す。
    """
    stopped = latest is not None and latest["state"] == "failed" and latest.get("failure_kind") in STOPPED_KINDS
    return {
        "reached": bool(stopped and STOPPED_KINDS.get(latest.get("failure_kind")) == "rate_limited"),
        "stopped": bool(stopped), "paused": False,
        "cause": STOPPED_KINDS.get(latest.get("failure_kind")) if stopped else None,
        "since": latest.get("ended_at") if stopped else None,
        "retry_not_before": None, "hint_at": None, "hint_source": None, "hint_source_label": None,
        "message": latest.get("message") if stopped else None, "remaining": 0,
        "resume": resume or {"available": False, "label": RESUME_LABEL, "reason": NOT_READY_REASON},
    }


# 企画（台本を書く係）の帯。何も動いていないとき
PLANNING_IDLE: dict[str, Any] = {"state": None, "active": False, "message": None}
# 「まだ働いているか」の答え。画面はこの 1 つを読んで実況を続けるか決める
LIVE_IDLE: dict[str, Any] = {"working": False, "reason": "idle"}


def planning_state(latest: dict[str, Any] | None) -> dict[str, Any]:
    """台本係・描き係がいま何をしているか（帯の 1 文）。終わった回は失敗のときだけ理由を出す。"""
    if latest is None:
        return dict(PLANNING_IDLE)
    if latest["state"] == "failed":
        if latest.get("failure_kind") in STOPPED_KINDS:
            return dict(PLANNING_IDLE)  # 理由は止まっている帯（limit）が出す。同じ文を 2 か所に出さない
        return {"state": "failed", "active": False, "message": latest.get("message")}
    if latest["state"] == "done":
        # 一部だけ描けなかったときは、その 1 文を残す（黙って完成に見せない）
        return {"state": "done", "active": False, "message": latest.get("message")}
    progress = latest.get("progress") or {}
    # 下調べ（依頼から作る道）は回の状態では writing のまま。帯には段の名前で出す
    phase = progress.get("phase")
    state = phase if latest["state"] == "writing" and phase in ("researching", "importing") else latest["state"]
    return {"state": state, "active": True, "message": planning_message(latest["state"], progress)}


def live_state(store, deck_id: str) -> dict[str, Any]:
    return {"working": True, "reason": "working"} if store.deck_working(deck_id) else dict(LIVE_IDLE)


def _tidy(job: dict[str, Any]) -> bool:
    req = job.get("request") or {}
    return req.get("op") == "render" and req.get("reason") == "tidy"


def deck_view(store, deck_id: str) -> dict[str, Any]:
    """スライドの画面と実況の両方が読む材料（同じ式を 2 か所に書かない）。錠を取ってから呼ぶ。"""
    from .page_view import pages_view, pending_inserts

    row = _live_deck_row(store, deck_id)
    shown = store.shown_run(deck_id)
    latest = store.latest_run(deck_id)
    jobs = store.open_jobs(deck_id)
    pages = pages_view(store, deck_id, shown=shown, latest=latest, jobs=jobs)
    revision = None if shown is None else {"id": shown["id"], "revision": shown["number"], "reason": "run",
                                           "created_at": shown["created_at"]}
    resume = resume_capability(store, deck_id, latest, shown)
    planning = planning_state(latest)
    if planning["active"] and jobs and all(_tidy(j) for j in jobs):
        # 描かない直しの後の組み直しは裏で走らせる。画面は新しい並びと前の絵をもう出しているので、帯は出さない
        planning = dict(PLANNING_IDLE)
    return {
        "row": row, "pages": pages, "revision": revision,
        "planning": planning, "live": live_state(store, deck_id),
        "limit": limit_state(row, latest, resume), "seq": store.last_event_seq(deck_id),
        "pending_inserts": pending_inserts(store, jobs),
        "capabilities": deck_capabilities(row, pages=pages, args=(shown or latest or {}).get("args") or {},
                                          resume=resume),
    }


# --- 読み取り ---
def route_vocabulary(server: "QuickSlideServer", req: "Request") -> "Response":
    return _json(200, vocabulary(server.kind_refusals()))


def _mode_param(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    if raw not in DECK_MODES:
        raise ApiError(400, "validation_error", f"mode は {' / '.join(DECK_MODES)} のいずれかで指定してください。")
    return raw


def mode_facets(counts: dict[str, int]) -> list[dict[str, Any]]:
    """形式の絞り込みの選択肢（`DECK_KINDS` の順・件数つき）。"""
    return [{"mode": k["mode"], "label": k["label"], "count": int(counts.get(k["mode"], 0))} for k in DECK_KINDS]


def route_home(server: "QuickSlideServer", req: "Request") -> "Response":
    mode = _mode_param(_query(req).get("mode"))
    with server.store_lock:
        assert server.store is not None
        data = server.store.home_decks(mode=mode)
    data["modes"] = mode_facets(data.pop("mode_counts"))
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
    mode = _mode_param(q.get("mode"))
    limit = _int_param(q.get("limit"), "limit", 50)
    offset = _int_param(q.get("offset"), "offset", 0)
    with server.store_lock:
        assert server.store is not None
        data = server.store.search_decks(q=q.get("q") or None, status=status, mode=mode, archived=archived,
                                         pinned=pinned, brand_id=q.get("brand") or None, tag=q.get("tag") or None,
                                         limit=limit, offset=offset)
    data["items"] = [card_public(c) for c in data["items"]]
    data["modes"] = mode_facets(data.pop("mode_counts"))
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
    from .. import edit_kinds
    from .edit_routes import can_generate_images  # 遅延 import（循環回避）

    deck_id = req.params["deck_id"]
    can_generate = can_generate_images(server)   # エンジンに聞く（少し掛かる）ので、鍵の外で
    with server.store_lock:
        assert server.store is not None
        store = server.store
        view = deck_view(store, deck_id)
        card = card_public(store.deck_card(view["row"]))
        imported_images = _imported_images(store, deck_id, view["pages"])
    return _json(200, {
        "deck": card,
        # 構成（台本の版＋採用した描いた回）。P2 は見せる回（P3 で採用が入る）
        "revision": view["revision"],
        "pages": view["pages"],
        "counts": _counts(view["pages"]),
        "batch": None,
        "limit": view["limit"],
        "pending_inserts": view["pending_inserts"],
        "planning": view["planning"],
        "live": view["live"],
        "last_event_seq": view["seq"],
        "capabilities": view["capabilities"],
        # 書き出しのメニューの形式（モードで決まる。可否は capabilities.export と、ここの形式ごとの理由）
        "export_formats": _export_formats(view["row"]["mode"]),
        # 修正の欄の「何を直しますか？」に出す種類と選択肢（モードと機械で決まる。docs/adr/0008-edit-kinds.md）
        "edit_kinds": edit_kinds.offered(view["row"]["mode"], can_generate=can_generate),
        # 取り込んだページ（PPTX から。ページの type が html）の修正の欄に出す種類（docs/adr/0013）
        "imported_edit_kinds": edit_kinds.offered_for_imported(view["row"]["mode"], can_generate=can_generate),
        # 取り込んだページの中の画像の数（「差し替える」はページに画像があるときだけ出す。docs/adr/0015）
        "imported_images": imported_images,
    })


def _imported_images(store: Any, deck_id: str, pages: list[dict[str, Any]]) -> dict[str, int]:
    """取り込んだページ（型 html）ごとの、中の画像の数（ページの id → 数）。取り込んだページが無ければ台本を読まない。"""
    from .. import importing
    from ..scriptdoc import IMPORTED_TYPE, slides_of

    keys = {p["key"]: p["slide_id"] for p in pages if p.get("type") == IMPORTED_TYPE}
    if not keys:
        return {}
    head = store.head_script(deck_id)
    data = store.load_script(head) if head else {}
    # 元のページの画像と、前に重ねた挿絵・図解・画像（どちらも差し替えられる）
    return {keys[str(s.get("id"))]: len(importing.page_images(s.get("html") or "")) + len(s.get("illustrations") or [])
            for s in slides_of(data) if str(s.get("id")) in keys}


def _export_formats(mode: str) -> list[dict[str, Any]]:
    from .page_view import export_formats

    return export_formats(mode)


def route_progress(server: "QuickSlideServer", req: "Request") -> "Response":
    deck_id = req.params["deck_id"]
    with server.store_lock:
        assert server.store is not None
        store = server.store
        view = deck_view(store, deck_id)
        now = store.now()
    return _json(200, {
        "deck_id": deck_id,
        "candidates": [],
        "revision_id": view["revision"]["id"] if view["revision"] else None,
        "server_time": now,
        "last_event_seq": view["seq"],
        "batch": None,
        "limit": view["limit"],
        "planning": view["planning"],
        "live": view["live"],
        "pages": view["pages"],
        "counts": _counts(view["pages"]),
        "pending_inserts": view["pending_inserts"],
        "capabilities": view["capabilities"],
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
    from ..render.caches import forget_duplicate_cache, seed_duplicate

    try:                                          # 描いた絵を複製の区画へ（できなければ次に描くとき描き直すだけ）
        seed_duplicate(server.root, new["id"], server.tools.extra_env)
    except Exception:  # noqa: BLE001
        try:
            forget_duplicate_cache(server.root, new["id"])   # 元のデッキの区画を指したまま残さない
        except Exception:  # noqa: BLE001
            pass
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
