"""デザインテンプレートのリソースが、この機体にあるか（API の resource-check と `--doctor`）。中身は見ない。"""
from __future__ import annotations

import http.client
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from helpers import REPO, start_server, temp_dir

from quick_slide import design_templates as dt
from quick_slide.server import template_routes

SETUP = REPO / "skills" / "quick-slide" / "scripts" / "quick_slide_setup.py"
# どの OS でも絶対パスになる、存在しない置き場
NO_SUCH = Path(tempfile.gettempdir()).resolve() / "qs-design-template-no-such"
MISSING_KEYS = {"brand_name", "template_id", "template_name", "resource_name", "kind"}


def load_setup():
    sys.dont_write_bytecode = True   # 配る物の中に __pycache__ を残さない
    spec = importlib.util.spec_from_file_location("quick_slide_setup_for_test", SETUP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.fx = start_server(self)
        with self.fx.server.store_lock:
            store = self.fx.store
            config = json.loads(json.dumps(store.brand_revision(store.preset_brand_revision_id())["config"]))
            self.bid = store.create_brand("試験のブランド", config)["id"]
        self.here = temp_dir(self, "qs-resource-here-")

    def create(self, name, resources):
        document = dt.new_document("weekly", self.bid, name)
        document["resources"] = resources
        r = self.fx.post("/api/settings/design-templates", {"brand_id": self.bid, "document": document})
        self.assertEqual(201, r.status, r.body)
        return r.json()

    def present(self, rid="r1"):
        return {"id": rid, "name": "画像フォルダ", "kind": "directory", "path": str(self.here), "usage": ""}

    def absent(self, rid="r2", name="別の機体の資料"):
        return {"id": rid, "name": name, "kind": "file", "path": str(NO_SUCH / "notes.md"), "usage": ""}

    def check(self):
        got = self.fx.get("/api/settings/design-templates/resource-check")
        self.assertEqual(200, got.status, got.body)
        return got.json()


class ResourceCheckApiTests(Base):
    def test_reports_only_missing_resources_without_paths(self):
        self.create("週報", [self.present(), self.absent()])
        body = self.check()
        self.assertEqual({"templates", "resources", "missing"}, set(body))   # パスを返さない
        self.assertEqual((1, 2), (body["templates"], body["resources"]))
        self.assertEqual([MISSING_KEYS], [set(m) for m in body["missing"]])
        self.assertEqual([("週報", "別の機体の資料", "file")],
                         [(m["template_name"], m["resource_name"], m["kind"]) for m in body["missing"]])
        self.assertNotIn(str(NO_SUCH), json.dumps(body, ensure_ascii=False))

    def test_deleted_templates_and_older_versions_are_not_counted(self):
        gone = self.create("消したもの", [self.absent()])
        r = self.fx.post(f"/api/settings/design-templates/{gone['template_id']}/delete",
                         {"expected_revision_id": gone["current_revision_id"]})
        self.assertEqual(200, r.status, r.body)
        kept = self.create("直したもの", [self.absent()])
        document = dict(kept["document"], resources=[self.present()])
        r = self.fx.post(f"/api/settings/design-templates/{kept['template_id']}",
                         {"document": document, "mode": "new_version", "expected_revision_id": kept["current_revision_id"]})
        self.assertEqual(200, r.status, r.body)
        body = self.check()
        self.assertEqual((1, 1, []), (body["templates"], body["resources"], body["missing"]))

    def test_existence_is_checked_outside_the_store_lock(self):
        self.create("週報", [self.absent()])
        lock = self.fx.server.store_lock
        free = []

        def presence(resources):
            # 別のスレッドから鍵が取れるなら、この確認は鍵の外で動いている
            got = []

            def probe():
                ok = lock.acquire(timeout=1)
                if ok:
                    lock.release()   # RLock は取ったスレッドでしか放せない
                got.append(ok)

            t = threading.Thread(target=probe)
            t.start()
            t.join()
            free.append(bool(got and got[0]))
            return [False for _ in resources]

        with mock.patch.object(template_routes.dt, "resource_presence", side_effect=presence):
            self.check()
        self.assertEqual([True], free)


class DoctorTests(Base):
    def write_record(self, home: Path, **overrides):
        instance = self.fx.get("/api/health").json()["instance_id"]
        record = {"host": "127.0.0.1", "port": self.fx.port, "url": "http://not-resolvable.invalid:1/",
                  "instance_id": instance, **overrides}
        (home / "runtime").mkdir(parents=True, exist_ok=True)
        (home / "runtime" / "server.json").write_text(json.dumps(record), encoding="utf-8")

    def item(self, home: Path, setup=None):
        setup = setup or load_setup()
        rep = setup.Report()
        setup.check_template_resources(rep, home)
        return rep.get("template_resources")

    def test_warns_while_the_server_runs_even_if_the_shown_url_does_not_resolve(self):
        self.create("週報", [self.present(), self.absent()])
        home = temp_dir(self, "qs-home-")
        self.write_record(home)   # 表示用の url は引けない名前。記録の host・port に直接つなぐ
        item = self.item(home)
        self.assertEqual("warn", item["level"])
        self.assertIn("週報: 別の機体の資料", item["detail"])
        self.assertTrue(item["ok"])   # 注意にとどめ、導入の可否は変えない

    def test_all_present_is_ok(self):
        self.create("週報", [self.present()])
        home = temp_dir(self, "qs-home-")
        self.write_record(home)
        item = self.item(home)
        self.assertEqual("ok", item["level"])
        self.assertIn("この機体にあります", item["detail"])

    def test_another_instance_on_the_recorded_port_is_not_asked(self):
        self.create("週報", [self.absent()])
        home = temp_dir(self, "qs-home-")
        self.write_record(home, instance_id="someone-else")
        item = self.item(home)
        self.assertEqual("ok", item["level"])
        self.assertIn("サーバが動いていない", item["detail"])

    def test_broken_or_foreign_responses_are_not_read_as_all_present(self):
        home = temp_dir(self, "qs-home-")
        self.write_record(home, instance_id="i-1")
        ok = {"templates": 1, "resources": 1, "missing": []}
        cases = {
            "empty object": json.dumps({}), "no missing": json.dumps({"templates": 1, "resources": 1}),
            "string count": json.dumps(dict(ok, templates="1")), "bool count": json.dumps(dict(ok, resources=True)),
            "negative": json.dumps(dict(ok, templates=-1)), "missing not list": json.dumps(dict(ok, missing={})),
            "item without names": json.dumps(dict(ok, missing=[{"template_name": "週報"}])),
            "array": "[]", "null": "null", "bad json": "{oops", "bad utf-8": b"\xff\xfe{}",
            "utf-16": json.dumps(ok).encode("utf-16"), "not 200": (404, json.dumps(ok)), "201": (201, json.dumps(ok)),
            "cut short": http.client.IncompleteRead(b"{"), "cut while reading": ReadFails(http.client.IncompleteRead(b"{")),
            "timeout": TimeoutError("timed out"), "refused": ConnectionRefusedError(),
        }
        for name, answer in cases.items():
            with self.subTest(name=name):
                fake = FakeServer({"/api/health": json.dumps({"instance_id": "i-1"}),
                                   "/api/settings/design-templates/resource-check": answer})
                item = self.item_with(home, fake)
                self.assertEqual("warn", item["level"])
                self.assertIn("確かめられませんでした", item["detail"])
                self.assertEqual(["/api/health", "/api/settings/design-templates/resource-check"], fake.asked)
                self.assertTrue(fake.conns and all(c.closed for c in fake.conns))   # 断ったときも閉じる

    def test_every_connection_is_made_to_the_record_with_a_timeout_and_closed(self):
        home = temp_dir(self, "qs-home-")
        self.write_record(home, host="::1", port=8765, instance_id="i-1")
        fake = FakeServer({"/api/health": json.dumps({"instance_id": "i-1"}),
                           "/api/settings/design-templates/resource-check":
                               json.dumps({"templates": 0, "resources": 0, "missing": []})})
        self.assertEqual("ok", self.item_with(home, fake)["level"])
        self.assertEqual({("::1", 8765)}, {(c.host, c.port) for c in fake.conns})
        self.assertTrue(all(c.timeout and c.timeout <= 10 for c in fake.conns))
        self.assertEqual({"[::1]:8765"}, {c.headers.get("Host") for c in fake.conns})   # サーバの Host 検査を通る
        self.assertTrue(all(c.closed for c in fake.conns))

    def test_another_instance_is_never_asked_for_the_check(self):
        home = temp_dir(self, "qs-home-")
        self.write_record(home, instance_id="i-1")
        for name, health in (("other", json.dumps({"instance_id": "i-2"})), ("no id", json.dumps({})),
                             ("down", ConnectionRefusedError())):
            with self.subTest(name=name):
                fake = FakeServer({"/api/health": health})
                item = self.item_with(home, fake)
                self.assertEqual("ok", item["level"])
                self.assertIn("サーバが動いていない", item["detail"])
                self.assertEqual(["/api/health"], fake.asked)
                self.assertTrue(all(c.closed for c in fake.conns))

    def test_broken_records_are_not_dialled_and_do_not_stop_the_doctor(self):
        records = {"array": "[]", "host with newline": json.dumps({"host": "127.0.0.1\nx", "port": 1, "instance_id": "i"}),
                   "huge port": '{"host": "127.0.0.1", "port": 1e999, "instance_id": "i"}',
                   "port as text": json.dumps({"host": "127.0.0.1", "port": "8765", "instance_id": "i"}),
                   "port out of range": json.dumps({"host": "127.0.0.1", "port": 70000, "instance_id": "i"}),
                   "id not text": json.dumps({"host": "127.0.0.1", "port": 1, "instance_id": 3})}
        for name, content in records.items():
            with self.subTest(name=name):
                home = temp_dir(self, "qs-home-")
                (home / "runtime").mkdir(parents=True)
                (home / "runtime" / "server.json").write_text(content, encoding="utf-8")
                fake = FakeServer({})
                item = self.item_with(home, fake)
                self.assertEqual("ok", item["level"])
                self.assertIn("サーバが動いていない", item["detail"])
                self.assertEqual([], fake.asked)

    def test_neither_starts_the_server_nor_opens_the_database(self):
        # 記録が無い・壊れている・半端なときも、サーバに最後まで聞けたときも
        setup = load_setup()
        answers = {"/api/health": json.dumps({"instance_id": "i-1"}),
                   "/api/settings/design-templates/resource-check": json.dumps({"templates": 0, "resources": 0, "missing": []})}
        for name, content in (("none", None), ("corrupt", "{not json"), ("partial", json.dumps({"host": "127.0.0.1"})),
                              ("running", json.dumps({"host": "127.0.0.1", "port": 1, "instance_id": "i-1"}))):
            with self.subTest(name=name):
                home = temp_dir(self, "qs-home-")
                if content is not None:
                    (home / "runtime").mkdir(parents=True)
                    (home / "runtime" / "server.json").write_text(content, encoding="utf-8")
                fail = AssertionError("--doctor はサーバを起こさず、データベースを開かない")
                with mock.patch.object(subprocess, "Popen", side_effect=fail), \
                     mock.patch.object(subprocess, "run", side_effect=fail), \
                     mock.patch.object(sqlite3, "connect", side_effect=fail):
                    item = self.item_with(home, FakeServer(answers), setup)
                self.assertEqual("ok", item["level"])

    def test_the_whole_doctor_survives_broken_records(self):
        setup = load_setup()
        quiet = {name: mock.patch.object(setup, name, return_value=value) for name, value in (
            ("check_python", []), ("check_node", {}), ("check_browser", None), ("check_fonts", None),
            ("check_motion", None), ("check_codex", None), ("check_writers", None))}
        records = ('{"host": "\\ud800", "port": 0, "instance_id": "i"}', '{"host": "a\\u0000b", "port": 0, "instance_id": "i"}',
                   '{"host": "127.0.0.1\\nx", "port": 0, "instance_id": "i"}', '{"host": 3, "port": 0}', "[]", "{oops")
        for content in records:
            with self.subTest(record=content):
                home = temp_dir(self, "qs-home-")
                (home / "config").mkdir(parents=True)
                (home / "config" / "settings.json").write_text(json.dumps({"server": {"port": 0}}), encoding="utf-8")
                (home / "runtime").mkdir(parents=True)
                (home / "runtime" / "server.json").write_text(content, encoding="utf-8")
                for p in quiet.values():
                    p.start()
                try:
                    with mock.patch.object(http.client, "HTTPConnection", FakeServer({}).connect):
                        rep, _ = setup.doctor(home)
                finally:
                    for p in quiet.values():
                        p.stop()
                self.assertIsNotNone(rep.get("port"))
                self.assertEqual("ok", rep.get("template_resources")["level"])

    def test_an_unreadable_listen_host_is_reported_not_raised(self):
        setup = load_setup()
        for host in ("\ud800", "a\u0000b"):
            with self.subTest(host=repr(host)):
                home = temp_dir(self, "qs-home-")
                (home / "config").mkdir(parents=True)
                (home / "config" / "settings.json").write_text(
                    json.dumps({"server": {"host": host, "port": 7999}}), encoding="utf-8")
                rep = setup.Report()
                setup.check_port(rep, home)
                self.assertEqual("ng", rep.get("port")["level"])
                self.assertIn("server.host", rep.get("port")["fix"])

    def item_with(self, home: Path, fake: "FakeServer", setup=None):
        with mock.patch.object(http.client, "HTTPConnection", fake.connect):
            return self.item(home, setup)


class ReadFails:
    """答えの頭は返るが、本文を読んでいる途中で切れる。"""

    def __init__(self, error):
        self.error = error


class FakeServer:
    """http.client.HTTPConnection の代わり。道ごとの答え（本文・(状態, 本文)・投げる例外）を返し、聞かれた道を控える。"""

    def __init__(self, answers):
        self.answers, self.asked, self.conns = answers, [], []

    def connect(self, host, port, timeout=None):
        conn = FakeConnection(self, host, port, timeout)
        self.conns.append(conn)
        return conn


class FakeConnection:
    def __init__(self, server, host, port, timeout):
        self.server, self.host, self.port, self.timeout = server, host, port, timeout
        self.headers, self.path, self.closed = {}, None, False

    def request(self, method, path, headers=None):
        self.server.asked.append(path)
        self.path, self.headers = path, dict(headers or {})
        if path not in self.server.answers:
            raise ConnectionRefusedError()

    def getresponse(self):
        answer = self.server.answers[self.path]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, ReadFails):
            return mock.Mock(status=200, read=mock.Mock(side_effect=answer.error))
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        body = body.encode("utf-8") if isinstance(body, str) else body
        return mock.Mock(status=status, read=mock.Mock(return_value=body))

    def close(self):
        self.closed = True

if __name__ == "__main__":
    unittest.main()
