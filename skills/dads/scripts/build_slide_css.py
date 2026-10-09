#!/usr/bin/env python3
"""tokens/slide.json から tokens/slide.css を作る（標準ライブラリだけ）。slide.json が正本。slide.css は直接編集しない。

    python3 skills/dads/scripts/build_slide_css.py            # tokens/slide.css を書く
    python3 skills/dads/scripts/build_slide_css.py --check    # 今の slide.css が slide.json から作った中身と同じか（違えば rc=1）

変数名は `--slide-*`（DADS の公式トークン `--color-*`・`--font-*` などとは衝突しない）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

TOKENS = pathlib.Path(__file__).resolve().parent.parent / "tokens"
SRC = TOKENS / "slide.json"
OUT = TOKENS / "slide.css"

HEADER = """/*
 * スライド用ローカルデザインシステムのトークン（CSS 変数）。
 * 仕様: ../references/slide-local.md ／ 正本: slide.json（このファイルは scripts/build_slide_css.py が作る。直接編集しない）
 * 土台: デジタル庁デザインシステムのトークン（dads.css・MIT）。値は DADS のものをそのまま使い、スライド用の拡張は origin で区別する。
 * 変数名は --slide-*（DADS の --color-* などと衝突しない）。このファイル単体で使える（dads.css を読まなくてよい）。
 */
"""


def _num(v: float) -> str:
    return f"{v:g}"


def build(d: dict) -> str:
    L: list[str] = [HEADER, ":root {"]
    add = L.append

    c = d["canvas"]
    add(f"  --slide-canvas-width: {c['width']}px;")
    add(f"  --slide-canvas-height: {c['height']}px;")

    f = d["font"]
    add(f"  --slide-font-sans: {f['family']};")
    add(f"  --slide-font-mono: {f['mono']};")
    for k, v in f["weight"].items():
        add(f"  --slide-font-weight-{k.lower()}: {v};")

    t = d["type"]
    for s in t["scale"]:
        add(f"  --slide-fs-{s['px']}: {s['px']}px;")
    for k, v in t["line_height"].items():
        add(f"  --slide-lh-{k}: {_num(v)};")
    for k, v in t["letter_spacing_em"].items():
        add(f"  --slide-ls-{k}: {_num(v)}em;")
    for k, v in t["floor"].items():
        add(f"  --slide-fs-floor-{k}: {v}px;")
    for name, r in t["roles"].items():
        add(f"  --slide-t-{name}-size: var(--slide-fs-{r['size']});")
        add(f"  --slide-t-{name}-weight: var(--slide-font-weight-{r['weight'].lower()});")
        add(f"  --slide-t-{name}-line: var(--slide-lh-{r['line']});")
        add(f"  --slide-t-{name}-spacing: var(--slide-ls-{r['size']});")

    sp = d["space"]
    for k, v in sp["steps"].items():
        add(f"  --slide-space-{k}: {v}px;")
    add(f"  --slide-margin-x: {sp['page_margin_x']}px;")
    add(f"  --slide-margin-y: {sp['page_margin_y']}px;")

    g = d["grid"]
    add(f"  --slide-grid-columns: {g['columns']};")
    add(f"  --slide-grid-column: {g['column']}px;")
    add(f"  --slide-grid-gutter: {g['gutter']}px;")
    add(f"  --slide-grid-width: {g['width']}px;")
    for k, v in g["span"].items():
        add(f"  --slide-grid-span-{k}: {v}px;")

    for k, v in d["radius"].items():
        add(f"  --slide-radius-{k}: {v}px;")
    for k, v in d["radius_roles"].items():
        add(f"  --slide-radius-{k}: {v}px;")

    for k, v in d["elevation"].items():
        add(f"  --slide-elevation-{k}: {v};")
    for k, v in d["elevation_roles"].items():
        add(f"  --slide-elevation-{k}: " + ("none" if v == "none" else f"var(--slide-elevation-{v})") + ";")

    st = d["stroke"]
    add(f"  --slide-stroke-line: {st['line']}px;")
    add(f"  --slide-stroke-line-bold: {st['line_bold']}px;")

    ic = d["icon"]
    for k, v in ic["label_to_box"].items():
        add(f"  --slide-icon-for-{k}: {v}px;")
    for v in ic["front"]:
        add(f"  --slide-icon-front-{v}: {v}px;")

    for name, col in d["color"].items():
        add(f"  --slide-color-{name}: {col['hex']};")

    add("}")
    add("")
    add("/* 役ごとの文字（font-size・font-weight・line-height・letter-spacing を一度に当てる） */")
    for name in t["roles"]:
        add(f".slide-t-{name} {{")
        add("  font-family: var(--slide-font-sans);")
        add(f"  font-size: var(--slide-t-{name}-size);")
        add(f"  font-weight: var(--slide-t-{name}-weight);")
        add(f"  line-height: var(--slide-t-{name}-line);")
        add(f"  letter-spacing: var(--slide-t-{name}-spacing);")
        add("}")
    add("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="今の slide.css が slide.json から作った中身と同じか")
    a = ap.parse_args()
    css = build(json.loads(SRC.read_text(encoding="utf-8")))
    if a.check:
        if OUT.read_text(encoding="utf-8") != css:
            print("NG: slide.css が slide.json と合っていない（scripts/build_slide_css.py で作り直す）", file=sys.stderr)
            return 1
        print("OK: slide.css は slide.json と同じ")
        return 0
    OUT.write_text(css, encoding="utf-8")
    print(f"{OUT}: {css.count(chr(10))} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
