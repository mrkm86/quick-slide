"""新しいリリースのお知らせの API（ホームのお知らせ・設定 › 更新。docs/adr/0020-release-and-update-notice.md）。

- `GET /api/update` → `UpdateChecker.status` の形。`?refresh=1` は覚えた答えを使わずに確かめ直す（「今すぐ確かめる」）
- `POST /api/settings/updates` `{check: bool}` → `settings.json` の `updates.check` を書き、新しい状態を返す
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from .. import updates as U
from ..errors import ValidationError
from ..settings import save_updates_section
from .deck_routes import _json, _query
from .errors import ApiError

if TYPE_CHECKING:
    from .app import QuickSlideServer, Request, Response


def _disabled_by(server: "QuickSlideServer") -> str | None:
    if U.env_disables(os.environ):
        return "env"
    if not server.refresh_settings().updates.check:
        return "settings"
    return None


def route_status(server: "QuickSlideServer", req: "Request") -> "Response":
    refresh = _query(req).get("refresh") in ("1", "true")
    return _json(200, server.updates.status(disabled_by=_disabled_by(server), refresh=refresh))


def route_save(server: "QuickSlideServer", req: "Request") -> "Response":
    body = req.json_object()
    if set(body) - {"check"} or not isinstance(body.get("check"), bool):
        raise ApiError(400, "bad_update_setting", "設定を受け取れませんでした。画面を読み込み直してください。")
    try:
        with server.store_lock:   # settings.json を書くほかの口（設定 › アクセス）と同じ鍵で並べる
            save_updates_section(server.root.settings_path, check=body["check"])
    except ValidationError:
        # settings.json が手で壊されているときなど。中身の診断は返さない
        raise ApiError(409, "settings_unreadable", "設定のファイルを読めないため保存できませんでした。settings.json を確認してください。") from None
    return _json(200, server.updates.status(disabled_by=_disabled_by(server)))
