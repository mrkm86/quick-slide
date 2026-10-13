"""同梱の見本の確かめ（docs/adr/0006-bundled-samples.md）: **本物のブラウザ**で、見本を入れたサーバの画面を開く。

- ホームに見本が 1 冊ずつ（3 つのモード）並び、見本のスライドの画面でページの絵が出る
- 設定 › 見本: 見本ごとの行と状態が出る。1 冊をごみ箱へ送ると「入っていません」になり、「見本を入れ直す」で戻る
- どの画面でも JavaScript の例外が出ない

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

from helpers import start_server  # noqa: E402

from quick_slide.paths import SAMPLES_DIR  # noqa: E402

from . import browser  # noqa: E402

SETTLE_SECONDS = 0.8


class SamplesScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        self.fx = start_server(self, samples_dir=SAMPLES_DIR)
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def open(self, path: str, ready: str, what: str) -> browser.Page:
        page = self.br.open(self.fx.base_url + path)
        page.until(ready, seconds=20, what=what)
        time.sleep(SETTLE_SECONDS)
        page.raise_if_broken()
        return page

    def test_home_and_deck_show_the_samples(self) -> None:
        cards = self.fx.get("/api/decks?limit=50").json()["items"]
        self.assertEqual(len(cards), 3)
        home = self.open("/", "document.querySelectorAll('img[src^=\"/api/assets/\"]').length >= 3", "ホームの見本のカード")
        self.assertEqual(home.errors, [])
        pitch = next(c for c in cards if c["mode"] == "pitch")
        deck = self.open(f"/slides/{pitch['id']}",
                         "Array.from(document.querySelectorAll('img[src^=\"/api/assets/\"]')).some(i => i.complete && i.naturalWidth === 1920)",
                         "見本のスライドの画面のページの絵")
        self.assertEqual(deck.errors, [])

    def test_settings_lists_samples_and_restores_a_deleted_one(self) -> None:
        page = self.open("/settings/samples", "document.querySelectorAll('.sample-row').length === 3", "見本の行")
        states = page.eval("Array.from(document.querySelectorAll('.sample-row__state-text')).map(e => e.textContent)")
        self.assertEqual(states, ["入っています"] * 3)
        # 設定のトップから行ける
        top = self.open("/settings", "!!document.querySelector('a[href=\"/settings/samples\"]')", "設定のトップの「見本」")
        self.assertEqual(top.errors, [])
        motion = next(c for c in self.fx.get("/api/decks?limit=50").json()["items"] if c["mode"] == "motion")
        self.assertEqual(self.fx.post(f"/api/decks/{motion['id']}/delete").status, 200)
        page = self.open("/settings/samples", "document.querySelectorAll('.sample-row').length === 3", "見本の行")
        self.assertTrue(page.eval("document.querySelector('[data-sample=\"motion\"] .sample-row__state-text').textContent.startsWith('入っていません')"))
        page.eval("document.querySelector('.sample-restore').click()")
        page.until("Array.from(document.querySelectorAll('.sample-row__state-text')).every(e => e.textContent === '入っています')",
                   seconds=30, what="入れ直したあとの状態")
        self.assertEqual(len([c for c in self.fx.get("/api/decks?limit=50").json()["items"] if c["mode"] == "motion"]), 1)
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
