#!/usr/bin/env python3
"""資料の型（deck.py・deck_types.py）の純ロジックのテスト。

    python3 test_deck.py <ケース名>     # 1ケース
    python3 test_deck.py all           # 全部

台本の検証（DeckError になるもの）・見本の台本が通ること・handout と steps: false・HTML の決まりの属性を見る。
ブラウザは使わない（見た目は scripts/snapshot.mjs で人が見る）。字体は PowerPoint 同梱の游ゴシックを読む。
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
from html.parser import HTMLParser

SKILL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
import deck  # noqa: E402
import deck_types as T  # noqa: E402

SAMPLE = SKILL / "examples" / "sample-deck.json"
IMAGE_SAMPLE = SKILL / "examples" / "image-deck.json"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="html-slide-deck-test-"))


def sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def build(sc: dict, name: str, **kw):
    """台本を一時ディレクトリに書き出して組む（examples/assets をそばに写し、画像の相対パスを生かす）。"""
    if not (TMP / "assets").is_dir():
        shutil.copytree(SAMPLE.parent / "assets", TMP / "assets")
    p = TMP / f"{name}.json"
    p.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    return deck.build_html(p, TMP / name, **kw)


def expect_error(sc: dict, name: str, *words):
    try:
        build(sc, name)
    except T.DeckError as e:
        msg = str(e)
        for w in words:
            assert w in msg, f"メッセージに「{w}」が無い: {msg}"
        return msg
    raise AssertionError("DeckError にならなかった")


def one(slide: dict, talk=None) -> dict:
    s = dict(slide)
    if talk is not None:
        s["talk"] = talk
    return {"title": "t", "slides": [s]}


class Sections(HTMLParser):
    """section ごとに data-step の値・入れ子・属性・ノートを集める。"""

    VOID = {"img", "br", "hr", "meta", "input", "link", "source", "polygon", "rect", "circle", "path", "line"}

    def __init__(self):
        super().__init__()
        self.secs, self.stack, self.in_notes = [], [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section" and "data-slide" in a:
            self.secs.append({"attrs": a, "steps": [], "fx": [], "nested": 0, "notes": "", "keep": []})
            self.stack = []
            return
        if not self.secs:
            return
        cur = self.secs[-1]
        if tag == "aside" and a.get("class") == "notes":
            self.in_notes = True
        stepped = "data-step" in a
        if stepped:
            if any(self.stack):
                cur["nested"] += 1
            cur["steps"].append(int(a["data-step"]))
            cur["fx"].append(a.get("data-fx"))
        if "data-keep" in a:
            cur["keep"].append(a["data-keep"])
        if tag not in self.VOID and not tag.endswith("/"):
            self.stack.append(stepped)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.secs and tag not in self.VOID and self.stack:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "aside":
            self.in_notes = False
        if self.secs and self.stack and tag != "section":
            self.stack.pop()

    def handle_data(self, data):
        if self.in_notes and self.secs:
            self.secs[-1]["notes"] += data


def parse(html_path) -> list[dict]:
    p = Sections()
    p.feed(pathlib.Path(html_path).read_text(encoding="utf-8"))
    return p.secs


# --- ケース ------------------------------------------------------------------------

def case_sample_ok():
    """見本が通る・全型を使っている・talk の数と段の数が合っている。"""
    path, info = deck.build_html(SAMPLE, TMP / "sample")
    sc = sample()
    img = json.loads(IMAGE_SAMPLE.read_text(encoding="utf-8"))
    used = {s["type"] for s in sc["slides"] + img["slides"]}
    missing = set(T.TYPES) - used
    assert not missing, f"見本（sample-deck と image-deck）で使っていない型: {missing}"
    assert len(T.TYPES) >= 12, f"型が {len(T.TYPES)} 種しかない"
    assert len(info["slides"]) == len(sc["slides"])
    for s, i in zip(sc["slides"], info["slides"]):
        assert len(s["talk"]) == i["clicks"] + 1, (s["type"], i)
    assert (path.parent / "assets").is_dir()
    assert info["slides"][0]["transition"] == "fade" and info["slides"][1]["transition"] == "fade"


def case_default_clicks():
    """型ごとの段の数の既定（deck-format.md の表と同じ）。"""
    _, info = deck.build_html(SAMPLE, TMP / "clicks")
    sc = sample()
    want = {"cover": 0, "agenda": 0, "section": 0, "statement": 0, "photo": 0, "quote": 0, "closing": 0,
            "bar-chart": 1, "table": 1, "timeline": 1, "compare": 2,
            "summary": 1, "kpi": 1, "line-chart": 1, "team": 1, "matrix": 1, "figure": 0}
    for s, i in zip(sc["slides"], info["slides"]):
        t = s["type"]
        if t in want:
            assert i["clicks"] == want[t], (t, i["clicks"])
        elif t in ("cards", "flow", "bullets", "gallery"):
            assert i["clicks"] == len(s["items"]), (t, i["clicks"])
        elif t == "big-number":
            assert i["clicks"] == (2 if s.get("side") else 1)
        elif t == "equation":
            assert i["clicks"] == len(s["items"]) + 1, (t, i["clicks"])
        elif t == "hub":
            assert i["clicks"] == 1, (t, i["clicks"])
        elif t == "pairs":
            assert i["clicks"] == len(s["items"]), (t, i["clicks"])


def case_contract_attrs():
    """HTML の決まり: section・transition・ノートの［クリック］の数＝段の数・fx の語彙・入れ子なし・keep。"""
    path, info = deck.build_html(SAMPLE, TMP / "attrs")
    secs = parse(path)
    assert len(secs) == len(info["slides"])
    for n, (sec, i) in enumerate(zip(secs, info["slides"]), 1):
        assert sec["attrs"].get("data-transition") in ("fade", "none"), n
        clicks = len({v for v in sec["steps"] if v >= 1})
        assert clicks == i["clicks"], (n, clicks, i)
        assert sec["notes"].count("［クリック］") == clicks, (n, sec["notes"])
        assert all(f in T.FX for f in sec["fx"]), (n, sec["fx"])
        assert sec["nested"] == 0, f"{n}枚目: data-step が入れ子"
        assert len(sec["keep"]) == len(set(sec["keep"])), f"{n}枚目: data-keep の名前が重なっている"
    assert any("band" in s["keep"] and "pageno" in s["keep"] for s in secs)


def case_no_forbidden_css():
    """許可リストの外の CSS を出していない（transform・filter・clip-path・mask・背景画像・::before の content）。"""
    path, _ = deck.build_html(SAMPLE, TMP / "css")
    html = path.read_text(encoding="utf-8")
    css = re.sub(r"@font-face\s*\{[^}]*\}|<!--.*?-->", "", html, flags=re.S)
    for bad in ("transform", "filter:", "clip-path", "mask", "background-image", "::before", "::after",
                "writing-mode", "radial-gradient"):
        assert bad not in css, f"使ってはいけない CSS: {bad}"
    assert not re.search(r"background[^;]*url\(", css), "背景画像"
    assert "<script" not in html, "スクリプトを入れない"
    assert not re.search(r"(src|href)=\"https?:", html), "外部 URL を読まない"


def case_handout():
    """handout: 動きなし（data-step・data-delay を付けない）・transition は none・ノートに［クリック］なし・talk の照合は talk と同じ。
    PPTX に効果も切り替えも入らない。"""
    sc = sample()
    sc["mode"] = "handout"
    path, info = build(sc, "handout")
    for sec in parse(path):
        assert not sec["steps"], f"動きの属性が残った: {sec['steps']}"
        assert "［クリック］" not in sec["notes"]
        assert sec["attrs"]["data-transition"] == "none"
    assert all(i["clicks"] == 0 for i in info["slides"])
    html = path.read_text(encoding="utf-8")
    assert "data-step=" not in html and "data-delay=" not in html and "data-fx=" not in html, "動きの属性が残った"
    bad = copy.deepcopy(sc)
    next(s for s in bad["slides"] if s["type"] == "cards")["talk"].append("多い")
    expect_error(bad, "handout-bad-talk", "talk")
    import render as R
    rep = R.render(path, path.parent / "deck.pptx")
    assert rep["totals"]["effects"] == 0 and all(x["transition"] == "none" for x in rep["slides"]), rep["totals"]
    import zipfile
    z = zipfile.ZipFile(path.parent / "deck.pptx")
    xml = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("ppt/slides/slide"))
    assert "<p:timing" not in xml and "<p:transition" not in xml, "PPTX に効果か切り替えが入った"


def case_steps_false():
    """steps: false の1枚は全部 step 0。talk は1つにする（2つ以上は rc=2）。"""
    sc = sample()
    cards = next(s for s in sc["slides"] if s["type"] == "cards")
    cards["steps"] = False
    expect_error(copy.deepcopy(sc), "steps-false-bad", "talk が 4 個", "クリック 0 回")
    cards["talk"] = ["全部まとめて話す"]
    path, info = build(sc, "steps-false")
    no = sc["slides"].index(cards)
    assert info["slides"][no]["clicks"] == 0
    assert set(parse(path)[no]["steps"]) == {0}


def case_talk_mismatch():
    """talk の数 − 1 が段の数と違えば rc=2。省けば段の数ぶんの空。"""
    sc = sample()
    s = next(s for s in sc["slides"] if s["type"] == "compare")
    s["talk"] = s["talk"][:2]
    expect_error(sc, "talk", "compare", "talk が 2 個", "3 個")
    s.pop("talk")
    path, info = build(sc, "talk-omit")
    no = sc["slides"].index(s)
    assert parse(path)[no]["notes"] == "［クリック］［クリック］"


def case_unknown_type():
    expect_error(one({"type": "pie-chart", "headline": "x"}), "unknown", "知らない型", "pie-chart")


def case_unknown_key():
    expect_error(one({"type": "statement", "headline": "主張", "bodyy": "x"}), "unknown-key", "知らないキー", "bodyy")


def case_missing_required():
    expect_error(one({"type": "cover"}), "req1", "1枚目（cover）", "title が無い")
    expect_error(one({"type": "cards", "items": ["a", "b"]}, ["", "", ""]), "req2", "headline が無い")
    expect_error(one({"type": "cards", "headline": "h", "items": ["a"]}), "req3", "items は 2〜4 個")
    expect_error(one({"type": "bar-chart", "headline": "h", "bars": [{"label": "a", "value": "x"}, {"label": "b", "value": 1}]}),
                 "req4", "0 以上の数")


def case_photo_url():
    expect_error(one({"type": "photo", "image": "https://example.com/a.jpg", "headline": "h"}), "url", "URL は使えない")
    expect_error(one({"type": "cover", "title": "t", "image": "data:image/png;base64,AAAA"}), "url2", "URL は使えない")


def case_photo_image_only():
    """photo の全面の hide_text: 画像だけを敷く扉絵（幕も文字も出さない。見出しはページの名前として持つだけ）。出典だけは
    右下に小さく。左右・帯には使えない・見出しは要る（ADR 0026）。"""
    html, _ = build(one({"type": "photo", "image": "assets/meeting.png", "headline": "扉の題", "hide_text": True}), "photo-only")
    page = pathlib.Path(html).read_text(encoding="utf-8").split("<section", 2)[1]
    assert "<h1" not in page and 'class="scrim"' not in page, "画像だけのページに文字や幕を出した"
    assert 'class="photo"' in page and "width:1920px; height:1080px" in page, "全面の画像が無い"
    assert "扉の題" not in page, "見出しを画面に出した"
    html, _ = build(one({"type": "photo", "image": "assets/meeting.png", "headline": "扉の題", "hide_text": True,
                         "caption": "出典: 記事の題 https://example.com/"}), "photo-only-cap")
    page = pathlib.Path(html).read_text(encoding="utf-8").split("<section", 2)[1]
    assert "<h1" not in page and "出典: 記事の題" in page and 'data-name="写真の出典"' in page
    expect_error(one({"type": "photo", "image": "assets/meeting.png", "hide_text": True}), "photo-only-nohead", "headline が無い")
    expect_error(one({"type": "photo", "image": "assets/meeting.png", "headline": "h", "layout": "left", "hide_text": True}),
                 "photo-left-hide", "layout: full のときだけ")


def case_missing_image():
    expect_error(one({"type": "photo", "image": "assets/nai.png", "headline": "h"}), "noimg", "画像が無い", "nai.png")


def case_too_long():
    """見出しが2行に入らなければ rc=2（文字数の目安）。"""
    long = "会議の時間を半分にするために" * 6
    expect_error(one({"type": "cards", "headline": long, "items": ["a", "b"]}, ["", "", ""]), "long", "見出し（headline）が長すぎて")


def case_theme():
    """テーマ: 3つとも通る・読めない色は rc=2・無い名前は rc=2。"""
    for name in ("default", "forest", "warm"):
        build(sample(), f"theme-{name}", theme=name)
    # 薄い主役の色（淡い水色など）は止めない。同じ色相のまま明度だけを動かして、文字は 4.5:1・図形は 3:1 にする（DADS・ADR 0020）
    pale = TMP / "pale.yaml"
    pale.write_text('background: "#ffffff"\nink: "#1a2b4c"\nprimary: "#9ec5ff"\n', encoding="utf-8")
    html, _ = build(sample(), "theme-pale", theme=str(pale))
    p = deck.palette(deck.load_theme(pale))
    assert deck.ratio(p["primary-text"], "#ffffff") >= 4.5 and deck.ratio(p["primary-mark"], "#ffffff") >= 3.0, p
    assert deck.ratio(p["on-primary"], p["primary-strong"]) >= 4.5, p
    h0, h1 = deck.KC.to_oklch("#9ec5ff")[2], deck.KC.to_oklch(p["primary-text"])[2]
    assert abs((h0 - h1 + 180) % 360 - 180) < 10, f"色相が動いた: {h0} → {h1}"
    assert all(p[f"accent{r}"] == p[f"primary{r}"] for r in ("", "-text", "-strong", "-soft", "-mark", "-decor")), "差し色を書かないのに primary と違う"
    h = pathlib.Path(html).read_text(encoding="utf-8")
    assert "var(--primary-text)" in h and f"--primary-text: {p['primary-text']}" in h and f"--primary-mark: {p['primary-mark']}" in h, "色の役が CSS 変数に入っていない"
    assert deck.contrast_problems(p) == [], deck.contrast_problems(p)
    # 地と文字が近いテーマは今までどおり止める（強調色だけが救われる）
    bad = TMP / "bad.yaml"
    bad.write_text('background: "#ffffff"\nink: "#bbbbbb"\nprimary: "#1f5fd1"\n', encoding="utf-8")
    try:
        build(sample(), "theme-bad", theme=str(bad))
        raise AssertionError("読めない文字の色が通った")
    except T.DeckError as e:
        assert "コントラスト" in str(e)
    try:
        build(sample(), "theme-none", theme="nai")
        raise AssertionError("無いテーマが通った")
    except T.DeckError as e:
        assert "テーマが無い" in str(e)


def case_fit():
    """文字の大きさの決め方: 入れば元の大きさ・入らなければ縮める・tidy は1語だけの行を避ける。"""
    s, n = T.fit("短い見出し", 64, 1680, what="x", lines=2, bold=True)
    assert (s, n) == (64, 1)
    s, n = T.fit("あ" * 40, 64, 1680, what="x", lines=2, bold=True)
    assert n == 2 and s == 64
    s, n = T.fit("会議の時間を半分にする", 96, 816, what="x", lines=3, bold=True, tidy=True)
    assert n == 2, (s, n)
    # DADS の段を1つずつ下りる（連続値で縮めない）。段の外の大きさは段に丸める
    s, n = T.fit("あ" * 30, 64, 1680, what="x", lines=1, lh=1.4, min_size=36, bold=True)
    assert (s, n) == (48, 1), (s, n)                       # 64 → 57 → 48 と段を下りて、1行に入る
    assert [T.snap(v) for v in (200, 100, 60, 58, 40, 30, 22, 17, 10)] == [200, 96, 57, 57, 36, 28, 22, 16, 16]
    assert T.step_down(64, 2) == 48 and T.step_down(28, 3, 24) == 24 and T.step_up(28, 1, 36) == 32 and T.step_up(28, 5, 36) == 36 and T.step_up(45, 1, 45) == 45
    try:
        T.fit("あ" * 60, 64, 1680, what="x", lines=1, lh=1.4, min_size=45, bold=True)
        raise AssertionError("下限まで下りても入らないのに通った")
    except T.DeckError as e:
        assert "45px まで下りても" in str(e), e
    assert T.count_lines("abcdefghij" * 3, 40, 200) == 99      # 英数字の長い語は折れない
    try:
        T.fit("https://example.com/very/long/path", 30, 200, what="x", lines=2)
        raise AssertionError("折れない長い語が通った")
    except T.DeckError:
        pass


def case_new_types_errors():
    """新しい型の検証: 値の数・範囲・語彙・凡例・頭文字。"""
    lc = {"type": "line-chart", "headline": "h", "labels": ["a", "b", "c"], "series": [{"values": [1, 2]}]}
    expect_error(one(lc, ["", ""]), "lc1", "labels と同じ 3 個")
    lc2 = dict(lc, series=[{"values": [1, 2, 3]}, {"values": [3, 2, 1]}])
    expect_error(one(lc2, ["", ""]), "lc2", "name（凡例）")
    lc3 = dict(lc, series=[{"values": [1, -2, 3]}])
    expect_error(one(lc3, ["", ""]), "lc3", "min は")
    mx = {"type": "matrix", "headline": "h", "x": {"label": "横"}, "y": {"label": "縦"}, "items": [{"label": "p", "x": 120, "y": 5}]}
    expect_error(one(mx, ["", ""]), "mx1", "0〜100")
    expect_error(one(dict(mx, items=[{"label": "p", "x": 1, "y": 1}], quadrants=["a", "b"]), ["", ""]), "mx2", "4つ")
    kp = {"type": "kpi", "headline": "h", "items": [{"label": "a", "value": "1", "trend": "sideways"}, {"label": "b", "value": "2"},
                                                   {"label": "c", "value": "3"}]}
    expect_error(one(kp, ["", ""]), "kp1", "trend は")
    tm = {"type": "team", "headline": "h", "members": [{"name": "a", "initials": "abc"}, {"name": "b"}]}
    expect_error(one(tm, ["", ""]), "tm1", "2文字まで")
    sm = {"type": "summary", "headline": "h", "points": ["一つだけ"]}
    expect_error(one(sm, ["", ""]), "sm1", "points は 2〜3 個")


def case_line_chart_svg():
    """折れ線: 強める線は accent・点の数＝時点の数・値の高いほど上。"""
    sc = sample()
    path, _ = build(sc, "line")
    html = path.read_text(encoding="utf-8")
    m = re.search(r'<svg class="line-svg"[^>]*data-name="線1"[^>]*>(.*?)</svg>', html, re.S)
    assert m, "線1 の svg が無い"
    poly = re.search(r'points="([^"]+)" fill="none" stroke="(#[0-9a-f]+)"', m.group(1))
    pts = [tuple(map(float, xy.split(","))) for xy in poly.group(1).split()]
    lc = next(s for s in sc["slides"] if s["type"] == "line-chart")
    assert len(pts) == len(lc["labels"])
    assert poly.group(2) == deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))["accent-mark"]
    vals = lc["series"][0]["values"]
    hi, lo = vals.index(max(vals)), vals.index(min(vals))
    assert pts[hi][1] < pts[lo][1], "大きい値ほど上に描く"


def _sizes(html: str, cls: str) -> list[int]:
    return [int(x) for x in re.findall(rf'class="{cls}(?: [\w-]+)*"[^>]*?font-size:(\d+)px', html)]


def case_fit_size_applied():
    """Codex r2 #3: fit() が縮めた大きさを、その要素の font-size に書く（KPI のラベル・増減・注記・表紙の日付など）。"""
    kp = {"type": "kpi", "headline": "h", "items": [
        {"label": "A" * 23, "value": "1", "delta": "B" * 22, "note": "注" * 22}, {"label": "b", "value": "2"}, {"label": "c", "value": "3"}]}
    path, _ = build(one(kp, ["", ""]), "fit-kpi")
    html = path.read_text(encoding="utf-8")
    lab, dl, nt = _sizes(html, "kpi-label"), _sizes(html, "kpi-delta"), _sizes(html, "kpi-note")
    assert lab and lab[0] < 28 and dl and dl[0] < 28 and nt and nt[0] <= 24, (lab, dl, nt)   # 注記は 22 が下限（大きく組むときは 24 まで上る）
    iw = T.inner_w(deck_types_cw(3))
    assert T.text_width("A" * 23, lab[0], True) <= iw * T.SAFE, "折れない語が1行に入る大きさになっていない"
    cv = {"type": "cover", "title": "t", "meta": ["あ" * 60]}
    html = build(one(cv), "fit-cover")[0].read_text(encoding="utf-8")
    m = re.search(r'class="cover-meta"[^>]*><div style="font-size:(\d+)px">', html)
    assert m and int(m.group(1)) < 28, "表紙の日付・組織が縮んでいない"


def deck_types_cw(n, gap=T.GUTTER):
    return (T.CW - gap * (n - 1)) / n


def case_callout_inner_width():
    """Codex r2 #4: グラフの右の要点は、実際の内幅（格子の 4 列 528 − 左右 42）で測る。"""
    assert T.CALLOUT_W == T.span(4) and T.CALLOUT_IW == 444
    for typ, extra in (("bar-chart", {"bars": [{"label": "a", "value": 1}, {"label": "b", "value": 2}]}),
                       ("line-chart", {"labels": ["a", "b", "c"], "series": [{"values": [1, 2, 3]}]})):
        sc = one({"type": typ, "headline": "h", "callout": {"value": "12345678", "text": "x"}} | extra, ["", ""])
        html = build(sc, f"callout-{typ}")[0].read_text(encoding="utf-8")
        m = re.search(r'class="callout-value" style="font-size:(\d+)px">12345678<', html)
        assert m, typ
        assert T.text_width("12345678", int(m.group(1)), True) <= T.CALLOUT_IW, (typ, m.group(1))


def case_zero_bars():
    """Codex r2 #5: 値が全部 0 の棒グラフも、max を省いて組める。"""
    sc = one({"type": "bar-chart", "headline": "h", "bars": [{"label": "a", "value": 0}, {"label": "b", "value": 0}]}, ["", ""])
    _, info = build(sc, "zero-bars")
    assert info["slides"][0]["clicks"] == 1


def case_agenda_source():
    """Codex r2 #6: 目次の source を捨てない。"""
    sc = one({"type": "agenda", "items": ["a", "b"], "source": "出典の文字"})
    html = build(sc, "agenda-src")[0].read_text(encoding="utf-8")
    assert 'class="source"' in html and "出典の文字" in html


SKIP_ESC = {"type", "role", "image", "layout", "trend", "value_labels", "accent", "fx", "transition", "initials", "kind", "state"}


def _poison(v, key=None):
    if isinstance(v, dict):
        return {k: _poison(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_poison(x, key) for x in v]
    if isinstance(v, str) and key not in SKIP_ESC:
        return "<z>" + v[:6]
    return v


def minimal(type_: str, role: dict) -> dict:
    """型ごとに、検証を通る最小の中身（文字は `{…}` の空欄）。元は雛形（scaffold）用で、ADR 0004 で雛形をやめたのでテストへ移した。"""
    h = role.get("hint") or role.get("label") or "主張"
    head = {"headline": f"{{主張: {h}}}"}
    if type_ == "cover":
        return {"title": f"{{題: {h}}}", "subtitle": "{副題}", "meta": ["{日付}", "{組織の名前}"]}
    if type_ == "agenda":
        return {"items": ["{項目1}", "{項目2}", "{項目3}"]}
    if type_ == "section":
        return {"title": f"{{章の題: {role.get('label', '')}}}"}
    if type_ == "statement":
        return head | {"body": "{補足の1〜2文}"}
    if type_ == "summary":
        return head | {"points": [{"title": f"{{根拠{i}}}", "body": "{根拠の説明}"} for i in (1, 2, 3)]}
    if type_ == "big-number":
        return head | {"value": "{数}", "unit": "{単位}", "label": "{何の数か（母集団と単位）}"}
    if type_ == "kpi":
        return head | {"items": [{"label": f"{{指標{i}}}", "value": "{数}", "unit": "{単位}", "delta": "{前年比など}"}
                                 for i in (1, 2, 3)]}
    if type_ == "cards":
        return head | {"items": [{"label": f"{{ラベル{i}}}", "title": f"{{要点{i}}}", "body": "{説明}"} for i in (1, 2, 3)]}
    if type_ == "equation":
        return head | {"items": [{"title": f"{{要素{i}}}", "body": "{補足}"} for i in (1, 2, 3)], "result": {"title": "{答え}", "body": "{札}"}}
    if type_ == "hub":
        return head | {"center": {"title": "{中心}", "body": "{補足}"},
                       "items": [{"title": f"{{周り{i}}}", "body": "{補足}", "tag": "{札}"} for i in (1, 2, 3, 4)]}
    if type_ == "pairs":
        return head | {"items": [{"label": f"{{名札{i}}}", "before": "{よくない例}", "after": "{よい例}"} for i in (1, 2, 3)]}
    if type_ == "flow":
        return head | {"items": [{"label": f"{{段{i}}}", "title": f"{{段{i}の要点}}", "body": "{説明}"} for i in (1, 2, 3, 4)]}
    if type_ == "compare":
        side = {"label": "{名札}", "title": "{要約}", "points": ["{違い1}", "{違い2}"]}
        return head | {"left": dict(side), "right": dict(side)}
    if type_ == "bar-chart":
        return head | {"bars": [{"label": f"{{項目{i}}}", "value": i} for i in (1, 2, 3)], "note": "{何の数か（母集団と単位）・値を入れる}"}
    if type_ == "line-chart":
        return head | {"labels": [f"{{時点{i}}}" for i in (1, 2, 3, 4)], "series": [{"values": [1, 2, 3, 4]}],
                       "note": "{何の数か（母集団と単位）・値を入れる}"}
    if type_ == "table":
        return head | {"columns": ["{列1}", "{列2}", "{列3}"], "rows": [["{…}", "{…}", "{…}"], ["{…}", "{…}", "{…}"]]}
    if type_ == "bullets":
        return head | {"items": [{"title": f"{{項目{i}}}", "body": "{説明}"} for i in (1, 2, 3)]}
    if type_ == "timeline":
        return head | {"items": [{"when": f"{{時期{i}}}", "title": f"{{やること{i}}}"} for i in (1, 2, 3)]}
    if type_ == "photo":
        return head | {"image": str(SKILL / "examples" / "assets" / "meeting.png"), "body": "{写真に添える1文・画像を差し替える}"}
    if type_ == "quote":
        return {"quote": f"{{引用: {h}}}", "by": "{誰の言葉か}"}
    if type_ == "team":
        return head | {"members": [{"name": f"{{名前{i}}}", "role": "{役割}", "initials": "？"} for i in (1, 2, 3)]}
    if type_ == "matrix":
        axis = {"label": "{軸の名前}", "low": "{小さい}", "high": "{大きい}"}
        return head | {"x": dict(axis), "y": dict(axis), "items": [{"label": "{点1}", "x": 70, "y": 70}, {"label": "{点2}", "x": 30, "y": 30}]}
    if type_ == "gallery":
        return head | {"items": [{"image": {"prompt": f"{{写真{i}: 何を写すか}}"}, "caption": f"{{写真{i}の説明}}"} for i in (1, 2, 3)]}
    if type_ == "figure":
        return head | {"image": {"prompt": "{図・画面の写し（切らずに見せる）}"}, "caption": "{図の説明}"}
    if type_ == "annotate":
        return head | {"image": {"prompt": "{注釈を付ける写真か図}"},
                       "items": [{"x": x, "y": y, "title": f"{{印{i}の説明}}"} for i, (x, y) in enumerate(((25, 30), (60, 50), (40, 75)), 1)]}
    if type_ == "closing":
        return head | {"items": [{"title": "{次の一手1}", "body": "{期限と担当}"}, {"title": "{次の一手2}", "body": "{期限と担当}"}],
                       "contact": {"name": "{窓口}", "lines": ["{連絡先}"]}}
    if type_ == "notice":
        return head | {"items": [{"kind": "warning", "label": "{種類}", "title": "{題}", "body": "{本文}", "points": ["{箇条1}"]}]}
    if type_ == "deflist":
        return head | {"items": [{"term": f"{{用語{i}}}", "body": "{説明}"} for i in (1, 2, 3)]}
    if type_ == "steps":
        return head | {"current": 2, "items": [{"title": f"{{段{i}}}", "body": "{説明}", "state": s}
                                               for i, s in zip((1, 2, 3), ("done", "current", "todo"))]}
    if type_ == "layers":
        return head | {"rows": [{"label": "{層1}", "items": [{"id": "a", "title": "{箱1}", "body": "{補足}"}, {"id": "b", "title": "{箱2}", "body": "{補足}"}]},
                                {"label": "{層2}", "items": [{"id": "c", "title": "{箱3}", "body": "{補足}"}]}],
                       "links": [{"from": "a", "to": "b", "label": "{名札}"}, {"from": "b", "to": "c"}]}
    if type_ == "free":
        return head | {"body": '<div class="f-row f-grow"><div class="f-box f-grow"><div class="f-label">{箱1}</div><div>{本文1}</div></div>'
                               '<div class="f-box deep f-grow"><div class="f-label">{箱2}</div><div>{本文2}</div></div></div>'}
    raise T.DeckError(f"雛形を作れない型: {type_}")


def case_outline_role_ignored():
    """ADR 0004: ストーリーテンプレートはやめた。前の台本の outline と role は読み飛ばし、警告も出さない。"""
    sc = {"title": "t", "outline": "ringi", "slides": [
        {"type": "cover", "title": "t", "role": "cover"},
        {"type": "statement", "headline": "h", "role": "nazo"},
        {"type": "statement", "headline": "h"}, {"type": "statement", "headline": "h"}]}
    _, info = build(sc, "outline-ignored")
    assert info["warnings"] == [], info["warnings"]
    assert "outline" not in info


def case_escape_all_types():
    """Codex r2 #7: 全型の文字のキーに <z> を入れても、HTML にはタグとして出ない（summary の label も）。"""
    for typ in T.TYPES:
        spec = {"type": typ} | minimal(typ, {"hint": "h", "label": "l"})
        if typ == "summary":
            spec["label"] = "根拠"
        body_html = spec.get("body") if typ == "free" else None
        spec = _poison(spec)
        if typ == "free":                              # body は HTML の決まりで読む欄。タグを書けば止まり（下で確かめる）、文字の中の &lt; はそのまま出る
            expect_error({"title": "t", "slides": [dict(spec)]}, "esc-free-tag", "<z> は書けない")
            spec["body"] = body_html.replace("{箱1}", "&lt;z&gt;箱")
        spec.setdefault("kicker", "<z>k") if typ not in ("cover", "section", "agenda") else None
        sc = {"title": "<z>t", "footer": "<z>f", "slides": [spec]}
        try:
            path, info = build(sc, f"esc-{typ}", placeholder=True)
        except T.DeckError as e:
            if "知らないキー" in str(e) and "kicker" in str(e):
                spec.pop("kicker")
                path, info = build(sc, f"esc-{typ}", placeholder=True)
            elif "talk" in str(e):
                n = int(re.search(r"(\d+) 個（画面", str(e)).group(1))
                spec["talk"] = ["<z>"] * n
                path, info = build(sc, f"esc-{typ}", placeholder=True)
            else:
                raise
        html = path.read_text(encoding="utf-8")
        body = html[html.index("<body>"):]
        assert "<z>" not in body, (typ, body[body.index("<z>") - 80: body.index("<z>") + 20])
        assert "&lt;z&gt;" in body, typ


def _img_dir(name: str) -> pathlib.Path:
    """画像の置き場の試験用フォルダ（台本と images/ を置く）。"""
    d = TMP / name
    (d / "images").mkdir(parents=True, exist_ok=True)
    return d


def _build_in(d: pathlib.Path, sc: dict, **kw):
    p = d / "deck.json"
    p.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    return deck.build_html(p, d / "out", **kw)


def case_image_object():
    """画像の書き方: {path, prompt, focus, alt}。focus は object-position（語は % に直す）・alt は img の alt。"""
    d = _img_dir("img-object")
    shutil.copy(SAMPLE.parent / "assets" / "meeting.png", d / "m.png")
    sc = one({"type": "photo", "headline": "h", "image": {"path": "m.png", "focus": "top", "alt": "会議室<の>絵", "prompt": "上から見た会議室"}})
    html = _build_in(d, sc)[0].read_text(encoding="utf-8")
    assert "object-position:50% 0%;" in html
    assert 'alt="会議室&lt;の&gt;絵"' in html
    sc["slides"][0]["image"]["focus"] = "40% 30%"
    assert "object-position:40% 30%;" in _build_in(d, sc)[0].read_text(encoding="utf-8")
    for bad, words in (({"path": "m.png", "focus": "middle"}, "focus は"), ({"path": "m.png", "size": "x"}, "知らないキー"),
                       ({"alt": "x"}, "path か prompt"), ({"path": "m.png", "focus": "140% 0%"}, "focus は")):
        sc["slides"][0]["image"] = bad
        try:
            _build_in(d, sc)
            raise AssertionError(f"通ってしまった: {bad}")
        except T.DeckError as e:
            assert words in str(e), (bad, str(e))


def case_image_default_path():
    """path が無ければ <台本のフォルダ>/images/s{NN}-{枠の名前}.png を使う（.jpg も見る）。"""
    d = _img_dir("img-default")
    shutil.copy(SAMPLE.parent / "assets" / "meeting.png", d / "images" / "s02-image.png")
    shutil.copy(SAMPLE.parent / "assets" / "calendar.png", d / "images" / "s01-member2.png")
    sc = {"title": "t", "slides": [
        {"type": "team", "headline": "h", "members": [{"name": "a"}, {"name": "b", "image": {"prompt": "人の絵"}}], "talk": ["", ""]},
        {"type": "photo", "headline": "h", "image": {"prompt": "会議室"}}]}
    path, info = _build_in(d, sc)
    assert (path.parent / "assets" / "s02-image.png").is_file() and (path.parent / "assets" / "s01-member2.png").is_file()
    slots = {(x["slide"], x["slot"]): x for x in info["images"]}
    assert slots[(2, "image")]["exists"] and pathlib.PurePath(slots[(2, "image")]["path"]).parts[-2:] == ("images", "s02-image.png")


def case_image_missing_placeholder():
    """画像が無いとき: 既定は rc=2（置き場を言う）・placeholder なら仮の枠＋警告（H-3 に掛かる「ここに画像」）。
    prompt だけの枠の置き場はキャッシュの区画の gen-<鍵>.png（ADR 0011・0014）。"generated" は生成の枠だけ仮にし、持ち込みの無い絵は止める。"""
    d = _img_dir("img-missing")
    sc = one({"type": "photo", "headline": "h", "image": {"prompt": "夕方の商店街を上から見た写真"}})
    try:
        _build_in(d, sc)
        raise AssertionError("無い画像が通った")
    except T.DeckError as e:
        # 置き場のパスは、区切り文字をそろえて（Windows の Git Bash は C:/… の形で渡し、エンジンは \ で書く）、そのままか
        # realpath（mac の /var は /private/var）のどちらかで当てる
        cache = os.environ["HTML_SLIDE_CACHE"]
        assert os.sep + "gen-" in str(e) and any(c in str(e) for c in (os.path.normpath(cache), os.path.realpath(cache))) \
            and "fill" in str(e), str(e)
    path, info = _build_in(d, sc, placeholder=True)
    html = path.read_text(encoding="utf-8")
    assert 'class="img-ph"' in html and "ここに画像: 夕方の商店街" in html
    assert any("仮の枠" in w and os.sep + "gen-" in w for w in info["warnings"]), info["warnings"]
    assert info["images"][0]["exists"] is False and info["images"][0]["origin"] == "generated"
    _build_in(d, sc, placeholder="generated")                       # 生成の枠は描く前でも通る
    brought = one({"type": "photo", "headline": "h", "image": {"path": "images/nai.png", "prompt": "写真"}})
    try:
        _build_in(d, brought, placeholder="generated")
        raise AssertionError("持ち込みの無い絵が通った")
    except T.DeckError as e:
        assert os.path.join("images", "nai.png") in str(e), str(e)   # エンジンは OS の区切り文字で書く


def case_image_slots():
    """image_slots: 全部の枠・推奨の大きさ（縦横比が一番近い）・prompt_full（文字を描かない・重ねる側を空ける）。"""
    assert T.gen_size(1920, 1080) == (1536, 1024)
    assert T.gen_size(600, 682) == (1024, 1024)
    assert T.gen_size(880, 1080) == (1024, 1536)
    d = _img_dir("img-slots")
    sc = {"title": "t", "slides": [
        {"type": "photo", "headline": "h", "image": {"prompt": "会議室"}},
        {"type": "photo", "layout": "left", "headline": "h", "image": {"prompt": "予定表"}},
        {"type": "cover", "title": "t", "image": "nai.png"}]}
    p = d / "deck.json"
    p.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    slots = deck.image_slots(p)
    assert [(x["slide"], x["type"], x["slot"]) for x in slots] == [(1, "photo", "image"), (2, "photo", "image"), (3, "cover", "image")]
    full, half, cover = slots
    assert full["size"] == "1536x1024" and full["clear"] == "左下"
    assert full["crop"].startswith("画像が無いので未定（1536x1024 で作ると上下を計 16% 切り抜く"), full["crop"]
    assert "文字・ロゴ・透かしを描かない" in full["prompt_full"] and "左下" in full["prompt_full"]
    assert half["clear"] is None and "空ける" not in half["prompt_full"]
    assert cover["exists"] is False and cover["path"].endswith("nai.png") and cover["prompt_full"] == ""
    sl = deck.image_slots(SAMPLE)
    assert len(sl) == 5 and all(x["exists"] for x in sl), sl


def case_image_sample():
    """画像の見本: 16枚・クリック18・警告0・全部の枠に prompt と画像・画像の型と置き方を全部使う・クリックの既定。"""
    path, info = deck.build_html(IMAGE_SAMPLE, TMP / "image-sample")
    sc = json.loads(IMAGE_SAMPLE.read_text(encoding="utf-8"))
    assert len(info["slides"]) == 16 and sum(x["clicks"] for x in info["slides"]) == 18, info["slides"]
    assert info["warnings"] == [], info["warnings"]
    assert all(x["prompt"] and x["exists"] for x in info["images"]), [x for x in info["images"] if not (x["prompt"] and x["exists"])]
    for s, i in zip(sc["slides"], info["slides"]):
        assert len(s["talk"]) == i["clicks"] + 1, (s["type"], i)
    uses = {(s["type"], s.get("layout"), s.get("tone"), s.get("split"), s.get("side")) for s in sc["slides"]}
    for need in (("cover", "hero", None, None, None), ("section", "hero", "dark", None, None), ("photo", "left", None, "1/3", None),
                 ("bullets", None, None, "2/5", "left"), ("quote", "full", None, None, None), ("quote", "portrait", None, None, None)):
        assert need in uses, need
    assert {"gallery", "figure", "annotate"} <= {s["type"] for s in sc["slides"]}
    for need in (("photo", "band", None, None, None), ("gallery", "collage", None, None, None)):
        assert need in uses, need
    assert any(s["type"] == "kpi" and s.get("image") for s in sc["slides"])
    assert any(s["type"] == "compare" and s["left"].get("image") and s["right"].get("image") for s in sc["slides"])
    assert sc["slides"][-1]["type"] == "closing" and sc["slides"][-1].get("image")
    assert any(s["type"] == "cards" and all(it.get("image") for it in s["items"]) for s in sc["slides"])
    gal = next(i for s, i in zip(sc["slides"], info["slides"]) if s["type"] == "gallery")
    assert gal["clicks"] == 3
    ann = next((s, i) for s, i in zip(sc["slides"], info["slides"]) if s["type"] == "annotate")
    assert ann[1]["clicks"] == len(ann[0]["items"])


def case_hero():
    """全面の表紙・章扉（layout: hero）: 幕・文字を置く側・明暗・誤りは rc=2。"""
    d = _img_dir("hero")
    shutil.copy(SAMPLE.parent / "images" / "s01-image.jpg", d / "images" / "s01-image.jpg")
    shutil.copy(SAMPLE.parent / "images" / "s01-image.jpg", d / "images" / "s02-image.jpg")
    sc = {"title": "t", "slides": [
        {"type": "cover", "layout": "hero", "title": "題", "subtitle": "副題", "image": {"prompt": "会議室"}},
        {"type": "section", "layout": "hero", "tone": "dark", "text_side": "right", "title": "章", "image": {"prompt": "夜"}},
        {"type": "closing", "headline": "h", "items": ["a"]}]}
    path, info = _build_in(d, sc)
    html = path.read_text(encoding="utf-8")
    secs = html.split("<section")[1:]
    assert 'class="veil"' in secs[0] and "linear-gradient(to right" in secs[0]
    assert 'class="cover-subtitle" style="font-size:32px; color:var(--ink)"' in secs[0], "明るい幕の副題は ink"
    assert "on-dark" in secs[1] and "linear-gradient(to left" in secs[1] and "var(--scrim)" in secs[1]
    assert re.search(r'class="sec-text on-dark" style="left:1032px', secs[1]), "右に文字（1920 − 144 − 744）"
    sl = {x["slide"]: x for x in info["images"]}
    assert sl[1]["clear"].startswith("左の45%") and sl[2]["clear"].startswith("右の45%")
    assert "左の45%" in sl[1]["prompt_full"]
    for bad, words in (({"type": "cover", "layout": "hero", "title": "t"}, "image が要る"),
                       ({"type": "cover", "title": "t", "tone": "dark"}, "hero のときだけ"),
                       ({"type": "section", "layout": "hero", "tone": "grey", "title": "t", "image": {"prompt": "x"}}, "tone は")):
        try:
            _build_in(d, one(bad), placeholder=True)
            raise AssertionError(f"通ってしまった: {bad}")
        except T.DeckError as e:
            assert words in str(e), (bad, str(e))


def case_graphic():
    """画像なしの表紙・章扉（layout: graphic）: 図形は svg.art の層で、全部が層の中（section の外に出ない）・図柄の既定は題で決まる・
    クリックは増やさない・handout は動かない・誤りは rc=2。"""
    import deck_art as ART
    sc = {"title": "図形の試し", "slides": [{"type": "cover", "layout": "graphic", "title": "題", "subtitle": "副題"}]
          + [{"type": "section", "layout": "graphic", "motif": m, "title": f"章 {m}"} for m in ART.MOTIFS]}
    path, info = build(sc, "graphic")
    secs = pathlib.Path(path).read_text(encoding="utf-8").split("<section")[1:]
    assert len({ART.default_motif(f"資料{i}") for i in range(20)}) == len(ART.MOTIFS), "題が違えば図柄も替わる"
    want = ART.art_layers(ART.default_motif("図形の試し"), 880, 1080)
    assert want[0] and want[0] in secs[0], "motif を省いた表紙は、題で決まる図柄"
    for i, sec in enumerate(secs):
        arts = re.findall(r'<svg class="art" viewBox="0 0 (\d+) (\d+)"[^>]*style="position:absolute; left:(\d+)px([^>]*)>(.*?)</svg>', sec)
        assert len(arts) >= 3, f"{i + 1}枚目: 図形の層が無い"
        w, h, left = int(arts[0][0]), int(arts[0][1]), int(arts[0][2])
        assert left + w == 1920 and h == 1080 and left == (1040 if i == 0 else 960), (left, w, h)
        assert all('data-step="0"' in a[3] for a in arts), "図形は画面に入ったら出る（step 0）"
        for a in arts:
            for pts in re.findall(r'points="([^"]+)"', a[4]):
                for xy in pts.split():
                    x, y = map(float, xy.split(","))
                    assert -0.01 <= x <= w + 0.01 and -0.01 <= y <= h + 0.01, f"{i + 1}枚目: 層の外の点 {x},{y}"
            for cx, cy, r in re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"', a[4]):
                assert float(r) <= float(cx) <= w - float(r) and float(r) <= float(cy) <= h - float(r)
            for x, y, rw, rh, st in re.findall(r'<rect x="([\d.-]+)" y="([\d.-]+)" width="([\d.]+)" height="([\d.]+)" style="([^"]*)"', a[4]):
                m = float(re.search(r"stroke-width:([\d.]+)", st).group(1)) / 2 if "stroke:" in st else 0   # 線は辺の両側に出る
                x, y, rw, rh = float(x) - m, float(y) - m, float(rw) + 2 * m, float(rh) + 2 * m
                assert -0.01 <= x and x + rw <= w + 0.01 and -0.01 <= y and y + rh <= h + 0.01, f"{i + 1}枚目: 線ごと層の外 {x},{y},{rw},{rh}"
    assert 'data-step="1"' not in "".join(secs), "飾りでクリックが増えた"
    path, _ = build(sc, "graphic-handout", mode="handout")
    assert "data-step" not in pathlib.Path(path).read_text(encoding="utf-8").split("<section", 1)[1]
    for bad, words in (({"type": "cover", "layout": "graphic", "title": "t", "image": {"prompt": "x"}}, "image と一緒に書かない"),
                       ({"type": "cover", "title": "t", "motif": "circles"}, "graphic のときだけ"),
                       ({"type": "section", "layout": "graphic", "motif": "waves", "title": "t"}, "motif は"),
                       ({"type": "cover", "layout": "grafic", "title": "t"}, "graphic（")):
        expect_error(one(bad), "graphic-bad", words)


def _role_sizes(html: str, cls: str) -> list[int]:
    return [int(v) for v in re.findall(rf'class="{cls}" style="font-size:(\d+)px', html)]


def case_grow():
    """中身が少ないときに大きく組む（GROW_STEPS）: 短い3枚のカードは大きくなる・詰まったページと画像のあるページは元のまま・
    並ぶ項目の題と本文は同じ大きさにそろう・本文は題との元の比より大きくしない。"""
    def html_of(slide, name):
        path, _ = build(one(slide), name)
        return pathlib.Path(path).read_text(encoding="utf-8").split("<section", 1)[1]
    short = {"type": "cards", "headline": "見出し", "items": [{"title": "速い", "body": "待たない"}, {"title": "安い", "body": "払わない"},
                                                             {"title": "近い", "body": "歩ける"}]}
    h = html_of(short, "grow-short")
    ts, bs = _role_sizes(h, "card-title"), _role_sizes(h, "card-body")
    assert len(set(ts)) == 1 and ts[0] > 36, f"短いカードの題が大きくならない: {ts}"
    assert len(set(bs)) == 1 and bs[0] <= 45 and bs[0] < ts[0] and bs[0] > 32, f"本文が段を上がっていない・題や上限（本文 45・ADR 0022）を越えた: {ts} {bs}"
    assert all(v in T.SCALE for v in ts + bs), "大きく組んでも DADS の段の外にならない"
    mixed = copy.deepcopy(short)
    mixed["items"][1]["title"] = "コラムに使わない／TOP には二度と使わない"
    ts = _role_sizes(html_of(mixed, "grow-mixed"), "card-title")
    assert len(set(ts)) == 1, f"題の大きさがそろわない: {ts}"
    full = {"type": "bullets", "headline": "見出し", "items": [
        {"title": f"項目{i}の題はここまで長く書いてある", "body": "本文も二行に届くくらいの長さで書いておき、場所を使い切るようにしてある。さらにもう少し足す"}
        for i in range(5)]}
    assert max(_role_sizes(html_of(full, "grow-full"), "li-title")) <= 45, "詰まったページが大きくなった"
    d = _img_dir("grow-img")
    sc = one({"type": "cards", "headline": "見出し", "items": [dict(it, image={"prompt": "x"}) for it in short["items"]]})
    path, _ = _build_in(d, sc, placeholder=True)
    assert max(_role_sizes(pathlib.Path(path).read_text(encoding="utf-8"), "card-title")) <= 45, "画像のあるページが大きくなった"
    long5 = {"type": "flow", "headline": "見出し", "items": [{"label": "1", "title": "あ" * 19, "body": "本文"} for _ in range(5)]}
    bs = _role_sizes(html_of(long5, "grow-floor"), "flow-body")
    assert min(bs) >= 28, f"大きく組んだのに本文が段を上る前の大きさより縮んだ: {bs}"
    tl = {"type": "timeline", "headline": "見出し", "items": [{"when": "2026年10月から2027年3月", "title": "開始"},
                                                                {"when": "2027年", "title": "実行"}, {"when": "2028年", "title": "完了"}]}
    hs = re.findall(r'class="tl-when" style="font-size:\d+px; height:([\d.]+)px"', html_of(tl, "grow-tl"))
    assert len(hs) == 3 and len(set(hs)) == 1, f"時期の行の高さがそろわない（丸と線がずれる）: {hs}"
    d2 = _img_dir("grow-img2")
    sc = one({"type": "cards", "headline": "見出し", "items": [{"title": "速い", "image": {"prompt": "x"}},
                                                             {"title": "あ" * 40, "image": {"prompt": "y"}}]})
    _, info = _build_in(d2, sc, placeholder=True)
    names = [x["slot"] for x in info["images"]]
    assert len(names) == len(set(names)) == 2, f"画像の枠が二重に数えられた: {names}"
    flow = {"type": "flow", "headline": "見出し", "items": [{"label": "1段目", "title": "完全一致"}, {"label": "2段目", "title": "見た目の指紋"},
                                                            {"label": "3段目", "title": "画像の意味＋人の目"}]}
    ts = _role_sizes(html_of(flow, "grow-flow"), "flow-title")
    assert len(set(ts)) == 1 and ts[0] > 36, f"短い矢羽根の題が大きくならない: {ts}"


def case_grow_more_types():
    """grow を広げた型（ADR 0022）: 中身が少ないときは文字が段を上り、詰まったページは元のまま（DADS の段の中）。"""
    def html_of(slide, name):
        path, _ = build(one(slide), name)
        return pathlib.Path(path).read_text(encoding="utf-8").split("<section", 1)[1]
    sizes = lambda h, c: [int(v) for v in re.findall(rf'class="{c}[^"]*" style="[^"]*?font-size:(\d+)px', h)]
    ax = {"label": "軸", "low": "低", "high": "高"}
    mem = lambda n: [{"name": f"人{i}", "role": "役割の例", "note": "ひとことの例を書く"} for i in range(n)]
    cases = [
        ("agenda", {"type": "agenda", "items": ["現状", "原因", "対策"]},
         {"type": "agenda", "items": [{"title": f"六つの項目の{i}番目", "note": "補足"} for i in range(6)]}, "agenda-title", 45),
        ("team", {"type": "team", "headline": "h", "members": mem(2)}, {"type": "team", "headline": "h", "members": mem(6)}, "member-name", 32),
        ("hub", {"type": "hub", "headline": "h", "center": {"title": "中心"}, "items": [{"title": "a"}, {"title": "b"}, {"title": "c"}]},
         {"type": "hub", "headline": "h", "center": {"title": "中心"}, "items": [{"title": f"部署{i}の名前", "body": "補足の文", "tag": "札"} for i in range(6)]}, "hub-title", 36),
        ("bar-chart", {"type": "bar-chart", "headline": "h", "bars": [{"label": "a", "value": 1}, {"label": "b", "value": 2}, {"label": "c", "value": 3}]},
         {"type": "bar-chart", "headline": "h", "bars": [{"label": f"部署{i}", "value": i} for i in range(1, 9)]}, "bar-label", 28),
        ("matrix", {"type": "matrix", "headline": "h", "x": ax, "y": ax, "items": [{"label": "案A", "x": 75, "y": 75}, {"label": "案B", "x": 25, "y": 25}]},
         {"type": "matrix", "headline": "h", "x": ax, "y": ax, "items": [{"label": f"案{i}", "x": 10 + 11 * i, "y": 10 + 11 * ((i * 3) % 8)} for i in range(8)]}, "mx-label", 28),
        ("line-chart", {"type": "line-chart", "headline": "h", "labels": ["1", "2", "3", "4"], "series": [{"values": [1, 2, 3, 4]}]},
         {"type": "line-chart", "headline": "h", "labels": [str(i) for i in range(12)], "series": [{"values": list(range(12))}]}, "bar-label", 28),
        ("quote", {"type": "quote", "quote": "短い言葉。"}, None, "quote-text", 57),
        ("equation", {"type": "equation", "headline": "h", "items": [{"title": "a"}, {"title": "b"}], "result": {"title": "c"}}, None, "eq-title", 45),
        ("steps", {"type": "steps", "headline": "h", "current": 2, "items": [{"title": "a"}, {"title": "b"}, {"title": "c"}]}, None, "st-title", 36),
        ("big-number", {"type": "big-number", "headline": "h", "value": "1", "label": "説明"}, None, "bn-label", 36),
        ("kpi", {"type": "kpi", "headline": "h", "items": [{"label": "a", "value": "1"}, {"label": "b", "value": "2"}, {"label": "c", "value": "3"}]}, None, "kpi-label", 28),
        ("pairs", {"type": "pairs", "headline": "h", "items": [{"label": "a", "before": "x", "after": "y"}, {"label": "b", "before": "x", "after": "y"}]}, None, "pair-label", 36),
    ]
    for name, few, many, cls, base in cases:
        sz = sizes(html_of(few, f"gm-{name}-few"), cls)
        assert sz and max(sz) > base and all(v in T.SCALE for v in sz), f"{name}: 少ないときに {cls} が {base} から上がらない: {sz}"
        if many:
            sz = sizes(html_of(many, f"gm-{name}-many"), cls)
            assert sz and max(sz) <= base, f"{name}: 詰まったページが大きくなった: {sz}"
    tbl = lambda n: {"type": "table", "headline": "h", "columns": ["a", "b", "c"], "rows": [["1", "2", "3"]] * n}
    sz = lambda h: [int(v) for v in re.findall(r'<div class="td"[^>]*><div style="font-size:(\d+)px', h)]
    assert max(sz(html_of(tbl(2), "gm-table-few"))) > 28 and max(sz(html_of(tbl(7), "gm-table-many"))) <= 28, "表"
    nt = lambda n: {"type": "notice", "headline": "h", "items": [{"title": "題", "body": "本文"}] * n}
    assert max(sizes(html_of(nt(1), "gm-notice"), "nt-title")) > 45, "notice"


def case_diagram_types():
    """図解の型・アイコン・締めの帯（ADR 0019）: 見出しは大きい・締めの帯は最後の段と一緒に出て中身はその上で止まる・
    アイコンは図形の SVG・知らないアイコンと置けない型の締めの帯と、箇条書きのアイコンの書き漏れは rc=2。"""
    import deck_icons as ICON
    sc = {"title": "t", "slides": [
        {"type": "equation", "headline": "短い会議 ＝ 30分＋議題", "items": [{"icon": "clock", "title": "30分"}, {"title": "議題"}],
         "result": {"title": "半分", "body": "実測"}, "takeaway": "2つがそろうと半分になる"},
        {"type": "cards", "headline": "見出し", "items": [{"icon": "search", "title": "探す"}, {"title": "見せる"}]},
        {"type": "bullets", "headline": "見出し", "items": [{"icon": "alert", "title": "a"}, {"icon": "clock", "title": "b"}]}]}
    path, info = build(sc, "diagram")
    secs = pathlib.Path(path).read_text(encoding="utf-8").split("<section")[1:]
    assert info["slides"][0]["clicks"] == 3, info["slides"][0]
    m = re.search(r'class="headline" style="font-size:(\d+)px', secs[0])
    assert int(m.group(1)) > 60, f"図解の型の見出しが大きくない: {m.group(1)}"
    take = re.search(r'<div class="take" style="left:[\d.]+px; top:([\d.]+)px;[^"]*"([^>]*)>', secs[0])
    assert take and float(take.group(1)) == T.BODY_BOTTOM - T.TAKE_H, take and take.group(1)
    assert 'data-step="3"' in take.group(2), f"締めの帯は最後の段（3）と一緒に出る: {take.group(2)}"
    box_top = re.search(r'class="center" style="left:120px; top:([\d.]+)px; width:1680px; height:([\d.]+)px', secs[0])
    assert float(box_top.group(1)) + float(box_top.group(2)) <= T.BODY_BOTTOM - T.TAKE_H - T.TAKE_GAP + 0.5, "中身が帯に重なる"
    # 丸の色は CSS 変数（--disc-ground・--disc-ink）経由。変数が無ければ今までと同じ色（淡い地に主役の色の文字）
    assert "var(--disc-ink, var(--primary-text))" in secs[1] and "var(--disc-ground, var(--tint))" in secs[1], "カードのアイコン: 丸の色は変数経由で、既定は今までと同じ"
    assert "stroke" not in re.search(r"<svg.*?</svg>", secs[1], re.S).group(0) and 'class="icon-disc"' in secs[1], "カードのアイコン"
    assert secs[2].count('class="li-icon"') == 2
    assert len(ICON.ICONS) == 100 and all(re.fullmatch(r"[a-z]+(-[a-z]+)*", k) for k in ICON.ICONS)
    assert all(d for d, _ in ICON.ICONS.values()) and set(ICON.DESCRIPTIONS) == set(ICON.ICONS), "説明は100種すべてに付く"
    assert all(v["viewBox"] == "0 -960 960 960" and v["path"][0] in "Mm" for _, v in ICON.ICONS.values())
    assert 'style="fill:#123456"' in ICON.svg("info", 40, "#123456") and "stroke" not in ICON.svg("info", 40, "#123456")
    fmt = (pathlib.Path(__file__).resolve().parents[1] / "references" / "deck-format.md").read_text(encoding="utf-8")
    assert ICON.words() in fmt, "deck-format.md のアイコンの一覧が deck_icons.words() と違う（貼り直す）"
    for bad, words in (({"type": "cards", "headline": "h", "items": [{"icon": "unicorn", "title": "a"}, {"title": "b"}]}, "unicorn"),
                       ({"type": "statement", "headline": "h", "takeaway": "x"}, "知らないキー"),
                       ({"type": "team", "headline": "h", "members": [{"name": "a"}, {"name": "b"}], "takeaway": "x"}, "知らないキー"),
                       ({"type": "equation", "headline": "h", "items": [{"title": "A"}, {"title": "B"}, {"title": "C"}, {"title": "D"}],
                         "result": {"title": "答え", "body": "あいうえおかきくけこさしすせそたちつてとなにぬねの"}}, "長すぎて"),
                       ({"type": "bullets", "headline": "h", "items": [{"icon": "alert", "title": "a"}, {"title": "b"}]}, "全部の項目"),
                       ({"type": "equation", "headline": "h", "items": [{"title": "a"}, {"title": "b"}]}, "result"),
                       ({"type": "hub", "headline": "h", "center": {"title": "c"}, "items": [{"title": "a"}, {"title": "b"}]}, "3〜6"),
                       ({"type": "pairs", "headline": "h", "items": [{"label": "a", "before": "x"}, {"label": "b", "before": "x", "after": "y"}]}, "after"),
                       ({"type": "cards", "headline": "h", "items": [{"icon": "clock", "image": {"prompt": "x"}, "title": "a"},
                                                                    {"icon": "clock", "image": {"prompt": "y"}, "title": "b"}]}, "どちらか")):
        try:
            build(one(bad), "diagram-bad", placeholder=True)
            raise AssertionError(f"通ってしまった: {bad}")
        except T.DeckError as e:
            assert words in str(e), (bad, str(e))


def case_overlay_contrast():
    """写真の上の文字の幕が薄すぎれば rc=2（暗い幕は白い写真、明るい幕は黒い写真を仮定）。使った幕だけを見る。"""
    for table, name, deckp in ((T.OVERLAYS, "photo（全面）", SAMPLE), (T.LIGHT_OVERLAYS, "cover・section（hero・light）", IMAGE_SAMPLE)):
        keep = table[name]
        table[name] = 0.3
        try:
            deck.build_html(deckp, TMP / "overlay-thin")
            raise AssertionError(f"{name} 0.3 が通った")
        except T.DeckError as e:
            assert name in str(e) and "コントラスト" in str(e) and "枚目" in str(e), str(e)
        finally:
            table[name] = keep
    p = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    assert deck.ratio(p["accent-on-dark"], deck.mix(p["ink"], T.SCRIM_ALPHA, "#ffffff")) >= 4.5      # 写真の上の文字も 4.5（DADS）
    veil = deck.mix(p["bg"], T.HERO_VEIL, "#000000")
    assert deck.ratio(p["primary-on-light"], veil) >= 4.5 and deck.ratio(p["ink"], veil) >= 4.5
    # Quick Slide の既定のブランド（水色 #0DB4F2・ink #333333）でも、tint の地（visual）の全面の表紙の幕で 4.5 を満たす
    for look in ({}, {"background": "tint"}):
        th = deck.DS.look_theme({"background": "#ffffff", "ink": "#333333", "primary": "#0db4f2", "font": "Noto Sans JP", "font_files": None}, look)
        pal = deck.palette(th)
        assert deck.overlay_problems(pal, {"cover・section（hero・light）": 1, "cover・section（hero・dark）": 2, "photo（全面）": 3, "quote（全面）": 4}) == [], look


def case_focus_words():
    """focus: 1〜2語（"right top"・"top left"・"center 30%"）を % に直す。px は rc=2。"""
    s = T.Slide(1, {"type": "photo"}, {}, False, TMP, [])
    assert s.focus_css("right top", "image") == "100% 0%"
    assert s.focus_css("top left", "image") == "0% 0%"
    assert s.focus_css("center 30%", "image") == "50% 30%"
    assert s.focus_css("bottom", "image") == "50% 100%"
    for bad in ("20px 10px", "left right top", "130% 0%"):
        try:
            s.focus_css(bad, "image")
            raise AssertionError(bad)
        except T.DeckError as e:
            assert "px は使えない" in str(e)


def case_split_side():
    """写真の幅（split）と箇条書きの写真の側（side）。"""
    d = _img_dir("split")
    shutil.copy(SAMPLE.parent / "assets" / "calendar.png", d / "c.png")
    sc = one({"type": "photo", "layout": "left", "split": "1/3", "headline": "h", "image": "c.png"})
    html = _build_in(d, sc)[0].read_text(encoding="utf-8")
    assert 'style="left:0px; top:0px; width:640px; height:1080px;' in html and 'class="photo-text" style="left:760px' in html
    sc = one({"type": "bullets", "headline": "h", "items": ["a", "b"], "image": "c.png", "side": "left", "split": "1/2"},
             ["", "", ""])
    html = _build_in(d, sc)[0].read_text(encoding="utf-8")
    assert 'class="side-img" src="assets/s01-image.png" alt="" style="left:120px;' in html and f"width:{T.span(6)}px" in html     # 格子の 6 列
    assert f'class="list" style="left:{120 + T.span(6) + 96}px' in html
    for bad, words in (({"type": "photo", "headline": "h", "image": "c.png", "split": "1/3"}, "left・right のときだけ"),
                       ({"type": "bullets", "headline": "h", "items": ["a", "b"], "side": "left"}, "image があるときだけ")):
        try:
            _build_in(d, one(bad, ["", "", ""]))
            raise AssertionError(bad)
        except T.DeckError as e:
            assert words in str(e), str(e)


def case_figure_gallery_cards():
    """図は切らずに見せる（contain・キャプションは図のすぐ下）・写真の並びは1枚ずつ・カードの写真は全部か無しか。"""
    _, info = deck.build_html(IMAGE_SAMPLE, TMP / "fig")
    fig = next(x for x in info["images"] if x["type"] == "figure")
    assert fig["fit"] == "contain" and fig["crop"].startswith("切り抜かない")
    html = pathlib.Path(info["html"]).read_text(encoding="utf-8")
    m = re.search(r'class="figure-img"[^>]*style="left:120px; top:(\d+)px; width:1680px; height:(\d+)px;', html)
    c = re.search(r'class="fig-cap" style="left:120px; top:(\d+)px', html)
    top, fh, cap = int(m.group(1)), int(m.group(2)), int(c.group(1))
    dh = 900 * min(1680 / 1600, fh / 900)            # 見本の図は 1600×900
    assert abs(cap - (top + (fh + dh) / 2 + 16)) <= 1, (top, fh, cap)
    sc = one({"type": "cards", "headline": "h", "items": [{"title": "a", "image": {"prompt": "x"}}, {"title": "b"}]}, ["", "", ""])
    try:
        build(sc, "cards-img", placeholder=True)
        raise AssertionError("片方だけの写真が通った")
    except T.DeckError as e:
        assert "全部のカードに書く" in str(e)


def case_quote_layouts():
    """引用: full は写真の上（下の文字・ページ番号を出さない・白い文字）・portrait は左に丸い写真・plain に image は rc=2。"""
    path, _ = deck.build_html(IMAGE_SAMPLE, TMP / "quote-layouts")
    secs = parse(path)
    sc = json.loads(IMAGE_SAMPLE.read_text(encoding="utf-8"))
    for s, sec in zip(sc["slides"], secs):
        if s["type"] == "quote" and s["layout"] == "full":
            assert not sec["keep"], "全面の引用に帯・下の文字・ページ番号を出さない"
        if s["type"] == "quote" and s["layout"] == "portrait":
            assert "pageno" in sec["keep"]
    html = path.read_text(encoding="utf-8")
    assert 'class="quote on-dark"' in html and "var(--scrim-quote)" in html
    try:
        build(one({"type": "quote", "quote": "q", "image": {"prompt": "x"}}), "quote-plain-img", placeholder=True)
        raise AssertionError("plain に image が通った")
    except T.DeckError as e:
        assert "layout を full か portrait" in str(e)


def case_small_image_warning():
    """枠の 0.8 倍より小さい画像は、引き伸ばすと警告（rc は変えない）。"""
    from PIL import Image
    d = _img_dir("small")
    Image.new("RGB", (400, 225), "#888888").save(d / "images" / "s01-image.png")
    _, info = _build_in(d, one({"type": "photo", "headline": "h", "image": {"prompt": "小さい"}}))
    assert any("小さい" in w and "400×225" in w for w in info["warnings"]), info["warnings"]
    assert info["images"][0]["pixels"] == "400x225" and info["images"][0]["size_exact"] == "2048x1152"


def case_r3_overlay_only_used():
    """Codex r3 #4: 写真を使わない資料は、幕の検査で止めない（使う置き方だけを検査する）。"""
    th = TMP / "plain-theme.yaml"
    th.write_text('background: "#ffffff"\nink: "#6a6a6a"\nmuted: "#6a6a6a"\nprimary: "#1f5fd1"\n', encoding="utf-8")   # 文字は 4.5:1 だが、暗幕にすると白い文字が 4.5:1 に届かない淡さ
    sc = one({"type": "statement", "headline": "文字だけの主張"})
    path, _ = build(sc, "r3-plain", theme=str(th))          # 写真を使わないので通る
    assert path.is_file()
    d = _img_dir("r3-plain-photo")
    shutil.copy(SAMPLE.parent / "assets" / "meeting.png", d / "m.png")
    try:                                                    # 全面の写真に白い文字を重ねると、薄い ink の暗幕では読めない
        _build_in(d, one({"type": "photo", "headline": "h", "image": "m.png"}), theme=str(th))
        raise AssertionError("薄い ink の全面写真が通った")
    except T.DeckError as e:
        assert "1枚目 photo（全面）" in str(e), str(e)


def case_r3_figure_caption():
    """Codex r3 #2: 図のキャプションは、focus の縦の位置で実際に描いた画像の下端の下に置く（重ならない）。"""
    from PIL import Image
    d = _img_dir("r3-fig")
    Image.new("RGB", (2000, 500), "#888888").save(d / "images" / "s01-image.png")
    for focus, expect in (("bottom", 1.0), ("top", 0.0), (None, 0.5)):
        im = {"prompt": "横長の図"} | ({"focus": focus} if focus else {})
        sc = one({"type": "figure", "headline": "短い", "image": im, "caption": "説明"})
        html = _build_in(d, sc)[0].read_text(encoding="utf-8")
        m = re.search(r'class="figure-img"[^>]*style="left:120px; top:(\d+)px; width:1680px; height:(\d+)px;', html)
        top, fh = int(m.group(1)), int(m.group(2))
        dh = 500 * min(1680 / 2000, fh / 500)
        img_bottom = top + (fh - dh) * expect + dh
        cap = int(re.search(r'class="fig-cap" style="left:120px; top:(\d+)px', html).group(1))
        assert img_bottom + 15 <= cap <= img_bottom + 17, (focus, img_bottom, cap)


def case_r3_quote_full_source():
    """Codex r3 #3: 全面の写真の引用でも source を出す（暗幕の上なので白）。"""
    d = _img_dir("r3-quote")
    shutil.copy(SAMPLE.parent / "assets" / "meeting.png", d / "m.png")
    sc = one({"type": "quote", "layout": "full", "quote": "q", "image": "m.png", "source": "出典の文字"})
    html = _build_in(d, sc)[0].read_text(encoding="utf-8")
    assert re.search(r'class="source" style="[^"]*color:#ffffff;[^"]*" data-name="出典">出典の文字<', html)


def case_r3_crop_actual():
    """Codex r3 #6: 切り抜きの説明は、画像が在れば実寸で（1774×887 を正方形に入れると左右を計 50%）、無ければ未定。"""
    from PIL import Image
    d = _img_dir("r3-crop")
    Image.new("RGB", (1774, 887), "#888888").save(d / "images" / "s01-image.png")
    sc = one({"type": "quote", "layout": "portrait", "quote": "q", "image": {"prompt": "人物"}})
    p = d / "deck.json"
    p.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    sl = deck.image_slots(p)[0]
    assert sl["crop"] == "左右を計 50% 切り抜く（中央を残す）", sl["crop"]
    (d / "images" / "s01-image.png").unlink()
    sl = deck.image_slots(p)[0]
    assert sl["crop"].startswith("画像が無いので未定") and "切り抜かない）" in sl["crop"], sl["crop"]


def case_catalog_script():
    """カタログ: 全部の型と、見た目が大きく変わる置き方を網羅・deck を通る・名前は重ならない・--only の絞り込み。"""
    import catalog as C
    sc, names = C.catalog_script()
    ss = sc["slides"]
    assert {s["type"] for s in ss} == set(T.TYPES), set(T.TYPES) - {s["type"] for s in ss}
    assert len(names) == len(set(names)) == len(ss)
    got = {(s["type"], s.get("layout"), s.get("text_side", "left"), s.get("tone", "light"), s.get("split"),
            s.get("side") if isinstance(s.get("side"), str) else None,
            bool(s.get("image"))) for s in ss}
    need = [("cover", "hero", "left", "light", None, None, True), ("cover", "hero", "right", "light", None, None, True),
            ("cover", "hero", "left", "dark", None, None, True), ("cover", None, "left", "light", None, None, True),
            ("section", "hero", "left", "light", None, None, True), ("section", "hero", "left", "dark", None, None, True),
            ("section", None, "left", "light", None, None, True), ("photo", None, "left", "light", None, None, True),
            ("quote", "full", "left", "light", None, None, True), ("quote", "portrait", "left", "light", None, None, True),
            ("quote", None, "left", "light", None, None, False), ("figure", None, "left", "light", None, None, True)]
    need += [("photo", lay, "left", "light", sp, None, True) for lay in ("left", "right") for sp in ("1/2", "2/5", "1/3")]
    need += [("bullets", None, "left", "light", None, None, True), ("bullets", None, "left", "light", "2/5", "left", True),
             ("bullets", None, "left", "light", "1/2", None, True)]
    for n in need:
        assert n in got, n
    assert sorted(len(s["items"]) for s in ss if s["type"] == "gallery" and not s.get("layout")) == [2, 3, 4]
    assert any(s["type"] == "gallery" and s.get("layout") == "collage" for s in ss)
    assert any(s["type"] == "photo" and s.get("layout") == "band" for s in ss)
    assert any(s["type"] == "kpi" and s.get("image") for s in ss) and any(s["type"] == "closing" and s.get("image") for s in ss)
    assert any(s["type"] == "compare" and s["left"].get("image") for s in ss)
    assert {s.get("fit", "cover") for s in ss if s["type"] == "annotate"} == {"cover", "contain"}
    assert any(s["type"] == "cards" and all(i.get("image") for i in s["items"]) for s in ss)
    assert any(s["type"] == "team" and all(m.get("image") for m in s["members"]) for s in ss)
    p = TMP / "catalog.json"
    p.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    _, info = deck.build_html(p, TMP / "catalog")
    assert len(info["slides"]) == len(ss) and all(x["exists"] for x in info["images"])
    hero = [x for x in info["images"] if x["slide"] == 3][0]
    assert hero["path"].endswith("s01-image.jpg"), "全面ヒーローは本物の絵"
    img, _ = C.catalog_script("image")
    txt, _ = C.catalog_script("text")
    assert img["slides"] and all(_has_image(s) for s in img["slides"])
    assert txt["slides"] and not any(_has_image(s) for s in txt["slides"])
    assert len(img["slides"]) + len(txt["slides"]) == len(ss)
    assert {s["type"] for s in C.catalog_script("photo")[0]["slides"]} == {"photo"}
    try:
        C.catalog_script("nope")
        raise AssertionError("知らない --only が通った")
    except T.DeckError as e:
        assert "--only" in str(e)
    assert "big-number / 補足あり" in names, "配列のキー（side）を名前に出さない"
    assert "cover / layout: hero・text_side: right" in names


def _has_image(s: dict) -> bool:
    return (bool(s.get("image")) or any(isinstance(x, dict) and x.get("image") for k in ("items", "members") for x in s.get(k) or [])
            or any(isinstance(s.get(k), dict) and s[k].get("image") for k in ("left", "right")))


def case_catalog_sheets():
    """一覧画像: 4列×4段で並べ、収まらなければ複数枚（Chrome を使わず、仮の PNG で並べ方だけ見る）。"""
    import catalog as C
    import outputs as OUT
    from PIL import Image
    d = TMP / "sheets"
    d.mkdir(exist_ok=True)
    pngs = []
    for i in range(18):
        q = d / f"slide-{i + 1:02d}.png"
        Image.new("RGB", (1920, 1080), (200, 200 - i * 5, 220)).save(q)
        pngs.append(q)
    out = C.contact_sheets(pngs, [f"型{i} / 置き方の名前が長い場合の折り返しを見る" for i in range(18)], d, "試験")
    assert [p.name for p in out] == ["catalog-01.png", "catalog-02.png"]
    w1, h1 = Image.open(out[0]).size
    w2, h2 = Image.open(out[1]).size
    assert w1 == w2 == OUT.MARGIN * 2 + OUT.COLS * OUT.THUMB_W + (OUT.COLS - 1) * OUT.GAP
    assert h1 > h2, "2枚目は1段だけ"
    assert T.num_label(6.0, 1) == "6.0" and T.num_label(1240) == "1,240"


def case_six_image_layouts():
    """画像の置き方6つ: 帯（photo band）・写真つきの締め・コラージュ・写真つきの比較・写真の帯＋数字・注釈つきの画像。"""
    path, info = deck.build_html(IMAGE_SAMPLE, TMP / "six")
    sc = json.loads(IMAGE_SAMPLE.read_text(encoding="utf-8"))
    secs = parse(path)
    html = path.read_text(encoding="utf-8").split("<section")[1:]
    no = {s["type"] + ("/" + s.get("layout", "") if s.get("layout") else ""): i for i, s in enumerate(sc["slides"])}
    band = no["photo/band"]
    assert 'style="left:0px; top:0px; width:1920px; height:480px;' in html[band]
    assert "band" not in secs[band]["keep"] and "pageno" in secs[band]["keep"], "帯の写真には上の印を重ねない・ページ番号は出す"
    kpi = no["kpi"]
    assert f'width:1920px; height:{T.KPI_BAND_H}px;' in html[kpi] and "band" not in secs[kpi]["keep"]
    assert re.search(rf'class="head" style="width:1680px; top:{T.KPI_BAND_H + 48}px"', html[kpi])
    cl = no["closing"]
    assert f'style="left:{T.W - T.CLOSING_IMG_W}px; top:0px; width:{T.CLOSING_IMG_W}px; height:1080px;' in html[cl] and not secs[cl]["keep"]
    col = no["gallery/collage"]
    assert len(re.findall(r'class="gal-item"', html[col])) == 3 and f"width:{T.span(7)}px" in html[col]
    sl = {(x["slide"], x["slot"]) for x in info["images"]}
    cmpn = no["compare"] + 1
    assert (cmpn, "left") in sl and (cmpn, "right") in sl
    for bad, words in (
            ({"type": "gallery", "layout": "collage", "headline": "h", "items": [{"image": {"prompt": "a"}}, {"image": {"prompt": "b"}}]},
             "items は 3〜3 個"),
            ({"type": "compare", "headline": "h", "left": {"title": "a", "image": {"prompt": "x"}}, "right": {"title": "b"}}, "両方に書く"),
            ({"type": "annotate", "headline": "h", "image": {"prompt": "x"}, "items": [{"x": 120, "y": 0, "title": "a"}]}, "0〜100"),
            ({"type": "annotate", "headline": "h", "image": {"prompt": "x"}, "fit": "fill", "items": [{"x": 1, "y": 1, "title": "a"}]}, "fit は")):
        try:
            build(one(bad, ["", "", "", ""]), "six-bad", placeholder=True)
            raise AssertionError(f"通ってしまった: {bad}")
        except T.DeckError as e:
            assert words in str(e), (bad, str(e))


def case_annotate_marks():
    """注釈の印: cover は枠の %、contain は実際に描いた画像の %。印と説明は同じクリックで1つずつ出る。"""
    from PIL import Image
    d = _img_dir("annotate")
    Image.new("RGB", (2000, 1000), "#888888").save(d / "images" / "s01-image.png")
    for fit_mode in ("cover", "contain"):
        sc = one({"type": "annotate", "headline": "短い", "fit": fit_mode, "image": {"prompt": "図"},
                  "items": [{"x": 0, "y": 0, "title": "左上"}, {"x": 100, "y": 100, "title": "右下"}]}, ["", "", ""])
        html = _build_in(d, sc)[0].read_text(encoding="utf-8")
        m = re.search(rf'class="(?:side|figure)-img"[^>]*style="left:120px; top:(\d+)px; width:{T.ANNOTATE_IMG_W}px; height:(\d+)px;', html)
        top, ih = int(m.group(1)), int(m.group(2))
        dots = [tuple(map(int, x)) for x in re.findall(r'class="mk-dot" style="left:(-?\d+)px; top:(-?\d+)px;', html)]
        if fit_mode == "cover":
            exp = [(120, top), (120 + T.ANNOTATE_IMG_W, top + ih)]
        else:
            k = min(T.ANNOTATE_IMG_W / 2000, ih / 1000)
            rw, rh = 2000 * k, 1000 * k
            ox, oy = 120 + (T.ANNOTATE_IMG_W - rw) / 2, top + (ih - rh) / 2
            exp = [(ox, oy), (ox + rw, oy + rh)]
        for (x, y), (ex, ey) in zip(dots, exp):
            assert abs(x + T.MARK_D / 2 - ex) <= 1 and abs(y + T.MARK_D / 2 - ey) <= 1, (fit_mode, dots, exp)
        steps = re.findall(r'data-step="(\d)" data-fx="(pop|rise)"[^>]*data-name="(印|説明)\d', html)
        assert sorted(steps) == sorted([("1", "pop", "印"), ("1", "rise", "説明"), ("2", "pop", "印"), ("2", "rise", "説明")]), steps


def case_line_breaks():
    """台本の改行（\\n）は <br> になり、行数の見積もりにも数える（balance が文節を割るとき切れ目を決める）。"""
    assert T.rich("次は1月から、\n全部署に広げたい") == "次は1月から、<br>全部署に広げたい"
    assert T.count_lines("あ\nい", 60, 1000) == 2
    try:
        T.fit("一行目\n二行目\n三行目", 60, 1680, what="見出し", lines=2, bold=True)
        raise AssertionError("3行の見出しが通った")
    except T.DeckError:
        pass


def _r4_specs():
    """Codex r4 の入力（#2 注釈6件・#3 画像つき KPI の2行見出し）。"""
    ann = {"type": "annotate", "headline": "短い見出し", "image": {"prompt": "図"},
           "items": [{"x": 10 + i * 15, "y": 50, "title": "項" * 12, "body": "説" * 10} for i in range(6)]}
    kpi = {"type": "kpi", "headline": "先行した3部署の4週目は、会議の時間と長さと出なくてよかった割合がそろって下がった",
           "image": {"prompt": "現場"}, "items": [
               {"label": "1人あたり週の会議の時間（先行3部署）", "value": "2.8", "unit": "時間", "delta": "前は6.0", "note": "予定表から集計"},
               {"label": "会議1回の平均の長さ（先行3部署）", "value": "26", "unit": "分", "delta": "前は48分", "note": "予定表から集計"},
               {"label": "出なくてもよかった会議の割合", "value": "21", "unit": "%", "delta": "前は58%", "note": "アンケート"}]}
    return ann, kpi


def case_r4_annotate_kpi_fit():
    """Codex r4 #2・#3: 注釈6件と、画像つき KPI の2行見出しでも、件数と高さから字を縮めて入れる。"""
    ann, kpi = _r4_specs()
    html = build(one(ann, [""] * 7), "r4-ann", placeholder=True)[0].read_text(encoding="utf-8")
    sizes = [int(x) for x in re.findall(r'class="li-title" style="font-size:(\d+)px', html)]
    assert len(sizes) == 6 and max(sizes) < 36, sizes
    html = build(one(kpi, ["", ""]), "r4-kpi", placeholder=True)[0].read_text(encoding="utf-8")
    labels = [int(x) for x in re.findall(r'class="kpi-label" style="font-size:(\d+)px', html)]
    assert labels and max(labels) < 28, labels


def _kpi3(items, **kw):
    return {"type": "kpi", "headline": "h", "items": items, **kw}


def _kpi_html(spec, name, **kw):
    kw.setdefault("style", "classic")     # 箱の高さを中身に沿わせる前の既定。standard は fill: stretch で高さを伸ばす（case_look_new_values）
    return build(one(spec, ["", ""]), name, **kw)[0].read_text(encoding="utf-8")


def case_kpi_tone_group():
    """kpi の増減の札（tone）・カードの種類分け（group）・3枚の高さ: 既定の対応・上書き・誤りは rc=2・group なしの見た目は今のまま。"""
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    base = [{"label": "a", "value": "1", "delta": "−4", "trend": "down"}, {"label": "b", "value": "2", "delta": "+3", "trend": "up"},
            {"label": "c", "value": "3", "delta": "±0", "trend": "flat"}]
    html = _kpi_html(_kpi3(base), "kt-default")
    assert re.findall(r'class="kpi-delta tone-(\w+)"', html) == ["bad", "good", "neutral"], "trend から tone が決まる"
    assert ">▼ −4<" in html and ">▲ +3<" in html and ">→ ±0<" in html, "記号は trend のまま"
    over = [dict(base[0], tone="good"), dict(base[1], tone="bad"), dict(base[2], tone="bad")]
    html = _kpi_html(_kpi3(over), "kt-over")
    assert re.findall(r'class="kpi-delta tone-(\w+)"', html) == ["good", "bad", "bad"], "tone が trend に勝つ"
    assert ">▼ −4<" in html and ">▲ +3<" in html, "tone を変えても記号は変わらない"
    html = _kpi_html(_kpi3([{"label": "a", "value": "1", "delta": "x"}, {"label": "b", "value": "2", "tone": "bad"}, {"label": "c", "value": "3"}]), "kt-none")
    assert re.findall(r'class="kpi-delta tone-(\w+)"', html) == ["neutral"] and "tone-bad" not in html.split("</style>")[1], "trend なしは neutral・delta なしは札なし"
    for v in ("warn", "red", 3, ["good"]):
        expect_error(one(_kpi3([dict(base[0], tone=v)] + base[1:])), "kt-bad-tone", "items の 1 個目", "tone は good / bad / neutral")
    for v in ("rise", [], {}, ["up"], {"up": 1}, True, 3):         # 型の違う trend も例外を漏らさず、位置付きの DeckError（rc=2）
        expect_error(one(_kpi3([dict(base[0], trend=v)] + base[1:])), "kt-bad-trend", "items の 1 個目", "trend は up / down / flat")
    for v in ([], {}, True, 3):
        expect_error(one(_kpi3([dict(base[0], tone=v)] + base[1:])), "kt-bad-tone-type", "items の 1 個目", "tone は good / bad / neutral")
    expect_error(one(_kpi3([dict(base[0], group={"label": "自動", "tone": []})] + base[1:])), "kt-bad-gtone-type", "items の 1 個目", "group.tone は a / b / c")
    expect_error(one(_kpi3([dict(base[0], group={"label": "自動", "tone": {}})] + base[1:])), "kt-bad-gtone-type2", "items の 1 個目", "group.tone は a / b / c")
    # 3枚はカードが使える高さいっぱい・中身は縦の中央。4枚は中身の高さのまま
    html = _kpi_html(_kpi3(base), "kt-fill")
    hs = re.findall(r'class="kpi" style="width:\d+px; height:(\d+)px; justify-content:center"', html)
    assert len(hs) == 3 and len(set(hs)) == 1 and int(hs[0]) > 600, hs
    html = _kpi_html(_kpi3(base + [{"label": "d", "value": "4"}]), "kt-four")
    assert "justify-content:center" not in html.split("</style>")[1] and 'class="kpi" style="width:' in html
    # group: 札は必ず出る・同じ group は同じ面と札・書かないカードは今のまま
    g = lambda lab, tn: {"label": lab, "tone": tn}
    items = [{"label": "a", "value": "1", "group": g("自動", "a")}, {"label": "b", "value": "2", "group": g("自動", "a")},
             {"label": "c", "value": "3", "group": g("手作業", "b")}, {"label": "d", "value": "4"}]
    html = _kpi_html(_kpi3(items), "kt-group")
    body = html.split("</style>")[1]
    assert re.findall(r'class="kpi( group-\w)?"', body) == [" group-a", " group-a", " group-b", ""], "group を書かないカードは .kpi のまま"
    assert body.count('class="chip group group-a">自動<') == 2 and body.count('class="chip group group-b">手作業<') == 1
    assert 'class="chip' not in body.split('class="kpi" style=')[1], "group のないカードに札は付かない"
    bad = lambda it, *words: expect_error(one(_kpi3([it] + base[1:])), "kt-bad-group", "items の 1 個目", *words)
    row = {"label": "a", "value": "1"}
    bad(dict(row, group="自動"), "group は label と tone のオブジェクト")
    bad(dict(row, group={"label": "自動"}), "group は label と tone のオブジェクト")
    bad(dict(row, group={"label": "自動", "tone": "a", "x": 1}), "group は label と tone のオブジェクト")
    bad(dict(row, group={"label": "", "tone": "a"}), "group.label は空でない")
    bad(dict(row, group={"label": 3, "tone": "a"}), "group.label は空でない")
    bad(dict(row, group={"label": "自動", "tone": "d"}), "group.tone は a / b / c のどれか")
    bad(dict(row, group={"label": "とても長い札の語です", "tone": "a"}), "group.label は短く")
    bad(dict(row, group={"label": "自動", "tone": "a"}, accent=True), "accent と group は同時に書けない")
    # 強める指標と group のあるカードが並んでもよい（同じカードでなければ）
    _kpi_html(_kpi3([dict(row, accent=True), dict(row, group=g("自動", "a")), dict(row)]), "kt-mix")
    # 共有検査: 要約の帯（accent を許さない）でも同じ位置づけで検査する
    s = T.Slide(1, {"summary": [{"label": "a", "value": "1", "accent": True}]}, pal, False, TMP, [])
    try:
        T._kpi_items(s, "summary", lo=1, hi=4, allow_accent=False)
        raise AssertionError("allow_accent=False で accent が通った")
    except T.DeckError as e:
        assert "summary の 1 個目に知らないキー: accent" in str(e), e
    s = T.Slide(1, {"summary": [{"label": "a", "value": "1", "tone": "bad", "group": g("自動", "c")}]}, pal, False, TMP, [])
    assert T._kpi_items(s, "summary", lo=1, hi=4, allow_accent=False)[0]["tone"] == "bad"


def case_kpi_role_sizes():
    """kpi: 同じ役（label・value・unit・delta・note）の文字は、長短を混ぜても全部のカードで同じ大きさ。高さは上限を守る。"""
    long = {"label": "a" * 10, "value": "1", "unit": "件", "delta": "B" * 14, "trend": "up", "note": "注" * 34}      # 3枚では delta・note が元の大きさから下りる
    long4 = dict(long, delta="B" * 8, note="注" * 20)                                                                # 4枚でも下りる長さ
    short = {"label": "b", "value": "2", "unit": "件", "delta": "+1", "trend": "up", "note": "短い"}
    for name, items in (("kr-three", [long, short, dict(short)]), ("kr-four", [long4, short, dict(short), dict(short)])):
        html = _kpi_html(_kpi3(items), name)
        body = html.split("</style>")[1]
        for role in ("label", "value", "unit", "delta", "note"):
            sizes = re.findall(rf'class="kpi-{role}[^"]*" style="font-size:(\d+)px', body)
            assert len(sizes) == len(items) and len(set(sizes)) == 1, (name, role, sizes)
            if role in ("delta", "note"):
                assert int(sizes[0]) < (36 if role == "delta" else 28), (name, role, sizes, "長い方に合わせて下りている")
        hs = [float(x) for x in re.findall(r'class="kpi" style="width:\d+px; height:(\d+)px', body)]
        assert all(h <= T.BODY_BOTTOM for h in hs), hs
    # 共通の組版を直接呼んでも、role_prefix 付きの役が s.seen に残り、高さ（内容）は上限以内
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    s = T.Slide(1, {}, pal, False, TMP, [])
    tiles, tallest = T._kpi_tiles(s, [long, short, dict(short)], key="summary", total_width=T.CW, gap=T.GUTTER, vmax=96, drop=0, height_cap=900,
                                  pad=T.CALLOUT_PAD, role_prefix="sum")
    assert {"sum-label", "sum-value", "sum-delta", "sum-note"} <= set(s.seen), s.seen
    # 最も高いのが通常カードで、強調カードが同じ行にあるとき: 枠の差（4px − 2px）の上余白も返す内容高に入る（余白なしの組みより 2px 高い）
    def tall_of(accent):
        s2 = T.Slide(1, {}, pal, False, TMP, [])
        its = [{"label": "a", "value": "1", **({"accent": True} if accent else {"group": {"label": "x", "tone": "a"}})}, {"label": "b", "value": "2", "note": "note"}, {"label": "c", "value": "3"}]
        return T._kpi_tiles(s2, its, key="items", total_width=T.CW, gap=T.GUTTER, vmax=96, drop=0, height_cap=900, pad=T.CALLOUT_PAD)[1]
    assert tall_of(True) == tall_of(False) + (4 - T.BORDER), (tall_of(True), tall_of(False))
    assert tallest <= 900 and len(tiles) == 3
    s.caps = {k: min(v) for k, v in s.seen.items()}               # build_slide が組み直すのと同じ。そろえた後の高さは上の組みより低いか同じ
    s.seen = {}
    tiles2, tallest2 = T._kpi_tiles(s, [long, short, dict(short)], key="summary", total_width=T.CW, gap=T.GUTTER, vmax=96, drop=0, height_cap=900,
                                    pad=T.CALLOUT_PAD, role_prefix="sum")
    assert tallest2 <= tallest and all(len(set(v)) == 1 for v in s.seen.values()), s.seen


def case_group_c_fixed():
    """group の a・b・c は、テーマを変えても同じ（c はテーマの主色ではなく固定の青）。a/b/c は重ならず、文字 4.5:1・枠 3:1。"""
    th = deck.load_theme(deck.THEMES / "default.yaml")
    themes = [deck.load_theme(deck.THEMES / f"{n}.yaml") for n in ("default", "forest", "warm")] + \
             [th | {"primary": c} for c in ("#197a4b", "#c54600", "#264af4")]          # 主色が緑・橙・青のテーマ
    cs = set()
    for i, tv in enumerate(themes):
        p = deck.palette(tv)
        assert deck.contrast_problems(p) == [], (i, deck.contrast_problems(p))
        for role in ("bg", "text", "rule"):
            vals = [p[f"group-{g}-{role}"] for g in "abc"]
            assert len(set(vals)) == 3, (i, role, vals)                # a/b/c の組が重ならない
        for g in "abc":
            assert deck.ratio(p[f"group-{g}-text"], p[f"group-{g}-bg"]) >= 4.5 and deck.ratio(p[f"group-{g}-rule"], p[f"group-{g}-bg"]) >= 3.0, (i, g)
        if tv.get("background", "#ffffff") == "#ffffff":
            cs.add(tuple(p[f"group-c-{r}"] for r in ("bg", "text", "rule")))
        # c は青系（b が R より大きい）で、a（緑）・b（橙）と色相が離れる
        r, g_, b = (int(p["group-c-rule"][k:k + 2], 16) for k in (1, 3, 5))
        assert b > r and b > g_, (i, p["group-c-rule"])
    assert len(cs) == 1, f"白い地なら、テーマを変えても a/b/c の対応は変わらない: {cs}"


def case_kpi_tone_chip_strong():
    """強い look（emphasis: strong）と全テーマ・主色違いの組み合わせでも、増減の札の文字 × 札の地は 4.5:1（札は自前の地を持つ）。"""
    th = deck.load_theme(deck.THEMES / "default.yaml")
    st = _style("tone-strong", "name: x\nlook: {emphasis: strong, surface: line}\n")
    kpi = _kpi3([{"label": "a", "value": "1", "accent": True, "delta": "+1", "trend": "up"},
                 {"label": "b", "value": "2", "delta": "−1", "trend": "down"}, {"label": "c", "value": "3", "delta": "±0", "trend": "flat"}])
    variants = [(n, deck.THEMES / f"{n}.yaml") for n in ("default", "forest", "warm")]
    for name, color in (("green", "#197a4b"), ("orange", "#c54600"), ("blue", "#264af4"), ("red", "#e60012")):
        f = TMP / f"tone-strong-{name}.yaml"
        f.write_text(f'background: "#ffffff"\nink: "#333333"\nprimary: "{color}"\n', encoding="utf-8")
        variants.append((name, f))
    for name, f in variants:
        html = _kpi_html(kpi, f"kt-strong-{name}", theme=str(f), style=st)
        root = {m.group(1): m.group(2) for m in re.finditer(r"--([\w-]+):\s*(#[0-9a-f]{6})", html.split("</style>")[0])}
        assert "kpi hl" in html and re.findall(r'class="kpi-delta tone-(\w+)"', html) == ["good", "bad", "neutral"], name
        for tone in ("good", "bad", "neutral"):
            r = deck.ratio(root[f"tone-{tone}-text"], root[f"tone-{tone}-bg"])
            assert r >= 4.5, (name, tone, r)
        assert ".kpi.hl div:where(:not(.kpi-delta))" in html, "強める箱の文字色の一括指定から増減の札を外している"


def case_kpi_look_priority():
    """look（emphasis: strong・surface: line）でも、増減の札の色と group のカードの面は look に潰されない。"""
    import deck_style as DS
    css = DS.look_css({"emphasis": "strong", "surface": "line"})
    assert ".kpi.hl div:where(:not(.kpi-delta))" in css, "強める箱の文字色の一括指定から増減の札を外す"
    tpl = deck.TEMPLATE.read_text(encoding="utf-8")
    sty = tpl.split("<style>")[1].split("</style>")[0]
    for tone in ("good", "bad", "neutral"):
        assert f".kpi-delta.tone-{tone} {{ background-color: var(--tone-{tone}-bg); color: var(--tone-{tone}-text); border-color: var(--tone-{tone}-text); }}" in sty
    for g in "abc":
        assert f".kpi.group-{g} {{ background-color: var(--group-{g}-bg); border-color: var(--group-{g}-rule); }}" in sty
    kpi = _kpi3([{"label": "a", "value": "1", "accent": True, "delta": "+1", "trend": "up"}, {"label": "b", "value": "2", "group": {"label": "自動", "tone": "a"}},
                 {"label": "c", "value": "3"}])
    html = _kpi_html(kpi, "kt-look", style="visual")
    assert "kpi-delta tone-good" in html and "group-a" in html


def case_kpi_old_scripts_same():
    """tone・group を書かない旧台本: 増減の札とカードの高さ以外（面・文字の大きさ・順・段の数）は変わらない。4枚の高さも今のまま。"""
    kpi = _kpi3([{"label": "a", "value": "1", "unit": "件", "note": "n"}, {"label": "b", "value": "2"}, {"label": "c", "value": "3"}, {"label": "d", "value": "4"}])
    html = _kpi_html(kpi, "kt-old4")
    body = html.split("</style>")[1]
    assert len(re.findall(r'class="kpi" style="width:\d+px" data-step="1"', body)) == 4, "4枚は高さを固定しない・1クリック"
    assert "tone-" not in body and "group" not in body and "chip" not in body, "何も書かなければ札も色も付かない"
    assert 'class="kpi ref' not in body and "chip ref" not in body, "reference を書かなければ参考の見た目は付かない"


def case_kpi_reference():
    """kpi・table summary の reference: 参考の見た目（class・札・灰の増減）・誤りは rc=2・混ぜても数字の上端がそろう・全部が参考でもよい・色の検査。"""
    ref = {"label": "手作業", "value": "180", "unit": "件", "delta": "+9%", "trend": "up", "tone": "bad", "reference": True}
    main = [{"label": "自動", "value": "1,200", "delta": "+35%", "trend": "up"}, {"label": "時間", "value": "4", "delta": "-60%", "trend": "down", "tone": "good"}]
    body = _kpi_html(_kpi3(main + [ref]), "kref-mix").split("</style>")[1]
    assert re.findall(r'class="kpi( ref)?"', body) == ["", "", " ref"], "参考のカードだけ .ref"
    assert body.count('class="chip ref">参考<') == 1, "札の語は固定"
    assert re.findall(r'class="kpi-delta tone-(\w+)"', body) == ["good", "good", "neutral"], "参考の増減は tone・trend によらず灰"
    assert "▲ +9%" in body, "記号は trend のまま"
    # 札を持たないカードにも同じ高さの空き（数字の上端がそろう）
    tops = re.findall(r'class="kpi[^"]*" style="[^"]*"[^>]*>(?:<div class="chip[^>]*>[^<]*</div>|<div style="height:(\d+)px; flex:none"></div>)', body)
    assert len(tops) == 3 and tops[0] == tops[1] != "", tops
    # 全部が参考でもよい
    allref = [dict(ref, label=x) for x in "abc"]
    body = _kpi_html(_kpi3(allref), "kref-all").split("</style>")[1]
    assert body.count('class="kpi ref"') == 3 and body.count('class="chip ref">参考<') == 3
    # 誤り
    bad = lambda it, *words: expect_error(one(_kpi3([it] + main)), "kref-bad", "items の 1 個目", *words)
    row = {"label": "a", "value": "1"}
    for v in ("true", 1, None, False, [], {}):
        bad(dict(row, reference=v), "reference は true だけ")
    bad(dict(row, reference=True, accent=True), "accent と reference は同時に書けない")
    bad(dict(row, reference=True, group={"label": "自動", "tone": "a"}), "group と reference は同時に書けない")
    # table の summary にも同じ名前・同じ見た目
    tb = {"type": "table", "headline": "h", "columns": ["a", "b"], "rows": [["1", "2"]],
          "summary": [{"label": "x", "value": "1"}, dict(ref, label="y")]}
    html = build(one(tb, ["", "", ""]), "kref-sum")[0].read_text(encoding="utf-8")
    body = html.split("</style>")[1]
    assert re.findall(r'class="kpi( ref)?"', body) == ["", " ref"] and body.count('class="chip ref">参考<') == 1
    assert re.findall(r'class="kpi-delta tone-(\w+)"', body) == ["neutral"]
    expect_error(one(dict(tb, summary=[{"label": "x", "value": "1", "reference": True, "accent": True}])), "kref-sum-bad", "summary の 1 個目")
    # 色: 全テーマで 文字 4.5:1・枠 3:1・札の枠 3:1（contrast_problems が見る）。壊せば拾う
    for n in ("default", "forest", "warm"):
        pal = deck.palette(deck.load_theme(deck.THEMES / f"{n}.yaml"))
        assert deck.contrast_problems(pal) == [], n
        assert deck.ratio(pal["ref-text"], pal["ref-bg"]) >= 4.5 and deck.ratio(pal["ref-rule"], pal["ref-bg"]) >= 3.0, n
        assert pal["ref-text"] != pal["ink"], "文字は ink より一段薄い"
        assert deck.contrast_problems(dict(pal, **{"ref-text": pal["ref-bg"]})), "壊せば contrast_problems が拾う"


def case_r4_line_breaks_everywhere():
    """Codex r4 #4・#5: 改行は KPI の label・連絡先の名前などでも <br> になる。強調は改行をまたげる。属性とノートは改行のまま。"""
    assert T.rich("**1行目\n2行目**") == "<em>1行目<br>2行目</em>"
    sc = {"title": "t", "slides": [
        {"type": "kpi", "headline": "h", "items": [{"label": "国内\n売上", "value": "1"}, {"label": "b", "value": "2"}, {"label": "c", "value": "3"}],
         "talk": ["一行目\n二行目", ""]},
        {"type": "closing", "headline": "h", "contact": {"name": "経営企画部\n会議改善チーム", "lines": ["内線"]}}]}
    path, _ = build(sc, "r4-br")
    html = path.read_text(encoding="utf-8")
    assert ">国内<br>売上<" in html and ">経営企画部<br>会議改善チーム<" in html
    body = html[html.index("<body>"):]
    assert '<aside class="notes">一行目\n二行目［クリック］</aside>' in body, "ノートは改行のまま"
    screen = re.sub(r'<aside class="notes">.*?</aside>', "", body, flags=re.S)
    raw = [seg for seg in re.findall(r">([^<>]*)<", screen) if "\n" in seg.strip()]
    assert not raw, f"画面の文字に生の改行が残っている: {raw}"


def case_r4_closing_image_guards():
    """Codex r4 #1・#6: 写真つきの締めで、連絡先が見出しに届いたら rc=2。出典は左の列の幅で置き、測る。"""
    d = _img_dir("r4-closing")
    shutil.copy(SAMPLE.parent / "images" / "s16-image.png", d / "images" / "s01-image.png")
    long_contact = {"type": "closing", "headline": "開始\n連絡", "image": {"prompt": "窓"},
                    "contact": {"name": "窓口", "lines": [f"連絡先 {i}" for i in range(12)]}}
    try:
        _build_in(d, one(long_contact))
        raise AssertionError("見出しに重なる連絡先が通った")
    except T.DeckError as e:
        assert "見出しに重なる" in str(e), str(e)
    ok = {"type": "closing", "headline": "次の一手", "image": {"prompt": "窓"}, "items": ["承認"],
          "contact": {"name": "窓口", "lines": ["内線"]}, "source": "出典: 社内の資料（架空）"}
    html = _build_in(d, one(ok))[0].read_text(encoding="utf-8")
    left_w = T.W - T.CLOSING_IMG_W - T.X0 - 96
    assert f'class="source" style="top:{T.BODY_BOTTOM + 8}px; width:{left_w}px;' in html
    try:
        _build_in(d, one(ok | {"source": "出典：" + "あ" * 60}))
        raise AssertionError("写真に届く出典が通った")
    except T.DeckError as e:
        assert "source" in str(e), str(e)



# --- スタイル（ADR 0005） ----------------------------------------------------------

def _style(name: str, body: str) -> str:
    path = TMP / f"style-{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


def case_style_validation():
    """スタイルの YAML の誤りは DeckError（rc=2）。組み込み・雛形は読める。"""
    import deck_style as DS
    assert DS.load(None)["name"] == "standard" and DS.load("visual")["name"] == "visual"
    assert DS.load(str(SKILL / "templates" / "custom-style.example.yaml"))["name"] == "my-style"
    sc = {"title": "t", "slides": [{"type": "cover", "title": "t"}]}
    bad = [
        ("name: x\nfeel: y\n", "知らないキー"),
        ("description: x\n", "name が無い"),
        ("name: x\ndefaults: {nazo: {layout: hero}}\n", "知らない型"),
        ("name: x\ndefaults: {table: {layout: hero}}\n", "変化形を持たない型"),
        ("name: x\ndefaults: {cover: {split: 1/2}}\n", "変化形 'split' は無い"),
        ("name: x\ndefaults: {cover: {layout: full}}\n", "split / hero のどれか"),
        ("name: x\ndefaults: {compare: {arrow: 'no'}}\n", "True / False のどれか"),
        ("name: x\nmotion: {fx: zoom}\n", "motion.fx は"),
        ("name: x\nmotion: {transition: slide}\n", "motion.transition は"),
        ("name: x\nmotion: {speed: 2}\n", "fx と transition だけ"),
        ("name: x\ndefaults: false\n", "defaults は"),                       # Codex レビュー: false を空として通さない
        ("name: x\ndefaults: 0\n", "defaults は"),
        ("name: x\nmotion: []\n", "fx と transition だけ"),
        ("name: x\n1: a\nother: b\n", "知らないキー"),                       # 文字でないキーでも例外で落ちない
    ]
    for i, (body, want) in enumerate(bad):
        try:
            build(sc, f"style-bad-{i}", style=_style(f"bad{i}", body))
            raise AssertionError(f"通ってしまった: {body!r}")
        except T.DeckError as e:
            assert want in str(e), f"{body!r}: {e}"
    for spec, want in (("nazo", "スタイルが無い"), (str(TMP / "none.yaml"), "ファイルが無い")):
        try:
            build(sc, "style-missing", style=spec)
            raise AssertionError(spec)
        except T.DeckError as e:
            assert want in str(e), e


def case_style_apply():
    """visual の既定: 画像のあるページだけ hero・台本が勝つ・split は left/right のときだけ・collage は3枚だけ・動きの既定。
    画像のある引用に layout が無いと standard では止まる（今までどおり）。visual は full を足す。"""
    img = "assets/meeting.png"
    sc = {"title": "t", "slides": [
        {"type": "cover", "title": "表紙", "image": img},                              # → hero
        {"type": "section", "title": "章"},                                              # 画像なし → split のまま
        {"type": "photo", "headline": "写真", "image": img, "layout": "left"},           # 台本の left が勝つ
        {"type": "quote", "quote": "言葉"},                                               # 画像なし → plain のまま
        {"type": "quote", "quote": "言葉", "image": img, "layout": "full"},              # （standard で止まらないよう台本に書く）
        {"type": "gallery", "headline": "g", "items": [{"image": img}, {"image": img}]},  # 2枚 → row のまま
        {"type": "cards", "headline": "c", "transition": "fade",
         "items": [{"title": "a"}, {"title": "b"}], "talk": ["", "", ""]},
    ]}
    _, info = build(sc, "style-visual", style="visual")
    st = {x["no"]: x["styled"] for x in info["slides"]}
    tr = {x["no"]: x["transition"] for x in info["slides"]}
    assert info["style"] == "visual"
    assert st[1].get("layout") == "hero" and "transition" not in st[1], st[1]
    assert "layout" not in st[2], f"画像の無い章扉に hero を当てた: {st[2]}"
    assert "layout" not in st[3] and "split" not in st[3], f"台本の layout を上書きした: {st[3]}"
    assert "layout" not in st[4] and "layout" not in st[5], (st[4], st[5])
    assert "layout" not in st[6], f"2枚のギャラリーに collage を当てた: {st[6]}"
    assert all(st[n].get("fx") == "fade" for n in st), "fx の既定が全ページに入っていない"
    assert tr[1] == "fade" and tr[2] == "fade" and tr[7] == "fade", f"切り替え: {tr}"
    _, info = build(sc, "style-visual-handout", style="visual", mode="handout")
    assert not any("transition" in x["styled"] for x in info["slides"]), "handout に切り替えの既定を当てた"
    q = {"title": "t", "slides": [{"type": "quote", "quote": "言葉", "image": img}]}
    _, info = build(q, "style-visual-quote", style="visual")
    assert info["slides"][0]["styled"].get("layout") == "full", info["slides"][0]
    _, info = build(sc, "style-standard", style="standard")
    st = {x["no"]: x["styled"] for x in info["slides"]}
    assert not any(st.values()), f"standard が何かを足した: {st}"


def case_style_standard_keeps_samples():
    """standard（既定）は今までの見本の台本に何も足さない（今までの見た目のまま）。"""
    for script in (SAMPLE, IMAGE_SAMPLE):
        _, info = deck.build_html(script, TMP / f"std-{script.stem}", style="standard", placeholder=True)
        added = {x["no"]: x["styled"] for x in info["slides"] if x["styled"]}
        assert not added, f"{script.name}: standard が足した: {added}"



def case_style_look():
    """見た目の調子（look）: 誤りは rc=2・visual の CSS が入る・standard は空・地の色を変えても読みやすさの検査を通る。"""
    import deck_style as DS
    sc = {"title": "t", "slides": [{"type": "statement", "headline": "主張"},
                                   {"type": "cards", "headline": "h", "steps": False, "talk": ["x"],
                                    "items": [{"title": "a"}, {"title": "b", "accent": True}]}]}
    for i, (body, want) in enumerate([("name: x\nlook: {shadow: big}\n", "look に 'shadow' は無い"),
                                      ("name: x\nlook: {surface: glass}\n", "look.surface は"),
                                      ("name: x\nlook: [a]\n", "look は")]):
        try:
            build(sc, f"look-bad-{i}", style=_style(f"lookbad{i}", body))
            raise AssertionError(body)
        except T.DeckError as e:
            assert want in str(e), e
    html, _ = build(sc, "look-visual", style="visual")
    h = pathlib.Path(html).read_text(encoding="utf-8")
    for want in (".card, .cmp, .kpi, .callout { background-color: var(--bg); border: 2px solid var(--rule); }", ".band { left: 0; width: 1920px", 'section[data-type="statement"] { background-color: var(--primary-strong)',
                 ".card.hl, .cmp.hl, .kpi.hl { background-color: var(--accent-strong)", "border-radius: 32px",
                 ".kpi.hl .kpi-value, .kpi.hl .kpi-unit { color: var(--on-accent); }"):
        assert want in h, f"visual の CSS に {want!r} が無い"
    html, _ = build(sc, "look-standard", style="standard")
    assert "box-shadow" not in pathlib.Path(html).read_text(encoding="utf-8").split("{{LOOK}}")[0].split("/* スタイルの見た目の調子")[1], "standard に look の CSS が入った"
    # look.surface: white（白に影）は退役。受け付けるが、line（白に枠線）と同じに描き、影は出さない（ADR 0020）
    assert DS.look_css({"surface": "white"}) == DS.look_css({"surface": "line"}) and "shadow" not in DS.look_css({"surface": "white"}), "white が line と同じでない"
    assert DS.look_css({}) == "", "look が空なら CSS も空"
    t = DS.look_theme({"background": "#ffffff", "ink": "#1a2b4c", "primary": "#1f5fd1"}, {"background": "tint"})
    assert t["background"] != "#ffffff", "tint で地の色が変わっていない"


def case_look_radius_boxes():
    """look.radius は notice の枠・式の答えの箱・NG／OK の箱にも効く（#58）: square 4px・round 32px・書かなければ今のまま。
    notice は地を kind の色にして、白い面（nt-main）を枠の内側の角で丸める。帯は幅だけ持つので、角から出ない。"""
    import deck_style as DS
    for r, px_ in (("square", 4), ("round", 32)):
        css = DS.look_css({"radius": r})
        assert ".notice, .eq-res, .pair-box { border-radius: %dpx; }" % px_ in css, (r, css)
        assert ".nt-main { border-radius: %dpx; }" % (px_ - 2) in css, (r, css)
    assert DS.look_css({"radius": "default"}) == "" and DS.look_css({}) == "", "書かなければ CSS を足さない"
    # 地・帯は kind の色。帯そのものは色を持たない（持つと角から出る）
    nt = {"type": "notice", "headline": "h", "items": [{"kind": k, "title": "x"} for k in T.NOTICE_KINDS]}
    s = _secs(build(one(nt), "radius-nt")[0])[0]
    for kind, (_, _, line, _) in T.NOTICE_KINDS.items():
        assert f'class="notice" style="width:1680px; border-color:{line}; background-color:{line}"' in s, kind
    assert '<div class="nt-band"></div>' in s
    # radius を書かないスタイル（standard）では、notice・equation・pairs の CSS に角の指定が増えない。visual は round なので丸くなる
    scs = {"notice": nt, "equation": {"type": "equation", "headline": "h", "items": [{"title": "A"}, {"title": "B"}], "result": {"title": "C"}},
           "pairs": {"type": "pairs", "headline": "h", "items": [{"label": "a", "before": "x", "after": "y"}] * 2}}
    for name, sl in scs.items():
        std = pathlib.Path(build(one(sl), f"radius-std-{name}", style="standard")[0]).read_text(encoding="utf-8")
        vis = pathlib.Path(build(one(sl), f"radius-vis-{name}", style="visual")[0]).read_text(encoding="utf-8")
        assert ".notice, .eq-res, .pair-box {" not in std and ".nt-main { border-radius" not in std, f"standard の {name} に角の指定が入った"
        assert ".notice, .eq-res, .pair-box { border-radius: 32px; }" in vis, f"visual の {name} が丸くならない"


def case_theme_primary_accent():
    """テーマの主役の色（primary）と差し色（accent）: 骨格は primary・強調は accent・古い書き方（accent だけ）は rc=2。"""
    two = TMP / "two.yaml"
    two.write_text('background: "#ffffff"\nink: "#1a1a1a"\nprimary: "#f5a623"\naccent: "#399dd1"\n', encoding="utf-8")
    p = deck.palette(deck.load_theme(two))
    assert p["primary"] != p["accent"] and p["primary-mark"] != p["accent-mark"], "primary と accent が同じ色になった"
    assert deck.ratio(p["primary-text"], "#ffffff") >= 4.5 and deck.ratio(p["primary-mark"], "#ffffff") >= 3.0, "明るい黄色は、同じ色相のまま暗くして 4.5:1・3:1 にする"
    assert p["on-primary"] == "#ffffff" and deck.ratio(p["on-primary"], p["primary-strong"]) >= 4.5, "暗くした黄色の面が白抜きになっていない"
    assert deck.ratio(p["accent-text"], "#ffffff") >= 4.5 and deck.ratio(p["on-accent"], p["accent-strong"]) >= 4.5, p
    assert p["on-accent"] == "#ffffff" and p["accent-strong"] != "#399dd1", "中くらいの青の面が白抜きになっていない（少し暗くして白）"
    assert deck.contrast_problems(p) == [], deck.contrast_problems(p)
    dflt = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    assert dflt["on-primary"] == "#ffffff", "既定のシアンの面が白抜きになっていない"
    sc = {"title": "t", "slides": [
        {"type": "cards", "headline": "会議を*半分*にする", "items": [{"title": "a"}, {"title": "b", "accent": True}]}]}
    html, _ = build(sc, "theme-two", theme=str(two), style="visual")
    h = pathlib.Path(html).read_text(encoding="utf-8")
    for want in (f"--primary: {p['primary']}", f"--accent: {p['accent']}", "em { font-style: normal; color: var(--accent-text); }",
                 "background-color: var(--primary-decor); }", ".card.hl, .cmp.hl, .kpi.hl { background-color: var(--accent-strong)"):
        assert want in h, f"{want!r} が無い"
    old = TMP / "old.yaml"
    old.write_text('background: "#ffffff"\nink: "#1a1a1a"\naccent: "#f5a623"\n', encoding="utf-8")
    try:
        deck.load_theme(old)
        raise AssertionError("古い書き方（accent だけ）が通った")
    except T.DeckError as e:
        assert "primary に名前が変わった" in str(e)
    bad = TMP / "two-bad.yaml"
    bad.write_text('background: "#ffffff"\nink: "#1a1a1a"\nprimary: "#f5a623"\naccent: "blue"\n', encoding="utf-8")
    try:
        deck.load_theme(bad)
        raise AssertionError("色でない accent が通った")
    except T.DeckError as e:
        assert "#rrggbb" in str(e)


def _logo_png(name, size, box):
    """透明な地に、box（left, top, right, bottom）だけ塗ったロゴの見本。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=(20, 20, 20, 255))
    path = TMP / name
    im.save(path)
    return path


def case_logo_geometry():
    """ロゴの大きさ: 329×44 の枠に縦横比を保って収める・引き伸ばさない・透明な余白は切る・小さければ警告・PNG/JPEG 以外は rc=2。"""
    g = deck.logo_geometry(444, 59)                          # 横長: 幅で決まる
    assert (g["w"], g["h"]) == (329, 44) and g["x"] == 1920 - 120 - 329, g
    g = deck.logo_geometry(370, 100)                         # 高さで決まる
    assert g["h"] == 44 and g["w"] == 163 and g["warning"] is None, g
    g = deck.logo_geometry(100, 20)                          # 枠より小さい原本は原寸のまま
    assert (g["w"], g["h"], g["scale"]) == (100, 20, 1.0) and "原寸" in g["warning"], g
    g = deck.logo_geometry(200, 600)                         # 縦長は小さくなる（拒否はしない）
    assert g["h"] == 44 and g["w"] == 15 and "収めた" in g["warning"], g
    p = _logo_png("padded.png", (595, 365), (113, 132, 483, 232))
    path, geo = deck.load_logo(p)
    assert geo["trim"] == [113, 132, 483, 232] and (geo["w"], geo["h"]) == (163, 44), geo
    for bad, word in ((TMP / "nope.png", "無い"), (TMP / "padded.json", "PNG か JPEG")):
        if word != "無い":
            bad.write_text("{}", encoding="utf-8")
        try:
            deck.load_logo(bad)
            raise AssertionError(f"{bad.name} が通った")
        except T.DeckError as e:
            assert word in str(e), e


def case_logo_pages():
    """ロゴのページごとの扱い（Chrome で測る）: 右下 → 左下の順に、写真・絵・文字がかからない所に置く（両方だめなら置かない）・
    地はその場所の地の色・
    ページ番号はロゴの左（置かないページは元の位置）・PPTX の部品名は !!logo と !!logo-plate。"""
    logo = _logo_png("wide.png", (444, 59), (0, 0, 444, 59))
    html, info = deck.build_html(SAMPLE, TMP / "logo-visual", style="visual", logo=str(logo))
    pages = {pg["no"]: pg for pg in info["logo"]["pages"]}
    off = sorted(n for n, pg in pages.items() if pg.get("off"))
    assert off == [1, 9, 15], f"ロゴを置かないページ {off}（表紙・章扉の絵・全面の写真）"
    th = deck.DS.look_theme(deck.load_theme(deck.THEMES / "default.yaml"), deck.DS.load("visual")["look"])
    assert pages[25]["plate"] == deck.palette(th)["primary-strong"], f"主張の全面の地の色になっていない: {pages[25]}"
    assert pages[2]["plate"] == th["background"], pages[2]
    h = pathlib.Path(html).read_text(encoding="utf-8")
    secs = h.split("<section data-slide")[1:]
    assert 'class="logo"' not in secs[0] and 'class="logo"' in secs[1]
    assert re.search(r'data-name="ページ番号">2<', secs[1]), "ページ番号が中央下（動かさない）でない"
    assert re.search(r'class="footer"[^>]*style="width: 792px;"', secs[1]), "下の文字が中央下の番号の手前で止まっていない"
    assert (TMP / "logo-visual" / "assets" / "logo.png").is_file()
    _, info = deck.build_html(SAMPLE, TMP / "logo-standard", logo=str(logo))      # 右半分が絵の表紙・章扉は左下へ
    pages = {pg["no"]: pg for pg in info["logo"]["pages"]}
    assert [(pages[n].get("corner"), pages[n].get("x")) for n in (1, 9)] == [("bottom-left", 160), ("bottom-left", 120)], \
        f"右下がふさがるページが、文字の左端にそろえた左下に移っていない: {pages[1]} {pages[9]}"
    assert pages[15].get("off") and pages[2]["corner"] == "bottom-right", (pages[15], pages[2])
    sc = {"title": "t", "slides": [{"type": "cover", "layout": "graphic", "title": "題"},
                                   {"type": "section", "layout": "graphic", "title": "章"}]}
    _, info = build(sc, "logo-graphic", logo=str(logo))                           # 図形の飾りは写真と同じく避ける
    assert [pg.get("corner") for pg in info["logo"]["pages"]] == ["bottom-left", "bottom-left"], info["logo"]["pages"]


def case_logo_contrast():
    """ロゴが地の上で読めるか（Chrome で地の色を測る）: 読めればそのまま・読めなければ白いロゴ（--logo-white）・それも無ければ
    余白つきの地（ステッカー）。判定の値は image-slide と同じ。白いロゴの縦横比違い・--logo なしは rc=2。"""
    L = deck.logo_look
    assert deck.LOGO_CONTRAST == 3.0 and deck.LOGO_STICKER_PAD == 12
    dark, light = 0.012, 1.0                                            # 黒に近いロゴ・白いロゴの明るさ
    assert L(dark, None, "#ffffff") == {"variant": "normal", "plate": "#ffffff", "sticker": False}, "白い地の黒いロゴはそのまま"
    assert L(dark, None, "#162428") == {"variant": "normal", "plate": "#ffffff", "sticker": True}, "暗い地の黒いロゴは白いステッカー"
    assert L(dark, light, "#162428") == {"variant": "white", "plate": "#162428", "sticker": False}, "白いロゴがあれば替える"
    assert L(dark, light, "#ffffff")["variant"] == "normal", "白い地では白いロゴに替えない"
    assert L(light, None, "#ffffff") == {"variant": "normal", "plate": "#000000", "sticker": True}, "白いロゴだけを白い地に置くなら黒いステッカー"
    assert L(dark, None, "#f5a623")["sticker"] is False, "明るい主役の色の面では黒いロゴが読める"
    from PIL import Image, ImageDraw
    def striped(name, color, size=(464, 79), box=(10, 10, 454, 69)):   # すき間が透けるロゴ（縦の帯）。べた塗りの画像は、自分の地を持つロゴの扱いになる
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for x in range(box[0], box[2], 24):
            d.rectangle((x, box[1], min(x + 11, box[2] - 1), box[3] - 1), fill=color)
        d.rectangle((box[2] - 12, box[1], box[2] - 1, box[3] - 1), fill=color)
        im.save(TMP / name)
        return TMP / name
    black, white = striped("lc-black.png", (20, 20, 20, 255)), striped("lc-white.png", (255, 255, 255, 255))
    sc = {"title": "t", "slides": [{"type": "cards", "headline": "白い地", "items": [{"title": "a"}, {"title": "b"}]},
                                   {"type": "closing", "headline": "締め", "items": [{"title": "次の一手"}]}]}
    html, info = build(sc, "lc-a", logo=str(black))                     # 既定のスタイル: 締めはほぼ黒の面
    pg = info["logo"]["pages"]
    assert (pg[0]["variant"], pg[0]["sticker"]) == ("normal", False) and (pg[1]["variant"], pg[1]["sticker"], pg[1]["plate"]) == ("normal", True, "#ffffff"), pg
    h = pathlib.Path(html).read_text(encoding="utf-8").split("<section data-slide")[2]
    m = re.search(r'class="logo-plate"[^>]*style="background-color: #ffffff; left: (\d+)px; top: (\d+)px; width: (\d+)px; height: (\d+)px; border-radius: 8px;"', h)
    lg = info["logo"]
    assert m and [int(v) for v in m.groups()] == [lg["x"] - 12, lg["y"] - 12, lg["w"] + 24, lg["h"] + 24], "ステッカーはロゴの周りに 12px の余白"
    html, info = build(sc, "lc-b", logo=str(black), logo_white=str(white))
    pg = info["logo"]["pages"]
    assert [p["variant"] for p in pg] == ["normal", "white"] and not pg[1]["sticker"], pg
    secs = pathlib.Path(html).read_text(encoding="utf-8").split("<section data-slide")
    assert 'src="assets/logo.png"' in secs[1] and 'src="assets/logo-white.png"' in secs[2] and (TMP / "lc-b" / "assets" / "logo-white.png").is_file()
    # 余白つきの地の帯に文字がかかるページ: 余白をとらず、ロゴと同じ大きさの地にする（覆わない）
    lg = info["logo"]
    near = {"title": "t", "slides": [{"type": "html", "background": "#162428", "html":
            f'<div style="position:absolute; left:{lg["x"] - 300}px; top:{lg["y"]}px; width:{300 - 6}px; height:{lg["h"]}px; font-size:22px; line-height:1.5; color:#ffffff">隣の文字</div>'}]}
    html, info2 = build(near, "lc-near", logo=str(black))
    assert info2["logo"]["pages"][0]["sticker"] is True, info2["logo"]["pages"]
    h = pathlib.Path(html).read_text(encoding="utf-8")
    m = re.search(rf'class="logo-plate"[^>]*style="background-color: #ffffff; left: {lg["x"]}px; top: {lg["y"]}px; width: {lg["w"]}px; height: {lg["h"]}px;([^"]*)"', h)
    assert m and "border-radius" not in m.group(1), "帯に文字がかかるなら余白なし・角も丸めない（ロゴの四隅まで覆う）"
    # 左下で、広げた地がページの外へ出るとき（文字の左端が 0）も余白なし
    edge = {"title": "t", "slides": [{"type": "html", "background": "#162428", "html":
            '<div style="position:absolute; left:0px; top:200px; width:600px; height:48px; font-size:32px; line-height:1.5; color:#ffffff">左端の文字</div>'}]}
    html, info5 = build(edge, "lc-edge", logo=str(black), logo_position="bottom-left")
    pg5 = info5["logo"]["pages"][0]
    assert (pg5["corner"], pg5["x"], pg5["sticker"]) == ("bottom-left", 0, True), pg5
    assert re.search(r'class="logo-plate"[^>]*style="background-color: #ffffff; left: 0px;', pathlib.Path(html).read_text(encoding="utf-8")), "ページの外へ出さない"
    # 縁の 1 画素だけ半透明な、地を塗ったロゴも「自分の地を持つロゴ」（扱いが切り替わらない）
    almost = TMP / "lc-almost.png"
    im = Image.new("RGBA", (444, 59), (20, 20, 20, 255))
    im.putpixel((0, 0), (20, 20, 20, 120))
    im.save(almost)
    _, info6 = build(sc, "lc-almost", logo=str(almost))
    assert [p["sticker"] for p in info6["logo"]["pages"]] == [False, False], info6["logo"]["pages"]
    # 外周 1px だけ半透明（縁をなめらかにした書き出し）でも、解像度によらず「自分の地を持つロゴ」。すき間のあるロゴは透けるロゴ
    for size in ((444, 59), (888, 118)):
        im = Image.new("RGBA", size, (20, 20, 20, 120))
        ImageDraw.Draw(im).rectangle((1, 1, size[0] - 2, size[1] - 2), fill=(20, 20, 20, 255))
        f = TMP / f"lc-rim-{size[0]}.png"
        im.save(f)
        assert deck.load_logo(str(f))[1]["lum"] is None, f"外周 1px の半透明で扱いが変わった: {size}"
    assert deck.load_logo(str(black))[1]["lum"] is not None and deck.load_logo(str(almost))[1]["lum"] is None
    # 半透明の白い細い線だけのロゴ: 描かれている画素で明るさを測る（透明な画素の黒を数えない）。暗い面にそのまま置き、白い地で消さない
    thin = TMP / "lc-thin.png"
    im = Image.new("RGBA", (444, 59), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((0, 0, 443, 58), outline=(255, 255, 255, 200), width=2)
    im.save(thin)
    lum = deck.load_logo(str(thin))[1]["lum"]
    assert lum is not None and lum > 0.9, lum
    assert deck.logo_look(lum, None, "#162428") == {"variant": "normal", "plate": "#162428", "sticker": False}
    # 透明な所の無いロゴ（自分の地を持つ）は、暗い面でもそのまま（地を足さない）。全部透明な画像は rc=2
    solid = TMP / "lc-solid.png"
    Image.new("RGB", (444, 59), (20, 20, 20)).save(solid)
    _, info3 = build(sc, "lc-solid", logo=str(solid))
    assert [(p["variant"], p["sticker"]) for p in info3["logo"]["pages"]] == [("normal", False), ("normal", False)], info3["logo"]["pages"]
    assert deck.logo_look(None, 1.0, "#162428") == {"variant": "normal", "plate": "#162428", "sticker": False}
    empty = TMP / "lc-empty.png"
    Image.new("RGBA", (444, 59), (0, 0, 0, 0)).save(empty)
    for kw in ({"logo": str(empty)}, {"logo": str(black), "logo_white": str(empty)}):
        try:
            build(sc, "lc-empty", **kw)
            raise AssertionError(f"全部透明なロゴが通った: {kw}")
        except T.DeckError as e:
            assert "全部透明" in str(e), e
    # 余白のある白いロゴ: 余白を切った形が合えば使う
    padded = striped("lc-white-padded.png", (255, 255, 255, 255), size=(644, 159), box=(100, 50, 544, 109))
    _, info4 = build(sc, "lc-padded", logo=str(black), logo_white=str(padded))
    assert info4["logo"]["pages"][1]["variant"] == "white", info4["logo"]["pages"]
    tall = TMP / "lc-tall.png"
    Image.new("RGBA", (100, 100), (255, 255, 255, 255)).save(tall)
    for kw, word in (({"logo": str(black), "logo_white": str(tall)}, "縦横比"), ({"logo_white": str(white)}, "一緒に")):
        try:
            build(sc, "lc-bad", **kw)
            raise AssertionError(f"通った: {kw}")
        except T.DeckError as e:
            assert word in str(e), e


def case_logo_positions():
    """--logo-position（Chrome で測る）: 左下は 左下 → 右下・右上は 右上 → 右下（上下をまたぐのは右の列だけ）。
    左下なら下の文字をロゴの右へ、右上ならページ番号は元の位置。角ごとに PPTX の部品名を変える。知らない値は止める。"""
    logo = _logo_png("wide.png", (444, 59), (0, 0, 444, 59))
    html, info = deck.build_html(SAMPLE, TMP / "logo-bl", logo=str(logo), logo_position="bottom-left")
    pages = {pg["no"]: pg for pg in info["logo"]["pages"]}
    assert info["logo"]["position"] == "bottom-left"
    assert pages[2]["corner"] == "bottom-left" and pages[23]["corner"] == "bottom-right" and pages[15].get("off"), \
        f"左下 → 右下 → 置かない になっていない: {pages[2]} {pages[23]} {pages[15]}（23 は左半分が絵）"
    secs = pathlib.Path(html).read_text(encoding="utf-8").split("<section data-slide")[1:]
    assert 'data-keep="logo-left"' in secs[1] and 'data-keep="logo-plate-left"' in secs[1]
    assert re.search(r'class="footer"[^>]*style="left: 477px; width: 435px;"', secs[1]), "下の文字がロゴの右に寄っていない"
    assert re.search(r'class="pageno"[^>]*>2<', secs[1]) and 'data-name="ページ番号" style=' not in secs[1], "ページ番号が動いた"
    assert 'data-keep="logo"' in secs[22], "右下に逃げたページの部品名が右下のものでない"
    html, info = deck.build_html(SAMPLE, TMP / "logo-tr", logo=str(logo), logo_position="top-right")
    pages = {pg["no"]: pg for pg in info["logo"]["pages"]}
    assert pages[2]["corner"] == "top-right" and all(pages[n].get("off") for n in (1, 9, 15)), \
        f"右上 → 右下 → 置かない になっていない（表紙・章扉は右半分が絵）: {[pages[n] for n in (1, 2, 9, 15)]}"
    secs = pathlib.Path(html).read_text(encoding="utf-8").split("<section data-slide")[1:]
    assert 'data-keep="logo-top"' in secs[1] and "top: 24px;" in secs[1], "右上の位置か部品名が違う"
    assert 'data-name="ページ番号" style=' not in secs[1] and 'style="width: 792px;"' in secs[1] and "left: 477px" not in secs[1], \
        "右上なのに下の文字が動いた（中央下の番号の手前まで、のまま）"
    try:
        deck.build_html(SAMPLE, TMP / "logo-tl", logo=str(logo), logo_position="top-left")
        raise AssertionError("top-left が通った")
    except T.DeckError as e:
        assert "--logo-position" in str(e), e


def case_page_numbers_default_off():
    """ページ番号は既定でオフ（page_numbers を書かなければ出さない）・出すときは中央下（2026-10-06）。"""
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data.pop("page_numbers", None)
    p = TMP / "no-numbers.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    html, _ = deck.build_html(p, TMP / "no-numbers", placeholder=True)
    h = pathlib.Path(html).read_text(encoding="utf-8")
    assert 'class="pageno"' not in h and 'class="footer" data-keep="footer" data-name="下の文字">' in h, "既定でページ番号が出た"
    css = (deck.TEMPLATE).read_text(encoding="utf-8")
    assert ".pageno { left: 896px; width: 128px; text-align: center; }" in css, "ページ番号が中央下でない"


def case_watermark():
    """--watermark: 全ページ（表紙・写真も）の中央下の端・固定のグレー・下の文字の行とは重ならない。
    PPTX では全スライドの一番上に、選べない・動かせない部品（!!watermark）。付けなければ何も足さない。改行は止める。"""
    import render as R
    from pptx import Presentation
    html, info = deck.build_html(SAMPLE, TMP / "wm", placeholder=True, watermark="example.com")
    h = pathlib.Path(html).read_text(encoding="utf-8")
    n = len(h.split("<section data-slide")) - 1
    assert h.count('<div class="watermark" data-keep="watermark" data-name="透かし">example.com</div>') == n, "全ページに透かしが無い"
    assert info["watermark"] == "example.com"
    css = deck.TEMPLATE.read_text(encoding="utf-8")
    assert (f"top: {deck.WATERMARK_TOP}px;" in css and f"font-size: {deck.WATERMARK_PX}px;" in css and "color: var(--faint);" in css), \
        "templates/deck.html の .watermark が deck.py の数字とずれた"
    assert deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))["faint"] == deck.WATERMARK_COLOR and deck.ratio(deck.WATERMARK_COLOR, "#ffffff") >= 4.5, "透かしの色は白の地で 4.5:1（Gray-536）"
    assert deck.WATERMARK_TOP > 1008 + 22 * 1.5, "透かしが下の文字の行にかかる"
    rep = R.render(html, TMP / "wm" / "deck.pptx")
    assert not R.errors(rep.get("violations") or []), rep.get("violations")
    for i, sl in enumerate(Presentation(str(TMP / "wm" / "deck.pptx")).slides, 1):
        sh = [x for x in sl.shapes if x.name == "!!watermark"]
        assert len(sh) == 1 and sh[0].text_frame.text == "example.com", f"{i}枚目に透かしの部品が無い"
        locks = sh[0]._element.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}spLocks")
        assert locks is not None and locks.get("noSelect") == "1" and locks.get("noMove") == "1", f"{i}枚目の透かしが選べる・動かせる"
        mid = (sh[0].left + sh[0].width // 2) / 6350
        assert abs(mid - 960) < 2 and sh[0].top / 6350 > 1040, f"{i}枚目の透かしが中央下でない: {mid} {sh[0].top / 6350}"
    html, info = deck.build_html(SAMPLE, TMP / "wm-off", placeholder=True)
    assert 'class="watermark"' not in pathlib.Path(html).read_text(encoding="utf-8") and "watermark" not in info, "付けていないのに透かしが出た"
    try:
        deck.build_html(SAMPLE, TMP / "wm-nl", placeholder=True, watermark="a\nb")
        raise AssertionError("改行のある透かしが通った")
    except T.DeckError as e:
        assert "1行" in str(e), e


# --- DADS（スライド用ローカルデザインシステム・ADR 0020） --------------------------------

SLIDE_JSON = SKILL.parent / "dads" / "tokens" / "slide.json"


def _slide_json() -> dict | None:
    return json.loads(SLIDE_JSON.read_text(encoding="utf-8")) if SLIDE_JSON.is_file() else None


def case_dads_tokens():
    """トークンの正本（skills/dads/tokens/slide.json）と、エンジンの定数が同じ: 文字の段・行高・余白・格子・角丸・影・色。"""
    j = _slide_json()
    if j is None:
        print("skills/dads が無いので飛ばす")
        return
    assert [x["px"] for x in j["type"]["scale"]] == list(T.SCALE), "文字の段"
    assert [float(v) for v in j["type"]["line_height"].values()] == list(T.LINE_HEIGHTS), "行高の段"
    assert {int(k): v for k, v in j["space"]["steps"].items()} == T.SPACE and j["space"]["page_margin_x"] == T.X0, "余白の段"
    g = j["grid"]
    assert (g["columns"], g["column"], g["gutter"], g["margin"], g["width"]) == (T.COLS, T.COL, T.GUTTER, T.X0, T.CW), "格子"
    assert all(T.span(int(k)) == v for k, v in g["span"].items()), "n 列ぶんの幅"
    assert sorted(int(k) for k in j["radius"] if k != "full") + [T.FULL] == sorted(T.RADII), "角丸の段"
    assert j["elevation"]["2"].replace(" ", "") == T.ELEVATION_2.replace(" ", ""), "elevation-2"
    c = j["color"]
    for mine, theirs in ((deck.INK, "text"), (deck.MUTED, "text-muted"), (deck.GRAY_SURFACE, "ground-alt"), (deck.RULE, "border"),
                         (deck.RULE_WEAK, "border-weak"), (deck.RULE, "text-faint")):
        assert mine == c[theirs]["hex"], (mine, theirs)
    assert deck.WATERMARK_COLOR == c["text-faint"]["hex"]
    for k, v in deck.SEMANTIC.items():
        assert v == c[k]["hex"], k
    dflt = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    for mine, theirs in (("primary", "key-primary"), ("tint", "key-background"), ("primary-decor", "key-secondary")):
        assert dflt[mine] == c[theirs]["hex"], (mine, dflt[mine], c[theirs]["hex"])


def case_dads_palette():
    """色の役: 3つのテーマとブランドの色で、文字は 4.5:1・図形と罫は 3:1・意味の色は白の上で（deck.contrast_problems が空）。"""
    for name in ("default", "forest", "warm"):
        th = deck.load_theme(deck.THEMES / f"{name}.yaml")
        p = deck.palette(th)
        assert deck.contrast_problems(p) == [], (name, deck.contrast_problems(p))
        assert p["ink"] == "#333333" and p["muted"] == "#666666", name           # 文字は DADS の Gray-800・Gray-600
        assert deck.ratio(p["rule"], p["bg"]) >= 3.0 and deck.ratio(p["bar"], p["bg"]) >= 3.0, name
        assert deck.ratio(p["faint"], p["bg"]) >= 4.5, name
    for hexc in ("#0db4f2", "#f5a623", "#9ec5ff", "#e60012", "#1f5fd1"):                 # ブランドの色は、同じ色相のまま明度を動かす
        th = deck.load_theme(deck.THEMES / "default.yaml") | {"primary": hexc}
        p = deck.palette(th)
        assert deck.contrast_problems(p) == [], (hexc, deck.contrast_problems(p))
        assert p["primary-mark"] != p["primary-decor"] or deck.ratio(p["primary-decor"], "#ffffff") >= 3.0
    for k, v in deck.SEMANTIC.items():                                                      # 意味の色は白い面の上だけ
        assert deck.ratio(v, "#ffffff") >= (4.5 if k.endswith("-2") else 3.0), k
    # 同じ入力なら同じ色（keycolors は決まった手順）
    th = deck.load_theme(deck.THEMES / "default.yaml")
    assert deck.palette(th) == deck.palette(dict(th))
    dflt = deck.palette(th)
    assert (dflt["primary"], dflt["primary-mark"], dflt["tint"]) == ("#006f83", "#008da6", "#e9f7f9"), dflt
    # 増減の札（tone）とカードの種類分け（group）の役: 実際の色同士で文字 4.5・枠 3。非白の地・surface・ブランドの色でも成り立つ
    roles = [f"tone-{x}-{y}" for x in ("good", "warn", "bad", "neutral") for y in ("bg", "text")] + \
            [f"group-{x}-{y}" for x in "abc" for y in ("bg", "text", "rule")]
    assert all(r in dflt for r in roles) and dflt["tone-neutral-bg"] == dflt["surface"]
    themes = [th, th | {"background": "#fbf6ec", "surface": "#efe7d6"}, th | {"primary": "#e60012"}, th | {"primary": "#f5a623", "accent": "#399dd1"}]
    for i, tv in enumerate(themes):
        p = deck.palette(tv)
        assert deck.contrast_problems(p) == [], (i, deck.contrast_problems(p))
        for x in ("good", "warn", "bad"):
            assert deck.ratio(p[f"tone-{x}-text"], p[f"tone-{x}-bg"]) >= 4.5 and deck.ratio(p[f"tone-{x}-text"], p["bg"]) >= 4.5, (i, x)
        for x in "abc":
            assert all(deck.ratio(p[fg], p[f"group-{x}-bg"]) >= 4.5 for fg in ("ink", "muted", "accent-text", f"group-{x}-text")), (i, x)
            assert deck.ratio(p[f"group-{x}-rule"], p[f"group-{x}-bg"]) >= 3.0 and deck.ratio(p[f"group-{x}-rule"], p["bg"]) >= 3.0, (i, x)
    # 棒の色の段（bar-…）: 良い側 3 段が別の色・濃い順。非白の地・surface・ブランドの色でも棒 3:1・値の文字 4.5:1
    for i, tv in enumerate(themes):
        p = deck.palette(tv)
        g = [p[f"bar-{t}"] for t in ("good-soft", "good", "good-strong")]
        assert len(set(g)) == 3 and deck.ratio(g[0], p["bg"]) < deck.ratio(g[1], p["bg"]) < deck.ratio(g[2], p["bg"]), (i, g)
        for t in ("good-strong", "good", "good-soft", "warn", "bad"):
            for ground in ("bg", "surface"):
                assert deck.ratio(p[f"bar-{t}"], p[ground]) >= 3.0 and deck.ratio(p[f"bar-{t}-text"], p[ground]) >= 4.5, (i, t, ground)
    for role, bad in (("bar-warn", "#e0cf8a"), ("bar-good-text", "#7fc9a0")):
        broken = dict(dflt, **{role: bad})
        assert any(role in m for m in deck.contrast_problems(broken)), role
    for role, bad in (("tone-warn-text", "#d9c26a"), ("tone-bad-bg", "#cc9999"), ("group-b-text", "#f0a060"), ("group-c-rule", "#e9f7f9")):
        broken = dict(dflt, **{role: bad})                      # 色を故意に壊すと contrast_problems が拾う
        assert any(role in m for m in deck.contrast_problems(broken)), role
    # 増減の札の輪郭: 札の置かれる面（通常・group・強調の淡い面）に tone の枠が 3:1、強い look の面には札の地が 3:1
    for face in ("bg", "surface", "accent-soft", "group-a-bg", "group-b-bg", "group-c-bg"):
        broken = dict(dflt, **{face: dflt["tone-good-text"]})
        assert any(f"tone-good-text（{dflt['tone-good-text']}）× {face}" in m for m in deck.contrast_problems(broken)), face
    broken = dict(dflt, **{"accent-strong": dflt["tone-bad-bg"]})
    assert any("tone-bad-bg" in m and "accent-strong" in m for m in deck.contrast_problems(broken)), "強い look の面と札の地"


def _bar_html(bars, name, **extra):
    return build(one({"type": "bar-chart", "headline": "h", "unit": "件", "bars": bars} | extra), name)[0].read_text(encoding="utf-8")


def case_bar_tone():
    """bar-chart の棒の tone: 5段＋neutral・誤りは rc=2・highlight と同時は色が tone・値 0 の札・tone なしと neutral は旧台本と同じ。"""
    tones = ("good-strong", "good", "good-soft", "warn", "bad")
    bars = [{"label": f"b{i}", "value": 10 - i, "tone": t} for i, t in enumerate(tones)]
    body = _bar_html(bars, "bt-all").split("</style>")[1]
    assert re.findall(r'class="bar tone-([\w-]+)"', body) == list(tones) and re.findall(r'class="bar-value tone-([\w-]+)"', body) == list(tones)
    assert all(f'>b{i}<' in body for i in range(5)), "label は必ず出る"
    # 誤り: 位置付きの rc=2。型の違う値も例外を漏らさない
    for v in ("red", "Good", "", 3, True, [], {}, ["good"]):
        expect_error(one({"type": "bar-chart", "headline": "h", "bars": [dict(bars[0], tone=v), bars[1]]}), "bt-bad", "bars の 1 個目の tone は",
                     "good-strong / good / good-soft / warn / bad / neutral のどれか")
    # tone なし・neutral は同じ（灰・highlight もそのまま）。旧台本と同じ HTML（棒の部分）
    old = [{"label": "A", "value": 9, "highlight": True}, {"label": "B", "value": 5}, {"label": "C", "value": 0}]
    neu = [dict(b, tone="neutral") for b in old]
    a, b = (_bar_html(x, n, note="n", callout={"value": "9", "text": "x"}).split("</style>")[1] for x, n in ((old, "bt-old"), (neu, "bt-neu")))
    assert a == b and "tone-" not in a and 'class="bar hl"' in a and 'class="bar"' in a and 'class="bar-label hl"' in a and "var(--accent-text)" in a
    assert 'height:4px' in a, "値 0 の灰の棒は今の 4px の線"
    # highlight と有彩色 tone: 色は tone・accent の色は解く・ラベルは太字
    mix = _bar_html([{"label": "A", "value": 9, "tone": "good", "highlight": True}, {"label": "B", "value": 3, "highlight": True}], "bt-hl").split("</style>")[1]
    assert 'class="bar tone-good"' in mix and 'class="bar-label bold"' in mix and 'class="bar hl"' in mix and 'class="bar-label hl"' in mix
    assert mix.count("var(--accent-text)") == 1, "accent の色は tone のない highlight の棒だけ"
    # 値 0 の札: 有彩色だけ 16px・全部 0 でも組める・note・callout と一緒でも組める
    zero = _bar_html([{"label": "a", "value": 0, "tone": "warn"}, {"label": "b", "value": 0}, {"label": "c", "value": 0, "tone": "neutral"}], "bt-zero",
                     note="注", callout={"value": "0", "text": "t"}).split("</style>")[1]
    hs = re.findall(r'class="bar[^"]*" style="width:\d+px; height:([\d.]+)px"', zero)
    assert hs == ["16", "4", "4"], hs
    # 良い側の 3 段は別の色・濃い順・棒は地（bg・surface）で 3:1・値の文字は 4.5:1
    for th in ("default", "forest", "warm"):
        pal = deck.palette(deck.load_theme(deck.THEMES / f"{th}.yaml"))
        g = [pal[f"bar-{t}"] for t in ("good-soft", "good", "good-strong")]
        assert len(set(g)) == 3 and deck.ratio(g[0], pal["bg"]) < deck.ratio(g[1], pal["bg"]) < deck.ratio(g[2], pal["bg"]), (th, g)
        assert set(T.BAR_TONES) == set(deck.DP.BAR_TONES)


def _css_values(text: str, prop: str) -> list[str]:
    return re.findall(rf"(?<![\w-]){prop}:\s*([^;}}\"]+)", text)


def case_dads_scale_rhythm():
    """組んだ HTML の文字の大きさは DADS の段・行高は行高の段・角丸は角の段・影は elevation-2 だけ。雛形の余白は 8 の倍数。"""
    sizes = set(T.SCALE) | {16}
    lhs = {str(v) for v in T.LINE_HEIGHTS} | {"1"}
    radii = set(T.RADII)
    for src, name in ((SAMPLE, "dads-sample"), (IMAGE_SAMPLE, "dads-image")):
        path, _ = deck.build_html(src, TMP / name, placeholder=True)
        h = pathlib.Path(path).read_text(encoding="utf-8")
        bad = [v for v in _css_values(h, "font-size") if int(re.match(r"\d+", v).group(0)) not in sizes]
        assert not bad, (name, "段の外の文字", sorted(set(bad)))
        bad = [v for v in _css_values(h, "line-height") if v.strip() not in lhs and v.strip() != "1.0"]
        assert not bad, (name, "段の外の行高", sorted(set(bad)))
        bad = [n for v in _css_values(h, "border-radius") for n in re.findall(r"(\d+)px", v) if int(n) not in radii and int(n) not in (4,)]
        assert not bad, (name, "段の外の角丸", sorted(set(bad)))
        shadows = set(_css_values(h, "box-shadow"))
        assert shadows <= {"none", T.ELEVATION_2.replace(", ", ", ")} or all("rgba(0, 0, 0, 0.1)" in x for x in shadows), (name, shadows)
    tpl = deck.TEMPLATE.read_text(encoding="utf-8")
    css = tpl.split("<style>")[1].split("</style>")[0]
    bad = [v for prop in ("padding", "margin", "gap", "padding-left", "margin-left", "margin-top", "margin-bottom", "margin-right") for v in _css_values(css, prop)
           for n in re.findall(r"-?\d+(?:\.\d+)?(?=px)", v) if float(n) % 8]
    assert not bad, f"雛形の余白が 8 の倍数でない: {sorted(set(bad))}"
    assert all(int(v) in sizes for v in re.findall(r"font-size:\s*(\d+)px", css)), "雛形の文字の大きさが段の外"
    assert "var(--line)" not in css and "var(--shadow)" not in css and "box-shadow" in css and css.count("box-shadow") == 1, "影は番号の丸（elevation-2）だけ"


def case_font_default_and_fallback():
    """字体は既定が Noto Sans JP。ファイルが分からなければ游ゴシックに落ちて理由を残す（警告にはしない）。設定に書いてあれば Noto で組む。"""
    th = deck.load_theme(deck.THEMES / "default.yaml")
    assert th["font"] == "Noto Sans JP" and deck.load_theme(deck.THEMES / "forest.yaml")["font"] == "Noto Sans JP"
    yu = T.DEFAULT_FONTS
    fam, files, note = deck.resolve_font(th, {})
    assert fam == "Yu Gothic" and files == yu and "Noto Sans JP" in note, (fam, note)
    cfg = {"fonts": {"Noto Sans JP": {"regular": yu["regular"], "bold": yu["bold"]}}}       # 設定に書いてある（ここでは游ゴシックのファイルを借りる）
    fam, files, note = deck.resolve_font(th, cfg)
    assert fam == "Noto Sans JP" and note is None and files == yu
    gone = {"fonts": {"Noto Sans JP": {"regular": "/nonexistent/NotoSansJP-Regular.ttf"}}}   # 書いてあってもファイルが無ければ落ちる
    assert deck.resolve_font(th, gone)[0] == "Yu Gothic"
    try:
        deck.resolve_font(th | {"font": "My Sans"}, {})
        raise AssertionError("ファイルの分からない字体が通った")
    except T.DeckError as e:
        assert "字体 My Sans" in str(e), e
    assert deck.resolve_font(th | {"font": "Yu Gothic"}, {})[0] == "Yu Gothic"
    _, info = deck.build_html(SAMPLE, TMP / "font-fallback", placeholder=True)
    assert info["font"] in ("Yu Gothic", "Noto Sans JP") and (info["font"] == "Noto Sans JP") == (info["font_note"] is None)
    assert info["warnings"] == [], "字体の落ちは警告にしない"
    h = pathlib.Path(info["html"]).read_text(encoding="utf-8")
    assert f'font-family: "{info["font"]}"' in h and "ascent-override" in h, "字体の上下の幅を 1em に縮める（文字のはみ出しの検査のため）"


def case_footer_one_line():
    """下の文字は1行（注記 22px。入らなければ 20px まで下りる）。入らなければ、透かしがあれば rc=2・無ければ警告。"""
    sc = one({"type": "statement", "headline": "h"})
    sc["page_numbers"] = True
    base = deck.T.text_width("あ", 22)
    room = deck.FOOTER_END_WITH_NO - deck.FOOTER_LEFT - 8
    fit22 = "あ" * int(room // base)
    fit20 = "あ" * int(room // (base * 20 / 22))
    long_ = "あ" * 80
    h, info = build(dict(sc, footer=fit22), "footer-22")
    assert info["footer_px"] == 22 and info["warnings"] == [], info
    h, info = build(dict(sc, footer=fit20), "footer-20")
    assert info["footer_px"] == 20 and 'style="font-size: 20px; width: 792px;"' in pathlib.Path(h).read_text(encoding="utf-8"), info
    h, info = build(dict(sc, footer=long_), "footer-long")
    assert any("1行に入らない" in w for w in info["warnings"]), info["warnings"]
    try:
        build(dict(sc, footer=long_), "footer-long-wm", watermark="社外秘")
        raise AssertionError("透かしの帯にかかる footer が通った")
    except T.DeckError as e:
        assert "透かしの帯" in str(e), e


def _secs(path) -> list[str]:
    return pathlib.Path(path).read_text(encoding="utf-8").split("<section")[1:]


def case_dads_emphasis_not_by_color():
    """型の構造（ADR 0021）: 強める項目は色だけで示さない（札・太い輪郭・縦の罫と太字・二重の丸）。badge は強める項目にだけ書ける。"""
    cards = {"type": "cards", "headline": "h", "items": [{"title": "a", "accent": True}, {"title": "b"}]}
    s = _secs(build(one(cards), "emph-cards")[0])[0]
    assert s.count('class="chip on"') == 1 and ">注目<" in s, "強めるカードに札「注目」が付く"
    cards["items"][0]["badge"] = "おすすめ"
    assert ">おすすめ<" in _secs(build(one(cards), "emph-cards2")[0])[0]
    expect_error(one({"type": "cards", "headline": "h", "items": [{"title": "a", "badge": "x"}, {"title": "b"}]}), "emph-bad", "badge")
    expect_error(one({"type": "cards", "headline": "h", "items": [{"title": "a", "accent": True, "badge": "あいうえおかきくけこ"}, {"title": "b"}]}),
                 "emph-long", "badge")
    kpi = {"type": "kpi", "headline": "h", "items": [{"label": "a", "value": "1", "accent": True, "badge": "目標"}, {"label": "b", "value": "2"},
                                                     {"label": "c", "value": "3"}]}
    assert ">目標<" in _secs(build(one(kpi), "emph-kpi")[0])[0]
    cmp_ = {"type": "compare", "headline": "h", "left": {"label": "いま", "title": "a"}, "right": {"label": "これから", "title": "b", "badge": "目玉"}}
    s = _secs(build(one(cmp_), "emph-cmp")[0])[0]
    assert ">目玉<" in s and 'class="chips"' in s and s.index("いま") < s.index("これから"), "札は label の隣に並ぶ"
    flow = {"type": "flow", "headline": "h", "items": [{"label": "a"}, {"label": "b"}, {"label": "c", "badge": "ゴール"}]}
    s = _secs(build(one(flow), "emph-flow")[0])[0]
    assert 'stroke-width="4"' in s and ">ゴール<" in s, "最後の段は太い輪郭（と、書けば札）"
    assert 'class="chip' not in _secs(build(one(dict(flow, items=[{"label": "a"}, {"label": "b"}, {"label": "c"}])), "emph-flow2")[0])[0], "札は書いたときだけ"
    tbl = {"type": "table", "headline": "h", "columns": ["a", "b", "c"], "rows": [["1", "2", "3"]], "highlight": 2}
    s = _secs(build(one(tbl), "emph-table")[0])[0]
    assert s.count('class="td hl"') == 2, "強める列の見出しと行"
    css = deck.TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"\.td\.hl \{[^}]*border-left: 4px solid var\(--accent-mark\)[^}]*border-right: 4px", css), "強める列は太い縦の罫"
    tl = {"type": "timeline", "headline": "h", "current": 2, "items": [{"when": "1月", "title": "a"}, {"when": "2月", "title": "b"}, {"when": "3月", "title": "c"}]}
    s = _secs(build(one(tl), "emph-tl")[0])[0]
    assert ">いま<" in s and 'class="tl-dot hl"' in s and 'class="tl-dot-in"' in s, "いまは札と二重の丸"
    mx = {"type": "matrix", "headline": "h", "x": {"label": "x"}, "y": {"label": "y"}, "focus": 2,
          "items": [{"label": "a", "x": 80, "y": 80, "accent": True}, {"label": "b", "x": 20, "y": 20}]}
    s = _secs(build(one(mx), "emph-mx")[0])[0]
    assert 'class="mx-dot hl"' in s and 'class="mx-dot-in"' in s, "強める点は二重の輪"
    assert re.search(r"\.mx-focus \{[^}]*border: 4px solid var\(--accent-mark\)", css), "focus の象限は枠線で囲む"
    assert s.index('class="mx-ylab"') < s.index('class="mx-item"') < s.index("mx-quad") if "mx-quad" in s else True, "読む順は 軸 → 項目 → 象限名"


def case_table_align():
    """table の align: 列ごとの揃えを th・td の両方に当てる。省略時は今どおり（1列目 left・残り center）。数・値が悪ければ rc=2。"""
    base = {"type": "table", "headline": "h", "columns": ["a", "b", "c"], "rows": [["1", "2", "3"], ["4", "5", "6"]]}
    def tds(sc, name):
        return re.findall(r'class="td(?: hl)?" style="width:[\d.]+px; justify-content:([a-z-]+)"><div style="font-size:\d+px; text-align:([a-z]+);', _secs(build(one(sc), name)[0])[0])
    default = tds(base, "al-default")
    assert default == [("flex-start", "left"), ("center", "center"), ("center", "center")] * 3, default
    assert tds(dict(base, align=["left", "center", "center"]), "al-same") == default, "明示しても省略時と同じ"
    got = tds(dict(base, align=["center", "right", "left"], highlight=2), "al-mixed")
    assert got == [("center", "center"), ("flex-end", "right"), ("flex-start", "left")] * 3, got
    msg = "align は columns と同じ数の配列で、値は left / center / right のどれか"
    for bad, n in (([], "al-empty"), (["left", "right"], "al-short"), (["left", "right", "middle"], "al-value"), ("right", "al-str"), ([1, 2, 3], "al-int"), ([["left"], "right", "center"], "al-list")):
        expect_error(one(dict(base, align=bad)), n, msg)


def case_footnote():
    """footnote: source の下の1行・source が無ければ source の位置・脚注の分だけ中身の下端と帯が上がる・入らなければ rc=2・書かない台本は今のまま。"""
    src, fn = "定義の1行", "※1 補足の1行"
    tbl = {"type": "table", "headline": "h", "columns": ["a", "b"], "rows": [["1", "2※1"], ["3", "4"]]}
    tops = lambda sc, name: [int(v) for v in re.findall(r'class="source[^"]*" style="top:(\d+)px', _secs(build(one(sc), name)[0])[0])]
    both = _secs(build(one(dict(tbl, source=src, footnote=fn)), "fn-both")[0])[0]
    assert re.findall(r'class="source( footnote)?"[^>]*data-name="(出典|脚注)">([^<]*)<', both) == [("", "出典", src), (" footnote", "脚注", fn)], "出典 → 脚注の順"
    t_both = tops(dict(tbl, source=src, footnote=fn), "fn-both")
    assert t_both[1] - t_both[0] == T.FOOT_H, f"脚注は source の1行下: {t_both}"
    assert tops(dict(tbl, footnote=fn), "fn-only") == [t_both[0]], "脚注だけなら source の位置"
    # 型ごと: source の下に1行・下端の文字（1008〜）と重ならない
    types = {
        "kpi": {"items": [{"label": "売上", "value": "1"}, {"label": "件数", "value": "2"}, {"label": "解約", "value": "3"}]},
        "bar-chart": {"bars": [{"label": "東", "value": 4}, {"label": "西", "value": 3}]},
        "line-chart": {"labels": ["1月", "2月", "3月"], "series": [{"name": "東", "values": [1, 2, 3]}]},
        "bullets": {"items": [{"title": "a", "body": "b"}, {"title": "c", "body": "d"}]},
        "statement": {"body": "本文"},
        "quote": {"quote": "引用"},
    }
    for ty, body in types.items():
        sc = dict(body, type=ty, source=src, footnote=fn)
        if ty not in ("quote",):
            sc.setdefault("headline", "h")
        t_ = tops(sc, f"fn-{ty}")
        assert len(t_) == 2 and t_[1] - t_[0] == T.FOOT_H and t_[1] + T.FOOT_H <= 1008, f"{ty}: {t_}"
        plain = tops({k: v for k, v in sc.items() if k != "footnote"}, f"fn-{ty}-plain")
        assert t_[0] == plain[0] - T.FOOT_H, f"{ty}: 脚注の分だけ source が上がる: {t_} {plain}"
        only = tops({k: v for k, v in sc.items() if k != "source"}, f"fn-{ty}-only")
        assert only == plain, f"{ty}: 脚注だけの top は source だけの top と同じ: {only} {plain}"
    # 書かない台本は旧版と同じ
    plain = _secs(build(one(dict(tbl, source=src)), "fn-plain")[0])[0]
    assert "footnote" not in plain and tops(dict(types["bullets"], type="bullets", headline="h", source=src), "fn-plain") == [T.BODY_BOTTOM + 8], "書かない台本: source は今までの位置（BODY_BOTTOM + 8）で脚注の部品が無い"
    # takeaway・summary・legend と併用して入らなければ rc=2（縮める所を言う）
    many = dict(tbl, rows=[[str(i), "x"] for i in range(7)], summary=[{"label": "平均", "value": "1"}],
                legend=[{"tone": "good", "text": "良"}], takeaway="結論", source=src, footnote=fn)
    expect_error(one(many), "fn-over", "rows を減らす", "footnote")
    # 1行に入らなければ縮めず rc=2（source と同じ扱い）・文字でなければ rc=2
    expect_error(one(dict(tbl, footnote="※" * 200)), "fn-long", "footnote")
    expect_error(one(dict(tbl, footnote=["※1"])), "fn-list", "footnote")
    # 見出しを持たない型（cover など）では書けない
    expect_error(one({"type": "cover", "title": "t", "footnote": fn}), "fn-cover", "知らないキー")


def case_table_tone_dim_legend():
    """table のセルの tone・dim_rows・legend: 地と文字色の class・退かせた行は tone を外す・凡例は表と source の間・誤りは位置付きで rc=2。"""
    base = {"type": "table", "headline": "h", "columns": ["a", "b", "c"], "rows": [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]]}
    rows = [["1", "2", {"text": "3日", "tone": "good"}], ["4", {"text": 5, "tone": "warn"}, {"text": "6日", "tone": "bad"}], ["7", "8", {"text": "**9**日", "tone": "bad"}]]
    legend = [{"tone": "good", "text": "予定どおり"}, {"tone": "warn", "text": "1週間"}, {"tone": "bad", "text": "2週間"}, {"tone": "dim", "text": "見送り"}]
    full = dict(base, rows=rows, dim_rows=[3, 3], highlight=3, legend=legend, source="注記")
    s = _secs(build(one(full), "tdl-full")[0])[0]
    cells = re.findall(r'class="td(?: hl)? tone-(good|warn|bad)"', s)
    assert cells == ["good", "warn", "bad"], f"セルの tone の class（退かせた3行目の bad は付かない）: {cells}"
    assert re.findall(r'class="tone-legend-item tone-(\w+)"', s) == ["good", "warn", "bad", "dim"], "凡例の見本の class"
    assert s.count('class="tr dim"') == 1 and s.count(" dim\"") == 1, "dim_rows の重複は1行にまとまる"
    assert 'class="td hl tone-good"' in s and ">5<" in s, "highlight の列でも tone（地は tone が勝つ。罫と太字は hl のまま）・数のセルは num_label"
    assert s.count('class="tone-legend-item') == 4 and 'data-name="色の凡例"' in s and s.index('class="table"') < s.index('class="tone-legend"') < s.index('class="source"'), "表 → 凡例 → source"
    css = deck.TEMPLATE.read_text(encoding="utf-8")
    for t_ in ("good", "warn", "bad"):
        assert re.search(rf"\.td\.tone-{t_} \{{ background-color: var\(--tone-{t_}-bg\)", css) and f".td.tone-{t_} div, .td.tone-{t_} em {{ color: var(--tone-{t_}-text)" in css
        assert f".tone-legend-item.tone-{t_} .tone-legend-swatch {{ background-color: var(--tone-{t_}-bg); border-color: var(--tone-{t_}-text)" in css
    assert ".tr.dim .td div, .tr.dim .td em { color: var(--muted); }" in css, "退かせた行は em も muted"
    y = lambda cls, h: float(re.search(rf'class="{cls}" style="(?:left:\d+px; )?top:([\d.]+)px', h).group(1))
    tbl_top = y("table", s)
    rh = float(re.search(r'class="tr th" style="height:(\d+)px', s).group(1))
    assert y("tone-legend", s) == round(tbl_top + rh * 4 + 16) and y("source", s) == round(tbl_top + rh * 4 + 16 + 48), "凡例は表の下16px・source は凡例の分48pxずれる"
    # 旧台本: tone・dim・legend が無ければ class も凡例も付かず、source は表の下16px
    old = _secs(build(one(dict(base, source="注記")), "tdl-old")[0])[0]
    assert "tone-" not in old and "tr dim" not in old and "tone-legend" not in old, "書かなければ何も足さない"
    rh0 = float(re.search(r'class="tr th" style="height:(\d+)px', old).group(1))
    assert y("source", old) == round(y("table", old) + rh0 * 4 + 16), "凡例なしの source の位置は今までどおり"
    assert rh < rh0 or rh == rh0, "凡例ぶんだけ行は低くなるか、同じ"
    # 凡例は表の行を低くしても入る。3 色すべて使うとき・使わない色を書くのも通る（凡例に未使用の色を許す）
    build(one(dict(base, legend=[{"tone": "dim", "text": "使っていない"}])), "tdl-unused")
    # rc=2: セル・列・dim_rows・legend（型の悪い値でも例外を漏らさない）
    cell = lambda v: dict(base, rows=[["1", "2", "3"], ["4", v, "6"], ["7", "8", "9"]])
    for v, name, word in (({"text": "x", "tone": "info"}, "c-tone", "表の 2 行目 2 列目: tone は good / warn / bad のどれか"),
                          ({"text": "x", "tone": 1}, "c-tone-int", "tone は good / warn / bad のどれか"),
                          ({"text": "x", "tone": ["good"]}, "c-tone-list", "tone は good / warn / bad のどれか"),
                          ({"tone": "good"}, "c-notext", "表の 2 行目 2 列目: text は文字列か数"),
                          ({"text": True, "tone": "good"}, "c-bool", "text は文字列か数"),
                          ({"text": ["a"], "tone": "good"}, "c-list", "text は文字列か数"),
                          ({"text": {"a": 1}}, "c-dict", "text は文字列か数"),
                          ({"text": "x", "colour": "good"}, "c-key", "知らないキー: colour")):
        expect_error(one(cell(v)), name, word)
    expect_error(one(dict(base, columns=["a", {"text": "b", "tone": "good"}, "c"])), "c-col", "columns の 2 個目にセルのオブジェクトは書けない")
    for v, name, word in ((5, "d-out", "dim_rows の 5 は rows（3 行）の外"), (0, "d-zero", "dim_rows の 0 は rows（3 行）の外"), (True, "d-bool", "整数でない"),
                          ("2", "d-str", "整数でない"), (1.5, "d-float", "整数でない"), ([1], "d-nest", "整数でない"), (None, "d-none", "整数でない")):
        expect_error(one(dict(base, dim_rows=[v])), name, word)
    expect_error(one(dict(base, dim_rows="1")), "d-notlist", "dim_rows は行の番号")
    # 書いたキーの null は未指定と分けて rc=2（セルの tone は行・列つき、ほかはキー名つき）
    expect_error(one(cell({"text": "b", "tone": None})), "c-tone-null", "表の 2 行目 2 列目: tone は good / warn / bad のどれか")
    expect_error(one(dict(base, dim_rows=None)), "d-null", "dim_rows は行の番号")
    expect_error(one(dict(base, legend=None)), "l-null", "legend は 1〜4 個")
    build(one(dict(base, dim_rows=[])), "d-empty")
    ok = {"tone": "warn", "text": "注意"}
    for lg, name, word in (([], "l-empty", "legend は 1〜4 個"), ([ok] * 5, "l-five", "legend は 1〜4 個"), ("warn", "l-str", "legend は 1〜4 個"), ({"tone": "warn"}, "l-dict", "legend は 1〜4 個"),
                           ([ok, {"tone": "info", "text": "x"}], "l-tone", "legend の 2 個目: tone は good / warn / bad / dim のどれか"),
                           ([ok, {"tone": "dim"}], "l-notext", "legend の 2 個目は tone と text のオブジェクト"),
                           ([{"tone": "warn", "text": ""}], "l-empty-text", "legend の 1 個目: text は空でない"),
                           ([{"tone": "warn", "text": 3}], "l-int-text", "text は空でない"),
                           ([{"tone": "warn", "text": "a", "color": "x"}], "l-extra", "legend の 1 個目は tone と text"),
                           (["warn"], "l-item-str", "legend の 1 個目は tone と text"),
                           ([{"tone": "warn", "text": "あ" * 40}] * 3, "l-wide", "legend が1行に入らない")):
        expect_error(one(dict(base, legend=lg)), name, word)
    expect_error(one({"type": "line-chart", "headline": "h", "labels": ["1", "2"], "series": [{"values": [1, 2]}], "legend": [ok]}), "l-line", "legend")
    expect_error(one({"type": "cards", "headline": "h", "items": [{"title": "a"}, {"title": "b"}], "legend": [ok]}), "l-cards", "legend")
    # 行が低すぎて最小の文字（24px・行高 1.5・上下 12px＝48px）が入らないときは rc=2。見出しを小さく組み直せば入るなら、そちらで入れる
    long_head = {"kicker": "k", "headline": "見出しを長くして二行にする、さらに長くして確かめる文章を書く、まだ続けて二行に収める",
                 "sub": "リード文も長くして二行にする。リード文も長くして二行にする。リード文も長くする。"}
    tight = dict(base, **long_head, rows=[["1", "2", "3"]] * 7, legend=legend[:1], source="注記", takeaway="結論")
    msg = expect_error(one(tight), "tdl-tight", "表が入らない", "文字 24px が入らない")
    assert "legend" in msg and "rows" in msg, msg
    packed = dict(base, rows=[["1", "2", "3"]] * 7, source="x", takeaway="t", kicker="k", sub="リード文を長く" * 6)
    for sc, name in ((packed, "tdl-packed"), (dict(packed, rows=[["1", "2", "3"]] * 5, legend=legend[:2]), "tdl-packed-lg")):
        rhs = [int(v) for v in re.findall(r'class="tr th?" style="height:(\d+)px', _secs(build(one(sc), name)[0])[0])]
        assert rhs and min(rhs) >= 48, f"最小の行の高さ 48px を割った: {rhs}"
        assert all(v % 8 == 0 for v in rhs), f"行の高さが 8 の倍数でない: {rhs}"


def case_table_tone_contrast_all_themes():
    """セルの tone の文字×地は 4.5:1・凡例の枠×地は 3:1・薄い行の muted×highlight の地は 4.5:1（全テーマ）。壊せば contrast_problems が拾う。"""
    import deck_palette as DP
    for name in ("default", "forest", "warm"):
        pal = deck.palette(deck.load_theme(deck.THEMES / f"{name}.yaml"))
        for t_ in ("good", "warn", "bad"):
            assert DP.ratio(pal[f"tone-{t_}-text"], pal[f"tone-{t_}-bg"]) >= 4.5, (name, t_, "セルの文字")
            assert DP.ratio(pal[f"tone-{t_}-text"], pal["bg"]) >= 3.0, (name, t_, "凡例の枠")
        for g in ("bg", "surface", "accent-soft"):
            assert DP.ratio(pal["muted"], pal[g]) >= 4.5, (name, g, "薄い行の文字")
        assert not DP.contrast_problems(pal), name
        broken = dict(pal, **{"tone-warn-text": pal["tone-warn-bg"]})
        assert DP.contrast_problems(broken), "tone の文字を地と同じにしたら拾う"


def case_dads_matrix_overlap():
    """matrix: 名前は点・ほかの名前・区切り線と重ならないようにずらす。点どうしが重なる・置けないときは rc=2。"""
    base = {"type": "matrix", "headline": "h", "x": {"label": "x"}, "y": {"label": "y"}}
    near = dict(base, items=[{"label": "決めたらその場で閉じる", "x": 78, "y": 88, "accent": True}, {"label": "25分を基本にする", "x": 86, "y": 70, "accent": True},
                             {"label": "議題は前日までに出す", "x": 60, "y": 62, "accent": True}])
    s = _secs(build(one(near), "mx-near")[0])[0]
    boxes = []
    for m in re.finditer(r'class="mx-item" style="left:([\d.]+)px; top:([\d.]+)px; width:([\d.]+)px; height:([\d.]+)px', s):
        x, y, w, h = map(float, m.groups())
        boxes.append((x, y, x + w, y + h))
    assert len(boxes) == 3
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]), f"項目が重なる: {a} {b}"
    expect_error(one(dict(base, items=[{"label": "a", "x": 50, "y": 50}, {"label": "b", "x": 51, "y": 50}])), "mx-dots", "重なる")


def case_dads_line_chart_marks():
    """line-chart: 系列は点の形（丸・四角・ひし形）と線種（実線・破線・点線）で区別し、線の端に名前を出す。長くて入らなければ凡例（線種と点の形も写す）。"""
    three = {"type": "line-chart", "headline": "h", "labels": ["1月", "2月", "3月", "4月"],
             "series": [{"name": "全体", "values": [3, 4, 5, 6]}, {"name": "東日本", "values": [2, 3, 3, 4], "highlight": True},
                        {"name": "西日本", "values": [1, 2, 2, 3]}]}
    s = _secs(build(one(three), "lc-3")[0])[0]
    assert s.count("<polyline") == 3 and s.count("stroke-dasharray") == 2, "2本目・3本目は破線・点線"
    assert "<circle" in s and "<rect" in s and "<polygon" in s, "点の形が3つ"
    assert s.count('class="end-name"') == 3 and 'class="legend"' not in s, "線の端に名前（凡例は出さない）"
    for nm in ("全体", "東日本", "西日本"):
        assert f"<div>{nm}</div>" in s
    long_ = dict(three, series=[{"name": "系列Aの名前" + "あ" * 20, "values": [3, 4, 5, 6]},
                                {"name": "系列Bの名前" + "あ" * 20, "values": [2, 3, 3, 4]}])
    s = _secs(build(one(long_), "lc-long")[0])[0]
    assert 'class="legend"' in s and 'class="end-name"' not in s and "<line" in s, "名前が入らなければ凡例（線種と点の形つき）"
    big = {"type": "line-chart", "headline": "h", "labels": ["1", "2", "3"], "callout": {"value": "x", "text": "y"},
           "series": [{"name": "A", "values": [100000000000] * 3}, {"name": "B", "values": [90000000000] * 3}]}
    s = _secs(build(one(big), "lc-big")[0])[0]          # 最後の値（桁が多い）も端の幅に入る。callout の左（1224）を越えない
    for m in re.finditer(r'class="end-name" style="left:([\d.]+)px; top:[\d.]+px; width:([\d.]+)px', s):
        assert float(m.group(1)) + float(m.group(2)) <= 1224 + 0.5, m.groups()
    assert s.count('class="end-name"') == 2 and "100,000,000,000" in s
    tall = {"type": "line-chart", "kicker": "k", "headline": "見出しを長くして二行にする、さらに長くして確かめる文章を書く、まだ続けて二行に収める", "sub": "リード文も長くして二行にする。リード文も長くして二行にする。リード文も長くする。",
            "labels": ["1", "2", "3"], "min": 0, "max": 10, "takeaway": "結論の1行",
            "series": [{"name": "第一系列" + "あ" * 8, "values": [1, 2, 0]}, {"name": "第二系列" + "い" * 8, "values": [1, 2, 0]},
                       {"name": "第三系列" + "う" * 8, "values": [1, 2, 0]}]}
    s = _secs(build(one(tall), "lc-tall")[0])[0]       # 端の名前の高さが見出しの下から軸までに入らなければ凡例（見出しに重ならない）
    assert 'class="legend"' in s and 'class="end-name"' not in s, "高さが足りないのに端の名前を置いた"
    dl = _secs(build(one({"type": "deflist", "headline": "h", "items": [{"term": "a", "body": "b"}] * 4}), "dl-top")[0])[0]
    assert 'class="col" style="left:120px; top:' in dl and 'class="center"' not in dl, "deflist は見出しの下から始める"
    # Codex r4: 長い用語で用語の列が広がっても、説明が入らなければほかの幅で組む（前に組めた台本を止めない・ADR 0022）
    long_dl = {"type": "deflist", "headline": "用語", "items": [{"term": "語" * 18, "body": "文" * 80}] * 7}
    _secs(build(one(long_dl), "dl-long")[0])
    # 大きく組んだ狭い箱で、禁則と文節の折り返しで行が1字延びて箱を越えた実例（3枚のカードの本文が 45 に上がった）
    tight = {"type": "cards", "headline": "写真はコピーせず、台帳には情報だけを置く",
             "items": [{"label": "読むだけ", "title": "写真フォルダと使用済み写真フォルダ", "body": "写真はコピーせず、本文も保存しない", "accent": True},
                       {"label": "使用履歴", "title": "Excel にして置く", "body": "部のフォルダに置く（見る用）"},
                       {"label": "タグ", "title": "2種類で付ける", "body": "フォルダ名から取る「場所・来歴」と、画像から作る「中身」"}]}
    _secs(build(one(tight), "grow-kinsoku")[0])
    # Codex r6: 2組の pairs は大きく組まないとき今までの箱の高さ（360）。4〜5行の文が入る（前に組めた台本を止めない）
    for k in (4, 5):
        t = "\n".join(["文"] * k)
        _secs(build(one({"type": "pairs", "headline": "h", "items": [{"label": "a", "before": t, "after": t}] * 2}), f"pairs-tall-{k}")[0])
    # 非強調の線は rule 以上の濃さ（ink・muted）
    p = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    assert deck.ratio(p["ink"], p["bg"]) >= deck.ratio(p["rule"], p["bg"]) and deck.ratio(p["muted"], p["bg"]) >= deck.ratio(p["rule"], p["bg"])


def case_dads_reading_order():
    """DOM 順（読む順）: hub は 中心 → 周り・pairs は 札 → 文。pairs の札は NG・OK の語とアイコン（×・チェック）を両方持つ。takeaway は頭にアイコン。"""
    hub = {"type": "hub", "headline": "h", "center": {"title": "中心"}, "items": [{"title": "a"}, {"title": "b"}, {"title": "c"}]}
    s = _secs(build(one(hub), "ro-hub")[0])[0]
    assert s.index('class="card hub-center"') < s.index('class="card hub-item"'), "中心が先"
    assert "stroke:var(--rule); stroke-width:2" in s and "stroke-dasharray" not in s, "接続線は rule の 2px"
    pairs = {"type": "pairs", "headline": "h", "items": [{"label": "a", "before": "x", "after": "y"}, {"label": "b", "before": "x", "after": "y"}]}
    s = _secs(build(one(dict(pairs, takeaway="結論", takeaway_icon="bulb")), "ro-pairs")[0])[0]
    first = s.index('class="card pair"')
    tags = [m.start() for m in re.finditer(r'class="pair-tag ', s[first:])]
    boxes = [m.start() for m in re.finditer(r'class="pair-box ', s[first:])]
    assert tags[0] < boxes[0] < tags[1] < boxes[1] or tags[0] < boxes[0], "札 → 文の順"
    assert "var(--error-2)" in s and "var(--success-2)" in s and ">NG<" in s and ">OK<" in s
    assert re.search(r'class="pair-tag pair-bad-tag"[^>]*><svg', s) and re.search(r'class="pair-tag pair-good-tag"[^>]*><svg', s), "札にアイコン"
    take = re.search(r'<div class="take".*?</div></div>', s, re.S).group(0)
    assert "<svg" in take and 'class="take-t"' in take and "結論" in take, "帯はアイコンと語"
    plain = _secs(build(one(dict(pairs, takeaway="結論")), "ro-pairs2")[0])[0]
    assert re.search(r'<div class="take".*?<path d="([^"]+)"', plain, re.S).group(1) != re.search(r'<div class="take".*?<path d="([^"]+)"', s, re.S).group(1), "takeaway_icon が効く"
    expect_error(one(dict(pairs, takeaway_icon="bulb")), "ro-icon-only", "takeaway_icon")
    expect_error(one(dict(pairs, takeaway="x", takeaway_icon="unicorn")), "ro-icon-bad", "unicorn")


def case_dads_new_types():
    """新しい型（ADR 0021）: notice（種類・アイコンと語・クリックは枠ごと）・deflist（行ごと）・steps（段ごと・状態は形と札と線で）。誤りは rc=2。"""
    notice = {"type": "notice", "headline": "h", "items": [{"kind": "info", "title": "a", "body": "b"}, {"kind": "success", "title": "c"},
                                                           {"kind": "warning", "title": "d", "points": ["e", "f"]}]}
    path, info = build(one(notice), "nt")
    s = _secs(path)[0]
    assert info["slides"][0]["clicks"] == 3
    for word in ("お知らせ", "完了", "注意"):
        assert f">{word}<" in s
    assert 'class="nt-band"' in s and s.count("<svg") >= 3 and "var(--warning-orange-2)" in s and "var(--success-1)" in s, "種類は色・アイコン・語"
    assert ">重要<" in _secs(build(one({"type": "notice", "headline": "h", "items": [{"kind": "error", "title": "x"}]}), "nt-err")[0])[0]
    assert ">できた<" in _secs(build(one({"type": "notice", "headline": "h", "items": [{"title": "x", "label": "できた"}]}), "nt-label")[0])[0]
    for bad, words in (({"type": "notice", "headline": "h", "items": [{"kind": "nazo", "title": "x"}]}, "kind"),
                       ({"type": "notice", "headline": "h", "items": [{"title": "x"}] * 5}, "1〜4"),
                       ({"type": "notice", "headline": "h", "items": [{"title": "x", "points": ["a"] * 5}]}, "points"),
                       ({"type": "notice", "headline": "h", "items": [{"kind": "info"}]}, "title"),
                       ({"type": "notice", "headline": "h", "items": [{"title": "x"}], "takeaway": "t"}, "知らないキー")):
        expect_error(one(bad), "nt-bad", words)
    dl = {"type": "deflist", "headline": "h", "items": [{"term": f"用語{i}", "body": f"説明{i}"} for i in range(1, 5)]}
    path, info = build(one(dl), "dl")
    s = _secs(path)[0]
    assert info["slides"][0]["clicks"] == 4 and s.count('class="dl-row') == 4 and 'class="dl-row first"' in s
    assert re.search(r"\.dl-row \{[^}]*border-bottom: 2px solid var\(--rule\)", deck.TEMPLATE.read_text(encoding="utf-8")), "行の区切りは rule"
    assert f"width:{T.span(3)}px" in s, "用語の列は格子の 3 列"
    for bad, words in (({"type": "deflist", "headline": "h", "items": [{"term": "a", "body": "b"}] * 2}, "3〜8"),
                       ({"type": "deflist", "headline": "h", "items": [{"term": "a", "body": "b", "icon": "doc"}, {"term": "c", "body": "d"}, {"term": "e", "body": "f"}]}, "全部の行"),
                       ({"type": "deflist", "headline": "h", "items": [{"term": "a"}, {"term": "b"}, {"term": "c"}]}, "body")):
        expect_error(one(bad), "dl-bad", words)
    steps = {"type": "steps", "headline": "h", "current": 2, "items": [{"title": "a"}, {"title": "b"}, {"title": "c"}, {"title": "d"}]}
    path, info = build(one(steps), "st")
    s = _secs(path)[0]
    assert info["slides"][0]["clicks"] == 4 and ">済<" in s and ">いま<" in s and s.count(">これから<") == 2, "状態は札の語"
    assert s.count('class="st-no cur"') == 1 and "stroke-dasharray" in s, "いまは二重の輪・これからの線は破線"
    col = _secs(build(one(dict(steps, layout="column", items=steps["items"] + [{"title": "e"}, {"title": "f"}])), "st-col")[0])[0]
    assert col.count('class="st-row"') == 6
    assert ">いま<" in _secs(build(one({"type": "steps", "headline": "h", "items": [{"title": "a", "state": "done"}, {"title": "b", "state": "current"}, {"title": "c"}]}),
                                   "st-state")[0])[0], "state だけでも書ける"
    for bad, words in (({"type": "steps", "headline": "h", "items": [{"title": "a"}, {"title": "b"}, {"title": "c"}]}, "flow"),
                       ({"type": "steps", "headline": "h", "current": 9, "items": [{"title": "a"}, {"title": "b"}, {"title": "c"}]}, "current"),
                       ({"type": "steps", "headline": "h", "items": [{"title": "a", "state": "current"}, {"title": "b", "state": "current"}, {"title": "c"}]}, "1 つ"),
                       ({"type": "steps", "headline": "h", "current": 1, "items": [{"title": "a", "state": "nazo"}, {"title": "b"}, {"title": "c"}]}, "state"),
                       ({"type": "steps", "headline": "h", "current": 1, "items": [{"title": str(i)} for i in range(6)]}, "3〜5"),
                       ({"type": "steps", "headline": "h", "current": 1, "layout": "diag", "items": [{"title": str(i)} for i in range(3)]}, "layout")):
        expect_error(one(bad), "st-bad", words)
    # 台本の talk は 段の数 + 1（クリックは枠・行・段ごと）
    build(one(notice, talk=["入り", "1", "2", "3"]), "nt-talk")
    build(one(dl, talk=["入り", "1", "2", "3", "4"]), "dl-talk")


def case_notice_four():
    """notice の4個と label_style（#49）: 4個は余白・枠のあいだ・題の出発点を詰めて1ページに収まり、入らなければ rc=2。
    chip は頭の行をアイコンでなく kind の色の札にする（語は残る・札の文字は地に 4.5:1・輪郭は 3:1）。1〜3個で label_style を書かない台本は今のまま。"""
    four = [{"kind": "error", "label": "課題", "title": "返信に平均 3 日かかっていた"},
            {"kind": "warning", "label": "原因", "title": "担当の割り振りが決まっていなかった"},
            {"kind": "info", "label": "対策", "title": "受付の時点で担当を自動で割り振る"},
            {"kind": "success", "label": "アクション", "title": "来月から割り振りの表を運用する", "body": "月末に件数と返信までの日数を見直す"}]
    nt = lambda items, **kw: {"type": "notice", "headline": "返信が遅れた件", "items": items, **kw}
    path, info = build(one(nt(four)), "nt4")
    s = _secs(path)[0]
    assert info["slides"][0]["clicks"] == 4 and s.count('class="notice"') == 4 and s.count("<svg") >= 4
    assert s.count('padding:12px 40px') == 4 and "gap:12px" in s, "4個は余白と枠のあいだを詰める"
    for word in ("課題", "原因", "対策", "アクション"):
        assert f">{word}<" in s
    for var in ("var(--error-1)", "var(--warning-orange-1)", "var(--primary-mark)", "var(--success-1)"):
        assert var in s, f"4つの枠の色が分かれる: {var}"
    assert len({re.search(r'border-color:([^"]+)"', b).group(1) for b in s.split('class="notice"')[1:]}) == 4
    only = [dict(it) for it in four]
    for it in only:
        it.pop("body", None)
    s = _secs(build(one(nt(only, label_style="chip")), "nt4-chip-only")[0])[0]
    assert s.count('class="notice"') == 4 and s.count("notice-tag") == 4 and "<svg" not in s, "title だけの4枠・札はアイコンを出さない"
    # chip: 語は札に残り、アイコンは出ない。札の色は kind の -2（文字）で、同じ色の輪郭
    s = _secs(build(one(nt(four, label_style="chip")), "nt4-chip")[0])[0]
    tags = re.findall(r'<div style="color:([^;]+); border-color:([^"]+)" class="chip notice-tag">([^<]+)</div>', s)
    assert [(c, b, w) for c, b, w in tags] == [(c, c, w) for c, w in zip(("var(--error-2)", "var(--warning-orange-2)", "var(--primary-text)", "var(--success-2)"),
                                                                      ("課題", "原因", "対策", "アクション"))], tags
    assert "nt-label" not in s and "<svg" not in s
    # 札の色: 地（枠の白い面）の上で、文字 4.5:1・輪郭 3:1
    pal = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    for kind, (_, _, line, strong) in T.NOTICE_KINDS.items():
        role = strong[len("var(--"):-1]
        assert deck.ratio(pal[role], "#ffffff") >= 4.5, (kind, role)
        assert deck.ratio(pal[role], "#ffffff") >= 3.0, (kind, role)
        assert strong in s, (kind, "札の色")
    # 札の既定の語（label を省くと kind の語）
    s = _secs(build(one(nt([{"kind": "success", "title": "x"}], label_style="chip")), "nt4-chip-dflt")[0])[0]
    assert ">完了<" in s and "notice-tag" in s
    # 個数によらず選べる・icon は書かないのと同じ HTML
    for n in (1, 2, 3, 4):
        base = _secs(build(one(nt(four[:n])), f"nt-base{n}")[0])[0]
        same = _secs(build(one(nt(four[:n], label_style="icon")), f"nt-icon{n}")[0])[0]
        assert base == same, f"label_style: icon は書かないのと同じ（{n}個）"
        assert "notice-tag" not in base
        assert "notice-tag" in _secs(build(one(nt(four[:n], label_style="chip")), f"nt-chip{n}")[0])[0]
        if n < 4:
            assert 'padding:' not in base.split('class="nt-main"', 1)[1].split(">", 1)[0] and "gap:24px" in base, f"1〜3個は余白も枠のあいだも今のまま（{n}個）"
    # 入らなければ rc=2 で、縮める所が分かる
    long = [dict(it, title="とても長い題を二行に折り返すくらいまで書いてみる" * 2, body="本文も長く書く" * 12, points=["箇条を書く"] * 4) for it in four]
    expect_error(one(nt(long)), "nt4-over", "入らない", "短く")
    for bad in ("nazo", "", None, 1, ["chip"], True):
        expect_error(one(nt(four, label_style=bad)), "nt-ls-bad", "label_style")
    expect_error(one(nt([dict(four[3], label="とても長い種類の語を札に入れる" * 8)], label_style="chip")), "nt-ls-wide", "label")


def case_retired():
    """退役の仕組み（ADR 0021）: 型は RETIRED_TYPES に入れると、今までどおり描くが警告し、deck-format.md には載せない。
    変化形 look.surface: white は line と同じに描き、スタイルを読んだとき注意を出す。今、退役した型は無い。"""
    assert T.RETIRED_TYPES == {}, "今は退役した型が無い（足したら、deck-format.md に載っていないこと・image-slide の TYPES にも載っていないことをここで見る）"
    fmt = (SKILL / "references" / "deck-format.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([a-z-]+)` \|", fmt[fmt.index("## 型の一覧"):fmt.index("## 型ごとのキー")], re.M))
    assert listed == set(T.TYPES) - set(T.RETIRED_TYPES), listed ^ set(T.TYPES)
    T.RETIRED_TYPES["pairs"] = "代わりは table"
    try:
        _, info = build(one({"type": "pairs", "headline": "h", "items": [{"label": "a", "before": "x", "after": "y"}, {"label": "b", "before": "x", "after": "y"}]}), "retired-type")
        assert any("退役" in w and "pairs" in w for w in info["warnings"]), info["warnings"]
    finally:
        del T.RETIRED_TYPES["pairs"]
    import deck_style as DS
    sc = {"title": "t", "slides": [{"type": "cards", "headline": "h", "items": [{"title": "a"}, {"title": "b"}]}]}
    h_white, info = build(sc, "retired-white", style=_style("w", "name: w\nlook: {surface: white}\n"))
    assert any("退役" in w and "white" in w for w in info["warnings"]), info["warnings"]
    h_line, info = build(sc, "retired-line", style=_style("l", "name: l\nlook: {surface: line}\n"))
    assert info["warnings"] == [], info["warnings"]
    css = lambda p: re.search(r"/\* look \*/|\.card, \.cmp, \.kpi, \.callout \{[^}]*\}", pathlib.Path(p).read_text(encoding="utf-8")).group(0)
    assert css(h_white) == css(h_line) and "box-shadow" not in css(h_white), "white は line と同じに描く（影を出さない）"
    assert DS.RETIRED_LOOKS == {("surface", "white"): "line"}


def case_no_morph():
    """モーフは退役（ADR 0023）: 切り替えの既定は全部 fade（handout は none）。台本・スタイルに morph が書いてあれば fade で描いて警告する。
    PPTX にモーフは出ない。"""
    sc = {"title": "t", "slides": [{"type": "statement", "headline": "a"}, {"type": "statement", "headline": "b"},
                                   {"type": "statement", "headline": "c", "transition": "none"}, {"type": "statement", "headline": "d", "transition": "morph"}]}
    _, info = build(sc, "nomorph")
    assert [x["transition"] for x in info["slides"]] == ["fade", "fade", "none", "fade"], info["slides"]
    ws = [w for w in info["warnings"] if "退役" in w and "morph" in w]
    assert len(ws) == 1 and ws[0].startswith("4枚目"), info["warnings"]
    _, info = build(sc, "nomorph-handout", mode="handout")
    assert {x["transition"] for x in info["slides"]} == {"none"}
    _, info = build({"title": "t", "slides": sc["slides"][:2]}, "nomorph-style", style=_style("m", "name: m\nmotion: {transition: morph}\n"))
    assert [x["transition"] for x in info["slides"]] == ["fade", "fade"] and any("motion.transition" in w and "退役" in w for w in info["warnings"]), info
    import deck_style as DS
    assert DS.TRANSITIONS == ("fade", "none") and DS.RETIRED_TRANSITIONS == {"morph": "fade"} and deck.RETIRED_TRANSITIONS == {"morph": "fade"}
    assert "morph" not in (STYLES_VISUAL := (SKILL / "styles" / "visual.yaml").read_text(encoding="utf-8")), "組み込みのスタイルに morph が残っている"
    import pptx_writer as PW
    assert not hasattr(PW, "MORPH"), "モーフの書き出しが残っている"
    fmt = (SKILL / "references" / "deck-format.md").read_text(encoding="utf-8")
    row = next(l for l in fmt.splitlines() if l.startswith("| `transition` |"))
    assert "退役" in row and "`fade`（既定）" in row, row


def _sum_table(summary, **kw):
    return {"type": "table", "headline": "h", "columns": ["a", "b", "c"], "rows": [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]], "summary": summary, **kw}


def _sum_card(i, **kw):
    return {"label": f"指標{i}", "value": str(i), "unit": "件", **kw}


def case_table_summary():
    """table の summary: 1〜4枚・幅は本文を枚数で等分・カードは段1・表と凡例は段2・誤りは位置付きで rc=2・書かなければ今のまま。"""
    cws = {1: 1680, 2: 816, 3: 528, 4: 384}
    for n in (1, 2, 3, 4):
        s = _secs(build(one(_sum_table([_sum_card(i) for i in range(n)]), ["", "", ""]), f"ts-{n}")[0])[0]
        assert [int(w) for w in re.findall(r'class="kpi" style="width:(\d+)px; padding:24px"', s)] == [cws[n]] * n, f"{n}枚は本文の幅を等分"
        assert re.findall(r'class="kpi" [^>]*data-step="(\d)" data-fx="pop"', s) == ["1"] * n, "カードは段1で順に出る"
        assert 'class="table" style="left:120px; top:' in s and re.search(r'class="table"[^>]*data-step="2"', s), "表は段2"
        assert s.index('class="row"') < s.index('class="table"'), "カードは表の上"
    msgs = {"0": [], "5": [_sum_card(i) for i in range(5)]}
    expect_error(one(_sum_table(msgs["0"])), "ts-zero", "summary は 1〜4 個（0 個）")
    expect_error(one(_sum_table(msgs["5"])), "ts-five", "summary は 1〜4 個（5 個）")
    expect_error(one(_sum_table("x")), "ts-str", "summary は配列で書く")
    for key in ("accent", "badge"):
        expect_error(one(_sum_table([_sum_card(1), _sum_card(2, **{key: True})])), f"ts-{key}", "summary の 2 個目の accent / badge は使えない")
    expect_error(one(_sum_table([_sum_card(1, color="red")])), "ts-key", "summary の 1 個目に知らないキー: color")
    expect_error(one(_sum_table([{"label": "a"}])), "ts-value", "summary の 1 個目に value が無い")
    expect_error(one(_sum_table([_sum_card(1, trend="rise")])), "ts-trend", "summary の 1 個目の trend は up / down / flat")
    expect_error(one(_sum_table([_sum_card(1, tone="warn")])), "ts-tone", "summary の 1 個目の tone は good / bad / neutral")
    expect_error(one(_sum_table([_sum_card(1, group={"label": "x"})])), "ts-group", "summary の 1 個目の group は label と tone")
    # tone・trend・delta・group は kpi と同じ見た目
    full = [_sum_card(1, delta="+3", trend="up"), _sum_card(2, delta="−1", trend="up", tone="bad"), _sum_card(3, group={"label": "自動", "tone": "b"}), _sum_card(4)]
    s = _secs(build(one(_sum_table(full)), "ts-look")[0])[0]
    assert re.findall(r'class="kpi-delta tone-(\w+)"', s) == ["good", "bad"] and ">▲ +3<" in s and ">▲ −1<" in s
    assert 'class="kpi group-b"' in s and 'class="chip group group-b">自動<' in s
    # 4枚で delta が1行に入らなければ全部のカードの段が下がり（同じ役は同じ大きさ）、下げても入らなければ rc=2
    wide = [_sum_card(i, delta="+3件（前月比 6件増）", trend="up") for i in range(4)]
    s = _secs(build(one(_sum_table(wide)), "ts-drop")[0])[0]
    sizes = set(re.findall(r'class="kpi-delta[^"]*" style="font-size:(\d+)px', s))
    assert sizes == {"24"}, f"delta は 24px まで下りる: {sizes}"
    s0 = _secs(build(one(_sum_table([_sum_card(i, delta="+3件", trend="up") for i in range(4)])), "ts-nodrop")[0])[0]
    assert set(re.findall(r'class="kpi-delta[^"]*" style="font-size:(\d+)px', s0)) == {"28"}, "入るなら下げない"
    expect_error(one(_sum_table([_sum_card(i, delta="あ" * 14) for i in range(4)])), "ts-wide", "summary の 1 個目の delta")
    expect_error(one(_sum_table([_sum_card(i, value="9" * 14) for i in range(4)])), "ts-longvalue", "summary の 1 個目の value が長すぎて入らない")
    # 高さ: 帯は min(384, 使える高さ) まで。表の下の凡例・source・takeaway を含めて入らなければ rc=2（縮める所を言う）
    tight = _sum_table([_sum_card(i, note="注" * 28) for i in range(4)], rows=[["1", "2", "3"]] * 7, legend=[{"tone": "good", "text": "良"}], source="x", takeaway="結論",
                       kicker="k", sub="リード文を長く" * 6)
    expect_error(one(tight), "ts-tight", "summary を置くと表が入らない", "rows を減らす", "summary の label・note を短くする")
    # 複合: group・tone・align・dim・legend・source・takeaway を全部書いても組めて、最小の行の高さ（48px・8 の倍数）を守る
    combo = _sum_table([_sum_card(1, delta="+3", trend="up", group={"label": "自動", "tone": "a"}), _sum_card(2, tone="bad", delta="x")],
                       rows=[["1", "2", {"text": "3", "tone": "warn"}]], align=["left", "right", "center"], dim_rows=[1], highlight=2,
                       legend=[{"tone": "warn", "text": "注意"}, {"tone": "dim", "text": "見送り"}], source="出典", takeaway="結論")
    path = build(one(combo), "ts-combo")[0]
    s = _secs(path)[0]
    rhs = [int(v) for v in re.findall(r'class="tr th?" style="height:(\d+)px', s)]
    assert rhs and min(rhs) >= 48 and all(v % 8 == 0 for v in rhs), rhs
    y = lambda cls: float(re.search(rf'class="{cls}"[^>]*?top:([\d.]+)px', s).group(1))
    assert y("table") > y("row") + 100 and y("tone-legend") > y("table") and y("source") > y("tone-legend"), "上から 帯 → 表 → 凡例 → source"
    assert "結論" in s, "takeaway も入る（帯は s.bottom を上げてあるので二重に引かない）"
    # takeaway なしなら 3 行まで入る（上の組と同じ帯・凡例・source）
    no_take = {k: v for k, v in combo.items() if k != "takeaway"}
    s3 = _secs(build(one(dict(no_take, rows=[["1", "2", "3"]] * 3, dim_rows=[2])), "ts-combo3")[0])[0]
    assert min(int(v) for v in re.findall(r'class="tr th?" style="height:(\d+)px', s3)) >= 48
    # summary を書かない table は今までどおり: 1クリック・kpi の部品もカードの行も出ない・段は 1 だけ
    old = _secs(build(one({k: v for k, v in _sum_table([]).items() if k != "summary"}, ["", ""]), "ts-old")[0])[0]
    assert "kpi" not in old and 'class="row"' not in old
    assert set(re.findall(r'data-step="(\d+)"', old)) == {"0", "1"}, "表は段1だけ（見出しは 0）"


def case_table_summary_big():
    """summary のある小さな表を大きく組むと、カードの文字は上るが上限（数字 64・label と delta 36・note 28）と帯の上限 384px を守り、表と重ならない（group 混在でも）。"""
    plain = [_sum_card(1, delta="+1", trend="up"), _sum_card(2, delta="−1", trend="down")]
    mixed = [_sum_card(1, delta="+1", trend="up", note="短い", group={"label": "自動", "tone": "a"}), _sum_card(2, delta="−1", trend="down")]
    caps = {"value": 64, "label": 36, "delta": 36, "note": 28}
    for name, summary in (("plain", plain), ("mixed", mixed)):
        small = {"type": "table", "headline": "h", "columns": ["a", "b"], "rows": [["1", "2"]], "summary": summary}
        s = _secs(build(one(small, ["", "", ""]), f"tsb-{name}")[0])[0]
        sizes = lambda role: [int(v) for v in re.findall(rf'class="kpi-{role}[^"]*" style="font-size:(\d+)px', s)]
        for role, cap in caps.items():
            got = sizes(role)
            assert not got or (max(got) <= cap and len(set(got)) == 1), (name, role, got)
        if name == "plain":
            assert sizes("label")[0] > 28, "小さな表は大きく組まれる（この試験が上限を見る意味がある）"
        y = lambda cls: float(re.search(rf'class="{cls}"[^>]*?top:([\d.]+)px', s).group(1))
        band = y("table") - T.SUMMARY_GAP - y("row")
        assert 0 < band <= T.SUMMARY_MAX_H, f"{name}: 帯の高さが上限を越えた: {band}"
        heights = [float(v) for v in re.findall(r'class="kpi[^"]*" style="[^"]*?height:([\d.]+)px', s)]
        assert all(h <= T.SUMMARY_MAX_H for h in heights), (name, heights)


def case_table_summary_clicks():
    """summary のある table の段の数: 2クリック（summary なしは1）・属性・ノートの［クリック］・handout・steps: false・talk の照合。"""
    tbl = _sum_table([_sum_card(1), _sum_card(2)], legend=[{"tone": "good", "text": "良"}], source="出典")
    plain = {k: v for k, v in tbl.items() if k not in ("summary", "legend")}
    sc = {"title": "t", "slides": [dict(tbl, talk=["a", "b", "c"]), dict(plain, talk=["a", "b"])]}
    path, info = build(sc, "tsc")
    assert [i["clicks"] for i in info["slides"]] == [2, 1], info["slides"]
    secs = parse(path)
    assert sorted({v for v in secs[0]["steps"] if v >= 1}) == [1, 2] and sorted({v for v in secs[1]["steps"] if v >= 1}) == [1]
    assert secs[0]["notes"].count("［クリック］") == 2 and secs[1]["notes"].count("［クリック］") == 1
    assert all(f in T.FX for sec in secs for f in sec["fx"]) and all(sec["nested"] == 0 for sec in secs)
    s = _secs(path)[0]
    assert len(re.findall(r'data-step="2"', s)) == 2, "段2は表と凡例"
    expect_error({"title": "t", "slides": [dict(tbl, talk=["a", "b"])]}, "tsc-talk", "table", "talk が 2 個", "3 個")
    sc2 = {"title": "t", "slides": [dict(tbl, talk=["a", "b", "c"])], "mode": "handout"}
    hpath, hinfo = build(sc2, "tsc-handout")
    html = hpath.read_text(encoding="utf-8")
    assert hinfo["slides"][0]["clicks"] == 0 and "data-step=" not in html and "data-fx=" not in html, "handout は動きなし"
    assert 'class="kpi"' in html and 'class="table"' in html
    flat = dict(tbl, steps=False, talk=["全部"])
    fpath, finfo = build({"title": "t", "slides": [flat]}, "tsc-flat")
    assert finfo["slides"][0]["clicks"] == 0 and set(parse(fpath)[0]["steps"]) == {0}, "steps: false は全部 step 0"
    expect_error({"title": "t", "slides": [dict(tbl, steps=False, talk=["a", "b", "c"])]}, "tsc-flat-bad", "talk が 3 個", "クリック 0 回")


def case_combo_samples():
    """deck-format.md の「組み合わせの見本」の台本は、そのまま組める（rc=0）・1ページに収まる・SKILL.md と notice の行からたどれる。"""
    fmt = (SKILL / "references" / "deck-format.md").read_text(encoding="utf-8")
    sec = fmt.split("\n## 組み合わせの見本\n", 1)[1].split("\n## ", 1)[0]
    blocks = re.findall(r"```json\n(.*?)\n```", sec, re.S)
    assert blocks, "組み合わせの見本に台本が無い"
    for i, b in enumerate(blocks):
        slide = json.loads(b)
        _, info = build(one(slide), f"combo-{i}")
        assert len(info["slides"]) == 1 and not info["warnings"], info["warnings"]
    assert json.loads(blocks[0])["type"] == "notice" and len(json.loads(blocks[0])["items"]) == 4
    notice_row = next(l for l in fmt.splitlines() if l.startswith("| `notice` |") and "(#組み合わせの見本)" in l)
    assert notice_row
    assert "「組み合わせの見本」" in (SKILL / "SKILL.md").read_text(encoding="utf-8")


# --- 見た目の仕上げ（ADR 0029）: 表の幅・カタカナ・構成図・主役を大きく・look の新しい値・色の役 -------------------------

def case_table_widths_by_content():
    """表の列の幅: widths が無ければ中身（列で一番長いセル）で決める・書けば書いたとおりの比・どの列も等分の半分より狭くしない。"""
    tbl = {"type": "table", "headline": "h", "columns": ["項目", "内容", "担当"],
           "rows": [["a", "議題を前日までに予定表へ1行で書き、決めたいことも添える", "設計"],
                    ["b", "会議の終わりに決まったことと担当と期限を読み上げる", "営業"]]}
    ws = [int(w) for w in re.findall(r'class="td[^"]*" style="width:(\d+)px', _secs(build(one(tbl), "tw-auto")[0])[0])][:3]
    assert ws[1] > ws[0] * 2 and ws[1] > ws[2] * 2, f"長い文の列が広くならない: {ws}"
    assert min(ws) >= T.CW / 3 * 0.5 - 1, f"等分の半分より狭い列がある: {ws}"
    assert abs(sum(ws) - T.CW) <= 3, ws
    ws = [int(w) for w in re.findall(r'class="td[^"]*" style="width:(\d+)px', _secs(build(one(dict(tbl, widths=[1, 1, 2])), "tw-given")[0])[0])][:3]
    assert ws == [round(T.CW / 4), round(T.CW / 4), round(T.CW / 2)], f"widths を書けば書いたとおり: {ws}"
    same = dict(tbl, rows=[["あいう", "えおか", "きくけ"], ["さしす", "せそた", "ちつて"]])
    ws = [int(w) for w in re.findall(r'class="td[^"]*" style="width:(\d+)px', _secs(build(one(same), "tw-even")[0])[0])][:3]
    assert max(ws) - min(ws) <= 3, f"同じ長さの列は等分: {ws}"
    expect_error(one(dict(tbl, widths=[1, 2])), "tw-bad", "widths")
    expect_error(one(dict(tbl, widths=[1, 0, 1])), "tw-bad0", "widths")


def case_table_align_default():
    """表の揃えの既定: 1列目は左・全部のセルが全角8字までの列は中央・文が入る列は左。align を書けば書いたとおり。"""
    tbl = {"type": "table", "headline": "h", "columns": ["項目", "短い列", "文の列"],
           "rows": [["a", "30分", "議題を前日までに予定表へ1行で書き、決めたいことも添える"],
                    ["b", "25分", "会議の終わりに決まったことと担当と期限を読み上げる"]]}
    just = lambda html: re.findall(r'class="td[^"]*" style="width:\d+px; justify-content:([a-z-]+)"', html)[:3]
    assert just(_secs(build(one(tbl), "ta-def")[0])[0]) == ["flex-start", "center", "flex-start"], "1列目左・短い列は中央・文の列は左"
    assert just(_secs(build(one(dict(tbl, align=["right", "left", "center"])), "ta-given")[0])[0]) == ["flex-end", "flex-start", "center"]


def case_katakana_not_split():
    """カタカナの語（3字以上）は途中で折らない: 入る段へ下りる・どの段でも入らなければ今までどおり語の途中で折る（止めない）。"""
    word = "メタモルフォーゼ"
    assert T.count_lines(word, 57, 400, True, split_kana=False) == 99, "折らない数え方では、幅を越える語は 99"
    assert T.count_lines(word, 57, 400, True, split_kana=True) == 2, "折る数え方なら 2 行"
    size, n = T.fit(word, 57, 400, what="題", lines=2, bold=True, min_size=28)
    assert size < 57 and n == 1 and T.count_lines(word, size, 400, True, split_kana=False) == 1, (size, n)
    size, n = T.fit(word, 57, 200, what="題", lines=4, bold=True, min_size=45)
    assert (size, n) == (57, 3), "どの段でも入らなければ、大きさは下げず語の途中で折る（止めない）"
    assert T.count_lines("ツー", 57, 100, True, split_kana=False) == 2, "2字の語は対象外（今までどおり折れる）"
    assert T.count_lines("ABCDEFG", 57, 100, True) == 99 and T.count_lines("ABCDEFG", 28, 400, True) == 1, "英数字の語は今までどおり"


def _layers_spec(**kw) -> dict:
    spec = {"type": "layers", "headline": "h",
            "rows": [{"label": "現場", "items": [{"id": "a", "title": "議題", "body": "前日に"}, {"id": "b", "title": "会議", "accent": True},
                                               {"id": "c", "title": "記録"}]},
                     {"label": "事務局", "items": [{"id": "d", "title": "集計", "col": 2}, {"id": "e", "title": "報告", "col": 3}]}],
            "links": [{"from": "a", "to": "b", "label": "前日"}, {"from": "c", "to": "b", "accent": True},
                      {"from": "c", "to": "d", "label": "その日"}, {"from": "d", "to": "e", "both": True}]}
    return spec | kw


def case_layers():
    """layers（構成図）: 層と箱の数・矢印の向き・col で縦にそろう・クリックは1・誤り（知らない id・隣でない矢印・col の重なりなど）は rc=2。"""
    path, info = build(one(_layers_spec(), ["x", "y"]), "layers-ok")
    s = _secs(path)[0]
    assert info["slides"][0]["clicks"] == 1 and info["warnings"] == [], info
    assert s.count('class="dg-band"') == 2 and s.count('class="dg-node') == 5 and s.count('class="dg-node hl"') == 1, "層 2・箱 5・強める箱 1"
    assert s.count('class="dg-rowlabel"') == 2 and s.count("<polygon") == 5, "矢印の頭は 4 本＋両向きの 1"
    nodes = [(int(x), int(y)) for x, y in re.findall(r'class="dg-node[^"]*" style="left:(\d+)px; top:(\d+)px', s)]
    assert nodes[3][0] == nodes[1][0] and nodes[4][0] == nodes[2][0], f"同じ col は縦にそろう: {nodes}"
    assert nodes[0][1] == nodes[1][1] == nodes[2][1] < nodes[3][1] == nodes[4][1], f"層ごとに高さがそろう: {nodes}"
    nw = int(re.search(r'class="dg-node[^"]*" style="left:\d+px; top:\d+px; width:(\d+)px', s).group(1))
    heads = [[tuple(float(v) for v in pt.split(",")) for pt in m.split()] for m in re.findall(r'<polygon points="([^"]*)"', s)]
    tip = lambda h: h[0]
    right_of = lambda i: nodes[i][0] + nw
    assert abs(tip(heads[0])[0] - (nodes[1][0] - 8)) < 1.5, "a → b: 矢印の先は b の左"
    assert abs(tip(heads[1])[0] - (right_of(1) + 8)) < 1.5, "c → b（右から左）: 矢印の先は b の右"
    assert tip(heads[2])[1] > nodes[2][1], "c → d は上の層から下の層へ: 矢印の先は下向き（d の上）"
    assert "stroke:var(--accent-mark)" in s and "stroke-width:4" in s, "強める矢印は差し色の太い線"
    assert s.count(f"left:{nodes[3][0]}px") >= 1 and ">前日<" in s and ">その日<" in s, "矢印の名札"
    steps = set(re.findall(r'data-step="(\d+)"', s))
    assert steps == {"0", "1"}, f"箱も矢印も1クリック: {steps}"
    # 誤り（rc=2）
    link = lambda **k: _layers_spec(links=[dict({"from": "a", "to": "b"}, **k)])
    for sc, name, words in (
            (link(to="zzz"), "ly-unknown", ("links の 1 本目", "zzz")),
            (_layers_spec(links=[{"from": "a", "to": "c"}]), "ly-skip", ("隣どうし",)),
            (_layers_spec(links=[{"from": "a", "to": "a"}]), "ly-self", ("違う箱",)),
            (_layers_spec(rows=[{"items": [{"id": "a", "title": "x"}]}, {"items": [{"id": "b", "title": "y"}]}, {"items": [{"id": "c", "title": "z"}]}],
                          links=[{"from": "a", "to": "c"}]), "ly-rows", ("隣の層",)),
            (_layers_spec(rows=[{"items": [{"id": "a", "title": "x", "col": 2}, {"id": "b", "title": "y", "col": 2}]}], links=[]), "ly-col", ("col 2",)),
            (_layers_spec(rows=[{"items": [{"id": "a", "title": "x"}, {"id": "a", "title": "y"}]}], links=[]), "ly-dup", ("id",)),
            (_layers_spec(rows=[{"items": [{"title": "x"}]}], links=[]), "ly-noid", ("id",)),
            (_layers_spec(rows=[], links=[]), "ly-empty", ("rows は 1〜3 段",)),
            (_layers_spec(rows=[{"items": [{"id": str(i), "title": "x"}]} for i in range(4)], links=[]), "ly-four", ("rows は 1〜3 段",)),
            (_layers_spec(rows=[{"items": [{"id": str(i), "title": "x"} for i in range(5)]}], links=[]), "ly-five", ("1〜4",)),
            (_layers_spec(links=[{"from": "a", "to": "b", "color": "red"}]), "ly-key", ("知らないキー",)),
            (_layers_spec(rows=[{"items": [{"id": "a", "title": "x"}], "mark": 1}], links=[]), "ly-rowkey", ("知らないキー",))):
        expect_error(one(sc), name, *words)
    expect_error(one(_layers_spec(), ["x"]), "ly-talk", "talk")


def case_layout_lead():
    """layout: lead（cards・summary）: cards は accent の項目（無ければ1つ目）が主役・summary は1つ目の根拠・クリックの数は変わらない・
    写真つきカードと知らない値は rc=2。"""
    items = [{"label": "ア", "title": "あ", "body": "b1"}, {"label": "イ", "title": "い", "body": "b2", "accent": True},
             {"label": "ウ", "title": "う", "body": "b3"}]
    cards = {"type": "cards", "headline": "h", "items": items}
    base = build(one(cards), "lead-row")[1]["slides"][0]["clicks"]
    path, info = build(one(dict(cards, layout="lead")), "lead-cards")
    s = _secs(path)[0]
    assert info["slides"][0]["clicks"] == base == 3, (base, info["slides"][0])
    panel = re.search(r'class="lead-panel".*?(?=<div class="card lead-row")', s, re.S).group(0)
    assert ">い<" in panel and ">あ<" not in panel and ">う<" not in panel, "accent の項目が主役"
    assert s.count('class="card lead-row"') == 2 and s.index(">あ<") > s.index(">い<"), "残りは右に積む"
    assert 'class="card hl"' not in s, "主役の面と強調の箱を二重にしない"
    plain_first = _secs(build(one(dict(cards, layout="lead", items=[{k: v for k, v in it.items() if k != "accent"} for it in items])), "lead-first")[0])[0]
    assert ">あ<" in re.search(r'class="lead-panel".*?(?=<div class="card lead-row")', plain_first, re.S).group(0), "accent が無ければ1つ目"
    summ = {"type": "summary", "headline": "h", "points": [{"value": "11.5", "title": "あ", "body": "b1"}, {"title": "い"}, {"title": "う"}]}
    sb = build(one(summ), "lead-sum-row")[1]["slides"][0]["clicks"]
    path, info = build(one(dict(summ, layout="lead")), "lead-sum")
    s = _secs(path)[0]
    assert info["slides"][0]["clicks"] == sb == 1, (sb, info["slides"][0])
    panel = re.search(r'class="lead-panel".*?(?=<div class="card lead-row")', s, re.S).group(0)
    assert ">11.5<" in panel and ">あ<" in panel and ">い<" not in panel, "summary は1つ目の根拠が主役（value は大きく）"
    assert s.count('class="card lead-row"') == 2
    imaged = dict(cards, layout="lead", items=[dict(it, image={"prompt": "p"}) for it in items])
    expect_error(one(imaged), "lead-img", "layout: lead", "写真")
    expect_error(one(dict(cards, layout="grid")), "lead-bad", "layout")
    expect_error(one(dict(summ, layout="grid")), "lead-sum-bad", "layout")
    expect_error(one(dict(cards, layout="lead", items=[dict(items[0], badge="新"), items[1], items[2]])), "lead-badge", "badge")


def _style_file(name: str, text: str) -> pathlib.Path:
    p = TMP / f"{name}.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def case_look_new_values():
    """look の新しい値（surface: flat・fill: stretch・title: solid／deep・emphasis／statement: deep・deep: on）: CSS が出る・
    classic は空・standard は出る・知らない値は rc=2（deep: on は YAML の真偽値でも読める）。"""
    DS = deck.DS
    assert DS.look_css({}) == "" and DS.load("classic")["look"] == {}, "classic は look なし"
    std = DS.load("standard")["look"]
    assert std == {"surface": "flat", "band": "none", "fill": "stretch", "emphasis": "strong", "statement": "deep", "title": "solid", "deep": "on"}, std
    flat, solid, deep_title = DS.look_css({"surface": "flat"}), DS.look_css({"title": "solid"}), DS.look_css({"title": "deep"})
    assert ".card-bar { display: none; }" in flat and "--disc-ground: var(--primary-fill)" in flat, "flat"
    assert "var(--primary-fill)" in solid and 'section[data-type="cover"]' in solid and "var(--on-primary-fill)" in solid, "solid"
    assert "var(--deep)" in deep_title and "var(--deep-pop)" in deep_title and "primary-fill" not in deep_title, "title: deep"
    emph = DS.look_css({"emphasis": "deep"})
    assert "var(--deep)" in emph and "var(--on-deep)" in emph and "accent-strong" not in emph, "emphasis: deep"
    assert "var(--accent-strong)" in DS.look_css({"emphasis": "strong"}), "emphasis: strong は今までどおり"
    stmt = DS.look_css({"statement": "deep"})
    assert "var(--deep)" in stmt and "primary-strong" not in stmt, "statement: deep"
    deep_on = DS.look_css({"deep": "on"})
    assert 'section[data-type="closing"]' in deep_on and ".tr.th" in deep_on and ".take" in deep_on, "deep: on"
    assert DS.look_css({"fill": "stretch"}) == "", "fill は組みの値（CSS は出さない）"
    # 組んだ HTML: classic には出ず、standard には出る
    def style_of(name, **kw):
        h = build(one({"type": "cards", "headline": "h", "items": [{"title": "a"}, {"title": "b"}]}), name, **kw)[0].read_text(encoding="utf-8")
        return h[h.index("<style"):h.index("</style>")]
    classic_css, standard_css = style_of("lk-classic", style="classic"), style_of("lk-standard")
    assert "--disc-ground:" not in classic_css and ".card-bar { display: none; }" not in classic_css and "var(--deep)" not in classic_css, "classic には look の CSS が出ない"
    assert "--disc-ground: var(--primary-fill)" in standard_css and ".card-bar { display: none; }" in standard_css and "var(--deep)" in standard_css, "standard には出る"
    # 知らない値は rc=2・deep は引用符なしの on でも読める
    for item, bad in (("surface", "glass"), ("fill", "grow"), ("title", "gradient"), ("emphasis", "neon"), ("statement", "neon"), ("deep", "maybe")):
        f = _style_file(f"lk-bad-{item}", f"name: x\nlook:\n  {item}: {bad}\n")
        try:
            DS.load(str(f))
            raise AssertionError(f"{item}: {bad} が通った")
        except T.DeckError as e:
            assert f"look.{item}" in str(e), e
    f = _style_file("lk-on", "name: x\nlook:\n  deep: on\n  fill: stretch\n")
    assert DS.load(str(f))["look"] == {"deep": "on", "fill": "stretch"}, "引用符なしの on（YAML の真偽値）も読める"
    f = _style_file("lk-off", "name: x\nlook:\n  deep: off\n")
    assert DS.load(str(f))["look"] == {"deep": "off"}
    assert DS.look_css({"deep": "off"}) == ""
    # 雛形がそのまま読める
    assert DS.load(str(SKILL / "templates" / "custom-style.example.yaml"))["look"]["fill"] == "content"


def case_look_emphasis_on_fills():
    """色の面の上の強調の語（**語**）・出典: 面と同じ色相の強調の色のまま残さない（査読の指摘。表紙の副題・締め・表の見出し・結論の帯）。
    look の CSS に面ごとの規則があり、その色の役と面が 4.5:1（3 テーマと明るい主役の色）。layers の矢印の型違い・表の幅の合計も見る。"""
    DS = deck.DS
    std = DS.look_css(DS.load("standard")["look"])
    for rule in (".cover-subtitle em, .sec-subtitle em { color: var(--on-primary-fill); font-weight: 700; }",
                 ".take-t em, .tr.th .td em { color: var(--deep-pop); }",
                 ".tr.th .td.hl em { color: var(--on-accent); }",
                 'section[data-type="closing"] .source { color: var(--on-deep); }',
                 'section[data-type="closing"] em { color: var(--deep-pop); }',
                 'section[data-type="closing"] .contact em { color: var(--on-primary); }'):
        assert rule in std, f"standard の look に無い: {rule}"
    assert ".cover-subtitle em" in DS.look_css({"title": "deep"}) and "var(--deep-pop)" in DS.look_css({"title": "deep"})
    flat = DS.look_css({"surface": "flat"})
    assert ".take-t, .take-t em { color: var(--on-primary-fill); }" in flat and ".tr.th .td div, .tr.th .td em { color: var(--on-primary-fill); }" in flat, "flat だけのとき"
    # 後に出る規則が勝つ: standard では、濃い面の規則が淡い面の規則より後ろ
    assert std.index(".take-t em, .tr.th .td em { color: var(--deep-pop); }") > std.index(".take-t, .take-t em { color: var(--on-primary-fill); }")
    # 全面の写真（hero）の表紙・章扉: 題は幕の上に載るので、幕の上の色（検査済み）が、面の上の色の規則より後ろにあって勝つ（再査読の指摘）
    for look in ({"title": "solid"}, {"title": "deep"}, DS.load("standard")["look"]):
        css = DS.look_css(look)
        last_title = max(css.rfind(".cover-subtitle em"), css.rfind(".cover-rule { background-color: var(--on-primary-fill); }"), css.rfind(".sec-panel"))
        for rule in (".on-light .cover-title, .on-light .cover-subtitle, .on-light div, .on-light .sec-title, .on-light .sec-subtitle { color: var(--ink); }",
                     ".on-light em { color: var(--accent-on-light); }", ".on-light .cover-label, .on-light .sec-label, .on-light .sec-no { color: var(--primary-on-light); }",
                     ".on-dark .cover-title, .on-dark .cover-subtitle, .on-dark div, .on-dark .sec-title, .on-dark .sec-subtitle { color: #ffffff; }",
                     ".on-dark em { color: var(--accent-on-dark); }", ".on-dark .cover-label, .on-dark .sec-label, .on-dark .sec-no { color: var(--primary-on-dark); }"):
            assert rule in css and css.index(rule) > last_title, (look, rule)
    assert ".on-light" not in DS.look_css({"surface": "flat"}), "title の look が無ければ足さない"
    img = str(SKILL / "examples" / "images" / "s01-image.jpg")
    for tone, cls in (("light", "on-light"), ("dark", "on-dark")):       # 組んだ HTML: 題・日付の箱が幕の class を持つ（上の規則が効く相手）
        for spec in ({"type": "cover", "layout": "hero", "tone": tone, "title": "題の**強調**", "subtitle": "説明の**重要語**", "meta": ["日付"], "image": {"path": img}},
                     {"type": "section", "layout": "hero", "tone": tone, "number": "01", "title": "章の**題**", "subtitle": "副題の**語**", "image": {"path": img}}):
            h = build(one(spec), f"hero-{spec['type']}-{tone}")[0].read_text(encoding="utf-8")
            assert f'-text {cls}"' in h, (spec["type"], tone)
    tpl = (SKILL / "templates" / "deck.html").read_text(encoding="utf-8")
    assert ".tr.th .td.hl div, .tr.th .td.hl em { color: var(--on-accent); }" in tpl, "強める列の見出しの強調の語（どのスタイルでも）"
    for th in ({"background": "#ffffff", "ink": "#333333", "primary": "#006f83"}, {"background": "#ffffff", "ink": "#1a1a1a", "primary": "#f5a623", "accent": "#399dd1"},
               {"background": "#faf7f0", "ink": "#333333", "primary": "#ac3e00"}, {"background": "#ffffff", "ink": "#333333", "primary": "#197a4b"}):
        pal = deck.palette(th)
        for fg, bg in (("on-primary-fill", "primary-fill"), ("deep-pop", "deep"), ("on-deep", "deep"), ("on-accent", "accent-strong"), ("on-primary", "primary-strong")):
            assert deck.ratio(pal[fg], pal[bg]) >= 4.5 - 1e-9, (th["primary"], fg, bg, deck.ratio(pal[fg], pal[bg]))
    # 組んだ HTML に、強調の語と出典がそのまま出ている（色は上の規則が決める）
    h = build(one({"type": "closing", "headline": "締め", "items": [{"title": "**重要**な次の一手"}], "source": "出典"}), "em-closing")[0].read_text(encoding="utf-8")
    assert "<em>重要</em>" in h and 'class="source"' in h
    # layers: both・accent は真偽値だけ・links は書いたなら配列
    base = {"type": "layers", "headline": "h", "rows": [{"items": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]}]}
    for bad in ({"links": [{"from": "a", "to": "b", "both": "true"}]}, {"links": [{"from": "a", "to": "b", "accent": 1}]}, {"links": None}):
        try:
            build(one({**base, **bad}), "ly-bad")
            raise AssertionError(f"通った: {bad}")
        except T.DeckError:
            pass
    build(one({**base, "links": [{"from": "a", "to": "b", "both": True, "accent": False}]}), "ly-ok")
    build(one(base), "ly-nolinks")
    # 表: 列の幅の合計は、いつも本文の幅ちょうど（列ごとに丸めて 1px 越えない）
    import re as _re
    h = build(one({"type": "table", "headline": "h", "columns": ["a", "b", "c"], "rows": [["1", "2", "3"]], "widths": [1, 1, 7]}), "tb-sum")[0].read_text(encoding="utf-8")
    row = h[h.index('class="tr th"'):]
    ws = [int(v) for v in _re.findall(r'class="td[^"]*" style="width:(\d+)px', row[:row.index("</div></div></div>")])][:3]
    assert sum(ws) == T.CW, ws


def case_look_fill_stretch():
    """fill: stretch は並ぶ箱の高さを置ける高さへ伸ばす（8 の倍数・中身の 1.6 倍まで）。classic は伸ばさず、中身に沿う。"""
    cards = {"type": "cards", "headline": "h", "items": [{"title": "a", "body": "短い"}, {"title": "b", "body": "短い"}, {"title": "c", "body": "短い"}]}
    heights = lambda style: [int(v) for v in re.findall(r'class="card[^"]*" style="[^"]*height:(\d+)px', _secs(build(one(cards), f"fs-{style}", style=style)[0])[0])]
    std, classic = heights("standard"), heights("classic")
    assert len(std) == 3 and all(v % 8 == 0 for v in std) and len(set(std)) == 1, f"standard は同じ高さに伸ばす（8 の倍数）: {std}"
    assert classic == [], f"classic は高さを固定しない（中身に沿う）: {classic}"
    sl = T.Slide(1, {"type": "cards"}, deck.palette(deck.load_theme(deck.THEMES / "default.yaml")), flat=False, script_dir=TMP, assets=[], look={"fill": "stretch"})
    assert T.stretch_h(sl, 300, 1000) == 480 and T.stretch_h(sl, 300, 400) == 400 and T.stretch_h(sl, 300, 300) is None, "中身の 1.6 倍まで・置ける高さまで・伸ばせなければ None"
    sl = T.Slide(1, {"type": "cards"}, deck.palette(deck.load_theme(deck.THEMES / "default.yaml")), flat=False, script_dir=TMP, assets=[], look={})
    assert T.stretch_h(sl, 300, 1000) is None, "fill が無ければ伸ばさない"


def case_color_roles():
    """色の役（primary-fill・accent-fill・on-…-fill・deep・on-deep・deep-pop）が3つのテーマと、黄のような明るい主役の色で 4.5:1 を満たす。"""
    themes = {n: dict(deck.load_theme(deck.THEMES / f"{n}.yaml")) for n in ("default", "forest", "warm")}
    base = themes["default"]
    themes["yellow"] = dict(base, primary="#ffd400", accent="#f5c518")
    themes["pale-yellow"] = dict(base, primary="#fff176", accent="#ffe082")
    themes["navy"] = dict(base, primary="#0b1f4d", accent="#1f3b8a")
    for name, th in themes.items():
        p = deck.palette(th)
        for fg, bg in (("on-primary-fill", "primary-fill"), ("on-accent-fill", "accent-fill"), ("on-deep", "deep"), ("deep-pop", "deep")):
            r = deck.ratio(p[fg], p[bg])
            assert r >= 4.5, f"{name}: {fg} {p[fg]} × {bg} {p[bg]} は {r:.2f}:1（4.5 未満）"
        assert deck.contrast_problems(p) == [], (name, deck.contrast_problems(p))
        r_, g_, b_ = (int(p["deep"][i:i + 2], 16) for i in (1, 3, 5))
        assert max(r_, g_, b_) <= 64, f"{name}: deep はほぼ黒（{p['deep']}）"
    p = deck.palette(themes["yellow"])
    assert p["primary-fill"] == "#ffd400" and p["on-primary-fill"] != "#ffffff", "黄の面の上の文字は白にしない（明るい色の上は黒に寄せる）"
    p = deck.palette(base)
    assert p["primary-fill"] == base["primary"], "テーマの色そのまま（明度を動かさない）"


def _free(body: str, **kw) -> dict:
    return {"type": "free", "headline": "h", "body": body, **kw}


def case_free_parser():
    """自由なページ（型 free・ADR 0030）の解析器: 許した要素・属性・CSS だけを通す。外れたものは、理由つきで rc=2。"""
    import free_body as FB
    bad = {
        "独自のタグ": ('<icon name="users"/>', "書けない"),
        "自己終了の div": ('<div class="f-box"/>', "閉じる書き方"),
        "閉じない": ("<div><div>あ</div>", "閉じていない"),
        "閉じが合わない": ("<div><b>あ</div></b>", "合わない"),
        "position": ('<div style="position:absolute; left:0">あ</div>', "position"),
        "transform": ('<div style="transform: rotate(3deg)">あ</div>', "transform"),
        "色の直書き": ('<div style="color:#ff0000">あ</div>', "var(--名前)"),
        "知らない色の変数": ('<div style="color:var(--rule)">あ</div>', "var(--名前)"),
        "予備の値つきの var": ('<div style="color:var(--ink, #000)">あ</div>', "var(--名前)"),
        "段の外の大きさ": ('<div style="font-size:30px">あ</div>', "font-size"),
        "小さすぎる文字": ('<div style="font-size:16px">あ</div>', "font-size"),
        "段の外の行高": ('<div style="line-height:1.1">あ</div>', "line-height"),
        "読めない色の組": ('<div class="f-box deep"><div style="color:var(--ink)">あ</div></div>', "読めない"),
        "文字と箱の混在": ("<div>あ<div>い</div></div>", "包む"),
        "知らない class": ('<div class="card">あ</div>', "class card は無い"),
        "知らない属性": ('<div id="x">あ</div>', "属性 id"),
        "属性の on…": ('<div onclick="x()">あ</div>', "属性 onclick"),
        "url の入る style": ('<div style="background-image:url(x.png)">あ</div>', "background-image"),
        "コメント": ("<div>あ</div><!-- x -->", "コメント"),
        "一番外に文字": ("あ", "一番外"),
        "空": ("", "空"),
        "マスの範囲": ('<div class="f-cells" data-total="100" data-on="120" data-cols="10"></div>', "data-on"),
        "マスが多すぎる": ('<div class="f-cells" data-total="400" data-on="1" data-cols="10"></div>', "data-total"),
        "棒が上限を越える": ('<div class="f-bars" data-max="10"><div class="f-bar" data-value="12">a</div></div>', "越えている"),
        "棒の外の f-bar": ('<div class="f-bar" data-value="1">a</div>', "f-bars の中"),
        "無いアイコン": ('<span class="f-icon" data-icon="no-such"></span>', "data-icon"),
        "top の場所": ('<div class="f-box"><div>a</div><div class="f-chip top">札</div></div>', "最初の子"),
        "箱の種類が 2 つ": ('<div class="f-box deep fill"><div>a</div></div>', "1 つだけ"),
        "data-step を文字の中に": ('<div><span data-step="1">a</span></div>', "書けない"),
        "data-fx だけ": ('<div data-fx="pop"><div>a</div></div>', "data-step と一緒"),
    }
    for name, (body, word) in bad.items():
        msg = expect_error(one(_free(body)), "free-bad", word)
        assert "1枚目（free）" in msg, (name, msg)
    expect_error(one({"type": "free", "headline": "h"}), "free-nobody", "body")
    expect_error(one(_free("<div>a</div>", items=[])), "free-key", "知らないキー")
    pal = deck.palette({"background": "#ffffff", "ink": "#333333", "primary": "#006f83"})
    assert "mark-off" in pal and "deep-off" in pal, "塗っていない側の色の役"
    assert FB.visible_text('<div class="f-row"><div>割合<b>太字</b></div><div class="f-cells" data-total="100" data-on="21" data-cols="10"></div></div>') \
        == ["割合", "太字", "100 のうち 21"]


def case_free_render():
    """自由なページの組み: 枠はほかの型と同じ部品・面の箱ごとに文字の色が決まる・部品の展開・クリックの段は data-step から。"""
    body = ('<div class="f-row f-grow"><div class="f-box f-grow"><div class="f-label">淡い面</div><div>本文と<em>強調</em>と<b>太字</b></div>'
            '<span class="f-icon disc" data-icon="users"></span></div>'
            '<div class="f-box deep f-grow"><div class="f-chip top">札</div><div class="f-num">4.4</div><div class="f-note f-muted">注記</div></div>'
            '<div class="f-box fill f-grow"><div class="f-strong">主役の色の面</div>'
            '<div class="f-cells" data-total="20" data-on="5" data-cols="10"></div></div></div>')
    path, info = build(one(_free(body, kicker="k", source="出典", takeaway="結論の1行"), ["x"]), "free-ok")
    h = path.read_text(encoding="utf-8")
    sec = h.split("<section data-slide")[1]
    assert 'data-type="free"' in sec and 'class="kicker"' in sec and 'class="headline"' in sec and 'class="source"' in sec and 'class="take"' in sec, "枠はほかの型と同じ部品"
    assert 'class="f-body"' in sec and "data-free-body" in sec and "left:120px" in sec.split('class="f-body"')[1][:120].replace(" ", ""), "中身の箱"
    assert '<div class="f-box f-grow" style="background-color: var(--primary-soft);">' in sec
    assert '<div class="f-label" style="color: var(--ink);">淡い面</div>' in sec
    assert '<span style="color: var(--primary-text); font-weight: 700;">強調</span>' in sec and '<b style="font-weight: 700;">太字</b>' in sec
    assert 'class="f-icon-disc" style="width: 96px; height: 96px; background-color: var(--primary-fill);"' in sec and "<svg" in sec
    assert '<div class="f-box deep f-grow" style="background-color: var(--deep);">' in sec
    assert '<div class="f-chip f-tag" style="background-color: var(--accent-fill); color: var(--on-accent-fill);">札</div>' in sec, "箱の上の縁の札"
    assert '<div class="f-num" style="color: var(--deep-pop);">4.4</div>' in sec and '<div class="f-note f-muted" style="color: var(--on-deep);">注記</div>' in sec, "ほぼ黒の面の上の文字"
    assert '<div class="f-strong" style="color: var(--on-primary-fill);">主役の色の面</div>' in sec
    cells = sec[sec.index('class="f-cells"'):]
    assert 'data-name="マス（20 のうち 5）"' in cells and "repeat(10, 1fr)" in cells
    assert cells.count("background-color: var(--on-primary-fill);") == 5 and cells.count("background-color: var(--primary-soft);") >= 15, "塗った 5・塗っていない 15"
    assert "onclick" not in sec and "position:absolute" not in sec.split('class="f-body"')[1].replace(" ", "")[200:], "body に position を書かせない"
    # 棒: 長さは数に比例した flex の伸び（丸めない）・on の棒は差し色
    bars = '<div class="f-box f-grow"><div class="f-bars" data-max="21"><div class="f-bar on" data-value="21"><b>21%</b>導入前</div><div class="f-bar" data-value="6.5"><b>6.5%</b>3か月目</div></div></div>'
    h = build(one(_free(bars)), "free-bars")[0].read_text(encoding="utf-8")
    assert 'style="flex: 21 1 0px; background-color: var(--accent-fill);"' in h and 'style="flex: 6.5 1 0px; background-color: var(--primary-fill);"' in h
    assert 'style="flex: 14.5 1 0px;"' in h and 'class="f-bar-value" style="color: var(--primary-text);">6.5%</div>' in h and ">3か月目</div>" in h
    # クリックの段: data-step の 1 以上の値の種類の数（talk と照合）。配る資料・steps: false は型と同じ扱い
    steps = '<div class="f-row f-grow"><div class="f-box f-grow" data-step="1"><div>一</div></div><div class="f-box f-grow" data-step="2" data-fx="pop" data-name="二つ目"><div>二</div></div></div>'
    path, _ = build(one(_free(steps), ["a", "b", "c"]), "free-steps")
    sec = path.read_text(encoding="utf-8").split("<section data-slide")[1]
    assert 'data-step="1" data-fx="rise"' in sec and 'data-step="2" data-fx="pop" data-name="二つ目"' in sec and "［クリック］" in sec
    expect_error(one(_free(steps), ["a", "b"]), "free-talk", "talk")
    path, _ = build({**one(_free(steps), ["a", "b", "c"]), "mode": "handout"}, "free-handout")
    assert "data-step" not in path.read_text(encoding="utf-8").split("<section data-slide")[1].split("<aside")[0], "配る資料は動きなし"
    path, _ = build(one(_free(steps, steps=False), ["a"]), "free-flat")
    sec = path.read_text(encoding="utf-8").split("<section data-slide")[1]
    assert 'data-step="1"' not in sec and 'data-delay="600"' in sec and 'data-delay="1200"' in sec, "steps: false は遅れに直す"
    # 8 の倍数でない px は警告・部品が多すぎれば警告（止めない）
    _, info = build(one(_free('<div class="f-box" style="padding: 30px"><div>a</div></div>')), "free-warn")
    assert any("8 の倍数でない" in w for w in info["warnings"]), info["warnings"]
    many = "".join('<div class="f-cells" data-total="100" data-on="1" data-cols="20" style="width: 240px"></div>' for _ in range(3))
    _, info = build(one(_free(f'<div class="f-row f-grow">{many}</div>')), "free-many")
    assert any("部品が多い" in w for w in info["warnings"]), info["warnings"]


def case_free_chrome():
    """自由なページの描き出しの検査（Chrome）: 中身の箱から出たら誤り・下が空けば警告・画像だけの出力でも誤りで止まる。"""
    import subprocess
    import render as R
    ok = '<div class="f-row f-grow"><div class="f-box f-grow"><div>a</div></div><div class="f-box deep f-grow"><div class="f-chip top">札</div><div>b</div></div></div>'
    vs = R.check(build(one(_free(ok)), "free-c-ok")[0])
    assert not R.errors(vs) and not any(v["rule"].startswith("free-") for v in vs), vs
    vs = R.check(build(one(_free('<div class="f-box"><div>箱が1つだけ</div></div>')), "free-c-sparse")[0])
    assert [v["level"] for v in vs if v["rule"] == "free-sparse"] == ["warn"] and not R.errors(vs), vs
    out_path = build(one(_free('<div class="f-row" style="min-height: 1200px"><div class="f-box f-grow"><div>高すぎる箱</div></div></div>')), "free-c-out")[0]
    vs = R.check(out_path)
    assert any(v["rule"] == "free-outside" and v.get("level", "error") == "error" for v in vs), vs
    script = TMP / "free-c-out.json"
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "html_slide.py"), "deck", "--script", str(script), "--out", str(TMP / "free-c-png"),
                        "--no-fill", "--format", "png"], capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert r.returncode == 2 and "free-outside" in r.stderr and not (TMP / "free-c-png" / "slides").exists(), (r.returncode, r.stderr[-400:])


CASES = {k[5:]: v for k, v in globals().items() if k.startswith("case_")}


def main(argv):
    names = list(CASES) if not argv or argv[0] == "all" else argv
    bad = 0
    for n in names:
        try:
            CASES[n]()
            print(f"ok   {n}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
    return 1 if bad else 0


if __name__ == "__main__":
    # 試験の表示（失敗の報告・skip の理由・同じプロセスで呼んだエンジンの表示）だけは UTF-8 にする。英語の Windows（cp1252）
    # では日本語を print しただけで落ちる。PYTHONIOENCODING は使わない: 子の出力にも効いて、文字コードの書き忘れを隠す
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
