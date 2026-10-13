"""P5 の確かめ: **本物のブラウザ**で、描き終えた 1 冊（偽物の台本係・本物の image-slide・絵は偽物の codex）の画面の
書き出し（PDF・PPTX のボタンが押せ、押すと「ダウンロードしました」）と、スライドのメニューの「設定を替えて組み直す」
（ブランドと見せ方を選ぶ器が開き、送ると新しい回ができる）。JavaScript の例外が 1 つも出ないこと。

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

from helpers import fake_tools, intake_payload, start_server, wait_until_idle  # noqa: E402

from quick_slide.settings import Settings, WorkerSettings  # noqa: E402

from . import browser  # noqa: E402


class DeckMenuExportAndRebuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        tools, self.log = fake_tools(self)
        self.fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def test_export_buttons_and_rebuild_from_the_menu(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(runtime="codex", mode="pitch"))
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        first = wait_until_idle(self.fx, deck, seconds=120)
        self.assertEqual(first["state"], "done", first["message"])
        page = self.br.open(f"{self.fx.base_url}/slides/{deck}")
        page.until("document.querySelectorAll('.pages article.page .frame img').length === 4", seconds=30, what="4 ページ")
        # 書き出しはボタン 1 つ。押すと形式（PDF・PPTX）のメニューが開く
        self.assertFalse(page.eval("document.querySelector('.deck-export').disabled"))
        page.eval("document.querySelector('.deck-export').click()")
        page.until("Array.from(document.querySelectorAll('.menu button')).map(b => b.textContent).join() === 'PDF,PPTX'",
                   what="書き出しのメニューに PDF と PPTX")
        self.assertFalse(page.eval("Array.from(document.querySelectorAll('.menu button')).some(b => b.disabled)"))
        page.eval("Array.from(document.querySelectorAll('.menu button')).find(b => b.textContent === 'PDF').click()")
        page.until("Array.from(document.querySelectorAll('.toast')).some(t => t.textContent.indexOf('ダウンロードしました') >= 0)",
                   seconds=30, what="書き出しの知らせ")
        with self.fx.server.store_lock:
            exports = self.fx.store.deck_exports(deck)
        self.assertEqual([(e["format"], e["run_id"]) for e in exports], [("pdf", first["id"])])
        # メニュー → 設定を替えて組み直す
        page.eval("document.querySelector('.deck-actions .menu-anchor .btn--icon').click()")
        page.until("Array.from(document.querySelectorAll('.menu button')).some(b => b.textContent === '設定を替えて組み直す')",
                   what="メニューの項目")
        page.eval("Array.from(document.querySelectorAll('.menu button')).find(b => b.textContent === '設定を替えて組み直す').click()")
        page.until("!!document.querySelector('.modal #rebuild-brand') && !!document.querySelector('.modal #rebuild-style')",
                   what="ブランドと見せ方を選ぶ器")
        self.assertIn("ピッチ", page.eval("document.querySelector('.modal p.muted').textContent"))
        # 見せ方は付属の 2 つだけ（docs/adr/0009）
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('#rebuild-style option')).map(o => o.textContent).join('|')"),
                         "説明・報告（文字と図で説明する）|ビジュアル（絵で印象を残す）")
        page.eval("Array.from(document.querySelectorAll('.modal .btn--primary')).find(b => b.textContent === '組み直す').click()")
        page.until("!document.querySelector('.modal')", what="器が閉じる")
        time.sleep(0.5)
        last = wait_until_idle(self.fx, deck, seconds=120)
        self.assertNotEqual(last["id"], first["id"])
        self.assertEqual(last["state"], "done", last["message"])
        with self.fx.server.store_lock:
            job = self.fx.store._one("SELECT request_json FROM jobs WHERE run_id=?", (last["id"],))
        self.assertIn('"op": "rebuild"', job["request_json"].replace('":"', '": "'))
        time.sleep(1.0)
        page.raise_if_broken()
        self.assertEqual(page.errors, [])
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('#view .notice--error')).length"), 0)


if __name__ == "__main__":
    unittest.main()
