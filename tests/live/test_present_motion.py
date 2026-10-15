"""発表の画面（motion）を本物のブラウザで: 中の編集できる要素・操作の部品でのキーがページ送りに奪われない。

エンジンは偽物の motion（本物の engine.js を埋めた HTML。1 場面目にボタンと contenteditable を置く）。Chrome が無ければ skip。
"""

from __future__ import annotations

import json
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
    def click(self, page, selector: str) -> None:
        # activeElement は背面のタブにも残る。実際の入力先を前面にしてから操作する。
        page.call("Page.bringToFront")
        page.until("document.hasFocus() && document.visibilityState === 'visible'",
                   what="操作するタブが前面で焦点を持つ")
        document = page.call("DOM.getDocument")["root"]["nodeId"]
        node = page.call("DOM.querySelector", {"nodeId": document, "selector": "#slide iframe"})["nodeId"]
        frame = page.call("DOM.describeNode", {"nodeId": node})["node"]["frameId"]
        # 別プロセスのフレームをデバッグ対象として列挙してから接続する。
        page.call("Target.getTargets")
        session = page.call("Target.attachToTarget", {"targetId": frame, "flatten": True})["sessionId"]
        try:
            page.call("Runtime.enable", session_id=session)
            # 外枠の ready と、中の resize による舞台の拡縮は別。双方の現在の寸法を読む。
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                box = page.eval("(() => { const r = document.querySelector('#slide iframe').getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })()")
                fitted = page.eval(f"""Math.abs(innerWidth - {box[2]}) <= 1 && Math.abs(innerHeight - {box[3]}) <= 1 &&
                    Math.abs(document.getElementById('stage').getBoundingClientRect().width -
                             1920 * Math.min(innerWidth / 1920, innerHeight / 1080)) < 1""", session_id=session)
                if fitted:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"iframe の表示領域と舞台の拡縮が合わなかった: {box}")
            target = json.dumps(selector)
            point = page.until(f"""(() => {{
              const el = document.querySelector({target});
              if (!el) return false;
              const r = el.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
              const hit = document.elementFromPoint(x, y);
              return r.width > 0 && r.height > 0 && hit && el.contains(hit) && [x, y];
            }})()""", session_id=session, what="クリック対象が実際に表示されている")
            x, y = box[0] + point[0], box[1] + point[1]
            self.click_info = {"selector": selector, "frame": box, "point": [x, y]}
            page.until(f"document.elementFromPoint({x}, {y}) === document.querySelector('#slide iframe')",
                       what=f"外枠の部品がクリック対象を覆っていない: {self.click_info}")
            # 表示直後の別プロセスへの最初の移動は届かないことがある。対象上に移せたことを
            # 確かめるまでポインターだけを動かす。押下・クリックはやり直さない。
            deadline = time.monotonic() + 20
            move = 0
            while time.monotonic() < deadline:
                page.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x + move % 2, "y": y})
                if page.eval(f"document.querySelector({target}).matches(':hover')", session_id=session):
                    break
                move += 1
                time.sleep(0.05)
            else:
                self.fail(f"iframe 内の対象にポインターが届かなかった: {self.click_info}")
            event = {"x": x, "y": y, "button": "left", "clickCount": 1}
            page.call("Input.dispatchMouseEvent", {"type": "mousePressed", **event})
            try:
                page.until(f"document.querySelector({target}).matches(':active')", session_id=session,
                           what="iframe 内のクリック対象が押下を受け取る")
                page.until("document.activeElement === document.querySelector('#slide iframe')",
                           what=f"押下で iframe に焦点が移る: {self.click_info}")
            finally:
                page.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **event})
        finally:
            page.call("Target.detachFromTarget", {"sessionId": session})

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

    # 本物のブラウザの別プロセスの iframe には、続けて 2 回クリックを送ると 2 回目が届かないことがある（CDP の送り方の都合）。
    # だから 1 つの画面に 1 回のクリックだけ送る

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

    def test_click_activates_a_background_tab_before_sending_input(self) -> None:
        deck = self.make("motion", "claude_code")
        page = self.open(deck)
        other = self.open(deck)
        other.call("Page.bringToFront")
        page.until("!document.hasFocus() && document.visibilityState === 'hidden'",
                   what="別のタブを前面にして再現条件を作る")
        page.call("Emulation.setDeviceMetricsOverride", {"width": 1264, "height": 625,
                                                        "deviceScaleFactor": 1, "mobile": False})
        self.click(page, "#lz")
        page.until("document.getElementById('count').textContent.replace(/\\n/g, '') === '4 / 4拍 1 / 2'",
                   what="背面だったタブでも1回のクリックでLIVEのハンドラーが動く")
