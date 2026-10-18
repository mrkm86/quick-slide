"""デザインテンプレートの投入（PR 5: docs/design-template-generation.md §2.1・§2.2）。

名前・ID の解決（既定のブランド・同名複数・別ブランド・削除済み・無い版・版だけ・style_id との併用・使えないモード）、
送り直しの同一判定（確定した内部改訂で決まる）、force_new_deck、競合・途中の失敗での全体のロールバック、
控え（template_snapshots）の中身、テンプレートを使わない投入が今までどおりであること。ワーカーは起こさない。
"""

from __future__ import annotations

import hashlib
import json
import unittest
from unittest import mock

from helpers import MANUSCRIPT, intake_payload, open_store, start_server

from quick_slide import design_templates as dt
from quick_slide.errors import SelectionError
from quick_slide.settings import Settings
from quick_slide.store import Store
from quick_slide.store.design_templates import content_sha256

CONV = "conversation-key-for-the-template-tests-0001"
TABLES = ("decks", "intakes", "runs", "jobs", "launches", "template_snapshots")


def doc(name: str = "社内報告", modes=("presentation", "pitch"), instructions: str = "図は少なめにする"):
    d = dt.new_document("placeholder", "placeholder", name)
    d.update(structure="## 1. 結論\n先に結論を書く", instructions=instructions, modes=list(modes))
    return d


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.store = open_store(self)
        self.brand = self.store.create_brand("試験のブランド", self.config())
        self.bid = self.brand["id"]

    def config(self, primary: str | None = None) -> dict:
        cfg = json.loads(json.dumps(self.store.brand_revision(self.store.preset_brand_revision_id())["config"]))
        if primary:
            cfg["colors"]["primary"] = primary
        return cfg

    def template(self, name: str = "社内報告", brand_id: str | None = None, **kw) -> dict:
        return self.store.create_design_template(brand_id or self.bid, doc(name, **kw))

    def payload(self, *, conv: str | None = CONV, **attrs) -> dict:
        p = intake_payload(**attrs)
        if conv:
            p["origin"]["conversation_key"] = conv
        return p

    def intake(self, **attrs) -> dict:
        return self.store.create_intake(self.payload(**attrs))

    def counts(self) -> dict[str, int]:
        return {t: self.store.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}

    def refused(self, code: str, **attrs) -> SelectionError:
        before = self.counts()
        with self.assertRaises(SelectionError) as cm:
            self.intake(**attrs)
        self.assertEqual(cm.exception.code, code, str(cm.exception))
        self.assertEqual(self.counts(), before, "断ったときは何も作らない")
        return cm.exception

    def snapshot_of(self, run_id: str) -> dict:
        ref = self.store.run(run_id)["args"]["template_snapshot"]
        return self.store.template_snapshot(ref)


class ResolutionTests(Base):
    def test_a_name_resolves_inside_the_default_brand_and_the_run_keeps_a_snapshot_reference(self) -> None:
        t = self.template()
        r = self.intake(design_template="社内報告")
        run = self.store.run(r["run_id"])
        self.assertEqual(set(run["args"]["template_snapshot"]), {"snapshot_id", "snapshot_sha256"})
        snap = self.snapshot_of(r["run_id"])
        self.assertEqual((snap["template"]["id"], snap["template"]["revision"], snap["template"]["revision_id"]),
                         (t["template_id"], 1, t["revision_id"]))
        self.assertEqual(snap["template"]["document"], t["document"], "文書の全文を持つ")
        self.assertEqual(snap["template"]["content_sha256"], content_sha256(t["document"]))
        self.assertEqual((snap["brand"]["id"], snap["brand"]["revision_id"]), (self.bid, self.brand["current_revision_id"]))
        self.assertEqual(snap["mode"], "presentation")
        summary = r["design_template"]
        self.assertEqual((summary["template_id"], summary["version"], summary["name"]), (t["template_id"], 1, "社内報告"))
        self.assertNotIn("document", summary, "応答に本文を載せない")

    def test_the_deck_is_pinned_to_the_snapshots_brand_revision_and_the_default_style(self) -> None:
        self.template()
        r = self.intake(design_template="社内報告")
        deck = self.store.deck(r["deck_id"])
        self.assertEqual(deck["brand_revision_id"], self.brand["current_revision_id"])
        style = self.store.style_revision(deck["style_revision_id"])
        self.assertEqual(style["style_id"], self.store.style_revision(self.store.preset_style_revision_id())["style_id"])

    def test_an_id_resolves_before_a_name(self) -> None:
        a = self.template("別の名前")
        self.template(a["template_id"])   # 名前が別のテンプレートの ID と同じ
        r = self.intake(design_template=a["template_id"])
        self.assertEqual(self.snapshot_of(r["run_id"])["template"]["id"], a["template_id"])

    def test_the_explicit_brand_is_used_instead_of_the_default(self) -> None:
        other = self.store.create_brand("別のブランド", self.config())   # 作った順で、こちらが既定になる
        t = self.template("共通の名前")
        r = self.intake(design_template="共通の名前", brand_id=self.bid)
        self.assertEqual(self.snapshot_of(r["run_id"])["template"]["id"], t["template_id"])
        err = self.refused("template_not_found", design_template="共通の名前")   # 既定は別のブランド。広げて探さない
        self.assertEqual(err.details["brand_id"], other["id"])

    def test_an_invalid_default_brand_stops_instead_of_moving_to_another_brand(self) -> None:
        store = open_store(self, settings=Settings(default_brand_id="00000000-0000-4000-8000-000000000000"))
        brand = store.create_brand("試験のブランド", self.config())
        store.create_design_template(brand["id"], doc())
        with self.assertRaises(SelectionError) as cm:
            store.create_intake(self.payload(design_template="社内報告"))
        self.assertEqual(cm.exception.code, "brand_not_found")

    def test_same_names_stop_with_candidate_ids(self) -> None:
        a, b = self.template("重複"), self.template("重複")
        err = self.refused("template_ambiguous", design_template="重複")
        self.assertEqual(sorted(c["id"] for c in err.details["candidates"]), sorted([a["template_id"], b["template_id"]]))
        r = self.intake(design_template=a["template_id"])   # ID なら決まる
        self.assertEqual(self.snapshot_of(r["run_id"])["template"]["id"], a["template_id"])

    def test_an_id_of_another_brand_asks_for_the_brand(self) -> None:
        other = self.store.create_brand("別のブランド", self.config())   # 既定は、あとから作ったこちら
        t = self.template("社内報告", brand_id=self.bid)
        self.refused("template_other_brand", design_template=t["template_id"])
        self.refused("template_not_found", design_template=t["template_id"], brand_id=other["id"])
        r = self.intake(design_template=t["template_id"], brand_id=self.bid)
        self.assertEqual(self.snapshot_of(r["run_id"])["template"]["id"], t["template_id"])

    def test_a_deleted_template_or_brand_is_refused(self) -> None:
        t = self.template()
        self.store.delete_design_template(t["template_id"], expected_revision_id=t["revision_id"])
        self.refused("template_deleted", design_template=t["template_id"])
        self.refused("template_not_found", design_template="社内報告")
        live = self.template("生きている")
        self.store.delete_brand(self.bid)
        self.refused("brand_not_found", design_template=live["template_id"], brand_id=self.bid)
        # 既定は付属のブランドに移っている。削除済みのブランドの子は、ID でも選べない（ブランドを指定し直すよう言う）
        self.refused("template_other_brand", design_template=live["template_id"])

    def test_versions_the_missing_one_and_the_version_alone(self) -> None:
        t = self.template()
        self.store.save_design_template_for_conversation(
            t["template_id"], doc(instructions="v2 の指示"), expected_revision_id=t["revision_id"], origin_runtime="cli",
            conversation_key=None)
        r1 = self.intake(design_template="社内報告", design_template_version=1)
        self.assertEqual(self.snapshot_of(r1["run_id"])["template"]["revision"], 1)
        r2 = self.intake(design_template="社内報告")   # 省けば受付時の最新
        self.assertEqual(self.snapshot_of(r2["run_id"])["template"]["revision"], 2)
        err = self.refused("template_version_not_found", design_template="社内報告", design_template_version=3)
        self.assertEqual(err.details["latest_version"], 2)
        self.refused("design_template_required", design_template_version=1)
        for bad in (0, -1, True, "1"):
            self.refused("design_template_version_invalid", design_template="社内報告", design_template_version=bad)

    def test_style_id_and_motion_and_unsupported_modes_are_refused(self) -> None:
        self.template("プレゼンだけ", modes=("presentation",))
        style_id = self.store.style_revision(self.store.preset_style_revision_id())["style_id"]
        self.refused("design_template_style_conflict", design_template="プレゼンだけ", style_id=style_id)
        self.refused("template_mode_unsupported", design_template="プレゼンだけ", mode="motion")
        err = self.refused("template_mode_unsupported", design_template="プレゼンだけ", mode="pitch")
        self.assertEqual(err.details["modes"], ["presentation"])
        self.intake(design_template="プレゼンだけ", mode="presentation")

    def test_an_expected_revision_that_changed_is_a_409_and_writes_nothing(self) -> None:
        t = self.template()
        self.store.save_design_template_for_conversation(
            t["template_id"], doc(instructions="見たあとに保存された"), expected_revision_id=t["revision_id"],
            origin_runtime="cli", conversation_key=None, overwrite=True)
        before = self.counts()
        with self.assertRaises(SelectionError) as cm:
            self.intake(design_template="社内報告", design_template_expected_revision_id=t["revision_id"])
        self.assertEqual((cm.exception.code, cm.exception.status), ("template_revision_changed", 409))
        self.assertEqual(self.counts(), before)
        current = self.store.get_design_template(t["template_id"])["revision_id"]
        self.intake(design_template="社内報告", design_template_expected_revision_id=current)

    def test_the_snapshot_uses_the_parents_latest_brand_not_the_one_saved_with_the_template(self) -> None:
        t = self.template()
        newer = self.store.add_brand_revision(self.bid, self.config("#C8102E"))
        self.assertNotEqual(newer["id"], t["brand_revision_id"])
        r = self.intake(design_template="社内報告")
        self.assertEqual(self.store.deck(r["deck_id"])["brand_revision_id"], newer["id"])
        snap = self.snapshot_of(r["run_id"])
        self.assertEqual(snap["brand"]["revision_id"], newer["id"])
        self.assertEqual(snap["brand"]["config"]["colors"]["primary"], "#C8102E")
        # 保存したときのブランドの版は、履歴として残る（投入するブランド版の指定には使わない）
        self.assertEqual(self.store.get_design_template(t["template_id"])["brand_revision_id"], t["brand_revision_id"])

    def test_an_old_version_is_also_taken_with_the_latest_brand(self) -> None:
        t = self.template()
        v2 = self.store.add_design_template_version(t["template_id"], doc(instructions="二版目"), expected_revision_id=t["revision_id"])
        newer = self.store.add_brand_revision(self.bid, self.config("#C8102E"))
        r = self.intake(design_template=t["template_id"], design_template_version=1)
        snap = self.snapshot_of(r["run_id"])
        self.assertEqual((snap["template"]["revision"], snap["template"]["revision_id"]), (1, t["revision_id"]))
        self.assertEqual((snap["brand"]["revision_id"], self.store.deck(r["deck_id"])["brand_revision_id"]), (newer["id"], newer["id"]))
        self.assertNotEqual(v2["brand_revision_id"], newer["id"])
        identity_brand = snap["brand"]["sha256"]
        self.assertEqual(identity_brand, hashlib.sha256(json.dumps({"config": snap["brand"]["config"], "assets": snap["brand"]["assets"]},
                                                                    ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest())

    def test_a_brand_only_update_is_a_new_request_while_the_same_brand_revision_resends(self) -> None:
        self.template()
        a = self.intake(design_template="社内報告")
        self.assertEqual(self.intake(design_template="社内報告")["resolution"], "resent")
        newer = self.store.add_brand_revision(self.bid, self.config("#C8102E"))
        b = self.intake(design_template="社内報告")
        self.assertEqual(b["resolution"], "new")
        self.assertNotEqual(b["deck_id"], a["deck_id"])
        self.assertEqual(self.snapshot_of(b["run_id"])["brand"]["revision_id"], newer["id"])
        self.assertEqual(self.snapshot_of(a["run_id"])["brand"]["revision_id"], self.brand["current_revision_id"], "前の投入の控えは変わらない")
        c = self.intake(design_template="社内報告")
        self.assertEqual((c["resolution"], c["deck_id"]), ("resent", b["deck_id"]))

    def test_a_deleted_parent_brand_is_refused_not_replaced(self) -> None:
        self.template()
        self.store.delete_brand(self.bid)
        before = self.counts()
        with self.assertRaises(SelectionError):
            self.intake(design_template="社内報告", brand_id=self.bid)
        self.assertEqual(self.counts(), before)

    def test_the_snapshot_cannot_be_sent_by_the_client(self) -> None:
        self.template()
        p = self.payload(design_template="社内報告")
        p["deck_attributes"]["template_snapshot"] = {"snapshot_id": "x", "snapshot_sha256": "y"}
        with self.assertRaises(Exception) as cm:
            self.store.create_intake(p)
        self.assertNotIsInstance(cm.exception, SelectionError)


class ResendIdentityTests(Base):
    def test_the_same_content_returns_the_same_deck(self) -> None:
        self.template()
        a = self.intake(design_template="社内報告")
        b = self.intake(design_template="社内報告")
        self.assertEqual((b["resolution"], b["deck_id"]), ("resent", a["deck_id"]))
        self.assertEqual(b["design_template"], a["design_template"])
        self.assertEqual(self.counts()["template_snapshots"], 1)

    def test_an_explicit_version_and_the_latest_that_resolve_to_the_same_revision_are_the_same_request(self) -> None:
        t = self.template()
        a = self.intake(design_template="社内報告")
        b = self.intake(design_template=t["template_id"], design_template_version=1)
        self.assertEqual((b["resolution"], b["deck_id"]), ("resent", a["deck_id"]))

    def test_the_same_version_overwritten_is_a_new_request(self) -> None:
        t = self.template()
        a = self.intake(design_template="社内報告")
        saved = self.store.overwrite_design_template(t["template_id"], doc(instructions="上書きした指示"),
                                                     expected_revision_id=t["revision_id"])
        self.assertEqual(saved["revision"], 1)
        b = self.intake(design_template="社内報告", design_template_version=1)
        self.assertEqual(b["resolution"], "new")
        self.assertNotEqual(b["deck_id"], a["deck_id"])
        self.assertEqual(self.snapshot_of(b["run_id"])["template"]["document"]["instructions"], "上書きした指示")
        self.assertEqual(self.counts()["template_snapshots"], 2)

    def test_a_changed_manuscript_title_or_override_is_a_new_request(self) -> None:
        self.template()
        a = self.intake(design_template="社内報告")
        self.assertEqual(self.intake(design_template="社内報告", style_note="落ち着いた調子")["resolution"], "new")
        self.assertEqual(self.intake(design_template="社内報告", title="別の題")["resolution"], "new")
        self.assertEqual(self.intake(design_template="社内報告")["deck_id"], a["deck_id"])

    def test_a_template_request_and_a_plain_request_are_different(self) -> None:
        self.template()
        a = self.intake()
        b = self.intake(design_template="社内報告")
        self.assertEqual((a["resolution"], b["resolution"]), ("new", "new"))
        self.assertNotIn("template_snapshot", self.store.run(a["run_id"])["args"])

    def test_force_new_deck_always_makes_a_new_deck(self) -> None:
        self.template()
        a = self.intake(design_template="社内報告")
        p = self.payload(design_template="社内報告")
        p["force_new_deck"] = True
        b = self.store.create_intake(p)
        self.assertEqual(b["resolution"], "new")
        self.assertNotEqual(b["deck_id"], a["deck_id"])
        self.assertEqual(self.counts()["template_snapshots"], 1, "同じ内容の控えは 1 行を共有する")

    def test_the_hash_holds_the_resolved_selection_and_no_manuscript_or_conversation_key(self) -> None:
        t = self.template()
        r = self.intake(design_template="社内報告")
        sha = self.store.intake(r["intake_id"])["request_sha256"]
        snap = self.snapshot_of(r["run_id"])
        identity = {"template_id": t["template_id"], "version": 1, "revision_id": t["revision_id"],
                    "template_sha256": content_sha256(t["document"]), "brand_id": self.bid,
                    "brand_revision_id": t["brand_revision_id"], "brand_sha256": snap["brand"]["sha256"],
                    "explicit_overrides": {}}
        attrs = {"mode": "presentation", "style": "standard", "image_look": None, "images": True, "outline_fixed": False,
                 "style_id": None, "style_said": False}
        asked = [MANUSCRIPT, "会議を半分にする", attrs, {"design_template": identity}]
        self.assertEqual(sha, hashlib.sha256(json.dumps(asked, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest())
        payload_json = json.dumps(self.store.intake(r["intake_id"])["payload"], ensure_ascii=False)
        self.assertNotIn(CONV, payload_json)
        self.assertNotIn("template_snapshot", payload_json)


class AtomicityTests(Base):
    def test_a_failure_after_the_snapshot_is_saved_rolls_everything_back(self) -> None:
        self.template()
        before = self.counts()
        with mock.patch.object(Store, "create_job", side_effect=RuntimeError("途中で失敗")):
            with self.assertRaises(RuntimeError):
                self.intake(design_template="社内報告")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.intake(design_template="社内報告")["resolution"], "new", "失敗の後も投入できる")

    def test_the_selection_is_resolved_inside_the_intake_transaction(self) -> None:
        self.template()
        seen = []
        real = Store.resolve_design_template

        def spy(store, attrs):
            seen.append(store.conn.in_transaction)
            return real(store, attrs)

        with mock.patch.object(Store, "resolve_design_template", spy):
            self.intake(design_template="社内報告")
        self.assertEqual(seen, [True])


class PlainIntakeTests(Base):
    def test_a_non_template_intake_is_unchanged(self) -> None:
        r = self.intake()
        self.assertIsNone(r["design_template"])
        run = self.store.run(r["run_id"])
        self.assertNotIn("template_snapshot", run["args"])
        attrs = {"mode": "presentation", "style": "standard", "image_look": None, "images": True, "outline_fixed": False,
                 "style_id": None, "style_said": False}
        asked = [MANUSCRIPT, "会議を半分にする", attrs]
        self.assertEqual(self.store.intake(r["intake_id"])["request_sha256"],
                         hashlib.sha256(json.dumps(asked, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest())
        self.assertEqual(self.counts()["template_snapshots"], 0)
        self.assertEqual(self.intake()["resolution"], "resent")

    def test_style_id_still_works_without_a_template(self) -> None:
        style_id = self.store.style_revision(self.store.preset_style_revision_id())["style_id"]
        r = self.intake(style_id=style_id)
        self.assertNotIn("template_snapshot", self.store.run(r["run_id"])["args"])


class HttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = start_server(self, start_workers=False)
        with self.fx.server.store_lock:
            store = self.fx.store
            cfg = json.loads(json.dumps(store.brand_revision(store.preset_brand_revision_id())["config"]))
            self.brand = store.create_brand("試験のブランド", cfg)
            self.template = store.create_design_template(self.brand["id"], doc())

    def post(self, **attrs):
        return self.fx.post("/api/intakes", intake_payload(**attrs))

    def test_a_template_intake_is_accepted_and_says_generation_is_not_ready(self) -> None:
        r = self.post(design_template="社内報告", design_template_version=1)
        self.assertEqual(r.status, 201, r.body)
        body = r.json()
        self.assertEqual(body["design_template"]["template_id"], self.template["template_id"])
        self.assertIs(body["generation_ready"], False)
        self.assertIn("生成できる状態にはなっていません", body["generation_note"])
        self.assertNotIn("document", json.dumps(body))

    def test_refusals_are_400_with_codes_and_candidates(self) -> None:
        with self.fx.server.store_lock:
            self.fx.store.create_design_template(self.brand["id"], doc("重複"))
            self.fx.store.create_design_template(self.brand["id"], doc("重複"))
        r = self.post(design_template="重複")
        self.assertEqual((r.status, r.error_code), (400, "template_ambiguous"))
        self.assertEqual(len(r.json()["error"]["details"]["candidates"]), 2)
        for attrs, code in (({"design_template": "ない名前"}, "template_not_found"),
                            ({"design_template_version": 1}, "design_template_required"),
                            ({"design_template": "社内報告", "design_template_version": 9}, "template_version_not_found"),
                            ({"design_template": "社内報告", "mode": "motion"}, "template_mode_unsupported"),
                            ({"design_template": "社内報告", "style_id": "x"}, "design_template_style_conflict")):
            r = self.post(**attrs)
            self.assertEqual((r.status, r.error_code), (400, code), attrs)
        r = self.post(design_template="社内報告", design_template_expected_revision_id="old")
        self.assertEqual((r.status, r.error_code), (409, "template_revision_changed"))
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0], 0)

    def test_the_client_cannot_send_a_snapshot(self) -> None:
        r = self.post(design_template="社内報告", template_snapshot={"snapshot_id": "x"})
        self.assertEqual(r.status, 400)


if __name__ == "__main__":
    unittest.main()
