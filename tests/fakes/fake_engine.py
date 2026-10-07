#!/usr/bin/env python3
"""偽物のエンジン（`html_slide.py` / `image_slide.py` の `check` と `deck` の口だけ真似る）。

終了コードと進みの行を思いどおりに出して、描き係の読み方（rc の写し方・進みの数え方）を確かめる。
本物と同じく `--out` の直下に `slides/slide-NN.png` と記録（`build.json` / `deck-info.json`）を書く。

    FAKE_ENGINE_RC      deck の終了コード（既定 0）
    FAKE_ENGINE_PAGES   絵を出すページ（例 "1,3"。既定は全部・"none" は 1 枚も出さない）
    FAKE_ENGINE_SLEEP   1 ページごとに待つ秒（既定 0.05）
    FAKE_ENGINE_LOG     呼ばれた引数を書くフォルダ（任意）
    FAKE_ENGINE_AUTH_RC auth（ピッチの描く前の確かめ）の終了コード（既定 0）
"""
import json
import os
import pathlib
import sys
import time

sys.dont_write_bytecode = True


def main(kind: str) -> int:
    argv = sys.argv[1:]
    cmd = argv[0]
    log = os.environ.get("FAKE_ENGINE_LOG")
    if log:
        pathlib.Path(log).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(log) / f"{time.time():.6f}-{cmd}.json").write_text(json.dumps({"kind": kind, "argv": argv}))
    if cmd == "auth":
        rc = int(os.environ.get("FAKE_ENGINE_AUTH_RC", "0"))
        print("OK: ログイン済み" if rc == 0 else "NG: Codex にログインしていない", file=sys.stderr)
        return rc
    script = pathlib.Path(argv[argv.index("--script") + 1])
    leaked = [k for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "AZURE_OPENAI_API_KEY")
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
    (out / ("build.json" if kind == "image" else "deck-info.json")).write_text(json.dumps({"slides": rows}))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv.pop(1)))
