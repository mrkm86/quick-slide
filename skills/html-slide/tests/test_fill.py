#!/usr/bin/env python3
"""画像の枠を Codex で埋める（fill・deck・revise・ADR 0011）のテスト。`python3 test_fill.py <名前>` で1つ走らせる。

本物の Codex は呼ばない。偽物（fake_codex.py・image-slide の写し）を HTML_SLIDE_CODEX で差し、実ホームも偽の CODEX_HOME にする。
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))
ENTRY = SKILL / "scripts" / "html_slide.py"
PHOTO = SKILL / "examples" / "assets" / "meeting.png"

import deck  # noqa: E402
import imagegen as G  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def tmpdir() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(prefix="html-slide-fill-test."))


def fake_home(root: pathlib.Path) -> pathlib.Path:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    home = root / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(json.dumps({
        "auth_mode": "chatgpt", "OPENAI_API_KEY": None, "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tokens": {"id_token": "x", "access_token": f"{enc({'alg': 'none'})}.{enc({'exp': int(time.time()) + 5 * 86400})}.sig",
                   "refresh_token": "r", "account_id": "a"}}))
    return home


def run(args, root: pathlib.Path, mode="ok"):
    """偽物の codex で入口を流す。(rc, stdout, stderr, この回の生成の呼び出し)"""
    log = root / "calls"
    shutil.rmtree(log, ignore_errors=True)
    env = {**os.environ, "HTML_SLIDE_CODEX": str(HERE / "fake_codex.py"), "FAKE_CODEX_LOG": str(log),
           "FAKE_CODEX_MODE": mode, "CODEX_HOME": str(fake_home(root)), "OPENAI_API_KEY": "sk-test-should-not-pass",
           "FAKE_CODEX_SLEEP": "0.1"}
    r = subprocess.run([sys.executable, str(ENTRY), *args], capture_output=True, text=True, env=env, timeout=600)
    calls = [json.loads(p.read_text()) for p in sorted(log.glob("*.json"))] if log.exists() else []
    return r.returncode, r.stdout, r.stderr, [c for c in calls if c.get("cmd") == "exec" and "t1" in c]


def script(root: pathlib.Path, slides: list, name="deck.json") -> pathlib.Path:
    p = root / name
    p.write_text(json.dumps({"title": "t", "slides": slides}, ensure_ascii=False))
    return p


def pages():
    return [{"type": "cover", "title": "表紙", "image": {"prompt": "朝の光が入る明るいオフィス"}},
            {"type": "photo", "headline": "現場", "image": {"prompt": "工場の作業台を上から見た様子"}},
            {"type": "photo", "headline": "持ち込み", "image": {"path": str(PHOTO)}}]


def area(root):
    """台本のフォルダ（root）の、描いた絵の置き場（キャッシュの区画・ADR 0014）。"""
    import cache as K
    return K.area_path(K.root("html-slide", {}), root)


def gens(root):
    return sorted(area(root).glob("gen-*.png"))


def kit_png(root: pathlib.Path) -> pathlib.Path:
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (64, 100), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((10, 10, 54, 90), fill=(200, 80, 40, 255))
    p = root / "hana.png"
    im.save(p)
    return p


# --- テスト -----------------------------------------------------------------------

def t_fill_generates_missing():
    """まだ絵の無い生成の枠だけ描く・持ち込みは触らない・2回目は描かない・API キーを渡さない・形を伝える・横に記録。"""
    root = tmpdir()
    sc = script(root, pages())
    rc, so, se, calls = run(["fill", "--script", str(sc)], root)
    check(rc == 0, se)
    check(len(calls) == 2 and len(gens(root)) == 2, f"描いた数: {len(calls)}・{gens(root)}")
    check(not any(c["api_key"] for c in calls), "OPENAI_API_KEY が codex に渡った")
    meta = json.loads(gens(root)[0].with_suffix(".json").read_text())
    check(meta["image_look"] == "illustration" and "文字・ロゴ・透かしを描かない" in meta["text"]
          and G.IMAGE_LOOKS["illustration"] in meta["text"], meta)
    check("枚目" not in meta["text"], "描く指示にページ番号が入った（並べ替えで描き直しになる）")
    shapes = {json.loads(p.with_suffix(".json").read_text())["shape"].split(",")[0] for p in gens(root)}
    check(shapes <= {"landscape", "portrait", "square"}, shapes)
    rc, so, se, calls = run(["fill", "--script", str(sc)], root)
    check(rc == 0 and not calls, f"2回目も描いた: {len(calls)}")


def t_reorder_and_look():
    """並べ替えても描き直さない（鍵にページ番号が無い）。画風を変えると生成の枠だけ描き直し、戻すと描かない。"""
    root = tmpdir()
    ps = pages()
    sc = script(root, ps)
    check(run(["fill", "--script", str(sc)], root)[0] == 0, "1回目")
    ps2 = [ps[0], ps[2], {**ps[1], "headline": "見出しを変えた"}]
    rc, so, se, calls = run(["fill", "--script", str(script(root, ps2))], root)
    check(rc == 0 and not calls, f"並べ替え・見出しの変更で描き直した: {len(calls)}")
    rc, so, se, calls = run(["fill", "--script", str(sc), "--image-look", "handdrawn"], root)
    check(rc == 0 and len(calls) == 2, f"画風を変えて描いた数: {len(calls)}")
    rc, so, se, calls = run(["fill", "--script", str(sc), "--style", "standard"], root)
    check(rc == 0 and not calls, "元の画風に戻したのに描いた")
    rc, so, se, calls = run(["fill", "--script", str(sc), "--style", "visual"], root)
    check(rc == 0 and len(calls) == 2, f"visual（既定は photo）で描いた数: {len(calls)}")
    meta = [json.loads(p.with_suffix(".json").read_text()) for p in gens(root)]
    check({m["image_look"] for m in meta} == {"illustration", "handdrawn", "photo"}, "画風ごとに絵が残っていない")


def t_brought_missing_stops():
    """持ち込みの絵が無ければ、描く前に rc=2（Codex は呼ばない）。"""
    root = tmpdir()
    sc = script(root, [*pages()[:2], {"type": "photo", "headline": "h", "image": {"path": "nai.png"}}])
    rc, so, se, calls = run(["fill", "--script", str(sc)], root)
    check(rc == 2 and not calls and "持ち込みの絵が無い" in se, se)


def t_characters_to_first_slot():
    """_characters はそのページの最初の生成の枠に添える（透過は白に合成）。描く枠が無ければ警告。キャラの絵が変われば描き直す。"""
    root = tmpdir()
    kp = kit_png(root)
    chars = [{"name": "Hana", "image": str(kp), "pose": "考える"}]
    ps = [{"type": "photo", "headline": "h", "image": {"prompt": "会議室"}, "_characters": chars},
          {"type": "photo", "headline": "h", "image": {"path": str(PHOTO)}, "_characters": chars}]
    sc = script(root, ps)
    rc, so, se, calls = run(["fill", "--script", str(sc)], root)
    check(rc == 0 and len(calls) == 1, se)
    check(len(calls[0]["refs"]) == 1 and calls[0]["ref_modes"] == ["RGB"], calls[0])
    meta = json.loads(gens(root)[0].with_suffix(".json").read_text())
    check("添付の参照画像1は Hana（ポーズ・表情: 考える）" in meta["text"] and meta["characters"][0]["name"] == "Hana", meta["text"])
    with tempfile.TemporaryDirectory() as tmp:
        _, info = deck.build_html(sc, tmp, placeholder="generated")
    check(any("2枚目" in w and "_characters を描き込む枠が無い" in w for w in info["warnings"]), info["warnings"])
    from PIL import Image
    Image.new("RGBA", (64, 100), (10, 10, 10, 255)).save(kp)
    rc, so, se, calls = run(["fill", "--script", str(sc)], root)
    check(rc == 0 and len(calls) == 1, "キャラの絵を差し替えても描き直さない")


def t_auth_dead_is_rc4():
    root = tmpdir()
    rc, so, se, calls = run(["fill", "--script", str(script(root, pages()))], root, mode="auth")
    check(rc == 4 and "codex login" in se, se)


def t_deck_fills_and_falls_back():
    """deck は先に描いて埋める。描けなければ仮の枠で組んで rc=1。--no-fill は描かずに仮の枠。check は描く前でも通る。"""
    root = tmpdir()
    sc = script(root, pages()[:2])
    rc, so, se, calls = run(["check", "--script", str(sc)], root)
    check(rc == 0 and not calls, se)
    rc, so, se, calls = run(["deck", "--script", str(sc), "--out", str(root / "none"), "--no-png", "--no-odp"], root, mode="none")
    check(rc == 1 and (root / "none" / "deck.pptx").is_file() and "仮の枠" in se, f"rc={rc} {se[-400:]}")
    rc, so, se, calls = run(["deck", "--script", str(sc), "--out", str(root / "nf"), "--no-png", "--no-odp", "--no-fill"], root)
    check(rc == 0 and not calls and "仮の枠" in se, se[-400:])
    rc, so, se, calls = run(["deck", "--script", str(sc), "--out", str(root / "ok"), "--no-png", "--no-odp"], root)
    check(rc == 0 and len(calls) == 2, se[-400:])
    html = (root / "ok" / "deck.html").read_text()
    check('class="img-ph"' not in html and html.count('src="assets/s0') == 2, "描いた絵が HTML に入っていない")


def t_revise():
    """微修正は今の絵を土台に添える・作り直しは添えない・前の絵を残す・鍵は元のまま・持ち込みは直さない・直したら deck を組む。"""
    root = tmpdir()
    sc = script(root, pages())
    out = root / "out"
    base = ["--script", str(sc), "--out", str(out), "--no-png", "--no-odp"]
    rc, so, se, calls = run(["revise", *base, "--page", "2", "--note", "人を一人足す"], root)
    check(rc == 2 and "まだ描いていない" in se, se)
    check(run(["fill", "--script", str(sc)], root)[0] == 0, "fill")
    before = {p.name for p in gens(root)}
    rc, so, se, calls = run(["revise", *base, "--page", "2", "--note", "人を一人足す"], root)
    check(rc == 0 and len(calls) == 1 and len(calls[0]["refs"]) == 1, f"rc={rc} {se[-300:]}")
    check({p.name for p in gens(root)} == before, "直した絵の置き場（鍵）が変わった")
    hist = list((area(root) / "history").glob("gen-*.png"))
    check(len(hist) == 1, hist)
    meta = json.loads(next(p for p in gens(root) if p.stem in hist[0].stem).with_suffix(".json").read_text())
    check(meta["revision"]["mode"] == "minor" and meta["revision"]["note"] == "人を一人足す", meta)
    check((out / "deck.pptx").is_file(), "直したあと deck を組んでいない")
    rc, so, se, calls = run(["revise", *base, "--page", "2", "--remake", "--note", "夜の景色に"], root)
    check(rc == 0 and len(calls) == 1 and calls[0]["refs"] == [], calls)
    rc, so, se, calls = run(["fill", "--script", str(sc)], root)
    check(rc == 0 and not calls, "直した絵を、次の fill が描き直した")
    rc, so, se, calls = run(["revise", *base, "--page", "3", "--note", "明るく"], root)
    check(rc == 2 and "持ち込みの絵" in se and not calls, se)
    rc, so, se, calls = run(["revise", *base, "--page", "2"], root)
    check(rc == 2 and "直す中身が無い" in se, se)


def t_cache_area():
    """描いた絵はキャッシュの区画（台本のフォルダごと）。assets/ は区画からのハードリンク・deck-info に区画の場所。
    前の置き場（台本のそばの images/gen-<鍵>.png）の絵は区画へ写して使い、元は消さない（ADR 0014）。"""
    import cache as K
    root = tmpdir()
    sc = script(root, pages())
    out = root / "out"
    rc, so, se, calls = run(["deck", "--script", str(sc), "--out", str(out), "--no-png", "--no-odp"], root)
    check(rc == 0 and len(calls) == 2 and len(gens(root)) == 2, se[-400:])
    check(not (root / "images").exists(), "台本のそばに images/ を作った")
    check(json.loads((area(root) / K.STAMP).read_text())["owner"] == str(root.resolve()), "区画の印に持ち主が無い")
    info = json.loads((out / "deck-info.json").read_text())
    check(info["cache"] == str(area(root)), f"deck-info に区画の場所が無い: {info.get('cache')}")
    linked = [p for p in (out / "assets").iterdir() if any(p.stat().st_ino == g.stat().st_ino for g in gens(root))]
    check(len(linked) == 2, f"描いた絵を assets/ に張っていない（写すと場所を二重に取る）: {sorted(p.name for p in (out / 'assets').iterdir())}")
    # 前の置き場: 区画を消し、台本のそばに置く → 描かずに写して使う・元は残す
    (root / "images").mkdir()
    for g in gens(root):
        shutil.copy2(g, root / "images" / g.name)
        shutil.copy2(g.with_suffix(".json"), root / "images" / g.with_suffix(".json").name)
    shutil.rmtree(area(root))
    rc, so, se, calls = run(["deck", "--script", str(sc), "--out", str(out), "--no-png", "--no-odp"], root)
    check(rc == 0 and not calls, f"前の置き場の絵を使わずに描いた: {len(calls)} {se[-300:]}")
    check(len(gens(root)) == 2 and len(list((root / "images").glob("gen-*.png"))) == 2, "区画へ写していない・元を消した")


def t_style_image_look():
    """スタイルの image_look・expects_images: 誤りは rc=2・visual は表紙に画像が無ければ警告。"""
    import deck_style as DS
    check(DS.load("standard")["image_look"] == "illustration" and DS.load("visual")["image_look"] == "photo", "組み込みの画風")
    root = tmpdir()
    (root / "s.yaml").write_text("name: x\nimage_look: oil\n")
    try:
        DS.load(str(root / "s.yaml"))
        raise AssertionError("知らない画風が通った")
    except deck.DeckError as e:
        check("image_look は" in str(e), str(e))
    sc = script(root, [{"type": "cover", "title": "t"}])
    with tempfile.TemporaryDirectory() as tmp:
        _, info = deck.build_html(sc, tmp, style="visual")
        check(any("画像を置く型だが" in w for w in info["warnings"]), info["warnings"])
        _, info = deck.build_html(sc, tmp)
        check(not any("画像を置く型だが" in w for w in info["warnings"]), "standard でも警告した")


TESTS = {k[2:]: v for k, v in globals().items() if k.startswith("t_")}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if name not in TESTS:
        print("使えるテスト: " + ", ".join(TESTS))
        sys.exit(2)
    TESTS[name]()
    print("ok")
