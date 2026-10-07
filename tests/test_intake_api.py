"""P2 の取り込み（`POST /api/intakes`）: 形・モードとスタイルの検査・秘密らしい原稿・まだ受けない項目・作るもの。

ワーカーは起こさない（取り込みの形だけを見る。台本係・エンジンは別の試験）。
"""

from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlsplit

from helpers import capture_logs, intake_payload, start_server

from quick_slide.ids import PRESET_STYLE_INFOGRAPHIC_ID, PRESET_STYLE_STANDARD_ID


class IntakeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = start_server(self, start_workers=False)
        self.logs = capture_logs(self)

    def post(self, payload):
        return self.fx.post("/api/intakes", payload)

    def test_creates_deck_intake_launch_run_and_job(self) -> None:
        r = self.post(intake_payload(mode="pitch", style="infographic", image_look="handdrawn", purpose="試行の提案",
                                     audience="部長会", page_count=6))
        self.assertEqual(r.status, 201, r.body)
        body = r.json()
        for key in ("intake_id", "deck_id", "launch_id", "job_id", "run_id", "url", "deck_title", "mode", "style",
                    "resolution", "appended"):
            self.assertIn(key, body)
        self.assertEqual((body["mode"], body["style"], body["appended"], body["resolution"]), ("pitch", "infographic", False, "new"))
        url = urlsplit(body["url"])
        self.assertEqual(url.path, f"/slides/{body['deck_id']}")
        q = parse_qs(url.query)
        self.assertEqual(q["launch"], [body["launch_id"]])
        self.assertTrue(q["token"][0])
        with self.fx.server.store_lock:
            store = self.fx.store
            deck = store.deck(body["deck_id"])
            self.assertEqual((deck["mode"], deck["title"], deck["purpose"], deck["audience"]),
                             ("pitch", "会議を半分にする", "試行の提案", "部長会"))
            # 会話が言ったスタイルにあたる付属スタイル（P4: infographic には専用の付属スタイルがある）
            self.assertEqual(store.style_revision(deck["style_revision_id"])["style_id"], PRESET_STYLE_INFOGRAPHIC_ID)
            run = store.run(body["run_id"])
            self.assertEqual((run["state"], run["engine"], run["number"]), ("queued", "image-slide", 1))
            self.assertEqual({k: run["args"][k] for k in ("mode", "style", "image_look", "images", "page_count")},
                             {"mode": "pitch", "style": "infographic", "image_look": "handdrawn", "images": True,
                              "page_count": 6})
            self.assertEqual(store.job(body["job_id"])["state"], "queued")
            self.assertTrue(store.deck_working(body["deck_id"]))
            intake = store.intake(body["intake_id"])
            self.assertNotIn("manuscript", intake["payload"], "原稿は payload_json に入れない")
            self.assertEqual(store.launch(body["launch_id"])["deck_id"], body["deck_id"])
        # 画面から見ても「働いている・まだページは無い」
        view = self.fx.get(f"/api/decks/{body['deck_id']}").json()
        self.assertEqual((view["pages"], view["live"]["working"]), ([], True))
        self.assertEqual(view["planning"]["message"], "順番を待っています。前の仕事が終わったら始めます。")
        self.assertTrue(view["planning"]["active"])

    def test_defaults_are_presentation_standard_with_images(self) -> None:
        body = self.post(intake_payload()).json()
        self.assertEqual((body["mode"], body["style"]), ("presentation", "standard"))
        with self.fx.server.store_lock:
            run = self.fx.store.run(body["run_id"])
            deck = self.fx.store.deck(body["deck_id"])
            self.assertEqual(self.fx.store.style_revision(deck["style_revision_id"])["style_id"], PRESET_STYLE_STANDARD_ID)
        self.assertEqual((run["engine"], run["args"]["images"], run["args"]["image_look"]), ("html-slide", True, None))
        body = self.post(intake_payload(title=None)).json()
        self.assertTrue(body["deck_title"].startswith("無題のスライド"))

    def test_mode_and_style_are_checked_per_mode(self) -> None:
        bad = [
            {"mode": "movie"},
            {"mode": "presentation", "style": "infographic"},   # infographic はピッチだけ
            {"mode": "pitch", "style": "fancy"},
            {"image_look": "oil"},
            {"images": "yes"},
            {"page_count": 0},
            {"page_count": 41},
            {"unknown_key": 1},
        ]
        for attrs in bad:
            r = self.post(intake_payload(**attrs))
            self.assertEqual((r.status, r.error_code), (400, "validation_error"), attrs)
        for attrs in ({"mode": "presentation", "style": "visual"}, {"mode": "pitch", "style": "standard"},
                      {"mode": "pitch", "images": False}):
            self.assertEqual(self.post(intake_payload(**attrs)).status, 201, attrs)
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.search_decks()["total"], 3, "断った取り込みは何も作らない")

    def test_secret_like_manuscript_is_refused_without_echoing_it(self) -> None:
        secret = "sk-" + "A" * 30
        payload = intake_payload()
        payload["manuscript"] += f"\n鍵は {secret} です\n"
        r = self.post(payload)
        self.assertEqual((r.status, r.error_code), (400, "secret_like_content"))
        self.assertEqual(r.json()["error"]["details"]["patterns"], ["openai_style_key"])
        self.assertNotIn(secret, r.body.decode("utf-8"))
        self.assertNotIn(secret, self.logs.text())
        r = self.post(intake_payload(purpose="ghp_" + "b" * 36))
        self.assertEqual((r.status, r.error_code), (400, "validation_error"))
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.search_decks()["total"], 0)

    def test_not_ready_fields_are_refused_not_dropped(self) -> None:
        cases = [
            {"append_to_deck_id": "5a1de57d-0000-4b00-8000-00000000ffff"},
            {"transcript_excerpt": "会話の抜粋"},
            {"attachments": [{"path": "/tmp/x.png", "purpose": "reference"}]},
        ]
        for extra in cases:
            r = self.post({**intake_payload(), **extra})
            self.assertEqual((r.status, r.error_code), (400, "not_ready_in_v2"), extra)
            self.assertIn("v2 では", r.json()["error"]["message"])
        r = self.post(intake_payload(format_id="5a1de57d-0000-4b00-8000-00000000ffff"))
        self.assertEqual((r.status, r.error_code), (400, "not_ready_in_v2"))
        self.assertEqual(self.post({**intake_payload(), "force_new_deck": True}).status, 201)

    def test_shape_errors(self) -> None:
        for payload in ({}, {"schema_version": 2}, {**intake_payload(), "schema_version": 2},
                        {**intake_payload(), "manuscript": "   "}, {**intake_payload(), "origin": {"kind": "x", "runtime": "other"}},
                        {**intake_payload(), "origin": {"kind": "conversation", "runtime": "claude_code", "conversation_key": "short"}}):
            r = self.post(payload)
            self.assertEqual((r.status, r.error_code), (400, "validation_error"), payload)

    def test_conversation_key_is_hashed_and_never_stored_raw(self) -> None:
        key = "conversation-key-0123456789"  # gitleaks:allow（試験用のダミーの会話キー）
        payload = intake_payload()
        payload["origin"]["conversation_key"] = key
        body = self.post(payload).json()
        with self.fx.server.store_lock:
            intake = self.fx.store.intake(body["intake_id"])
        self.assertEqual(len(intake["conversation_hash"]), 64)
        self.assertNotIn(key, json.dumps(intake, ensure_ascii=False))
        for blob in self.fx.all_files().values():
            self.assertNotIn(key.encode("utf-8"), blob)
        echoed = intake_payload(title=f"題 {key}")
        echoed["origin"]["conversation_key"] = key
        r = self.post(echoed)
        self.assertEqual((r.status, r.error_code), (400, "conversation_key_echoed"))

    def test_headless_origin_gets_no_launch(self) -> None:
        payload = intake_payload()
        payload["origin"]["kind"] = "headless"
        body = self.post(payload).json()
        self.assertIsNone(body["launch_id"])
        self.assertNotIn("?launch=", body["url"])

    def test_intake_needs_the_session(self) -> None:
        r = self.fx.request("POST", "/api/intakes", intake_payload())
        self.assertEqual((r.status, r.error_code), (403, "session_required"))


if __name__ == "__main__":
    unittest.main()
