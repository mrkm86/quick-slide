"""DB の仕組み: 移行（前進のみ・改変の検出・将来版の拒否）、v2 の表の形、プリセット、アクセス層の守り。"""

from __future__ import annotations

import shutil
import sqlite3
import unittest

from helpers import make_deck, open_store, temp_dir

from quick_slide.db.connection import connect
from quick_slide.db.migrate import LATEST_VERSION, MIGRATIONS_DIR, migrate, status
from quick_slide.enums import DECK_MODES, EVENT_KINDS
from quick_slide.errors import StartupError, ValidationError
from quick_slide.ids import PRESET_BRAND_ID, PRESET_STYLE_STANDARD_ID, PRESET_STYLE_VISUAL_ID
from quick_slide.paths import DataRoot
from quick_slide.settings import Settings
from quick_slide.store import Store

P1_TABLES = {"assets", "brands", "brand_revisions", "brand_assets", "styles", "style_revisions", "decks", "tags",
             "deck_tags", "events", "deletion_tasks", "launches", "schema_migrations"}
# P2（0002）: 取り込み・台本の版とページ・ページの同一性・描いた回とページの結果・仕事
P2_TABLES = {"intakes", "scripts", "script_pages", "page_ids", "runs", "run_pages", "jobs"}


class MigrationTests(unittest.TestCase):
    def test_a_fresh_db_gets_exactly_the_p1_and_p2_tables(self) -> None:
        store = open_store(self)
        tables = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        self.assertEqual(tables, P1_TABLES | P2_TABLES)
        for v1_only in ("plan_revisions", "plan_pages", "batches", "batch_items", "attempts", "slide_versions",
                        "deck_revisions", "instructions", "tasks", "formats", "drafts", "exports"):
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
        self.assertEqual({s["id"] for s in second.list_styles()}, {PRESET_STYLE_STANDARD_ID, PRESET_STYLE_VISUAL_ID})
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

    def test_the_v1_db_file_name_is_not_used(self) -> None:
        store = open_store(self)
        self.assertFalse((store.root.db_dir / "studio.sqlite3").exists())


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
