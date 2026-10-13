#!/usr/bin/env python3
"""エンジンの偽物の codex（`skills/*/tests/fake_codex.py`）を呼び、描いた絵に呼ばれた回ごとに違う印を足す。

エンジンの偽物は毎回同じ絵を描くので、「絵だけ直した」「前の絵に戻した」を絵の中身で見分けられない。
この包みは偽物をそのまま呼び（振る舞いと記録は偽物のまま）、`$CODEX_HOME/generated_images/` に置かれた
一番新しい絵の左上に、呼ぶたびに違う色の四角を描き足す。本物の codex は呼ばない。

    FAKE_CODEX_INNER  包む偽物（既定は image-slide の tests/fake_codex.py）
"""
import os
import pathlib
import subprocess
import sys
import uuid

sys.dont_write_bytecode = True
HERE = pathlib.Path(__file__).resolve().parent
INNER = os.environ.get("FAKE_CODEX_INNER") or str(HERE.parents[1] / "skills" / "image-slide" / "tests" / "fake_codex.py")


def main() -> int:
    home = pathlib.Path(os.environ.get("CODEX_HOME", ""))
    before = set(home.glob("generated_images/*/*.png")) if home.is_dir() else set()
    data = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    r = subprocess.run([sys.executable, "-X", "utf8", INNER, *sys.argv[1:]], input=data, stdout=sys.stdout, stderr=sys.stderr)
    if r.returncode != 0 or not sys.argv[1:] or sys.argv[1] != "exec":
        return r.returncode
    new = sorted(set(home.glob("generated_images/*/*.png")) - before, key=lambda p: p.stat().st_mtime)
    if not new:
        return r.returncode
    # 並列に呼ばれても重ならない印（数えるファイルの読み書きは競るので使わない）
    mark = uuid.uuid4().bytes
    from PIL import Image, ImageDraw

    im = Image.open(new[-1]).convert("RGB")
    draw = ImageDraw.Draw(im)
    for i in range(4):
        draw.rectangle((i * 12, 0, i * 12 + 11, 11), fill=(mark[3 * i], mark[3 * i + 1], mark[3 * i + 2]))
    im.save(new[-1])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
