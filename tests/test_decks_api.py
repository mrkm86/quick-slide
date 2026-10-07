"""デッキの API: ホーム・一覧・ごみ箱・スライドの画面の材料・実況・管理の操作・まだ使えない口。

P1 のデッキはページを持たない。画面が読む項目は全部そろっていて（v1 と同じ形）、
描く・直す・書き出しは `capabilities` で押せないと言い、口を叩けば「まだ使えない」と答える。
"""

from __future__ import annotations

import hashlib
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import quote

from helpers import make_deck, png_bytes, start_server, temp_dir

from quick_slide.errors import StateError
from quick_slide.paths import DataRoot
from quick_slide.server.app import run_startup_sweep
from quick_slide.server.later import NOT_READY_REASON
from quick_slide.settings import Settings
from quick_slide.store import Store
from quick_slide.store import management

# スライドの画面（web/app/deck.js）が `GET /api/decks/<id>` から読む項目
DECK_SCREEN_KEYS = {"deck", "revision", "pages", "counts", "limit", "pending_inserts", "planning", "live",
                    "last_event_seq", "capabilities"}
# 実況（`/progress`）から読む項目
PROGRESS_KEYS = {"planning", "counts", "pages", "capabilities", "candidates", "limit", "pending_inserts",
                 "revision_id", "last_event_seq", "live"}
# 画面が `SS.cap(caps, key)` で読む操作
SCREEN_CAPABILITIES = {"deck_instruction", "page_instruction", "regenerate", "adopt_version", "export",
                       "reorder_pages", "insert_page", "resume"}
CARD_KEYS = {"id", "title", "pinned", "lock_version", "updated_at", "page_count", "page_count_label", "thumbnail_url",
             "brand", "mode"}


class ReadApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = start_server(self)
        self.talk = self.fx.make_deck("プレゼンの資料", mode="presentation", purpose="四半期の報告")
        self.pitch = self.fx.make_deck("ピッチの資料", mode="pitch")
        with self.fx.server.store_lock:
            self.fx.store.pin_deck(self.pitch["id"], True)
            self.fx.store.add_tag(self.talk["id"], "営業")

    def test_home_lists_pinned_and_recent_cards(self) -> None:
        data = self.fx.get("/api/decks/home").json()
        self.assertEqual([c["id"] for c in data["pinned"]], [self.pitch["id"]])
        self.assertEqual([c["id"] for c in data["recent"]], [self.talk["id"]])
        self.assertEqual(data["more_count"], 0)
        for card in data["pinned"] + data["recent"]:
            self.assertTrue(CARD_KEYS <= set(card), CARD_KEYS - set(card))
            self.assertEqual((card["page_count"], card["page_count_label"], card["thumbnail_url"]), (0, "0枚", None))
        self.assertEqual(data["pinned"][0]["mode"], "pitch")
        self.assertEqual(data["pinned"][0]["mode_label"], "ピッチ")
        self.assertIn("purge", data["capabilities"])

    def test_list_search_filters_and_total(self) -> None:
        data = self.fx.get("/api/decks?limit=24&offset=0").json()
        self.assertEqual((data["total"], len(data["items"])), (2, 2))
        self.assertEqual(self.fx.get("/api/decks?q=" + quote("四半期")).json()["total"], 1, "目的でも引ける")
        self.assertEqual(self.fx.get("/api/decks?q=" + quote("営業")).json()["total"], 1, "タグでも引ける")
        self.assertEqual(self.fx.get("/api/decks?pinned=1").json()["total"], 1)
        self.assertEqual(self.fx.get("/api/decks?mode=pitch").json()["items"][0]["id"], self.pitch["id"])
        self.assertEqual(self.fx.get("/api/decks?status=draft").json()["total"], 2)
        for bad in ("/api/decks?status=nope", "/api/decks?mode=movie", "/api/decks?limit=0", "/api/decks?pinned=maybe"):
            r = self.fx.get(bad)
            self.assertEqual((r.status, r.error_code), (400, "validation_error"), bad)
        self.assertEqual([t["name"] for t in self.fx.get("/api/tags").json()["items"]], ["営業"])

    def test_deck_screen_has_every_key_the_screen_reads_for_a_deck_with_no_pages(self) -> None:
        for deck in (self.talk, self.pitch):
            data = self.fx.get(f"/api/decks/{deck['id']}").json()
            self.assertTrue(DECK_SCREEN_KEYS <= set(data), DECK_SCREEN_KEYS - set(data))
            self.assertEqual(data["deck"]["id"], deck["id"])
            self.assertEqual(data["deck"]["mode"], deck["mode"])
            self.assertEqual((data["pages"], data["revision"], data["pending_inserts"]), ([], None, []))
            self.assertEqual(data["counts"]["total"], 0)
            self.assertEqual(data["counts"]["headline"], "ページなし")
            self.assertEqual(set(data["counts"]["labels"]), {"ready", "generating", "waiting", "failed"})
            self.assertFalse(data["limit"]["stopped"])
            self.assertEqual(set(data["limit"]["resume"]), {"available", "label", "reason"})
            self.assertEqual(data["planning"], {"state": None, "active": False, "message": None})
            self.assertEqual(data["live"], {"working": False, "reason": "idle"})
            self.assertIsInstance(data["last_event_seq"], int)
            caps = data["capabilities"]
            self.assertTrue(SCREEN_CAPABILITIES <= set(caps), SCREEN_CAPABILITIES - set(caps))
            for key, cap in caps.items():
                self.assertEqual(set(cap), {"available", "label", "reason"}, key)
                self.assertFalse(cap["available"], key)
                self.assertTrue(cap["reason"], key)
            # 直す口は P3 からある。ページが無いうちは「ページが無い」と言う。書き出しは P5 まで「まだ」
            for key in ("deck_instruction", "page_instruction", "pages_instruction", "regenerate", "adopt_version",
                        "insert_page", "reorder_pages", "duplicate_page", "page_visibility"):
                self.assertEqual(caps[key]["reason"], "まだページがありません。ページができるまでお待ちください。", key)
            self.assertEqual(caps["export"]["reason"], NOT_READY_REASON)
            self.assertEqual(caps["resume"]["reason"], "描き直すものはありません。")

    def test_progress_has_every_key_the_screen_reads(self) -> None:
        data = self.fx.get(f"/api/decks/{self.talk['id']}/progress").json()
        self.assertTrue(PROGRESS_KEYS <= set(data), PROGRESS_KEYS - set(data))
        self.assertEqual((data["pages"], data["candidates"], data["revision_id"]), ([], [], None))
        before = data["last_event_seq"]
        self.fx.post(f"/api/decks/{self.talk['id']}/title", {"title": "名前を変えた"})
        self.assertGreater(self.fx.get(f"/api/decks/{self.talk['id']}/progress").json()["last_event_seq"], before,
                           "そのデッキの出来事が増えたら番号が進む（画面はこれで描き直しを決める）")

    def test_unknown_or_deleted_deck_is_404_in_japanese(self) -> None:
        for path in (f"/api/decks/{uuid.uuid4()}", f"/api/decks/{uuid.uuid4()}/progress"):
            r = self.fx.get(path)
            self.assertEqual((r.status, r.error_code), (404, "deck_not_found"))
            self.assertIn("見つかりません", r.json()["error"]["message"])
        self.fx.post(f"/api/decks/{self.talk['id']}/delete")
        self.assertEqual(self.fx.get(f"/api/decks/{self.talk['id']}").status, 404)

    def test_vocabulary_has_every_key_the_screen_reads(self) -> None:
        v = self.fx.get("/api/vocabulary").json()
        for key in ("page_states", "groups", "deck_statuses", "edit_modes", "reorder", "card", "card_limits", "versions",
                    "instruction", "instruction_limits", "export_formats", "export", "purge", "insert", "deck_modes"):
            self.assertIn(key, v, key)
        self.assertEqual(v["deck_modes"], {"presentation": "プレゼン", "pitch": "ピッチ"})
        self.assertEqual(v["card_limits"], {"title": 200})
        for key in ("empty_reason", "remake_forced_reason", "text_too_long_reason", "too_many_attachments_reason"):
            self.assertTrue(v["instruction"][key])
        self.assertTrue(v["insert"]["empty_reason"])

    def test_responses_never_contain_file_paths(self) -> None:
        root = str(self.fx.root.root).encode()
        for path in ("/api/decks/home", "/api/decks", f"/api/decks/{self.talk['id']}", "/api/trash", "/api/settings"):
            self.assertNotIn(root, self.fx.get(path).body, path)


class MutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = start_server(self)
        self.deck = self.fx.make_deck("元の名前", mode="pitch")

    def test_mutations_without_session_are_rejected_and_write_nothing(self) -> None:
        for path in ("title", "pin", "duplicate", "delete"):
            r = self.fx.request("POST", f"/api/decks/{self.deck['id']}/{path}", {"title": "x", "pinned": True})
            self.assertEqual((r.status, r.error_code), (403, "session_required"), path)
        card = self.fx.get(f"/api/decks/{self.deck['id']}").json()["deck"]
        self.assertEqual((card["title"], card["pinned"]), ("元の名前", False))

    def test_rename_pin_and_stale_lock_version(self) -> None:
        lock = self.fx.get(f"/api/decks/{self.deck['id']}").json()["deck"]["lock_version"]
        r = self.fx.post(f"/api/decks/{self.deck['id']}/title", {"title": "  新しい名前  ", "lock_version": lock})
        self.assertEqual(r.status, 200, r)
        self.assertEqual(r.json()["deck"]["title"], "新しい名前")
        r = self.fx.post(f"/api/decks/{self.deck['id']}/pin", {"pinned": True, "lock_version": lock})
        self.assertEqual((r.status, r.error_code), (409, "conflict"))
        self.assertNotIn("lock_version", r.json()["error"]["message"], "列名を利用者に見せない")
        r = self.fx.post(f"/api/decks/{self.deck['id']}/pin", {"pinned": True})
        self.assertTrue(r.json()["deck"]["pinned"])
        for bad in ({"title": ""}, {"title": "x" * 201}, {"title": 3}):
            r = self.fx.post(f"/api/decks/{self.deck['id']}/title", bad)
            self.assertEqual((r.status, r.error_code), (400, "title_required"), bad)
        r = self.fx.post(f"/api/decks/{self.deck['id']}/pin", {"pinned": "yes"})
        self.assertEqual((r.status, r.error_code), (400, "validation_error"))

    def test_duplicate_keeps_the_mode_and_does_not_pin(self) -> None:
        self.fx.post(f"/api/decks/{self.deck['id']}/pin", {"pinned": True})
        r = self.fx.post(f"/api/decks/{self.deck['id']}/duplicate")
        self.assertEqual(r.status, 201, r)
        copy = r.json()["deck"]
        self.assertEqual((copy["title"], copy["mode"], copy["pinned"], copy["origin_deck_id"]),
                         ("元の名前のコピー", "pitch", False, self.deck["id"]))

    def test_the_mode_cannot_be_changed_after_creation(self) -> None:
        from quick_slide.errors import ValidationError

        with self.assertRaises(ValidationError):
            self.fx.store.update_deck_metadata(self.deck["id"], mode="presentation")

    def test_trash_restore_purge_and_empty(self) -> None:
        other = self.fx.make_deck("もう 1 つ", mode="presentation")
        r = self.fx.post(f"/api/decks/{self.deck['id']}/delete")
        self.assertEqual(r.status, 200, r)
        self.assertIn("ごみ箱", r.json()["message"])
        trash = self.fx.get("/api/trash").json()
        self.assertEqual([c["id"] for c in trash["items"]], [self.deck["id"]])
        row = trash["items"][0]
        self.assertTrue(row["capabilities"]["purge"]["available"])
        self.assertIn("reclaimable_size", row)
        self.assertTrue(trash["capabilities"]["purge_all"]["available"])
        self.assertEqual(trash["purge_all_confirm_label"], "1 件すべて完全に削除する")
        # 二重削除・戻せないものは日本語で断る
        r = self.fx.post(f"/api/decks/{self.deck['id']}/delete")
        self.assertEqual(r.status, 404)
        r = self.fx.post(f"/api/decks/{other['id']}/restore")
        self.assertEqual((r.status, r.error_code), (409, "state_conflict"))
        r = self.fx.post(f"/api/decks/{other['id']}/purge")
        self.assertEqual((r.status, r.error_code), (409, "state_conflict"))
        # 戻す
        r = self.fx.post(f"/api/decks/{self.deck['id']}/restore")
        self.assertEqual(r.status, 200, r)
        self.assertEqual(self.fx.get("/api/trash").json()["items"], [])
        # もう一度捨てて完全に削除: 行は 1 つも残らない
        self.fx.post(f"/api/decks/{self.deck['id']}/delete")
        r = self.fx.post(f"/api/decks/{self.deck['id']}/purge")
        self.assertEqual(r.status, 200, r)
        with self.fx.server.store_lock:
            store = self.fx.store
            for table in ("decks", "deck_tags", "deletion_tasks", "launches", "events"):
                col = "id" if table == "decks" else "deck_id"
                n = store.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (self.deck["id"],)).fetchone()[0]
                self.assertEqual(n, 0, table)
            self.assertEqual(store.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertFalse(any(self.fx.root.trash_dir.iterdir()), "退避の置き場も消える")
        # ごみ箱を空にする
        self.fx.post(f"/api/decks/{other['id']}/delete")
        r = self.fx.post("/api/trash/purge")
        self.assertEqual(r.status, 200, r)
        self.assertIn("ごみ箱を空にしました（1 件", r.json()["message"])
        self.assertEqual(self.fx.get("/api/trash").json()["capabilities"]["purge_all"]["available"], False)


class _Crash(BaseException):
    """サーバがそこで止まったことの代わり（後片付けの except Exception に拾われない）。"""


class TrashInterruptedTests(unittest.TestCase):
    """ごみ箱へ移す途中の失敗・停止で、隠れたまま戻せないスライドを作らない。"""

    def setUp(self) -> None:
        self.root = DataRoot(temp_dir(self) / "home")
        self.store = self.open()
        self.deck = make_deck(self.store, "消すスライド")
        self.asset_rel, self.asset_id = self.attach_page_image(self.deck["id"])

    def open(self, **kw) -> Store:
        store = Store.open(self.root, settings=Settings(), **kw)
        self.addCleanup(store.close)
        return store

    def reopen(self, **kw) -> Store:
        self.store.close()
        self.store = self.open(**kw)
        return self.store

    def attach_page_image(self, deck_id: str) -> tuple[str, str]:
        """そのデッキだけが使うページの画像（描いた回のページ）を 1 枚置く。"""
        data = png_bytes()
        with self.store.transaction():
            run = self.store.create_run(deck_id, args={"mode": "presentation"})
            rel = f"assets/decks/{deck_id}/{run['id']}/slide-01.png"
            path = self.root.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            asset = self.store.register_asset(relative_path=rel, media_type="image/png",
                                              sha256=hashlib.sha256(data).hexdigest(), bytes_=len(data))
            self.store.conn.execute("INSERT INTO run_pages(run_id,page_no,state,asset_id) VALUES(?,?,?,?)",
                                    (run["id"], 1, "ready", asset["id"]))
        return rel, asset["id"]

    def assert_restorable_and_restore(self) -> None:
        store = self.store
        self.assertEqual([d["id"] for d in store.trashed_decks()], [self.deck["id"]], "ごみ箱に見えている")
        store.restore_deck_from_trash(self.deck["id"])
        self.assertIsNone(store.deck(self.deck["id"])["deleted_at"])
        self.assertTrue((self.root.root / self.asset_rel).is_file(), "ファイルが元の場所に戻る")
        self.assertEqual(store.asset(self.asset_id)["state"], "ready")
        self.assertEqual(store.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_a_manifest_that_cannot_be_written_leaves_the_deck_untouched(self) -> None:
        with patch.object(management, "_write_json_atomic", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(StateError, "スライドはそのまま"):
                self.store.delete_deck_to_trash(self.deck["id"])
        self.assertIsNone(self.store.deck(self.deck["id"])["deleted_at"], "隠さない")
        self.assertEqual(self.store.deletion_tasks_for_deck(self.deck["id"]), [])
        self.assertTrue((self.root.root / self.asset_rel).is_file())
        self.assertFalse(self.root.trash_dir.exists() and any(self.root.trash_dir.iterdir()), "書きかけを残さない")
        # 直ったら普通に捨てて戻せる
        self.store.delete_deck_to_trash(self.deck["id"])
        self.assert_restorable_and_restore()

    def test_a_refused_hide_cleans_up_the_manifest(self) -> None:
        with patch.object(self.store, "soft_delete_deck", side_effect=StateError("同時に削除された")):
            with self.assertRaises(StateError):
                self.store.delete_deck_to_trash(self.deck["id"])
        self.assertFalse(self.root.trash_dir.exists() and any(self.root.trash_dir.iterdir()))
        self.assertIsNone(self.store.deck(self.deck["id"])["deleted_at"])

    def test_stopping_after_files_moved_then_restarting_can_restore(self) -> None:
        """退避は済んだが記録の前に止まった（タスクは pending のまま）。起こし直すと戻せる。"""
        original = self.store.append_event

        def stop_at_record(kind, payload, **kw):
            if kind == "deck_trashed":
                raise _Crash()
            return original(kind, payload, **kw)

        with patch.object(self.store, "append_event", side_effect=stop_at_record):
            with self.assertRaises(_Crash):
                self.store.delete_deck_to_trash(self.deck["id"])
        self.assertEqual(self.store.deletion_tasks_for_deck(self.deck["id"])[0]["state"], "pending")
        self.assertFalse((self.root.root / self.asset_rel).is_file(), "ファイルは退避の置き場へ動いている")
        with self.assertRaises(StateError):
            self.store.restore_deck_from_trash(self.deck["id"])   # 掃除の前は pending なので断る（動いている途中かもしれない）

        store = self.reopen()
        task = store.deletion_tasks_for_deck(self.deck["id"])[0]
        self.assertEqual(task["state"], "failed", "起動時の掃除が戻せる形にする")
        self.assertEqual(store.asset(self.asset_id)["state"], "trashed", "退避済みの画像は trashed")
        self.assert_restorable_and_restore()

    def test_stopping_before_any_file_moved_then_restarting_can_restore(self) -> None:
        real_replace = management.os.replace

        def stop_before_moving_files(src, dst):
            if str(src).endswith(".png"):
                raise _Crash()
            return real_replace(src, dst)

        with patch.object(management.os, "replace", side_effect=stop_before_moving_files):
            with self.assertRaises(_Crash):
                self.store.delete_deck_to_trash(self.deck["id"])
        self.assertTrue((self.root.root / self.asset_rel).is_file())
        store = self.reopen(startup_sweep=False)
        summary = run_startup_sweep(store)
        self.assertEqual(summary["deletions_recovered"], 1)
        self.assertTrue(summary["ok"])
        self.assertEqual(store.asset(self.asset_id)["state"], "ready", "動いていない画像は ready のまま")
        self.assert_restorable_and_restore()

    def test_a_pending_deletion_without_a_manifest_from_the_old_order_is_restorable(self) -> None:
        """古い版は「隠す → manifest」の順だった。manifest の前に止まった削除も、起こし直せば戻せる。"""
        self.store.soft_delete_deck(self.deck["id"], manifest_path=".trash/never-written/manifest.json")
        store = self.reopen()
        self.assertEqual(store.deletion_tasks_for_deck(self.deck["id"])[0]["state"], "failed")
        self.assert_restorable_and_restore()

    def test_a_missing_manifest_with_moved_files_is_still_refused(self) -> None:
        """退避の置き場にファイルがあるのに manifest が無いなら、何かが欠けている。黙って戻さない。"""
        self.store.delete_deck_to_trash(self.deck["id"])
        task = self.store.deletion_tasks_for_deck(self.deck["id"])[0]
        (self.root.root / task["manifest_path"]).unlink()
        self.store.conn.execute("UPDATE deletion_tasks SET state='failed' WHERE id=?", (task["id"],))
        with self.assertRaisesRegex(StateError, "manifest"):
            self.store.restore_deck_from_trash(self.deck["id"])


class NotReadyRoutesTests(unittest.TestCase):
    """まだ無い口（ページの削除・書き出し P5・v1 の企画の口）は残し、v1 と同じエラーの形で「まだ使えない」と答える。
    成功したふりをしない。P3 で作った直す口は、ページの無いデッキには「ページが無い」と断る。"""

    def setUp(self) -> None:
        self.fx = start_server(self)
        self.deck = self.fx.make_deck()

    def test_every_later_route_answers_not_ready(self) -> None:
        d, s = self.deck["id"], str(uuid.uuid4())
        posts = [f"/api/decks/{d}/pages/{s}/remove", f"/api/decks/{d}/exports",
                 f"/api/decks/{d}/instructions/{s}/generate", f"/api/decks/{d}/plans/{s}/approve",
                 f"/api/decks/{d}/generate"]
        gets = [f"/api/decks/{d}/plan", f"/api/exports/{s}/file"]
        for path in posts:
            r = self.fx.post(path, {"text": "x"})
            self.assertEqual((r.status, r.error_code), (503, "not_ready_in_v2"), path)
            self.assertEqual(set(r.json()["error"]), {"code", "message"}, path)
        for path in gets:
            r = self.fx.get(path)
            self.assertEqual((r.status, r.error_code), (503, "not_ready_in_v2"), path)
        # P3 の直す口は、ページの無いデッキには 409（ページが無い）と断る
        for path, body in ((f"/api/decks/{d}/instructions", {"scope": "page", "slide_id": s, "text": "x"}),
                           (f"/api/decks/{d}/pages/insert", {"after_slide_id": None, "text": "x"}),
                           (f"/api/decks/{d}/pages/reorder", {"slide_ids": []}),
                           (f"/api/decks/{d}/pages/{s}/duplicate", {}),
                           (f"/api/decks/{d}/pages/{s}/visibility", {"hidden": True}),
                           (f"/api/decks/{d}/adopt", {"choices": {s: s}})):
            r = self.fx.post(path, body)
            self.assertEqual((r.status, r.error_code), (409, "no_pages"), path)
        r = self.fx.post(f"/api/decks/{d}/resume", {})
        self.assertEqual((r.status, r.error_code), (409, "nothing_to_resume"))
        # 断ったあとも何も変わっていない
        self.assertEqual(self.fx.get("/api/decks").json()["total"], 1)
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store._scalar("SELECT COUNT(*) FROM jobs"), 0)


if __name__ == "__main__":
    unittest.main()
