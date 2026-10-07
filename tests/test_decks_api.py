"""デッキの API: ホーム・一覧・ごみ箱・スライドの画面の材料・実況・管理の操作・まだ使えない口。

P1 のデッキはページを持たない。画面が読む項目は全部そろっていて（v1 と同じ形）、
描く・直す・書き出しは `capabilities` で押せないと言い、口を叩けば「まだ使えない」と答える。
"""

from __future__ import annotations

import unittest
import uuid
from urllib.parse import quote

from helpers import start_server

from quick_slide.server.later import NOT_READY_REASON

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
            for key in ("deck_instruction", "page_instruction", "regenerate", "adopt_version", "export", "resume"):
                self.assertEqual(caps[key]["reason"], NOT_READY_REASON, key)

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


class NotReadyRoutesTests(unittest.TestCase):
    """P3 以降の口は残し、v1 と同じエラーの形で「まだ使えない」と答える。成功したふりをしない。"""

    def setUp(self) -> None:
        self.fx = start_server(self)
        self.deck = self.fx.make_deck()

    def test_every_later_route_answers_not_ready(self) -> None:
        d, s = self.deck["id"], str(uuid.uuid4())
        posts = ["/api/conversations/deck", f"/api/decks/{d}/pages/reorder", f"/api/decks/{d}/pages/insert",
                 f"/api/decks/{d}/pages/{s}/duplicate", f"/api/decks/{d}/pages/{s}/remove",
                 f"/api/decks/{d}/pages/{s}/visibility", f"/api/decks/{d}/exports", f"/api/decks/{d}/adopt",
                 f"/api/decks/{d}/instructions", f"/api/decks/{d}/instructions/{s}/generate",
                 f"/api/decks/{d}/plans/{s}/approve", f"/api/decks/{d}/generate", f"/api/decks/{d}/resume"]
        gets = [f"/api/decks/{d}/plan", f"/api/exports/{s}/file"]
        for path in posts:
            r = self.fx.post(path, {"text": "x"})
            self.assertEqual((r.status, r.error_code), (503, "not_ready_in_v2"), path)
            self.assertEqual(set(r.json()["error"]), {"code", "message"}, path)
        for path in gets:
            r = self.fx.get(path)
            self.assertEqual((r.status, r.error_code), (503, "not_ready_in_v2"), path)
        # 断ったあとも何も変わっていない
        self.assertEqual(self.fx.get("/api/decks").json()["total"], 1)


if __name__ == "__main__":
    unittest.main()
