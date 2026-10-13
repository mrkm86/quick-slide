"""同梱の見本の台帳（docs/adr/0007-bundled-samples.md）。見本の中身の読み方・入れ方は `quick_slide/samples.py`。

- `samples_seen`: 一度入れた見本の id（消されても起動のたびに入れ直さない印。デッキを完全に削除しても残す）
- `sample_decks`: 見本から入れたデッキ → どの見本のどの版か・入れたときの台本の版と描いた回
"""

from __future__ import annotations

from typing import Any

from .base import StoreBase


class SamplesMixin(StoreBase):
    def sample_seen(self, sample_id: str) -> bool:
        return self._scalar("SELECT 1 FROM samples_seen WHERE sample_id=?", (sample_id,)) is not None

    def mark_sample_seen(self, sample_id: str) -> None:
        with self.transaction():
            self._exec("INSERT OR IGNORE INTO samples_seen(sample_id,first_seen_at) VALUES(?,?)", (sample_id, self.now()))

    def record_sample_deck(self, deck_id: str, *, sample_id: str, version: str, script_id: str, run_id: str) -> None:
        """見本から入れた（進めた）デッキを書く。進めたときは同じ行の版・台本・回を替える。"""
        with self.transaction():
            now = self.now()
            self._exec(
                "INSERT INTO sample_decks(deck_id,sample_id,version,script_id,run_id,imported_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?) ON CONFLICT(deck_id) DO UPDATE SET version=excluded.version,"
                " script_id=excluded.script_id,run_id=excluded.run_id,updated_at=excluded.updated_at",
                (deck_id, sample_id, version, script_id, run_id, now, now))

    def sample_decks(self, sample_id: str | None = None) -> list[dict[str, Any]]:
        """見本から入れたデッキ（新しく入れた順）。デッキの状態（ごみ箱か）と、入れたときのまま触っていないかを添える。

        触っていない = ごみ箱になく、いまの台本が入れたときの版のまま、一番新しい回が入れたときの回のまま、
        仕事が待っても走ってもいない（名前の変更・ピン留め・アーカイブは数えない）。"""
        where, params = ("WHERE s.sample_id=?", (sample_id,)) if sample_id else ("", ())
        rows = self._all(
            "SELECT s.deck_id,s.sample_id,s.version,s.script_id,s.run_id,s.imported_at,s.updated_at,"
            "  d.title,d.mode,d.deleted_at,d.archived_at,h.script_id AS head_script_id,"
            "  (SELECT r.id FROM runs r WHERE r.deck_id=s.deck_id ORDER BY r.number DESC LIMIT 1) AS latest_run_id,"
            "  (SELECT 1 FROM jobs j WHERE j.deck_id=s.deck_id AND j.state IN ('queued','running') LIMIT 1) AS working"
            " FROM sample_decks s JOIN decks d ON d.id=s.deck_id LEFT JOIN deck_heads h ON h.deck_id=s.deck_id "
            + where + " ORDER BY s.imported_at DESC,s.rowid DESC", params)
        for r in rows:
            r["live"] = r["deleted_at"] is None
            r["untouched"] = (r["live"] and not r["working"] and r["head_script_id"] == r["script_id"]
                              and r["latest_run_id"] == r["run_id"])
            for k in ("head_script_id", "latest_run_id", "working"):
                r.pop(k)
        return rows
