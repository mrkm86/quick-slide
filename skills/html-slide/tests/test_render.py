#!/usr/bin/env python3
"""描き出し係（extract.mjs・pptx_writer・from_video・verify）のテスト。`python3 test_render.py <名前>` で1本ずつ走る。

- Chrome と puppeteer-core は手元に入っているものを scripts/browser.py が探して使う（ADR 0016）。ネットワークは使わない
- HTML は fixtures/contract-ok.html（決まりに沿った3枚）と fixtures/contract-bad.html（違反入り）
- from_video は Chrome を通さず、作った部品 JSON と台本で規則だけを見る
- PPTX は一時フォルダに書いて消す
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
FIX = HERE / "fixtures"
TMP = Path(tempfile.mkdtemp(prefix="html-slide-test-"))
sys.path.insert(0, str(SKILL / "scripts"))

import from_video  # noqa: E402
import pptx_writer  # noqa: E402
import render  # noqa: E402
import verify  # noqa: E402

OK, BAD = FIX / "contract-ok.html", FIX / "contract-bad.html"
NS = {"p": pptx_writer.NS_P, "a": pptx_writer.NS_A}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def slide_xml(pptx, no) -> etree._Element:
    z = zipfile.ZipFile(pptx)
    names = verify._slide_names(z)
    return etree.fromstring(z.read(names[no - 1]))


def rendered_ok():
    out = TMP / "ok" / "deck.pptx"
    rep = render.render(OK, out)
    return out, rep


def t_contract_ok():
    """正常な HTML: 違反なし・PPTX が出る・段/効果/ノートの数・切り替え・!! の名前・report の保存。"""
    vs = render.check(OK)
    check(not render.errors(vs), f"正常な HTML で違反: {[render.format_violation(v) for v in vs]}")
    out, rep = rendered_ok()
    check(out.exists(), "PPTX が無い")
    check([s["steps"] for s in rep["slides"]] == [0, 2, 1], f"段の数: {[s['steps'] for s in rep['slides']]}")
    check([s["effects"] for s in rep["slides"]] == [0, 4, 2], f"効果の数: {[s['effects'] for s in rep['slides']]}")
    check([s["notes_clicks"] for s in rep["slides"]] == [0, 2, 1], "ノートの［クリック］の数")
    check([s["transition"] for s in rep["slides"]] == ["fade", "fade", "fade"], f"切り替え（morph は退役して fade）: {[s['transition'] for s in rep['slides']]}")
    check(any(v["rule"] == "retired-transition" and v["level"] == "warn" for v in vs), "morph を書いた HTML に退役の警告が無い")
    check("p159:morph" not in "".join(slide_xml(out, n).xpath("string(/*)") + etree.tostring(slide_xml(out, n)).decode() for n in (1, 2, 3)), "PPTX にモーフが出た")
    check(not rep["mismatch"], f"ずれ: {rep['mismatch']}")
    saved = json.loads(Path(rep["report_path"]).read_text(encoding="utf-8"))
    check(saved["renderer"] == "html-slide" and saved["totals"]["effects"] == 6, "report が build.json に保存されていない")
    for no in (1, 2, 3):
        names = slide_xml(out, no).xpath("//p:cNvPr/@name", namespaces=NS)
        check("!!band" in names, f"slide{no} に !!band が無い: {names[:8]}")
    check("card-a" in slide_xml(out, 2).xpath("//p:cNvPr/@name", namespaces=NS), "data-name が図形名になっていない")
    # 文字は PowerPoint の文字（画像にしていない）・字体名は font-family の先頭
    x = slide_xml(out, 2)
    texts = "".join(x.xpath("//a:t/text()", namespaces=NS))
    check("受け取る人が家にいない時間に届く" in texts and "原因は2つある" in texts, f"文字が無い: {texts[:80]}")
    check(set(x.xpath("//a:rPr/a:ea/@typeface", namespaces=NS)) == {"Yu Gothic"}, "字体名が Yu Gothic でない")
    # 強調の色替え run（em）
    runs = x.xpath("//a:r[a:t='2つ']/a:rPr/a:solidFill/a:srgbClr/@val", namespaces=NS)
    check(runs == ["D0306A"], f"em の色が run になっていない: {runs}")
    # 画像（object-fit: cover の切り抜き）と角丸
    pics = slide_xml(out, 1).xpath("//p:pic", namespaces=NS)
    check(len(pics) == 1 and pics[0].xpath(".//a:srcRect", namespaces=NS), "画像が無いか、cover の切り抜きが無い")


def t_contract_bad():
    """違反入りの HTML: transform・はみ出し・::before・section の外・ノートのずれを全部拾い、render は書かずに rc=2。"""
    vs = render.check(BAD)
    rules = {v["rule"] for v in render.errors(vs)}
    want = {"transform", "text-overflow", "pseudo-content", "outside-slide", "notes-clicks"}
    check(want <= rules, f"拾えていない違反: {want - rules}（拾ったのは {rules}）")
    by = {v["rule"]: v for v in vs}
    check("#tilt" in by["transform"]["el"] and "#narrow" in by["text-overflow"]["el"] and "#deco" in by["pseudo-content"]["el"],
          f"違反の場所が違う: {[(v['rule'], v['el']) for v in vs]}")
    out = TMP / "bad" / "deck.pptx"
    try:
        render.render(BAD, out)
        raise AssertionError("違反があるのに render が通った")
    except render.RenderError as e:
        check(e.rc == 2 and e.violations, f"rc={e.rc}")
    check(not out.exists(), "違反があるのに PPTX を書いた")
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "render.py"), "check", str(BAD)], capture_output=True, text=True, encoding="utf-8")
    check(r.returncode == 2 and "NG:" in r.stderr, f"CLI の check が rc=2 にならない: {r.returncode}")


def t_timing_xml():
    """timing の XML が整形式で、段の数・nodeType・遅れ・出方の番号が期待どおり。"""
    out, _ = rendered_ok()
    x = slide_xml(out, 2)                       # 見出し step 0・カード step 1・カード2枚 step 2（delay 300）
    seq = x.xpath("//p:cTn[@nodeType='mainSeq']/p:childTnLst/p:par", namespaces=NS)
    check(len(seq) == 3, f"mainSeq の段が {len(seq)}（自動1 + クリック2）")
    check(seq[0].xpath(".//p:cond[@evt='onBegin']", namespaces=NS), "step 0 が onBegin になっていない")
    nt = x.xpath("//p:cTn/@nodeType", namespaces=NS)
    check((nt.count("afterEffect"), nt.count("clickEffect"), nt.count("withEffect")) == (1, 2, 1), f"nodeType: {nt}")
    eff = x.xpath("//p:cTn[@presetClass='entr']", namespaces=NS)
    check([e.get("presetID") for e in eff] == ["42", "53", "10", "22"], f"出方: {[e.get('presetID') for e in eff]}")
    wipe = eff[3]
    check(wipe.get("presetSubtype") == "8" and wipe.xpath(".//p:animEffect/@filter", namespaces=NS) == ["wipe(left)"],
          "wipe-right が「左から」（subtype 8・wipe(left)）になっていない")
    check(wipe.xpath("p:stCondLst/p:cond/@delay", namespaces=NS) == ["300"], "data-delay が効いていない")
    # 段の番号が飛んでも詰めて数える（step 0, 1, 3 → クリック2回）
    parts = {"slides": [{"id": "s1", "notes": "［クリック］［クリック］", "units": {
        "a": {"step": 0, "fx": "fade"}, "b": {"step": 1, "fx": "wheel"}, "c": {"step": 3, "fx": "wipe-up", "delay": 0}},
        "prims": [{"type": "rect", "unit": u, "rect": {"x": 10 + i * 100, "y": 10, "w": 50, "h": 50}, "fill": {"hex": "ff0000", "a": 1}}
                  for i, u in enumerate("abc")]}]}
    rep = pptx_writer.write(parts, TMP / "gap.pptx")
    check(rep["slides"][0]["steps"] == 2 and rep["slides"][0]["ok"], f"段の詰め: {rep['slides'][0]}")
    w = slide_xml(TMP / "gap.pptx", 1).xpath("//p:cTn[@presetID='22']", namespaces=NS)[0]
    check(w.get("presetSubtype") == "4" and "wipe(down)" in etree.tostring(w).decode(), "wipe-up が「下から」になっていない")


def t_text_frames():
    """1行は折り返さず真ん中、複数行は幅で折り返し・行送り pt 固定・余白0・上揃え・ぶら下げなし・文節折りだけ改行を固定。"""
    out, _ = rendered_ok()
    x = slide_xml(out, 2)
    for sp in x.xpath("//p:sp[p:txBody]", namespaces=NS):
        text = "".join(sp.xpath(".//a:t/text()", namespaces=NS))
        bp = sp.xpath(".//a:bodyPr", namespaces=NS)[0]
        if text.startswith("受け取る人"):
            check(bp.get("wrap") == "square" and bp.get("anchor") == "t", f"複数行の bodyPr: {dict(bp.attrib)}")
            check(all(bp.get(k) == "0" for k in ("lIns", "tIns", "rIns", "bIns")), "複数行の余白が0でない")
            sp_pts = sp.xpath(".//a:lnSpc/a:spcPts/@val", namespaces=NS)
            check(sp_pts == ["2560"], f"行送り: {sp_pts}（32px × 1.6 = 51.2px = 25.6pt）")
            check(sp.xpath(".//a:pPr/@hangingPunct", namespaces=NS) == ["0"], "ぶら下げが切れていない")
            check(not sp.xpath(".//a:br", namespaces=NS), "文節折りでない文字に改行を入れた")
        if text.startswith("指定した時間に"):
            check(len(sp.xpath(".//a:br", namespaces=NS)) >= 1, "文節折り（auto-phrase）の文字に改行が固定されていない")
        if text == "不在":
            check(bp.get("wrap") == "none" and bp.get("anchor") == "ctr", f"1行の bodyPr: {dict(bp.attrib)}")


def t_table_align():
    """table の align: 折り返す右寄せ・中央寄せのセルが、PPTX でも HTML の文字の右端・中央に合う（右へ遊びを足してずれない）。"""
    import deck
    cols = ["項目", "右", "中央", "数"]
    long_ = "これは折り返して二行以上になるセルの文章です"
    sc = {"title": "t", "slides": [{"id": "t1", "type": "table", "headline": "揃え", "columns": cols, "align": ["left", "right", "center", "right"],
                                    "widths": [0.6, 1.4, 1.4, 0.6], "rows": [["A", long_, long_, "10/01"], ["B", "短い", "短い", "7日"]]}]}
    src = TMP / "align.json"
    src.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    html = deck.build_html(src, TMP / "align")[0]
    out = TMP / "align" / "deck.pptx"
    render.render(Path(html), out)
    x = slide_xml(out, 1)
    boxes = {}
    for sp in x.xpath("//p:sp[p:txBody]", namespaces=NS):
        text = "".join(sp.xpath(".//a:t/text()", namespaces=NS))
        off, ext = sp.xpath(".//a:off", namespaces=NS)[0], sp.xpath(".//a:ext", namespaces=NS)[0]
        lo = int(off.get("x")) / pptx_writer.PX
        boxes.setdefault(text, []).append((lo, lo + int(ext.get("cx")) / pptx_writer.PX, sp.xpath(".//a:pPr/@algn", namespaces=NS)[0]))
    wrapped = sorted(boxes[long_])
    check(wrapped[0][2] == "r" and wrapped[1][2] == "ctr", f"折り返すセルの段落の揃え: {wrapped}")
    # 右寄せの列（1210px 付近）の右端は、その列の「短い」「見出し」の文字の右端と同じ（TEXT_PAD ぶんの遊びを除く）
    short = min(boxes["短い"])[1] - pptx_writer.TEXT_PAD
    check(abs(wrapped[0][1] - short) <= 1, f"右寄せの折り返しの右端 {wrapped[0][1]} が1行の右端 {short} とずれた")
    mid = (wrapped[1][0] + wrapped[1][1]) / 2
    short_mid = sum(max(boxes["短い"])[:2]) / 2
    check(abs(mid - short_mid) <= 1, f"中央寄せの折り返しの中心 {mid} が1行の中心 {short_mid} とずれた")
    # deck の表の折り返しは改行を固定する経路（pin）を通る。固定しない複数行の経路（WRAP_SLACK）も、右端・中心を HTML の箱に合わせ続ける
    plain = []
    for k, al in enumerate(("r", "ctr", "l")):
        plain.append({"type": "text", "unit": None, "rect": {"x": 200, "y": 100 + 200 * k, "w": 400, "h": 120}, "lines": 2, "align": al,
                      "runs": [{"text": "折り返して二行になる文章", "size": 32}]})
    pptx_writer.write({"slides": [{"id": "s1", "prims": plain, "units": {}}]}, TMP / "align-plain.pptx")
    got = {}
    for sp in slide_xml(TMP / "align-plain.pptx", 1).xpath("//p:sp[p:txBody]", namespaces=NS):
        off, ext = sp.xpath(".//a:off", namespaces=NS)[0], sp.xpath(".//a:ext", namespaces=NS)[0]
        lo = int(off.get("x")) / pptx_writer.PX
        got[sp.xpath(".//a:pPr/@algn", namespaces=NS)[0]] = (lo, lo + int(ext.get("cx")) / pptx_writer.PX)
    check(abs(got["r"][1] - 600) <= 0.5, f"固定しない右寄せの右端 {got['r'][1]} が箱の右端 600 とずれた")
    check(abs(sum(got["ctr"]) / 2 - 400) <= 0.5, f"固定しない中央寄せの中心 {sum(got['ctr']) / 2} が箱の中心 400 とずれた")


def t_footnote():
    """footnote: source の下の脚注が PPTX に写る（文字・muted・source の1行下・同じ左端）。書かない台本には脚注の部品が無い。"""
    import deck
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    fn = "※1 月末に休業日が 2 日あった"
    tbl = {"type": "table", "headline": "h", "columns": ["部署", "今月"], "rows": [["受付", "1.4 日※1"]], "source": "定義の1行"}
    sc = TMP / "foot.json"
    sc.write_text(json.dumps({"title": "t", "slides": [dict(tbl, footnote=fn), tbl]}, ensure_ascii=False), encoding="utf-8")
    html, _ = deck.build_html(sc, TMP / "foot-deck")
    out = TMP / "foot.pptx"
    render.render(html, out)
    x = slide_xml(out, 1)
    pos = lambda word: (int(x.xpath(f"//p:sp[.//a:t='{word}']//a:off/@x", namespaces=NS)[0]), int(x.xpath(f"//p:sp[.//a:t='{word}']//a:off/@y", namespaces=NS)[0]))
    (sx, sy), (fx, fy) = pos("定義の1行"), pos(fn)
    check(sx == fx and fy > sy, f"脚注が source の下・同じ左端にない: {(sx, sy)} {(fx, fy)}")
    check(abs((fy - sy) - 33 * 6350) < 2 * 6350, f"脚注と source の行の間隔が 1 行ぶん（33px）でない: {fy - sy}")
    color = x.xpath(f"//p:sp[.//a:t='{fn}']//a:rPr/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper()
    check(color == pal["muted"][1:].upper(), f"脚注の文字色が muted でない: {color}")
    check(not slide_xml(out, 2).xpath(f"//a:t[contains(text(), '月末')]", namespaces=NS), "書かない台本に脚注が出た")


def t_table_tone_dim_legend():
    """table: セルの tone の地・文字色、薄くした行の文字色、凡例の見本（塗り・枠・角丸）と文字（見本の右・重ならない）が PPTX に写る。"""
    import deck
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    up = lambda h: h[1:].upper()
    rows = [["資料", "完了", {"text": "3日", "tone": "good"}], ["写真", "進行", {"text": "9日", "tone": "warn"}],
            ["表紙", "進行", {"text": "20日", "tone": "bad"}], ["帯", "見送り", {"text": "12日", "tone": "warn"}], ["目次", "見送り", "5日"]]
    legend = [{"tone": "good", "text": "予定どおり"}, {"tone": "warn", "text": "1週間を過ぎた"}, {"tone": "bad", "text": "2週間を過ぎた"}, {"tone": "dim", "text": "見送り"}]
    sc = TMP / "tone.json"
    sc.write_text(json.dumps({"title": "t", "slides": [{"type": "table", "headline": "h", "columns": ["品目", "状態", "経過"], "rows": rows, "highlight": 3,
                                                       "dim_rows": [4, 5], "legend": legend, "source": "注記"}]}, ensure_ascii=False), encoding="utf-8")
    html, _ = deck.build_html(sc, TMP / "tone-deck")
    out = TMP / "tone.pptx"
    render.render(html, out)
    x = slide_xml(out, 1)
    fills = {c.upper() for c in x.xpath("//p:sp/p:spPr/a:solidFill/a:srgbClr/@val", namespaces=NS)}
    for role in ("tone-good-bg", "tone-warn-bg", "tone-bad-bg"):
        check(up(pal[role]) in fills, f"セルの地 {role} が PPTX に無い")
    color_of = lambda word: x.xpath(f"//p:sp[.//a:t='{word}']//a:rPr/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper()
    for word, role in (("3日", "tone-good-text"), ("9日", "tone-warn-text"), ("20日", "tone-bad-text")):
        check(color_of(word) == up(pal[role]), f"{word} の文字色 {color_of(word)} が {role} でない")
    for word in ("帯", "見送り", "12日", "5日", "目次"):        # 薄くした行（4・5 行目）は tone があっても muted。1 列目の太字は残す
        check(color_of(word) == up(pal["muted"]), f"薄い行の {word} の文字色が muted でない: {color_of(word)}")
    check(color_of("資料") == up(pal["ink"]), "薄くしない行は ink のまま")
    check(color_of("12日") != up(pal["tone-warn-text"]), "薄い行に tone の文字色が残った")
    bold = x.xpath("//p:sp[.//a:t='帯']//a:rPr/@b", namespaces=NS)
    check(bold == ["1"], f"薄い行の 1 列目の太字が消えた: {bold}")
    shapes = {}                                                   # 凡例の部品: 名前の通し番号 → (種類, 塗り, 枠, x, 幅, y, 文字)
    for sp in x.xpath("//p:sp[starts-with(p:nvSpPr/p:cNvPr/@name, '色の凡例/')]", namespaces=NS):
        n = int(sp.xpath("p:nvSpPr/p:cNvPr/@name", namespaces=NS)[0].split("/")[1])
        g = lambda path: (sp.xpath(path, namespaces=NS) or [None])[0]
        shapes[n] = (g(".//a:prstGeom/@prst"), (g("p:spPr/a:solidFill/a:srgbClr/@val") or "").upper(), (g("p:spPr/a:ln/a:solidFill/a:srgbClr/@val") or "").upper(),
                     int(g(".//a:off/@x")), int(g(".//a:ext/@cx")), int(g(".//a:off/@y")), "".join(sp.xpath(".//a:t/text()", namespaces=NS)))
    check(len(shapes) == 8, f"凡例の部品の数: {len(shapes)}")
    want = [("tone-good-bg", "tone-good-text"), ("tone-warn-bg", "tone-warn-text"), ("tone-bad-bg", "tone-bad-text"), ("muted", "muted")]
    for k, (bgk, linek) in enumerate(want):
        sw, tx = shapes[2 * k], shapes[2 * k + 1]
        check(sw[0] == "roundRect" and sw[1] == up(pal[bgk]) and sw[2] == up(pal[linek]), f"{k + 1} 個目の見本（角丸・塗り・枠）: {sw}")
        check(tx[6] == legend[k]["text"] and tx[3] >= sw[3] + sw[4], f"{k + 1} 個目の文字が見本の右にない: {sw} {tx}")
        check(tx[1] == "" and color_of(legend[k]["text"]) == up(pal["muted"]), f"{k + 1} 個目の説明の文字色")
        if k:
            prev = shapes[2 * k - 1]
            check(sw[3] >= prev[3] + prev[4], f"項目が重なる: {prev} {sw}")
    src_y = int(x.xpath("//p:sp[.//a:t='注記']//a:off/@y", namespaces=NS)[0])
    check(src_y > shapes[7][5], "source が凡例の下にない")


def t_verify_mismatch():
    """verify: ノートの［クリック］とクリックの数のずれを rc=2 で拾う。合っていれば rc=0。"""
    prims = [{"type": "rect", "unit": "u1", "rect": {"x": 10, "y": 10, "w": 50, "h": 50}, "fill": {"hex": "00ff00", "a": 1}}]
    good = {"slides": [{"id": "s1", "notes": "［クリック］出す", "units": {"u1": {"step": 1, "fx": "pop"}}, "prims": prims}]}
    bad = {"slides": [{"id": "s1", "notes": "［クリック］出す［クリック］もう1つ", "units": {"u1": {"step": 1, "fx": "pop"}}, "prims": prims}]}
    rg = pptx_writer.write(good, TMP / "v-good.pptx")
    rb = pptx_writer.write(bad, TMP / "v-bad.pptx")
    check(not rg["mismatch"] and rb["mismatch"] == [1], f"report のずれ: {rg['mismatch']} / {rb['mismatch']}")
    cli = [sys.executable, str(SKILL / "scripts" / "verify.py")]
    r = subprocess.run(cli + [str(TMP / "v-good.pptx")], capture_output=True, text=True, encoding="utf-8")
    check(r.returncode == 0 and "ALL OK" in r.stdout, f"合っているのに rc={r.returncode}: {r.stdout}")
    r = subprocess.run(cli + [str(TMP / "v-bad.pptx")], capture_output=True, text=True, encoding="utf-8")
    check(r.returncode == 2 and "NG" in r.stdout, f"ずれを拾わない: rc={r.returncode} {r.stdout}")


def t_verify_odp():
    """verify --odp: LibreOffice の読み取りの効果の数が report と一致（soffice が無ければ飛ばす）。"""
    if not (shutil.which("soffice") or Path("/opt/homebrew/bin/soffice").exists()):
        print("soffice が無いので飛ばす")
        return
    out, rep = rendered_ok()
    res = verify.verify(out, odp=True)
    check(res["ok"] and res["odp"]["odp"] == [s["effects"] for s in rep["slides"]], f"ODP の効果の数: {res['odp']}")


def t_from_video_rules():
    """from_video: 1文 = 1クリック・型が出すものは自動・html-video の印（× ・チェック・ハンコ・手書き・斜線）と知らない印・章カード。"""
    def rect(u, x, y=100, w=80, h=60, typ="rect"):
        return {"type": typ, "unit": u, "rect": {"x": x, "y": y, "w": w, "h": h}, "fill": {"hex": "ffffff", "a": 1}}

    def path(u, x1, x2):
        return {"type": "path", "unit": u, "subs": [{"pts": [[x1, 300], [x2, 302]], "closed": False}],
                "stroke": {"hex": "e8457d", "a": 1}, "sw": 7}

    U = lambda t, kind: {"step": None, "fx": None, "delay": 0, "keep": None, "name": "", "t": t, "kind": kind}
    s1 = {"id": "s1", "start": 0, "end": 10, "prims": [
        rect("frame-board", 0, 0, 1340, 810), rect("s1-heading", 80, 64), rect("s1-c1", 100), rect("s1-c2", 300),
        {"type": "text", "unit": None, "rect": {"x": 310, "y": 110, "w": 40, "h": 30}, "runs": [{"text": "中", "size": 24}]},
        path("m1-s1-c1", 500, 400), path("m2-s1-c2", 300, 380), path("m3-s1-c2", 300, 380), path("m4-s1-c1", 300, 380),
        path("m5-s1-c2", 300, 380), path("m6-s1-c1", 300, 380), path("m7-s1-c1", 300, 380)],
        "units": {"frame-board": {"step": None, "keep": "board"}, "s1-heading": U(0.15, "rise"), "s1-c1": U(1.0, "pop"),
                  "s1-c2": U(4.0, "pop"), "m1-s1-c1": U(1.5, "mark"), "m2-s1-c2": U(4.5, "mark"), "m3-s1-c2": U(5.0, "mark"),
                  "m4-s1-c1": U(6.0, "mark"), "m5-s1-c2": U(7.0, "mark"), "m6-s1-c1": U(8.0, "mark"), "m7-s1-c1": U(8.5, "mark")}}
    s2 = {"id": "s2", "start": 10, "end": 12, "prims": [rect("s2-chapter-title", 170, 400)],
          "units": {"s2-chapter-title": U(10.3, "rise")}}
    s3 = {"id": "s3", "start": 12, "end": 15, "prims": [rect("s3-heading", 80, 64)], "units": {"s3-heading": U(12.15, "rise")}}
    parts = {"slides": [s1, s2, s3]}
    script = {"title": "t", "scenes": [
        {"layout": "cards", "lines": [
            {"text": "一つめ。", "show": [{"target": "c1"}], "marks": [{"kind": "underline", "target": "c1"}]},
            {"text": "二つめ。", "show": [{"target": "c2", "effect": "slide"}],
             "marks": [{"kind": "cross", "target": "c2"}, {"kind": "stamp", "target": "c2", "text": "済"}]},
            {"text": "何も出さない文。"},
            {"text": "四つめ。", "marks": [{"kind": "check", "target": "c1"}, {"kind": "write", "target": "c2", "text": "大事"},
                                          {"kind": "strike", "target": "c1"}, {"kind": "zigzag", "target": "c1"}]}]},
        {"layout": "chapter", "title": "章", "lines": []},
        {"layout": "cards", "lines": [{"text": "章のあと。"}]}]}
    from_video.convert(parts, script, {"scenes": [{"layout": "cards"}, {"layout": "chapter"}, {"layout": "cards"}]})
    u = s1["units"]
    got = {k: (v["step"], v["fx"]) for k, v in u.items() if not k.startswith("frame")}
    want = {"s1-heading": (0, "rise"), "s1-c1": (1, "pop"), "m1-s1-c1": (1, "wipe-left"),       # 右から左へ引いた下線
            "s1-c2": (2, "wipe-right"), "m2-s1-c2": (2, "wipe-right"), "m3-s1-c2": (2, "fade"),
            "m4-s1-c1": (3, "wipe-right"), "m5-s1-c2": (3, "wipe-right"), "m6-s1-c1": (3, "wipe-right"), "m7-s1-c1": (3, "fade")}
    check(got == want, f"段と出方: {got}")
    check(u["m2-s1-c2"]["delay"] == 500 and u["m3-s1-c2"]["delay"] == 1000, f"遅れ: {u['m2-s1-c2']['delay']}, {u['m3-s1-c2']['delay']}")
    check(u["frame-board"]["step"] is None, "外枠に段が付いた")
    check(u["s1-heading"].get("keep") == "heading", "見出しが !!heading になっていない")
    check(s1["prims"][4]["unit"] == "s1-c2", "箱の上の文字が箱と一緒に出ない")
    check(s1["notes"].split("\n") == ["［クリック］一つめ。", "［クリック］二つめ。", "何も出さない文。", "［クリック］四つめ。"],
          f"ノート: {s1['notes']!r}")
    check([s["transition"] for s in parts["slides"]] == ["fade", "fade", "fade"], "章カードの前後がフェードでない")
    check(s2["units"]["s2-chapter-title"]["step"] == 0 and s2["notes"] == "", "章カードの文字が自動で出ない")
    rep = pptx_writer.write(parts, TMP / "fv.pptx")
    check([s["steps"] for s in rep["slides"]] == [3, 0, 0] and not rep["mismatch"], f"PPTX の段: {rep['slides']}")


# --- Codex レビュー r1 の回帰 ------------------------------------------------------

EDGE = FIX / "contract-edge.html"


def edge():
    return render.extract(EDGE)


def text_of(p):
    return "".join(r.get("text", "") for r in p.get("runs") or [])


def t_r1_zorder():
    """①兄弟の z-index を描画順に並べる（先に書いた z-index:2 が手前）・flex の order は rc=2。"""
    d = edge()
    names = [p.get("name") for p in d["slides"][0]["prims"]]
    check(names.index("back") < names.index("front"), f"z-index の手前の箱が奥にある: {names}")
    rules = {(v["slide"], v["rule"]) for v in render.errors(d["violations"])}
    check((1, "order") in rules, f"order を止めない: {rules}")


def t_r1_portrait():
    """②縦の動画は rc=2（縦の動画は未対応）。"""
    parts = {"canvas": {"w": 1080, "h": 1920}, "slides": [{"id": "s1", "prims": [], "units": {}}]}
    script = {"scenes": [{"layout": "cover", "lines": []}]}
    for p, b in ((parts, None), ({**parts, "canvas": {"w": 1920, "h": 1080}}, {"orientation": "portrait", "canvas": [1080, 1920]})):
        try:
            from_video.convert(p, script, b)
            raise AssertionError("縦の動画を通した")
        except ValueError as e:
            check("縦の動画は未対応" in str(e), str(e))


def t_r1_wrap_spaces():
    """③自動で折り返した所の単語の間の空白を残す。改行を固定するときは行の終わりの空白を落として a:br。"""
    d = edge()
    ps = {p.get("name"): p for p in d["slides"][1]["prims"]}
    check(ps["wrap"]["lines"] == 2 and text_of(ps["wrap"]) == "Hello world again", f"空白が消えた: {text_of(ps['wrap'])!r}")
    rep = pptx_writer.write({"slides": [{"id": "s", "prims": [ps["wrap"], ps["balanced"]], "units": {}}]}, TMP / "ws.pptx")
    x = slide_xml(TMP / "ws.pptx", 1)
    bodies = x.xpath("//p:txBody", namespaces=NS)
    free = "".join(bodies[0].xpath(".//a:t/text()", namespaces=NS))
    check(free == "Hello world again" and not bodies[0].xpath(".//a:br", namespaces=NS), f"自由に折る文字: {free!r}")
    pinned = [t for t in bodies[1].xpath(".//a:t/text()", namespaces=NS)]
    check(bodies[1].xpath(".//a:br", namespaces=NS) and pinned == ["Hello world", "again"], f"改行を固定した文字: {pinned}")


def t_r1_open_fill():
    """④塗りのある開いた SVG パスも塗る（塗りは閉じた path・線は開いたままの path に分ける）。"""
    tri = {"type": "path", "unit": None, "subs": [{"pts": [[0, 0], [100, 0], [0, 100]], "closed": False}],
           "fill": {"hex": "ff0000", "a": 1}}
    stroked = {**tri, "stroke": {"hex": "0000ff", "a": 1}, "sw": 4}
    pptx_writer.write({"slides": [{"id": "s", "prims": [tri, stroked], "units": {}}]}, TMP / "of.pptx")
    sps = slide_xml(TMP / "of.pptx", 1).xpath("//p:sp", namespaces=NS)
    a = sps[0].xpath(".//a:pathLst/a:path", namespaces=NS)
    check(len(a) == 1 and a[0].xpath("a:close", namespaces=NS) and sps[0].xpath("p:spPr/a:solidFill", namespaces=NS),
          "線の無い開いたパスが閉じて塗られていない")
    b = sps[1].xpath(".//a:pathLst/a:path", namespaces=NS)
    check(len(b) == 2 and b[0].get("stroke") == "0" and b[0].xpath("a:close", namespaces=NS)
          and b[1].get("fill") == "none" and not b[1].xpath("a:close", namespaces=NS), "塗りと線の path が分かれていない")


def t_r1_svg_tspan():
    """⑤位置指定の tspan があっても、親 text の直の文字を行として拾う。"""
    texts = [text_of(p) for p in edge()["slides"][1]["prims"] if p["type"] == "text" and not p.get("name")]
    check("Hello" in texts and "World tail" in texts, f"SVG の行: {texts}")


def t_r1_inline_step():
    """⑥文字の中の inline 要素の data-step は rc=2（data-step はブロック要素に付ける）。"""
    vs = [v for v in render.errors(edge()["violations"]) if v["rule"] == "inline-step"]
    check(len(vs) == 1 and vs[0]["slide"] == 2 and "span" in vs[0]["el"], f"inline の data-step: {vs}")
    check("`data-step` はブロック要素に付ける" in (SKILL / "references" / "contract.md").read_text(encoding="utf-8"), "contract.md に書いていない")


def t_r1_hidden_inline():
    """⑦隠れた inline（visibility: hidden・opacity: 0）の文字は出さない。半透明は run の色に掛ける。"""
    p = next(p for p in edge()["slides"][1]["prims"] if p.get("name") == "hidden")
    runs = [(r["text"], r["color"]["a"]) for r in p["runs"]]
    check(runs == [("見える", 1), ("半分", 0.5)], f"隠れた run: {runs}")


def t_r1_section_checks():
    """⑧section 自身の背景画像・::before の content も rc=2。"""
    got = {(v["rule"], v["el"]) for v in render.errors(edge()["violations"]) if v["slide"] == 3}
    check({("background-image", "section#s3"), ("pseudo-content", "section#s3")} <= got, f"section の違反: {got}")


def t_r1_nested_show():
    """⑨show の対象の中で自分も出方を持つ単位は、対象と同じ文のクリックで出る（自動で先に出ない）。"""
    U = lambda t, kind, parent=None: {"step": None, "fx": None, "delay": 0, "keep": None, "name": "", "t": t, "kind": kind, "parent": parent}
    box = lambda u, x: {"type": "rect", "unit": u, "rect": {"x": x, "y": 100, "w": 80, "h": 60}, "fill": {"hex": "ffffff", "a": 1}}
    s1 = {"id": "s1", "start": 0, "end": 10, "prims": [box("s1-heading", 0), box("s1-svg-g", 100), box("s1-svg-g-fill", 100), box("s1-svg-g-label", 100)],
          "units": {"s1-heading": U(0.15, "rise"), "s1-svg-g": U(2.0, "fade"), "s1-svg-g-fill": U(2.6, "fade", "s1-svg-g"),
                    "s1-svg-g-label": U(2.6, "fade", "s1-svg-g-fill")}}
    script = {"scenes": [{"layout": "picture", "lines": [{"text": "前置き。"}, {"text": "描く。", "show": [{"target": "svg.g", "effect": "draw"}]}]}]}
    from_video.convert({"slides": [s1]}, script, {"scenes": [{"layout": "picture"}]})
    u = s1["units"]
    got = {k: (v["step"], v["fx"]) for k, v in u.items()}
    check(got == {"s1-heading": (0, "rise"), "s1-svg-g": (1, "fade"), "s1-svg-g-fill": (1, "fade"), "s1-svg-g-label": (1, "fade")}, f"段: {got}")
    check(u["s1-svg-g-fill"]["delay"] == 500, f"中の要素の遅れ: {u['s1-svg-g-fill']['delay']}")
    check(s1["notes"] == "前置き。\n［クリック］描く。", f"ノート: {s1['notes']!r}")


# --- Codex レビュー r2 の回帰 ------------------------------------------------------

def t_r2_zindex_context():
    """重ね順の文脈を作らない親の中の z-index は rc=2。section の直下・文脈を作る親（position＋z-index）の中は通す。"""
    vs = [v for v in render.errors(edge()["violations"]) if v["rule"] == "z-index-context"]
    els = [v["el"] for v in vs]
    check(len(vs) == 1 and "#z-in-plain" in els[0], f"z-index の文脈の違反: {els}")
    check("`z-index` は section の直下か" in (SKILL / "references" / "contract.md").read_text(encoding="utf-8"), "contract.md に書いていない")


def t_r2_transparent_text():
    """color: transparent の文字は run に出さない（全部透明なら文字の箱ごと出さない）。writer も透明な run を書かない。"""
    ps = {p.get("name"): p for p in edge()["slides"][1]["prims"]}
    check(text_of(ps["clear"]) == "見える", f"透明な run が出た: {text_of(ps['clear'])!r}")
    check("all-clear" not in ps, "全部透明な文字の箱が出た")
    p = {"type": "text", "unit": None, "rect": {"x": 0, "y": 0, "w": 200, "h": 60}, "lines": 1, "lh": 60,
         "runs": [{"text": "見える", "size": 40, "color": {"hex": "222222", "a": 1}}, {"text": "透明", "size": 40, "color": {"hex": "000000", "a": 0}}]}
    pptx_writer.write({"slides": [{"id": "s", "prims": [p], "units": {}}]}, TMP / "tr.pptx")
    texts = slide_xml(TMP / "tr.pptx", 1).xpath("//a:t/text()", namespaces=NS)
    check(texts == ["見える"], f"writer が透明な run を書いた: {texts}")


# --- Codex レビュー r3 の回帰 ------------------------------------------------------

def t_r3_img_alt():
    """img の alt を PPTX の代替テキスト（p:cNvPr/@descr）に引き継ぐ。cover の切り抜きは小数4桁。"""
    parts = render.extract(OK)
    img = next(p for p in parts["slides"][0]["prims"] if p["type"] == "img")
    check(img.get("alt") == "宅配の箱が並んだ玄関", f"部品の alt: {img.get('alt')!r}")
    check(img.get("crop") and any(round(v, 2) != v for v in img["crop"].values()), f"切り抜きが2桁に丸められている: {img.get('crop')}")
    out, _ = rendered_ok()
    descr = slide_xml(out, 1).xpath("//p:pic/p:nvPicPr/p:cNvPr/@descr", namespaces=NS)
    check(descr == ["宅配の箱が並んだ玄関"], f"PPTX の descr: {descr}")


def t_kpi_tone_group():
    """kpi: 増減の札の塗り・文字色と、group のカードの面・枠・札が PPTX に写る。3枚はカードが使える高さいっぱい。"""
    import deck
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    items = [{"label": "a", "value": "1", "delta": "−4", "trend": "down"}, {"label": "b", "value": "2", "delta": "+3", "trend": "up"},
             {"label": "c", "value": "3", "delta": "±0", "trend": "flat", "group": {"label": "手作業", "tone": "b"}}]
    sc = TMP / "kpi.json"
    sc.write_text(json.dumps({"title": "t", "slides": [{"type": "kpi", "headline": "h", "items": items}]}, ensure_ascii=False), encoding="utf-8")
    html, _ = deck.build_html(sc, TMP / "kpi-deck")
    out = TMP / "kpi.pptx"
    render.render(html, out)
    x = slide_xml(out, 1)
    up = lambda h: h[1:].upper()
    fills = {}                                       # 角丸の塗りの色 → 高さ（EMU）の並び
    for sp in x.xpath("//p:sp[.//a:prstGeom/@prst='roundRect']", namespaces=NS):
        fill = sp.xpath("p:spPr/a:solidFill/a:srgbClr/@val", namespaces=NS)
        fills.setdefault(fill[0].upper() if fill else None, []).append(int(sp.xpath("p:spPr/a:xfrm/a:ext/@cy", namespaces=NS)[0]))
    for role in ("tone-bad-bg", "tone-good-bg", "tone-neutral-bg", "group-b-bg"):
        check(up(pal[role]) in fills, f"{role} の塗りが PPTX に無い: {sorted(k for k in fills if k)}")
    runs = {c.upper() for c in x.xpath("//a:rPr/a:solidFill/a:srgbClr/@val", namespaces=NS)}
    for role in ("tone-bad-text", "tone-good-text", "tone-neutral-text", "group-b-text"):
        check(up(pal[role]) in runs, f"{role} の文字色が PPTX に無い")
    check(up(pal["group-b-rule"]) in {c.upper() for c in x.xpath("//a:ln/a:solidFill/a:srgbClr/@val", namespaces=NS)}, "group の枠の色が無い")
    tall = max(fills[up(pal["group-b-bg"])])
    # 強める箱（look: emphasis strong）: 面の上の札は面と反対の色・増減の札は tone の色のまま
    sty = TMP / "strong.yaml"
    sty.write_text("name: strong-line\ndescription: 試験\nlook:\n  emphasis: strong\n  surface: line\n", encoding="utf-8")
    items2 = [{"label": "a", "value": "1", "accent": True, "delta": "+1", "trend": "up"}] + items[1:]
    sc.write_text(json.dumps({"title": "t", "slides": [{"type": "kpi", "headline": "h", "items": items2}]}, ensure_ascii=False), encoding="utf-8")
    html, _ = deck.build_html(sc, TMP / "kpi-deck2", style=str(sty))
    render.render(html, TMP / "kpi2.pptx")
    x2 = slide_xml(TMP / "kpi2.pptx", 1)
    color_of = lambda word: x2.xpath(f"//p:sp[.//a:t='{word}']//a:rPr/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper()
    check(color_of("注目") == up(pal["accent-strong"]), f"強める箱の札の文字が面と同じ色: {color_of('注目')}")
    check(color_of("▲ +1") == up(pal["tone-good-text"]), f"強める箱の増減の札が tone の色でない: {color_of('▲ +1')}")
    check(tall > 330 * 12700, f"3枚のカードが高さいっぱいに伸びていない: {tall / 12700:.0f}pt")   # 版は 1080px＝540pt。使える高さは 700px 余り

def t_table_summary():
    """table の summary: カード（地・枠・札・増減の札・文字）と表・凡例が PPTX に写る。カードは表の上・同じ幅で並び、段は 1→2 の2クリック。"""
    import deck
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    up = lambda h: h[1:].upper()
    summary = [{"label": "今月の改善", "value": "9", "unit": "件", "delta": "+3件", "trend": "up"},
               {"label": "累計", "value": "15", "unit": "件", "delta": "−1件", "trend": "down", "group": {"label": "全体", "tone": "b"}},
               {"label": "着手中", "value": "4", "unit": "件", "note": "来月に持ち越す"}]
    sc = TMP / "sum.json"
    sc.write_text(json.dumps({"title": "t", "slides": [{"type": "table", "headline": "h", "summary": summary, "columns": ["日付", "内容"],
                                                       "rows": [["10/02", "手順を減らした"], ["10/09", "項目を見直した"]],
                                                       "legend": [{"tone": "good", "text": "効果あり"}], "source": "注記", "talk": ["", "", ""]}]}, ensure_ascii=False), encoding="utf-8")
    html, _ = deck.build_html(sc, TMP / "sum-deck")
    out = TMP / "sum.pptx"
    rep = render.render(html, out)
    check(rep["totals"]["steps"] == 2 and rep["totals"]["notes_clicks"] == 2, f"クリックの数: {rep['totals']}")
    x = slide_xml(out, 1)
    cards = [sp for sp in x.xpath("//p:sp", namespaces=NS) if re.fullmatch(r"指標\d/0", sp.xpath("p:nvSpPr/p:cNvPr/@name", namespaces=NS)[0])]
    geo = lambda sp: [int(v) for v in sp.xpath("p:spPr/a:xfrm/a:off/@x | p:spPr/a:xfrm/a:off/@y | p:spPr/a:xfrm/a:ext/@cx | p:spPr/a:xfrm/a:ext/@cy", namespaces=NS)]
    boxes = [geo(sp) for sp in cards if sp.xpath(".//a:prstGeom/@prst", namespaces=NS) == ["roundRect"] and sp.xpath("p:spPr/a:ln", namespaces=NS)]
    check(len(boxes) == 3, f"カードの地と枠が3枚ぶん無い: {len(boxes)}")
    check(len({b[2] for b in boxes}) == 1 and len({b[3] for b in boxes}) == 1 and len({b[1] for b in boxes}) == 1, f"カードの幅・高さ・上端がそろわない: {boxes}")
    xs = sorted(b[0] for b in boxes)
    check(abs((xs[1] - xs[0]) - (xs[2] - xs[1])) <= 1 and xs[1] - xs[0] > boxes[0][2], f"カードが等間隔に並ばない: {xs}")
    fills = {c.upper() for c in x.xpath("//p:sp/p:spPr/a:solidFill/a:srgbClr/@val", namespaces=NS)}
    for role in ("tone-good-bg", "tone-bad-bg", "group-b-bg"):
        check(up(pal[role]) in fills, f"{role} の塗りが PPTX に無い")
    check(up(pal["group-b-rule"]) in {c.upper() for c in x.xpath("//a:ln/a:solidFill/a:srgbClr/@val", namespaces=NS)}, "group の枠の色が無い")
    runs = {c.upper() for c in x.xpath("//a:rPr/a:solidFill/a:srgbClr/@val", namespaces=NS)}
    for role in ("tone-good-text", "tone-bad-text", "group-b-text"):
        check(up(pal[role]) in runs, f"{role} の文字色が無い")
    texts = "".join(x.xpath("//a:t/text()", namespaces=NS))
    for word in ("今月の改善", "累計", "全体", "▲ +3件", "▼ −1件", "来月に持ち越す", "10/09", "項目を見直した", "効果あり", "注記"):
        check(word in texts, f"{word} が PPTX に無い")
    card_bottom = max(b[1] + b[3] for b in boxes)
    y_of = lambda word: int(x.xpath(f"//p:sp[.//a:t='{word}']//a:off/@y", namespaces=NS)[0])
    check(y_of("日付") >= card_bottom, "表がカードの下に来ていない")
    check(y_of("効果あり") > y_of("10/09") and y_of("注記") > y_of("効果あり"), "表 → 凡例 → source の順でない")
    check(boxes[0][3] <= 384 * 12700 / 2, f"カードの帯の高さがページの半分（1080px の半分未満）を越えた: {boxes[0][3] / 12700:.0f}pt")



def t_table_summary_chip_mix():
    """table の summary: 札（group）のあるカードとないカードが混ざる帯でも、数字の上端・増減の札の上端がそろい、帯は上限内で表と重ならない（2〜4枚）。"""
    import deck
    g = lambda tone: {"label": "自動", "tone": tone}
    cards = [{"label": "受付件数", "value": "11", "unit": "件", "delta": "+12%", "trend": "up", "group": g("a")},
             {"label": "平均時間", "value": "22", "unit": "分", "delta": "−4分", "trend": "down"},
             {"label": "手戻り", "value": "33", "unit": "件", "note": "前月比", "group": g("b")},
             {"label": "満足度", "value": "44", "unit": "点", "delta": "±0", "trend": "flat"}]
    for n in (2, 3, 4):
        summary = cards[:n]
        sc = TMP / f"sum-mix{n}.json"
        sc.write_text(json.dumps({"title": "t", "slides": [{"type": "table", "headline": "h", "summary": summary, "columns": ["日付", "内容"],
                                                           "rows": [["10/02", "手順を減らした"], ["10/09", "項目を見直した"]]}]}, ensure_ascii=False), encoding="utf-8")
        html, _ = deck.build_html(sc, TMP / f"sum-mix-deck{n}")
        out = TMP / f"sum-mix{n}.pptx"
        render.render(html, out)
        x = slide_xml(out, 1)
        y_of = lambda word: int(x.xpath(f"//p:sp[.//a:t='{word}']/p:spPr/a:xfrm/a:off/@y", namespaces=NS)[0])
        ys = [y_of(c["value"]) for c in summary]
        check(len(set(ys)) == 1, f"{n}枚: 数字の上端がそろわない: {ys}")
        mark = {"up": "▲ ", "down": "▼ ", "flat": "→ "}
        dys = {y_of(mark[c["trend"]] + c["delta"]) for c in summary if c.get("delta")}
        check(len(dys) <= 1, f"{n}枚: 増減の札の上端がそろわない: {dys}")
        boxes = []
        for sp in x.xpath("//p:sp", namespaces=NS):
            if re.fullmatch(r"指標\d/0", sp.xpath("p:nvSpPr/p:cNvPr/@name", namespaces=NS)[0]) and sp.xpath("p:spPr/a:ln", namespaces=NS) \
                    and sp.xpath(".//a:prstGeom/@prst", namespaces=NS) == ["roundRect"]:
                boxes.append([int(v) for v in sp.xpath("p:spPr/a:xfrm/a:off/@y | p:spPr/a:xfrm/a:ext/@cy", namespaces=NS)])
        check(len(boxes) == n and len({b[0] for b in boxes}) == 1 and len({b[1] for b in boxes}) == 1, f"{n}枚: カードの上端・高さがそろわない: {boxes}")
        check(boxes[0][1] <= 384 * 12700 / 2, f"{n}枚: 帯が上限を越えた: {boxes[0][1] / 12700:.0f}pt")
        check(y_of("日付") >= boxes[0][0] + boxes[0][1], f"{n}枚: 表が帯と重なる")


def t_kpi_reference():
    """kpi・table summary の reference: 参考のカードの地・点線の枠（PPTX の prstDash）・札・文字の濃さ・灰の増減の札が PPTX に写り、主役と混ぜても数字の上端がそろう。"""
    import deck
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    up = lambda h: h[1:].upper()
    items = [{"label": "自動で処理", "value": "1200", "unit": "件", "delta": "+35%", "trend": "up", "note": "前月比"},
             {"label": "処理時間", "value": "4", "delta": "-60%", "trend": "down", "tone": "good"},
             {"label": "手作業で処理", "value": "180", "unit": "件", "delta": "+9%", "trend": "up", "tone": "bad", "reference": True},
             {"label": "手作業の時間", "value": "25", "reference": True}]
    docs = {"kpi": {"type": "kpi", "headline": "h", "items": items},
            "summary": {"type": "table", "headline": "h", "columns": ["a", "b"], "rows": [["1", "2"]], "summary": items[1:]}}
    for name, slide in docs.items():
        sc = TMP / f"ref-{name}.json"
        sc.write_text(json.dumps({"title": "t", "slides": [slide]}, ensure_ascii=False), encoding="utf-8")
        html, _ = deck.build_html(sc, TMP / f"ref-deck-{name}")
        out = TMP / f"ref-{name}.pptx"
        render.render(html, out)
        x = slide_xml(out, 1)
        vals = [it["value"] for it in (items if name == "kpi" else items[1:])]
        ys = [int(x.xpath(f"//p:sp[.//a:t='{v}']/p:spPr/a:xfrm/a:off/@y", namespaces=NS)[0]) for v in vals]
        check(len(set(ys)) == 1, f"{name}: 数字の上端がそろわない: {ys}")
        dashed = [sp for sp in x.xpath("//p:sp[.//a:prstGeom/@prst='roundRect']", namespaces=NS) if sp.xpath("p:spPr/a:ln/a:prstDash/@val", namespaces=NS) == ["dash"]]
        check(len(dashed) == 2, f"{name}: 点線の枠が参考のカードの数（2）だけ無い: {len(dashed)}")
        for sp in dashed:
            check(sp.xpath("p:spPr/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper() == up(pal["ref-bg"]), f"{name}: 参考の地の色が違う")
            check(sp.xpath("p:spPr/a:ln/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper() == up(pal["ref-rule"]), f"{name}: 参考の枠の色が違う")
        color_of = lambda word: x.xpath(f"//p:sp[.//a:t='{word}']//a:rPr/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper()
        check(len(x.xpath("//a:t[text()='参考']", namespaces=NS)) == 2, f"{name}: 参考の札の語が2つ無い")
        check(color_of("参考") == up(pal["ref-text"]), f"{name}: 札の文字色: {color_of('参考')}")
        label = "手作業で処理" if name == "kpi" else "手作業の時間"
        check(color_of(label) == up(pal["ref-text"]) and color_of(label) != up(pal["ink"]), f"{name}: 参考のラベルの文字が薄くない")
        check(color_of("▲ +9%") == up(pal["tone-neutral-text"]), f"{name}: 参考の増減の札が灰でない: {color_of('▲ +9%')}")
        check(color_of("▼ -60%") == up(pal["tone-good-text"]), f"{name}: 主役の増減の札の色が変わった")


def t_kpi_chip_align():
    """kpi: 増減の札に tone の色の枠が付き（PPTX の線）、札のあるカードとないカードが混ざる行でも数字の上端がそろう（3枚・4枚・強い look）。"""
    import deck
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    up = lambda h: h[1:].upper()
    three = [{"label": "受付件数", "value": "11", "unit": "件", "delta": "+12%", "trend": "up", "note": "前月比", "group": {"label": "自動", "tone": "a"}},
             {"label": "処理にかかった時間の平均", "value": "22", "delta": "−4分", "trend": "down"},
             {"label": "手戻り", "value": "33", "group": {"label": "手作業", "tone": "b"}}]
    strong = TMP / "align-strong.yaml"
    strong.write_text("name: strong\ndescription: 試験\nlook:\n  emphasis: strong\n  surface: line\n", encoding="utf-8")
    cases = {"three": (three, None), "four": (three + [{"label": "d", "value": "44", "delta": "±0", "trend": "flat"}], None),
             "strong": ([dict(three[0], accent=True, group=None)] + three[1:], str(strong))}
    for name, (items, style) in cases.items():
        items = [{k: v for k, v in it.items() if v is not None} for it in items]
        sc = TMP / f"align-{name}.json"
        sc.write_text(json.dumps({"title": "t", "slides": [{"type": "kpi", "headline": "h", "items": items}]}, ensure_ascii=False), encoding="utf-8")
        html, _ = deck.build_html(sc, TMP / f"align-deck-{name}", **({"style": style} if style else {}))
        out = TMP / f"align-{name}.pptx"
        render.render(html, out)
        x = slide_xml(out, 1)
        ys = [int(x.xpath(f"//p:sp[.//a:t='{it['value']}']/p:spPr/a:xfrm/a:off/@y", namespaces=NS)[0]) for it in items]
        check(len(set(ys)) == 1, f"{name}: 数字の上端がそろわない: {ys}")
        # 増減の札: 角丸の枠が tone の文字色（強い look の面の上では札の地の色）
        lines = {}
        for sp in x.xpath("//p:sp[.//a:prstGeom/@prst='roundRect']", namespaces=NS):
            fill = sp.xpath("p:spPr/a:solidFill/a:srgbClr/@val", namespaces=NS)
            ln = sp.xpath("p:spPr/a:ln/a:solidFill/a:srgbClr/@val", namespaces=NS)
            if fill:
                lines[fill[0].upper()] = ln[0].upper() if ln else None
        for tone in ("good", "bad"):
            bg = up(pal[f"tone-{tone}-bg"])
            check(bg in lines, f"{name}: {tone} の増減の札の塗りが PPTX に無い: {lines}")
            if name == "strong" and tone == "good":
                check(lines[bg] in (None, bg), f"{name}: 濃い面の上の札に、面に沈む色の枠が付いている: {lines[bg]}")   # 札の地の色が輪郭
            else:
                check(lines[bg] == up(pal[f"tone-{tone}-text"]), f"{name}: {tone} の増減の札の枠が PPTX に無い・色が違う: {lines}")


def t_bar_tone():
    """bar-chart: 棒の塗り・値の文字色・値 0 の札（16px の角丸）が PPTX に写る。tone なしと neutral は灰のまま。"""
    import deck
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    tones = ("good-strong", "good", "good-soft", "warn", "bad")
    bars = [{"label": f"b{i}", "value": 10 - i, "tone": t} for i, t in enumerate(tones)] + \
           [{"label": "z", "value": 0, "tone": "bad"}, {"label": "n", "value": 4, "tone": "neutral"}, {"label": "p", "value": 4}]
    sc = TMP / "bar.json"
    sc.write_text(json.dumps({"title": "t", "slides": [{"type": "bar-chart", "headline": "h", "bars": bars}]}, ensure_ascii=False), encoding="utf-8")
    html, _ = deck.build_html(sc, TMP / "bar-deck")
    out = TMP / "bar.pptx"
    render.render(html, out)
    x = slide_xml(out, 1)
    up = lambda h: h[1:].upper()
    fills = {}                                       # 棒の塗りの色 → 高さ（EMU）の並び
    for sp in x.xpath("//p:sp[p:spPr/a:solidFill/a:srgbClr]", namespaces=NS):
        fills.setdefault(sp.xpath("p:spPr/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper(), []).append(int(sp.xpath("p:spPr/a:xfrm/a:ext/@cy", namespaces=NS)[0]))
    for t in tones:
        check(up(pal[f"bar-{t}"]) in fills, f"bar-{t} の塗りが PPTX に無い: {sorted(fills)}")
    check(len(fills[up(pal["bar"])]) == 2, f"灰の棒は neutral と tone なしの 2 本: {fills[up(pal['bar'])]}")
    zero = [h for h in fills[up(pal["bar-bad"])] if h < 100 * 12700]
    check(len(zero) == 1 and abs(zero[0] / 12700 - 16 * 0.5) < 0.6, f"値 0 の札は高さ 16px（8pt）: {[h / 12700 for h in zero]}")
    runs = {c.upper() for c in x.xpath("//a:rPr/a:solidFill/a:srgbClr/@val", namespaces=NS)}
    for t in ("good-strong", "warn", "bad"):
        check(up(pal[f"bar-{t}-text"]) in runs, f"bar-{t}-text の値の文字色が PPTX に無い")


def t_notice_four():
    """notice: 4個の枠（枠・色帯・札・文字）が PPTX に写る。label_style: chip は角丸の札（白地・kind の色の輪郭と文字）、既定はアイコン。"""
    import deck
    import deck_types as T
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    items = [{"kind": "error", "label": "課題", "title": "返信に平均 3 日かかっていた"},
             {"kind": "warning", "label": "原因", "title": "担当の割り振りが決まっていなかった"},
             {"kind": "info", "label": "対策", "title": "受付の時点で担当を自動で割り振る"},
             {"kind": "success", "label": "アクション", "title": "来月から割り振りの表を運用する", "body": "月末に件数と返信までの日数を見直す"}]
    up = lambda v: (pal[v[len("var(--"):-1]] if v.startswith("var(") else v)[1:].upper()
    for style in ("chip", "icon"):
        sc = TMP / f"nt-{style}.json"
        slide = {"type": "notice", "headline": "h", "items": items}
        if style == "chip":
            slide["label_style"] = "chip"
        sc.write_text(json.dumps({"title": "t", "slides": [slide]}, ensure_ascii=False), encoding="utf-8")
        html, _ = deck.build_html(sc, TMP / f"nt-{style}-deck")
        out = TMP / f"nt-{style}.pptx"
        render.render(html, out)
        x = slide_xml(out, 1)
        for it in items:
            line, strong = T.NOTICE_KINDS[it["kind"]][2:]
            check(up(line) in {c.upper() for c in x.xpath("//a:ln/a:solidFill/a:srgbClr/@val", namespaces=NS)}, f"{style}: {it['kind']} の枠の色が無い")
            check(up(line) in {c.upper() for c in x.xpath("//p:sp/p:spPr/a:solidFill/a:srgbClr/@val", namespaces=NS)}, f"{style}: {it['kind']} の色帯が無い")
            word = x.xpath(f"//p:sp[.//a:t='{it['label']}']", namespaces=NS)
            check(len(word) == 1, f"{style}: 語「{it['label']}」が PPTX に無い")
            check(word[0].xpath(".//a:rPr/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper() == up(strong), f"{style}: 語の色が {it['kind']} の色でない")
            for t_ in (it["title"],):
                check(x.xpath(f"//p:sp[.//a:t='{t_}']", namespaces=NS), f"{style}: 題が無い")
        round_ = x.xpath("//p:sp[p:spPr/a:prstGeom/@prst='roundRect']", namespaces=NS)
        if style == "chip":
            check(len(round_) == 4, f"札は 4 つの角丸: {len(round_)}")
            for sp, it in zip(round_, items):
                strong = up(T.NOTICE_KINDS[it["kind"]][3])
                check(sp.xpath("p:spPr/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper() == "FFFFFF", "札の地は白")
                check(sp.xpath("p:spPr/a:ln/a:solidFill/a:srgbClr/@val", namespaces=NS)[0].upper() == strong, "札の輪郭は kind の色")
            check(not x.xpath("//p:pic | //p:sp[.//a:custGeom]", namespaces=NS), "札のとき、アイコンの図形が出ている")
        else:
            check(not round_ and (x.xpath("//p:pic | //p:sp[.//a:custGeom]", namespaces=NS)), "既定は角丸の札なし・アイコンあり")


def t_notice_radius():
    """notice の look.radius: 枠と白い面が角丸で、色帯は別の四角を出さない（PPTX では帯が角から出ない）。radius を書かないと角丸なし。"""
    import deck
    items = [{"kind": k, "title": "題"} for k in ("info", "success", "warning", "error")]
    got = {}
    for r in ("round", "default"):
        sty = TMP / f"nt-{r}.yaml"
        sty.write_text(f"name: nt-{r}\ndescription: x\nlook:\n  radius: {r}\n", encoding="utf-8")
        sc = TMP / f"nt-{r}.json"
        sc.write_text(json.dumps({"title": "t", "slides": [{"type": "notice", "headline": "h", "items": items}]}, ensure_ascii=False), encoding="utf-8")
        html, _ = deck.build_html(sc, TMP / f"nt-{r}-deck", style=str(sty))
        out = TMP / f"nt-{r}.pptx"
        render.render(html, out)
        x = slide_xml(out, 1)
        got[r] = (len(x.xpath("//p:sp[p:spPr/a:prstGeom/@prst='roundRect']", namespaces=NS)), len(x.xpath("//p:sp[p:spPr/a:prstGeom/@prst='rect']", namespaces=NS)))
    check(got["default"][0] == 0, f"default: 角丸なし: {got['default']}")
    check(got["round"][0] == 8 and got["round"][1] == got["default"][1] - 8, f"round: 枠と白い面が各4つの角丸になり、帯の四角は出ない: {got}")


TESTS = {k[2:]: v for k, v in dict(globals()).items() if k.startswith("t_")}

if __name__ == "__main__":
    # 試験の表示（失敗の報告・skip の理由・同じプロセスで呼んだエンジンの表示）だけは UTF-8 にする。英語の Windows（cp1252）
    # では日本語を print しただけで落ちる。PYTHONIOENCODING は使わない: 子の出力にも効いて、文字コードの書き忘れを隠す
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    name = sys.argv[1]
    try:
        TESTS[name]()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
