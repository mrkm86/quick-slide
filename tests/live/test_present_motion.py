"""発表の画面（motion）を本物のブラウザで: 中の編集できる要素・操作の部品でのキーがページ送りに奪われない。

エンジンは偽物の motion（本物の engine.js を埋めた HTML。1 場面目にボタンと contenteditable を置く）。Chrome が無ければ skip。
"""

from __future__ import annotations

import sys
import json
from contextlib import closing
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
    def click(self, page, selector: str) -> None:
        # 外側の幅から舞台の座標を推測せず、中で描かれた要素の位置を読む。
        # 初回は iframe の resize と合成が外枠の ready より遅れることがある。
        with closing(page.frame("#slide iframe")) as inner:
            target = json.dumps(selector)
            inner.until(f"!!document.querySelector({target})", what="クリックする部品")
            try:
                inner.until(f"""(() => {{
                  const el = document.querySelector({target}), r = el.getBoundingClientRect();
                  return r.width > 0 && r.height > 0 &&
                    !!document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.closest({target});
                }})()""", what="部品が舞台に描画される")
            except AssertionError as exc:
                state = inner.eval(f"""(() => {{
                  const el = document.querySelector({target}), r = el.getBoundingClientRect();
                  return {{ viewport: [innerWidth, innerHeight], rect: [r.x, r.y, r.width, r.height],
                    stage: document.getElementById('stage').style.transform, visibility: document.visibilityState,
                    hit: document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.outerHTML.slice(0, 500) }};
                }})()""")
                raise AssertionError(f"{exc}\n描画状態: {state}") from exc
            box = inner.eval(f"""(() => {{
              const el = document.querySelector({target}), r = el.getBoundingClientRect();
              return {{ x: r.x + r.width / 2, y: r.y + r.height / 2,
                width: r.width, height: r.height, viewport: [innerWidth, innerHeight],
                hit: document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.outerHTML.slice(0, 500) }};
            }})()""")
            self.assertGreater(box["width"], 0, box)
            self.assertGreater(box["height"], 0, box)
            inner.until(f"!!document.elementFromPoint({box['x']}, {box['y']})?.closest({target})",
                        what=f"部品がクリック位置に描かれる: {box}")
            outer = page.eval("(() => { const r = document.querySelector('#slide iframe').getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })()")
            x, y = outer[0] + box["x"], outer[1] + box["y"]
            self.click_info = {"selector": selector, "inner": box, "outer": outer, "point": [x, y]}
            inner.eval("window.__testPointerDown = null; addEventListener('pointerdown', e => { window.__testPointerDown = e.target; }, {once:true, capture:true})")
            page.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            event = {"x": x, "y": y, "button": "left", "clickCount": 1}
            page.call("Input.dispatchMouseEvent", {"type": "mousePressed", **event})
            try:
                inner.until(f"!!window.__testPointerDown?.closest({target})",
                            what=f"部品に押下が届く: {self.click_info}")
            finally:
                page.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **event})

    def test_keys_in_editable_and_controls_do_not_turn_pages(self) -> None:
        deck = self.make("motion", "claude_code")
        page = self.open(deck)
        self.assertEqual(count(page).replace("\n", ""), "1 / 4拍 1 / 2")
        self.click(page, "#ce")
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

    def test_click_on_a_role_button_inside_live_zone_reaches_its_handler(self) -> None:
        deck = self.make("motion", "claude_code")
        # 初期化直後の入力を、毎回新しい画面で確かめる。失敗したクリックはやり直さない。
        for attempt in range(5):
            with self.subTest(attempt=attempt):
                page = self.open(deck)
                self.click(page, "#lz")
                try:
                    page.until("document.getElementById('count').textContent.replace(/\\n/g, '') === '4 / 4拍 1 / 2'", what="LIVE の部品のハンドラー")
                except AssertionError as exc:
                    raise AssertionError(f"{exc}\n発表位置: {count(page)!r}\nクリック時: {self.click_info}") from exc

    def test_click_on_an_editable_runs_its_handler_and_only_the_stage_advance_is_stopped(self) -> None:
        deck = self.make("motion", "claude_code")
        page = self.open(deck)
        # contenteditable のハンドラー（MS.go(2,0)）は動き、そのあとエンジンの「次へ」は動かない（3 場面目の 1 拍目のまま）
        self.click(page, "#ce2")
        page.until("document.getElementById('count').textContent.replace(/\\n/g, '') === '3 / 4拍 1 / 2'", what="編集できる要素のハンドラー")
        time.sleep(0.6)
        self.assertEqual(count(page).replace("\n", ""), "3 / 4拍 1 / 2", "ハンドラーのあとにページ送りが重ならない")
