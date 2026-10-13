"""同梱の見本の API（設定 › 見本。docs/adr/0006-bundled-samples.md）。

- `GET /api/samples` → `{"available", "items": [{id, title, subtitle, mode, pages, state, decks: [{id, title, current, archived}]}]}`
  （state: present＝最新の版が入っている・outdated＝古い版だけ・missing＝ごみ箱にしか無いか、無い）
- `POST /api/samples/restore` → 入れ直す。最新の版が残っている見本は何もしない。触っていない古い版は進め、
  ほかは別のデッキとして足す。`{"imported", "advanced", "kept", "message", "items"}`
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import samples as S
from .deck_routes import _json
from .errors import ApiError

if TYPE_CHECKING:
    from .app import QuickSlideServer, Request, Response

UNAVAILABLE_REASON = "このサーバは見本を入れない設定で起動しています。"
NOTHING_REASON = "同梱の見本が見つかりません。"


def _view(server: "QuickSlideServer") -> dict:
    if server.samples_dir is None:
        return {"available": False, "reason": UNAVAILABLE_REASON, "items": []}
    assert server.store is not None
    got = S.status(server.store, directory=server.samples_dir)
    return {"available": bool(got["items"]), "reason": "" if got["items"] else NOTHING_REASON, "items": got["items"]}


def route_list(server: "QuickSlideServer", req: "Request") -> "Response":
    with server.store_lock:
        return _json(200, _view(server))


def route_restore(server: "QuickSlideServer", req: "Request") -> "Response":
    if server.samples_dir is None:
        raise ApiError(409, "samples_unavailable", UNAVAILABLE_REASON)
    with server.store_lock:
        assert server.store is not None
        got = S.restore(server.store, server.root, directory=server.samples_dir, extra_env=server.tools.extra_env)
        view = _view(server)
    added, advanced = len(got["imported"]), len(got["advanced"])
    if not view["items"]:
        raise ApiError(409, "samples_unavailable", NOTHING_REASON)
    if got["problems"] and not (added or advanced):
        raise ApiError(500, "samples_failed", "見本を入れられませんでした。" + got["problems"][0])
    if added and advanced:
        message = f"見本を {added} 冊入れ、{advanced} 冊を新しい版にしました。"
    elif added:
        message = f"見本を {added} 冊入れました。"
    elif advanced:
        message = f"見本 {advanced} 冊を新しい版にしました。"
    else:
        message = "見本はすべて入っています。"
    return _json(200, {"imported": got["imported"], "advanced": got["advanced"], "kept": got["kept"],
                       "problems": got["problems"], "message": message, "items": view["items"]})
