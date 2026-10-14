"""I7（トレースバックも英語の例外も返さない）/ I8（hmac.key と asset 配信）/ I9（Host / Origin / セッション・bind 先）。"""

from __future__ import annotations

import json
import os
import unittest
import uuid

from helpers import can_symlink, capture_logs, make_static_root, start_server, temp_dir

from quick_slide.errors import StartupError
from quick_slide.paths import DataRoot
from quick_slide.server.app import SESSION_HEADER, QuickSlideServer
from quick_slide.settings import ServerSettings, Settings


class OriginHostSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = start_server(self)
        self.deck = self.fx.make_deck()

    def test_host_header_is_checked_on_every_request(self) -> None:
        port = self.fx.port
        for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"LOCALHOST:{port}", f"[::1]:{port}"):
            self.assertEqual(self.fx.get("/api/health", host=host).status, 200, host)
        for host in ("evil.example", f"evil.example:{port}", f"127.0.0.1:{port + 1}", "127.0.0.1", "",
                     f"127.0.0.1.evil.example:{port}", f"localhost.evil:{port}", f"127.0.0.1:{port}.evil"):
            r = self.fx.get("/api/health", host=host)
            self.assertEqual((r.status, r.error_code), (421, "bad_host"), host)
        # Host 無し（HTTP/1.0 風）も拒否
        out = self.fx.raw(b"GET /api/health HTTP/1.1\r\n\r\n")
        self.assertIn(b" 421 ", out.split(b"\r\n", 1)[0])

    def test_mutations_require_session_token_and_same_origin(self) -> None:
        path = f"/api/decks/{self.deck['id']}/duplicate"
        good_origin = f"http://127.0.0.1:{self.fx.port}"
        # セッション無し
        r = self.fx.request("POST", path, {})
        self.assertEqual((r.status, r.error_code), (403, "session_required"))
        # セッション不一致
        r = self.fx.request("POST", path, {}, {SESSION_HEADER: "wrong"})
        self.assertEqual((r.status, r.error_code), (403, "session_invalid"))
        # 他サイトの Origin（セッションが正しくても拒否）
        for origin in ("https://evil.example", "null", "http://127.0.0.1:1", f"http://evil.example:{self.fx.port}",
                       f"https://127.0.0.1:{self.fx.port}"):
            r = self.fx.post(path, {}, headers={"Origin": origin})
            self.assertEqual((r.status, r.error_code), (403, "bad_origin"), origin)
        # 拒否された要求は何も書いていない
        self.assertEqual(self.fx.store.search_decks()["total"], 1)
        # 同一オリジン + セッション → 通る
        r = self.fx.post(path, {}, headers={"Origin": good_origin})
        self.assertEqual(r.status, 201, r)
        r = self.fx.post(path, {}, headers={"Origin": f"http://localhost:{self.fx.port}"}, host=f"localhost:{self.fx.port}")
        self.assertEqual(r.status, 201, r)
        self.assertEqual(self.fx.store.search_decks()["total"], 3)
        # GET は安全側: セッション不要
        self.assertEqual(self.fx.get("/api/health").status, 200)
        # /api/session は cross-site の Sec-Fetch-Site で拒否
        r = self.fx.get("/api/session", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual((r.status, r.error_code), (403, "bad_origin"))
        self.assertEqual(self.fx.get("/api/session", headers={"Sec-Fetch-Site": "same-origin"}).status, 200)
        # stop もセッション必須
        r = self.fx.request("POST", "/api/server/stop", {})
        self.assertEqual(r.status, 403)
        self.assertEqual(self.fx.get("/api/health").status, 200, "拒否された stop でサーバは止まらない")

    def test_not_ready_routes_also_require_the_session(self) -> None:
        r = self.fx.request("POST", "/api/conversations/deck", {})
        self.assertEqual((r.status, r.error_code), (403, "session_required"))
        r = self.fx.request("POST", "/api/intakes", {})
        self.assertEqual((r.status, r.error_code), (403, "session_required"))

    def test_session_endpoint_checks_origin_not_just_sec_fetch_site(self) -> None:
        token = self.fx.session()
        for origin in ("https://evil.example.com", "null", f"http://127.0.0.1:{self.fx.port + 1}", "http://localhost.evil.com"):
            for extra in ({}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}):
                r = self.fx.get("/api/session", headers={"Origin": origin, **extra})
                self.assertEqual((r.status, r.error_code), (403, "bad_origin"), (origin, extra))
                self.assertNotIn(token, r.body.decode("utf-8"), (origin, extra))
        self.assertEqual(self.fx.get("/api/session").status, 200)
        self.assertEqual(self.fx.get("/api/session", headers={"Origin": f"http://127.0.0.1:{self.fx.port}"}).status, 200)
        r = self.fx.get("/api/session", headers={"Origin": f"http://localhost:{self.fx.port}"}, host=f"localhost:{self.fx.port}")
        self.assertEqual(r.status, 200)

    def test_unknown_routes_and_methods(self) -> None:
        self.assertEqual(self.fx.get("/api/nope").error_code, "not_found")
        self.assertEqual(self.fx.request("DELETE", "/api/health", raw_body=b"").status, 405)
        self.assertEqual(self.fx.request("POST", "/api/health", {}, session=True).status, 405)
        r = self.fx.request("GET", "/api/intakes")
        self.assertEqual((r.status, r.error_code), (405, "method_not_allowed"))
        r = self.fx.request("POST", "/api/intakes", session=True)  # Content-Length 無し
        self.assertEqual((r.status, r.error_code), (411, "length_required"))
        r = self.fx.request("POST", "/api/intakes", session=True, headers={"Content-Length": "99999999999"}, raw_body=b"")
        self.assertEqual((r.status, r.error_code), (413, "body_too_large"))


class BindTests(unittest.TestCase):
    """「ぜんぶの口」と名前には開かない。どこに開いたかを黙らないことは `test_bind_address.py` が見る。"""

    def test_the_wide_open_bind_is_refused_not_silently_downgraded(self) -> None:
        tmp = temp_dir(self)
        for host in ("0.0.0.0", "::", "example.com", "127.1"):
            with self.assertRaises(StartupError, msg=host):
                QuickSlideServer(DataRoot(tmp / "home"), settings=Settings(), host=host, port=0)
        root = DataRoot(tmp / "home2")
        root.ensure()
        root.settings_path.write_text(json.dumps({"server": {"host": "0.0.0.0", "port": 0}}), encoding="utf-8")
        with self.assertRaises(StartupError):
            QuickSlideServer(root)
        self.assertFalse(root.server_json_path.exists(), "拒否した起動は server.json を作らない")

    def test_loopback_hosts_bind_and_report_the_real_address(self) -> None:
        for host in ("127.0.0.1", "localhost"):
            fx = start_server(self, host=host)
            h = fx.get("/api/health").json()
            self.assertEqual((h["host"], h["port"]), (host, fx.port))
            self.assertTrue(fx.server.url.startswith(f"http://{host}:"))

    def test_server_settings_validation(self) -> None:
        with self.assertRaises(Exception):
            ServerSettings(host="", port=1)
        with self.assertRaises(Exception):
            ServerSettings(port=70000)
        self.assertTrue(ServerSettings().is_loopback)
        self.assertFalse(ServerSettings(host="0.0.0.0").is_loopback)


class NoTracebackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logs = capture_logs(self)
        self.fx = start_server(self)

    def test_unexpected_exception_yields_generic_japanese_500_with_correlation_id(self) -> None:
        secret_detail = "boom internal detail marker"
        original = self.fx.store.launch

        def broken(_id):
            raise RuntimeError(secret_detail)

        self.fx.store.launch = broken  # type: ignore[assignment]
        try:
            r = self.fx.get(f"/api/launches/{uuid.uuid4()}")
        finally:
            self.fx.store.launch = original  # type: ignore[assignment]
        self.assertEqual(r.status, 500)
        body = r.json()["error"]
        self.assertEqual(body["code"], "internal_error")
        self.assertIn("予期しない問題", body["message"])
        text = r.body.decode("utf-8")
        for forbidden in ("Traceback", "RuntimeError", secret_detail, "File \"", "Exception"):
            self.assertNotIn(forbidden, text)
        cid = body["correlation_id"]
        self.assertRegex(cid, r"^[0-9a-f]{12}$")
        log_text = self.logs.text()
        self.assertIn(cid, log_text, "相関 ID はローカルログにある")
        self.assertIn(secret_detail, log_text, "詳細はローカルログにだけある")
        self.assertIn("Traceback", log_text)
        self.assertEqual(self.fx.get("/api/health").status, 200)

    def test_malformed_request_lines_get_japanese_json_not_english_html(self) -> None:
        for raw in (b"GARBAGE\r\n\r\n", b"GET /api/health HTTP/9.9\r\nHost: x\r\n\r\n", b"GET\r\n\r\n",
                    b"GET /api/health HTTP/1.1\r\n" + b"X-Long: " + b"a" * 70000 + b"\r\n\r\n"):
            out = self.fx.raw(raw)
            status_line, _, rest = out.partition(b"\r\n")
            self.assertRegex(status_line, rb"HTTP/1\.[01] (400|414|431|505)", raw[:30])
            body = rest.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in rest else b""
            self.assertIn(b"application/json", rest)
            self.assertNotIn(b"<html", rest.lower())
            self.assertNotIn(b"Bad request", rest)
            self.assertNotIn(b"Error response", rest)
            self.assertIn("error", json.loads(body.decode("utf-8")))

    def test_store_errors_map_to_japanese_json(self) -> None:
        r = self.fx.get("/api/decks?status=nope")
        self.assertEqual((r.status, r.error_code), (400, "validation_error"))
        self.assertNotIn("Traceback", r.body.decode("utf-8"))
        self.assertNotIn("ValidationError", r.body.decode("utf-8"))

    def test_not_ready_routes_answer_in_the_error_shape_and_log_no_error(self) -> None:
        r = self.fx.post(f"/api/decks/{uuid.uuid4()}/generate", {"format": "pdf"})
        self.assertEqual(r.status, 503)
        err = r.json()["error"]
        self.assertEqual(err["code"], "not_ready_in_v2")
        self.assertIn("v2 ではまだ使えません", err["message"])
        self.assertNotIn("correlation_id", err, "想定どおりの断りに相関 ID は付けない")
        self.assertNotIn("ERROR", self.logs.text())


class SecretsNeverServedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)
        self.static_root, _ = make_static_root(self.tmp)
        self.fx = start_server(self, static_root=self.static_root)
        self.key = self.fx.root.hmac_key_path.read_bytes()
        self.assertEqual(len(self.key), 32)

    def test_hmac_key_bytes_and_db_never_appear_in_any_response(self) -> None:
        from test_static import ADVERSARIAL

        deck = self.fx.make_deck()
        launch_id, _token = self.fx.make_launch(deck["id"])
        for path in ADVERSARIAL + ["/api/health", "/api/session", f"/api/launches/{launch_id}", "/", f"/decks/{deck['id']}",
                                   f"/api/decks/{deck['id']}", "/api/settings",
                                   "/api/assets/../../config/hmac.key", f"/api/assets/{uuid.uuid4()}/file"]:
            try:
                self.fx.get(path)
            except Exception:  # noqa: BLE001 - http.client が組み立てられないパスは対象外（test_static が raw で見る）
                continue
        for b in self.fx.responses:
            self.assertNotIn(self.key, b)
            self.assertNotIn(b"SQLite format 3", b)

    def _register(self, relative_path: str, media_type: str = "image/png", *, bytes_: int | None = None, state: str = "ready") -> str:
        store = self.fx.store
        with store.transaction():
            a = store.register_asset(relative_path=relative_path, media_type=media_type, sha256="a" * 64,
                                     bytes_=bytes_ if bytes_ is not None else 0, width=1, height=1, state=state)
        return a["id"]

    def test_assets_are_served_only_by_id_and_only_from_the_assets_dir(self) -> None:
        root = self.fx.root
        png = b"\x89PNG\r\n\x1a\nFAKE-PNG-BODY"
        good_dir = root.assets_dir / str(uuid.uuid4())
        good_dir.mkdir()
        (good_dir / "original.png").write_bytes(png)
        good = self._register(f"assets/{good_dir.name}/original.png", bytes_=len(png))
        r = self.fx.get(f"/api/assets/{good}/file")
        self.assertEqual((r.status, r.headers["content-type"], r.body), (200, "image/png", png))
        self.assertEqual(self.fx.request("HEAD", f"/api/assets/{good}/file").status, 200)

        key_row = self._register("config/hmac.key", bytes_=32)
        r = self.fx.get(f"/api/assets/{key_row}/file")
        self.assertEqual(r.status, 404)
        self.assertNotIn(self.key, r.body)
        if can_symlink():
            link_dir = root.assets_dir / str(uuid.uuid4())
            link_dir.mkdir()
            os.symlink(root.hmac_key_path, link_dir / "original.png")
            link_row = self._register(f"assets/{link_dir.name}/original.png", bytes_=32)
            r = self.fx.get(f"/api/assets/{link_row}/file")
            self.assertEqual(r.status, 404)
            self.assertNotIn(self.key, r.body)
            os.symlink(root.config_dir, root.assets_dir / "cfg")
            r = self.fx.get(f"/api/assets/{self._register('assets/cfg/hmac.key', media_type='image/png', bytes_=32)}/file")
            self.assertEqual(r.status, 404)

        def copy_of(name: str) -> str:
            d = root.assets_dir / str(uuid.uuid4())
            d.mkdir()
            (d / name).write_bytes(png)
            return f"assets/{d.name}/{name}"

        self.assertEqual(self.fx.get(f"/api/assets/{self._register(copy_of('original.png'), media_type='image/jpeg', bytes_=len(png))}/file").status, 404)
        self.assertEqual(self.fx.get(f"/api/assets/{self._register(copy_of('notes.txt'), media_type='text/plain', bytes_=len(png))}/file").status, 404)
        self.assertEqual(self.fx.get(f"/api/assets/{self._register(copy_of('original.png'), bytes_=len(png) + 1)}/file").status, 404)
        self.assertEqual(self.fx.get(f"/api/assets/{self._register(copy_of('original.png'), bytes_=len(png), state='quarantined')}/file").status, 404)
        self.assertEqual(self.fx.get(f"/api/assets/{self._register(copy_of('ORIGINAL.PNG'), bytes_=len(png))}/file").status, 404,
                         "大文字拡張子は許可しない")
        self.assertEqual(self.fx.get(f"/api/assets/{uuid.uuid4()}/file").status, 404)
        self.assertEqual(self.fx.get("/api/assets/not-a-uuid/file").status, 404)
        self.assertEqual(self.fx.get(f"/api/assets/{good}/../../config/hmac.key").status, 404)
        for b in self.fx.responses:
            self.assertNotIn(self.key, b)


if __name__ == "__main__":
    unittest.main()
