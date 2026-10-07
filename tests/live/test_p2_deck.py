"""P2 の確かめ: **本物のブラウザ**で、会話からの投入（偽物の台本係・本物の image-slide・絵は偽物の codex）を
描いている最中にスライドの画面を開き、待っている間は帯が「いま何をしているか」を言い、描き終えたら
**画面を読み込み直さなくても**ページの画像が出ること。JavaScript の例外が 1 つも出ないこと。ホームのカードに先頭の絵。

Chrome が無い環境では skip する。
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from helpers import fake_tools, intake_payload, start_server  # noqa: E402

from quick_slide.settings import Settings, WorkerSettings  # noqa: E402

from . import browser  # noqa: E402


class DeckScreenShowsDrawnPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        tools, _ = fake_tools(self, extra={"FAKE_CODEX_SLEEP": "3"})
        self.fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def test_pages_appear_while_the_screen_is_open(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(runtime="codex", mode="pitch"))
        self.assertEqual(r.status, 201, r.body)
        body = r.json()
        url = body["url"].split("://", 1)[1].split("/", 1)[1]
        page = self.br.open(self.fx.base_url + "/" + url)
        page.until("!!document.querySelector('.deck-screen')", what="スライドの画面")
        said = page.until("(function(){ var t = document.querySelector('.planning-band__text');"
                          " return t && t.textContent; })()", seconds=20, what="待っている間の帯の 1 文")
        self.assertTrue(any(w in said for w in ("台本", "描", "順番", "仕上げ", "準備")), said)
        self.assertNotIn("token", page.eval("location.href"), "起動のトークンは URL から消える")
        page.until("document.querySelectorAll('.pages article.page .frame img').length === 4", seconds=120,
                   what="4 ページの画像が出ること（読み込み直さずに）")
        # 3 枚目からは loading=lazy なので、見える所まで送ってから読めたかを見る
        page.eval("document.querySelectorAll('.pages article.page .frame img').forEach(i => i.loading = 'eager')")
        page.until("Array.from(document.querySelectorAll('.pages article.page .frame img')).every(i => i.complete && i.naturalWidth > 0)",
                   seconds=20, what="画像が実際に読めること")
        api = [p["image_url"] for p in self.fx.get(f"/api/decks/{body['deck_id']}").json()["pages"]]
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.pages article.page .frame img')).map(i => i.getAttribute('src'))"), api)
        time.sleep(1.0)
        page.raise_if_broken()
        self.assertEqual(page.errors, [])
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('#view .notice--error')).length"), 0)
        self.assertEqual(page.eval("document.querySelectorAll('.thumb img').length"), 4)
        titles = page.eval("Array.from(document.querySelectorAll('.page__title')).map(e => e.textContent)")
        self.assertEqual(titles[1], "会議の4割は何も決めずに終わっている")
        self.assertTrue(page.eval("document.querySelector('.planning-band').hidden"), "描き終えたら帯は畳む")
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.launch(body["launch_id"])["state"], "attached")

        home = self.br.open(self.fx.base_url + "/")
        home.until("document.querySelectorAll('#view .card img').length >= 1"
                   " && document.querySelector('#view .card img').naturalWidth > 0", what="ホームのカードの先頭の絵")
        first = self.fx.get(f"/api/decks/{body['deck_id']}").json()["pages"][0]["image_url"]
        self.assertTrue(home.eval("document.querySelector('#view .card img').getAttribute('src')") == first)
        self.assertIn("4枚", home.eval("document.querySelector('#view .card').textContent"))
        time.sleep(0.5)
        home.raise_if_broken()
        self.assertEqual(home.errors, [])


if __name__ == "__main__":
    unittest.main()
