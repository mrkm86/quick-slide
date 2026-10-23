#!/usr/bin/env python3
"""レビューモード（review・ADR 0028）のテスト。

    python3 test_review.py <ケース名>     # 1ケース
    python3 test_review.py all           # 全部

notes・wishes・止まらずに描く作り・deck が注記を読まないことは、ブラウザを使わない。review の通し・画素の比較・幅・watch は Chrome を使う。
"""
from __future__ import annotations

import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

SKILL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
import browser as B  # noqa: E402
import deck  # noqa: E402
import html_slide as H  # noqa: E402
import review as RV  # noqa: E402

SAMPLE = SKILL / "examples" / "sample-deck.json"
TMP = pathlib.Path(tempfile.mkdtemp(prefix="html-slide-review-test-"))
PICK = (0, 2, 4, 5, 8, 9)       # cover・agenda・section・big-number・section（画像つき）・matrix（章扉が 2 つ入る）


def script_of(name: str, mutate=None) -> pathlib.Path:
    """見本から数ページを抜いた台本を、画像の置き場ごと作る。"""
    work = TMP / name
    work.mkdir()
    shutil.copytree(SAMPLE.parent / "assets", work / "assets")
    sc = json.loads(SAMPLE.read_text(encoding="utf-8"))
    sc["slides"] = [sc["slides"][i] for i in PICK]
    if mutate:
        mutate(sc)
    p = work / "draft.json"
    p.write_text(json.dumps(sc, ensure_ascii=False), encoding="utf-8")
    return p


def sections(path: pathlib.Path) -> list[str]:
    return RV.split_doc(path.read_text(encoding="utf-8"))[1]


def case_notes_of():
    """_memo・_wish は文字・_ok は真偽値。省略・空文字は無いのと同じ・型違いは誤りとして返す（注記には入れない）。"""
    assert RV.notes_of({"type": "cover"}) == ({"memo": "", "wish": "", "ok": False}, [])
    n, bad = RV.notes_of({"_memo": "狙い\n2行目", "_wish": "  ", "_ok": True})
    assert n == {"memo": "狙い\n2行目", "wish": "", "ok": True} and not bad
    n, bad = RV.notes_of({"_memo": 3, "_wish": ["a"], "_ok": "yes"})
    assert n == {"memo": "", "wish": "", "ok": False} and len(bad) == 3, bad
    assert "_memo" in bad[0] and "_wish" in bad[1] and "_ok" in bad[2]
    # 明示した null は、省略とは別。型違いとして返す（使わないならキーごと外す）
    for k in ("_memo", "_wish", "_ok"):
        n, bad = RV.notes_of({k: None})
        assert len(bad) == 1 and k in bad[0] and "null" in bad[0], (k, bad)
    assert RV.notes_of({"type": "cover"})[1] == []
    assert RV.notes_of("文字列のページ") == ({"memo": "", "wish": "", "ok": False}, [])


def case_wishes_markdown():
    """希望一覧: 台本順・ページ番号・ID・見出し・希望本文（無ければ「希望なし」）。タグと Markdown の記号は文字として出す。"""
    pages = [{"no": 1, "label": "表紙", "id": None, "wish": ""},
             {"no": 2, "label": "# 比較 <b>", "id": "cmp", "wish": "左右を <script> で\n\n*強く* [見せたい](x)"},
             {"no": 3, "label": "三", "id": None, "wish": "最後"}]
    md = RV.wishes_markdown(pages, "資料")
    assert md.index("## 1ページ") < md.index("## 2ページ") < md.index("## 3ページ")
    assert "- 希望: 希望なし" in md and "- ID: cmp" in md
    assert "\\<script\\>" in md and "\\*強く\\*" in md and "\\[見せたい\\]" in md and "\\# 比較 \\<b\\>" in md, md
    assert "<script>" not in md.replace("\\<script\\>", "")


def case_continue_on_error():
    """止まらずに描く: 正常 → 失敗 → 正常。失敗ページに理由の枠・番号は欠番にしない・章番号は台本の順・失敗ページの素材・枠・警告は捨てる。
    deck（page_errors なし）は従来どおり最初の誤りで止まる。"""
    def mutate(sc):
        sc["slides"][1]["type"] = "nonexistent"          # 2枚目: 知らない型
        sc["slides"][3] = dict(sc["slides"][3], headline="x" * 400)   # 4枚目: 2行に入らない
        sc["slides"][4]["image"] = {"path": "assets/nothing.png"}      # 5枚目（章扉・画像つき）: 持ち込みの絵が無い
    p = script_of("continue", mutate)
    errs: dict = {}
    path, info = deck.build_html(p, TMP / "continue" / "out", page_errors=errs)
    assert sorted(errs) == [2, 4, 5], errs
    secs = sections(path)
    assert len(secs) == 6 and [x["no"] for x in info["slides"]] == [1, 2, 3, 4, 5, 6]
    assert 'data-type="error"' in secs[1] and "知らない型" in html.unescape(secs[1]) and "2枚目を描けない" in secs[1]
    assert 'data-type="error"' in secs[4]
    assert 'data-type="error"' not in secs[0] and 'data-type="error"' not in secs[2]
    # 章番号: 台本の 3 枚目が章 1、5 枚目（失敗）が章 2、6 枚目は章扉でない。失敗しても数え直さない
    ok = script_of("continue-ok")
    _, base = deck.build_html(ok, TMP / "continue-ok" / "out")
    sec3 = sections(TMP / "continue-ok" / "out" / "deck.html")[2]
    assert sections(path)[2] == sec3, "失敗したページの前後で、正常なページの section が変わった"
    # 失敗した 5 枚目の画像の枠は一覧に残らない
    assert all(x["slide"] != 5 for x in info["images"]), [x["slide"] for x in info["images"]]
    # deck は止まる
    try:
        deck.build_html(p, TMP / "continue" / "strict")
    except deck.DeckError as e:
        assert "2枚目" in str(e)
    else:
        raise AssertionError("deck が止まらなかった")


def case_chapter_numbers_kept():
    """失敗した章扉のあとの章扉も、台本の順の番号（2 つ目の章扉は「02」）。"""
    def mutate(sc):
        sc["slides"][2]["headline"] = ""      # 3枚目（章扉）を壊す
    p = script_of("chapters", mutate)
    errs: dict = {}
    path, _ = deck.build_html(p, TMP / "chapters" / "out", page_errors=errs)
    assert list(errs) == [3], errs
    assert ">02<" in sections(path)[4], "2 つ目の章扉の番号が台本の順でない"


def case_deck_ignores_notes():
    """_memo・_ok・_wish を書いても deck の出力（HTML）は変わらない。既存の内部キー（_characters）も今までどおり。"""
    plain = script_of("notes-plain")
    noted = script_of("notes-noted", lambda sc: [sc["slides"][i].update({"_memo": "m", "_ok": i % 2 == 0, "_wish": "w <b>"})
                                                 for i in range(len(sc["slides"]))])
    a, _ = deck.build_html(plain, TMP / "notes-plain" / "out")
    b, _ = deck.build_html(noted, TMP / "notes-noted" / "out")
    sa, sb = sections(a), sections(b)
    assert sa == sb and len(sa) == len(PICK)
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")
    # 型違いの注記を書いても deck は止まらない（見るのは review）
    bad = script_of("notes-bad", lambda sc: sc["slides"][0].update({"_memo": 3, "_ok": "x"}))
    c, _ = deck.build_html(bad, TMP / "notes-bad" / "out")
    assert sections(c) == sa


def case_unknown_underscore_kept():
    """未知の _ キーは、これまでどおり未知キーの検査から外れる（一律には禁止しない）。"""
    p = script_of("underscore", lambda sc: sc["slides"][0].update({"_whatever": 1}))
    deck.build_html(p, TMP / "underscore" / "out")


def lacking() -> bool:
    return bool(B.missing())


def run_review(p: pathlib.Path, *extra, out="review") -> tuple[int, pathlib.Path]:
    out_dir = p.parent / out
    rc = H.main(["review", "--script", str(p), "--out", str(out_dir), *extra])
    return rc, out_dir


def case_review_end_to_end():
    """review（Chrome）: 注記なしの台本は rc=0・全ページ・目次・埋め込みの外部通信なし・メモと希望の改行を保つ・_ok は畳む・誤りのページは理由つきで残りを描く・
    型違いの注記は誤りに出して描画は続ける・希望の一覧を書く。"""
    if lacking():
        print("skip: Chrome が無い", file=sys.stderr)
        return
    plain = script_of("rv-plain")
    rc, out = run_review(plain)
    assert rc == 0, rc
    text = (out / "review.html").read_text(encoding="utf-8")
    assert text.count("<iframe") == len(PICK) and text.count('class="page') >= len(PICK)
    assert not re.search(r'(?:src|href)="https?://', text), "外部の URL を読んでいる"
    assert "<details class=\"page\" id=\"p1\" open>" in text and "検査: 問題なし" in text
    assert not (out / "wishes.md").exists(), "--wishes を付けないのに書いた"

    def mutate(sc):
        s = sc["slides"]
        s[0].update({"_memo": "狙い: 全体像\n数字の出どころ: 調査", "_wish": "表紙を\n全面の絵にしたい <i>", "_ok": True})
        s[1]["type"] = "nonexistent"
        s[3]["_ok"] = "yes"
    p = script_of("rv-notes", mutate)
    rc, out = run_review(p, "--wishes")
    assert rc == 2, rc
    text = (out / "review.html").read_text(encoding="utf-8")
    assert text.count("<iframe") == len(PICK), "止まらずに全ページを出していない"
    assert '<details class="page is-ok" id="p1">' in text, "_ok のページを畳んでいない"
    assert "狙い: 全体像\n数字の出どころ: 調査" in html.unescape(text)
    assert "&lt;i&gt;" in text and "<i>" not in text.replace("&lt;i&gt;", ""), "希望の HTML を文字として出していない"
    assert "知らない型" in html.unescape(text) and "描画不能" in text
    assert "_ok は true か false" in text and "注記の型" in text
    assert "誤り 1" in text      # 目次・見出しに件数が残る
    md = (out / "wishes.md").read_text(encoding="utf-8")
    assert "## 1ページ" in md and "表紙を\n  全面の絵にしたい \\<i\\>" in md and md.count("希望なし") == len(PICK) - 1
    # 後ろの正常なページは、失敗の前後で同じ section
    assert sections(out / "deck.html")[2] == sections(plain.parent / "review" / "deck.html")[2]


def case_review_same_slides_as_deck():
    """review の各ページの文書（CSS・字体・section）は、deck.html と同じ。deck の出力は review 用の注記があっても同じ。"""
    if lacking():
        print("skip: Chrome が無い", file=sys.stderr)
        return
    p = script_of("rv-same", lambda sc: sc["slides"][0].update({"_memo": "m"}))
    rc, out = run_review(p)
    assert rc == 0
    d, _ = deck.build_html(p, p.parent / "deckout")
    dhtml = d.read_text(encoding="utf-8")
    head = RV.split_doc(dhtml)[0]
    srcdocs = [html.unescape(m) for m in re.findall(r'srcdoc="([^"]*)"', (out / "review.html").read_text(encoding="utf-8"))]
    secs = sections(d)
    assert len(srcdocs) == len(secs)
    for doc, sec in zip(srcdocs, secs):
        assert doc.startswith(head) and sec in doc and doc.count("<section data-slide") == 1


def png_diff(a, b) -> int:
    """違う画素の数。丸い図形の縁の薄い差（iframe の中の合成による）だけは許す側で数える。"""
    from PIL import Image, ImageChops
    with Image.open(a) as x, Image.open(b) as y:
        assert x.size == y.size, (x.size, y.size)
        h = ImageChops.difference(x.convert("RGB"), y.convert("RGB")).convert("L").histogram()
        return sum(h[1:])


PIXEL_SLACK = 300      # 1920×1080 の 0.015%。縁の薄い差の数（見本では丸い図形の縁で 100 ほど）。文字や面がずれればこの数を大きく超える


def case_review_pixels_match_deck():
    """review の iframe（等倍）の画素は、deck.html の section の PNG と同じ（表紙・章扉・図・画像つきなど）。"""
    if lacking():
        print("skip: Chrome が無い", file=sys.stderr)
        return
    p = script_of("rv-pixels", lambda sc: sc["slides"][1].update({"_memo": "m", "_ok": True}))
    rc, out = run_review(p)
    assert rc == 0
    node, _ = B.node()
    env = B.child_env()
    r = subprocess.run([node, str(pathlib.Path(__file__).with_name("review_shot.mjs")), str(out / "review.html"), str(out / "frames")],
                       capture_output=True, text=True, timeout=600, env=env, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    d = p.parent / "deckpng"
    assert H.snapshot(out / "deck.html", d, len(PICK))
    for i in range(1, len(PICK) + 1):
        assert png_diff(out / "frames" / f"frame-{i:02d}.png", d / f"slide-{i:02d}.png") <= PIXEL_SLACK, f"{i}ページの画素が deck と違う"
    # PDF: deck と同じ文書から、同じページ数・順で出る（ページ数は pdfinfo があるとき）
    pdf = p.parent / "deck.pdf"
    assert H.write_pdf(out / "deck.html", pdf) and pdf.stat().st_size > 1000


def case_review_narrow():
    """スマホの幅（390px）でも、横にはみ出さない。"""
    if lacking():
        print("skip: Chrome が無い", file=sys.stderr)
        return
    p = script_of("rv-narrow")
    rc, out = run_review(p)
    assert rc == 0
    node, _ = B.node()
    r = subprocess.run([node, str(pathlib.Path(__file__).with_name("review_shot.mjs")), str(out / "review.html"), str(out / "narrow"), "390"],
                       capture_output=True, text=True, timeout=600, env=B.child_env(), encoding="utf-8")
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["scrollWidth"] <= got["clientWidth"], got


def case_review_guards():
    """出力が台本と重なる指定は rc=2・Chrome が足りなければ書かずに rc=4（検査済みのふりをしない）・台本全体の誤りは書かずに rc=2。"""
    p = script_of("rv-guard")
    assert H.main(["review", "--script", str(p), "--out", str(p.parent), "--wishes", str(p)]) == 2
    saved = H.lacking_browser
    real_missing = B.missing
    B.missing = lambda *a, **k: ["Chrome"]
    try:
        out = p.parent / "no-chrome"
        assert H.main(["review", "--script", str(p), "--out", str(out)]) == 4
        assert not (out / "review.html").exists()
    finally:
        B.missing = real_missing
    if lacking():
        return
    bad = p.parent / "broken.json"
    bad.write_text("{", encoding="utf-8")
    out = p.parent / "broken-out"
    assert H.main(["review", "--script", str(bad), "--out", str(out)]) == 2
    assert not (out / "review.html").exists()


def case_review_null_notes():
    """_memo・_wish・_ok の null は、そのページの注記の型違い（rc=2）として出る。描画は続ける。省略は正常。"""
    if lacking():
        print("skip: Chrome が無い", file=sys.stderr)
        return
    p = script_of("rv-null", lambda sc: sc["slides"][0].update({"_memo": None, "_wish": None, "_ok": None}))
    rc, out = run_review(p)
    assert rc == 2, rc
    text = (out / "review.html").read_text(encoding="utf-8")
    assert text.count("<iframe") == len(PICK) and text.count("注記の型") == 3 and "null" in text


def case_review_wishes_collision():
    """--wishes の書き出し先が review の出力（review.html・deck.html・一時ファイル・assets/）と重なれば、生成前に rc=2。既存の成果物は変わらない。"""
    p = script_of("rv-collide")
    out = p.parent / "out"
    out.mkdir()
    (out / "assets").mkdir()
    keep = {n: b"keep-" + n.encode() for n in ("review.html", "deck.html")}
    for n, b in keep.items():
        (out / n).write_bytes(b)
    for target in (out / "review.html", out / "deck.html", out / "review.html.tmp", out / "assets" / "w.md", out / "assets", out):
        assert H.main(["review", "--script", str(p), "--out", str(out), "--wishes", str(target)]) == 2, target
        assert all((out / n).read_bytes() == b for n, b in keep.items()), f"{target} を拒否する前に成果物が変わった"
    assert not (out / "review.html.tmp").exists() and not (out / "assets" / "w.md").exists()


def case_review_watch():
    """--watch: 台本が変わると作り直す（同じ長さの変更・ファイルの置き換えでも）・壊れた台本では直前の成果物を残して見続ける・stop で終わる。"""
    if lacking():
        print("skip: Chrome が無い", file=sys.stderr)
        return
    p = script_of("rv-watch")
    out = p.parent / "review"

    class A:
        theme = style = mode = logo = watermark = image_look = None
        logo_position = "bottom-right"

    runs = []
    steps = [
        lambda: p.write_text(p.read_text(encoding="utf-8").replace("会議", "会義"), encoding="utf-8"),   # 同じ長さの変更
        lambda: (p.with_name("new.json").write_text(p.read_text(encoding="utf-8").replace("会義", "会議"), encoding="utf-8"),
                 os.replace(p.with_name("new.json"), p)),                                              # 置き換え
        lambda: p.write_text("{", encoding="utf-8"),                                                    # 保存の途中
    ]
    real = RV.generate

    def counting(*a, **k):
        runs.append(1)
        return real(*a, **k)

    RV.generate = counting
    stamp = {}

    def stop():
        n = len(runs)
        if n and n not in stamp:                     # 回が終わるごとに、次の変更を入れる
            stamp[n] = (out / "review.html").read_bytes() if (out / "review.html").exists() else None
            if n - 1 < len(steps):
                steps[n - 1]()
                return False
            return True
        return False

    try:
        RV.watch(p, out, A, None, stop=stop, poll=0.05, settle=0.05)
    finally:
        RV.generate = real
    assert len(runs) == 4, runs                                                                         # 最初 + 変更 3 回
    assert stamp[2] != stamp[1], "同じ長さの変更で作り直していない"
    assert stamp[4] == stamp[3], "壊れた台本で、直前の成果物が変わった"
    assert "会議" in stamp[4].decode("utf-8")


CASES = {k[5:]: v for k, v in globals().items() if k.startswith("case_")}


def main(argv):
    names = list(CASES) if not argv or argv[0] == "all" else argv
    bad = 0
    for n in names:
        try:
            CASES[n]()
            print(f"ok   {n}")
        except Exception as e:  # noqa: BLE001
            import traceback
            bad += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
            traceback.print_exc()
    return 1 if bad else 0


if __name__ == "__main__":
    # 試験の表示（失敗の報告・skip の理由）だけは UTF-8 にする（英語の Windows の cp1252 では日本語を print しただけで落ちる）
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
