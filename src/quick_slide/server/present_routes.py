"""発表の画面（見せる用）の口。

- `GET /slides/<デッキ>/present`（`?p=<番号>`）           発表の画面（`web/app/present.html`。編集の画面とは別の HTML・JS・CSS）
- `GET /api/decks/<デッキ>/present`                      発表に要るもの（ページの並び・絵・エンジン・段の数・回の HTML の在りか）
- `GET /present/<デッキ>/<回>/deck.html`                 html-slide の回の HTML（仕切りの中で開く。下の CSP を付ける）
- `GET /present/<デッキ>/<回>/assets/<画像>`・`fonts/<字体>`  その HTML が読むもの（回のフォルダの `present/` の控えだけ）

見せる版は、編集の画面でいま見せている版（`shown_run`＝描き終えた一番新しい回）。非表示のページは並びに入れない。
ページの絵はどのエンジンでも PNG（`image_url`）を持ち、html-slide の回に HTML の控えがあれば `html` に在りかが入る
（無い古い回は `html` が null で、画面はページの PNG で出す）。段の数 `steps` は回の `build.json`（html-slide の記録）から。

**仕切りの中の文書（deck.html）の安全設定。** エンジンの HTML は `<style>` を使い、字体を `file://` で読み、再生のために
スクリプトが要る。だから編集の画面の CSP（`default-src 'self'`）とは別に、次の CSP を付ける:

- `default-src 'none'`（既定は何も読ませない）。外へ取りに行く先（connect・frame・form など）は全部これで閉じる
- `style-src 'unsafe-inline' 'self'`・`img-src 'self'`・`font-src 'self'`
- `script-src` は再生スクリプト 1 本のパスだけ（`'self'` にしない。ほかの `/static/` の JS を読ませない）
- `sandbox allow-scripts`（直接開かれても同一オリジンにならない。API・セッションに触れない）

外枠は `<iframe sandbox="allow-scripts">`（`allow-same-origin` なし）で開き、postMessage でつなぐ。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from ..engines import engine_for
from ..errors import NotFoundError
from ..ids import is_uuid
from ..render import workspace as W
from .deck_routes import _live_deck_row
from .errors import ApiError, not_found
from .page_view import asset_url

if TYPE_CHECKING:  # 実行時は app.py の末尾で遅延 import される（循環を避ける）
    from .app import QuickSlideServer, Request, Response

PLAYER_PATH = "/static/present-frame.js"
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


def frame_document(html: str, base: str, fonts_dir: Path) -> str:
    """回の HTML を仕切りの中で開く形にする: `file://` の字体を配信のパスへ書き換え・再生スクリプトを足す。

    字体は控え（`present/fonts/`）にあるものだけを書き換える。無いものは `src` ごと消す（元のパスを残さない・
    外へ取りに行かせない）。`base` は `/present/<デッキ>/<回>`。
    """
    def font(m: re.Match[str]) -> str:
        path = unquote(m.group(1))
        name = W.present_font_name(path)
        if (fonts_dir / name).is_file():
            return f'url("{base}/fonts/{name}")'
        return 'local("sans-serif")'

    html = _FONT_URL.sub(font, html)
    # 画像は相対パス（assets/…）なので、文書の URL（…/deck.html）からそのまま引ける。どこにも書き換えは要らない
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
    hidden = {p["page_id"] for p in store.script_pages(head["id"]) if p["hidden"]} if head else set()
    run_dir = server.root.run_dir(deck_id, shown["id"])
    steps = _steps(run_dir)
    has_html = (run_dir / W.PRESENT / "deck.html").is_file() if engine.mode == "presentation" else False
    pages = []
    for p in store.run_pages(shown["id"]):
        if p["page_id"] in hidden or p["state"] != "ready" or p["asset_state"] != "ready" or not p["asset_id"]:
            continue
        pages.append({
            "no": len(pages) + 1,
            "slide_id": p["page_id"],
            "title": p["title"],
            "image_url": asset_url(p["asset_id"]),
            "section": p["page_no"] - 1,       # 回の HTML の section の番号（0 から）。html が無ければ使わない
            "steps": steps.get(p["page_no"], 0) if has_html else 0,
        })
    out["run_id"] = shown["id"]
    out["pages"] = pages
    out["message"] = None if pages else NO_PAGES_MESSAGE
    if has_html and pages:
        out["html"] = {"url": f"/present/{deck_id}/{shown['id']}/deck.html", "sections": len(steps) or None}
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
    if rel == "deck.html":
        host = (req.header("host") or "").strip().lower()    # 検査済み（app._check_host）
        base = f"/present/{deck_id}/{run_id}"
        fonts = server.root.run_dir(deck_id, run_id) / W.PRESENT / "fonts"
        body = frame_document(data.decode("utf-8", errors="replace"), base, fonts).encode("utf-8")
        return Response(200, body, content_type=resolved.content_type,
                        headers=(("Content-Security-Policy", frame_csp(host)),))
    extra: tuple[tuple[str, str], ...] = ()
    if rel.startswith("fonts/"):
        # 仕切りの中の文書のオリジンは不透明（null）なので、字体は CORS で読まれる。字体は秘密ではない
        extra = (("Access-Control-Allow-Origin", "*"),)
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
