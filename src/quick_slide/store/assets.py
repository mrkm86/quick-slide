"""assets の登録・参照。ファイルの一時保存→検証→rename はサーバ側（settings_routes.upload）の責務で、ここは DB 行だけを扱う。"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..enums import ASSET_STATES, require
from ..errors import ValidationError
from .base import StoreBase

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AssetsMixin(StoreBase):
    def register_asset(
        self,
        *,
        relative_path: str,
        media_type: str,
        sha256: str,
        bytes_: int,
        width: int | None = None,
        height: int | None = None,
        state: str = "ready",
        asset_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_tx()
        if not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
            raise ValidationError("relative_path は保存ルート相対・正規化済みであること")
        if not _SHA256_RE.match(sha256):
            raise ValidationError("sha256 は小文字 hex 64 文字")
        if bytes_ < 0:
            raise ValidationError("bytes は非負")
        require(state, ASSET_STATES, "assets.state")
        aid = asset_id or self.new_id()
        try:
            self._exec(
                "INSERT INTO assets(id,relative_path,media_type,sha256,bytes,width,height,state,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (aid, relative_path, media_type, sha256, bytes_, width, height, state, self.now()),
            )
        except sqlite3.IntegrityError as exc:
            # id は PRIMARY KEY、relative_path は UNIQUE。どちらの重複でも sqlite3 の例外を上位へ出さない
            raise ValidationError("asset が重複しています（id または relative_path が既存の行と衝突）") from exc
        return self._get("assets", aid)

    def asset(self, asset_id: str) -> dict[str, Any]:
        return self._get("assets", asset_id)

    def asset_by_sha256(self, sha256: str, *, media_type: str | None = None, state: str = "ready") -> dict[str, Any] | None:
        """内容ハッシュで既存 asset を引く。同じ原本を二重に複製しないため（画像は内容が正体）。"""
        if not _SHA256_RE.match(sha256):
            raise ValidationError("sha256 は小文字 hex 64 文字")
        require(state, ASSET_STATES, "assets.state")
        sql = "SELECT * FROM assets WHERE sha256=? AND state=?"
        params: tuple = (sha256, state)
        if media_type is not None:
            sql += " AND media_type=?"
            params += (media_type,)
        # 同着は挿入順 = rowid で決める（乱数 id だと並びが実行ごとに変わる）
        return self._one(sql + " ORDER BY created_at,rowid LIMIT 1", params)

    def set_asset_state(self, asset_id: str, state: str) -> None:
        self._require_tx()
        require(state, ASSET_STATES, "assets.state")
        if self._exec("UPDATE assets SET state=? WHERE id=?", (state, asset_id)) != 1:
            raise ValidationError("asset が存在しません")

    def _require_ready_asset(self, asset_id: str, where: str) -> dict[str, Any]:
        a = self._one("SELECT * FROM assets WHERE id=?", (asset_id,))
        if a is None:
            raise ValidationError(f"{where}: asset が存在しません")
        if a["state"] != "ready":
            raise ValidationError(f"{where}: asset が ready ではありません")
        return a
