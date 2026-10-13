#!/usr/bin/env python3
"""偽物のエンジン（`html_slide.py` / `image_slide.py` / `motion_slide.py` の `check` と `deck` の口だけ真似る）。

終了コードと進みの行を思いどおりに出して、描き係の読み方（rc の写し方・進みの数え方）を確かめる。
本物と同じく `--out` の直下に `slides/slide-NN.png` と記録（`build.json` / `deck-info.json`）を書く。

    FAKE_ENGINE_RC      deck の終了コード（既定 0）
    FAKE_ENGINE_PAGES   絵を出すページ（例 "1,3"。既定は全部・"none" は 1 枚も出さない）
    FAKE_ENGINE_SLEEP   1 ページごとに待つ秒（既定 0.05）
    FAKE_ENGINE_LOG     呼ばれた引数を書くフォルダ（任意）
    FAKE_ENGINE_AUTH_RC auth（ピッチの描く前の確かめ）の終了コード（既定 0）
    FAKE_ENGINE_QUICK_RC auth --quick（形式の札に出す軽い確かめ）の終了コード（既定 0。4 なら「codex が見つからない」と言う）。
                        画面を開くたびに呼ばれるので、FAKE_ENGINE_LOG には書かない
    FAKE_ENGINE_FONT    プレゼンの deck.html が file:// で読む字体ファイル（任意。発表の画面の字体の配信の試験が使う）
    FAKE_ENGINE_SKIP    作らない出力（例 "pdf"。既定は deck.pptx・deck.pdf も書く。書き出しの試験が使う）

motion（`motion_slide.py` の形）: 台本は `scenes`（場面）。`check` は `--quick`（形だけ）と全拍の検査を区別して呼ばれた引数を記録し、
`id` が `bad-scene` の場面を NG にする（`FAKE_ENGINE_FULL_NG` を立てると全拍の検査だけが NG にする）。`deck` は
`index.html`（本物のエンジンを埋めた HTML 1枚）・`deck.html`・`build.json`・`deck-info.json`（場面ごとの `beats`・`clicks`）・
`slides/slide-NN.png`・`thumbs.png`・`inspect.md`・`check/` を書き、標準出力に `index:`・`png:`・`thumbs:` の行を出す
（進みの行は標準エラーに出さない。本物と同じ）。
"""
import json
import os
import pathlib
import sys
import time

sys.dont_write_bytecode = True


def write_html(out: pathlib.Path, slides: list, skip: set) -> None:
    """html-slide の出力の形（section・data-step・assets/・file:// の字体）の deck.html と、段の数を持つ build.json。
    ページ i（1 から）の段の数は i % 3（1→1・2→2・3→0）。"""
    from urllib.parse import quote
    from PIL import Image
    font = os.environ.get("FAKE_ENGINE_FONT")
    face = (f'@font-face {{ font-family: "Fake"; font-weight: 400; src: url("file://{quote(font)}"); }}' if font else "")
    (out / "assets").mkdir(exist_ok=True)
    Image.new("RGB", (8, 8), (10, 20, 30)).save(out / "assets" / "s01-image.png")
    sections, rows = [], []
    for i, s in enumerate(slides, 1):
        steps = i % 3
        body = "".join(f'<div data-step="{n}" data-fx="pop" data-delay="{n * 10}">段{n}</div>' for n in range(1, steps + 1))
        sections.append(f'<section data-slide data-transition="fade">'
                        f'<h1 data-step="0">{i}</h1><img src="assets/s01-image.png" alt="">{body}'
                        f'<aside class="notes">ノート</aside></section>')
        rows.append({"no": i, "type": s.get("type"), "steps": steps, "transition": "fade"})
    if "html" not in skip:
        (out / "deck.html").write_text(f'<!doctype html><html><head><meta charset="UTF-8"><style>{face}</style></head>'
                                       f'<body>{"".join(sections)}</body></html>', encoding="utf-8")
    (out / "build.json").write_text(json.dumps({"slides": rows}), encoding="utf-8")


def motion_scenes_html(scenes: list) -> str:
    """本物の motion-slide のエンジン（engine.js・engine.css）を埋めた、場面だけの HTML 1枚（発表の画面の橋渡しを本物のエンジンで確かめる）。"""
    skill = pathlib.Path(__file__).resolve().parents[2] / "skills" / "motion-slide" / "engine"
    css = (skill / "engine.css").read_text(encoding="utf-8")
    js = (skill / "engine.js").read_text(encoding="utf-8")
    sections = []
    for i, s in enumerate(scenes):
        beats = int(s.get("beats") or 1)
        sections.append(f'<section class="scene" id="{s["id"]}" data-beats="{beats}" data-title="{s.get("title") or s["id"]}">'
                        + "".join(f'<h1 class="x" data-in="{b}" style="position:absolute;left:200px;top:{200 + 120 * b}px;'
                                  f'font-size:72px">{s.get("title")} 拍{b + 1}</h1>' for b in range(beats))
                        + (f'<button id="live-{s["id"]}" class="live-zone" style="position:absolute;right:200px;top:500px">'
                           f'触れる部品</button><div id="ce" contenteditable="true" style="position:absolute;left:200px;top:800px;'
                           f'width:300px;height:80px;font-size:40px">編集</div>'
                           f'<div id="ce2" contenteditable="true" onclick="MS.go(2,0)" style="position:absolute;left:700px;top:800px;'
                           f'width:300px;height:80px;font-size:40px">編集2</div>'
                           f'<div class="live-zone" style="position:absolute;left:1200px;top:800px;width:300px;height:80px">'
                           f'<span id="lz" role="button" tabindex="0" onclick="MS.go(3,0)" style="display:block;width:300px;height:80px;'
                           f'font-size:40px">LIVE</span></div>' if i == 0 else "") + "</section>")
    return (f'<!doctype html><html><head><meta charset="UTF-8"><title>偽物のモーション</title><style>{css}</style>'
            f'<script type="application/json" id="ms-config">{{"crumb": "偽物", "chapters": []}}</script>'
            f'<script>{js}</script></head><body><div id="stage">{"".join(sections)}</div></body></html>')


def main_motion(argv: list) -> int:
    cmd = argv[0]
    script = pathlib.Path(argv[argv.index("--script") + 1])
    leaked = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY") if k in os.environ]
    if leaked:
        print("NG: API キーが渡ってきた", file=sys.stderr)
        return 5
    # テーマとロゴだけ受ける（スタイル・画風・ロゴの角・透かしは渡ってこない）
    unknown = [a for a in argv if a in ("--style", "--image-look", "--logo-position", "--watermark", "--logo-white")]
    if unknown:
        print(f"NG: motion に渡ってはいけない引数: {unknown}", file=sys.stderr)
        return 5
    data = json.loads(script.read_text(encoding="utf-8"))
    if "slides" in data or "scenes" not in data:
        print("NG: 台本は scenes で渡す", file=sys.stderr)
        return 5
    scenes = data["scenes"]
    if cmd == "check":
        bad = [i for i, s in enumerate(scenes, 1) if s.get("id") == "bad-scene"]
        quick = "--quick" in argv
        if os.environ.get("FAKE_ENGINE_FULL_NG") and not quick:
            bad = bad or [1]
        for i in bad:
            print(f"NG: {i}枚目（id {scenes[i - 1]['id']}）: 場面の形が決まりに合わない", file=sys.stderr)
        if not bad:
            print("OK")
        return 2 if bad else 0
    out = pathlib.Path(argv[argv.index("--out") + 1])
    rc = int(os.environ.get("FAKE_ENGINE_RC", "0"))
    if rc in (2, 4):
        print("NG: 偽物のエンジンが止まった", file=sys.stderr)
        return rc
    time.sleep(float(os.environ.get("FAKE_ENGINE_SLEEP", "0.05")))
    from PIL import Image
    out.mkdir(parents=True, exist_ok=True)
    (out / "slides").mkdir(exist_ok=True)
    (out / "check" / "beats").mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(scenes, 1):
        Image.new("RGB", (192, 108), (20 * i % 255, 80, 160)).save(out / "slides" / f"slide-{i:02d}.png")
        for b in range(1, int(s.get("beats") or 1) + 1):
            Image.new("RGB", (192, 108), (20 * i % 255, 10 * b, 160)).save(out / "check" / "beats" / f"s{i:02d}-b{b}.png")
    skip = set(filter(None, os.environ.get("FAKE_ENGINE_SKIP", "").split(",")))
    index = motion_scenes_html(scenes)
    if "index" not in skip:
        (out / "index.html").write_text(index, encoding="utf-8")
    (out / "deck.html").write_text("<!doctype html><title>deck</title>", encoding="utf-8")
    (out / "inspect.md").write_text("# 検査の記録（偽物）\n", encoding="utf-8")
    (out / "check" / "inspect.md").write_text("# 検査の記録（偽物）\n", encoding="utf-8")
    rows = [{"no": i, "id": s["id"], "title": s.get("title"), "beats": int(s.get("beats") or 1),
             "clicks": int(s.get("beats") or 1) - 1, "page": f"slides/slide-{i:02d}.png"} for i, s in enumerate(scenes, 1)]
    record = {"engine": "motion-slide", "scenes": len(scenes), "slides": rows}
    for name in ("build.json", "deck-info.json"):
        (out / name).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    Image.new("RGB", (192, 108), (5, 5, 5)).save(out / "thumbs.png")
    print(f"index: {out / 'index.html'}（{len(index) // 1024}KB）")
    print(f"png: {out / 'slides'}（{len(scenes)}枚）")
    print(f"thumbs: {out / 'thumbs.png'}")
    return 0


def main(kind: str) -> int:
    argv = sys.argv[1:]
    cmd = argv[0]
    if cmd == "auth" and "--quick" in argv:
        rc = int(os.environ.get("FAKE_ENGINE_QUICK_RC", "0"))
        print("OK: codex がある" if rc == 0 else "NG: codex が見つからない", file=sys.stderr)
        return rc
    log = os.environ.get("FAKE_ENGINE_LOG")
    if log:
        pathlib.Path(log).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(log) / f"{time.time():.6f}-{cmd}.json").write_text(json.dumps({"kind": kind, "argv": argv}), encoding="utf-8")
    if cmd == "auth":
        rc = int(os.environ.get("FAKE_ENGINE_AUTH_RC", "0"))
        print("OK: ログイン済み" if rc == 0 else "NG: Codex にログインしていない", file=sys.stderr)
        return rc
    if kind == "motion":
        return main_motion(argv)
    script = pathlib.Path(argv[argv.index("--script") + 1])
    leaked = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY")
              if k in os.environ]
    if leaked:
        print("NG: API キーが渡ってきた", file=sys.stderr)
        return 5
    slides = json.loads(script.read_text(encoding="utf-8"))["slides"]
    if cmd == "check":
        bad = [i for i, s in enumerate(slides, 1) if s.get("type") == "no-such-type"]
        for i in bad:
            print(f"NG: {i}枚目: 知らない型", file=sys.stderr)
        if not bad:
            print("OK")
        return 2 if bad else 0
    out = pathlib.Path(argv[argv.index("--out") + 1])
    rc = int(os.environ.get("FAKE_ENGINE_RC", "0"))
    want = os.environ.get("FAKE_ENGINE_PAGES", "")
    pages = [] if want == "none" else ([int(x) for x in want.split(",")] if want else list(range(1, len(slides) + 1)))
    sleep = float(os.environ.get("FAKE_ENGINE_SLEEP", "0.05"))
    if rc in (2, 4):
        print("NG: 偽物のエンジンが止まった", file=sys.stderr)
        return rc
    if kind == "image":
        print(f"{len(slides)}枚（描く {len(slides)}・使い回す 0）→ {out}", file=sys.stderr, flush=True)
        for i in range(1, len(slides) + 1):
            time.sleep(sleep)
            print(f"  {'✓' if i in pages else '✗'} {i}枚目  [{i}/{len(slides)}]", file=sys.stderr, flush=True)
    else:
        print("画像の枠 2（生成 2・描く 2）", file=sys.stderr, flush=True)
        for i in (1, 2):
            time.sleep(sleep)
            print(f"  ✓ {i}.  [{i}/2]", file=sys.stderr, flush=True)
        if rc == 1:
            print("警告: 1枚目の image を描けなかった（gen: 偽物）", file=sys.stderr, flush=True)
    from PIL import Image
    (out / "slides").mkdir(parents=True, exist_ok=True)
    for i in pages:
        Image.new("RGB", (192, 108), (20 * i % 255, 80, 160)).save(out / "slides" / f"slide-{i:02d}.png")
    rows = [{"no": i, "type": s.get("type"), "status": "generated" if i in pages else "failed"}
            for i, s in enumerate(slides, 1)]
    (out / ("build.json" if kind == "image" else "deck-info.json")).write_text(json.dumps({"slides": rows}), encoding="utf-8")
    skip = set(filter(None, os.environ.get("FAKE_ENGINE_SKIP", "").split(",")))
    if kind != "image":
        write_html(out, slides, skip)
    body = json.dumps([s.get("title") or s.get("type") for s in slides], ensure_ascii=False).encode("utf-8")
    if "pptx" not in skip:
        (out / "deck.pptx").write_bytes(b"PK\x03\x04 fake pptx " + body)
    if "pdf" not in skip:
        (out / "deck.pdf").write_bytes(b"%PDF-1.4 fake " + body)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv.pop(1)))
