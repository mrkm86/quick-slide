"""発表の画面（見せる用）の口。

- `GET /slides/<デッキ>/present`（`?p=<番号>`）           発表の画面（`web/app/present.html`。編集の画面とは別の HTML・JS・CSS）
- `GET /api/decks/<デッキ>/present`                      発表に要るもの（ページの並び・絵・エンジン・段の数・回の HTML の在りか）
- `GET /present/<デッキ>/<回>/deck.html`                 html-slide の回の HTML（仕切りの中で開く。下の CSP を付ける）
- `GET /present/<デッキ>/<回>/index.html`                motion-slide の回の HTML 1枚（仕切りの中で開く。motion 用の CSP を付ける）
- `GET /present/<デッキ>/<回>/assets/<画像>`・`fonts/<字体>`  その HTML が読むもの（回のフォルダの `present/` の控えだけ）

見せる版は、編集の画面でいま見せている版（`shown_run`＝描き終えた一番新しい回）。非表示のページは並びに入れない。
ページの絵はどのエンジンでも PNG（`image_url`）を持ち、html-slide の回に HTML の控えがあれば `html` に在りかが入る
（無い古い回は `html` が null で、画面はページの PNG で出す）。段の数 `steps` は回の `build.json`（html-slide の記録）から。
html-slide の各ページは `html`（{url, section}）にも、そのページの区切りがある回の HTML を持つ。版を切り替えたページ
（`pins.py`）は、その版を描いた回の HTML なので、ページによって回が違うことがある（画面は回ごとに仕切りを開いて切り替える）。

**仕切りの中の文書（deck.html）の安全設定。** エンジンの HTML は `<style>` を使い、字体を `file://` で読み、再生のために
スクリプトが要る。だから編集の画面の CSP（`default-src 'self'`）とは別に、次の CSP を付ける:

- `default-src 'none'`（既定は何も読ませない）。外へ取りに行く先（connect・frame・form など）は全部これで閉じる
- `style-src 'unsafe-inline' 'self'`・`img-src 'self'`・`font-src 'self'`
- `script-src` は再生スクリプト 1 本のパスだけ（`'self'` にしない。ほかの `/static/` の JS を読ませない）
- `sandbox allow-scripts`（直接開かれても同一オリジンにならない。API・セッションに触れない）

外枠は `<iframe sandbox="allow-scripts">`（`allow-same-origin` なし）で開き、postMessage でつなぐ。

**motion-slide の HTML 1枚（index.html）の安全設定**（`motion_csp`）。エンジンは場面の hook・script を `new Function` で読み、
共通 script を JSON から script 要素にして実行するので `script-src` に `'unsafe-inline' 'unsafe-eval'` が要る。外へ何も
取りに行かない HTML（画像・字体・音は `data:`）なので、ほかは閉じる:

- `default-src 'none'`・`style-src 'unsafe-inline'`・`img-src data: 'self'`・`media-src data: 'self' blob:`・`font-src data: 'self'`
- `script-src 'unsafe-inline' 'unsafe-eval'` と橋渡しのスクリプト 1 本のパス（`/static/present-motion-frame.js`）
- `base-uri 'none'`・`form-action 'none'`・`frame-ancestors 'self'`・`sandbox allow-scripts`（同一オリジンにならない。API・セッションに触れない）

編集の画面と html-slide の CSP は変えない。橋渡しは中のエンジン（`window.MS`）と外枠を postMessage でつなぐ
（ページ＝場面・段＝拍。中の状態の変化を外枠へ返す）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..engines import engine_for
from ..errors import NotFoundError
from ..ids import is_uuid
from ..pins import head_pins
from ..render import workspace as W
from .deck_routes import _live_deck_row
from .errors import ApiError, not_found
from .page_view import asset_url

if TYPE_CHECKING:  # 実行時は app.py の末尾で遅延 import される（循環を避ける）
    from .app import QuickSlideServer, Request, Response

PLAYER_PATH = "/static/present-frame.js"
MOTION_PLAYER_PATH = "/static/present-motion-frame.js"
EDIT_URL = "/slides/{deck_id}"
NO_PAGES_MESSAGE = "見せられるページがまだありません。ページができてから開いてください。"

_FONT_URL = re.compile(r'url\("file://([^"]+)"\)')
_BODY_END = re.compile(r"</body\s*>", re.IGNORECASE)


def _response():
    from .app import Response  # 遅延 import（循環回避）

    return Response


def frame_csp(host: str) -> str:
    """仕切りの中の文書に付ける CSP。`host` は検査済みの Host ヘッダ（再生スクリプトの絶対パスに使う）。"""
    return "; ".join((
        "default-src 'none'",
        "style-src 'unsafe-inline' 'self'",
        f"script-src http://{host}{PLAYER_PATH}",
        "img-src 'self'",
        "font-src 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'self'",
        "sandbox allow-scripts",
    ))


def motion_csp(host: str) -> str:
    """仕切りの中の motion-slide の HTML 1枚に付ける CSP（理由はこのファイルの冒頭）。"""
    return "; ".join((
        "default-src 'none'",
        f"script-src 'unsafe-inline' 'unsafe-eval' http://{host}{MOTION_PLAYER_PATH}",
        "style-src 'unsafe-inline'",
        "img-src data: 'self'",
        "media-src data: 'self' blob:",
        "font-src data: 'self'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'self'",
        "sandbox allow-scripts",
    ))


def motion_document(html: str) -> str:
    """motion-slide の HTML 1枚に、橋渡しのスクリプト（`present-motion-frame.js`）を 1 本だけ足す（他は書き換えない）。"""
    tag = f'<script src="{MOTION_PLAYER_PATH}"></script>'
    if _BODY_END.search(html):
        return _BODY_END.sub(lambda m: tag + m.group(0), html, count=1)
    return html + tag


RUN_FILE_CACHE = "private, max-age=604800, immutable"
_IMG_SRC = re.compile(r"(<img\b[^>]*?\s)src=", re.I)


def frame_document(html: str, base: str, fonts_dir: Path) -> str:
    """回の HTML を仕切りの中で開く形にする: `file://` の字体を配信のパスへ書き換え・再生スクリプトを足す。

    字体は控え（`present/fonts/`）にあるものだけを書き換える。無いものは `src` ごと消す（元のパスを残さない・
    外へ取りに行かせない）。`base` は `/present/<デッキ>/<回>`。
    """
    def font(m: re.Match[str]) -> str:
        path = W.font_path_of_url(m.group(1))
        name = W.present_font_name(path)
        if (fonts_dir / name).is_file():
            return f'url("{base}/fonts/{name}")'
        return 'local("sans-serif")'

    html = _FONT_URL.sub(font, html)
    # 画像は相対パス（assets/…）なので、文書の URL（…/deck.html）からそのまま引ける。ただし src は data-qs-src に移し、
    # 再生スクリプトが見るページから読む（全ページの画像を最初にまとめて読まない。写真の多いデッキで数十 MB になる）
    html = _IMG_SRC.sub(r"\1data-qs-src=", html)
    tag = f'<script src="{PLAYER_PATH}"></script>'
    if _BODY_END.search(html):
        return _BODY_END.sub(lambda m: tag + m.group(0), html, count=1)
    return html + tag


def _steps(run_dir: Path) -> dict[int, int]:
    """ページ（回の何枚目か）→ 段の数。回の `build.json`（html-slide の記録）から。読めなければ空。"""
    rec = W.read_json(run_dir / "build.json")
    out: dict[int, int] = {}
    for row in rec.get("slides") or []:
        if isinstance(row, dict) and isinstance(row.get("no"), int):
            steps = row.get("steps")
            out[row["no"]] = steps if isinstance(steps, int) and not isinstance(steps, bool) and steps >= 0 else 0
    return out


def _clicks(run_dir: Path) -> dict[int, int]:
    """ページ（回の何枚目か）→ クリックの数（拍の数 - 1）。回の `deck-info.json`（motion-slide の記録）から。読めなければ空。"""
    rec = W.read_json(run_dir / "deck-info.json")
    out: dict[int, int] = {}
    for row in rec.get("slides") or []:
        if isinstance(row, dict) and isinstance(row.get("no"), int):
            clicks = row.get("clicks")
            out[row["no"]] = clicks if isinstance(clicks, int) and not isinstance(clicks, bool) and clicks >= 0 else 0
    return out


def present_view(server: "QuickSlideServer", deck_id: str) -> dict[str, Any]:
    """発表に要るもの。錠は呼ぶ側が取る。"""
    store = server.store
    assert store is not None
    row = _live_deck_row(store, deck_id)
    shown = store.shown_run(deck_id)
    engine = engine_for(row["mode"])
    out: dict[str, Any] = {
        "deck": {"id": deck_id, "title": row["title"]},
        "engine": engine.skill,
        "run_id": None,
        "pages": [],
        "html": None,
        "edit_url": EDIT_URL.format(deck_id=deck_id),
        "message": NO_PAGES_MESSAGE,
    }
    if shown is None:
        return out
    head = store.head_script(deck_id)
    run_dir = server.root.run_dir(deck_id, shown["id"])
    motion = engine.mode == "motion"
    html_name = "index.html" if motion else "deck.html"
    has_html = (run_dir / W.PRESENT / html_name).is_file() if engine.mode in ("presentation", "motion") else False
    steps_of: dict[str, dict[int, int]] = {}

    def steps(run_id: str) -> dict[int, int]:
        if run_id not in steps_of:
            folder = server.root.run_dir(deck_id, run_id)
            # 場面＝ページ・拍＝段（1 拍目が段 0。クリックの数が段の数）
            steps_of[run_id] = _clicks(folder) if motion else _steps(folder)
        return steps_of[run_id]

    def source(run_id: str, key: str | None, no: int) -> dict[str, Any] | None:
        """そのページの回の HTML の区切り（html-slide）。版を切り替えたページは、その版を描いた回の区切り
        （回の pictures/index.json の `present`）。その回に発表用の HTML が無ければ None。"""
        got = W.present_source(server.root.run_dir(deck_id, run_id), run_id, key or "", no)
        if got is None or not (server.root.run_dir(deck_id, got["run"]) / W.PRESENT / "deck.html").is_file():
            return None
        return got

    # 並びと非表示は**いまの台本**に従う（描かない直し＝非表示・並べ替え・版の切り替えは、組み直す前から発表に効く）。
    # 絵はそのページが指す版（版を切り替えたページはその版の回の行、ほかは見せる回の行）。見せる回で描いていないページ
    # （表示に戻したばかり）は、いまの中身のまま前の回で描いた絵を出す
    rows = {p["page_id"]: p for p in store.run_pages(shown["id"])}
    order = store.script_pages(head["id"]) if head else []
    fresh = head is not None and shown.get("script_id") == head["id"]   # 見せる回は、いまの台本で描いた回
    pins = head_pins(store, deck_id, head, order)
    earlier: dict[tuple[str, Any], dict[str, Any]] = {}
    for h in store.page_history(deck_id):   # 新しい回から
        earlier.setdefault((h["page_id"], h.get("content_sha256")), h)
    pages = []
    missing_section = False
    for sp in order:
        if sp["hidden"]:
            continue
        pin = pins.get(sp["key"])
        p = pin.row if pin is not None else rows.get(sp["page_id"])
        run_id = pin.run_id if pin is not None else shown["id"]
        if p is None or p["state"] != "ready" or p["asset_state"] != "ready" or not p["asset_id"]:
            h = earlier.get((sp["page_id"], sp.get("content_sha256"))) if sp.get("content_sha256") else None
            if h is None or not h.get("asset_id") or h.get("asset_state") != "ready":
                continue
            p, run_id = h, h["run_id"]
        entry: dict[str, Any] = {"no": len(pages) + 1, "slide_id": sp["page_id"], "title": sp["title"],
                                 "image_url": asset_url(p["asset_id"]), "section": None, "steps": 0}
        if motion:
            # HTML 1枚は見せる回のもの（中身から組むので、版を切り替えた場面も見せる回の中にある。ページの絵だけが版の回の絵）。
            # 見せる回の台本がいまの台本と同じときだけ使える（古ければ下で全部ページの絵にする）
            here = rows.get(sp["page_id"])
            if fresh and here is not None and here["state"] == "ready":
                entry["section"] = here["page_no"] - 1   # 回の HTML 1枚の場面の番号（0 から）
                entry["steps"] = steps(shown["id"]).get(here["page_no"], 0)
            else:
                missing_section = True
        elif engine.mode == "presentation":
            src = source(run_id, sp["key"], p["page_no"])
            if src is None:
                missing_section = True
            else:
                # 回の HTML の section の番号（0 から）と、その HTML の在りか（版の違うページが混ざると、回ごとに違う）
                entry["section"] = src["section"]
                entry["steps"] = steps(src["run"]).get(src["section"] + 1, 0)
                entry["html"] = {"url": f"/present/{deck_id}/{src['run']}/deck.html", "section": src["section"]}
        pages.append(entry)
    # 回の HTML を使えるのは、見せるページが全部どこかの回の区切りにあるときだけ（無いページが混ざったら、組み直しが
    # 終わるまでページの絵で出す）。モーションは中のエンジンが自分の並びで進むので、台本が回より新しければ絵で出す
    if missing_section or (motion and not fresh):
        has_html = False
    if not has_html:
        for p in pages:
            p["steps"] = 0
            p.pop("html", None)
    out["run_id"] = shown["id"]
    out["pages"] = pages
    out["message"] = None if pages else NO_PAGES_MESSAGE
    if has_html and pages:
        out["html"] = {"url": f"/present/{deck_id}/{shown['id']}/{html_name}", "sections": len(steps(shown["id"])) or None}
    return out


# --- ハンドラ ---
def route_present_screen(server: "QuickSlideServer", req: "Request") -> "Response":
    """発表の画面の HTML。デッキがあるかは画面が API で確かめる（HTML は同梱の 1 枚で、デッキの中身を持たない）。"""
    resolved = server.static.resolve("/static/present.html")
    data = resolved.read() if resolved is not None else None
    if resolved is None or data is None:
        raise ApiError(404, "ui_unavailable", "画面のファイルが見つかりません。インストールを確認してください。")
    return _response().file(resolved, data)


def route_present_data(server: "QuickSlideServer", req: "Request") -> "Response":
    with server.store_lock:
        view = present_view(server, req.params["deck_id"])
    return _response().json(200, view)


def route_present_file(server: "QuickSlideServer", req: "Request") -> "Response":
    deck_id, run_id, rel = req.params["deck_id"], req.params["run_id"], req.params["rest"]
    with server.store_lock:
        assert server.store is not None
        _live_deck_row(server.store, deck_id)
        try:
            run = server.store.run(run_id)
        except NotFoundError:
            run = None
    if run is None or run["deck_id"] != deck_id:
        raise not_found()
    resolved = server.present_files.resolve(deck_id, run_id, rel)
    data = resolved.read() if resolved is not None else None
    if resolved is None or data is None:
        raise not_found()
    Response = _response()
    if rel == "index.html":
        host = (req.header("host") or "").strip().lower()    # 検査済み（app._check_host）
        body = motion_document(data.decode("utf-8", errors="replace")).encode("utf-8")
        return Response(200, body, content_type=resolved.content_type,
                        headers=(("Content-Security-Policy", motion_csp(host)),))
    if rel == "deck.html":
        host = (req.header("host") or "").strip().lower()    # 検査済み（app._check_host）
        base = f"/present/{deck_id}/{run_id}"
        fonts = server.root.run_dir(deck_id, run_id) / W.PRESENT / "fonts"
        body = frame_document(data.decode("utf-8", errors="replace"), base, fonts).encode("utf-8")
        return Response(200, body, content_type=resolved.content_type,
                        headers=(("Content-Security-Policy", frame_csp(host)),))
    # 回の控えは描き終えたら変わらない（回の id ごとの置き場）。画像と字体はブラウザに覚えさせ、開き直すたびに読み直さない。
    # 覚えるのはこの機械のこのブラウザだけ（private）
    extra: tuple[tuple[str, str], ...] = (("Cache-Control", RUN_FILE_CACHE),)
    if rel.startswith("fonts/"):
        # 仕切りの中の文書のオリジンは不透明（null）なので、字体は CORS で読まれる。字体は秘密ではない
        extra += (("Access-Control-Allow-Origin", "*"),)
    return Response(200, data, content_type=resolved.content_type, headers=extra)


def present_route(p: str) -> dict[str, str] | None:
    """`/present/<デッキ>/<回>/<残り>`。デッキと回は小文字 UUID に限る（残りの検査は `PresentFiles.resolve`）。"""
    if not p.startswith("/present/"):
        return None
    parts = p[len("/present/"):].split("/", 2)
    if len(parts) != 3:
        return None
    deck_id, run_id, rest = parts
    if not (is_uuid(deck_id) and deck_id == deck_id.lower() and is_uuid(run_id) and run_id == run_id.lower()):
        return None
    return {"deck_id": deck_id, "run_id": run_id, "rest": rest}
