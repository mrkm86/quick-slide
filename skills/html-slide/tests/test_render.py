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
    saved = json.loads(Path(rep["report_path"]).read_text())
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
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "render.py"), "check", str(BAD)], capture_output=True, text=True)
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


def t_verify_mismatch():
    """verify: ノートの［クリック］とクリックの数のずれを rc=2 で拾う。合っていれば rc=0。"""
    prims = [{"type": "rect", "unit": "u1", "rect": {"x": 10, "y": 10, "w": 50, "h": 50}, "fill": {"hex": "00ff00", "a": 1}}]
    good = {"slides": [{"id": "s1", "notes": "［クリック］出す", "units": {"u1": {"step": 1, "fx": "pop"}}, "prims": prims}]}
    bad = {"slides": [{"id": "s1", "notes": "［クリック］出す［クリック］もう1つ", "units": {"u1": {"step": 1, "fx": "pop"}}, "prims": prims}]}
    rg = pptx_writer.write(good, TMP / "v-good.pptx")
    rb = pptx_writer.write(bad, TMP / "v-bad.pptx")
    check(not rg["mismatch"] and rb["mismatch"] == [1], f"report のずれ: {rg['mismatch']} / {rb['mismatch']}")
    cli = [sys.executable, str(SKILL / "scripts" / "verify.py")]
    r = subprocess.run(cli + [str(TMP / "v-good.pptx")], capture_output=True, text=True)
    check(r.returncode == 0 and "ALL OK" in r.stdout, f"合っているのに rc={r.returncode}: {r.stdout}")
    r = subprocess.run(cli + [str(TMP / "v-bad.pptx")], capture_output=True, text=True)
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
    check("`data-step` はブロック要素に付ける" in (SKILL / "references" / "contract.md").read_text(), "contract.md に書いていない")


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
    check("`z-index` は section の直下か" in (SKILL / "references" / "contract.md").read_text(), "contract.md に書いていない")


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


TESTS = {k[2:]: v for k, v in dict(globals()).items() if k.startswith("t_")}

if __name__ == "__main__":
    name = sys.argv[1]
    try:
        TESTS[name]()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
