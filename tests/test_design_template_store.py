"""デザインテンプレートの保存（store/design_templates.py・0011）。版の番号と不変の改訂の行・競合・同文・会話の分岐・移行。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from helpers import open_store, temp_dir
from legacy_templates import (GOLDEN_ROW_SHA, all_rows, insert_legacy_template, old_content_sha256, stored_row)

from quick_slide import design_templates as dt
from quick_slide.db.connection import connect
from quick_slide.db.migrate import LATEST_VERSION, MIGRATIONS_DIR, load_migrations, migrate, status
from quick_slide.ids import PRESET_BRAND_ID
from quick_slide.errors import ConflictError, NotFoundError, StateError, ValidationError
from quick_slide.store.design_templates import changed_sections, content_sha256


def doc(**kw):
    """画面が送るのと同じ形の文書（id・版・由来はサーバが決めるので、ここでは仮の値）。"""
    d = dt.new_document("placeholder", "placeholder", kw.pop("name", "社内報告"))
    d.update(structure="## 1. 結論\n先に結論を書く", instructions="図は少なめにする")
    d.update(kw)
    return d


def resource(rid="r-1", name="素材集", path="~/materials", usage="必要なものだけ選ぶ"):
    return {"id": rid, "name": name, "kind": "directory", "path": path, "usage": usage}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.store = open_store(self)
        self.brand = self.store.create_brand("試験のブランド", self.preset_config())
        self.bid = self.brand["id"]

    def preset_config(self):
        return json.loads(json.dumps(self.store.brand_revision(self.store.preset_brand_revision_id())["config"]))

    def create(self, **kw):
        return self.store.create_design_template(self.bid, kw.pop("document", None) or doc(), **kw)

    def count_rows(self, table="design_template_revisions") -> int:
        return self.store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class CreateAndReadTests(Base):
    def test_create_makes_v1_with_a_server_id_and_no_origin(self) -> None:
        t = self.create(document=doc(id="client-chosen", revision=9, based_on=3))
        self.assertNotEqual(t["template_id"], "client-chosen")
        self.assertEqual((t["revision"], t["save_sequence"], t["save_kind"]), (1, 1, "create"))
        d = t["document"]
        self.assertEqual((d["id"], d["brand_id"], d["revision"], d["based_on"]), (t["template_id"], self.bid, 1, None))
        self.assertIsNone(t["based_on_revision_id"])
        self.assertEqual(set(d), dt.TOP_KEYS)
        self.assertEqual(t["brand_revision"]["id"], self.brand["current_revision_id"])
        self.assertTrue(t["is_latest"] and t["is_current"])

    def test_an_import_may_keep_a_clean_id_but_never_collide(self) -> None:
        t = self.create(template_id="imported-1")
        self.assertEqual(t["template_id"], "imported-1")
        with self.assertRaises(ConflictError):
            self.create(template_id="imported-1")

    def test_invalid_documents_are_refused_and_nothing_is_written(self) -> None:
        for bad in (doc(name=" "), doc(modes=[]), doc(resources=[resource(path="relative/dir")]), "x", {"schema_version": 3}):
            with self.assertRaises(ValidationError):
                self.create(document=bad)
        self.assertEqual((self.count_rows(), self.count_rows("design_templates")), (0, 0))

    def test_old_schema_documents_are_stored_as_v4(self) -> None:
        old = {"schema_version": 2, "id": "x", "brand_id": "y", "revision": 0, "name": "旧形式", "modes": ["presentation"],
               "description": "", "script": {"structure": "構成の本文", "purpose": "目的"}, "drawing": {}}
        t = self.create(document=old)
        self.assertEqual(t["document"]["schema_version"], 4)
        self.assertNotIn("attachments", t["document"])
        self.assertEqual(t["document"]["structure"], "構成の本文")
        self.assertIn("目的", t["document"]["instructions"])

    def test_the_stored_document_has_no_machine_state(self) -> None:
        t = self.create(origin_runtime="codex", conversation_key="conv-secret-key")
        stored = self.store.conn.execute("SELECT document_json FROM design_template_revisions").fetchone()[0]
        self.assertNotIn(t["revision_id"], stored)
        self.assertNotIn("conv-secret-key", stored)
        self.assertNotIn("conversation", stored)

    def test_the_conversation_key_is_only_stored_as_a_hmac(self) -> None:
        self.create(origin_runtime="claude_code", conversation_key="conv-secret-key")
        row = self.store.conn.execute("SELECT conversation_hash, origin_runtime FROM design_template_revisions").fetchone()
        self.assertEqual(row["conversation_hash"], self.store.conversation_hash_for("claude_code", "conv-secret-key"))
        self.assertEqual(row["origin_runtime"], "claude_code")
        dump = "\n".join(self.store.conn.iterdump())
        self.assertNotIn("conv-secret-key", dump)
        with self.assertRaises(ValidationError):
            self.create(conversation_key="no-runtime")

    def test_saving_under_a_preset_brand_is_allowed(self) -> None:
        t = self.store.create_design_template(PRESET_BRAND_ID, doc())
        self.assertEqual(t["brand_id"], PRESET_BRAND_ID)
        self.assertEqual([x["template_id"] for x in self.store.list_design_templates(PRESET_BRAND_ID)],
                         [t["template_id"]])

    def test_a_missing_brand_is_not_found(self) -> None:
        with self.assertRaises(NotFoundError):
            self.store.create_design_template("no-such-brand", doc())

    def test_get_unknown_template_and_version(self) -> None:
        t = self.create()
        with self.assertRaises(NotFoundError):
            self.store.get_design_template("nope")
        with self.assertRaises(NotFoundError):
            self.store.get_design_template(t["template_id"], revision=7)
        with self.assertRaises(NotFoundError):
            self.store.get_design_template(t["template_id"], revision_id="nope")
        with self.assertRaises(ValidationError):
            self.store.get_design_template(t["template_id"], revision=1, revision_id=t["revision_id"])


class OverwriteTests(Base):
    def test_overwrite_appends_a_row_for_the_same_version_and_never_updates(self) -> None:
        t = self.create()
        before = dict(self.store.conn.execute("SELECT * FROM design_template_revisions").fetchone())
        o = self.store.overwrite_design_template(t["template_id"], doc(instructions="図を増やす"),
                                                 expected_revision_id=t["revision_id"])
        self.assertEqual((o["revision"], o["save_sequence"], o["save_kind"]), (1, 2, "overwrite"))
        self.assertNotEqual(o["revision_id"], t["revision_id"])
        self.assertEqual(self.count_rows(), 2)
        after = dict(self.store.conn.execute("SELECT * FROM design_template_revisions WHERE id=?", (t["revision_id"],)).fetchone())
        self.assertEqual(after, before, "上書き前の行は変わらない")
        # 版番号で引くと最後の保存、内部の改訂 ID で引けば上書き前
        self.assertEqual(self.store.get_design_template(t["template_id"], revision=1)["document"]["instructions"], "図を増やす")
        old = self.store.get_design_template(t["template_id"], revision_id=t["revision_id"])
        self.assertEqual(old["document"]["instructions"], "図は少なめにする")
        self.assertFalse(old["is_current"])
        self.assertEqual(self.store.get_design_template(t["template_id"])["revision_id"], o["revision_id"])

    def test_the_stored_rows_cannot_be_changed_or_removed(self) -> None:
        self.create()
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.conn.execute("UPDATE design_template_revisions SET save_kind='copy'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.conn.execute("DELETE FROM design_template_revisions")

    def test_overwrite_keeps_the_origin_of_the_version(self) -> None:
        t = self.create()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="v2"), expected_revision_id=t["revision_id"])
        o = self.store.overwrite_design_template(t["template_id"], doc(instructions="v2 直し"), expected_revision_id=v2["revision_id"])
        self.assertEqual((o["document"]["based_on"], o["based_on_revision_id"]), (1, t["revision_id"]))

    def test_overwrite_records_the_brand_revision_at_save_time(self) -> None:
        t = self.create()
        config = self.preset_config()
        config["footer"]["text"] = "新しいフッタ"
        b2 = self.store.add_brand_revision(self.bid, config)
        o = self.store.overwrite_design_template(t["template_id"], doc(instructions="変更"), expected_revision_id=t["revision_id"])
        self.assertEqual(o["brand_revision"]["id"], b2["id"])
        self.assertEqual(self.store.get_design_template(t["template_id"], revision_id=t["revision_id"])["brand_revision"]["id"],
                         self.brand["current_revision_id"], "前の行が控えたブランドの版は変わらない")


class NewVersionTests(Base):
    def test_a_new_version_is_max_plus_one_and_records_the_source(self) -> None:
        t = self.create()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="v2"), expected_revision_id=t["revision_id"])
        self.assertEqual((v2["revision"], v2["save_kind"]), (2, "new_version"))
        self.assertEqual((v2["document"]["based_on"], v2["based_on_revision_id"]), (1, t["revision_id"]))
        v3 = self.store.add_design_template_version(t["template_id"], doc(instructions="v3"), expected_revision_id=v2["revision_id"])
        self.assertEqual((v3["revision"], v3["document"]["based_on"]), (3, 2))
        self.assertEqual(self.store.get_design_template(t["template_id"], revision=1)["document"]["instructions"], "図は少なめにする")

    def test_the_version_number_keeps_growing_after_overwrites(self) -> None:
        t = self.create()
        o = self.store.overwrite_design_template(t["template_id"], doc(instructions="a"), expected_revision_id=t["revision_id"])
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="b"), expected_revision_id=o["revision_id"])
        self.assertEqual(v2["revision"], 2)
        self.assertEqual(v2["document"]["based_on"], 1)

    def test_a_client_cannot_forge_the_origin(self) -> None:
        t = self.create()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="v2", based_on=99, revision=50),
                                                    expected_revision_id=t["revision_id"])
        self.assertEqual((v2["revision"], v2["document"]["based_on"]), (2, 1))

    def test_a_source_from_another_template_is_refused(self) -> None:
        a, b = self.create(), self.create()
        with self.assertRaises(NotFoundError):
            self.store.add_design_template_version(a["template_id"], doc(instructions="x"),
                                                   expected_revision_id=a["revision_id"], source_revision_id=b["revision_id"])
        self.assertEqual(self.count_rows(), 2)

    def test_copying_an_older_version_makes_the_next_version_and_leaves_latest_alone(self) -> None:
        t = self.create()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="v2"), expected_revision_id=t["revision_id"])
        c = self.store.copy_design_template_version(t["template_id"], t["revision_id"], expected_revision_id=v2["revision_id"])
        self.assertEqual((c["revision"], c["save_kind"], c["document"]["based_on"]), (3, "copy", 1))
        self.assertEqual(c["based_on_revision_id"], t["revision_id"])
        self.assertEqual(c["document"]["instructions"], "図は少なめにする")
        self.assertEqual(self.store.get_design_template(t["template_id"], revision=2)["document"]["instructions"], "v2")
        self.assertEqual(self.count_rows(), 3)

    def test_a_copy_may_carry_edits(self) -> None:
        t = self.create()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="v2"), expected_revision_id=t["revision_id"])
        c = self.store.copy_design_template_version(t["template_id"], t["revision_id"], expected_revision_id=v2["revision_id"],
                                                    document=doc(instructions="v1 を直した"))
        self.assertEqual((c["revision"], c["document"]["based_on"], c["document"]["instructions"]), (3, 1, "v1 を直した"))

    def test_copying_with_a_stale_expectation_conflicts(self) -> None:
        t = self.create()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="v2"), expected_revision_id=t["revision_id"])
        with self.assertRaises(ConflictError):
            self.store.copy_design_template_version(t["template_id"], t["revision_id"], expected_revision_id=t["revision_id"])
        self.assertEqual(self.store.get_design_template(t["template_id"])["revision_id"], v2["revision_id"])


class DuplicateDeleteListTests(Base):
    def test_duplicate_is_a_separate_template_without_history(self) -> None:
        t = self.create()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="v2", resources=[resource()]),
                                                    expected_revision_id=t["revision_id"])
        c = self.store.duplicate_design_template(t["template_id"], expected_revision_id=v2["revision_id"])
        self.assertNotEqual(c["template_id"], t["template_id"])
        self.assertEqual((c["revision"], c["save_kind"], c["based_on_revision_id"]), (1, "duplicate", None))
        self.assertEqual((c["document"]["name"], c["document"]["based_on"], c["document"]["id"]),
                         ("社内報告のコピー", None, c["template_id"]))
        self.assertEqual(c["document"]["instructions"], "v2")
        self.assertEqual(c["document"]["resources"][0]["path"], "~/materials")
        self.assertEqual(len(self.store.design_template_revisions(c["template_id"])), 1)
        self.assertEqual(len(self.store.design_template_revisions(t["template_id"])), 2, "元は変わらない")

    def test_duplicate_takes_an_optional_name(self) -> None:
        t = self.create()
        c = self.store.duplicate_design_template(t["template_id"], expected_revision_id=t["revision_id"], name="別の名前")
        self.assertEqual(c["document"]["name"], "別の名前")
        with self.assertRaises(ValidationError):
            self.store.duplicate_design_template(t["template_id"], expected_revision_id=t["revision_id"], name="  ")

    def test_duplicate_conflicts_on_a_stale_expectation(self) -> None:
        t = self.create()
        self.store.overwrite_design_template(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"])
        with self.assertRaises(ConflictError):
            self.store.duplicate_design_template(t["template_id"], expected_revision_id=t["revision_id"])

    def test_soft_delete_hides_the_template_but_keeps_every_row(self) -> None:
        t = self.create()
        o = self.store.overwrite_design_template(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"])
        self.store.delete_design_template(t["template_id"], expected_revision_id=o["revision_id"])
        self.assertEqual(self.store.list_design_templates(self.bid), [])
        self.assertEqual(self.count_rows(), 2)
        got = self.store.get_design_template(t["template_id"])
        self.assertIsNotNone(got["deleted_at"])
        self.assertEqual(len(self.store.design_template_revisions(t["template_id"])), 1)
        # 削除済みには保存・複製できない。再削除は何もしない
        for call in (lambda: self.store.overwrite_design_template(t["template_id"], doc(instructions="y"), expected_revision_id=o["revision_id"]),
                     lambda: self.store.add_design_template_version(t["template_id"], doc(instructions="y"), expected_revision_id=o["revision_id"]),
                     lambda: self.store.duplicate_design_template(t["template_id"], expected_revision_id=o["revision_id"])):
            with self.assertRaises(StateError):
                call()
        self.store.delete_design_template(t["template_id"], expected_revision_id=o["revision_id"])

    def test_delete_conflicts_on_a_stale_expectation(self) -> None:
        t = self.create()
        self.store.overwrite_design_template(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"])
        with self.assertRaises(ConflictError):
            self.store.delete_design_template(t["template_id"], expected_revision_id=t["revision_id"])
        self.assertEqual(len(self.store.list_design_templates(self.bid)), 1)

    def test_list_is_by_brand_newest_first_and_excludes_deleted_templates_and_brands(self) -> None:
        from quick_slide.clock import FixedClock

        clock = FixedClock("2026-10-01T00:00:00.000000Z")
        self.store.clock = clock
        other = self.store.create_brand("別のブランド", self.preset_config())
        a = self.create(document=doc(name="A"))
        clock.advance(1)
        b = self.create(document=doc(name="B"))
        clock.advance(1)
        self.create(document=doc(name="C"))
        o = self.store.create_design_template(other["id"], doc(name="他"))
        self.store.delete_design_template(b["template_id"], expected_revision_id=b["revision_id"])
        clock.advance(1)
        self.store.overwrite_design_template(a["template_id"], doc(name="A2"), expected_revision_id=a["revision_id"])
        names = [x["name"] for x in self.store.list_design_templates(self.bid)]
        self.assertEqual(names, ["A2", "C"])
        first = self.store.list_design_templates(self.bid)[0]
        self.assertEqual((first["revision"], first["modes"], first["brand_id"]), (1, ["presentation"], self.bid))
        self.assertEqual([x["name"] for x in self.store.list_design_templates(other["id"])], ["他"])
        self.store.delete_brand(other["id"])
        self.assertEqual(self.store.list_design_templates(other["id"]), [])
        self.assertEqual(self.store.get_design_template(o["template_id"])["document"]["name"], "他", "履歴は読める")

    def test_equal_timestamps_are_ordered_by_creation_newest_first(self) -> None:
        from quick_slide.clock import FixedClock

        self.store.clock = FixedClock("2026-10-01T00:00:00.000000Z")
        for name in ("A", "B", "C"):
            self.create(document=doc(name=name))
        self.assertEqual([x["name"] for x in self.store.list_design_templates(self.bid)], ["C", "B", "A"])

    def test_saving_into_a_deleted_brand_is_refused(self) -> None:
        t = self.create()
        self.store.delete_brand(self.bid)
        with self.assertRaises(StateError):
            self.create()
        for call in (lambda: self.store.overwrite_design_template(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"]),
                     lambda: self.store.add_design_template_version(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"]),
                     lambda: self.store.duplicate_design_template(t["template_id"], expected_revision_id=t["revision_id"])):
            with self.assertRaises(StateError):
                call()
        self.assertEqual(self.count_rows(), 1)


class RevisionListTests(Base):
    def test_the_list_is_newest_first_with_saved_time_origin_and_changed_sections(self) -> None:
        from quick_slide.clock import FixedClock

        clock = FixedClock("2026-10-01T00:00:00.000000Z")
        self.store.clock = clock
        t = self.create(document=doc(resources=[resource()]))
        clock.advance(60)
        o = self.store.overwrite_design_template(t["template_id"], doc(resources=[resource()], instructions="v1 直し"),
                                                 expected_revision_id=t["revision_id"])
        clock.advance(60)
        v2 = self.store.add_design_template_version(
            t["template_id"], doc(name="改名", structure="新しい構成", instructions="v1 直し", resources=[resource(usage="変えた")]),
            expected_revision_id=o["revision_id"])
        clock.advance(60)
        v3 = self.store.copy_design_template_version(t["template_id"], t["revision_id"], expected_revision_id=v2["revision_id"])
        rows = self.store.design_template_revisions(t["template_id"])
        self.assertEqual([r["revision"] for r in rows], [3, 2, 1])
        r1, r2, r3 = rows[2], rows[1], rows[0]
        self.assertEqual(r1["changed"], [], "最初の版は比べる相手が無い")
        self.assertEqual((r1["revision_id"], r1["save_count"], r1["based_on"]), (o["revision_id"], 2, None))
        self.assertEqual((r1["created_at"], r1["saved_at"]), ("2026-10-01T00:00:00.000000Z", "2026-10-01T00:01:00.000000Z"))
        self.assertEqual(r1["document"]["instructions"], "v1 直し")
        self.assertEqual(r2["changed"], ["基本", "構成", "リソース"])
        self.assertEqual((r2["based_on"], r2["based_on_revision_id"]), (1, o["revision_id"]))
        self.assertEqual(r2["saved_at"], "2026-10-01T00:02:00.000000Z")
        self.assertEqual(r3["changed"], ["基本", "構成", "追加指示", "リソース"], "前の版（v2）との差（コピー元は上書き前の行）")
        self.assertEqual((r3["based_on"], r3["save_kind"], r3["is_latest"], r3["is_current"]), (1, "copy", True, True))
        self.assertEqual(r3["revision_id"], v3["revision_id"])
        self.assertFalse(r1["is_latest"] or r2["is_latest"])

    def test_section_labels_are_exact(self) -> None:
        base = doc(resources=[resource()])
        self.assertEqual(changed_sections(base, doc(resources=[resource()], name="b")), ["基本"])
        self.assertEqual(changed_sections(base, doc(resources=[resource()], structure="b")), ["構成"])
        self.assertEqual(changed_sections(base, doc(resources=[resource()], instructions="b")), ["追加指示"])
        self.assertEqual(changed_sections(base, doc(resources=[])), ["リソース"])
        self.assertEqual(changed_sections(base, doc(resources=[resource()], modes=["presentation", "pitch"])), ["基本"])
        self.assertEqual(changed_sections(None, base), [])


class UnchangedTests(Base):
    def test_the_same_content_makes_nothing_in_every_mode(self) -> None:
        t = self.create(document=doc(resources=[resource()]))
        same = doc(resources=[resource()])
        for call in (lambda: self.store.overwrite_design_template(t["template_id"], same, expected_revision_id=t["revision_id"]),
                     lambda: self.store.add_design_template_version(t["template_id"], same, expected_revision_id=t["revision_id"]),
                     lambda: self.store.save_design_template_for_conversation(
                         t["template_id"], same, expected_revision_id=t["revision_id"], origin_runtime="codex", conversation_key="c1")):
            r = call()
            self.assertTrue(r["unchanged"])
            self.assertEqual(r["revision_id"], t["revision_id"])
        self.assertEqual(self.count_rows(), 1)
        self.assertEqual(self.store.conn.execute("SELECT updated_at FROM design_templates").fetchone()[0],
                         self.store.get_design_template(t["template_id"])["saved_at"])

    def test_an_unchanged_save_still_checks_the_expected_revision(self) -> None:
        t = self.create()
        o = self.store.overwrite_design_template(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"])
        with self.assertRaises(ConflictError):
            self.store.overwrite_design_template(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"])
        self.assertTrue(self.store.overwrite_design_template(t["template_id"], doc(instructions="x"),
                                                             expected_revision_id=o["revision_id"])["unchanged"])

    def test_machine_ids_order_and_url_do_not_count_as_changes(self) -> None:
        a = doc(modes=["presentation", "pitch"], resources=[resource("a", "一"), resource("b", "二", path="~/two")])
        b = doc(modes=["pitch", "presentation"], resources=[resource("x9", "二", path="~/two"), resource("y8", "一")])
        self.assertEqual(content_sha256(dt.validate({**a, "id": "q"})), content_sha256(dt.validate({**b, "id": "q"})))
        t = self.create(document=a)
        self.assertTrue(self.store.overwrite_design_template(t["template_id"], b, expected_revision_id=t["revision_id"])["unchanged"])

    def test_a_markdown_round_trip_counts_as_unchanged(self) -> None:
        t = self.create(document=doc(description="用途", resources=[resource("original-id")]))
        md = dt.to_markdown(t["document"])
        back = dt.from_markdown(md, brand_id=self.bid, template_id=t["template_id"])
        self.assertNotEqual(back["resources"][0]["id"], "original-id")
        self.assertTrue(self.store.overwrite_design_template(t["template_id"], back, expected_revision_id=t["revision_id"])["unchanged"])

    def test_whitespace_is_not_normalized_away(self) -> None:
        t = self.create()
        r = self.store.overwrite_design_template(t["template_id"], doc(instructions="図は少なめにする "), expected_revision_id=t["revision_id"])
        self.assertFalse(r["unchanged"])

    def test_a_document_with_an_attachments_field_is_not_a_current_document(self) -> None:
        att = {"id": "one", "name": "画像", "file": "a.png", "url": "/x/one", "image": True, "usage": "使う", "sha256": "a" * 64}
        with self.assertRaises(ValidationError):
            self.create(document=doc(attachments=[att]))
        self.assertEqual(self.count_rows(), 0)


class LegacyRowTests(Base):
    """保存形式3（添付の専用欄）の行は書き換えず、読むときだけ作業用の v4 にする。無変更の判定は新しい形で前後を比べる。"""

    def legacy(self, kind: str = "empty") -> dict:
        return insert_legacy_template(self.store, self.bid, kind)

    def test_reading_converts_without_touching_the_stored_bytes_or_hash(self) -> None:
        for kind in ("empty", "full"):
            with self.subTest(kind=kind):
                t = self.legacy(kind)
                before = all_rows(self.store)
                view = self.store.get_design_template(t["template_id"])
                self.store.list_design_templates(self.bid)
                revisions = self.store.design_template_revisions(t["template_id"])
                self.assertEqual(all_rows(self.store), before, "読むだけでは行を書き換えない")
                self.assertEqual(stored_row(self.store, t["revision_id"]), (t["document_json"], t["content_sha256"]))
                self.assertEqual(json.loads(t["document_json"])["schema_version"], 3)
                for doc_ in (view["document"], revisions[0]["document"]):
                    self.assertEqual((doc_["schema_version"], "attachments" in doc_), (4, False))
                names = [r["name"] for r in view["document"]["resources"]]
                self.assertEqual(names, ["画像集"] + (["旧添付"] if kind == "full" else []))

    def test_the_old_stored_hash_is_the_old_algorithm_and_is_never_recomputed(self) -> None:
        for kind in ("empty", "full"):
            t = self.legacy(kind)
            self.assertEqual(t["content_sha256"], GOLDEN_ROW_SHA[kind])
            effective = self.store.get_design_template(t["template_id"])["document"]
            self.assertNotEqual(content_sha256(effective), t["content_sha256"], "新しい算法の値は旧い行のハッシュと違う")
            self.assertEqual(stored_row(self.store, t["revision_id"])[1], GOLDEN_ROW_SHA[kind])

    def test_a_same_content_save_makes_no_row_even_though_the_stored_hash_differs(self) -> None:
        for kind in ("empty", "full"):
            with self.subTest(kind=kind):
                t = self.legacy(kind)
                effective = self.store.get_design_template(t["template_id"])["document"]
                before = all_rows(self.store)
                for call in (lambda: self.store.overwrite_design_template(t["template_id"], effective, expected_revision_id=t["revision_id"]),
                             lambda: self.store.add_design_template_version(t["template_id"], effective, expected_revision_id=t["revision_id"])):
                    self.assertTrue(call()["unchanged"])
                # 旧形式のまま（添付の欄つき）で送り返す古い画面も、同じ中身なら何も作らない
                self.assertTrue(self.store.overwrite_design_template(t["template_id"], json.loads(t["document_json"]),
                                                                     expected_revision_id=t["revision_id"])["unchanged"])
                self.assertEqual(all_rows(self.store), before)

    def test_a_real_edit_appends_a_new_row_in_the_new_form_and_keeps_the_old_one(self) -> None:
        for kind in ("empty", "full"):
            with self.subTest(kind=kind):
                t = self.legacy(kind)
                effective = self.store.get_design_template(t["template_id"])["document"]
                before = all_rows(self.store)
                saved = self.store.overwrite_design_template(
                    t["template_id"], {**effective, "instructions": "直した指示"}, expected_revision_id=t["revision_id"])
                self.assertFalse(saved["unchanged"])
                after = all_rows(self.store)
                self.assertEqual(after[:len(before)], before, "旧い行は1バイトも変わらない")
                self.assertEqual(len(after), len(before) + 1)
                new_raw, new_sha = stored_row(self.store, saved["revision_id"])
                self.assertEqual(json.loads(new_raw)["schema_version"], 4)
                self.assertNotIn("attachments", json.loads(new_raw))
                self.assertEqual(new_sha, content_sha256(saved["document"]))
                self.assertEqual(saved["document"]["resources"], effective["resources"], "移したリソースは落ちない・改名されない")
                self.assertEqual(changed_sections(self.store.get_design_template(t["template_id"], revision_id=t["revision_id"])["document"],
                                                  saved["document"]), ["追加指示"])

    def test_an_unresolved_path_from_an_old_attachment_may_stay_but_a_new_relative_path_is_refused(self) -> None:
        t = self.legacy("full")
        effective = self.store.get_design_template(t["template_id"])["document"]
        added = {**effective, "resources": effective["resources"] + [resource("new-1", "新しい", "relative-new.md")]}
        with self.assertRaises(ValidationError):
            self.store.overwrite_design_template(t["template_id"], added, expected_revision_id=t["revision_id"])
        fixed = json.loads(json.dumps(effective))
        fixed["resources"][-1]["path"] = "~/Documents/memo.md"   # 利用者がパスを指定し直す
        saved = self.store.overwrite_design_template(t["template_id"], fixed, expected_revision_id=t["revision_id"])
        self.assertEqual(saved["document"]["resources"][-1]["path"], "~/Documents/memo.md")

    def test_copy_duplicate_and_the_version_list_work_on_old_rows(self) -> None:
        t = self.legacy("full")
        v2 = self.store.add_design_template_version(
            t["template_id"], {**self.store.get_design_template(t["template_id"])["document"], "instructions": "二版目"},
            expected_revision_id=t["revision_id"])
        copied = self.store.copy_design_template_version(t["template_id"], t["revision_id"], expected_revision_id=v2["revision_id"])
        self.assertEqual(copied["revision"], 3)
        self.assertEqual(copied["document"]["resources"][-1]["name"], "旧添付")
        dup = self.store.duplicate_design_template(t["template_id"], expected_revision_id=copied["revision_id"])
        self.assertEqual([r["name"] for r in dup["document"]["resources"]], ["画像集", "旧添付"])
        self.assertEqual(stored_row(self.store, t["revision_id"]), (t["document_json"], t["content_sha256"]))
        for row in self.store.design_template_revisions(t["template_id"]):
            self.assertNotIn("attachments", row["document"])


class ConflictTests(Base):
    def test_an_overwrite_from_a_stale_screen_conflicts_even_for_the_same_version(self) -> None:
        t = self.create()
        self.store.overwrite_design_template(t["template_id"], doc(instructions="先に保存された"), expected_revision_id=t["revision_id"])
        for call in (lambda: self.store.overwrite_design_template(t["template_id"], doc(instructions="遅れた"), expected_revision_id=t["revision_id"]),
                     lambda: self.store.add_design_template_version(t["template_id"], doc(instructions="遅れた"), expected_revision_id=t["revision_id"])):
            with self.assertRaises(ConflictError):
                call()
        self.assertEqual(self.count_rows(), 2)
        self.assertEqual(self.store.get_design_template(t["template_id"])["document"]["instructions"], "先に保存された")

    def test_a_missing_expectation_is_refused(self) -> None:
        t = self.create()
        for bad in (None, ""):
            with self.assertRaises(ValidationError):
                self.store.overwrite_design_template(t["template_id"], doc(instructions="x"), expected_revision_id=bad)

    def test_unknown_template_is_not_found(self) -> None:
        with self.assertRaises(NotFoundError):
            self.store.overwrite_design_template("nope", doc(), expected_revision_id="x")


class ConversationTests(Base):
    def save(self, t, text, key="conv-1", runtime="codex", **kw):
        return self.store.save_design_template_for_conversation(
            t["template_id"], doc(instructions=text), expected_revision_id=t["revision_id"],
            origin_runtime=runtime, conversation_key=key, **kw)

    def test_the_default_is_a_new_version(self) -> None:
        t = self.create()
        r = self.save(t, "会話から")
        self.assertEqual((r["revision"], r["mode"], r["save_kind"]), (2, "new_version", "new_version"))

    def test_the_same_conversation_overwrites_the_version_it_made(self) -> None:
        t = self.create()
        v2 = self.save(t, "一度目")
        v2b = self.save(v2, "二度目")
        self.assertEqual((v2b["revision"], v2b["mode"], v2b["save_kind"]), (2, "overwrite", "overwrite"))
        v2c = self.save(v2b, "三度目")
        self.assertEqual((v2c["revision"], v2c["mode"]), (2, "overwrite"))
        self.assertEqual(self.count_rows(), 4)
        self.assertEqual([r["revision"] for r in self.store.design_template_revisions(t["template_id"])], [2, 1])

    def test_someone_else_saving_in_between_forces_a_new_version(self) -> None:
        t = self.create()
        v2 = self.save(t, "一度目")
        gui = self.store.overwrite_design_template(t["template_id"], doc(instructions="画面で直した"), expected_revision_id=v2["revision_id"])
        again = self.save(gui, "同じ会話の続き")
        self.assertEqual((again["revision"], again["mode"]), (3, "new_version"))

    def test_another_conversation_forces_a_new_version(self) -> None:
        t = self.create()
        v2 = self.save(t, "一度目", key="conv-1")
        other = self.save(v2, "別の会話", key="conv-2")
        self.assertEqual((other["revision"], other["mode"]), (3, "new_version"))
        same_runtime_other = self.save(other, "同じ鍵で別の実行環境", key="conv-2", runtime="claude_code")
        self.assertEqual(same_runtime_other["mode"], "new_version")

    def test_an_old_version_made_by_the_conversation_is_not_overwritten(self) -> None:
        t = self.create()
        v2 = self.save(t, "会話の版")
        gui = self.store.add_design_template_version(t["template_id"], doc(instructions="画面の新版"), expected_revision_id=v2["revision_id"])
        again = self.save(gui, "同じ会話")
        self.assertEqual((again["revision"], again["mode"]), (4, "new_version"))
        self.assertEqual(self.store.get_design_template(t["template_id"], revision=2)["document"]["instructions"], "会話の版")

    def test_no_conversation_key_means_a_new_version(self) -> None:
        t = self.create()
        v2 = self.save(t, "一度目")
        none = self.store.save_design_template_for_conversation(
            t["template_id"], doc(instructions="識別子なし"), expected_revision_id=v2["revision_id"],
            origin_runtime="codex", conversation_key=None)
        self.assertEqual((none["revision"], none["mode"]), (3, "new_version"))
        self.assertIsNone(self.store.conn.execute("SELECT conversation_hash FROM design_template_revisions WHERE id=?",
                                                  (none["revision_id"],)).fetchone()[0])

    def test_a_version_the_conversation_made_by_create_can_be_overwritten(self) -> None:
        t = self.create(origin_runtime="codex", conversation_key="conv-1")
        r = self.save(t, "作った会話の続き")
        self.assertEqual((r["revision"], r["mode"]), (1, "overwrite"))

    def test_explicit_overwrite_targets_the_latest_and_still_checks_conflicts(self) -> None:
        t = self.create()
        r = self.save(t, "明示", overwrite=True)
        self.assertEqual((r["revision"], r["mode"]), (1, "overwrite"))
        with self.assertRaises(ConflictError):
            self.save(t, "古い期待", overwrite=True)

    def test_a_conflicting_conversation_save_writes_nothing(self) -> None:
        t = self.create()
        self.store.overwrite_design_template(t["template_id"], doc(instructions="先"), expected_revision_id=t["revision_id"])
        with self.assertRaises(ConflictError):
            self.save(t, "遅れ")
        self.assertEqual(self.count_rows(), 2)


    def test_a_version_edited_by_someone_else_never_regains_auto_overwrite(self) -> None:
        t = self.create()
        v2 = self.save(t, "A が作る")
        gui = self.store.overwrite_design_template(t["template_id"], doc(instructions="画面で直す"), expected_revision_id=v2["revision_id"])
        explicit = self.save(gui, "A が明示で上書き", overwrite=True)
        self.assertEqual((explicit["revision"], explicit["mode"]), (2, "overwrite"))
        again = self.save(explicit, "A の通常の保存")
        self.assertEqual((again["revision"], again["mode"]), (3, "new_version"))

    def test_an_anonymous_save_in_the_version_also_removes_the_eligibility(self) -> None:
        t = self.create()
        v2 = self.save(t, "A が作る")
        anon = self.store.save_design_template_for_conversation(
            t["template_id"], doc(instructions="識別子なし"), expected_revision_id=v2["revision_id"],
            origin_runtime="codex", conversation_key=None, overwrite=True)
        again = self.save(anon, "A")
        self.assertEqual(again["mode"], "new_version")


class RollbackTests(Base):
    def test_a_failure_after_the_insert_leaves_no_rows(self) -> None:
        t = self.create()
        with mock.patch.object(type(self.store), "append_event", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.store.add_design_template_version(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"])
            with self.assertRaises(RuntimeError):
                self.store.overwrite_design_template(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"])
            with self.assertRaises(RuntimeError):
                self.create()
            with self.assertRaises(RuntimeError):
                self.store.duplicate_design_template(t["template_id"], expected_revision_id=t["revision_id"])
            with self.assertRaises(RuntimeError):
                self.store.delete_design_template(t["template_id"], expected_revision_id=t["revision_id"])
        self.assertFalse(self.store.conn.in_transaction)
        self.assertEqual((self.count_rows(), self.count_rows("design_templates")), (1, 1))
        self.assertIsNone(self.store.get_design_template(t["template_id"])["deleted_at"])
        self.assertEqual(self.store.get_design_template(t["template_id"])["revision_id"], t["revision_id"])
        # 失敗のあとも、番号は欠けずに続けられる
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"])
        self.assertEqual((v2["revision"], v2["save_sequence"]), (2, 2))

    def test_the_foreign_keys_hold(self) -> None:
        t = self.create()
        self.store.add_design_template_version(t["template_id"], doc(instructions="x"), expected_revision_id=t["revision_id"])
        self.assertEqual(self.store.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaises(sqlite3.IntegrityError):   # 由来は同じテンプレートの改訂だけ
            other = self.create()
            with self.store.transaction():
                self.store.conn.execute(
                    "INSERT INTO design_template_revisions(id,template_id,revision,save_sequence,document_json,content_sha256,"
                    "based_on_revision_id,brand_revision_id,save_kind,created_at) VALUES('z',?,9,9,'{}',?,?,?,'copy','t')",
                    (other["template_id"], "0" * 64, t["revision_id"], self.brand["current_revision_id"]))


class ConcurrencyAndNumberingTests(Base):
    def second_store(self):
        from quick_slide.store import Store

        s = Store.open(self.store.root, settings=self.store.settings, startup_sweep=False)
        self.addCleanup(s.close)
        return s

    def test_two_connections_with_the_same_expectation_let_exactly_one_win(self) -> None:
        t = self.create()
        other = self.second_store()
        won = self.store.overwrite_design_template(t["template_id"], doc(instructions="先"), expected_revision_id=t["revision_id"])
        with self.assertRaises(ConflictError):
            other.add_design_template_version(t["template_id"], doc(instructions="後"), expected_revision_id=t["revision_id"])
        self.assertEqual(self.count_rows(), 2)
        self.assertEqual(other.get_design_template(t["template_id"])["revision_id"], won["revision_id"])

    def test_a_save_holding_the_write_transaction_forces_the_other_into_the_conflict_path(self) -> None:
        """A は保存の書き込みの途中（BEGIN IMMEDIATE のあと・改訂を足す所）で止める。B が同じ期待で同じ保存を始める。
        競合の検査が保存のトランザクションの中にあるなら、B は A が確定するまで書き込みの錠で待たされ、A の確定のあとで
        検査して ConflictError になる。検査がトランザクションの外にあれば、B は A の止まっている間に検査を通り、
        A のあとに自分も書いて2件とも成功する（下の assert が落ちる）。"""
        import threading

        t = self.create()
        a_store, b_store = self.second_store(), self.second_store()
        entered, release, b_started = threading.Event(), threading.Event(), threading.Event()
        original = a_store._insert_revision

        def paused(*args, **kwargs):
            entered.set()                          # A はここで書き込みの錠を持っている
            self.assertTrue(release.wait(30), "B の開始を待つ")
            return original(*args, **kwargs)

        a_store._insert_revision = paused
        results: dict[str, object] = {}

        def save(name, s):
            try:
                if name == "B":
                    self.assertTrue(entered.wait(30))
                    b_started.set()
                results[name] = s.add_design_template_version(
                    t["template_id"], doc(instructions=f"保存 {name}"), expected_revision_id=t["revision_id"])
            except Exception as exc:  # noqa: BLE001
                results[name] = exc

        ta = threading.Thread(target=save, args=("A", a_store))
        tb = threading.Thread(target=save, args=("B", b_store))
        ta.start()
        tb.start()
        self.assertTrue(b_started.wait(30))
        tb.join(0.5)
        self.assertTrue(tb.is_alive(), "B は A の確定まで錠で待たされ、検査も通れない")
        self.assertNotIn("B", results)
        release.set()
        ta.join(30)
        tb.join(30)
        self.assertEqual(results["A"]["revision"], 2)
        self.assertIsInstance(results["B"], ConflictError)
        self.assertEqual(self.count_rows(), 2)
        self.assertEqual([r["revision"] for r in self.store.design_template_revisions(t["template_id"])], [2, 1])

    def test_the_next_version_is_max_plus_one_not_current_plus_one(self) -> None:
        """current が低い版の行を指していても（高い版の行が履歴にある）、次の版は履歴の MAX+1。"""
        t = self.create()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="v2"), expected_revision_id=t["revision_id"])
        v3 = self.store.add_design_template_version(t["template_id"], doc(instructions="v3"), expected_revision_id=v2["revision_id"])
        self.store.conn.execute("UPDATE design_templates SET current_revision_id=? WHERE id=?", (t["revision_id"], t["template_id"]))
        nxt = self.store.add_design_template_version(t["template_id"], doc(instructions="次"), expected_revision_id=t["revision_id"])
        self.assertEqual(nxt["revision"], 4, "current+1（=2）でも件数+1（=4 が偶然合う）でもなく MAX+1")
        self.assertEqual(v3["revision"], 3)
        # 件数と MAX が離れる履歴（欠番）でも MAX+1
        conn = self.store.conn
        conn.execute("DROP TRIGGER design_template_revisions_no_update")
        conn.execute("UPDATE design_template_revisions SET revision=9 WHERE id=?", (nxt["revision_id"],))
        conn.execute("UPDATE design_templates SET current_revision_id=? WHERE id=?", (v2["revision_id"], t["template_id"]))
        after = self.store.add_design_template_version(t["template_id"], doc(instructions="さらに次"), expected_revision_id=v2["revision_id"])
        self.assertEqual(after["revision"], 10)

    def test_the_next_save_sequence_is_max_plus_one_with_gaps(self) -> None:
        t = self.create()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="v2"), expected_revision_id=t["revision_id"])
        conn = self.store.conn
        conn.execute("DROP TRIGGER design_template_revisions_no_update")
        conn.execute("UPDATE design_template_revisions SET save_sequence=20 WHERE id=?", (v2["revision_id"],))
        conn.execute("UPDATE design_templates SET current_revision_id=? WHERE id=?", (t["revision_id"], t["template_id"]))
        o = self.store.overwrite_design_template(t["template_id"], doc(instructions="v1 直し"), expected_revision_id=t["revision_id"])
        self.assertEqual(o["save_sequence"], 21, "件数+1（=3）でも current の次（=2）でもなく MAX+1")
        n = self.store.add_design_template_version(t["template_id"], doc(instructions="次"), expected_revision_id=o["revision_id"])
        self.assertEqual((n["save_sequence"], n["revision"]), (22, 3))


class MigrationTests(unittest.TestCase):
    def test_an_empty_db_gets_the_two_tables(self) -> None:
        conn = connect(temp_dir(self) / "x.db")
        self.addCleanup(conn.close)
        st = migrate(conn)
        self.assertTrue(st.up_to_date)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual({"design_templates", "design_template_revisions"}, names)
        self.assertEqual(len(load_migrations()), LATEST_VERSION)
        self.assertEqual(load_migrations()[10].name, "design_templates")

    def test_an_existing_db_is_upgraded_keeping_its_rows_and_rerun_is_a_no_op(self) -> None:
        tmp = temp_dir(self)
        old = tmp / "m10"
        old.mkdir()
        for m in load_migrations():
            if m.version <= 10:
                shutil.copyfile(m.path, old / m.path.name)
        conn = connect(tmp / "q.db")
        self.addCleanup(conn.close)
        migrate(conn, directory=old)
        t = "2026-10-01T00:00:00.000000Z"
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("INSERT INTO brands(id,name,current_revision_id,kind,created_at,updated_at) VALUES('b1','既存','br1','user',?,?)", (t, t))
        conn.execute("INSERT INTO brand_revisions(id,brand_id,revision,config_json,created_at) VALUES('br1','b1',1,'{}',?)", (t,))
        conn.execute("COMMIT")
        before = [dict(r) for r in conn.execute("SELECT * FROM brands")]
        st = migrate(conn, db_path=tmp / "q.db", backup_dir=tmp / "backups")
        self.assertTrue(st.up_to_date)
        self.assertEqual([dict(r) for r in conn.execute("SELECT * FROM brands")], before)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM design_templates").fetchone()[0], 0, "テンプレートは自動で作らない")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        again = migrate(conn)
        self.assertEqual(again.user_version, LATEST_VERSION)

    def test_a_failing_migration_rolls_back_with_no_partial_tables(self) -> None:
        tmp = temp_dir(self)
        directory = tmp / "migrations"
        shutil.copytree(MIGRATIONS_DIR, directory, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
        sql = (directory / "0011_design_templates.sql").read_text(encoding="utf-8")
        (directory / "0011_design_templates.sql").write_text(sql + "\nCREATE TABLE broken(;\n", encoding="utf-8")
        conn = connect(tmp / "x.db")
        self.addCleanup(conn.close)
        with self.assertRaises(Exception):
            migrate(conn, directory=directory)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 10)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        self.assertFalse({"design_templates", "design_template_revisions", "broken"} & names)
        self.assertFalse(conn.in_transaction)

    def test_the_applied_history_is_recorded(self) -> None:
        store = open_store(self)
        self.assertEqual(status(store.conn).applied[10]["name"], "design_templates")


if __name__ == "__main__":
    unittest.main()
