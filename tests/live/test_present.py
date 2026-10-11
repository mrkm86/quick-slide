"""発表の画面を**本物のブラウザ**で動かす: 進む・戻る・Home・End・一覧（O）・Esc（一覧を閉じる→編集へ戻る）・クリックの左右・
URL の `?p=`。image-slide（PNG のページ送り）と html-slide（仕切りの中の回の HTML を段ごとに）の両方。
html-slide は偽物のエンジンが書く deck.html（ページ i の段の数は i % 3）。仕切りの中の文書は別オリジンの扱いなので、
段の進みは外枠の「段 n / m」で確かめる。JavaScript の例外が 1 つも出ないこと。

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

KEYS = {"ArrowRight": 39, "ArrowLeft": 37, "Home": 36, "End": 35, "Escape": 27, "o": 79, "Enter": 13}


def press(page, key: str) -> None:
    code = KEYS[key]
    base = {"key": key, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
    page.call("Input.dispatchKeyEvent", {"type": "keyDown", **base})
    page.call("Input.dispatchKeyEvent", {"type": "keyUp", **base})


def count(page) -> str:
    return page.eval("document.getElementById('count').textContent")


class PresentLiveBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not browser.available():
            raise unittest.SkipTest("Chrome が無い環境")

    def setUp(self) -> None:
        from test_worker import fake_skills_dir
        tools, self.log = fake_tools(self, skills_dir=fake_skills_dir(self))
        tools.extra_env["FAKE_ENGINE_SLEEP"] = "0.01"
        self.fx = start_server(self, tools=tools, settings=Settings(worker=WorkerSettings(parallel=1)))
        self.br = browser.Browser(width=1280, height=720)
        self.addCleanup(self.br.close)

    def make(self, mode: str, runtime: str) -> str:
        r = self.fx.post("/api/intakes", intake_payload(runtime=runtime, mode=mode))
        self.assertEqual(r.status, 201, r.body)
        deck = r.json()["deck_id"]
        run = wait_until_idle(self.fx, deck, seconds=120)
        self.assertEqual(run["state"], "done", run["message"])
        return deck

    def open(self, deck: str, query: str = ""):
        page = self.br.open(f"{self.fx.base_url}/slides/{deck}/present{query}")
        page.until("document.getElementById('present').dataset.state === 'ready'", seconds=30, what="発表の画面が立ち上がる")
        return page


class ImagePresentTest(PresentLiveBase):
    def test_keys_list_and_escape(self) -> None:
        deck = self.make("pitch", "codex")
        page = self.open(deck)
        self.assertEqual(count(page), "1 / 4")
        self.assertTrue(page.eval("document.getElementById('prev').hidden"), "最初のページに「前へ」は出さない")
        press(page, "ArrowRight")
        page.until("document.getElementById('count').textContent === '2 / 4'", what="次へ")
        self.assertIn("p=2", page.eval("location.search"))
        press(page, "End")
        page.until("document.getElementById('count').textContent === '4 / 4'", what="最後へ")
        self.assertTrue(page.eval("document.getElementById('next').hidden"))
        press(page, "ArrowLeft")
        page.until("document.getElementById('count').textContent === '3 / 4'", what="戻る")
        press(page, "Home")
        page.until("document.getElementById('count').textContent === '1 / 4'", what="最初へ")
        # 一覧: O で開き、3 枚目を押すと飛ぶ。Esc は一覧を閉じるだけ
        press(page, "o")
        page.until("!document.getElementById('list').hidden", what="一覧が開く")
        self.assertEqual(page.eval("document.querySelectorAll('#list-items button').length"), 4)
        press(page, "Escape")
        page.until("document.getElementById('list').hidden", what="Esc で一覧が閉じる")
        self.assertIn("/present", page.eval("location.pathname"))
        page.eval("document.getElementById('list-btn').click()")
        page.until("!document.getElementById('list').hidden", what="ボタンでも開く")
        page.eval("document.querySelectorAll('#list-items button')[2].click()")
        page.until("document.getElementById('count').textContent === '3 / 4' && document.getElementById('list').hidden", what="一覧から飛ぶ")
        # 左側のクリックで戻り、右側のクリックで進む
        box = page.eval("(() => { const r = document.getElementById('hit').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()")
        for x_frac, want in ((0.1, "2 / 4"), (0.8, "3 / 4")):
            x, y = box[0] + box[2] * x_frac, box[1] + box[3] * 0.4
            for t in ("mousePressed", "mouseReleased"):
                page.call("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
            page.until(f"document.getElementById('count').textContent === '{want}'", what=f"クリック {x_frac}")
        # 開始ページは ?p=
        again = self.open(deck, "?p=3")
        self.assertEqual(count(again), "3 / 4")
        # ?s=<ページの id> で開始（見つからなければ 1 ページ目）。URL は ?p= に直る
        ids = [p["slide_id"] for p in self.fx.get(f"/api/decks/{deck}/present").json()["pages"]]
        by_id = self.open(deck, f"?s={ids[2]}")
        self.assertEqual(count(by_id), "3 / 4")
        self.assertIn("p=3", by_id.eval("location.search"))
        self.assertEqual(count(self.open(deck, "?s=nothing")), "1 / 4")
        # Esc で編集の画面へ
        press(again, "Escape")
        again.until(f"location.pathname === '/slides/{deck}'", what="編集の画面へ戻る")

    def test_image_pages_and_labels(self) -> None:
        deck = self.make("pitch", "codex")
        page = self.open(deck)
        self.assertEqual(page.eval("document.querySelector('#slide img.is-on').naturalWidth > 0"), True)
        self.assertIsNone(page.eval("document.querySelector('#slide iframe')"))
        for sel in ("prev", "next", "list-btn", "exit"):
            self.assertTrue(page.eval(f"!!document.getElementById('{sel}').getAttribute('aria-label')"), sel)
        self.assertIn("ページ 1", page.eval("document.getElementById('live').textContent"))


class FramePresentTest(PresentLiveBase):
    def test_steps_then_next_page(self) -> None:
        deck = self.make("presentation", "claude_code")
        page = self.open(deck)
        self.assertEqual(page.eval("document.querySelector('#slide iframe').getAttribute('sandbox')"), "allow-scripts")
        self.assertIn("/present/", page.eval("document.querySelector('#slide iframe').src"))
        seen = [count(page).replace("\n", "")]
        presses = 0
        while not page.eval("document.getElementById('next').hidden") and presses < 20:
            press(page, "ArrowRight")
            presses += 1
            page.until(f"document.getElementById('count').textContent !== {seen[-1]!r}", what="進む")
            seen.append(count(page))
        # 段の数 [1, 2, 0, 1]: 進む回数は 段の合計 + ページの切り替え 3 = 7。最後の段の後に次のページへ進む
        self.assertEqual(presses, 7, seen)
        self.assertEqual(seen[0], "1 / 4段 0 / 1")
        self.assertEqual(seen[1], "1 / 4段 1 / 1")
        self.assertEqual(seen[2], "2 / 4段 0 / 2")
        self.assertEqual(seen[-1], "4 / 4段 1 / 1")
        press(page, "ArrowLeft")
        page.until("document.getElementById('count').textContent.indexOf('段 0 / 1') >= 0", what="戻ると段が減る")
