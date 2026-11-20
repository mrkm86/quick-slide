"""直す板の「自分で直す」を**本物のブラウザ**で使う（docs/adr/0036-direct-text-edits.md）。

プレゼンの 1 冊（偽物の台本係・本物の html-slide）の 2 枚目の「直す」を開くと、絵の下に「自分で直す／AI に頼む」の切り替えが出る。
はじめは「AI に頼む」（今までの欄）。「自分で直す」を押すとページの文字が欄に並び、書き換えるまで保存は押せない。
見出しを書き換えて保存すると、台本係を呼ばずにそのページが描き直され、欄は新しい文字で読み直される。
ピッチでは、書き換えられない理由が出る。JavaScript の例外が 1 つも出ないこと。

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

from helpers import fake_tools, intake_payload, start_server, wait_until_idle, writer_calls  # noqa: E402

from quick_slide.settings import Settings, WorkerSettings  # noqa: E402

from test_engines_e2e import html_slide_ready  # noqa: E402

from . import browser  # noqa: E402

P = ".panel.is-open "
FIELDS = f"Array.from(document.querySelectorAll('{P}.panel__self-input')).map(t => t.value)"
SAVE = f"document.querySelector('{P}.panel__self-save')"
SET = ("(function(v){ var ta = document.querySelector('" + P + ".panel__self-input'); ta.value = v;"
       " ta.dispatchEvent(new Event('input', {bubbles: true})); })")


class DirectTextScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        tools, self.log = fake_tools(self, extra={"FAKE_CODEX_SLEEP": "0.05"})
        self.fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        self.br = browser.Browser()
        self.addCleanup(self.br.close)

    def make(self, mode: str, runtime: str) -> str:
        r = self.fx.post("/api/intakes", intake_payload(runtime=runtime, mode=mode))
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        first = wait_until_idle(self.fx, deck, seconds=300)
        self.assertEqual(first["state"], "done", first["message"])
        return deck

    def open_panel(self, deck: str):
        page = self.br.open(f"{self.fx.base_url}/slides/{deck}")
        page.until("document.querySelectorAll('.pages article.page .frame img').length >= 2", seconds=30, what="ページの絵")
        page.eval("document.querySelectorAll('.pages article.page .page__edit')[1].click()")
        page.until(f"!!document.querySelector('{P}.panel__tabs:not([hidden])')", what="直し方の切り替え")
        return page

    def test_rewrite_a_headline_by_hand(self) -> None:
        why = html_slide_ready()
        if why:
            self.skipTest(why)
        deck = self.make("presentation", "claude_code")
        page = self.open_panel(deck)
        self.assertEqual(page.eval(f"Array.from(document.querySelectorAll('{P}.panel__tab')).map(b => b.textContent)"),
                         ["自分で直す", "AI に頼む"])
        self.assertEqual(page.eval(f"document.querySelectorAll('{P}.panel__tab svg.icon').length"), 2, "どちらにもアイコン")
        self.assertEqual(page.eval(f"document.querySelector('{P}.panel__tab[aria-selected=true]').textContent"), "AI に頼む")
        self.assertTrue(page.eval(f"document.querySelector('{P}.panel__self').hidden"))
        self.assertFalse(page.eval(f"document.querySelector('{P}.panel__slot').hidden"), "はじめは今までの欄")

        page.eval(f"document.querySelector('{P}#panel-tab-self').click()")
        page.until(f"document.querySelectorAll('{P}.panel__self-input').length > 0", what="文字の欄")
        self.assertTrue(page.eval(f"document.querySelector('{P}.panel__slot').hidden"), "AI の欄は隠す")
        before = page.eval(FIELDS)
        title = self.fx.get(f"/api/decks/{deck}").json()["pages"][1]["title"]
        self.assertIn(title, before, "見出しが欄にある")
        self.assertTrue(page.eval(SAVE + ".disabled"), "書き換えるまで保存は押せない")
        # どの欄にも名前がある（見出しを左に出すか、読み上げにだけ渡すか）
        self.assertTrue(page.eval(f"Array.from(document.querySelectorAll('{P}.panel__self-input')).every(t =>"
                                  " !!t.getAttribute('aria-label') || !!document.querySelector('label[for=\"' + t.id + '\"]'))"))

        calls = len(writer_calls(self.log))
        page.eval(SET + "('人が書いた文字')")
        self.assertFalse(page.eval(SAVE + ".disabled"))
        self.assertTrue(page.eval(f"document.querySelector('{P}.panel__self-input').classList.contains('is-changed')"))
        # ページを送って戻っても、書きかけは残る
        page.eval(f"document.querySelector('{P}.panel__step--next').click()")
        page.until(f"document.querySelector('{P}.panel__self-input') && document.querySelector('{P}.panel__self-input').value"
                   " !== '人が書いた文字'", what="次のページの欄")
        page.eval(f"document.querySelector('{P}.panel__step--prev').click()")
        page.until(f"document.querySelector('{P}.panel__self-input') && document.querySelector('{P}.panel__self-input').value"
                   " === '人が書いた文字'", what="書きかけが戻る")
        page.eval(SAVE + ".click()")
        deadline = time.monotonic() + 20   # 押してから仕事ができるまで（できる前に「終わった」と読まない）
        while time.monotonic() < deadline:
            with self.fx.server.store_lock:
                if self.fx.store.latest_run(deck)["args"].get("edit") == "retext":
                    break
            time.sleep(0.1)
        run = wait_until_idle(self.fx, deck, seconds=300)
        self.assertEqual(run["state"], "done", run["message"])
        self.assertEqual(run["record"]["edit"]["op"], "retext")
        self.assertEqual(len(writer_calls(self.log)), calls, "台本係は呼ばない")
        with self.fx.server.store_lock:
            data = self.fx.store.load_script(self.fx.store.head_script(deck))
        self.assertIn("人が書いた文字", str(data["slides"][1]))
        # 描き終えたら、欄は台本から読み直される（書き換えた文字が元の文字になり、保存は押せない）
        page.until(f"{SAVE} && {SAVE}.disabled && {FIELDS}.indexOf('人が書いた文字') >= 0"
                   f" && !document.querySelector('{P}.panel__self-input').readOnly"
                   f" && !document.querySelector('{P}.panel__self-input.is-changed')", seconds=30, what="読み直した欄")
        self.assertEqual(page.eval(f"document.querySelectorAll('{P}.panel__self-input.is-changed').length"), 0)
        # 検査に落ちた書き換えは当たらない。欄には書きかけが残り、理由が出て、もう一度保存できる（「保存済み」と見せない）
        from unittest import mock

        from quick_slide.worker import edit as worker_edit
        from quick_slide.worker.pipeline import CheckResult

        refused = CheckResult(rc=2, problems=["NG: 見出しが長すぎる"], warnings=[], stopped=False)
        with mock.patch.object(worker_edit, "check_candidate", return_value=refused):
            page.eval(SET + "('検査に落ちる文字')")
            page.eval(SAVE + ".click()")
            self.assertTrue(page.eval(f"document.querySelector('{P}.panel__self-input').readOnly"), "送ってからは欄を書けない")
            why = page.until(f"(function(){{ var p = document.querySelector('{P}.panel__self-said .editbox__refused');"
                             " return p && p.textContent; })()", seconds=40, what="当たらなかった理由")
        self.assertIn("見出しが長すぎる", why)
        self.assertEqual(page.eval(f"document.querySelector('{P}.panel__self-input').value"), "検査に落ちる文字", "書きかけは残る")
        self.assertFalse(page.eval(f"document.querySelector('{P}.panel__self-input').readOnly"))
        self.assertFalse(page.eval(SAVE + ".disabled"), "もう一度保存できる")
        with self.fx.server.store_lock:
            data = self.fx.store.load_script(self.fx.store.head_script(deck))
        self.assertNotIn("検査に落ちる文字", str(data["slides"][1]))
        # 読み上げ: 2 つのタブは、それぞれの板を指す
        self.assertEqual(page.eval(f"Array.from(document.querySelectorAll('{P}.panel__tab')).map(b => b.getAttribute('aria-controls'))"),
                         ["panel-how-self", "panel-how-ai"])
        self.assertEqual(page.eval("['panel-how-self', 'panel-how-ai'].map(id => document.getElementById(id).getAttribute('role')"
                                   " + '/' + document.getElementById(id).getAttribute('aria-labelledby'))"),
                         ["tabpanel/panel-tab-self", "tabpanel/panel-tab-ai"])
        # AI に頼む に戻すと、今までの欄
        page.eval(f"document.querySelector('{P}#panel-tab-ai').click()")
        self.assertFalse(page.eval(f"document.querySelector('{P}.panel__slot').hidden"))
        self.assertTrue(page.eval(f"!!document.querySelector('{P}.panel__slot textarea')"))
        self.assertEqual(page.errors, [])

    def test_pitch_says_why_not(self) -> None:
        deck = self.make("pitch", "codex")
        page = self.open_panel(deck)
        page.eval(f"document.querySelector('{P}#panel-tab-self').click()")
        reason = page.until(f"(function(){{ var p = document.querySelector('{P}.panel__self .reason'); return p && p.textContent; }})()",
                            what="書き換えられない理由")
        self.assertIn("AI に頼む", reason)
        self.assertEqual(page.eval(f"document.querySelectorAll('{P}.panel__self-input').length"), 0)
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
