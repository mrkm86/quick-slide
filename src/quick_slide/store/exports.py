"""書き出し（P5）の台帳: どの描いた回の、どの形式のファイルを、どの中身（sha256）で配ったか。

ファイルは描いた回の控え（`decks/<デッキ>/runs/<回>/deck.pptx` など）をそのまま指す（写さない）。
配る口（`GET /api/exports/<id>/file`）は sha256 を照らしてから配るので、同じ書き出しの URL はいつも同じバイトを返す。
同じ回・同じ形式・同じ中身の書き出しは 1 行にまとめる（押すたびに行を増やさない）。
"""

from __future__ import annotations

from typing import Any

from ..enums import EXPORT_FORMATS, require
from ..errors import NotFoundError
from .base import StoreBase


class ExportsMixin(StoreBase):
    def record_export(self, deck_id: str, *, run_id: str, fmt: str, path: str, sha256: str, size: int,
                      filename: str) -> dict[str, Any]:
        """書き出しを記録する（同じ回・形式・中身・名前なら前の行を返す）。`path` はデータの置き場からの相対。"""
        require(fmt, EXPORT_FORMATS, "exports.format")
        with self.transaction():
            row = self._one("SELECT * FROM exports WHERE run_id=? AND format=? AND sha256=? AND filename=?"
                            " ORDER BY created_at DESC,rowid DESC LIMIT 1", (run_id, fmt, sha256, filename))
            if row is not None:
                return row
            eid = self.new_id()
            self._exec("INSERT INTO exports(id,deck_id,run_id,format,path,sha256,bytes,filename,created_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?)", (eid, deck_id, run_id, fmt, path, sha256, int(size), filename,
                                                     self.now()))
            self.append_event("export_created", {"export_id": eid, "run_id": run_id, "format": fmt, "bytes": int(size)},
                              deck_id=deck_id)
            return self._get("exports", eid)

    def export(self, export_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM exports WHERE id=?", (export_id,))
        if row is None:
            raise NotFoundError("その書き出しはありません")
        return row

    def deck_exports(self, deck_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM exports WHERE deck_id=? ORDER BY created_at,rowid", (deck_id,))
