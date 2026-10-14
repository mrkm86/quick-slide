"""発表の画面（見せる用）: ルート・API・回の HTML の控え・仕切りの中の文書（CSP・字体の配信）・画面の合図の形。

エンジンは偽物（`tests/fakes/fake_engine.py`）。偽物は html-slide の形の deck.html（section・data-step・assets/・file:// の字体）と、
段の数を持つ build.json を書く（ページ i の段の数は i % 3）。
"""

from __future__ import annotations

import re
import unittest
import uuid

from helpers import can_symlink
from test_p4_settings import Base

from quick_slide.render import workspace as W
from quick_slide.server.app import ROUTES
from quick_slide.server.present_routes import frame_csp, frame_document
from quick_slide.server.static import PresentFiles

FAKE_FONT = b"\x00\x01\x00\x00fake-font-bytes"


def routed(path: str) -> bool:
    return any(matcher(path) is not None for _m, matcher, _h in ROUTES)


class PresentBase(Base):
    def start_with_font(self):
        self.start()
        font = self.fx.root.root / "fonts-for-test" / "Fake-Regular.ttf"
        font.parent.mkdir(parents=True, exist_ok=True)
        font.write_bytes(FAKE_FONT)
        self.font = font
        self.fx.server.tools.extra_env["FAKE_ENGINE_FONT"] = str(font)

    def make_presentation(self, **kw):
        body, run = self.make(mode="presentation", **kw)
        self.assertEqual(run["state"], "done", run["message"])
        return body["deck_id"], run


class RouteTests(unittest.TestCase):
    def test_routes_exist_and_do_not_swallow_each_other(self) -> None:
        d, r = str(uuid.uuid4()), str(uuid.uuid4())
        for p in (f"/slides/{d}/present", f"/api/decks/{d}/present", f"/present/{d}/{r}/deck.html",
                  f"/present/{d}/{r}/assets/a.png", f"/present/{d}/{r}/fonts/f.ttf"):
            self.assertTrue(routed(p), p)
        for p in (f"/present/{d.upper()}/{r}/deck.html", f"/present/{d}/deck.html", "/present/x/y/deck.html"):
            self.assertFalse(routed(p), p)

    def test_the_screen_calls_only_routed_urls(self) -> None:
        from test_screen_static import WEB
        src = (WEB / "present.js").read_text(encoding="utf-8")
        self.assertIn('"/api/decks/" + deckId + "/present"', src)
        self.assertNotIn("window.open(", src)


class PresentApiTests(PresentBase):
    def setUp(self) -> None:
        self.start_with_font()

    def test_screen_and_data(self) -> None:
        deck, run = self.make_presentation()
        page = self.fx.get(f"/slides/{deck}/present")
        self.assertEqual(page.status, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn(b"present.js", page.body)
        self.assertNotIn(b'src="/static/deck.js"', page.body)          # 編集の画面の JS は読まない（軽く）
        self.assertIn("default-src 'self'", page.headers["content-security-policy"])
        data = self.fx.get(f"/api/decks/{deck}/present").json()
        self.assertEqual((data["engine"], data["run_id"], data["edit_url"]), ("html-slide", run["id"], f"/slides/{deck}"))
        self.assertEqual([p["no"] for p in data["pages"]], [1, 2, 3, 4])
        self.assertEqual([p["steps"] for p in data["pages"]], [1, 2, 0, 1])      # build.json の steps
        self.assertEqual([p["section"] for p in data["pages"]], [0, 1, 2, 3])
        self.assertEqual(data["html"]["url"], f"/present/{deck}/{run['id']}/deck.html")
        for p in data["pages"]:
            self.assertEqual(self.fx.get(p["image_url"]).status, 200)

    def test_hidden_pages_are_left_out(self) -> None:
        from helpers import wait_until_idle
        deck, _ = self.make_presentation()
        pages = self.fx.get(f"/api/decks/{deck}").json()["pages"]
        r = self.fx.post(f"/api/decks/{deck}/pages/{pages[1]['slide_id']}/visibility", {"hidden": True})
        self.assertEqual(r.status, 200, r.body)
        wait_until_idle(self.fx, deck)
        data = self.fx.get(f"/api/decks/{deck}/present").json()
        self.assertEqual(len(data["pages"]), 3)
        self.assertNotIn(pages[1]["slide_id"], [p["slide_id"] for p in data["pages"]])
        self.assertEqual([p["no"] for p in data["pages"]], [1, 2, 3])
        # section の番号は回の HTML の並び（隠したページは回に入らないので、番号は詰まる）
        self.assertEqual([p["section"] for p in data["pages"]], [0, 1, 2])

    def test_no_pages_yet_and_unknown_deck(self) -> None:
        empty = self.fx.make_deck("ページの無いスライド")
        data = self.fx.get(f"/api/decks/{empty['id']}/present").json()
        self.assertEqual((data["pages"], data["html"]), ([], None))
        self.assertTrue(data["message"])
        self.assertEqual(self.fx.get(f"/api/decks/{uuid.uuid4()}/present").status, 404)

    def test_capabilities_carry_the_labels(self) -> None:
        deck, _ = self.make_presentation()
        caps = self.fx.get(f"/api/decks/{deck}").json()["capabilities"]
        self.assertEqual((caps["present"]["available"], caps["present"]["label"]), (True, "発表"))
        self.assertEqual(caps["present_from_here"]["label"], "ここから発表")
        empty = self.fx.make_deck("ページの無いスライド")
        caps = self.fx.get(f"/api/decks/{empty['id']}").json()["capabilities"]
        self.assertFalse(caps["present"]["available"])
        self.assertTrue(caps["present"]["reason"])


class PresentRunHtmlTests(PresentBase):
    def setUp(self) -> None:
        self.start_with_font()

    def test_the_run_keeps_its_html_images_and_fonts(self) -> None:
        deck, run = self.make_presentation()
        folder = self.fx.root.run_dir(deck, run["id"]) / W.PRESENT
        self.assertTrue((folder / "deck.html").is_file())
        self.assertTrue((folder / "assets" / "s01-image.png").is_file())
        fonts = list((folder / "fonts").iterdir())
        self.assertEqual([f.read_bytes() for f in fonts], [FAKE_FONT])
        self.assertEqual(fonts[0].name, W.present_font_name(str(self.font)))

    def test_each_run_keeps_its_own_copy(self) -> None:
        """次の回でエンジンが作業フォルダの出力を作り直しても、前の回の控えは変わらない。"""
        from helpers import wait_until_idle
        deck, first = self.make_presentation()
        before = (self.fx.root.run_dir(deck, first["id"]) / W.PRESENT / "deck.html").read_bytes()
        pages = self.fx.get(f"/api/decks/{deck}").json()["pages"]
        self.fx.post(f"/api/decks/{deck}/pages/{pages[0]['slide_id']}/visibility", {"hidden": True})
        second = wait_until_idle(self.fx, deck)
        self.assertNotEqual(second["id"], first["id"])
        again = (self.fx.root.run_dir(deck, first["id"]) / W.PRESENT / "deck.html").read_bytes()
        self.assertEqual(before, again)
        new = (self.fx.root.run_dir(deck, second["id"]) / W.PRESENT / "deck.html").read_text(encoding="utf-8")
        self.assertEqual(new.count("<section"), 3)
        self.assertEqual(before.decode().count("<section"), 4)

    def test_old_runs_without_html_fall_back_to_page_images(self) -> None:
        import shutil
        deck, run = self.make_presentation()
        shutil.rmtree(self.fx.root.run_dir(deck, run["id"]) / W.PRESENT)
        data = self.fx.get(f"/api/decks/{deck}/present").json()
        self.assertIsNone(data["html"])
        self.assertEqual(len(data["pages"]), 4)
        self.assertEqual({p["steps"] for p in data["pages"]}, {0})
        self.assertEqual(self.fx.get(f"/present/{deck}/{run['id']}/deck.html").status, 404)


class PresentDocumentTests(PresentBase):
    def setUp(self) -> None:
        self.start_with_font()
        self.deck, self.run = self.make_presentation()
        self.base = f"/present/{self.deck}/{self.run['id']}"

    def test_document_has_the_strict_csp_and_the_player(self) -> None:
        r = self.fx.get(f"{self.base}/deck.html")
        self.assertEqual(r.status, 200)
        csp = r.headers["content-security-policy"]
        self.assertEqual(csp, frame_csp(f"127.0.0.1:{self.fx.port}"))
        self.assertIn("default-src 'none'", csp)
        self.assertIn(f"script-src http://127.0.0.1:{self.fx.port}/static/present-frame.js;", csp)
        self.assertIn("sandbox allow-scripts", csp)
        self.assertNotIn("allow-same-origin", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertIn("connect-src", csp) if False else None
        body = r.body.decode()
        self.assertIn('<script src="/static/present-frame.js"></script></body>', body)
        self.assertNotIn("file://", body)
        self.assertEqual(self.fx.get("/static/present-frame.js").status, 200)

    def test_font_is_served_from_the_run_copy_only(self) -> None:
        body = self.fx.get(f"{self.base}/deck.html").body.decode()
        m = re.search(r'url\("(/present/[^"]+/fonts/[0-9a-f]{16}\.ttf)"\)', body)
        self.assertTrue(m, body[:400])
        r = self.fx.get(m.group(1))
        self.assertEqual((r.status, r.body, r.headers["content-type"]), (200, FAKE_FONT, "font/ttf"))
        self.assertEqual(r.headers["access-control-allow-origin"], "*")
        # 画像は CORS ヘッダ無し・字体以外の拡張子や任意のパスは読めない
        self.assertEqual(self.fx.get(f"{self.base}/assets/s01-image.png").status, 200)
        self.assertNotIn("access-control-allow-origin", self.fx.get(f"{self.base}/assets/s01-image.png").headers)
        for bad in ("fonts/0000000000000000.ttf", "fonts/..%2fdeck.html", "fonts/%2e%2e/deck.html", "assets/../deck.html",
                    "fonts/Fake-Regular.ttf", "build.json", "deck-info.json", "../build.json", "assets/s01-image.png.txt",
                    "fonts/x.html", "assets", "fonts", ""):
            self.assertEqual(self.fx.get(f"{self.base}/{bad}").status, 404, bad)

    def test_font_not_kept_is_not_pointed_at(self) -> None:
        html = '<style>@font-face { src: url("file:///etc/passwd.ttf"); }</style></body>'
        out = frame_document(html, "/present/d/r", self.fx.root.run_dir(self.deck, self.run["id"]) / W.PRESENT / "fonts")
        self.assertNotIn("/etc/passwd", out)
        self.assertNotIn("file://", out)

    def test_other_decks_run_and_symlinks_are_refused(self) -> None:
        other, _ = self.make_presentation(title="別のスライド")
        self.assertEqual(self.fx.get(f"/present/{other}/{self.run['id']}/deck.html").status, 404)
        self.assertEqual(self.fx.get(f"/present/{self.deck}/{uuid.uuid4()}/deck.html").status, 404)
        folder = self.fx.root.run_dir(self.deck, self.run["id"]) / W.PRESENT
        if can_symlink():
            secret = self.fx.root.root / "outside.png"
            secret.write_bytes(b"SECRET")
            (folder / "assets" / "link.png").symlink_to(secret)
            self.assertEqual(self.fx.get(f"{self.base}/assets/link.png").status, 404)

    def test_editor_screen_csp_is_unchanged(self) -> None:
        r = self.fx.get(f"/slides/{self.deck}")
        self.assertEqual(r.headers["content-security-policy"], "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'")

    def test_files_class_confines_to_the_present_folder(self) -> None:
        files = PresentFiles(self.fx.root.root)
        self.assertIsNotNone(files.resolve(self.deck, self.run["id"], "deck.html"))
        self.assertIsNone(files.resolve(self.deck, self.run["id"], "../runs"))
        self.assertIsNone(files.resolve(self.deck, self.run["id"].upper(), "deck.html"))


class FrameMessageContractTests(unittest.TestCase):
    """仕切りの中と外枠の合図の形（JS の文面を固定する。動かす試験は tests/live/test_present.py）。"""

    def test_both_sides_check_sender_origin_and_shape(self) -> None:
        from test_screen_static import WEB
        outer = (WEB / "present.js").read_text(encoding="utf-8")
        inner = (WEB / "present-frame.js").read_text(encoding="utf-8")
        self.assertIn('setAttribute("sandbox", "allow-scripts")', outer)
        self.assertNotIn("allow-same-origin", outer)
        self.assertIn('e.source !== frame.contentWindow || e.origin !== "null"', outer)
        self.assertIn('d.qs === "present" && d.v === 1', outer)
        self.assertIn("e.source !== window.parent || e.origin !== parentOrigin", inner)
        self.assertIn('d.qs !== TAG || d.v !== VERSION', inner)
        self.assertNotIn("fetch(", inner)              # 仕切りの中は外へ何も取りに行かない
        self.assertNotIn("XMLHttpRequest", inner)


if __name__ == "__main__":
    unittest.main()
