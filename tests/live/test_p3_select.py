"""P3 の確かめ: **本物のブラウザ**で、10 ページの 1 冊（偽物の台本係・本物の image-slide・絵は偽物の codex）の
7・8 枚目を選び、指示を 1 つ送ると、読み込み直さずに 7・8 枚目の見出しと絵が替わり、版スタックに新しい版が
積まれること。ほかのページの絵は替わらないこと。JavaScript の例外が 1 つも出ないこと。

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

from helpers import FAKES, fake_tools, intake_payload, start_server, wait_until_idle  # noqa: E402

from quick_slide.settings import Settings, WorkerSettings  # noqa: E402

from . import browser  # noqa: E402

IMAGES = "Array.from(document.querySelectorAll('.pages article.page .frame img')).map(i => i.getAttribute('src'))"
TITLES = "Array.from(document.querySelectorAll('.pages article.page .page__title')).map(e => e.textContent)"


class SelectPagesAndInstructTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        varied = str(FAKES / "fake_codex_varied.py")
        tools, self.log = fake_tools(self, extra={"FAKE_WRITER_PAGES": "10", "FAKE_CODEX_SLEEP": "0.3",
                                                  "IMAGE_SLIDE_CODEX": varied})
        self.fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def test_select_7_and_8_and_send_one_instruction(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(runtime="codex", mode="pitch"))
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        self.assertEqual(wait_until_idle(self.fx, deck, seconds=120)["state"], "done")
        page = self.br.open(f"{self.fx.base_url}/slides/{deck}")
        page.until("document.querySelectorAll('.pages article.page .frame img').length === 10", seconds=30,
                   what="10 ページの絵")
        before_imgs = page.eval(IMAGES)
        before_titles = page.eval(TITLES)
        self.assertTrue(page.eval("document.querySelector('.deck-instruction__pages').hidden"), "選ぶまでは出さない")
        page.eval("document.querySelectorAll('.page__select-box')[6].click()")
        page.eval("document.querySelectorAll('.page__select-box')[7].click()")
        label = page.until("(function(){ var b = document.querySelector('.deck-instruction__pages');"
                           " return !b.hidden && b.querySelector('.target').textContent; })()", what="選んだページの欄")
        self.assertEqual(label, "選んだページ（7・8）")
        page.eval("(function(){ var ta = document.querySelector('.deck-instruction__pages textarea');"
                  " ta.value = '見出しを短くして'; ta.dispatchEvent(new Event('input')); })()")
        page.until("!document.querySelector('.deck-instruction__pages .btn--primary').disabled", what="送れる")
        page.eval("document.querySelector('.deck-instruction__pages .btn--primary').click()")
        said = page.until("(function(){ var s = document.querySelector('.deck-instruction__pages .editbox__said');"
                          " return s && s.textContent; })()", what="受け付けの 1 文")
        self.assertIn("7・8ページ", said)
        page.until("document.querySelector('.deck-instruction__pages').hidden", what="送ったら選んだ印を外す")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.page__select-box')).filter(b => b.checked).length"), 0)
        # 読み込み直さずに、見出しと絵と版スタックが替わる
        page.until("(function(){ var t = " + TITLES + "; return t[6].endsWith('（直し）') && t[7].endsWith('（直し）'); })()",
                   seconds=90, what="7・8 枚目の見出しが替わる")
        # 版の絵は右のパネルの「ページごとの直す束」（.page-tools）にある。ページの並びは中央の列で引く
        page.until("(function(){ var a = document.querySelectorAll('.pages article.page');"
                   " var n = function (i) { return document.querySelectorAll('.page-tools[data-slide=\"' + a[i].dataset.slide + '\"] .versions__list li').length; };"
                   " return n(6) === 2 && n(7) === 2; })()", seconds=60,
                   what="7・8 枚目の版スタックに新しい版")
        page.until("(function(){ var i = " + IMAGES + "; return i[6] !== " + repr(before_imgs[6]) +
                   " && i[7] !== " + repr(before_imgs[7]) + "; })()", seconds=60, what="7・8 枚目の絵が替わる")
        after_imgs = page.eval(IMAGES)
        after_titles = page.eval(TITLES)
        for i in range(10):
            if i in (6, 7):
                continue
            self.assertEqual(after_imgs[i], before_imgs[i], f"{i + 1} 枚目の絵は替わらない")
            self.assertEqual(after_titles[i], before_titles[i])
        self.assertEqual(page.eval("document.querySelectorAll('.page-tools[data-slide=\"' + document.querySelectorAll('.pages article.page')[0].dataset.slide + '\"] .versions__list li').length"), 1)
        self.assertEqual(page.eval("document.querySelectorAll('.live-notice').length"), 0,
                         "自分の直しを「ほかの場所で変わった」と言わない")
        time.sleep(1.0)
        page.raise_if_broken()
        self.assertEqual(page.errors, [])
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('#view .notice--error')).length"), 0)


if __name__ == "__main__":
    unittest.main()
