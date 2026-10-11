"""ページの行の鉛筆（直す）と ▶（再生）を**本物のブラウザ**で確かめる: 鉛筆は字を持たず名前は aria-label、
html-slide のデッキでは ▶ がページを 1 枚だけ再生するダイアログを開き（発表の画面を埋め込み）、1 段進み、
「最初から」で戻り、Esc で閉じて ▶ に焦点が戻る。image-slide のデッキには ▶ を出さない。

Chrome が無い環境では skip する。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from helpers import fake_tools, intake_payload, start_server, wait_until_idle  # noqa: E402

from quick_slide.settings import Settings, WorkerSettings  # noqa: E402

from . import browser  # noqa: E402

FRAME_COUNT = "(function(){ var f = document.querySelector('.play__frame');" \
              " var d = f && f.contentDocument; var c = d && d.getElementById('count'); return c ? c.textContent : ''; })()"


class PlayDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        from test_worker import fake_skills_dir
        tools, self.log = fake_tools(self, skills_dir=fake_skills_dir(self))
        tools.extra_env["FAKE_ENGINE_SLEEP"] = "0.01"
        self.fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        self.br = browser.Browser(width=1280, height=900)
        self.addCleanup(self.br.close)

    def open(self, mode: str):
        r = self.fx.post("/api/intakes", intake_payload(runtime="codex", mode=mode))
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        self.assertEqual(wait_until_idle(self.fx, deck, seconds=120)["state"], "done")
        page = self.br.open(f"{self.fx.base_url}/slides/{deck}")
        page.until("document.querySelectorAll('.pages article.page .frame img').length > 1", seconds=30, what="ページの絵")
        return page

    def test_pencil_is_icon_only_and_labelled(self) -> None:
        page = self.open("pitch")
        self.assertEqual(page.eval("document.querySelector('.page__edit').getAttribute('aria-label')"), "ページ 1 を直す")
        self.assertEqual(page.eval("document.querySelector('.page__edit').title"), "直す")
        self.assertEqual(page.eval("document.querySelector('.page__edit').textContent.trim()"), "")
        self.assertEqual(page.eval("document.querySelectorAll('.page__play').length"), 0, "image-slide には再生を出さない")

    def test_play_one_page_step_replay_escape(self) -> None:
        page = self.open("presentation")
        self.assertEqual(page.eval("document.querySelectorAll('.page__play').length"),
                         page.eval("document.querySelectorAll('.pages article.page').length"))
        self.assertEqual(page.eval("document.querySelectorAll('.page__play')[1].getAttribute('aria-label')"), "ページ 2 を再生")
        page.eval("document.querySelectorAll('.page__play')[1].focus()")
        page.eval("document.querySelectorAll('.page__play')[1].click()")
        page.until("!!document.querySelector('.dlg--play.is-open .play__frame')", what="再生のダイアログ")
        page.until(FRAME_COUNT + " !== ''", seconds=30, what="埋め込みの発表が立ち上がる")
        first = page.eval(FRAME_COUNT)
        self.assertNotIn("/ 4", first.split("段")[0], "ページ送りの表示は出さない")
        # 埋め込みの中のキーで 1 段進める（親から見ると iframe の中なので、同じオリジンの中の文書を直接押す）
        page.eval("(function(){ var d = document.querySelector('.play__frame').contentDocument;"
                  " d.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true})); })()")
        page.until(FRAME_COUNT + " !== " + repr(first), what="1 段進む")
        page.eval("Array.from(document.querySelectorAll('.dlg--play .btn')).filter(b => b.textContent.trim() === '最初から')[0].click()")
        page.until(FRAME_COUNT + " === " + repr(first), what="最初から")
        # Esc は中から親へ知らせ、親が閉じる。焦点は ▶ に戻る
        page.eval("(function(){ var d = document.querySelector('.play__frame').contentDocument;"
                  " d.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); })()")
        page.until("!document.querySelector('.dlg--play')", what="閉じる")
        self.assertEqual(page.eval("document.activeElement.getAttribute('aria-label')"), "ページ 2 を再生")
        page.raise_if_broken()
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
