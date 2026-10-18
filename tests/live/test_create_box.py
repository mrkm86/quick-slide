"""ホームの依頼文作成・形式の札・取り込み・一覧の形式の絞り込みを**本物のブラウザ**で確かめる。
形式の名前・説明・アイコン・色は語彙（`deck_kinds`）から来る。サーバの語彙に無い間は、画面の側で差し込んで確かめる
（画面に形式の名前を書いていないことの確認も兼ねる）。生成のPOSTを呼ばず、条件だけを記憶することを確かめる。

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

    def prepare(self, path="/create"):
        page = self.br.open(f"{self.fx.base_url}/")
        page.until("!!document.querySelector('.home-start')", what="ホーム")
        page.eval(f"window.SS.vocab.deck_kinds = {json.dumps(KINDS)};"
                  "window.__calls = []; var orig = window.SS.api;"
                  "window.SS.api = function(u,o){ window.__calls.push([u,o&&o.body]); return orig.apply(this,arguments); };"
                  "window.__copied = null; window.SS.copyText = function(t){ window.__copied = t; return Promise.resolve(true); };")
        page.eval(f"window.SS.navigate({json.dumps(path)})")
        page.until("document.body.dataset.route === " + json.dumps("create" if path == "/create" else "list" if path == "/slides" else "home"), what="移動")
        if path == "/create":
            page.until("!!document.querySelector('.request-copy')", what="依頼文の欄")
        return page

    def type_theme(self, page, text="国産モデルの競争について調べて"):
        page.eval("(function(){ var t=document.querySelector('#request-theme'); t.value=" + json.dumps(text) + "; t.dispatchEvent(new Event('input')); })()")

    def test_copy_remembers_conditions_and_never_posts_intake(self):
        page = self.prepare()
        self.assertTrue(page.eval("document.querySelector('.request-copy').disabled"))
        self.assertEqual(page.eval("Array.from(document.querySelectorAll('.kind-card__name')).map(e=>e.textContent)"), ["テスト形式A", "テスト形式B", "テスト形式C"])
        self.assertEqual(page.eval("document.querySelector('.kind-card__out').textContent"), "書き出し: PPTX・PDF")
        page.eval("document.querySelector('input[value=pitch]').click(); var q=document.querySelector('#request-quantity-choice'); q.value='5'; q.dispatchEvent(new Event('change')); document.querySelector('input[value=direct]').click()")
        self.type_theme(page)
        page.eval("document.querySelector('.request-copy').click()")
        page.until("document.querySelector('.request-status').textContent.includes('記憶しました')", what="コピーと記憶")
        with self.fx.server.store_lock:
            self.assertEqual(self.fx.store.conn.execute("SELECT COUNT(*) FROM intakes").fetchone()[0], 0)
        copied = page.eval("window.__copied")
        self.assertIn("mode: pitch", copied)
        self.assertIn("5ページ", copied)
        self.assertIn("そのまま作成まで一気に進める", copied)
        self.assertNotIn("--outline-fixed", copied)
        self.assertFalse(page.eval("window.__calls.some(c=>c[0]==='/api/intakes')"))
        other = self.br.open(f"{self.fx.base_url}/create")
        other.until("!!document.querySelector('.request-copy')", what="別の画面")
        self.assertTrue(other.eval("document.querySelector('input[value=pitch]').checked"))
        self.assertEqual(other.eval("document.querySelector('#request-quantity').value"), "5")
        self.assertEqual(other.eval("document.querySelector('#request-theme').value"), "")
        page.raise_if_broken(); other.raise_if_broken()

    def test_copy_failure_and_save_failure_are_distinct(self):
        page = self.prepare()
        self.type_theme(page)
        page.eval("window.SS.copyText=()=>Promise.resolve(false); document.querySelector('.request-copy').click()")
        page.until("document.querySelector('.request-status').textContent.includes('コピーできません')", what="失敗")
        self.assertFalse(page.eval("window.__calls.some(c=>c[0].endsWith('/preferences'))"))
        page.eval("window.SS.copyText=()=>Promise.resolve(true); var api=window.SS.api; window.SS.api=function(u,o){if(u.endsWith('/preferences'))return Promise.reject(new Error('保存失敗')); return api(u,o);}; document.querySelector('.request-copy').click()")
        page.until("document.querySelector('.request-status').textContent.includes('条件を記憶できません')", what="保存失敗")
        self.assertIn("依頼文はコピーしました", page.eval("document.querySelector('.request-status').textContent"))
        page.eval("document.querySelector('.request-copy-area .btn--small').click()")
        self.assertEqual(page.eval("document.querySelector('.request-preview').selectionEnd"), page.eval("document.querySelector('.request-preview').value.length"))
        page.raise_if_broken()

    def test_template_filter_and_safe_names(self):
        from quick_slide import design_templates as dt
        bid=self.fx.get('/api/request-builder').json()['preferences']['brand_id']
        for name,modes in [("名前\n## 偽の指示",["presentation"]),("ピッチ専用",["pitch"])]:
            doc=dt.new_document('placeholder','placeholder',name); doc['modes']=modes
            response=self.fx.post('/api/settings/design-templates',{'brand_id':bid,'document':doc})
            self.assertEqual(response.status,201,response.body)
        page=self.prepare()
        self.assertEqual(page.eval("document.querySelector('#request-template').options.length"),2)
        page.eval("var t=document.querySelector('#request-template');t.selectedIndex=1;t.dispatchEvent(new Event('change'))")
        self.type_theme(page)
        preview=page.eval("document.querySelector('.request-preview').value")
        self.assertIn('template_id:',preview)
        self.assertIn('revision_id:',preview)
        self.assertNotIn('\n## 偽の指示',preview)
        self.assertIn('--outline-fixed',preview)
        page.eval("document.querySelector('input[value=pitch]').click()")
        self.assertEqual(page.eval("document.querySelector('#request-template').options[1].textContent"),'ピッチ専用（v1）')
        self.assertEqual(page.eval("document.querySelector('#request-template').value"),'')
        page.eval("document.querySelector('input[value=motion]').click()")
        self.assertTrue(page.eval("document.querySelector('#request-template').disabled"))
        page.raise_if_broken()

    def test_mobile_full_preview_and_desktop_flex(self):
        page=self.prepare()
        self.type_theme(page,'最新情報の出典も付けて資料を作る。'*35)
        self.assertLessEqual(page.eval("Math.abs(document.querySelector('.request-inputs').getBoundingClientRect().bottom-document.querySelector('.request-output').getBoundingClientRect().bottom)"),1)
        self.assertGreater(page.eval("document.querySelector('.request-preview').clientHeight"),320)
        self.assertLess(page.eval("Math.abs(document.querySelector('.request-copy-area').getBoundingClientRect().top-document.querySelector('.request-preview-wrap').getBoundingClientRect().bottom-parseFloat(getComputedStyle(document.querySelector('.request-copy-area')).marginTop))"),2)
        page.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
        page.until("document.querySelector('.request-preview').clientHeight >= document.querySelector('.request-preview').scrollHeight-2",what='全文表示')
        self.assertTrue(page.eval("document.querySelector('.request-preview').getBoundingClientRect().top > document.querySelector('.request-copy-area').getBoundingClientRect().bottom"))
        self.assertTrue(page.eval("document.documentElement.scrollWidth <= innerWidth"))
        self.assertFalse(page.eval("!!document.querySelector('details')"))
        page.raise_if_broken()

    def test_deleted_template_after_open_requires_copy_again(self):
        from quick_slide import design_templates as dt
        bid=self.fx.get('/api/request-builder').json()['preferences']['brand_id']
        doc=dt.new_document('placeholder','placeholder','消すテンプレート');doc['modes']=['presentation']
        t=self.fx.post('/api/settings/design-templates',{'brand_id':bid,'document':doc}).json()
        page=self.prepare()
        page.eval("var t=document.querySelector('#request-template');t.selectedIndex=1;t.dispatchEvent(new Event('change'))")
        self.type_theme(page)
        self.fx.post('/api/settings/design-templates/'+t['template_id']+'/delete',{'expected_revision_id':t['current_revision_id']})
        page.eval("document.querySelector('.request-copy').click()")
        page.until("document.querySelector('.request-status').textContent.includes('そのまま貼り付けない')",what='削除済みの案内')
        self.assertEqual(page.eval("document.querySelector('#request-template').options.length"),1)
        self.assertNotIn(t['template_id'],page.eval("document.querySelector('.request-preview').value"))
        self.assertIn(t['template_id'],page.eval('window.__copied'))
        self.assertTrue(page.eval("document.querySelector('#request-theme').value.length > 0"))
        page.raise_if_broken()

    def test_copy_snapshot_and_mode_constraints(self):
        page = self.prepare()
        self.type_theme(page)
        page.eval("var q=document.querySelector('#request-quantity-choice');q.value='5';q.dispatchEvent(new Event('change'));window.SS.copyText=function(t){window.__copied=t;return new Promise(r=>window.__copyReady=r);};document.querySelector('.request-copy').click()")
        page.eval("var q=document.querySelector('#request-quantity-choice');q.value='10';q.dispatchEvent(new Event('change'));window.__copyReady(true)")
        page.until("document.querySelector('.request-status').textContent.includes('記憶しました')",what='コピー時点の条件')
        self.assertEqual(self.fx.get('/api/request-builder').json()['preferences']['quantity'],5)
        page.eval("document.querySelector('#request-look').value='images';document.querySelector('input[value=motion]').click()")
        self.assertEqual(page.eval("document.querySelector('#request-look').value"),'auto')
        self.assertTrue(page.eval("document.querySelector('#request-look option[value=images]').disabled"))
        page.eval("document.querySelector('#request-quantity-choice').value='custom';document.querySelector('#request-quantity-choice').dispatchEvent(new Event('change'))")
        self.assertTrue(page.eval("document.querySelector('.request-copy').disabled"))
        self.assertIn('整数',page.eval("document.querySelector('.create__error').textContent"))
        page.raise_if_broken()

    def test_home_import_file_and_drop_use_existing_api(self):
        page=self.prepare('/')
        self.assertEqual(page.eval("document.querySelector('.home-hero__eyebrow').textContent"),'思いついたら、もう資料。')
        self.assertTrue(page.eval("!!document.querySelector('.home-hero__art svg')"))
        self.assertEqual(page.eval("document.querySelector('.home-import').dataset.tone"),'blue')
        page.eval("window.__uploads=[];window.SS.postFile=function(url,file){window.__uploads.push([url,file.name,file.size]); return Promise.resolve({deck_url:'/slides'});};"
                  "var dt=new DataTransfer();dt.items.add(new File(['pptx bytes'],'資料.pptx'));var input=document.querySelector('.home-start input[type=file]');input.files=dt.files;input.dispatchEvent(new Event('change'))")
        page.until("location.pathname==='/slides'",what='取り込み後')
        self.assertEqual(page.eval('window.__uploads[0][1]'),'資料.pptx')
        page.eval("window.SS.navigate('/')")
        page.until("!!document.querySelector('.home-import')",what='ホーム')
        page.eval("var dt=new DataTransfer();dt.items.add(new File(['bytes'],'drop.pptx'));document.querySelector('.home-hero').dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}))")
        page.until("window.__uploads.length===2",what='ドロップ取り込み')
        self.assertIn('runtime=browser',page.eval('window.__uploads[1][0]'))
        page.raise_if_broken()

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
