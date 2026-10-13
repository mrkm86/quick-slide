"""DB の仕組み: 移行（前進のみ・改変の検出・将来版の拒否）、v2 の表の形、プリセット、アクセス層の守り。"""

from __future__ import annotations

import shutil
import sqlite3
import unittest
from pathlib import Path

from helpers import make_deck, open_store, temp_dir

from quick_slide.db.connection import Transaction, connect
from quick_slide.db.migrate import LATEST_VERSION, MIGRATIONS_DIR, migrate, status
from quick_slide.enums import DECK_MODES, EVENT_KINDS
from quick_slide.errors import StartupError, ValidationError
from quick_slide.ids import (
    PRESET_BRAND_ID,
    PRESET_STYLE_INFOGRAPHIC_ID,
    PRESET_STYLE_STANDARD_ID,
    PRESET_STYLE_VISUAL_ID,
)
from quick_slide.paths import DataRoot
from quick_slide.settings import Settings
from quick_slide.store import Store

P1_TABLES = {"assets", "brands", "brand_revisions", "brand_assets", "styles", "style_revisions", "decks", "tags",
             "deck_tags", "events", "deletion_tasks", "launches", "schema_migrations"}
# P2（0002）: 取り込み・台本の版とページ・ページの同一性・描いた回とページの結果・仕事
P2_TABLES = {"intakes", "scripts", "script_pages", "page_ids", "runs", "run_pages", "jobs"}
# P3（0003）: デッキのいまの台本
P3_TABLES = {"deck_heads"}
# P5（0004）: 書き出しの記録
P5_TABLES = {"exports"}
# 0008: 同梱の見本（docs/adr/0007-bundled-samples.md）
SAMPLE_TABLES = {"samples_seen", "sample_decks"}


class MigrationTests(unittest.TestCase):
    def test_a_fresh_db_gets_exactly_the_p1_and_p2_tables(self) -> None:
        store = open_store(self)
        tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(tables, P1_TABLES | P2_TABLES | P3_TABLES | P5_TABLES | SAMPLE_TABLES)
        for v1_only in ("plan_revisions", "plan_pages", "batches", "batch_items", "attempts", "slide_versions",
                        "deck_revisions", "instructions", "tasks", "formats", "drafts"):
            # exports は P5 で作り直した（描いた回のファイルの記録。v1 の形ではない）
            self.assertNotIn(v1_only, tables)
        st = status(store.conn)
        self.assertTrue(st.up_to_date)
        self.assertEqual(st.user_version, LATEST_VERSION)
        self.assertEqual(store.root.db_path.name, "quick-slide.db")

    def test_reopening_is_idempotent_and_keeps_presets(self) -> None:
        root = DataRoot(temp_dir(self) / "home")
        first = Store.open(root, settings=Settings())
        brand_rev = first.preset_brand_revision_id()
        first.close()
        second = Store.open(root, settings=Settings())
        self.addCleanup(second.close)
        self.assertEqual(second.preset_brand_revision_id(), brand_rev, "製品の内容が同じなら改訂を足さない")
        self.assertEqual(second.ensure_presets(), {"brand_created": False, "brand_updated": False,
                                                   "styles_created": [], "styles_updated": []})
        self.assertEqual({s["id"] for s in second.list_styles()},
                         {PRESET_STYLE_STANDARD_ID, PRESET_STYLE_VISUAL_ID, PRESET_STYLE_INFOGRAPHIC_ID})
        self.assertEqual([b["id"] for b in second.list_brands()], [PRESET_BRAND_ID])

    def test_a_tampered_migration_is_refused(self) -> None:
        tmp = temp_dir(self)
        migrations = tmp / "migrations"
        shutil.copytree(MIGRATIONS_DIR, migrations)
        conn = connect(tmp / "x.db")
        self.addCleanup(conn.close)
        migrate(conn, directory=migrations)
        sql = migrations / "0001_initial.sql"
        sql.write_text(sql.read_text(encoding="utf-8") + "\n-- 書き足し\n", encoding="utf-8")
        with self.assertRaises(StartupError):
            migrate(conn, directory=migrations)

    def test_a_future_db_is_refused(self) -> None:
        tmp = temp_dir(self)
        conn = connect(tmp / "x.db")
        self.addCleanup(conn.close)
        migrate(conn)
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION + 1}")
        with self.assertRaises(StartupError):
            migrate(conn)

    # --- 履歴と版の食い違い（改変を確かめられない DB を「最新」とみなさない） ---
    def _migrated(self) -> sqlite3.Connection:
        conn = connect(temp_dir(self) / "x.db")
        self.addCleanup(conn.close)
        migrate(conn)
        return conn

    def test_a_missing_history_row_is_refused(self) -> None:
        conn = self._migrated()
        conn.execute("DELETE FROM schema_migrations WHERE version=1")
        with self.assertRaisesRegex(StartupError, "履歴に無い版 \\[1\\]"):
            migrate(conn)
        self.assertFalse(status(conn).up_to_date)

    def test_all_history_rows_missing_is_refused(self) -> None:
        conn = self._migrated()
        conn.execute("DELETE FROM schema_migrations")
        with self.assertRaises(StartupError):
            migrate(conn)
        self.assertFalse(status(conn).up_to_date)

    def test_a_missing_history_table_is_refused_and_not_recreated(self) -> None:
        conn = self._migrated()
        conn.execute("DROP TABLE schema_migrations")
        with self.assertRaisesRegex(StartupError, "履歴（schema_migrations）がありません"):
            migrate(conn)
        has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
        self.assertIsNone(has_table, "拒否するときに空の履歴の表を作らない（作ると次は通ってしまう）")

    def test_history_ahead_of_the_version_is_refused(self) -> None:
        conn = self._migrated()
        conn.execute(f"PRAGMA user_version = {LATEST_VERSION - 1}")
        with self.assertRaisesRegex(StartupError, "版より先の履歴"):
            migrate(conn)

    def test_history_on_a_version_zero_db_is_refused(self) -> None:
        conn = self._migrated()
        conn.execute("PRAGMA user_version = 0")
        with self.assertRaises(StartupError):
            migrate(conn)

    def test_a_renamed_history_row_is_refused(self) -> None:
        conn = self._migrated()
        conn.execute("UPDATE schema_migrations SET name='something_else' WHERE version=1")
        with self.assertRaises(StartupError):
            migrate(conn)

    def test_a_consistent_db_still_passes(self) -> None:
        conn = self._migrated()
        st = migrate(conn)
        self.assertTrue(st.up_to_date)
        self.assertEqual([a["version"] for a in st.applied], list(range(1, LATEST_VERSION + 1)))

    # --- 外部キーを切る移行は、確定の前に参照を検査する ---
    def _with_extra_migration(self, body: str) -> tuple[sqlite3.Connection, Path]:
        tmp = temp_dir(self)
        migrations = tmp / "migrations"
        shutil.copytree(MIGRATIONS_DIR, migrations, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
        conn = connect(tmp / "x.db")
        self.addCleanup(conn.close)
        migrate(conn, directory=migrations)
        conn.execute("INSERT INTO tags(id,name) VALUES('tag-1','一つ目')")
        conn.execute("INSERT INTO tags(id,name) VALUES('tag-2','二つ目')")
        version = LATEST_VERSION + 1
        (migrations / f"{version:04d}_test_rebuild.sql").write_text(body, encoding="utf-8")
        return conn, migrations

    def test_a_foreign_keys_off_migration_with_dangling_rows_is_rolled_back(self) -> None:
        """表を作り替えて参照先を写し忘れた、という移行。外部キーを切っているので文は全部通ってしまう。"""
        conn, migrations = self._with_extra_migration(
            "-- requires_foreign_keys_off\n"
            "CREATE TABLE test_notes(id TEXT PRIMARY KEY, tag_id TEXT NOT NULL REFERENCES tags(id));\n"
            "INSERT INTO test_notes VALUES('n-1','tag-1');\n"
            "INSERT INTO test_notes VALUES('n-2','tag-2');\n"
            "CREATE TABLE tags_new(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);\n"
            "INSERT INTO tags_new SELECT id,name FROM tags WHERE id='tag-1';\n"   # tag-2 を写し忘れる
            "DROP TABLE tags;\n"
            "ALTER TABLE tags_new RENAME TO tags;\n"
        )
        with self.assertRaisesRegex(StartupError, "参照先の無い行"):
            migrate(conn, directory=migrations)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], LATEST_VERSION, "版は進まない")
        self.assertIsNone(conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (LATEST_VERSION + 1,)).fetchone())
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name IN ('test_notes','tags_new')").fetchone())
        self.assertEqual([r[0] for r in conn.execute("SELECT id FROM tags ORDER BY id")], ["tag-1", "tag-2"],
                         "作り替えの前の表に戻る")
        self.assertFalse(conn.in_transaction)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1, "外部キーは必ず戻す")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_a_foreign_keys_off_migration_that_keeps_references_is_applied(self) -> None:
        conn, migrations = self._with_extra_migration(
            "-- requires_foreign_keys_off\n"
            "CREATE TABLE test_notes(id TEXT PRIMARY KEY, tag_id TEXT NOT NULL REFERENCES tags(id));\n"
            "INSERT INTO test_notes VALUES('n-1','tag-1');\n"
            "CREATE TABLE tags_new(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);\n"
            "INSERT INTO tags_new SELECT id,name FROM tags;\n"
            "DROP TABLE tags;\n"
            "ALTER TABLE tags_new RENAME TO tags;\n"
        )
        st = migrate(conn, directory=migrations)
        self.assertTrue(st.up_to_date)
        self.assertEqual(st.user_version, LATEST_VERSION + 1)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_the_v1_db_file_name_is_not_used(self) -> None:
        store = open_store(self)
        self.assertFalse((store.root.db_dir / "studio.sqlite3").exists())


class TransactionTests(unittest.TestCase):
    """トランザクションの境界。COMMIT そのものが失敗しても、接続に開いたトランザクションを残さない。"""

    def _broken_brand(self, store: Store) -> None:
        """遅延外部キー（brands.current_revision_id）に違反する行。文の実行では通り、COMMIT で落ちる。"""
        now = store.now()
        store.conn.execute(
            "INSERT INTO brands(id,name,current_revision_id,kind,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("broken-brand", "壊れたブランド", "no-such-revision", "user", now, now),
        )

    def _committed_tags(self, store: Store) -> list[str]:
        """別の接続から読む（同じ接続だと未確定の行も見えてしまう）。"""
        other = connect(store.root.db_path)
        try:
            return [r[0] for r in other.execute("SELECT name FROM tags ORDER BY name")]
        finally:
            other.close()

    def test_a_failed_commit_is_rolled_back_and_the_next_operation_commits(self) -> None:
        store = open_store(self)
        with self.assertRaises(sqlite3.IntegrityError):
            with store.transaction():
                self._broken_brand(store)
                store.conn.execute("INSERT INTO tags(id,name) VALUES('t-lost','消えるはずのタグ')")
        self.assertFalse(store.conn.in_transaction, "COMMIT に失敗したら巻き戻して閉じる")
        self.assertIsNone(store._one("SELECT id FROM brands WHERE id='broken-brand'"))
        # 次の独立した操作は、入れ子ではなく自分で BEGIN して確定する
        with store.transaction() as tx:
            self.assertTrue(tx._outermost)
            store.conn.execute("INSERT INTO tags(id,name) VALUES('t-kept','残るタグ')")
        self.assertFalse(store.conn.in_transaction)
        self.assertEqual(self._committed_tags(store), ["残るタグ"])

    def test_an_error_inside_rolls_back_without_hiding_the_error(self) -> None:
        store = open_store(self)
        with self.assertRaises(ValueError):
            with store.transaction():
                store.conn.execute("INSERT INTO tags(id,name) VALUES('t-x','途中のタグ')")
                raise ValueError("途中で失敗")
        self.assertFalse(store.conn.in_transaction)
        self.assertEqual(self._committed_tags(store), [])

    def test_rollback_after_sqlite_already_closed_the_transaction_keeps_the_original_error(self) -> None:
        """SQLite が先に巻き戻した後でも、ROLLBACK の失敗で元の例外を隠さない。"""
        store = open_store(self)
        with self.assertRaises(KeyError):
            with Transaction(store.conn):
                store.conn.execute("ROLLBACK")   # SQLite 側で閉じた状態を作る
                raise KeyError("元の例外")
        self.assertFalse(store.conn.in_transaction)


class DeckModeTests(unittest.TestCase):
    def test_mode_is_required_and_checked_by_the_ddl_too(self) -> None:
        store = open_store(self)
        for mode in DECK_MODES:
            self.assertEqual(make_deck(store, mode=mode)["mode"], mode)
        with self.assertRaises(ValidationError):
            make_deck(store, mode="video")
        with self.assertRaises(sqlite3.IntegrityError):
            with store.transaction():
                store.conn.execute(
                    "INSERT INTO decks(id,title,mode,brand_revision_id,style_revision_id,created_at,updated_at)"
                    " VALUES('x','t','movie',?,?,'now','now')",
                    (store.preset_brand_revision_id(), store.preset_style_revision_id()))

    def test_duplicate_copies_mode_and_final_is_refused_without_pages(self) -> None:
        from quick_slide.errors import StateError

        store = open_store(self)
        deck = make_deck(store, mode="pitch")
        self.assertEqual(store.duplicate_deck(deck["id"])["mode"], "pitch")
        with self.assertRaises(StateError):
            store.update_deck_metadata(deck["id"], status="final")


class StoreGuardTests(unittest.TestCase):
    def test_events_refuse_forbidden_keys_and_unknown_kinds(self) -> None:
        store = open_store(self)
        with store.transaction():
            with self.assertRaises(ValidationError):
                store.append_event("deck_created", {"token": "x"})
            with self.assertRaises(ValidationError):
                store.append_event("no_such_kind", {})
        self.assertIn("launch_attached", EVENT_KINDS)

    def test_launch_tokens_are_stored_only_as_hashes(self) -> None:
        store = open_store(self)
        launch = store.create_launch(requested_via="start", deck_id=make_deck(store)["id"])
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.assertNotIn(launch["token"].encode(), store.root.db_path.read_bytes())
        self.assertNotIn("token", store.launch(launch["id"]))


if __name__ == "__main__":
    unittest.main()
