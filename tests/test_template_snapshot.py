"""デザインテンプレートの控え（PR 5: docs/design-template-generation.md §2.2・§2.3）。

受け付けたあとにテンプレートを上書き・削除し、ブランドを更新しても、look・直し・差し込み・再開の回は元の控えだけを使うこと
（ハッシュで比べる）。デッキの 2 つの改訂 ID が動かず、`follow_deck_settings` を通らないこと。rebuild・前の版への切り替えを
理由つきで断ること。素材の準備がつながるまで、台本を書く前に理由つきで止まること。テンプレートを使わない回は今までどおり。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import unittest
from unittest import mock

from helpers import fake_tools, intake_payload, start_server, wait_until_idle
from test_template_intake import Base, doc

from quick_slide import design_templates as dt
from quick_slide.errors import StateError
from quick_slide.render import look as LOOK
from quick_slide.scriptdoc import screen_pages
from quick_slide.server.deck_routes import SCREEN_LABELS
from quick_slide.server.page_view import edit_capabilities
from quick_slide.store import Store
from quick_slide.store.edits import TEMPLATE_REBUILD_REASON, TEMPLATE_RESTORE_REASON, TEMPLATE_RESUME_REASON
from quick_slide.settings import Settings
from quick_slide.worker.pipeline import TEMPLATE_RENDER_PENDING_MESSAGE, Context, make
from quick_slide.worker.tools import Tools

RED = "#C8102E"


def fingerprint(store, run_id: str) -> dict:
    """回の look を決め直し、元の文書・ブランド・テーマ・ロゴ・字体のハッシュを返す。"""
    run = store.run(run_id)
    update, record = LOOK.resolve(store, store.root, run, previous_style=None)
    theme = (store.root.run_settings_dir(run["deck_id"], run_id) / "theme.yaml").read_bytes()
    return {"template": record["template"], "brand": record["brand"], "theme_sha256": record["theme_sha256"],
            "theme_file_sha256": hashlib.sha256(theme).hexdigest(), "theme": record["theme"], "logo": record["logo"],
            "font": record["font"], "engine_style": record["engine_style"], "footer": record["footer"],
            "compilation": {k: v for k, v in record["compilation"].items() if k not in ("dir", "style_file")},
            "style_compiled": record["compilation"]["style_file"] is not None, "revisions": (update["look"]["brand_revision_id"], update["look"]["style_revision_id"])}


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

    def compilation_dir(self, run_id: str):
        return self.store.root.run_compilation_dir(self.deck_id, run_id)

    def test_the_compilation_outputs_are_stored_in_the_run_directory_and_recorded(self) -> None:
        self.accept()
        out = self.compilation_dir(self.first["run_id"])
        names = {p.name for p in out.iterdir()}
        self.assertEqual(names, {"template.json", "brand.json", "writer.md", "preparation.md", "theme.yaml",
                                 "engine-input.json", "compilation.json"})   # style.yaml はピッチだけ
        meta = json.loads((out / "compilation.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["compiler_version"], dt.COMPILER_VERSION)
        self.assertFalse(meta["render_ready"] or meta["generation_executed"])
        run = self.store.run(self.first["run_id"])
        LOOK.resolve(self.store, self.store.root, run, previous_style=None)
        for name, sha in meta["files"].items():
            self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), sha, name)
        record = self.before["compilation"]
        self.assertEqual((record["compiler_version"], record["files"]), (dt.COMPILER_VERSION, meta["files"]))
        self.assertFalse(self.before["style_compiled"])
        # 回の実効のテーマは、コンパイルしたテーマ（字体の行を足す前）から始まる
        theme = (self.store.root.run_settings_dir(self.deck_id, self.first["run_id"]) / "theme.yaml").read_text(encoding="utf-8")
        self.assertTrue(theme.startswith((out / "theme.yaml").read_text(encoding="utf-8")))

    def test_a_pitch_run_also_compiles_the_style_file(self) -> None:
        self.tpl = self.template("ピッチ用")
        r = self.intake(design_template="ピッチ用", mode="pitch")
        self.deck_id = r["deck_id"]
        fp = fingerprint(self.store, r["run_id"])
        self.assertTrue(fp["style_compiled"])
        self.assertTrue(self.compilation_dir(r["run_id"]).joinpath("style.yaml").is_file())

    def test_a_failure_while_compiling_can_be_recovered_from_the_snapshot(self) -> None:
        self.accept()
        self.change_everything()
        made = self.store.create_edit(self.deck_id, {"op": "render", "source": "screen"})["run"]["id"]
        shutil.rmtree(self.compilation_dir(self.first["run_id"]), ignore_errors=True)   # 前の回の写しが無い（使い回せない）状態
        with mock.patch.object(dt, "compile_document", side_effect=OSError("書き込めない")):
            with self.assertRaises(OSError):
                LOOK.resolve(self.store, self.store.root, self.store.run(made), previous_style=None)
        self.assertFalse(self.compilation_dir(made).exists())
        self.assert_unchanged(made)   # DB の控えから、もう一度同じ姿に決められる
        # ファイルが欠けても、控えから作り直せる（元のテンプレートは上書き・削除済み）
        self.assert_unchanged(made)
        shutil.rmtree(self.compilation_dir(self.first["run_id"]), ignore_errors=True)
        shutil.rmtree(self.compilation_dir(made), ignore_errors=True)
        self.assert_unchanged(self.first["run_id"])
        self.assertTrue(self.compilation_dir(self.first["run_id"]).joinpath("writer.md").is_file())

    def test_a_compiler_version_mismatch_stops_unless_a_completed_compilation_exists(self) -> None:
        self.accept()
        made = self.store.create_edit(self.deck_id, {"op": "render", "source": "screen"})["run"]["id"]
        with mock.patch.object(dt, "COMPILER_VERSION", dt.COMPILER_VERSION + 1):
            self.assert_unchanged(made)   # 完了済みのコンパイルを使い回す（新しい版で解釈し直さない）
            shutil.rmtree(self.compilation_dir(self.first["run_id"]), ignore_errors=True)
            shutil.rmtree(self.compilation_dir(made), ignore_errors=True)
            with self.assertRaises(StateError) as cm:
                LOOK.resolve(self.store, self.store.root, self.store.run(made), previous_style=None)
            self.assertIn("コンパイラの版", str(cm.exception))

    def test_a_reused_compilation_missing_a_required_file_is_recompiled_not_trusted(self) -> None:
        self.accept()
        for lost in ("writer.md", "theme.yaml", "preparation.md", "engine-input.json"):
            with self.subTest(lost=lost):
                self.assert_unchanged(self.first["run_id"])   # 前の回の写しを（欠けていれば）作り直して揃える
                made = self.store.create_edit(self.deck_id, {"op": "render", "source": "screen"})["run"]["id"]
                (self.compilation_dir(self.first["run_id"]) / lost).unlink()   # 一覧（compilation.json）には残ったまま
                self.assert_unchanged(made)   # 落ちずに、控えから作り直して同じ姿になる
                self.assertTrue((self.compilation_dir(made) / lost).is_file())
                # 作り直せない状況（コンパイラの版が違う）では、欠けた写しを受け入れずに止める
                other = self.store.create_edit(self.deck_id, {"op": "render", "source": "screen"})["run"]["id"]
                shutil.rmtree(self.compilation_dir(made))
                with mock.patch.object(dt, "COMPILER_VERSION", dt.COMPILER_VERSION + 1):
                    with self.assertRaises(StateError):
                        LOOK.resolve(self.store, self.store.root, self.store.run(other), previous_style=None)

    def test_a_tampered_snapshot_stops_the_run_instead_of_using_it(self) -> None:
        self.accept()
        ref = self.store.run(self.first["run_id"])["args"]["template_snapshot"]
        self.store.conn.execute("DROP TRIGGER template_snapshots_no_update")
        body = self.store.conn.execute("SELECT snapshot_json FROM template_snapshots WHERE id=?",
                                       (ref["snapshot_id"],)).fetchone()[0]
        self.store.conn.execute("UPDATE template_snapshots SET snapshot_json=? WHERE id=?",
                                (body.replace("図は少なめにする", "書き換えた指示"), ref["snapshot_id"]))
        with self.assertRaises(StateError):
            LOOK.resolve(self.store, self.store.root, self.store.run(self.first["run_id"]), previous_style=None)
        ctx = Context(self.store, threading.RLock(), self.store.root, Settings(), Tools(), threading.Event())
        self.assertEqual(make(ctx, self.store.job(self.first["job_id"])), "failed")
        run = self.store.run(self.first["run_id"])
        self.assertEqual(run["failure_kind"], "template_snapshot")
        self.assertIn("受け付けたときと違っています", run["message"])
        self.assertIsNone(self.store.head_script(self.deck_id))

    def test_the_same_template_accepted_later_gets_the_new_state_while_the_old_deck_stays(self) -> None:
        self.accept()
        self.change_everything()
        # 削除済みなので選べない。別のテンプレートで新しい資料を作ると、新しい控えを使う（古い資料は動かない）
        fresh = self.template("新しい社内報告")
        newer = self.intake(design_template=fresh["template_id"])
        self.assertNotEqual(fingerprint(self.store, newer["run_id"])["brand"]["revision_id"], self.before["brand"]["revision_id"])
        self.assert_unchanged(self.first["run_id"])


class MigrationBackfillTests(unittest.TestCase):
    """0013 より前（版 12）に受け付けたテンプレートのデッキは、移行でデッキの列へ控えが写り、拒否も複製も効く。"""

    def seed_v12(self):
        from helpers import temp_dir
        from quick_slide.db.migrate import load_migrations, user_version
        from quick_slide.db.migrate import migrate as run_migrate
        from quick_slide.paths import DataRoot

        tmp = temp_dir(self)
        old = tmp / "m12"
        old.mkdir()
        for m in load_migrations():
            if m.version <= 12:
                shutil.copyfile(m.path, old / m.path.name)
        root = DataRoot(tmp / "home")
        root.ensure()
        conn = sqlite3.connect(root.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        run_migrate(conn, directory=old)
        self.assertEqual(user_version(conn), 12)
        t = "2026-10-11T00:00:00Z"
        conn.execute("BEGIN")
        conn.execute("INSERT INTO brands(id,name,current_revision_id,kind,created_at,updated_at) VALUES('b','b','br','user',?,?)", (t, t))
        conn.execute("INSERT INTO brand_revisions(id,brand_id,revision,config_json,created_at) VALUES('br','b',1,'{}',?)", (t,))
        conn.execute("INSERT INTO styles(id,name,current_revision_id,kind,created_at,updated_at) VALUES('s','s','sr','user',?,?)", (t, t))
        conn.execute("INSERT INTO style_revisions(id,style_id,revision,config_json,created_at) VALUES('sr','s',1,'{}',?)", (t,))
        sha = "a" * 64
        conn.execute("INSERT INTO template_snapshots(id,snapshot_sha256,snapshot_json,created_at) VALUES('snap',?,'{}',?)", (sha, t))
        args = {"d1": {"mode": "presentation", "template_snapshot": {"snapshot_id": "snap", "snapshot_sha256": sha}},
                "d2": {"mode": "presentation"}}
        for did, a in args.items():
            conn.execute("INSERT INTO decks(id,title,mode,brand_revision_id,style_revision_id,created_at,updated_at)"
                         " VALUES(?,?,'presentation','br','sr',?,?)", (did, did, t, t))
            conn.execute("INSERT INTO runs(id,deck_id,number,mode,engine,args_json,state,created_at,updated_at,ended_at)"
                         " VALUES(?,?,1,'presentation','html-slide',?,'failed',?,?,?)", (f"r-{did}", did, json.dumps(a), t, t, t))
        conn.execute("COMMIT")
        return root, conn, sha

    def test_the_deck_column_is_backfilled_and_refusals_and_duplicate_work(self) -> None:
        from quick_slide.server.deck_routes import resume_capability
        from quick_slide.store.edits import TEMPLATE_RESUME_REASON

        root, conn, sha = self.seed_v12()
        store = Store.open(root, conn=conn, settings=Settings())
        self.addCleanup(store.close)
        rows = {r[0]: r[1] for r in store.conn.execute("SELECT id,template_snapshot_id FROM decks")}
        self.assertEqual(rows, {"d1": "snap", "d2": None})
        ref = {"snapshot_id": "snap", "snapshot_sha256": sha}
        self.assertEqual(store.deck_template_snapshot_ref("d1"), ref)
        self.assertIsNone(store.deck_template_snapshot_ref("d2"))
        cap = resume_capability(store, "d1", store.latest_run("d1"), None)
        self.assertEqual((cap["available"], cap["reason"]), (False, TEMPLATE_RESUME_REASON))
        with self.assertRaises(StateError):
            store.change_deck_axes("d1", brand_revision_id=store.preset_brand_revision_id())
        with self.assertRaises(StateError):
            store.create_edit("d1", {"op": "rebuild", "source": "screen", "changed": []})
        copy = store.duplicate_deck("d1")
        self.assertEqual(store.deck_template_snapshot_ref(copy["id"]), ref)
        self.assertEqual(store.deck_run_args(copy["id"])["template_snapshot"], ref)

    def test_a_deck_with_a_null_column_falls_back_to_its_first_runs_reference(self) -> None:
        root, conn, sha = self.seed_v12()
        store = Store.open(root, conn=conn, settings=Settings())
        self.addCleanup(store.close)
        store.conn.execute("UPDATE decks SET template_snapshot_id=NULL WHERE id='d1'")   # 0013 を適用済みの開発機の DB など
        self.assertEqual(store.deck_template_snapshot_ref("d1"), {"snapshot_id": "snap", "snapshot_sha256": sha})


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
    """本物のワーカー（台本係・エンジンの検査は偽物）で、台本を確かめたところ（描く前）で止まることを見る。"""

    def setUp(self) -> None:
        from test_worker import fake_skills_dir, settings

        tools, log = fake_tools(self, skills_dir=fake_skills_dir(self))
        tools.extra_env.update({"FAKE_ENGINE_SLEEP": "0.01", "FAKE_ENGINE_LOG": str(log / "engine")})
        self.fx = start_server(self, tools=tools, settings=settings())
        with self.fx.server.store_lock:
            store = self.fx.store
            cfg = json.loads(json.dumps(store.brand_revision(store.preset_brand_revision_id())["config"]))
            self.brand = store.create_brand("試験のブランド", cfg)
            self.template = store.create_design_template(self.brand["id"], doc("プレゼン用", modes=("presentation",)))

    def test_a_template_run_stops_after_the_script_is_checked_with_the_reason(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(design_template="プレゼン用"))
        self.assertEqual(r.status, 201, r.body)
        body = r.json()
        run = wait_until_idle(self.fx, body["deck_id"])
        self.assertEqual((run["state"], run["failure_kind"]), ("failed", "template_render_pending"))
        self.assertEqual(run["message"], TEMPLATE_RENDER_PENDING_MESSAGE)
        with self.fx.server.store_lock:
            store = self.fx.store
            self.assertIsNone(store.head_script(body["deck_id"]), "描く前なので、いまの台本にはしない")
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM scripts").fetchone()[0], 1, "台本は書いて確かめた")
            record = store.run(run["id"])["record"]
            self.assertEqual(record["look"]["template"]["template_id"], self.template["template_id"])
            self.assertFalse(record["look"]["template"]["followed_latest"])
            self.assertTrue(record["look"]["theme_sha256"])
            self.assertEqual(record["template_stop"]["reason"], "render_unavailable")
            gen = record["preparation"]["generations"][str(record["preparation"]["current"])]
            self.assertEqual((gen["state"], gen["stage"]), ("completed", "manifest"))
            self.assertNotIn("engine", record, "描いていない")
        view = self.fx.get(f"/api/decks/{body['deck_id']}").json()
        self.assertEqual(view["pages"], [])
        self.assertEqual((view["planning"]["state"], view["planning"]["message"]), ("failed", TEMPLATE_RENDER_PENDING_MESSAGE))
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
            row = store.conn.execute("SELECT id FROM scripts WHERE deck_id=?", (body["deck_id"],)).fetchone()
            script = store.script(row["id"])
            data = store.load_script(script)
            store.accept_script(script["id"], screen_pages(first["args"], data))   # 描画がつながるまでは画面側が承認した想定
            before = (store.head_script(body["deck_id"])["id"], [p["page_id"] for p in store.script_pages(script["id"])])
            made = store.create_edit(body["deck_id"], {"op": "insert", "after": None, "text": "1 枚足す", "attachments": [],
                                                       "source": "screen", "can_generate": False})
        self.fx.server.wake_worker()
        run = wait_until_idle(self.fx, body["deck_id"])
        self.assertEqual(run["id"], made["run"]["id"])
        self.assertEqual((run["state"], run["failure_kind"], run["message"]),
                         ("failed", "template_render_pending", TEMPLATE_RENDER_PENDING_MESSAGE))
        with self.fx.server.store_lock:
            store = self.fx.store
            self.assertEqual(store.head_script(body["deck_id"])["id"], before[0], "止まったあとも、いまの台本は変わらない")
            self.assertEqual([p["page_id"] for p in store.script_pages(before[0])], before[1])
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM scripts WHERE deck_id=?", (body["deck_id"],)).fetchone()[0], 2,
                             "候補は保存するが、承認しない")
            gens = store.run(run["id"])["record"]["preparation"]["generations"]
            (gen,) = gens.values()
            self.assertEqual((gen["state"], gen["parent"] is not None), ("completed", True), "完了した世代を引き継いだ新しい世代")

    def test_a_stopped_template_request_deck_cannot_be_resumed_until_preparation_is_connected(self) -> None:
        p = intake_payload(design_template="プレゼン用")
        p.pop("manuscript")
        p["request"] = "架空の勉強会の案内を作る"
        p["origin"]["kind"] = "request"
        body = self.fx.post("/api/intakes", p).json()
        run = wait_until_idle(self.fx, body["deck_id"])
        self.assertEqual(run["failure_kind"], "template_render_pending")
        view = self.fx.get(f"/api/decks/{body['deck_id']}").json()
        self.assertEqual((view["capabilities"]["resume"]["available"], view["capabilities"]["resume"]["reason"]),
                         (False, TEMPLATE_RESUME_REASON))
        self.assertFalse(view["limit"]["resume"]["available"])
        r = self.fx.post(f"/api/decks/{body['deck_id']}/resume", {})
        self.assertEqual((r.status, r.error_code), (409, "nothing_to_resume"))
        self.assertEqual(r.json()["error"]["message"], TEMPLATE_RESUME_REASON)
        with self.fx.server.store_lock:
            self.assertEqual(len(self.fx.store.runs_for_deck(body["deck_id"])), 1, "新しい回を作らない")

    def test_duplicating_a_stopped_template_deck_keeps_the_snapshot_and_the_refusals(self) -> None:
        body = self.fx.post("/api/intakes", intake_payload(design_template="プレゼン用")).json()
        wait_until_idle(self.fx, body["deck_id"])
        r = self.fx.post(f"/api/decks/{body['deck_id']}/duplicate", {})
        self.assertEqual(r.status, 201, r.body)
        copy = r.json()["deck"]["id"]
        self.assertNotEqual(copy, body["deck_id"])
        with self.fx.server.store_lock:
            store = self.fx.store
            self.assertEqual(store.deck_template_snapshot_ref(copy), store.deck_template_snapshot_ref(body["deck_id"]))
            self.assertIsNotNone(store.deck_template_snapshot_ref(copy))
            self.assertEqual(store.deck_run_args(copy)["template_snapshot"], store.deck_template_snapshot_ref(copy))
            with self.assertRaises(StateError):
                store.change_deck_axes(copy, brand_revision_id=store.preset_brand_revision_id())
            with self.assertRaises(StateError):
                store.create_edit(copy, {"op": "rebuild", "source": "screen", "changed": []})
        r = self.fx.post(f"/api/decks/{copy}/rebuild", {})
        self.assertEqual((r.status, r.error_code), (409, "template_rebuild_unsupported"))


if __name__ == "__main__":
    unittest.main()
