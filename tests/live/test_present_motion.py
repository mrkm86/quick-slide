"""発表の画面（motion）を本物のブラウザで: 中の編集できる要素・操作の部品でのキーがページ送りに奪われない。

エンジンは偽物の motion（本物の engine.js を埋めた HTML。1 場面目にボタンと contenteditable を置く）。Chrome が無ければ skip。
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
from .test_present import PresentLiveBase, count, press  # noqa: E402


class MotionKeysTest(PresentLiveBase):
    def click(self, page, x: float, y: float) -> None:
        # JavaScript の ready だけでは、別プロセスの iframe の描画がまだ終わっていないことがある。
        # 表示面の取得が終わるまで待ってから、実際のクリックを 1 回だけ送る。
        page.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        page.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.click_info = page.eval(f"""(() => {{
          const rect = el => {{ const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }};
          return {{ viewport: [innerWidth, innerHeight, devicePixelRatio],
            frame: rect(document.querySelector('#slide iframe')), bar: rect(document.getElementById('bar')),
            hit: document.elementFromPoint({x}, {y}).outerHTML.slice(0, 500) }};
        }})()""")
        for t in ("mousePressed", "mouseReleased"):
            page.call("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})

    def test_keys_in_editable_and_controls_do_not_turn_pages(self) -> None:
        deck = self.make("motion", "claude_code")
        page = self.open(deck)
        self.assertEqual(count(page).replace("\n", ""), "1 / 4拍 1 / 2")
        # 編集できる要素（舞台 1920x1080 の 200,800 → 画面 1280x720 の 約 233,560）をクリックして、キーを打つ
        r = page.eval("(() => { const b = document.querySelector('#slide iframe').getBoundingClientRect(); return [b.left, b.top, b.width, b.height]; })()")
        k = r[2] / 1920
        self.click(page, r[0] + (200 + 150) * k, r[1] + (800 + 40) * k)
        # フォーカスが中に移ってからキーを打つ（決まった秒数だけ待つと、遅い機械ではキーが先に着いてページが進んだ）
        page.until("document.activeElement === document.querySelector('#slide iframe')", what="クリックでフォーカスが中へ移る")
        time.sleep(0.3)
        self.assertEqual(count(page).replace("\n", ""), "1 / 4拍 1 / 2", "contenteditable のクリックでページが動かない")
        for key in ("ArrowRight", "ArrowLeft", "Enter"):
            press(page, key)
            time.sleep(0.4)
            self.assertEqual(count(page).replace("\n", ""), "1 / 4拍 1 / 2", f"contenteditable の {key} でページが動かない")
        # 操作の部品: Tab で次のフォーカス（中のボタン）へ移して Enter・Space。ページは動かない
        base = count(page).replace("\n", "")
        page.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Tab", "windowsVirtualKeyCode": 9})
        page.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Tab", "windowsVirtualKeyCode": 9})
        press(page, "Enter")
        time.sleep(0.6)
        self.assertEqual(count(page).replace("\n", ""), base, "ボタンの Enter でページが動かない")

    def geometry(self, page):
        r = page.eval("(() => { const b = document.querySelector('#slide iframe').getBoundingClientRect(); return [b.left, b.top, b.width]; })()")
        return r[0], r[1], r[2] / 1920

    # 本物のブラウザの別プロセスの iframe には、続けて 2 回クリックを送ると 2 回目が届かないことがある（CDP の送り方の都合）。
    # だから 1 つの試験に 1 回のクリックだけ置く

    def test_click_on_a_role_button_inside_live_zone_reaches_its_handler(self) -> None:
        deck = self.make("motion", "claude_code")
        page = self.open(deck)
        x0, y0, k = self.geometry(page)
        self.click(page, x0 + (1200 + 150) * k, y0 + (800 + 40) * k)
        try:
            page.until("document.getElementById('count').textContent.replace(/\\n/g, '') === '4 / 4拍 1 / 2'", what="LIVE の部品のハンドラー")
        except AssertionError as exc:
            hit = page.eval(f"document.elementFromPoint({x0 + 1350 * k}, {y0 + 840 * k}).outerHTML.slice(0, 500)")
            raise AssertionError(f"{exc}\n発表位置: {count(page)!r}\nクリック時: {self.click_info}\nクリック位置の要素: {hit}") from exc

    def test_click_on_an_editable_runs_its_handler_and_only_the_stage_advance_is_stopped(self) -> None:
        deck = self.make("motion", "claude_code")
        page = self.open(deck)
        x0, y0, k = self.geometry(page)
        # contenteditable のハンドラー（MS.go(2,0)）は動き、そのあとエンジンの「次へ」は動かない（3 場面目の 1 拍目のまま）
        self.click(page, x0 + (700 + 150) * k, y0 + (800 + 40) * k)
        page.until("document.getElementById('count').textContent.replace(/\\n/g, '') === '3 / 4拍 1 / 2'", what="編集できる要素のハンドラー")
        time.sleep(0.6)
        self.assertEqual(count(page).replace("\n", ""), "3 / 4拍 1 / 2", "ハンドラーのあとにページ送りが重ならない")
