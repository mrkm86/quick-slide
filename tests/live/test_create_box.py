"""ホームの作成の欄・形式の札・一覧の形式の絞り込みを**本物のブラウザ**で確かめる。
形式の名前・説明・アイコン・色は語彙（`deck_kinds`）から来る。サーバの語彙に無い間は、画面の側で差し込んで確かめる
（画面に形式の名前を書いていないことの確認も兼ねる）。作成の POST は送る中身だけを控えて受け流す。

Chrome が無い環境では skip する。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from helpers import fake_tools, intake_payload, start_server, wait_until_idle  # noqa: E402

from quick_slide.settings import Settings, WorkerSettings  # noqa: E402

from . import browser  # noqa: E402

KINDS = [
    {"mode": "presentation", "label": "テスト形式A", "description": "説明 A", "icon": "slideshow", "tone": "blue", "outputs": [{"format": "pptx", "label": "PPTX"}, {"format": "pdf", "label": "PDF"}]},
    {"mode": "pitch", "label": "テスト形式B", "description": "説明 B", "icon": "photo_library", "tone": "orange", "outputs": [{"format": "pdf", "label": "PDF"}]},
    {"mode": "motion", "label": "テスト形式C", "description": "説明 C", "icon": "no_such_icon", "tone": "green", "outputs": [{"format": "html", "label": "HTML"}]},
]


class CreateBoxTest(unittest.TestCase):
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

    def prepare(self, path: str):
        page = self.br.open(f"{self.fx.base_url}/")
        page.until("!!window.SS && !!window.SS.vocab", what="語彙")
        page.eval(f"window.SS.vocab.deck_kinds = {json.dumps(KINDS)}; window.SS.vocab.create_limits = {{request: 50}}; window.SS.vocab.create = {{heading: 'H', placeholder: 'P', submit: 'S', working: 'W', empty_reason: 'E', too_long_reason: 'L'}};"
                  " window.__calls = []; var orig = window.SS.api;"
                  " window.SS.api = function (u, o) { window.__calls.push([u, o && o.body]);"
                  "   if (u === '/api/settings' && window.__brands) { return Promise.resolve({brands: window.__brands}); }"
                  "   if (u === '/api/intakes') { return Promise.resolve({deck_id: 'x', deck_url: '/slides'}); }"
                  "   return orig.apply(this, arguments); };")
        if path != "/":
            page.eval(f"window.SS.navigate({json.dumps(path)})")
        else:
            page.eval("window.SS.render()")
        return page

    def test_create_box_posts_request_and_kind(self) -> None:
        page = self.prepare("/")
        page.until("document.querySelectorAll('.kind-card').length === 3", what="形式の 3 枚")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.kind-card__name')).map(e => e.textContent)"),
                         ["テスト形式A", "テスト形式B", "テスト形式C"], "名前は語彙から")
        self.assertTrue(page.eval("document.querySelector('.create__go').disabled"), "書くまで押せない")
        self.assertEqual(page.eval("document.querySelector('.create__go').textContent"), "S", "ボタンの語は語彙から")
        self.assertEqual(page.eval("document.querySelector('.create__text').placeholder"), "P")
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.kind-card__out')).map(e => e.textContent)[0]"), "書き出し: PPTX・PDF")
        self.assertEqual(page.eval("document.querySelector('.create__kinds').getAttribute('role')"), "radiogroup")
        # 長すぎると押せない（上限は語彙の request_limits）
        page.eval("(function(){ var t = document.querySelector('.create__text'); t.value = 'あ'.repeat(51);"
                  " t.dispatchEvent(new Event('input')); })()")
        self.assertTrue(page.eval("document.querySelector('.create__go').disabled"))
        page.eval("document.querySelectorAll('.create__radio')[1].click()")
        page.eval("(function(){ var t = document.querySelector('.create__text'); t.value = 'AI の動向を 5 ページで';"
                  " t.dispatchEvent(new Event('input')); })()")
        self.assertFalse(page.eval("document.querySelector('.create__go').disabled"))
        page.eval("document.querySelector('.create__go').click()")
        body = page.until("(function(){ var c = window.__calls.filter(x => x[0] === '/api/intakes')[0]; return c && c[1]; })()", what="POST")
        self.assertEqual(body["request"], "AI の動向を 5 ページで")
        self.assertEqual(body["deck_attributes"], {"mode": "pitch"})
        self.assertEqual(body["origin"], {"kind": "request", "runtime": "browser"})
        self.assertEqual(body["schema_version"], 1)
        # 選んだ形式は覚える
        self.assertEqual(page.eval("localStorage.getItem('qs-create-kind')"), "pitch")
        page.raise_if_broken()
        self.assertEqual(page.errors, [])

    def test_brand_picker_only_with_two_own_brands(self) -> None:
        for brands, shown in (([{"id": "p", "name": "既定", "kind": "preset"}, {"id": "a", "name": "A社", "kind": "user"}], False),
                              ([{"id": "p", "name": "既定", "kind": "preset"}, {"id": "a", "name": "A社", "kind": "user"},
                                {"id": "b", "name": "B社", "kind": "user"}], True)):
            page = self.br.open(f"{self.fx.base_url}/")
            page.until("!!window.SS && !!window.SS.vocab", what="語彙")
            page.eval(f"window.SS.vocab.deck_kinds = {json.dumps(KINDS)}; window.__brands = {json.dumps(brands)}; window.__calls = [];"
                      " var orig = window.SS.api; window.SS.api = function (u, o) { window.__calls.push([u, o && o.body]);"
                      " if (u === '/api/settings') { return Promise.resolve({brands: window.__brands, default_brand_id: 'b'}); }"
                      " if (u === '/api/intakes') { return Promise.resolve({deck_id: 'x', deck_url: '/slides'}); }"
                      " return orig.apply(this, arguments); }; window.SS.render();")
            page.until("!!document.querySelector('.create')", what="作成の欄")
            import time
            time.sleep(0.5)
            self.assertEqual(page.eval("!document.querySelector('.create__brand').hidden"), shown)
            page.eval("(function(){ var t = document.querySelector('.create__text'); t.value = 'x'; t.dispatchEvent(new Event('input')); })()")
            page.eval("document.querySelector('.create__go').click()")
            body = page.until("(function(){ var c = window.__calls.filter(x => x[0] === '/api/intakes')[0]; return c && c[1]; })()", what="POST")
            if shown:
                self.assertEqual(body["deck_attributes"]["brand_id"], "b", "既定（default_brand_id）を選んでおく")
            else:
                self.assertNotIn("brand_id", body["deck_attributes"])

    def test_kind_tag_and_unknown_icon_and_list_filter(self) -> None:
        r = self.fx.post("/api/intakes", intake_payload(runtime="codex", mode="presentation"))
        self.assertEqual(r.status, 201, r.body)
        self.assertEqual(wait_until_idle(self.fx, r.json()["deck_id"], seconds=120)["state"], "done")
        page = self.prepare("/slides")
        page.until("document.querySelectorAll('.card').length > 0", what="一覧")
        # 札: 先頭にアイコン・語彙の名前・色の tone
        page.eval("(function(){ var t = window.SS.kindTag('motion'); document.body.appendChild(t); t.id = 'probe'; })()")
        self.assertEqual(page.eval("document.querySelector('#probe').dataset.tone"), "green")
        self.assertEqual(page.eval("document.querySelector('#probe').textContent"), "テスト形式C")
        self.assertEqual(page.eval("document.querySelectorAll('#probe svg path').length"), 1, "知らないアイコン名は汎用の絵")
        self.assertEqual(page.eval("document.querySelector('#probe svg path').getAttribute('d').length > 10"), True)
        # 絞り込み: 形式の選択肢は語彙から。選ぶと mode= が一覧の口に付く
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('select[aria-label=\"形式\"] option')).map(o => o.textContent.replace(/（\\d+）$/, ''))"),
                         ["すべての形式", "テスト形式A", "テスト形式B", "テスト形式C"])
        page.eval("window.__calls.length = 0; var s = document.querySelector('select[aria-label=\"形式\"]'); s.value = 'pitch'; s.dispatchEvent(new Event('change'))")
        page.until("window.__calls.some(c => c[0].indexOf('/api/decks?') === 0 && c[0].indexOf('mode=pitch') > 0)", what="mode= を付けて取り直す")
        self.assertIn("mode=pitch", page.eval("location.search"))
        page.raise_if_broken()
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
