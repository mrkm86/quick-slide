#!/usr/bin/env python3
"""PPTX の取り込み（pptx_import.py → html 型の台本 → deck）のテスト。`python3 test_import.py <名前>` で1本ずつ走る。

- 取り込む PPTX は、その場で python-pptx で作る（図形・文字・画像・表・グラフ・グループ・アニメーション）
- deck まで通す試験は Chrome を使う（scripts/browser.py が探す）。ネットワークは使わない
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
TMP = Path(tempfile.mkdtemp(prefix="html-slide-import-"))
sys.path.insert(0, str(SKILL / "scripts"))

import deck as D  # noqa: E402
import deck_types as T  # noqa: E402
import pptx_import as PI  # noqa: E402

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# --- 試験用の PPTX ---------------------------------------------------------------------------

def _photo(path: Path):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (600, 400), (40, 120, 200))
    d = ImageDraw.Draw(im)
    d.ellipse([200, 100, 400, 300], fill=(220, 40, 60))
    im.save(path, quality=90)
    return path


def _click(cid, inner, auto=False):
    return (f'<p:par><p:cTn id="{cid}" fill="hold"><p:stCondLst><p:cond delay="{0 if auto else "indefinite"}"/></p:stCondLst>'
            f'<p:childTnLst><p:par><p:cTn id="{cid + 1}" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst>'
            f'<p:childTnLst>{inner}</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>')


def _effect(cid, spid, node, delay=0, preset=10, sub=0, cls="entr", para=None):
    tgt = f'<p:spTgt spid="{spid}"/>' if para is None else f'<p:spTgt spid="{spid}"><p:txEl><p:pRg st="{para}" end="{para}"/></p:txEl></p:spTgt>'
    return (f'<p:par><p:cTn id="{cid}" presetID="{preset}" presetClass="{cls}" presetSubtype="{sub}" fill="hold" nodeType="{node}">'
            f'<p:stCondLst><p:cond delay="{delay}"/></p:stCondLst><p:childTnLst><p:set><p:cBhvr>'
            f'<p:cTn id="{cid + 100}" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn>'
            f'<p:tgtEl>{tgt}</p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr>'
            f'<p:to><p:strVal val="visible"/></p:to></p:set></p:childTnLst></p:cTn></p:par>')


def _timing(seq: str) -> etree._Element:
    return etree.fromstring(
        f'<p:timing xmlns:p="{P_NS}"><p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">'
        f'<p:childTnLst><p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq">'
        f'<p:childTnLst>{seq}</p:childTnLst></p:cTn></p:seq></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>')


def build(path: Path, wide=True) -> Path:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu, Inches, Pt
    prs = Presentation()
    if wide:
        prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
    photo = _photo(TMP / "photo.jpg")
    # 1 表紙（ノートつき）
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "会議の時間を半分にする"
    s.placeholders[1].text = "業務改善の提案"
    s.notes_slide.notes_text_frame.text = "最初に結論を話します。"
    # 2 箇条書き（段落ごとに 3 クリック）
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "いまの課題"
    body = s.placeholders[1]
    tf = body.text_frame
    tf.text = "決めずに終わる"
    for t in ("読み上げが長い", "人が多い"):
        tf.add_paragraph().text = t
    seq = "".join(_click(10 * (i + 1), _effect(10 * (i + 1) + 2, body.shape_id, "clickEffect", para=i)) for i in range(3))
    s._element.append(_timing(seq))
    # 3 図形と色・回した文字・グループの動き（自動・ワイプ・ズーム・終了）
    s = prs.slides.add_slide(prs.slide_layouts[6])
    ch = s.shapes.add_shape(MSO_SHAPE.CHEVRON, Inches(0.5), Inches(1), Inches(3), Inches(1))
    ch.fill.solid()
    ch.fill.fore_color.rgb = RGBColor(0x12, 0x34, 0x56)
    ch.text = "段1"
    r = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(4), Inches(1), Inches(3), Inches(1))
    r.fill.solid()
    r.fill.fore_color.rgb = RGBColor(0xAB, 0xCD, 0xEF)
    tb = s.shapes.add_textbox(Inches(8), Inches(1), Inches(3), Inches(0.6))
    tb.text = "回した注記"
    tb.rotation = 30
    tb.text_frame.paragraphs[0].runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    g = s.shapes.add_group_shape()
    g.shapes.add_shape(MSO_SHAPE.OVAL, Inches(1), Inches(3), Inches(2), Inches(2))
    g.shapes.add_textbox(Inches(1), Inches(5.2), Inches(2), Inches(0.5)).text = "丸の説明"
    seq = (_click(10, _effect(12, ch.shape_id, "afterEffect"), auto=True)
           + _click(20, _effect(22, r.shape_id, "clickEffect", preset=22, sub=8) + _effect(24, tb.shape_id, "withEffect", delay=500, preset=53))
           + _click(30, _effect(32, g.shape_id, "clickEffect", preset=21) + _effect(34, ch.shape_id, "withEffect", cls="exit")))
    s._element.append(_timing(seq))
    s._element.append(etree.fromstring(f'<p:transition xmlns:p="{P_NS}"><p:fade/></p:transition>'))
    # 4 写真（切り抜き・丸く切り抜き）
    s = prs.slides.add_slide(prs.slide_layouts[6])
    pic = s.shapes.add_picture(str(photo), Inches(0), Inches(0), width=Inches(6), height=Inches(7.5))
    pic.crop_left = 0.2
    c = s.shapes.add_picture(str(photo), Inches(8), Inches(4), width=Inches(2), height=Inches(2))
    c._element.spPr.find(f"{{{A_NS}}}prstGeom").set("prst", "ellipse")
    # 5 表とグラフ
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "数字で見る"
    t = s.shapes.add_table(2, 2, Inches(0.5), Inches(1.5), Inches(5), Inches(1.5)).table
    for i, row in enumerate((("部署", "時間"), ("営業", "12時間"))):
        for j, v in enumerate(row):
            t.cell(i, j).text = v
    cd = CategoryChartData()
    cd.categories = ["4月", "5月"]
    cd.add_series("時間", (12, 7))
    s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(6), Inches(1.5), Inches(6), Inches(4), cd)
    # 6 非表示のスライド・スライドの外の図形
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = "隠したページ"
    s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(20), Inches(1), Inches(1), Inches(1))
    s._element.set("show", "0")
    prs.save(path)
    return path


_cache: dict = {}


def imported(wide=True):
    key = "wide" if wide else "43"
    if key not in _cache:
        src = build(TMP / f"in-{key}.pptx", wide)
        out = TMP / f"out-{key}"
        rep = PI.import_pptx(src, out)
        _cache[key] = (out, rep, json.loads((out / "script.json").read_text(encoding="utf-8")))
    return _cache[key]


# --- 試験 ----------------------------------------------------------------------------------

def t_structure():
    """台本: 6 枚・html 型・見出し・ノート・テーマ・画像は media/・報告に表とグラフ。"""
    out, rep, sc = imported()
    sl = sc["slides"]
    check(len(sl) == 6 and all(s["type"] == "html" for s in sl), f"枚数か型: {[s['type'] for s in sl]}")
    check(sl[0]["title"] == "会議の時間を半分にする", f"見出し: {sl[0]['title']}")
    check(sl[0]["notes"] == "最初に結論を話します。", f"ノート: {sl[0]['notes']!r}")
    check(sl[2]["title"] == "段1" or sl[2]["title"], "題の無いページも見出しを持つ")
    check(sc.get("theme") == "theme.yaml" and (out / "theme.yaml").is_file(), "テーマ")
    th = (out / "theme.yaml").read_text(encoding="utf-8")
    check('primary: "#4f81bd"' in th, f"テーマの accent1 が primary: {th}")
    imgs = re.findall(r'src="(media/[^"]+)"', sl[3]["html"])
    check(len(imgs) == 2 and all((out / p).is_file() for p in imgs), f"画像: {imgs}")
    whats = {it["what"] for it in rep["items"]}
    check("グラフ" in whats, f"報告: {whats}")
    check(sl[5].get("_hidden") is True, "非表示のスライドに印")
    check("非表示のスライド" in whats and "スライドの外の図形" in whats, f"報告: {whats}")
    check(sl[2]["transition"] == "fade" and sl[0]["transition"] == "none", "切り替え")


def t_colors_and_text():
    """色と文字: 指定の色（#123456・#666666）・字の大きさ（pt × 2 px）・字体は単引用符（属性を切らない）・回した文字。"""
    _, _, sc = imported()
    h = sc["slides"][2]["html"]
    check("#123456" in h and "#abcdef" in h, "図形の塗りの色")
    check("color:#666666" in h, "文字の色")
    check('font-family:"' not in h, "字体の名前を二重引用符で囲まない（style 属性が切れる）")
    check("transform:rotate(30deg)" in h, "回した文字の箱")
    t = sc["slides"][0]["html"]
    check(re.search(r"font-size:88px", t), "表紙の題は 44pt = 88px")


def t_geometry_4x3():
    """4:3 は中央に置く（左右に 240px ずつ）。背景は section の全面。"""
    _, rep, sc = imported(wide=False)
    check(rep["size"]["wide"] is False, "16:9 でないと報告")
    lefts = [float(x) for x in re.findall(r"left:(-?[\d.]+)px", sc["slides"][2]["html"])]
    check(lefts and min(v for v in lefts if v > 0) >= 240 - 1, f"左の端が 240px より内: {sorted(lefts)[:3]}")
    check(sc["slides"][0]["background"] in ("#ffffff", "#fff"), f"背景: {sc['slides'][0]['background']}")


def t_animations():
    """段: 段落ごとの 3 クリック・自動で出る段 0・ワイプの向き・同時の遅れ・ホイール・終了は写さず報告。"""
    _, rep, sc = imported()
    h2 = sc["slides"][1]["html"]
    steps = re.findall(r'data-step="(\d+)" data-fx="(\w[\w-]*)"', h2)
    check([s for s, _ in steps] == ["1", "2", "3"], f"段落ごとの段: {steps}")
    h3 = sc["slides"][2]["html"]
    got = re.findall(r'data-step="(\d+)" data-fx="([\w-]+)"(?: data-delay="(\d+)")?', h3)
    check(("0", "fade", "") in got, f"自動の段 0: {got}")
    check(("1", "wipe-right", "") in got and ("1", "pop", "500") in got, f"ワイプと同時の遅れ: {got}")
    check(("2", "wheel", "") in got, f"グループの段: {got}")
    hows = [it["how"] for it in rep["items"] if it["slide"] == 3]
    check(any("終了の効果は写さない" in x for x in hows), f"終了の報告: {hows}")


def t_engine_html_type():
    """エンジンの html 型: 画像を assets/ に写す・段を数える・handout は段を外す・書けないもの（script・on*・url()）は rc=2。"""
    out, _, sc = imported()
    html, info = D.build_html(out / "script.json", TMP / "built")
    text = html.read_text(encoding="utf-8")
    check(text.count("data-imported") == 6, "取り込んだページの印")
    check('src="assets/h-' in text and not re.search(r'src="media/', text), "画像は assets/ へ")
    # 同じ画像を何ページで使っても、assets/ には 1 つ（発表の画面がページの数だけ読まない）
    img = next((out / "media").iterdir())
    two = TMP / "two.json"
    two.write_text(json.dumps({"slides": [{"type": "html", "html": f'<img src="{img}">'},
                                          {"type": "html", "html": f'<img src="{img}">'}]}), encoding="utf-8")
    html3, _ = D.build_html(two, TMP / "built-two")
    names = re.findall(r'src="assets/(h-[^"]+)"', html3.read_text(encoding="utf-8"))
    check(len(names) == 2 and len(set(names)) == 1, f"同じ画像の名前: {names}")
    check(len(list((TMP / "built-two" / "assets").iterdir())) == 1, "assets/ に 1 つ")
    check([s["clicks"] for s in info["slides"]][:3] == [0, 3, 2], f"段の数: {[s['clicks'] for s in info['slides']]}")
    html2, info2 = D.build_html(out / "script.json", TMP / "built-h", mode="handout")
    check("data-step" not in html2.read_text(encoding="utf-8").split("<body>")[-1], "handout は段を外す")
    for bad, what in (('<script>alert(1)</script>', "script"), ('<div onclick="x()"></div>', "onclick"),
                      ('<div style="background:url(x.png)"></div>', "url"), ('<a href="http://x">x</a>', "a"),
                      ('<img src="http://example.com/x.png">', "URL の画像")):
        sc2 = {"slides": [{"type": "html", "html": bad}]}
        p = TMP / f"bad-{what}.json"
        p.write_text(json.dumps(sc2), encoding="utf-8")
        try:
            D.build_html(p, TMP / "bad")
        except T.DeckError:
            continue
        raise AssertionError(f"書けないものを通した: {what}")
    try:
        p = TMP / "bad-bg.json"
        p.write_text(json.dumps({"slides": [{"type": "html", "html": "", "background": "url(x.png)"}]}), encoding="utf-8")
        D.build_html(p, TMP / "bad")
        raise AssertionError("背景に url() を通した")
    except T.DeckError:
        pass


def t_deck_end_to_end():
    """deck（Chrome）: 取り込んだ台本が rc=0 で PPTX・ページの画像になる・クリックの数は元の段・回した文字の rot。"""
    out, _, _ = imported()
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "html_slide.py"), "deck", "--script", str(out / "script.json"),
                        "--out", str(TMP / "deck"), "--no-fill", "--format", "pptx,png"], capture_output=True, text=True,
                       encoding="utf-8", timeout=600)
    check(r.returncode == 0, f"deck が rc={r.returncode}: {r.stderr[-800:]}")
    check("verify=OK" in r.stdout, f"照合: {r.stdout[-300:]}")
    rep = json.loads((TMP / "deck" / "build.json").read_text(encoding="utf-8"))
    check([s["steps"] for s in rep["slides"]][:3] == [0, 3, 2], f"クリック: {[s['steps'] for s in rep['slides']]}")
    check(all(s.get("imported") for s in rep["slides"]), "build.json に取り込みの印")
    check(len(list((TMP / "deck" / "slides").glob("slide-*.png"))) == 6, "ページの画像")
    z = zipfile.ZipFile(TMP / "deck" / "deck.pptx")
    xml = z.read("ppt/slides/slide3.xml").decode()
    check(re.search(r'<a:xfrm rot="1800000"', xml), "回した文字の箱は rot=30°")
    check("最初に結論を話します。" in z.read("ppt/notesSlides/notesSlide1.xml").decode(), "ノート")


def t_bad_files():
    """PPTX でないもの・zip の部品の名前の誤り・巨大な部品は rc=2（ImportError_）。"""
    p = TMP / "not.pptx"
    p.write_bytes(b"hello")
    for target in (p,):
        try:
            PI.import_pptx(target, TMP / "x")
            raise AssertionError("PPTX でないものを通した")
        except PI.ImportError_:
            pass
    z = TMP / "evil.pptx"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("[Content_Types].xml", "<x/>")
        zf.writestr("ppt/presentation.xml", "<x/>")
        zf.writestr("../../etc/passwd", "x")
    try:
        PI.import_pptx(z, TMP / "y")
        raise AssertionError("外へ出る部品の名前を通した")
    except PI.ImportError_ as e:
        check("名前" in str(e), str(e))
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "html_slide.py"), "import", "--pptx", str(p), "--out", str(TMP / "z")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    check(r.returncode == 2 and "NG:" in r.stderr, f"入口の rc: {r.returncode} {r.stderr}")


def t_svg_only_image():
    """PNG の控えの無い SVG の画像は、Chrome で描いて PNG にし、回転を掛ける（ネットへは読みに行かない）。"""
    from PIL import Image
    rep = PI.Report()
    m = PI.Media(TMP / "svg", rep)
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 5"><rect width="10" height="5" fill="#ff0000"/>'
           b'<image href="http://example.com/x.png" width="1" height="1"/></svg>')
    rel, w, h = m.svg(1, svg, crop=(0, 0, 0, 0), size=(100, 50), mask=None, rot=90, fh=False, fv=False, alpha=1.0,
                      gray=False, duo=None, clr_change=None, tile=False)
    again, _, _ = m.svg(2, svg, crop=(0, 0, 0, 0), size=(100, 50), mask=None, rot=90, fh=False, fv=False, alpha=1.0,
                        gray=False, duo=None, clr_change=None, tile=False)
    check(again == rel, f"同じ SVG は 1 つの画像: {rel} / {again}")
    check(abs(w - 50) < 0.5 and abs(h - 100) < 0.5, f"回した外接の大きさ: {w}×{h}")
    m.finish()
    im = Image.open(TMP / "svg" / rel).convert("RGBA")
    check(im.width < im.height, f"回した向き: {im.size}")
    px = im.getpixel((im.width // 2, im.height // 2))
    check(px[0] > 200 and px[1] < 60 and px[3] > 200, f"赤く描けた: {px}")
    check(not rep.items, f"報告: {rep.items}")


def t_metafile_image():
    """合成 EMF 入り PPTX。画像枠・元の余白・srcRect・通常の PNG を確認する。"""
    import io
    import shutil
    import struct
    from unittest import mock
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches

    with mock.patch("shutil.which", return_value=None):
        check(PI.convert_metafile(b"", ".emf") is None, "変換器が無いとき")
        report = PI.Report()
        media = PI.Media(TMP / "metafile-missing", report)
        first = media.metafile(1, b"missing", ".emf", size=(100, 100))
        again = media.metafile(2, b"missing", ".emf", size=(100, 100))
        check(first == again and len(media.metafile_jobs) == 1, "同じ配置は1回だけ処理")
        media.finish()
        check((TMP / "metafile-missing" / first[0]).is_file() and report.items, "変換できない画像は枠と報告")
    if not (shutil.which("soffice") or shutil.which("libreoffice")):
        print("skip: EMF の実変換には LibreOffice が要る")
        return
    # 200×100 のキャンバス。赤い矩形の周囲に 10% の意図的な余白。
    records = (struct.pack("<6I", 39, 24, 1, 0, 0xff, 0)  # EMR_CREATEBRUSHINDIRECT
               + struct.pack("<3I", 37, 12, 1)  # EMR_SELECTOBJECT
               + struct.pack("<II4i", 43, 24, 20, 10, 180, 90)
               + struct.pack("<5I", 14, 20, 0, 0, 20))
    header = struct.pack("<II4i4iIIIIHHIII4i", 1, 88, 0, 0, 200, 100,
                         0, 0, 5292, 2646, 0x464d4520, 0x10000, 88 + len(records), 5,
                         2, 0, 0, 0, 0, 1920, 1080, 508, 286)
    blob = header + records
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("soffice", 60)):
        check(PI.convert_metafile(blob, ".emf") is None, "変換が時間切れのとき")
    png = PI.convert_metafile(blob, ".emf", size=(400, 200))
    check(png is not None, "EMF を変換できない")
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    check(im.size == (800, 400), f"画像枠に合わせた画素数: {im.size}")

    def red_bounds(im):
        rgb = im.convert("RGB")
        return Image.eval(rgb.getchannel("G"), lambda v: 255 if v < 60 else 0).getbbox()

    bounds = red_bounds(im)
    check(bounds and 70 <= bounds[0] <= 90 and 710 <= bounds[2] <= 730, f"元の余白を保つ: {bounds}")
    check(im.getpixel((5, 5))[:3] == (255, 255, 255), "元の余白は白")
    photo = TMP / "metafile-placeholder.png"
    Image.new("RGB", (200, 100), (0, 120, 220)).save(photo)
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for i in range(3):
        pic = slide.shapes.add_picture(str(photo), Inches(i * 3), Inches(1), width=Inches(2), height=Inches(1))
        if i == 1:
            pic.crop_left = pic.crop_right = 0.1
    original = io.BytesIO()
    prs.save(original)
    pptx = TMP / "metafile.pptx"
    with zipfile.ZipFile(io.BytesIO(original.getvalue())) as src, zipfile.ZipFile(pptx, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "ppt/slides/_rels/slide1.xml.rels":
                data = data.replace(b"</Relationships>", b'<Relationship Id="rIdEmf" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/test.emf"/></Relationships>')
            if name == "ppt/slides/slide1.xml":
                root = etree.fromstring(data)
                for blip in root.findall(".//a:blip", PI.NS)[:2]:
                    blip.set(PI.R + "embed", "rIdEmf")
                data = etree.tostring(root)
            if name == "[Content_Types].xml":
                data = data.replace(b"</Types>", b'<Default Extension="emf" ContentType="image/x-emf"/></Types>')
            dst.writestr(name, data)
        dst.writestr("ppt/media/test.emf", blob)
    out = TMP / "metafile-import"
    with mock.patch.object(PI, "convert_metafiles", wraps=PI.convert_metafiles) as convert:
        PI.import_pptx(pptx, out)
    check(convert.call_count == 1, "配置ごとに変換器を起動しない")
    check(len(convert.call_args.args[0]) == 1, "同じ素材・寸法のクロップ違いは変換を共有")
    script = json.loads((out / "script.json").read_text(encoding="utf-8"))
    imgs = re.findall(r'src="(media/[^\"]+)"', script["slides"][0]["html"])
    check(len(imgs) == 3, f"取り込んだ画像: {imgs}")
    full, cropped, normal = [Image.open(out / path).convert("RGBA") for path in imgs]
    b = red_bounds(full)
    check(b and 0.08 < b[0] / full.width < 0.12, f"取り込みの余白: {b}")
    b = red_bounds(cropped)
    check(b and b[0] < 3 and b[2] >= cropped.width - 3, f"PPTX のクロップ: {b}")
    check(normal.getpixel((0, 0))[:3] == (0, 120, 220), "通常の PNG")


def t_xml_entities():
    """XML の実体参照を解かない（外のファイルを読まない）。"""
    import ooxml as X
    blob = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/hosts">]><x>&e;</x>'
    el = X.parse(blob)
    check("localhost" not in (etree.tostring(el).decode()), "実体参照を解いた")


if __name__ == "__main__":
    # 試験の表示（失敗の報告・skip の理由・同じプロセスで呼んだエンジンの表示）だけは UTF-8 にする。英語の Windows（cp1252）
    # では日本語を print しただけで落ちる。PYTHONIOENCODING は使わない: 子の出力にも効いて、文字コードの書き忘れを隠す
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    name = sys.argv[1]
    try:
        globals()[f"t_{name}"]()
    finally:
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
    print("ok")
