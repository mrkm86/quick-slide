"""SQLite 接続。

- WAL、外部キー有効、busy timeout
- Python 3.12+ の `autocommit=True` で暗黙 BEGIN を止め、トランザクション境界は `Transaction` が
  `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` を明示的に発行する
- 行は `sqlite3.Row`。上位へは dict に変換して渡す
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BUSY_TIMEOUT_SECONDS = 5.0


def connect(path: Path | str, *, memory: bool = False) -> sqlite3.Connection:
    target = ":memory:" if memory else str(path)
    conn = sqlite3.connect(target, timeout=BUSY_TIMEOUT_SECONDS, autocommit=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
    if not memory:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


class Transaction:
    """`with Transaction(conn):` で BEGIN IMMEDIATE。入れ子は外側に合流する（内側は BEGIN しない）。

    例外で抜けたら最外殻が ROLLBACK する。内側で捕まえて続けることは想定しない
    （途中失敗は全部 rollback）。

    **COMMIT そのものが失敗したときも ROLLBACK する**（遅延外部キーの違反は COMMIT のときに出る）。
    巻き戻さないと接続にトランザクションが開いたまま残り、同じ接続の次の「独立した」操作が入れ子と
    みなされて、いつまでも確定しない。抜けるときは、最外殻なら必ずトランザクションを閉じている。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._outermost = False

    def __enter__(self) -> "Transaction":
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
            self._outermost = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self._outermost:
            return False
        if exc_type is None:
            try:
                self.conn.execute("COMMIT")
            except BaseException:
                self._rollback_quietly()
                raise
        else:
            self._rollback_quietly()
        return False

    def _rollback_quietly(self) -> None:
        """開いていれば巻き戻す。SQLite が先に巻き戻していることもある（容量不足など）ので、閉じていれば何もしない。
        巻き戻しの失敗で元の例外を隠さない。"""
        if not self.conn.in_transaction:
            return
        try:
            self.conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def integrity_check(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]


def foreign_key_check(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    return [
        {"table": r[0], "rowid": r[1], "parent": r[2], "fkid": r[3]}
        for r in rows
    ]
