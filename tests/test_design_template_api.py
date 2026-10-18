"""デザインテンプレートの API（server/template_routes.py・template_service.py）。一時データの本番サーバへ HTTP で一巡する。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path
from unittest import mock

from helpers import start_server
from legacy_templates import all_rows, insert_legacy_template, stored_row

from quick_slide import design_templates as dt
from quick_slide.ids import PRESET_BRAND_ID
from quick_slide.server import template_routes as routes

BASE = "/api/settings/design-templates"
# どの OS でも絶対パスになる、存在しない置き場
NO_SUCH_FILE = Path(tempfile.gettempdir()).resolve() / "qs-api-no-such" / "memo.md"


def doc(name="社内報告", **kw):
    d = dt.new_document("placeholder", "placeholder", name)
    d.update(structure="## 1. 結論\n先に結論を書く", instructions="図は少なめにする")
    d.update(kw)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.fx = start_server(self)
        with self.fx.server.store_lock:
            store = self.fx.store
            config = json.loads(json.dumps(store.brand_revision(store.preset_brand_revision_id())["config"]))
            self.bid = store.create_brand("試験のブランド", config)["id"]

    # --- 道具 ---
    def create(self, document=None, brand_id=None):
        r = self.fx.post(BASE, {"brand_id": brand_id or self.bid, "document": document or doc()})
        self.assertEqual(201, r.status, r.body)
        return r.json()

    def save(self, t, document, mode="overwrite", **kw):
        body = {"document": document, "mode": mode, "expected_revision_id": t["current_revision_id"], **kw}
        return self.fx.post(f"{BASE}/{t['template_id']}", body)

    def revised(self, t, **kw):
        d = dict(t["document"])
        d.update(kw)
        return d

    def rows(self, table="design_template_revisions"):
        with self.fx.server.store_lock:
            return self.fx.store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def snapshot(self):
        """書かないことの確かめ。関連する表の全行の中身と、データ置き場のファイル一覧（名前と、DB 以外の内容のハッシュ）。"""
        with self.fx.server.store_lock:
            c = self.fx.store.conn
            tables = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()]
                      for t in ("design_templates", "design_template_revisions", "events")}
            files = self.fx.all_files()
        listing = sorted((str(p.relative_to(self.fx.root.root)), "" if ".db" in p.name else hashlib.sha256(v).hexdigest())
                         for p, v in files.items())
        return tables, listing

    def template_state(self, template_id):
        with self.fx.server.store_lock:
            return self.fx.store.design_template_revisions(template_id), self.fx.store.get_design_template(template_id)

    def markdown(self, name="取り込み", template_id=None, **kw):
        d = doc(name, **kw)
        d["id"] = template_id or "from-file"
        d["brand_id"] = self.bid
        return dt.to_markdown(d)

    def check(self, text, brand_id=None):
        return self.fx.post(f"{BASE}/import/check", {"text": text, "brand_id": brand_id or self.bid})

    def do_import(self, text, mode, **kw):
        return self.fx.post(f"{BASE}/import", {"text": text, "brand_id": self.bid, "mode": mode, **kw})


class RouteTests(Base):
    def test_create_get_list(self):
        t = self.create()
        self.assertEqual(("created", [], 1), (t["action"], t["changed_sections"], t["revision"]))
        self.assertEqual("v1 を作りました", t["message"])
        self.assertEqual(t["document"]["id"], t["template_id"])
        got = self.fx.get(f"{BASE}/{t['template_id']}")
        self.assertEqual(200, got.status)
        self.assertEqual(t["document"], got.json()["document"])
        self.assertEqual("no-store", got.headers["cache-control"])
        listing = self.fx.get(f"{BASE}?brand_id={self.bid}").json()
        self.assertEqual([t["template_id"]], [x["template_id"] for x in listing["templates"]])
        self.assertEqual(400, self.fx.get(BASE).status)
        self.assertEqual(404, self.fx.get(f"{BASE}?brand_id=00000000-0000-4000-8000-000000000000").status)
        self.assertEqual(404, self.fx.get(f"{BASE}/00000000-0000-4000-8000-000000000000").status)

    def test_editor_url_opened_directly_returns_the_app(self):
        # 編集画面の URL を直接開いても（再読み込み・ブックマーク）、画面の HTML を返す
        t = self.create()
        page = self.fx.get(f"/settings/brands/{self.bid}/templates/{t['template_id']}")
        self.assertEqual(200, page.status)
        self.assertIn("text/html", page.headers["content-type"])

    def test_overwrite_new_version_unchanged_messages(self):
        t = self.create()
        o = self.save(t, self.revised(t, structure="## 1. 変えた")).json()
        self.assertEqual(("overwritten", ["構成"], 1), (o["action"], o["changed_sections"], o["revision"]))
        self.assertEqual("v1 を上書きしました（変更：構成）", o["message"])
        self.assertNotEqual(t["current_revision_id"], o["current_revision_id"])
        n = self.save(o, self.revised(o, instructions="別の指示", structure="新しい構成"), mode="new_version").json()
        self.assertEqual(("new_version", 2), (n["action"], n["revision"]))
        self.assertEqual(["構成", "追加指示"], n["changed_sections"])
        self.assertEqual("v2 を作りました（変更：構成・追加指示）", n["message"])
        self.assertEqual(1, n["document"]["based_on"])
        same = self.save(n, n["document"], mode="new_version").json()
        self.assertEqual(("unchanged", n["current_revision_id"]), (same["action"], same["current_revision_id"]))
        self.assertEqual(3, self.rows())

    def test_client_sent_id_revision_and_origin_are_ignored(self):
        t = self.create()
        o = self.save(t, self.revised(t, id="x", revision=9, based_on=7, brand_id="y", name="改名"), mode="new_version").json()
        d = o["document"]
        self.assertEqual((t["template_id"], self.bid, 2, 1), (d["id"], d["brand_id"], d["revision"], d["based_on"]))

    def test_copy_from_old_version_and_read_old(self):
        t = self.create()
        v2 = self.save(t, self.revised(t, instructions="二番目"), mode="new_version").json()
        old = self.fx.get(f"{BASE}/{t['template_id']}?revision=1").json()
        self.assertEqual("図は少なめにする", old["document"]["instructions"])
        c = self.save(v2, old["document"], mode="new_version", source_revision_id=old["revision_id"]).json()
        self.assertEqual(("copied", 3, 1), (c["action"], c["revision"], c["document"]["based_on"]))
        self.assertEqual(["追加指示"], c["changed_sections"])
        self.assertEqual(404, self.fx.get(f"{BASE}/{t['template_id']}?revision=9").status)
        self.assertEqual(400, self.fx.get(f"{BASE}/{t['template_id']}?revision=x").status)
        # 上書きにコピー元は付けられない
        r = self.save(c, old["document"], mode="overwrite", source_revision_id=old["revision_id"])
        self.assertEqual(400, r.status)

    def test_conversation_mode(self):
        t = self.create()
        a = self.save(t, self.revised(t, instructions="会話の 1"), mode="conversation",
                      origin_runtime="claude_code", conversation_key="conv-1")
        self.assertEqual(200, a.status, a.body)
        self.assertEqual("new_version", a.json()["action"])
        b = self.save(a.json(), self.revised(a.json(), instructions="会話の 2"), mode="conversation",
                      origin_runtime="claude_code", conversation_key="conv-1").json()
        self.assertEqual(("overwritten", 2), (b["action"], b["revision"]))
        bad = self.save(b, b["document"], mode="conversation")
        self.assertEqual(200, bad.status)   # origin 省略は browser 扱い
        self.assertEqual(400, self.save(b, b["document"], mode="other").status)

    def test_copy_duplicates_without_history(self):
        t = self.create()
        c = self.fx.post(f"{BASE}/{t['template_id']}/copy", {"expected_revision_id": t["current_revision_id"]})
        self.assertEqual(201, c.status, c.body)
        c = c.json()
        self.assertNotEqual(t["template_id"], c["template_id"])
        self.assertEqual(("社内報告のコピー", 1, None), (c["document"]["name"], c["revision"], c["document"]["based_on"]))
        self.assertEqual("copied", c["action"])
        stale = self.fx.post(f"{BASE}/{t['template_id']}/copy", {"expected_revision_id": "stale"})
        self.assertEqual((409, "conflict"), (stale.status, stale.error_code))

    def test_markdown_create_save_and_plan(self):
        text = self.markdown("週報", template_id="ignored-file-id")
        r = self.fx.post(BASE, {"brand_id": self.bid, "markdown": text})
        self.assertEqual(201, r.status, r.body)
        t = r.json()
        self.assertEqual(("週報", 1), (t["document"]["name"], t["revision"]))
        self.assertNotEqual("ignored-file-id", t["template_id"], "ファイルの ID は使わない")
        changed = self.markdown("週報", structure="## 1. 変えた構成")
        body = {"markdown": changed, "mode": "new_version", "expected_revision_id": t["current_revision_id"]}
        before = self.snapshot()
        p = self.fx.post(f"{BASE}/plan", {"template_id": t["template_id"], **body})
        self.assertEqual((200, "new_version", ["構成"]), (p.status, p.json()["action"], p.json()["changed_sections"]))
        self.assertEqual(before, self.snapshot())
        s = self.fx.post(f"{BASE}/{t['template_id']}", body)
        self.assertEqual(200, s.status, s.body)
        self.assertEqual((2, "変えた構成"), (s.json()["revision"], s.json()["document"]["structure"].split(". ")[-1]))
        same = self.fx.post(f"{BASE}/{t['template_id']}", {**body, "expected_revision_id": s.json()["current_revision_id"]})
        self.assertEqual("unchanged", same.json()["action"])

    def test_markdown_and_document_are_exclusive_and_bad_markdown_is_400(self):
        t = self.create()
        text = self.markdown()
        both = {"brand_id": self.bid, "document": doc(), "markdown": text}
        self.assertEqual(400, self.fx.post(BASE, both).status)
        self.assertEqual(400, self.fx.post(BASE, {"brand_id": self.bid}).status)
        self.assertEqual(400, self.fx.post(BASE, {"brand_id": self.bid, "markdown": "ただの文章"}).status)
        self.assertEqual(400, self.fx.post(BASE, {"brand_id": self.bid, "markdown": 5}).status)
        base = {"mode": "overwrite", "expected_revision_id": t["current_revision_id"]}
        self.assertEqual(400, self.fx.post(f"{BASE}/{t['template_id']}", {**base, "document": t["document"], "markdown": text}).status)
        self.assertEqual(400, self.fx.post(f"{BASE}/{t['template_id']}", {**base, "markdown": "ただの文章"}).status)
        self.assertEqual(400, self.fx.post(f"{BASE}/{t['template_id']}", base).status)
        self.assertEqual(400, self.fx.post(f"{BASE}/plan", {"template_id": t["template_id"], **base, "markdown": "x"}).status)
        self.assertEqual(1, self.rows())

    def legacy(self, kind="full"):
        with self.fx.server.store_lock:
            return insert_legacy_template(self.fx.store, self.bid, kind)

    def stored(self):
        with self.fx.server.store_lock:
            return all_rows(self.fx.store)

    def test_old_get_then_edit_and_save_converts_the_attachments_into_resources(self):
        """旧い行を GET すると添付の欄は無く、移したリソースが返る。そのまま編集して保存でき、旧い行は書き換わらない。"""
        old = self.legacy("full")
        frozen = self.stored()
        t = self.fx.get(f"{BASE}/{old['template_id']}").json()
        self.assertEqual((t["document"]["schema_version"], "attachments" in t["document"]), (4, False))
        moved = t["document"]["resources"][-1]
        self.assertEqual((moved["id"], moved["name"], moved["kind"], moved["path"], moved["usage"]),
                         ("m1", "旧添付", "file", "memo.md", "用語の見本"))
        # 同じ文書の保存は何も作らない（保存したハッシュは旧い算法のままなので、新しい形で前後を比べる）
        same = self.save(t, t["document"])
        self.assertEqual((200, "unchanged"), (same.status, same.json()["action"]), same.body)
        self.assertEqual(self.stored(), frozen)
        # 古い画面（添付の欄つきの旧形式の文書）が同じ中身を返しても、同じく何も作らない
        legacy_shape = {**{k: v for k, v in t["document"].items() if k != "resources"}, "schema_version": 3,
                        "resources": t["document"]["resources"][:-1],
                        "attachments": [{"id": "m1", "name": "旧添付", "file": "memo.md", "url": "/preview/files/m1", "image": False,
                                         "usage": "用語の見本", "sha256": "a" * 64}]}
        self.assertEqual("unchanged", self.save(t, legacy_shape).json()["action"])
        # 編集すると新しい形の行が増える（旧い行は変わらない）
        ok = self.save(t, self.revised(t, instructions="直した"))
        self.assertEqual((200, "overwritten"), (ok.status, ok.json()["action"]), ok.body)
        self.assertEqual(self.stored()[:len(frozen)], frozen)
        self.assertEqual(len(self.stored()), len(frozen) + 1)
        self.assertEqual([r["name"] for r in ok.json()["document"]["resources"]], ["画像集", "旧添付"])
        self.assertNotIn("attachments", ok.json()["document"])
        # 新しい相対パスのリソースは足せない（残せるのは旧添付から移した元のファイル名だけ）
        bad = self.save(ok.json(), self.revised(ok.json(), resources=ok.json()["document"]["resources"] + [
            {"id": "n1", "name": "新規", "kind": "file", "path": "relative.md", "usage": ""}]))
        self.assertEqual(400, bad.status)
        # 利用者がパスを指定し直せば、通常のリソースとして保存される
        fixed = json.loads(json.dumps(ok.json()["document"]))
        fixed["resources"][-1]["path"] = str(NO_SUCH_FILE)
        again = self.save(ok.json(), fixed)
        self.assertEqual(200, again.status, again.body)
        self.assertEqual(str(NO_SUCH_FILE), again.json()["document"]["resources"][-1]["path"])
        self.assertEqual(stored_row(self.fx.store, old["revision_id"]), (old["document_json"], old["content_sha256"]))

    def test_old_rows_are_shown_copied_exported_and_planned_in_the_new_form_only(self):
        old = self.legacy("full")
        t = self.fx.get(f"{BASE}/{old['template_id']}").json()
        new = self.save(t, self.revised(t, instructions="二版目"), mode="new_version").json()
        frozen = self.stored()
        revs = self.fx.get(f"{BASE}/{old['template_id']}/revisions").json()["revisions"]
        for row in revs:
            self.assertNotIn("attachments", row["document"])
            self.assertEqual(row["document"]["resources"][-1]["name"], "旧添付")
        shown = self.fx.get(f"{BASE}/{old['template_id']}?revision=1").json()
        self.assertNotIn("attachments", shown["document"])
        # 過去版をコピーして次の版を作る（コピー元の旧添付はリソースのまま）
        copied = self.save(new, shown["document"], mode="new_version", source_revision_id=shown["revision_id"])
        self.assertEqual(200, copied.status, copied.body)
        self.assertEqual("copied", copied.json()["action"])
        self.assertEqual(copied.json()["document"]["resources"][-1]["name"], "旧添付")
        before = self.snapshot()
        plan = self.fx.post(f"{BASE}/plan", {"template_id": old["template_id"], "mode": "new_version", "document": self.revised(copied.json(), instructions="計画だけ"),
                                             "expected_revision_id": copied.json()["current_revision_id"]})
        self.assertEqual(200, plan.status, plan.body)
        self.assertEqual(before, self.snapshot(), "計画は何も書かない")
        self.assertEqual(self.stored()[:len(frozen)], frozen, "旧い行は書き換わらない")

    def test_markdown_export_of_an_old_row_works_and_import_needs_a_real_path(self):
        old = self.legacy("full")
        exported = self.fx.get(f"{BASE}/{old['template_id']}/markdown")
        self.assertEqual(200, exported.status, "添付があっても書き出せる（通常のリソースとして）")
        text = exported.body.decode("utf-8")
        self.assertIn("旧添付", text)
        self.assertNotIn("attachments", text)
        t = self.fx.get(f"{BASE}/{old['template_id']}").json()
        frozen = self.stored()
        # 元のファイル名のままの Markdown は、パスが絶対でないので断る（何も書かない）
        r = self.fx.post(f"{BASE}/{t['template_id']}", {"markdown": text, "mode": "new_version", "expected_revision_id": t["current_revision_id"]})
        self.assertEqual(400, r.status, r.body)
        self.assertEqual(self.stored(), frozen)
        # パスを書き直せば、保存でき、書き出しに添付の欄は無い
        fixed = text.replace("path: memo.md", f"path: {NO_SUCH_FILE}")
        self.assertNotEqual(fixed, text)
        ok = self.fx.post(f"{BASE}/{t['template_id']}", {"markdown": fixed, "mode": "new_version", "expected_revision_id": t["current_revision_id"]})
        self.assertEqual(200, ok.status, ok.body)
        self.assertNotIn("attachments", ok.json()["document"])
        self.assertEqual(ok.json()["document"]["resources"][-1]["path"], str(NO_SUCH_FILE))

    def test_copy_accepts_a_name(self):
        t = self.create()
        c = self.fx.post(f"{BASE}/{t['template_id']}/copy", {"expected_revision_id": t["current_revision_id"], "name": "別名"})
        self.assertEqual((201, "別名", 1), (c.status, c.json()["document"]["name"], c.json()["revision"]))
        for bad in ("  ", 5, ""):
            r = self.fx.post(f"{BASE}/{t['template_id']}/copy", {"expected_revision_id": t["current_revision_id"], "name": bad})
            self.assertEqual(400, r.status, bad)

    def test_conflicts_are_409(self):
        t = self.create()
        self.save(t, self.revised(t, structure="先に別の画面が保存"))
        r = self.save(t, self.revised(t, structure="遅れて保存"))
        self.assertEqual((409, "conflict"), (r.status, r.error_code))
        r = self.fx.post(f"{BASE}/{t['template_id']}/delete", {"expected_revision_id": t["current_revision_id"]})
        self.assertEqual(409, r.status)
        self.assertEqual(400, self.fx.post(f"{BASE}/{t['template_id']}", {"document": t["document"], "mode": "overwrite"}).status)
        self.assertEqual(2, self.rows())   # 競合で増えない（作成 1 + 先の保存 1）

    def test_validation_and_unknown_fields_are_400(self):
        t = self.create()
        self.assertEqual(400, self.save(t, self.revised(t, name="  ")).status)
        self.assertEqual(400, self.save(t, self.revised(t, modes=[])).status)
        self.assertEqual(400, self.save(t, "文字列").status)
        self.assertEqual(400, self.fx.post(f"{BASE}/{t['template_id']}", {"document": t["document"], "mode": "overwrite",
                                                                          "expected_revision_id": "x", "extra": 1}).status)
        self.assertEqual(400, self.fx.post(BASE, {"brand_id": self.bid, "document": doc(resources=[
            {"id": "r1", "name": "相対", "kind": "file", "path": "relative.md", "usage": ""}])}).status)

    def test_attachments_cannot_be_created_through_the_api(self):
        attachment = {"id": "m1", "name": "旧添付", "file": "a.md", "url": "/x", "image": False, "usage": "", "sha256": "a" * 64}
        # 新しい形式の文書は添付の欄を持たない
        self.assertEqual(400, self.fx.post(BASE, {"brand_id": self.bid, "document": doc(attachments=[attachment])}).status)
        # 古い形式（添付の欄あり）で新しく作ろうとしても、元のファイル名は絶対パスではないので断る
        stale = {**doc(), "schema_version": 3, "attachments": [attachment]}
        self.assertEqual(400, self.fx.post(BASE, {"brand_id": self.bid, "document": stale}).status)
        self.assertEqual(0, self.rows())
        # 空の添付の欄だけなら、古い画面の文書でも通る（欄は外れる）
        ok = self.fx.post(BASE, {"brand_id": self.bid, "document": {**doc(), "schema_version": 3, "attachments": []}})
        self.assertEqual(201, ok.status, ok.body)
        self.assertNotIn("attachments", ok.json()["document"])

    def test_delete_hides_but_history_stays(self):
        t = self.create()
        v2 = self.save(t, self.revised(t, instructions="2"), mode="new_version").json()
        d = self.fx.post(f"{BASE}/{t['template_id']}/delete", {"expected_revision_id": v2["current_revision_id"]})
        self.assertEqual((200, "deleted"), (d.status, d.json()["action"]))
        self.assertEqual([], self.fx.get(f"{BASE}?brand_id={self.bid}").json()["templates"])
        revs = self.fx.get(f"{BASE}/{t['template_id']}/revisions").json()["revisions"]
        self.assertEqual([2, 1], [r["revision"] for r in revs])
        self.assertEqual(200, self.fx.get(f"{BASE}/{t['template_id']}?revision=1").status)
        r = self.save(v2, v2["document"])
        self.assertEqual((409, "state_conflict"), (r.status, r.error_code))
        self.assertEqual(2, self.rows())

    def test_revisions_list(self):
        t = self.create()
        self.save(t, self.revised(t, instructions="2"), mode="new_version")
        revs = self.fx.get(f"{BASE}/{t['template_id']}/revisions").json()["revisions"]
        self.assertEqual([2, 1], [r["revision"] for r in revs])
        self.assertEqual(["追加指示"], revs[0]["changed"])
        self.assertEqual(1, revs[0]["based_on"])
        self.assertTrue(revs[0]["is_latest"])

    def test_markdown_export_headers_and_round_trip(self):
        t = self.create(doc("週次 報告/A", resources=[{"id": "r1", "name": "素材", "kind": "directory", "path": "~/materials", "usage": "必要なものだけ"}]))
        r = self.fx.get(f"{BASE}/{t['template_id']}/markdown")
        self.assertEqual(200, r.status)
        self.assertEqual("no-store", r.headers["cache-control"])
        self.assertTrue(r.headers["content-type"].startswith("text/markdown"))
        name = urllib.parse.quote("qs-テンプレート-週次 報告/A.md", safe="")
        self.assertIn(f"filename*=UTF-8''{name}", r.headers["content-disposition"])
        text = r.body.decode("utf-8")
        back = dt.from_markdown(text, brand_id=self.bid, template_id=t["template_id"])
        self.assertEqual(t["document"]["structure"], back["structure"])
        self.assertEqual(t["document"]["instructions"], back["instructions"])
        # 書き出したものを、同じテンプレートへ取り込むと同文（新しい版を作らない）
        c = self.check(text).json()
        self.assertEqual(["new_version", "add"], c["choices"])
        same = self.do_import(text, "new_version", expected_revision_id=c["existing"]["current_revision_id"]).json()
        self.assertEqual(("unchanged", t["current_revision_id"]), (same["action"], same["current_revision_id"]))

    def test_plan_writes_nothing(self):
        t = self.create()
        before = self.snapshot()
        p = self.fx.post(f"{BASE}/plan", {"template_id": t["template_id"], "mode": "new_version",
                                          "document": self.revised(t, structure="計画だけ"),
                                          "expected_revision_id": t["current_revision_id"]})
        self.assertEqual(200, p.status, p.body)
        j = p.json()
        self.assertEqual((True, "new_version", 2, ["構成"]), (j["dry_run"], j["action"], j["revision"], j["changed_sections"]))
        self.assertEqual(before, self.snapshot())
        o = self.fx.post(f"{BASE}/plan", {"template_id": t["template_id"], "mode": "overwrite",
                                          "document": self.revised(t, instructions="上書きの計画"),
                                          "expected_revision_id": t["current_revision_id"]}).json()
        self.assertEqual(("overwritten", 1, ["追加指示"]), (o["action"], o["revision"], o["changed_sections"]))
        self.assertEqual(before, self.snapshot())
        same = self.fx.post(f"{BASE}/plan", {"template_id": t["template_id"], "mode": "overwrite", "document": t["document"],
                                             "expected_revision_id": t["current_revision_id"]}).json()
        self.assertEqual("unchanged", same["action"])
        c = self.fx.post(f"{BASE}/plan", {"brand_id": self.bid, "document": doc("計画の新規")}).json()
        self.assertEqual("created", c["action"])
        stale = self.fx.post(f"{BASE}/plan", {"template_id": t["template_id"], "mode": "overwrite", "document": t["document"],
                                              "expected_revision_id": "stale"})
        self.assertEqual(409, stale.status)
        self.assertEqual(before, self.snapshot())
        # 計画のあとに本保存しても、版は飛ばない
        self.assertEqual(2, self.save(t, self.revised(t, structure="本保存"), mode="new_version").json()["revision"])

    def test_compiled_json_and_zip_use_the_parents_latest_brand_revision(self):
        t = self.create(doc(modes=["presentation", "pitch"]))
        with self.fx.server.store_lock:   # 保存のあとでブランドが進めば、親の最新の版で作る（新しい資料の投入と同じ）
            store = self.fx.store
            config = json.loads(json.dumps(store.brand(self.bid)["current_revision"]["config"]))
            config["footer"] = {"text": "あとから変えた"}
            store.add_brand_revision(self.bid, config)
        r = self.fx.get(f"{BASE}/{t['template_id']}/compiled?mode=presentation")
        self.assertEqual(200, r.status, r.body)
        j = r.json()
        self.assertEqual(("instructions_compiled", False, 1), (j["stage"], j["render_ready"], j["template_revision"]))
        self.assertIn("あとから変えた", j["files"]["brand.json"])
        with self.fx.server.store_lock:
            latest = self.fx.store.brand(self.bid)["current_revision_id"]
        self.assertEqual(json.loads(j["files"]["engine-input.json"])["brand_revision_id"], latest)
        self.assertNotEqual(latest, t["brand_revision"]["id"], "保存したときのブランドの版ではない")
        self.assertEqual(400, self.fx.get(f"{BASE}/{t['template_id']}/compiled?mode=nope").status)
        z = self.fx.get(f"{BASE}/{t['template_id']}/compiled/export?mode=pitch&revision=1")
        self.assertEqual("application/zip", z.headers["content-type"])
        self.assertIn(f"{t['template_id']}-instructions.zip", z.headers["content-disposition"])
        names = zipfile.ZipFile(io.BytesIO(z.body)).namelist()
        self.assertIn("writer.md", names)
        self.assertIn("style.yaml", names)
        self.assertIn("compilation.json", names)

    def test_preset_brand_allowed_and_deleted_brand_hides_templates(self):
        p = self.create(brand_id=PRESET_BRAND_ID)
        self.assertEqual(PRESET_BRAND_ID, p["brand_id"])
        t = self.create()
        with self.fx.server.store_lock:
            self.fx.store.delete_brand(self.bid)
        self.assertEqual([], self.fx.get(f"{BASE}?brand_id={self.bid}").json()["templates"])
        r = self.save(t, self.revised(t, structure="削除済みブランドの子"))
        self.assertEqual((409, "state_conflict"), (r.status, r.error_code))
        r = self.fx.post(BASE, {"brand_id": self.bid, "document": doc("新規")})
        self.assertEqual(409, r.status)
        self.assertEqual(409, self.fx.post(f"{BASE}/{t['template_id']}/copy",
                                           {"expected_revision_id": t["current_revision_id"]}).status)
        self.assertEqual(409, self.check(self.markdown(), brand_id=self.bid).status)
        self.assertEqual(200, self.fx.get(f"{BASE}/{t['template_id']}/revisions").status)


class ImportTests(Base):
    def test_check_writes_nothing_and_reports(self):
        text = self.markdown("確かめるだけ", resources=[{"id": "r1", "name": "無い素材", "kind": "directory",
                                                      "path": "~/no-such-folder-for-test", "usage": ""}])
        before = self.snapshot()
        r = self.check(text)
        self.assertEqual(200, r.status, r.body)
        j = r.json()
        self.assertEqual(("確かめるだけ", ["create"], None, "from-file"), (j["name"], j["choices"], j["existing"], j["id"]))
        self.assertEqual([("無い素材", False)], [(x["name"], x["exists"]) for x in j["resources"]])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(400, self.check("ただの文章").status)
        self.assertEqual(404, self.check(text, brand_id="00000000-0000-4000-8000-000000000000").status)

    def test_create_when_no_existing_new_id_when_not_uuid(self):
        r = self.do_import(self.markdown("新規取り込み"), "create")
        self.assertEqual(201, r.status, r.body)
        j = r.json()
        self.assertEqual(("created", 1), (j["action"], j["revision"]))
        self.assertNotEqual("from-file", j["template_id"])   # UUID でない ID は引き継がない
        self.assertEqual(400, self.do_import("---\nformat: x\n---", "create").status)

    def test_create_always_gets_a_server_id_even_for_an_unused_uuid(self):
        file_id = "11111111-2222-4333-8444-555555555555"
        j = self.do_import(self.markdown("UUID あり", template_id=file_id), "create").json()
        self.assertNotEqual(file_id, j["template_id"])
        with self.fx.server.store_lock:
            self.assertEqual(1, self.rows("design_templates"))

    def test_create_is_refused_when_the_file_id_exists(self):
        t = self.create()
        text = self.fx.get(f"{BASE}/{t['template_id']}/markdown").body.decode("utf-8")   # 書き出しは採番済みの ID を持つ
        self.assertIn(t["template_id"], text)
        before = self.snapshot()
        again = self.do_import(text, "create")
        self.assertEqual((409, "import_choice_changed"), (again.status, again.error_code))
        self.assertEqual(before, self.snapshot())

    def test_new_version_with_based_on_and_stale_choice(self):
        t = self.create()
        text = self.markdown("取り込み後の名前", template_id=t["template_id"], instructions="取り込んだ指示")
        c = self.check(text).json()
        self.assertEqual(["new_version", "add"], c["choices"])
        self.assertEqual((t["template_id"], 1), (c["existing"]["template_id"], c["existing"]["revision"]))
        stale = self.do_import(text, "new_version", expected_revision_id="stale")
        self.assertEqual((409, "setting_changed"), (stale.status, stale.error_code))
        self.assertEqual(409, self.do_import(text, "create").status)
        ok = self.do_import(text, "new_version", expected_revision_id=c["existing"]["current_revision_id"])
        self.assertEqual(200, ok.status, ok.body)
        j = ok.json()
        self.assertEqual(("new_version", 2, 1, t["template_id"]), (j["action"], j["revision"], j["document"]["based_on"], j["template_id"]))
        self.assertEqual("取り込んだ指示", j["document"]["instructions"])

    def test_add_makes_another_template_with_suffix(self):
        t = self.create()
        text = self.markdown("元の名前", template_id=t["template_id"])
        r = self.do_import(text, "add")
        self.assertEqual(201, r.status, r.body)
        j = r.json()
        self.assertNotEqual(t["template_id"], j["template_id"])
        self.assertEqual("元の名前（インポート）", j["document"]["name"])
        self.assertEqual(2, len(self.fx.get(f"{BASE}?brand_id={self.bid}").json()["templates"]))
        # 既存が無い ID に「追加」は選べない（create になる）
        self.assertEqual(409, self.do_import(self.markdown("別", template_id="zzz"), "add").status)

    def test_deleted_id_is_never_revived(self):
        t = self.create()
        self.fx.post(f"{BASE}/{t['template_id']}/delete", {"expected_revision_id": t["current_revision_id"]})
        text = self.markdown("復活させない", template_id=t["template_id"])
        c = self.check(text).json()
        self.assertEqual((["add"], None), (c["choices"], c["existing"]))
        for mode in ("create", "new_version"):
            r = self.do_import(text, mode, expected_revision_id=t["current_revision_id"])
            self.assertEqual((409, "import_choice_changed"), (r.status, r.error_code), mode)
        j = self.do_import(text, "add").json()
        self.assertNotEqual(t["template_id"], j["template_id"])
        self.assertEqual(1, len(self.fx.get(f"{BASE}?brand_id={self.bid}").json()["templates"]))
        with self.fx.server.store_lock:
            self.assertIsNotNone(self.fx.store.get_design_template(t["template_id"])["deleted_at"])

    def test_same_id_in_another_brand_can_only_be_added(self):
        other = self.create(brand_id=PRESET_BRAND_ID)
        text = self.markdown("別ブランド", template_id=other["template_id"], instructions="取り込む側の指示")
        self.assertEqual(["add"], self.check(text).json()["choices"])
        untouched = self.template_state(other["template_id"])
        self.assertEqual(409, self.do_import(text, "create").status)
        self.assertEqual(409, self.do_import(text, "new_version", expected_revision_id=other["current_revision_id"]).status)
        self.assertEqual(untouched, self.template_state(other["template_id"]))
        r = self.do_import(text, "add")
        self.assertEqual(201, r.status, r.body)
        j = r.json()
        self.assertNotEqual(other["template_id"], j["template_id"])
        self.assertEqual((self.bid, self.bid), (j["brand_id"], j["document"]["brand_id"]))
        self.assertEqual("別ブランド（インポート）", j["document"]["name"])
        self.assertEqual(untouched, self.template_state(other["template_id"]))
        self.assertEqual([j["template_id"]], [x["template_id"] for x in self.fx.get(f"{BASE}?brand_id={self.bid}").json()["templates"]])

    def test_state_changed_between_check_and_import(self):
        t = self.create()
        text = self.markdown("確認後に変わる", template_id=t["template_id"])
        c = self.check(text).json()
        expected = c["existing"]["current_revision_id"]
        # 1. 確認のあとに、別の画面が新しい改訂を保存した
        newer = self.save(t, self.revised(t, instructions="先に保存された")).json()
        before = self.snapshot()
        r = self.do_import(text, "new_version", expected_revision_id=expected)
        self.assertEqual((409, "setting_changed"), (r.status, r.error_code))
        self.assertEqual(before, self.snapshot())
        # 2. 確認のあとに、テンプレートが削除された（選んだ道が無くなる）
        self.fx.post(f"{BASE}/{t['template_id']}/delete", {"expected_revision_id": newer["current_revision_id"]})
        before = self.snapshot()
        for mode in ("new_version", "create"):
            r = self.do_import(text, mode, expected_revision_id=newer["current_revision_id"])
            self.assertEqual((409, "import_choice_changed"), (r.status, r.error_code), mode)
        self.assertEqual(before, self.snapshot())

    def test_brand_deleted_between_check_and_import(self):
        t = self.create()
        text = self.markdown("ブランドが消える", template_id=t["template_id"])
        c = self.check(text).json()
        with self.fx.server.store_lock:
            self.fx.store.delete_brand(self.bid)
        before = self.snapshot()
        for mode in ("new_version", "add", "create"):
            r = self.do_import(text, mode, expected_revision_id=c["existing"]["current_revision_id"])
            self.assertEqual((409, "state_conflict"), (r.status, r.error_code), mode)
        self.assertEqual(before, self.snapshot())


class ResourceTests(Base):
    """リソースの有無の確認・コンパイルは、中身を読み切らない（上限つき）。読めない・無いパスでも落ちない。"""

    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp(prefix="quick-slide-res-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def row(self, rid, name, path, kind="file"):
        return {"id": rid, "name": name, "kind": kind, "path": str(path), "usage": ""}

    def test_check_uses_stat_only_and_never_hashes(self):
        small = self.dir / "small.md"
        small.write_text("中身", encoding="utf-8")
        folder = self.dir / "folder"
        folder.mkdir()
        (folder / "SKILL.md").write_text("説明", encoding="utf-8")
        text = self.markdown("有無だけ", resources=[self.row("a", "小", small), self.row("b", "フォルダ", folder, "directory"),
                                                    self.row("c", "無い", self.dir / "missing.md")])
        with mock.patch.object(dt, "_capped_sha256", side_effect=AssertionError("中身を読んではいけない")), \
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("中身を読んではいけない")), \
                mock.patch.object(Path, "open", side_effect=AssertionError("開いてはいけない")), \
                mock.patch("builtins.open", side_effect=AssertionError("開いてはいけない")), \
                mock.patch("os.open", side_effect=AssertionError("開いてはいけない")):
            r = self.check(text)
        self.assertEqual(200, r.status, r.body)
        self.assertEqual([("小", True), ("フォルダ", True), ("無い", False)], [(x["name"], x["exists"]) for x in r.json()["resources"]])

    def test_check_does_not_hold_the_store_lock_while_looking_at_files(self):
        text = self.markdown("鍵の外", resources=[self.row("a", "x", self.dir / "x.md")])
        seen = []
        original = dt.resource_presence
        def probe(rows):
            seen.append(self.fx.server.store_lock._is_owned())
            return original(rows)
        with mock.patch.object(dt, "resource_presence", probe):
            self.assertEqual(200, self.check(text).status)
        self.assertEqual([False], seen)

    def test_compile_skips_hashing_of_huge_files(self):
        huge = self.dir / "huge.bin"
        with huge.open("wb") as f:
            f.truncate(dt.MAX_HASH_BYTES + 1)   # 疎なファイル（実際の領域はほぼ使わない）
        small = self.dir / "small.md"
        small.write_text("小さい", encoding="utf-8")
        t = self.create(doc(resources=[self.row("a", "巨大", huge), self.row("b", "小", small)]))
        by = {r["name"]: r for r in self.fx.get(f"{BASE}/{t['template_id']}/compiled").json()["resources"]}
        self.assertEqual((True, None, True), (by["巨大"]["exists"], by["巨大"]["sha256"], by["巨大"]["hash_skipped"]))
        self.assertEqual((True, False), (by["小"]["exists"], by["小"]["hash_skipped"]))
        self.assertEqual(64, len(by["小"]["sha256"]))
        with mock.patch.object(dt, "MAX_HASH_BYTES", 1):   # 上限を下げれば、小さなファイルも省く
            by = {r["name"]: r for r in self.fx.get(f"{BASE}/{t['template_id']}/compiled").json()["resources"]}
        self.assertTrue(by["小"]["hash_skipped"])

    def test_compile_runs_outside_the_store_lock(self):
        t = self.create()
        seen = []
        original = dt.compile_document
        def probe(*a, **k):
            seen.append(self.fx.server.store_lock._is_owned())
            return original(*a, **k)
        with mock.patch.object(dt, "compile_document", probe):
            self.assertEqual(200, self.fx.get(f"{BASE}/{t['template_id']}/compiled").status)
        self.assertEqual([False], seen)

    def test_missing_and_unreadable_paths_do_not_fail(self):
        locked = self.dir / "locked.md"
        locked.write_text("読めない", encoding="utf-8")
        rows = [self.row("a", "無い", self.dir / "missing.md"), self.row("b", "読めない", locked)]
        t = self.create(doc(resources=rows))
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0:
            locked.chmod(0)
            self.addCleanup(locked.chmod, 0o600)
        r = self.fx.get(f"{BASE}/{t['template_id']}/compiled")
        self.assertEqual(200, r.status, r.body)
        j = r.json()
        self.assertFalse(j["can_prepare"])
        self.assertFalse(next(x for x in j["resources"] if x["name"] == "無い")["exists"])
        self.assertEqual(200, self.check(self.markdown("読めない", resources=rows)).status)

    def test_cap_is_enforced_on_bytes_actually_read(self):
        import stat as stat_module
        from types import SimpleNamespace

        class Stream:   # 大きさは 0 と申告するが、実際は上限を超えて返す
            def __init__(self, data):
                self.data = data
            def read(self, n=-1):
                chunk, self.data = self.data[:n], self.data[n:]
                return chunk
            def fileno(self):
                return 0
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        regular = SimpleNamespace(st_mode=stat_module.S_IFREG, st_size=0)
        with mock.patch.object(dt, "MAX_HASH_BYTES", 8), mock.patch.object(dt.os, "fstat", return_value=regular):
            with mock.patch.object(Path, "open", return_value=Stream(b"x" * 32)):
                self.assertEqual((None, True), dt._capped_sha256(Path("any")))
            with mock.patch.object(Path, "open", return_value=Stream(b"x" * 8)):
                sha, skipped = dt._capped_sha256(Path("any"))
            self.assertEqual((hashlib.sha256(b"x" * 8).hexdigest(), False), (sha, skipped))
            # 通常のファイルでないものは、ハッシュを取らない
            fifo = SimpleNamespace(st_mode=stat_module.S_IFIFO, st_size=0)
            with mock.patch.object(dt.os, "fstat", return_value=fifo), mock.patch.object(Path, "open", return_value=Stream(b"x")):
                self.assertEqual((None, True), dt._capped_sha256(Path("any")))

    def test_huge_unreadable_file_and_guide_are_not_usable(self):
        if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() == 0:
            self.skipTest("権限で読めなくする試験は、この環境ではできない")
        huge = self.dir / "huge-locked.bin"
        with huge.open("wb") as f:
            f.truncate(dt.MAX_HASH_BYTES + 1)
        folder = self.dir / "guided"
        folder.mkdir()
        guide = folder / "SKILL.md"
        with guide.open("wb") as f:
            f.truncate(dt.MAX_HASH_BYTES + 1)
        for p in (huge, guide):
            p.chmod(0)
            self.addCleanup(p.chmod, 0o600)
        rep = dt.resource_report([self.row("a", "巨大で読めない", huge), self.row("b", "説明書きが読めない", folder, "directory")])
        self.assertEqual([False, False], [r["exists"] for r in rep])
        t = self.create(doc(resources=[self.row("a", "巨大で読めない", huge)]))
        j = self.fx.get(f"{BASE}/{t['template_id']}/compiled").json()
        self.assertEqual((False, False), (j["resources"][0]["exists"], j["can_prepare"]))
        huge.chmod(0o600)   # 読めれば、省いたうえで有効
        self.assertEqual((True, True), (lambda r: (r["exists"], r["hash_skipped"]))(dt.resource_report([self.row("a", "x", huge)])[0]))

    def test_report_survives_an_oserror_while_hashing(self):
        f = self.dir / "f.md"
        f.write_text("x", encoding="utf-8")
        with mock.patch.object(dt, "_capped_sha256", side_effect=OSError("boom")):
            rep = dt.resource_report([self.row("a", "f", f)])
        self.assertEqual((False, None), (rep[0]["exists"], rep[0]["sha256"]))


class FlagTests(Base):
    def test_overwrite_must_be_a_real_boolean(self):
        t = self.create()
        for bad in ("false", "true", [False], 1, 0, None):
            r = self.save(t, self.revised(t, instructions="x"), mode="conversation", overwrite=bad,
                          origin_runtime="claude_code", conversation_key="c")
            self.assertEqual((400, "validation_error"), (r.status, r.error_code), repr(bad))
            p = self.fx.post(f"{BASE}/plan", {"template_id": t["template_id"], "mode": "conversation", "overwrite": bad,
                                              "document": self.revised(t, instructions="x"),
                                              "expected_revision_id": t["current_revision_id"]})
            self.assertEqual(400, p.status, repr(bad))
        self.assertEqual(1, self.rows())
        ok = self.save(t, self.revised(t, instructions="x"), mode="conversation", overwrite=True,
                       origin_runtime="claude_code", conversation_key="c")
        self.assertEqual((200, "overwritten"), (ok.status, ok.json()["action"]))   # 最新を明示的に上書き
        absent = self.save(ok.json(), self.revised(ok.json(), instructions="y"), mode="conversation",
                           origin_runtime="codex", conversation_key="d")
        self.assertEqual("new_version", absent.json()["action"])   # 省略は False


class SecurityTests(Base):
    def posts(self):
        t = self.create()
        tid = t["template_id"]
        body = {"expected_revision_id": t["current_revision_id"]}
        return [(BASE, {"brand_id": self.bid, "document": doc()}), (f"{BASE}/{tid}", {**body, "document": t["document"], "mode": "overwrite"}),
                (f"{BASE}/{tid}/copy", body), (f"{BASE}/{tid}/delete", body),
                (f"{BASE}/import/check", {"text": self.markdown(), "brand_id": self.bid}),
                (f"{BASE}/import", {"text": self.markdown(), "brand_id": self.bid, "mode": "create"}),
                (f"{BASE}/plan", {"brand_id": self.bid, "document": doc()}),
                ("/api/settings/resource-path/pick?kind=file", {})]

    def test_token_missing_or_wrong_is_refused_and_nothing_written(self):
        posts = self.posts()
        before = self.snapshot()
        for path, body in posts:
            r = self.fx.request("POST", path, body)
            self.assertEqual((403, "session_required"), (r.status, r.error_code), path)
            r = self.fx.request("POST", path, body, headers={"X-Quick-Slide-Session": "wrong"})
            self.assertEqual((403, "session_invalid"), (r.status, r.error_code), path)
        self.assertEqual(before, self.snapshot())

    def test_bad_origin_and_bad_host_are_refused(self):
        posts = self.posts()
        before = self.snapshot()
        for path, body in posts:
            r = self.fx.request("POST", path, body, session=True, headers={"Origin": "http://evil.example"})
            self.assertEqual((403, "bad_origin"), (r.status, r.error_code), path)
            r = self.fx.request("POST", path, body, session=True, host="evil.example")
            self.assertEqual(421, r.status, path)
        self.assertEqual(before, self.snapshot())

    def test_reads_do_not_need_a_token_but_check_host(self):
        t = self.create()
        self.assertEqual(200, self.fx.get(f"{BASE}/{t['template_id']}/markdown").status)
        self.assertEqual(421, self.fx.get(f"{BASE}/{t['template_id']}", host="evil.example").status)

    def test_bodies_and_paths_are_not_logged(self):
        from helpers import capture_logs
        logs = capture_logs(self)
        self.create(doc("秘密の名前", instructions="ひみつの指示本文", resources=[
            {"id": "r1", "name": "素材", "kind": "directory", "path": "~/secret-folder-name", "usage": ""}]))
        self.assertEqual(400, self.fx.post(BASE, {"brand_id": self.bid, "document": doc("秘密の名前2", instructions="ひみつの指示本文2",
                                                                                  modes=["x"])}).status)
        for word in ("ひみつの指示本文", "secret-folder-name", "秘密の名前"):
            self.assertNotIn(word, logs.text())


class PickTests(Base):
    def test_same_machine_check(self):
        self.assertTrue(routes.same_machine("127.0.0.1", "127.0.0.1"))
        self.assertTrue(routes.same_machine("::1", "127.0.0.1"))
        self.assertTrue(routes.same_machine("100.64.0.5", "100.64.0.5"))
        self.assertFalse(routes.same_machine("100.64.0.9", "100.64.0.5"))
        self.assertFalse(routes.same_machine("192.168.1.20", "127.0.0.1"))
        # 書き方の違い: 展開した IPv6・IPv4 に写した形・127/8 のほかのアドレスもループバック
        for client in ("0:0:0:0:0:0:0:1", "0000:0000:0000:0000:0000:0000:0000:0001", "::ffff:127.0.0.1", "::ffff:7f00:1",
                       "127.0.0.2", "::ffff:127.0.0.9"):
            self.assertTrue(routes.same_machine(client, "100.64.0.5"), client)
        # 待ち受けのアドレスとの一致も、書き方をそろえてから比べる
        self.assertTrue(routes.same_machine("::ffff:100.64.0.5", "100.64.0.5"))
        self.assertTrue(routes.same_machine("fd7a:115c:a1e0:0:0:0:0:1", "fd7a:115c:a1e0::1"))
        self.assertTrue(routes.same_machine("fe80::1%en0", "fe80::1"))
        # 別の機械・読めない値・空は断る
        for client in ("::2", "::ffff:100.64.0.9", "localhost", "evil", "", "127.0.0.1.evil", "0.0.0.0"):
            self.assertFalse(routes.same_machine(client, "100.64.0.5"), client)
        self.assertFalse(routes.same_machine("100.64.0.5", ""))
        self.assertFalse(routes.same_machine("100.64.0.5", "not-an-address"))

    def test_forwarding_headers_are_not_trusted(self):
        seen = []
        with mock.patch.object(routes, "same_machine", side_effect=lambda client, listen: seen.append(client) or False):
            r = self.fx.post("/api/settings/resource-path/pick?kind=file", {},
                             headers={"X-Forwarded-For": "127.0.0.1", "Forwarded": "for=127.0.0.1", "X-Real-IP": "127.0.0.1"})
        self.assertEqual(403, r.status)
        self.assertEqual(["127.0.0.1"], seen)   # 見るのは接続元だけ（この試験の接続元がたまたま手元）

    def test_refused_for_other_machines_and_never_opens_a_dialog(self):
        with mock.patch.object(routes, "same_machine", return_value=False), mock.patch.object(routes, "pick_path") as pick:
            r = self.fx.post("/api/settings/resource-path/pick?kind=file", {})
        self.assertEqual((403, "pick_not_local"), (r.status, r.error_code))
        self.assertIn("パスを入力", r.json()["error"]["message"])
        pick.assert_not_called()

    def test_local_pick_uses_the_dialog_function(self):
        with mock.patch.object(routes, "pick_path", return_value="/tmp/a.md") as pick:
            r = self.fx.post("/api/settings/resource-path/pick?kind=file", {})
            self.assertEqual({"path": "/tmp/a.md", "cancelled": False}, r.json())
            pick.assert_called_once_with("file")
        with mock.patch.object(routes, "pick_path", return_value=None):
            self.assertTrue(self.fx.post("/api/settings/resource-path/pick?kind=directory", {}).json()["cancelled"])
        with mock.patch.object(routes, "pick_path") as pick:
            self.assertEqual(400, self.fx.post("/api/settings/resource-path/pick?kind=x", {}).status)
            pick.assert_not_called()

    def test_unsupported_os_asks_for_manual_input(self):
        with mock.patch.object(routes.sys, "platform", "win32"), mock.patch.object(routes.subprocess, "run") as run:
            r = self.fx.post("/api/settings/resource-path/pick?kind=file", {})
        self.assertEqual((400, "pick_unsupported"), (r.status, r.error_code))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
