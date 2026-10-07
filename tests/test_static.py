"""I1 fail-closed 静的配信 / I8 秘密を配信しない。

「この入力で通った」ではなく「配信され得るのは許可リストのファイルだけ」という性質を、
(a) 敵対的入力の表、(b) ランダム生成した数千パス、(c) 最終防衛線（realpath）の単体検査、の 3 面から示す。
"""

from __future__ import annotations

import os
import random
import unittest
import uuid
from pathlib import Path

from helpers import allowed_static_bodies, make_static_root, start_server, temp_dir

from quick_slide.server.static import STATIC_TYPES, StaticRoot, confine, decode_path, split_segments

OUTSIDE_MARKERS = (b"OUTSIDE-", b"INSIDE-DOTGIT", b"INSIDE-FAKE-KEY", b"INSIDE-FAKE-DB", b"INSIDE-TXT", b"INSIDE-TRAILING",
                   b"INSIDE-NFC", b"SQLite format 3")

# 敵対的入力の表（必ず含めるもの: .. / %2e%2e / %252e / バックスラッシュ / 絶対パス / 先頭スラッシュ複数 / NUL /
# Unicode 正規化 / 外を指す symlink / 大文字小文字 / 末尾ドット・空白 / .git config db への到達）
ADVERSARIAL = [
    "/static/../outside/secret.html",
    "/static/../../outside/secret.html",
    "/static/sub/../../outside/secret.html",
    "/static/%2e%2e/outside/secret.html",
    "/static/%2E%2E/outside/secret.html",
    "/static/..%2foutside%2fsecret.html",
    "/static/%2e%2e%2foutside%2fsecret.html",
    "/static/%252e%252e/outside/secret.html",
    "/static/%252e%252e%252foutside%252fsecret.html",
    "/static/app.js%2f..%2f..%2foutside%2fsecret.html",
    "/static/..%5coutside%5csecret.html",
    "/static/%5c..%5c..%5coutside%5csecret.html",
    "/static/..%255c..%255coutside%255csecret.html",
    "/static//outside/secret.html",
    "/static///index.html",
    "/static/%2f/outside/secret.html",
    "/static/%2fetc%2fpasswd",
    "/static//etc/passwd",
    "/static/index.html%00.png",
    "/static/index%00.html",
    "/static/%00",
    "/static/app.js%00",
    "/static/cafe%CC%81.html",  # NFD（e + 結合アクセント）
    "/static/caf%C3%A9.html",  # NFC そのもの（非 ASCII は許可しない）
    "/static/%EF%BD%89ndex.html",  # 全角 i
    "/static/INDEX.HTML",
    "/static/Index.html",
    "/static/APP.JS",
    "/static/app.JS",
    "/static/index.html.",
    "/static/index.html%20",
    "/static/trailing-space.html%20",
    "/static/trailing-dot.html.",
    "/static/.git/config",
    "/static/.git/HEAD",
    "/static/config/hmac.key",
    "/static/db/quick-slide.db",
    "/static/../config/hmac.key",
    "/static/../db/quick-slide.db",
    "/static/../.git/config",
    "/static/link.html",
    "/static/linkdir/secret.html",
    "/static/linkdir/hmac.key",
    "/static/inside-link.html",
    "/static/notes.txt",
    "/static/index.htm",
    "/static/index.html/../app.js",
    "/static/./index.html",
    "/static/sub",
    "/static/sub/",
    "/static/",
    "/static",
    "/static/index.html/",
    "/static/%2e/index.html",
    "/static/.",
    "/static/..",
    "/static/....//index.html",
    "/static/a/b/c/d/e/f/g/h/i/index.html",
    "/static/" + "a" * 300 + ".html",
    "/decks/../static/../outside/secret.html",
    "/decks/%2e%2e/config/hmac.key",
    "/config/hmac.key",
    "/db/quick-slide.db",
    "/.git/config",
    "/index.html",
    "/app.js",
    "/web/app/index.html",
    "/api/../static/../outside/secret.html",
    "/api/assets/../../config/hmac.key",
]


class StaticResolverPropertyTests(unittest.TestCase):
    """HTTP を介さず、解決関数そのものの性質。"""

    def setUp(self) -> None:
        self.tmp = temp_dir(self)
        self.root, self.outside = make_static_root(self.tmp)
        self.static = StaticRoot(self.root)

    def test_adversarial_table_resolves_to_nothing(self) -> None:
        for path in ADVERSARIAL:
            self.assertIsNone(self.static.resolve(path), path)

    def test_only_allowlisted_regular_files_resolve(self) -> None:
        for path, marker in (("/static/index.html", b"INSIDE-INDEX"), ("/static/app.js", b"INSIDE-JS"), ("/static/sub/inner.css", b"INSIDE-CSS")):
            r = self.static.resolve(path)
            self.assertIsNotNone(r, path)
            self.assertEqual(r.read(), (self.root / path[len("/static/"):]).read_bytes())
            self.assertIn(marker, r.read())
            self.assertEqual(r.content_type, STATIC_TYPES[os.path.splitext(path)[1]])

    def test_content_types_are_an_allowlist(self) -> None:
        self.assertEqual(set(STATIC_TYPES), {".html", ".css", ".js", ".svg", ".png", ".ico", ".woff2"})
        self.assertTrue(all(k == k.lower() for k in STATIC_TYPES))

    def test_decode_and_segments_reject_the_dangerous_shapes(self) -> None:
        for raw in ("/static/%252e/x", "/static/a\\b", "/static/a%5cb", "/static/a%00b", "/static/café", "/static/a b", "static/x", ""):
            self.assertIsNone(decode_path(raw), raw)
        # 1 回のデコードは許す。`..` はその後のセグメント検査で必ず落ちる
        self.assertEqual(decode_path("/static/a%2Fb.js"), "/static/a/b.js")
        self.assertEqual(decode_path("/static/%2e%2e/x"), "/static/../x")
        self.assertIsNone(self.static.resolve("/static/%2e%2e/x"))
        for rel in ("", "a//b", "/a", "a/", ".", "..", "a/..", ".git/x", "a.", "a ", "a/./b", "x" * 129, "/".join(["a"] * 9)):
            self.assertIsNone(split_segments(rel), rel)
        self.assertEqual(split_segments("a/b.c/d.js"), ["a", "b.c", "d.js"])

    def test_realpath_confinement_is_enforced_independently(self) -> None:
        """walk が破れても、最終的に開くパスは realpath でルート配下であることを confine が強制する。"""
        root = Path(os.path.realpath(self.root))
        self.assertFalse(confine(root / "link.html", root), "外を指す symlink は realpath が外なので拒否")
        self.assertFalse(confine(root / "linkdir" / "secret.html", root))
        self.assertFalse(confine(root / ".." / "outside" / "secret.html", root))
        self.assertFalse(confine(root, root), "ルート自身は配信対象ではない")
        self.assertFalse(confine(Path("/etc/passwd"), root))
        self.assertTrue(confine(root / "index.html", root))
        self.assertTrue(confine(root / "sub" / "inner.css", root))
        # 見た目は配下でも、prefix 一致ではなく commonpath で判定する（/web-app2 のような兄弟に釣られない）
        sibling = Path(str(root) + "2")
        sibling.mkdir()
        (sibling / "x.html").write_text("SIBLING", encoding="utf-8")
        self.assertFalse(confine(sibling / "x.html", root))

    def test_missing_root_serves_nothing(self) -> None:
        s = StaticRoot(self.tmp / "does-not-exist")
        self.assertFalse(s.available)
        self.assertIsNone(s.resolve("/static/index.html"))
        self.assertIsNone(s.index())

    def test_random_paths_never_yield_anything_outside_the_allowlist(self) -> None:
        """性質: どんなパスでも、返るバイト列は許可リストのファイル内容の集合に含まれる。"""
        allowed = allowed_static_bodies(self.root)
        pieces = ["index.html", "app.js", "sub/inner.css", "sub/", "/index.html", "/app.js",
                  "/", "..", ".", "%2e", "%2E", "%2f", "%5c", "\\", "%00", "%25", "static", "index", "app", "sub", "inner",
                  ".html", ".js", ".css", ".HTML", ".txt", "link", "linkdir", "secret", "outside", "config", "hmac", ".key",
                  "db", "quick-slide", ".db", ".sqlite3", ".git", "caf%C3%A9", "cafe%CC%81", " ", "%20", "a", "A", "-", "_", "~", "%C3", "%80", "?", "#"]
        rng = random.Random(20260907)
        served = 0
        for _ in range(3000):
            n = rng.randint(1, 6)
            path = "/static/" + "".join(rng.choice(pieces) for _ in range(n)) if rng.random() < 0.9 else "".join(rng.choice(pieces) for _ in range(n))
            r = self.static.resolve(path)
            if r is None:
                continue
            data = r.read()
            self.assertIn(data, allowed, f"{path!r} が許可リスト外の内容を返した: {data[:60]!r}")
            served += 1
        self.assertGreater(served, 0, "肯定例が一つも生成されないなら検査の意味が無い")


class StaticHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temp_dir(self)
        self.root, self.outside = make_static_root(self.tmp)
        self.fx = start_server(self, static_root=self.root)

    def _assert_nothing_leaked(self, body: bytes, path: str) -> None:
        for m in OUTSIDE_MARKERS:
            self.assertNotIn(m, body, f"{path!r} の応答に {m!r} が含まれた")
        self.assertNotIn(self.fx.root.hmac_key_path.read_bytes(), body, f"{path!r} の応答に hmac.key のバイト列が含まれた")

    def test_adversarial_table_all_denied_over_http(self) -> None:
        for path in ADVERSARIAL:
            try:
                r = self.fx.get(path)
            except Exception as exc:  # noqa: BLE001 - http.client が要求を組み立てられないパスは raw で送る
                out = self.fx.raw(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.fx.port}\r\n\r\n".encode("latin-1", "replace"))
                self.assertRegex(out.split(b"\r\n", 1)[0], rb"HTTP/1\.[01] (400|404)", f"{path!r} ({exc.__class__.__name__})")
                self._assert_nothing_leaked(out, path)
                continue
            self.assertIn(r.status, (400, 404), f"{path!r} -> {r.status}")
            self._assert_nothing_leaked(r.body, path)
            self.assertTrue(r.headers["content-type"].startswith("application/json"), path)

    def test_leading_double_slash_is_normalised_by_stdlib_and_still_confined(self) -> None:
        """`//static/index.html` は http.server が `/static/index.html` に正規化する（gh-87389）。外には出ない。"""
        allowed = allowed_static_bodies(self.root)
        for path in ("//static/index.html", "///static/index.html", "//static/../outside/secret.html", "//static/link.html", "//outside/secret.html"):
            r = self.fx.get(path)
            self.assertIn(r.status, (200, 400, 404), path)
            if r.status == 200:
                self.assertIn(r.body, allowed, path)
            self._assert_nothing_leaked(r.body, path)

    def test_raw_bytes_in_request_line_are_denied(self) -> None:
        host = f"Host: 127.0.0.1:{self.fx.port}\r\n"
        for line in (b"GET /static/index.html\x00.png HTTP/1.1\r\n", b"GET /static/..\\outside\\secret.html HTTP/1.1\r\n",
                     b"GET /static/caf\xc3\xa9.html HTTP/1.1\r\n", b"GET /static/cafe\xcc\x81.html HTTP/1.1\r\n",
                     b"GET \\static\\index.html HTTP/1.1\r\n"):
            out = self.fx.raw(line + host.encode() + b"\r\n")
            status_line = out.split(b"\r\n", 1)[0]
            self.assertRegex(status_line, rb"HTTP/1\.[01] (400|404)", line)
            self._assert_nothing_leaked(out, repr(line))

    def test_allowed_files_are_served_with_allowlisted_types_and_hardening_headers(self) -> None:
        r = self.fx.get("/static/index.html")
        self.assertEqual((r.status, r.headers["content-type"]), (200, "text/html; charset=utf-8"))
        self.assertIn(b"INSIDE-INDEX", r.body)
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["referrer-policy"], "no-referrer")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])
        self.assertEqual(self.fx.get("/static/app.js").headers["content-type"], "text/javascript; charset=utf-8")
        self.assertEqual(self.fx.get("/static/sub/inner.css").headers["content-type"], "text/css; charset=utf-8")
        head = self.fx.request("HEAD", "/static/app.js")
        self.assertEqual((head.status, head.body), (200, b""))
        self.assertEqual(int(head.headers["content-length"]), len(b"// INSIDE-JS"))

    def test_index_routes_serve_the_bundled_page_only_for_well_formed_deck_ids(self) -> None:
        self.assertIn(b"INSIDE-INDEX", self.fx.get("/").body)
        self.assertIn(b"INSIDE-INDEX", self.fx.get(f"/slides/{uuid.uuid4()}").body)
        self.assertIn(b"INSIDE-INDEX", self.fx.get(f"/slides/{uuid.uuid4()}?launch=x&token=y").body)
        # `/slides` そのものは一覧画面なので断らない
        for bad in ("/slides/not-a-uuid", "/slides/", f"/slides/{uuid.uuid4()}/", f"/slides/{str(uuid.uuid4()).upper()}", f"/slides/{uuid.uuid4()}/../x"):
            self.assertEqual(self.fx.get(bad).status, 404, bad)

    def test_bundled_web_app_directory_is_served_and_contains_no_secrets_dirs(self) -> None:
        """製品同梱の web/app を実際に配信できる。config/ と db/ はその下に無い。"""
        from quick_slide.paths import WEB_APP_DIR

        self.assertTrue((WEB_APP_DIR / "index.html").is_file())
        self.assertFalse((WEB_APP_DIR / "config").exists())
        self.assertFalse((WEB_APP_DIR / "db").exists())
        for p in WEB_APP_DIR.rglob("*"):
            self.assertFalse(p.is_symlink(), f"同梱 web/app に symlink を置かない: {p}")
            if p.is_file():
                self.assertIn(p.suffix, STATIC_TYPES, f"同梱 web/app に許可リスト外の拡張子を置かない: {p}")
        fx = start_server(self)
        r = fx.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"Quick Slide", r.body)
        self.assertNotIn(b"window.open(", fx.get("/static/app.js").body)
        self.assertNotIn(b"target=\"_blank\"", r.body)



class OldScreenUrlsStillLeadSomewhereTests(unittest.TestCase):
    """`/library` と `/decks/<id>` は **301 で新しい URL へ送る**。

    この製品は会話に URL を返して開いてもらう形で動くので、過去の会話に貼られた
    `/decks/<id>` が 404 になると、利用者は**自分が作ったものへの入口を失う**。
    """

    def setUp(self) -> None:
        self.tmp = temp_dir(self)
        self.root, self.outside = make_static_root(self.tmp)
        self.fx = start_server(self, static_root=self.root)

    def test_the_old_list_url_is_sent_to_the_new_one(self):
        r = self.fx.get("/library")
        self.assertEqual(r.status, 301)
        self.assertEqual(r.headers.get("location"), "/slides")

    def test_an_old_deck_url_keeps_its_id(self):
        deck_id = str(uuid.uuid4())
        r = self.fx.get(f"/decks/{deck_id}")
        self.assertEqual(r.status, 301)
        self.assertEqual(r.headers.get("location"), f"/slides/{deck_id}")

    def test_the_launch_token_is_carried_across(self):
        """**クエリを落とさない。** 落とすと launch トークンが消え、attach が記録されない。"""
        deck_id = str(uuid.uuid4())
        r = self.fx.get(f"/decks/{deck_id}?launch=abc&token=def")
        self.assertEqual(r.status, 301)
        self.assertEqual(r.headers.get("location"), f"/slides/{deck_id}?launch=abc&token=def")

    def test_a_malformed_old_deck_url_is_not_redirected(self):
        """壊れた ID を新しい URL へ送り直さない（送っても行き先で断られるだけで、断る場所が2つになる）。"""
        for bad in ("/decks/not-a-uuid", "/decks/", "/decks"):
            self.assertEqual(self.fx.get(bad).status, 404, bad)


if __name__ == "__main__":
    unittest.main()
