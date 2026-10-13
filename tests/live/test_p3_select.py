"""P3 の確かめ: **本物のブラウザ**で、10 ページの 1 冊（偽物の台本係・本物の image-slide・絵は偽物の codex）の
7・8 枚目を選び、帯の「直す」で開いた欄から指示を 1 つ送ると、欄が閉じ、読み込み直さずに 7・8 枚目の見出しと絵が替わり、
版の番号が進むこと。ほかのページの絵は替わらないこと。JavaScript の例外が 1 つも出ないこと。

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

from helpers import FAKES, fake_tools, intake_payload, runnable, start_server, wait_until_idle  # noqa: E402

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
        varied = runnable(FAKES / "fake_codex_varied.py")
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
        # 欄は普段出ない。選ぶと上に帯が出て、帯の「直す」でパネルが「まとめて直す」で開く
        self.assertFalse(page.eval("document.querySelector('.panel').classList.contains('is-open')"), "選ぶまでは出さない")
        self.assertTrue(page.eval("document.querySelector('.selection-bar').hidden"))
        page.eval("document.querySelectorAll('.page__select-box')[6].click()")
        page.eval("document.querySelectorAll('.page__select-box')[7].click()")
        band = page.until("(function(){ var b = document.querySelector('.selection-bar');"
                          " return !b.hidden && b.querySelector('.selection-bar__text').textContent; })()", what="選んだページの帯")
        self.assertEqual(band, "2 ページを選んでいます")
        self.assertEqual(page.eval("document.querySelectorAll('.page--selected').length"), 2, "選んだスライドに枠が付く")
        page.eval("document.querySelector('.selection-bar .btn--primary').click()")
        label = page.until("(function(){ var p = document.querySelector('.panel.is-open');"
                           " return p && p.querySelector('.panel__target').textContent; })()", what="選んだページの欄")
        self.assertEqual(label, "7・8 ページ（2 ページ）")
        self.assertEqual(page.eval("document.querySelectorAll('.panel__strip .panel__shot img').length"), 2)
        page.eval("(function(){ var ta = document.querySelector('.panel textarea');"
                  " ta.value = '見出しを短くして'; ta.dispatchEvent(new Event('input')); })()")
        page.until("!document.querySelector('.panel .editbox__send').disabled", what="送れる")
        page.eval("document.querySelector('.panel .editbox__send').click()")
        # 送れたら欄は閉じ、選んだ印も外れる
        page.until("!document.querySelector('.panel').classList.contains('is-open')", what="送ったら欄を閉じる")
        page.until("document.querySelector('.selection-bar').hidden", what="送ったら選んだ印を外す")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.page__select-box')).filter(b => b.checked).length"), 0)
        # 読み込み直さずに、見出しと絵と版の番号が替わる
        page.until("(function(){ var t = " + TITLES + "; return t[6].endsWith('（直し）') && t[7].endsWith('（直し）'); })()",
                   seconds=90, what="7・8 枚目の見出しが替わる")
        # 版の一覧は「版を選ぶ…」のダイアログにある。ページの頭の版の番号（v2）で、新しい版が積まれたことを見る
        page.until("(function(){ var a = document.querySelectorAll('.pages article.page');"
                   " var v = function (i) { return a[i].querySelector('.page__ver').textContent; };"
                   " return v(6) === 'v2' && v(7) === 'v2'; })()", seconds=60,
                   what="7・8 枚目に新しい版")
        page.until("(function(){ var i = " + IMAGES + "; return i[6] !== " + repr(before_imgs[6]) +
                   " && i[7] !== " + repr(before_imgs[7]) + "; })()", seconds=60, what="7・8 枚目の絵が替わる")
        after_imgs = page.eval(IMAGES)
        after_titles = page.eval(TITLES)
        for i in range(10):
            if i in (6, 7):
                continue
            self.assertEqual(after_imgs[i], before_imgs[i], f"{i + 1} 枚目の絵は替わらない")
            self.assertEqual(after_titles[i], before_titles[i])
        self.assertEqual(page.eval("document.querySelectorAll('.pages article.page')[0].querySelector('.page__ver').textContent"), "v1")
        self.assertEqual(page.eval("document.querySelectorAll('.live-notice').length"), 0,
                         "自分の直しを「ほかの場所で変わった」と言わない")
        time.sleep(1.0)
        page.raise_if_broken()
        self.assertEqual(page.errors, [])
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('#view .notice--error')).length"), 0)


if __name__ == "__main__":
    unittest.main()
