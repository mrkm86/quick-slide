"""デザインテンプレートの控え（PR 5: docs/design-template-generation.md §2.2・§2.3）。

受け付けたあとにテンプレートを上書き・削除し、ブランドを更新しても、look・直し・差し込み・再開の回は元の控えだけを使うこと
（ハッシュで比べる）。デッキの 2 つの改訂 ID が動かず、`follow_deck_settings` を通らないこと。rebuild・前の版への切り替えを
理由つきで断ること。素材の準備がつながるまで、台本を書く前に理由つきで止まること。テンプレートを使わない回は今までどおり。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unittest
from unittest import mock

from helpers import intake_payload, start_server, wait_until_idle
from test_template_intake import Base, doc

from quick_slide import palette
from quick_slide.errors import StateError
from quick_slide.render import look as LOOK
from quick_slide.scriptdoc import screen_pages
from quick_slide.server.deck_routes import SCREEN_LABELS
from quick_slide.server.page_view import edit_capabilities
from quick_slide.store import Store
from quick_slide.store.edits import TEMPLATE_REBUILD_REASON, TEMPLATE_RESTORE_REASON
from quick_slide.worker.pipeline import TEMPLATE_PREPARATION_PENDING_MESSAGE

RED = "#C8102E"


def fingerprint(store, run_id: str) -> dict:
    """回の look を決め直し、元の文書・ブランド・テーマ・ロゴ・字体のハッシュを返す。"""
    run = store.run(run_id)
    update, record = LOOK.resolve(store, store.root, run, previous_style=None)
    theme = (store.root.run_settings_dir(run["deck_id"], run_id) / "theme.yaml").read_bytes()
    return {"template": record["template"], "brand": record["brand"], "theme_sha256": record["theme_sha256"],
            "theme_file_sha256": hashlib.sha256(theme).hexdigest(), "theme": record["theme"], "logo": record["logo"],
            "font": record["font"], "engine_style": record["engine_style"], "footer": record["footer"],
            "revisions": (update["look"]["brand_revision_id"], update["look"]["style_revision_id"])}


class SnapshotSurvivesChangesTests(Base):
    def accept(self, **attrs):
        self.tpl = self.template()
        self.first = self.intake(design_template="社内報告", **attrs)
        self.deck_id = self.first["deck_id"]
        self.deck_before = self.store.deck(self.deck_id)
        self.before = fingerprint(self.store, self.first["run_id"])

    def change_everything(self) -> None:
        """テンプレートの上書き・ブランドの更新（色）・テンプレートの削除。"""
        saved = self.store.overwrite_design_template(self.tpl["template_id"], doc(instructions="受付のあとに上書き"),
                                                     expected_revision_id=self.tpl["revision_id"])
        self.store.add_brand_revision(self.bid, self.config(RED))
        self.store.delete_design_template(self.tpl["template_id"], expected_revision_id=saved["revision_id"])

    def assert_unchanged(self, run_id: str) -> None:
        self.assertEqual(fingerprint(self.store, run_id), self.before)
        deck = self.store.deck(self.deck_id)
        self.assertEqual((deck["brand_revision_id"], deck["style_revision_id"]),
                         (self.deck_before["brand_revision_id"], self.deck_before["style_revision_id"]))

    def test_the_look_is_fixed_from_the_snapshot_and_records_it(self) -> None:
        self.accept()
        t = self.before["template"]
        self.assertEqual((t["template_id"], t["version"], t["revision_id"], t["followed_latest"]),
                         (self.tpl["template_id"], 1, self.tpl["revision_id"], False))
        self.assertEqual(self.before["brand"]["revision_id"], self.tpl["brand_revision_id"])
        self.assertEqual(self.before["revisions"], (self.tpl["brand_revision_id"], self.deck_before["style_revision_id"]))

    def test_after_overwrite_brand_update_and_delete_the_first_run_still_resolves_the_same(self) -> None:
        self.accept()
        self.change_everything()
        self.assert_unchanged(self.first["run_id"])

    def test_follow_deck_settings_is_never_called_for_a_template_run(self) -> None:
        self.accept()
        self.change_everything()
        with mock.patch.object(Store, "follow_deck_settings", side_effect=AssertionError("追随してはいけない")) as follow:
            fingerprint(self.store, self.first["run_id"])
        follow.assert_not_called()

    def test_render_and_insert_edits_inherit_the_snapshot(self) -> None:
        self.accept()
        self.change_everything()
        ref = self.store.run(self.first["run_id"])["args"]["template_snapshot"]
        edits = [self.store.create_edit(self.deck_id, {"op": "render", "source": "screen"}),
                 self.store.create_edit(self.deck_id, {"op": "insert", "after": None, "text": "1 枚足す", "attachments": [],
                                                       "source": "screen"})]
        for made in edits:
            self.assertEqual(self.store.run(made["run"]["id"])["args"]["template_snapshot"], ref)
            self.assert_unchanged(made["run"]["id"])
        later = self.store.create_edit(self.deck_id, {"op": "render", "source": "screen"})   # 直しの直しも同じ
        self.assertEqual(self.store.run(later["run"]["id"])["args"]["template_snapshot"], ref)
        self.assert_unchanged(later["run"]["id"])

    def test_a_resumed_request_run_keeps_the_first_snapshot(self) -> None:
        self.tpl = self.template()
        payload = self.payload(design_template="社内報告")
        payload.pop("manuscript")
        payload["request"] = "架空の勉強会の案内を作る"
        payload["origin"]["kind"] = "request"
        first = self.store.create_intake(payload)
        self.deck_id = first["deck_id"]
        self.deck_before = self.store.deck(self.deck_id)
        self.before = fingerprint(self.store, first["run_id"])
        self.change_everything()
        retried = self.store.retry_request_intake(self.deck_id)
        ref = self.store.run(first["run_id"])["args"]["template_snapshot"]
        self.assertEqual(self.store.run(retried["run"]["id"])["args"]["template_snapshot"], ref)
        self.assert_unchanged(retried["run"]["id"])

    def test_the_snapshot_row_is_immutable_and_a_tampered_reference_is_refused(self) -> None:
        self.accept()
        ref = self.store.run(self.first["run_id"])["args"]["template_snapshot"]
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.conn.execute("UPDATE template_snapshots SET snapshot_json='{}' WHERE id=?", (ref["snapshot_id"],))
        with self.assertRaises(StateError):
            self.store.template_snapshot({**ref, "snapshot_sha256": "0" * 64})
        with self.assertRaises(StateError):
            self.store.template_snapshot({"snapshot_id": "missing", "snapshot_sha256": ref["snapshot_sha256"]})

    def test_a_failure_while_writing_the_files_can_be_recovered_from_the_snapshot(self) -> None:
        self.accept()
        self.change_everything()
        with mock.patch.object(palette, "theme_yaml", side_effect=OSError("書き込めない")):
            with self.assertRaises(OSError):
                LOOK.resolve(self.store, self.store.root, self.store.run(self.first["run_id"]), previous_style=None)
        self.assert_unchanged(self.first["run_id"])   # DB の控えから、もう一度同じ姿に決められる

    def test_the_same_template_accepted_later_gets_the_new_state_while_the_old_deck_stays(self) -> None:
        self.accept()
        self.change_everything()
        # 削除済みなので選べない。別のテンプレートで新しい資料を作ると、新しい控えを使う（古い資料は動かない）
        fresh = self.template("新しい社内報告")
        newer = self.intake(design_template=fresh["template_id"])
        self.assertNotEqual(fingerprint(self.store, newer["run_id"])["brand"]["revision_id"], self.before["brand"]["revision_id"])
        self.assert_unchanged(self.first["run_id"])


class PlainRunsUnchangedTests(Base):
    def test_a_run_without_a_template_still_follows_the_latest_brand(self) -> None:
        first = self.intake()
        before = fingerprint_plain(self.store, first["run_id"])
        newer = self.store.add_brand_revision(self.bid, self.config(RED))
        after = fingerprint_plain(self.store, first["run_id"])
        self.assertNotEqual(before["theme_sha256"], after["theme_sha256"])
        self.assertEqual(after["brand_revision_id"], newer["id"])
        self.assertEqual(self.store.deck(first["deck_id"])["brand_revision_id"], newer["id"])
        self.assertNotIn("template", after)

    def test_rebuild_and_axes_changes_still_work_for_a_plain_deck(self) -> None:
        first = self.intake()
        made = self.store.create_edit(first["deck_id"], {"op": "rebuild", "source": "screen", "changed": []})
        self.assertNotIn("template_snapshot", self.store.run(made["run"]["id"])["args"])
        self.store.change_deck_axes(first["deck_id"], brand_revision_id=self.store.preset_brand_revision_id())


def fingerprint_plain(store, run_id: str) -> dict:
    run = store.run(run_id)
    update, record = LOOK.resolve(store, store.root, run, previous_style=None)
    return {"theme_sha256": record["theme_sha256"], "brand_revision_id": update["look"]["brand_revision_id"],
            **({"template": record["template"]} if "template" in record else {})}


class RefusalTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.template()
        self.deck_id = self.intake(design_template="社内報告")["deck_id"]

    def test_rebuild_and_restore_edits_are_refused_with_a_reason(self) -> None:
        before = self.store.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        with self.assertRaises(StateError) as cm:
            self.store.create_edit(self.deck_id, {"op": "rebuild", "source": "screen", "changed": []})
        self.assertEqual(str(cm.exception), TEMPLATE_REBUILD_REASON)
        with self.assertRaises(StateError) as cm:
            self.store.create_edit(self.deck_id, {"op": "restore", "page": "p", "run": "r", "source": "screen",
                                                  "positions": [1]})
        self.assertEqual(str(cm.exception), TEMPLATE_RESTORE_REASON)
        with self.assertRaises(StateError):
            self.store.change_deck_axes(self.deck_id, brand_revision_id=self.store.preset_brand_revision_id())
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], before, "回を作らない")

    def test_the_screen_capabilities_say_why(self) -> None:
        row = self.store.deck(self.deck_id)
        args = {"mode": "presentation", "template_snapshot": {"snapshot_id": "x", "snapshot_sha256": "y"}}
        caps = edit_capabilities(row, [{"hidden": False}], args, labels=SCREEN_LABELS)
        for key, reason in (("rebuild", TEMPLATE_REBUILD_REASON), ("adopt_version", TEMPLATE_RESTORE_REASON)):
            self.assertEqual((caps[key]["available"], caps[key]["reason"]), (False, reason))
        plain = edit_capabilities(row, [{"hidden": False}], {"mode": "presentation"}, labels=SCREEN_LABELS)
        self.assertTrue(plain["rebuild"]["available"] and plain["adopt_version"]["available"])


class ServerTests(unittest.TestCase):
    """本物のワーカー（台本係・エンジンは「呼ばれたら落ちる」偽物のまま）で、台本を書く前に止まることを見る。"""

    def setUp(self) -> None:
        self.fx = start_server(self)
        with self.fx.server.store_lock:
            store = self.fx.store
            cfg = json.loads(json.dumps(store.brand_revision(store.preset_brand_revision_id())["config"]))
            self.brand = store.create_brand("試験のブランド", cfg)
            self.template = store.create_design_template(self.brand["id"], doc("プレゼン用", modes=("presentation",)))

    def test_a_template_run_stops_before_writing_the_script_with_the_reason(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(design_template="プレゼン用"))
        self.assertEqual(r.status, 201, r.body)
        body = r.json()
        run = wait_until_idle(self.fx, body["deck_id"])
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "template_preparation"))
        self.assertEqual(run["message"], TEMPLATE_PREPARATION_PENDING_MESSAGE)
        self.assertIn("素材の準備がまだ使えない", run["message"])
        with self.fx.server.store_lock:
            store = self.fx.store
            self.assertIsNone(store.head_script(body["deck_id"]), "台本を書いていない")
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0], 0)
            record = store.run(run["id"])["record"]
            self.assertEqual(record["look"]["template"]["template_id"], self.template["template_id"])
            self.assertFalse(record["look"]["template"]["followed_latest"])
            self.assertTrue(record["look"]["theme_sha256"])
            self.assertEqual(record["template_stop"], {"reason": "preparation_unavailable"})
            self.assertNotIn("script", record)
        view = self.fx.get(f"/api/decks/{body['deck_id']}").json()
        self.assertEqual(view["pages"], [])
        self.assertEqual((view["planning"]["state"], view["planning"]["message"]), ("failed", TEMPLATE_PREPARATION_PENDING_MESSAGE))
        self.assertFalse(view["capabilities"]["resume"]["available"])

    def test_rebuild_and_adopt_routes_refuse_a_template_deck(self) -> None:
        body = self.fx.post("/api/intakes", intake_payload(design_template="プレゼン用")).json()
        wait_until_idle(self.fx, body["deck_id"])
        r = self.fx.post(f"/api/decks/{body['deck_id']}/rebuild", {})
        self.assertEqual((r.status, r.error_code), (409, "template_rebuild_unsupported"))
        self.assertEqual(r.json()["error"]["message"], TEMPLATE_REBUILD_REASON)
        r = self.fx.post(f"/api/decks/{body['deck_id']}/adopt", {"choices": {"a": "b"}})
        self.assertEqual((r.status, r.error_code), (409, "template_restore_unsupported"))

    def test_a_writer_edit_of_a_template_deck_stops_instead_of_writing_without_the_template(self) -> None:
        body = self.fx.post("/api/intakes", intake_payload(design_template="プレゼン用")).json()
        first = wait_until_idle(self.fx, body["deck_id"])
        with self.fx.server.store_lock:
            store = self.fx.store
            data = {"title": "試験", "slides": [{"id": "a", "type": "content", "title": "一枚目", "body": "本文"}]}
            script = store.save_script(body["deck_id"], data, written_by="screen", writer_backend=None, intake_id=None,
                                       run_id=None)
            store.set_script_check(script["id"], 0, [])
            store.accept_script(script["id"], screen_pages(first["args"], data))
            made = store.create_edit(body["deck_id"], {"op": "insert", "after": None, "text": "1 枚足す", "attachments": [],
                                                       "source": "screen", "can_generate": False})
        self.fx.server.wake_worker()
        run = wait_until_idle(self.fx, body["deck_id"])
        self.assertEqual(run["id"], made["run"]["id"])
        self.assertEqual((run["state"], run["failure_kind"], run["message"]),
                         ("failed", "template_preparation", TEMPLATE_PREPARATION_PENDING_MESSAGE))
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.script(self.fx.store.head_script(body["deck_id"])["id"])["version"], 1,
                             "台本の版を進めていない")

    def test_a_request_intake_with_a_template_can_be_resumed_and_still_stops(self) -> None:
        p = intake_payload(design_template="プレゼン用")
        p.pop("manuscript")
        p["request"] = "架空の勉強会の案内を作る"
        p["origin"]["kind"] = "request"
        body = self.fx.post("/api/intakes", p).json()
        run = wait_until_idle(self.fx, body["deck_id"])
        self.assertEqual(run["failure_kind"], "template_preparation")
        r = self.fx.post(f"/api/decks/{body['deck_id']}/resume", {})
        self.assertEqual(r.status, 200, r.body)
        run2 = wait_until_idle(self.fx, body["deck_id"])
        self.assertNotEqual(run2["id"], run["id"])
        self.assertEqual((run2["failure_kind"], run2["args"]["template_snapshot"]),
                         ("template_preparation", run["args"]["template_snapshot"]))


if __name__ == "__main__":
    unittest.main()
