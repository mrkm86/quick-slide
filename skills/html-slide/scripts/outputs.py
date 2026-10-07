"""出力の選び方（--format）とサムネイル（html-slide の ADR 0012・image-slide の ADR 0013）。

- 出力は4つ: pptx（deck.pptx）／pdf（deck.pdf）／png（slides/slide-NN.png・1920×1080・渡せる見た目のまま）／
  thumbs（thumbs.png・全ページを縮めて並べた一覧）。--format で選び、省けば4つとも作る
- thumbs はページの画像を縮めて作るので、png を選ばなくてもページの画像は残る
- このファイルは html-slide と image-slide で同じもの（片方を直したら、もう片方へ写す。テストで見比べる）
"""
from __future__ import annotations

import pathlib

FORMATS = ("pptx", "pdf", "png", "thumbs")
COLS, ROWS = 4, 4                 # サムネイル1枚に 4列×4段（16ページまで。超えたら2枚目へ）
THUMB_W, THUMB_H = 480, 270       # 1ページの縮小（16:9）
LABEL_H = 64                      # ページ番号と見出しの欄
GAP, MARGIN, HEAD_H = 24, 40, 80
FONTS = (
    "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/YuGothB.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)


def formats_of(spec: str | None) -> tuple[str, ...]:
    """--format の値（pptx,pdf,png,thumbs をカンマで並べる）。省けば4つとも。誤りは ValueError。"""
    if not spec:
        return FORMATS
    got = [w.strip().lower() for w in spec.split(",") if w.strip()]
    bad = [w for w in got if w not in FORMATS]
    if bad or not got:
        raise ValueError(f"--format は {' / '.join(FORMATS)} をカンマで並べる（{spec!r}）")
    return tuple(f for f in FORMATS if f in got)


def label_of(spec: dict) -> str:
    """台本の1ページの見出し（サムネイルに添える）。image-slide の script_io.headline_of と同じ選び方。"""
    for k in ("headline", "title", "quote"):
        v = spec.get(k)
        if isinstance(v, str) and v.strip():
            return v.replace("**", "").replace("\n", "")
    if spec.get("value"):
        return f"{spec['value']}{spec.get('unit') or ''}"
    return str(spec.get("type") or "")


def _font(size: int):
    from PIL import ImageFont
    for p in FONTS:
        if pathlib.Path(p).is_file():
            return ImageFont.truetype(p, size, index=0)
    return ImageFont.load_default(size)


def _label(d, text: str, x: int, y: int, w: int) -> None:
    """名前（2行まで。入らなければ字を小さくし、それでも入らなければ … で切る）。"""
    for size in (20, 18, 16, 14):
        f = _font(size)
        lines, cur = [], ""
        for ch in text:
            if f.getlength(cur + ch) > w:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
        if len(lines) <= 2:
            break
    if len(lines) > 2:
        lines = [lines[0], lines[1][:-1] + "…"]
    for i, ln in enumerate(lines):
        d.text((x, y + i * (size + 6)), ln, font=f, fill="#1a2b4c")


def sheets(pngs: list[pathlib.Path], labels: list[str], title: str) -> list:
    """縮めたページに「NN  見出し」を添え、COLS×ROWS で並べた一覧画像（PIL の Image）の列。"""
    from PIL import Image, ImageDraw
    per = COLS * ROWS
    pages = max(1, (len(pngs) + per - 1) // per)
    cell_h = THUMB_H + LABEL_H
    W_ = MARGIN * 2 + COLS * THUMB_W + (COLS - 1) * GAP
    out = []
    for pg in range(pages):
        chunk = list(zip(pngs, labels))[pg * per:(pg + 1) * per]
        rows = max(1, (len(chunk) + COLS - 1) // COLS)
        H_ = HEAD_H + MARGIN + rows * cell_h + (rows - 1) * GAP + MARGIN
        sheet = Image.new("RGB", (W_, H_), "#eef1f5")
        d = ImageDraw.Draw(sheet)
        d.text((MARGIN, 26), title + (f"（{pg + 1}/{pages}）" if pages > 1 else ""), font=_font(30), fill="#1a2b4c")
        for k, (png, name) in enumerate(chunk):
            x = MARGIN + (k % COLS) * (THUMB_W + GAP)
            y = HEAD_H + MARGIN + (k // COLS) * (cell_h + GAP)
            with Image.open(png) as img:
                sheet.paste(img.convert("RGB").resize((THUMB_W, THUMB_H), Image.LANCZOS), (x, y))
            d.rectangle([x - 1, y - 1, x + THUMB_W, y + THUMB_H], outline="#c3cad6")
            _label(d, f"{pg * per + k + 1:02d}  {name}", x, y + THUMB_H + 8, THUMB_W)
        out.append(sheet)
    return out


def write_thumbs(pngs: list[pathlib.Path], labels: list[str], out_dir: pathlib.Path, title: str) -> list[pathlib.Path]:
    """サムネイルを書く。1枚なら thumbs.png、16ページを超えたら thumbs-01.png・thumbs-02.png …（前の回の分は消す）。"""
    for old in [out_dir / "thumbs.png", *out_dir.glob("thumbs-[0-9][0-9].png")]:
        old.unlink(missing_ok=True)
    ims = sheets(pngs, labels, title)
    paths = [out_dir / "thumbs.png"] if len(ims) == 1 else [out_dir / f"thumbs-{i:02d}.png" for i in range(1, len(ims) + 1)]
    for im, p in zip(ims, paths):
        im.save(p, optimize=True)
    return paths


def prune_pages(slides_dir: pathlib.Path, count: int) -> None:
    """前の回のページの画像のうち、今のページ数を超えるもの（slide-NN.png）を消す。ページを減らしたときに古い絵が残らないように。"""
    for p in slides_dir.glob("slide-[0-9][0-9].png"):
        if int(p.stem.split("-")[1]) > count:
            p.unlink()
