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
    assert info["slides"][0]["transition"] == "fade" and info["slides"][1]["transition"] == "morph"


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


def case_contract_attrs():
    """HTML の決まり: section・transition・ノートの［クリック］の数＝段の数・fx の語彙・入れ子なし・keep。"""
    path, info = deck.build_html(SAMPLE, TMP / "attrs")
    secs = parse(path)
    assert len(secs) == len(info["slides"])
    for n, (sec, i) in enumerate(zip(secs, info["slides"]), 1):
        assert sec["attrs"].get("data-transition") in ("morph", "fade", "none"), n
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
    # 薄い主役の色（淡い水色など）は止めない。面にはそのまま使い、文字と、文字を載せる面は読める濃さにした色を使う（2026-10-05）
    pale = TMP / "pale.yaml"
    pale.write_text('background: "#ffffff"\nink: "#1a2b4c"\nprimary: "#9ec5ff"\n', encoding="utf-8")
    html, _ = build(sample(), "theme-pale", theme=str(pale))
    p = deck.palette(deck.load_theme(pale))
    assert p["primary"] == "#9ec5ff" and deck.ratio(p["primary-text"], "#ffffff") >= 4.5, p
    assert deck.ratio(p["on-primary"], p["primary-strong"]) >= 4.5, p
    assert all(p[f"accent{r}"] == p[f"primary{r}"] for r in ("", "-text", "-strong", "-soft")), "差し色を書かないのに primary と違う"
    h = pathlib.Path(html).read_text()
    assert "var(--primary-text)" in h and "--primary: #9ec5ff" in h, "面の色をそのまま使っていない"
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
    s, n = T.fit("短い見出し", 60, 1680, what="x", lines=2, bold=True)
    assert (s, n) == (60, 1)
    s, n = T.fit("あ" * 40, 60, 1680, what="x", lines=2, bold=True)
    assert n == 2 and s == 60
    s, n = T.fit("会議の時間を半分にする", 88, 820, what="x", lines=3, bold=True, tidy=True)
    assert n == 2, (s, n)
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
    assert poly.group(2) == deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))["accent"]
    vals = lc["series"][0]["values"]
    hi, lo = vals.index(max(vals)), vals.index(min(vals))
    assert pts[hi][1] < pts[lo][1], "大きい値ほど上に描く"


def _sizes(html: str, cls: str) -> list[int]:
    return [int(x) for x in re.findall(rf'class="{cls}"[^>]*?font-size:(\d+)px', html)]


def case_fit_size_applied():
    """Codex r2 #3: fit() が縮めた大きさを、その要素の font-size に書く（KPI のラベル・増減・注記・表紙の日付など）。"""
    kp = {"type": "kpi", "headline": "h", "items": [
        {"label": "A" * 23, "value": "1", "delta": "B" * 24, "note": "注" * 38}, {"label": "b", "value": "2"}, {"label": "c", "value": "3"}]}
    path, _ = build(one(kp, ["", ""]), "fit-kpi")
    html = path.read_text(encoding="utf-8")
    lab, dl, nt = _sizes(html, "kpi-label"), _sizes(html, "kpi-delta"), _sizes(html, "kpi-note")
    assert lab and lab[0] < 30 and dl and dl[0] < 30 and nt and nt[0] < 24, (lab, dl, nt)
    iw = (deck_types_cw(3)) - 88
    assert T.text_width("A" * 23, lab[0], True) <= iw * T.SAFE, "折れない語が1行に入る大きさになっていない"
    cv = {"type": "cover", "title": "t", "meta": ["あ" * 60]}
    html = build(one(cv), "fit-cover")[0].read_text(encoding="utf-8")
    m = re.search(r'class="cover-meta"[^>]*><div style="font-size:(\d+)px">', html)
    assert m and int(m.group(1)) < 28, "表紙の日付・組織が縮んでいない"


def deck_types_cw(n, gap=40):
    return (T.CW - gap * (n - 1)) / n


def case_callout_inner_width():
    """Codex r2 #4: グラフの右の要点は、実際の内幅（500 − 左右 44）で測る。"""
    assert T.CALLOUT_IW == 412
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


SKIP_ESC = {"type", "role", "image", "layout", "trend", "value_labels", "accent", "fx", "transition", "initials"}


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
        spec = _poison(spec)
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
    assert slots[(2, "image")]["exists"] and slots[(2, "image")]["path"].endswith("images/s02-image.png")


def case_image_missing_placeholder():
    """画像が無いとき: 既定は rc=2（置き場を言う）・placeholder なら仮の枠＋警告（H-3 に掛かる「ここに画像」）。
    prompt だけの枠の置き場はキャッシュの区画の gen-<鍵>.png（ADR 0011・0014）。"generated" は生成の枠だけ仮にし、持ち込みの無い絵は止める。"""
    d = _img_dir("img-missing")
    sc = one({"type": "photo", "headline": "h", "image": {"prompt": "夕方の商店街を上から見た写真"}})
    try:
        _build_in(d, sc)
        raise AssertionError("無い画像が通った")
    except T.DeckError as e:
        assert "/gen-" in str(e) and os.environ["HTML_SLIDE_CACHE"] in str(e) and "fill" in str(e), str(e)
    path, info = _build_in(d, sc, placeholder=True)
    html = path.read_text(encoding="utf-8")
    assert 'class="img-ph"' in html and "ここに画像: 夕方の商店街" in html
    assert any("仮の枠" in w and "/gen-" in w for w in info["warnings"]), info["warnings"]
    assert info["images"][0]["exists"] is False and info["images"][0]["origin"] == "generated"
    _build_in(d, sc, placeholder="generated")                       # 生成の枠は描く前でも通る
    brought = one({"type": "photo", "headline": "h", "image": {"path": "images/nai.png", "prompt": "写真"}})
    try:
        _build_in(d, brought, placeholder="generated")
        raise AssertionError("持ち込みの無い絵が通った")
    except T.DeckError as e:
        assert "images/nai.png" in str(e), str(e)


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
    assert 'class="cover-subtitle" style="font-size:36px; color:var(--ink)"' in secs[0], "明るい幕の副題は ink"
    assert "on-dark" in secs[1] and "linear-gradient(to left" in secs[1] and "var(--scrim)" in secs[1]
    assert re.search(r'class="sec-text on-dark" style="left:1040px', secs[1]), "右に文字（1920 − 140 − 740）"
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
    assert len(set(ts)) == 1 and ts[0] > 44, f"短いカードの題が大きくならない: {ts}"
    assert len(set(bs)) == 1 and bs[0] / ts[0] <= 32 / 44 + 0.01, f"本文が題との比を越えた: {ts} {bs}"
    mixed = copy.deepcopy(short)
    mixed["items"][1]["title"] = "コラムに使わない／TOP には二度と使わない"
    ts = _role_sizes(html_of(mixed, "grow-mixed"), "card-title")
    assert len(set(ts)) == 1, f"題の大きさがそろわない: {ts}"
    full = {"type": "bullets", "headline": "見出し", "items": [
        {"title": f"項目{i}の題はここまで長く書いてある", "body": "本文も二行に届くくらいの長さで書いておき、場所を使い切るようにしてある。さらにもう少し足す"}
        for i in range(5)]}
    assert max(_role_sizes(html_of(full, "grow-full"), "li-title")) <= 44, "詰まったページが大きくなった"
    d = _img_dir("grow-img")
    sc = one({"type": "cards", "headline": "見出し", "items": [dict(it, image={"prompt": "x"}) for it in short["items"]]})
    path, _ = _build_in(d, sc, placeholder=True)
    assert max(_role_sizes(pathlib.Path(path).read_text(encoding="utf-8"), "card-title")) <= 44, "画像のあるページが大きくなった"
    long5 = {"type": "flow", "headline": "見出し", "items": [{"label": "1", "title": "あ" * 19, "body": "本文"} for _ in range(5)]}
    bs = _role_sizes(html_of(long5, "grow-floor"), "flow-body")
    assert min(bs) >= 28, f"大きく組んだのに本文が倍率 1 の大きさより縮んだ: {bs}"
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
    assert len(set(ts)) == 1 and ts[0] > 40, f"短い矢羽根の題が大きくならない: {ts}"


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
    assert deck.ratio(p["accent-on-dark"], deck.mix(p["ink"], T.SCRIM_ALPHA, "#ffffff")) >= 3.0


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
    assert 'class="side-img" src="assets/s01-image.png" alt="" style="left:120px;' in html and "width:820px" in html
    assert 'class="list" style="left:1020px' in html
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
    assert abs(cap - (top + (fh + dh) / 2 + 12)) <= 1, (top, fh, cap)
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
    th.write_text('background: "#ffffff"\nink: "#555555"\nmuted: "#555555"\nprimary: "#1f5fd1"\n', encoding="utf-8")
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
        assert img_bottom + 11 <= cap <= img_bottom + 13, (focus, img_bottom, cap)


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
    assert 'width:1920px; height:340px;' in html[kpi] and "band" not in secs[kpi]["keep"]
    assert re.search(r'class="head" style="width:1680px; top:388px"', html[kpi])
    cl = no["closing"]
    assert 'style="left:1160px; top:0px; width:760px; height:1080px;' in html[cl] and not secs[cl]["keep"]
    col = no["gallery/collage"]
    assert len(re.findall(r'class="gal-item"', html[col])) == 3 and "width:974px" in html[col]
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
        m = re.search(r'class="(?:side|figure)-img"[^>]*style="left:120px; top:(\d+)px; width:1080px; height:(\d+)px;', html)
        top, ih = int(m.group(1)), int(m.group(2))
        dots = [tuple(map(int, x)) for x in re.findall(r'class="mk-dot" style="left:(-?\d+)px; top:(-?\d+)px;', html)]
        if fit_mode == "cover":
            exp = [(120, top), (120 + 1080, top + ih)]
        else:
            k = min(1080 / 2000, ih / 1000)
            rw, rh = 2000 * k, 1000 * k
            ox, oy = 120 + (1080 - rw) / 2, top + (ih - rh) / 2
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
           "items": [{"x": 10 + i * 15, "y": 50, "title": "項目", "body": "説明"} for i in range(6)]}
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
    assert labels and max(labels) < 30, labels


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
    left_w = T.W - T.CLOSING_IMG_W - T.X0 - 80
    assert f'class="source" style="top:958px; width:{left_w}px;' in html
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
    assert tr[1] == "fade" and tr[2] == "morph" and tr[7] == "fade", f"切り替え: {tr}"
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
    h = pathlib.Path(html).read_text()
    for want in ("box-shadow: 0 8px 28px var(--shadow)", ".band { left: 0; width: 1920px", 'section[data-type="statement"] { background-color: var(--primary-strong)',
                 ".card.hl, .cmp.hl, .kpi.hl { background-color: var(--accent-strong)", "border-radius: 32px",
                 ".kpi.hl .kpi-value, .kpi.hl .kpi-unit { color: var(--on-accent); }"):
        assert want in h, f"visual の CSS に {want!r} が無い"
    html, _ = build(sc, "look-standard", style="standard")
    assert "var(--shadow)" not in pathlib.Path(html).read_text().split("{{LOOK}}")[0].split("/* スタイルの見た目の調子")[1], "standard に look の CSS が入った"
    assert DS.look_css({}) == "", "look が空なら CSS も空"
    t = DS.look_theme({"background": "#ffffff", "ink": "#1a2b4c", "primary": "#1f5fd1"}, {"background": "tint"})
    assert t["background"] != "#ffffff", "tint で地の色が変わっていない"


def case_theme_primary_accent():
    """テーマの主役の色（primary）と差し色（accent）: 骨格は primary・強調は accent・古い書き方（accent だけ）は rc=2。"""
    two = TMP / "two.yaml"
    two.write_text('background: "#ffffff"\nink: "#1a1a1a"\nprimary: "#f5a623"\naccent: "#399dd1"\n', encoding="utf-8")
    p = deck.palette(deck.load_theme(two))
    assert p["primary"] == "#f5a623" and p["accent"] == "#399dd1", p
    assert p["on-primary"] == "#1a1a1a", "明るい黄色の面に黒の文字を選んでいない"
    assert deck.ratio(p["accent-text"], "#ffffff") >= 4.5 and deck.ratio(p["on-accent"], p["accent-strong"]) >= 4.5, p
    assert p["on-accent"] == "#ffffff" and p["accent-strong"] != "#399dd1", "中くらいの青の面が白抜きになっていない（少し暗くして白）"
    dflt = deck.palette(deck.load_theme(deck.THEMES / "default.yaml"))
    assert dflt["on-primary"] == "#ffffff", "既定のシアンの面が白抜きになっていない"
    sc = {"title": "t", "slides": [
        {"type": "cards", "headline": "会議を*半分*にする", "items": [{"title": "a"}, {"title": "b", "accent": True}]}]}
    html, _ = build(sc, "theme-two", theme=str(two), style="visual")
    h = pathlib.Path(html).read_text()
    for want in ("--primary: #f5a623", "--accent: #399dd1", "em { font-style: normal; color: var(--accent-text); }",
                 "background-color: var(--primary); }", ".card.hl, .cmp.hl, .kpi.hl { background-color: var(--accent-strong)"):
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
            bad.write_text("{}")
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
    assert off == [1, 8, 13], f"ロゴを置かないページ {off}（表紙・章扉の絵・全面の写真）"
    th = deck.DS.look_theme(deck.load_theme(deck.THEMES / "default.yaml"), deck.DS.load("visual")["look"])
    assert pages[21]["plate"] == deck.palette(th)["primary-strong"], f"主張の全面の地の色になっていない: {pages[21]}"
    assert pages[2]["plate"] == th["background"], pages[2]
    h = pathlib.Path(html).read_text()
    secs = h.split("<section data-slide")[1:]
    assert 'class="logo"' not in secs[0] and 'class="logo"' in secs[1]
    assert re.search(r'data-name="ページ番号">2<', secs[1]), "ページ番号が中央下（動かさない）でない"
    assert re.search(r'class="footer"[^>]*style="width: 792px;"', secs[1]), "下の文字が中央下の番号の手前で止まっていない"
    assert (TMP / "logo-visual" / "assets" / "logo.png").is_file()
    _, info = deck.build_html(SAMPLE, TMP / "logo-standard", logo=str(logo))      # 右半分が絵の表紙・章扉は左下へ
    pages = {pg["no"]: pg for pg in info["logo"]["pages"]}
    assert [(pages[n].get("corner"), pages[n].get("x")) for n in (1, 8)] == [("bottom-left", 160), ("bottom-left", 120)], \
        f"右下がふさがるページが、文字の左端にそろえた左下に移っていない: {pages[1]} {pages[8]}"
    assert pages[13].get("off") and pages[2]["corner"] == "bottom-right", (pages[13], pages[2])
    sc = {"title": "t", "slides": [{"type": "cover", "layout": "graphic", "title": "題"},
                                   {"type": "section", "layout": "graphic", "title": "章"}]}
    _, info = build(sc, "logo-graphic", logo=str(logo))                           # 図形の飾りは写真と同じく避ける
    assert [pg.get("corner") for pg in info["logo"]["pages"]] == ["bottom-left", "bottom-left"], info["logo"]["pages"]


def case_logo_positions():
    """--logo-position（Chrome で測る）: 左下は 左下 → 右下・右上は 右上 → 右下（上下をまたぐのは右の列だけ）。
    左下なら下の文字をロゴの右へ、右上ならページ番号は元の位置。角ごとに PPTX の部品名を変える。知らない値は止める。"""
    logo = _logo_png("wide.png", (444, 59), (0, 0, 444, 59))
    html, info = deck.build_html(SAMPLE, TMP / "logo-bl", logo=str(logo), logo_position="bottom-left")
    pages = {pg["no"]: pg for pg in info["logo"]["pages"]}
    assert info["logo"]["position"] == "bottom-left"
    assert pages[2]["corner"] == "bottom-left" and pages[19]["corner"] == "bottom-right" and pages[13].get("off"), \
        f"左下 → 右下 → 置かない になっていない: {pages[2]} {pages[19]} {pages[13]}（19 は左半分が絵）"
    secs = pathlib.Path(html).read_text().split("<section data-slide")[1:]
    assert 'data-keep="logo-left"' in secs[1] and 'data-keep="logo-plate-left"' in secs[1]
    assert re.search(r'class="footer"[^>]*style="left: 477px; width: 435px;"', secs[1]), "下の文字がロゴの右に寄っていない"
    assert re.search(r'class="pageno"[^>]*>2<', secs[1]) and 'data-name="ページ番号" style=' not in secs[1], "ページ番号が動いた"
    assert 'data-keep="logo"' in secs[18], "右下に逃げたページの部品名が右下のものでない"
    html, info = deck.build_html(SAMPLE, TMP / "logo-tr", logo=str(logo), logo_position="top-right")
    pages = {pg["no"]: pg for pg in info["logo"]["pages"]}
    assert pages[2]["corner"] == "top-right" and all(pages[n].get("off") for n in (1, 8, 13)), \
        f"右上 → 右下 → 置かない になっていない（表紙・章扉は右半分が絵）: {[pages[n] for n in (1, 2, 8, 13)]}"
    secs = pathlib.Path(html).read_text().split("<section data-slide")[1:]
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
    data = json.loads(SAMPLE.read_text())
    data.pop("page_numbers", None)
    p = TMP / "no-numbers.json"
    p.write_text(json.dumps(data, ensure_ascii=False))
    html, _ = deck.build_html(p, TMP / "no-numbers", placeholder=True)
    h = pathlib.Path(html).read_text()
    assert 'class="pageno"' not in h and 'class="footer" data-keep="footer" data-name="下の文字">' in h, "既定でページ番号が出た"
    css = (deck.TEMPLATE).read_text()
    assert ".pageno { left: 900px; width: 120px; text-align: center; }" in css, "ページ番号が中央下でない"


def case_watermark():
    """--watermark: 全ページ（表紙・写真も）の中央下の端・固定のグレー・下の文字の行とは重ならない。
    PPTX では全スライドの一番上に、選べない・動かせない部品（!!watermark）。付けなければ何も足さない。改行は止める。"""
    import render as R
    from pptx import Presentation
    html, info = deck.build_html(SAMPLE, TMP / "wm", placeholder=True, watermark="example.com")
    h = pathlib.Path(html).read_text()
    n = len(h.split("<section data-slide")) - 1
    assert h.count('<div class="watermark" data-keep="watermark" data-name="透かし">example.com</div>') == n, "全ページに透かしが無い"
    assert info["watermark"] == "example.com"
    css = deck.TEMPLATE.read_text()
    assert (f"top: {deck.WATERMARK_TOP}px;" in css and f"font-size: {deck.WATERMARK_PX}px;" in css and f"color: {deck.WATERMARK_COLOR};" in css), \
        "templates/deck.html の .watermark が deck.py の数字とずれた"
    assert deck.WATERMARK_TOP > 1006 + 22 * 1.4, "透かしが下の文字の行にかかる"
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
    assert 'class="watermark"' not in pathlib.Path(html).read_text() and "watermark" not in info, "付けていないのに透かしが出た"
    try:
        deck.build_html(SAMPLE, TMP / "wm-nl", placeholder=True, watermark="a\nb")
        raise AssertionError("改行のある透かしが通った")
    except T.DeckError as e:
        assert "1行" in str(e), e


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
    sys.exit(main(sys.argv[1:]))
