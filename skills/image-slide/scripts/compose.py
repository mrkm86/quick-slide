"""生成した絵を 1920×1080 にそろえ、下端の帯を検査し、確認用の合成画像を作る。

- normalize: 縦横比のずれが小さければ伸縮、横に長ければ左右を少し切る、それ以上は地の色で足す（文字を切らない）
- band_check: 下端の帯が無地か（フッタ・ページ番号を載せられるか）
- load_logo / place_logo: ロゴを html-slide と同じ規則で置く（329×44 に縮めて収める・--logo-position の角から順に・ADR 0008）
- overlay: 確認用 PNG と PDF のために、帯の上の部品を絵に焼く。PPTX では焼かずに PowerPoint の部品にする（pptx_out.py）
- burn_watermark: 透かし（--watermark）を絵に焼く。これだけは PPTX でも絵に焼いたまま渡す（消しにくくする・ADR 0010）
"""
from __future__ import annotations

import pathlib

import layout as L
import oscompat as OS

# 太い字の候補（游ゴシック → OS の日本語の字体）。在りかは OS で違う（oscompat）
BOLD_FONTS = OS.japanese_fonts("bold")
# 細い字（透かし）。テーマの regular が無ければこの順
REGULAR_FONTS = OS.japanese_fonts("regular")


def _rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def normalize(src: pathlib.Path, background: str):
    """(1920×1080 の RGB 画像, 何をしたか)。"""
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        im = im.convert("RGB")
    w, h = im.size
    target = L.W / L.H
    ratio = w / h
    info = {"source_size": [w, h]}
    if abs(ratio - target) / target <= L.RATIO_TOL:
        info["op"] = "resize"
    elif ratio > target and (w - h * target) / 2 / w <= L.CROP_SIDE_MAX:
        cw = round(h * target)
        x0 = (w - cw) // 2
        im = im.crop((x0, 0, x0 + cw, h))
        info.update(op="crop", cut_each_side=round((w - cw) / 2 / w, 4))
    else:
        im = ImageOps.pad(im, (round(max(w, h * target)), round(max(h, w / target))), color=_rgb(background))
        info["op"] = "pad"
    return im.resize((L.W, L.H), Image.LANCZOS), info


def band_check(im) -> dict:
    """下端の帯が無地か。代表色（中央値）から BAND_DIFF より離れた画素の割合で見る。"""
    from PIL import ImageStat
    band = im.crop((0, L.BAND_TOP, L.W, L.H))
    small = band.resize((L.W // 4, max(1, L.BAND_H // 4)))
    med = tuple(int(v) for v in ImageStat.Stat(small).median)
    px = list(small.getdata())
    far = sum(1 for p in px if max(abs(p[i] - med[i]) for i in range(3)) > L.BAND_DIFF)
    share = far / len(px)
    lum = (0.2126 * med[0] + 0.7152 * med[1] + 0.0722 * med[2]) / 255
    return {"ok": share <= L.BAND_MAX_SHARE, "share": round(share, 4), "color": "#%02x%02x%02x" % med,
            "dark": lum < 0.45}


def read_logo(src: pathlib.Path, mid: float = L.LOGO_MID_Y):
    """(透明な余白を切ったロゴの RGBA 画像, 右下に置くときの寸法)。寸法は html-slide の deck.load_logo と同じ
    {"x", "y", "w", "h", "scale", "warning", "trim"（切った範囲・切らなければ None）}。読めなければ ValueError。
    mid は下の行の中心（layout.row_mid。フッタか番号があるページは上がる）。"""
    from PIL import Image
    try:
        with Image.open(src) as im:
            im.load()
            rgba = im.convert("RGBA")
    except OSError as e:
        raise ValueError(f"ロゴの画像が読めない: {src.name}（{e}）") from e
    box = rgba.getchannel("A").getbbox()
    if box is None:
        raise ValueError(f"ロゴの画像が全部透明: {src.name}")
    trim = list(box) if box != (0, 0, *rgba.size) else None
    if trim:
        rgba = rgba.crop(box)
    return rgba, {**L.logo_geometry(*rgba.size, mid), "trim": trim}


def load_logo(src: pathlib.Path, out_dir: pathlib.Path, white: pathlib.Path | None = None, mid: float = L.LOGO_MID_Y) -> dict:
    """ロゴの透明な余白を切って <out_dir>/logo.png に写す。white（組織の公式の白いロゴ・--logo-white）があれば logo-white.png に。

    ロゴの画素には手を入れない（色を変えない・白抜きを作らない・縦横比を変えない。変えてよいのは大きさだけ・ADR 0009）。
    {"src", "file"（切った写し）, "size"（切った後の原寸）, "lum"（ロゴの明るさ）, "white_file", "white_lum", **read_logo の寸法}。"""
    rgba, geo = read_logo(src, mid)
    out_dir.mkdir(parents=True, exist_ok=True)
    file = out_dir / "logo.png"
    rgba.save(file)
    out = {"src": str(src), "file": file, "size": rgba.size, "lum": _logo_lum(rgba), "white_file": None, "white_lum": None,
           "white_src": None, **geo}
    if white:
        wimg, _ = read_logo(white)
        if abs(wimg.width / wimg.height - rgba.width / rgba.height) > 0.02 * rgba.width / rgba.height:
            raise ValueError(f"白いロゴ（--logo-white）の縦横比がロゴと違う（{wimg.width}×{wimg.height} と {rgba.width}×{rgba.height}）")
        wfile = out_dir / "logo-white.png"
        wimg.save(wfile)
        out.update(white_file=wfile, white_lum=_logo_lum(wimg), white_src=str(white))
    return out


def _lum(rgb) -> float:
    """相対輝度（WCAG）。"""
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _logo_lum(rgba) -> float:
    """ロゴの明るさ（不透明な画素の相対輝度の中央値）。"""
    import statistics
    small = rgba.copy()
    small.thumbnail((200, 200))
    px = [p for p in small.getdata() if p[3] > 200] or list(small.getdata())
    return statistics.median(_lum(p) for p in px)


def contrast(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def logo_file(logo: dict, place: dict) -> pathlib.Path:
    """その置き場で使うロゴの画像（公式の白いロゴか、そのままか）。"""
    return logo["white_file"] if place.get("variant") == "white" else logo["file"]


def ring_check(im, x: int, y: int, w: int, h: int) -> dict:
    """ロゴの枠の周り（枠＋上下左右 LOGO_RING px）が1色か。{"ok", "share"（物の割合）, "near"（1色の割合）, "color"（中央値）}。

    1色 = 代表色から BAND_DIFF より離れた画素が BAND_MAX_SHARE 以下（帯の検査と同じ）で、かつ
    LOGO_PLATE_SHARE 以上が代表色から LOGO_PLATE_DIFF 以内。ぼけた写真やグラデーションは「平ら」でも1色ではない。
    """
    from PIL import ImageStat
    m = L.LOGO_RING
    ring = im.crop((max(0, x - m), max(0, y - m), min(L.W, x + w + m), min(L.H, y + h + m))).convert("RGB")
    med = tuple(int(v) for v in ImageStat.Stat(ring).median)
    px = list(ring.getdata())
    dist = [max(abs(p[i] - med[i]) for i in range(3)) for p in px]
    share = sum(1 for v in dist if v > L.BAND_DIFF) / len(px)
    near = sum(1 for v in dist if v <= L.LOGO_PLATE_DIFF) / len(px)
    return {"ok": share <= L.BAND_MAX_SHARE and near >= L.LOGO_PLATE_SHARE, "share": round(share, 4),
            "near": round(near, 4), "color": "#%02x%02x%02x" % med}


def place_logo(im, logo: dict, *, band: str | None, position: str = "bottom-right", bleed: bool = False,
               background: str = "#ffffff") -> dict:
    """1枚の絵のどこにロゴを置くか（html-slide の deck.fit_logo_plates と同じ規則を、要素ではなく画素で見る）。

    ロゴの画素には手を入れない。読めるかは、ロゴの明るさと下の明るさのコントラスト比（LOGO_CONTRAST 以上）で見る。
    読めないときは、組織の公式の白いロゴ（--logo-white）があればそれ、無ければロゴの後ろに地を敷いて読めるようにする。
    position（--logo-position）の順（L.LOGO_POSITIONS）に角を試す:
    1. 1色の角: その色で同じ大きさの地を敷く（地は見えない）。帯を頼んで空いていた本文のページの下の角は、1色でなくても
       そこに置く（地は1色のときだけ）。帯が空かずに帯に板を敷いたページは、板の上の右下
    2. bleed（全面に描かせた本文のページ・visual）だけ: 平らな角（ぼけ・空）に地を敷かずに重ねる。それも無ければ、
       指定の角に、下の明るさで読めるならそのまま重ねる
    3. どれでも読めなければ（暗い岩の上など）、指定の角にステッカー（ロゴの周りに余白をとった角丸の地・地の色 background）
    帯を頼まないページ（表紙・章扉・写真）は 1 だけで、だめなら置かない（html-slide と同じ）。
    返り値は {"corner"（bottom-right・bottom-left・top-right）, "x", "y", "w", "h", "plate"（敷く色か None）,
    "plate_box"（地の枠）, "sticker"（bool）, "variant"（normal・white）} か {"off": True}。
    """
    def variant(color: str) -> str | None:
        g = _lum(_rgb(color))
        if contrast(logo["lum"], g) >= L.LOGO_CONTRAST:
            return "normal"
        if logo.get("white_lum") is not None and contrast(logo["white_lum"], g) >= L.LOGO_CONTRAST:
            return "white"
        return None

    def spot(corner, plate, v, sticker=False):
        x, y, w, h = L.logo_box(logo, corner)
        pad = L.LOGO_STICKER_PAD if sticker else 0
        return {"corner": corner, "x": x, "y": y, "w": w, "h": h, "plate": plate, "variant": v, "sticker": sticker,
                "plate_box": (x - pad, y - pad, w + 2 * pad, h + 2 * pad) if plate else None}

    def sticker(corner):
        for color in (background, "#ffffff", "#000000"):
            v = variant(color)
            if v:
                return spot(corner, color, v, sticker=True)
        return spot(corner, background, "normal", sticker=True)

    order = L.LOGO_POSITIONS[position]
    for corner in order:
        box = L.logo_box(logo, corner)
        if band and corner.startswith("bottom"):
            if band == "plate":
                v = variant(background)
                return spot(corner, None, v) if v else sticker(corner)
            r = ring_check(im, *box)
            v = variant(r["color"])
            if not v:
                return sticker(corner)
            # 周りが1色ならその色の地で細かい物を隠す。1色でなければ（淡い枠が帯に入りこんだ等）地は敷かずに重ねる
            # （地を敷くと淡い枠の角が四角く欠ける・2026-10-06 の週報）
            return spot(corner, r["color"] if r["ok"] else None, v)
        r = ring_check(im, *box)
        if r["ok"] and variant(r["color"]):
            return spot(corner, r["color"], variant(r["color"]))
    if not bleed:
        return {"off": True}
    for corner in order:
        r = ring_check(im, *L.logo_box(logo, corner))
        if r["share"] <= L.BAND_MAX_SHARE and variant(r["color"]):
            return spot(corner, None, variant(r["color"]))
    first = order[0]
    v = variant(ring_check(im, *L.logo_box(logo, first))["color"])
    return spot(first, None, v) if v else sticker(first)


def area_ink(im, box: tuple, theme: dict) -> str:
    """絵の上に直に載せる文字（全面に描かせたページのフッタ・ページ番号）の色。下が暗ければ白、明るければテーマの文字の色。"""
    from PIL import ImageStat
    x, y, w, h = box
    pad = 8
    med = ImageStat.Stat(im.crop((max(0, x - pad), max(0, y - pad), min(L.W, x + w + pad), min(L.H, y + h + pad))).convert("RGB")).median
    lum = (0.2126 * med[0] + 0.7152 * med[1] + 0.0722 * med[2]) / 255
    return "#ffffff" if lum < 0.45 else theme["ink"]


def text_color(band: dict, theme: dict, plate: bool) -> str:
    """帯の上の文字の色。板を敷くなら地の上なので文字の色、暗い帯なら白。"""
    if plate:
        return theme["ink"]
    return "#ffffff" if band["dark"] else theme["ink"]


def font_path(theme: dict, cfg: dict, weight: str = "bold") -> str | None:
    """字体のファイル。weight="bold"（フッタ・ページ番号）か "regular"（透かし）。"""
    ff = theme.get("font_files") or (cfg.get("fonts") or {}).get(theme["font"]) or {}
    order = ((ff.get("bold"), ff.get("regular"), *BOLD_FONTS) if weight == "bold" else
             (ff.get("regular"), *REGULAR_FONTS, ff.get("bold"), *BOLD_FONTS))
    for p in order:
        if p and pathlib.Path(p).expanduser().is_file():
            return str(pathlib.Path(p).expanduser())
    return None


def text_width(text: str, font_file: str | None) -> int:
    from PIL import ImageFont
    if not text:
        return 0
    f = ImageFont.truetype(font_file, L.TEXT_PX) if font_file else ImageFont.load_default()
    return int(round(f.getlength(text)))


def chrome_boxes(footer: str, number: str, place: dict | None, font_file: str | None, mid: float = L.LOGO_MID_Y) -> dict:
    """フッタとページ番号の枠 {"footer": (x, y, w, h) か None, "number": … か None}。左下のロゴならフッタをロゴの右へ。番号は中央下。
    mid は行の中心（layout.row_mid）。"""
    on = bool(place and not place.get("off"))
    nw = text_width(number, font_file)
    out = {"footer": None, "number": None}
    if footer:
        x, y, _, h = L.footer_box(place["w"] if on and place["corner"] == "bottom-left" else 0, bool(number), mid)
        out["footer"] = (x, y, text_width(footer, font_file), h)
    if number:
        out["number"] = L.page_number_box(nw, mid)
    return out


def inks(im, *, theme: dict, band: dict, plate: bool, bleed: bool, boxes: dict) -> dict:
    """フッタとページ番号の文字の色 {"footer", "number"}。帯の上なら帯の明るさで、全面の絵の上ならそれぞれの下の明るさで決める。"""
    if not bleed:
        c = text_color(band, theme, plate)
        return {"footer": c, "number": c}
    return {k: area_ink(im, b, theme) if b else theme["ink"] for k, b in boxes.items()}


def overlay(base, *, theme: dict, plate: bool, footer: str, number: str, ink: dict,
            logo: dict | None, place: dict | None, font_file: str | None, mid: float = L.LOGO_MID_Y):
    """確認用に、帯の上の部品（板・フッタ・ロゴの地・ロゴ・ページ番号）を焼いた画像を返す。

    位置は layout.py。logo は load_logo の返り値、place は place_logo の返り値（置かないページは {"off": True}）。
    ink は inks の返り値（フッタとページ番号の色）。"""
    from PIL import Image, ImageDraw, ImageFont
    im = base.copy()
    d = ImageDraw.Draw(im)
    if plate:
        d.rectangle((0, L.BAND_TOP, L.W, L.H), fill=_rgb(theme["background"]))
    font = ImageFont.truetype(font_file, L.TEXT_PX) if font_file else ImageFont.load_default()
    on = bool(logo and place and not place.get("off"))
    boxes = chrome_boxes(footer, number, place if on else None, font_file, mid)
    if footer:
        d.text(boxes["footer"][:2], footer, font=font, fill=_rgb(ink["footer"]), anchor="lt")
    if number:
        d.text(boxes["number"][:2], number, font=font, fill=_rgb(ink["number"]), anchor="lt")
    if on:
        x, y, w, h = place["x"], place["y"], place["w"], place["h"]
        if place["plate"]:
            px, py, pw, ph = place["plate_box"]
            if place.get("sticker"):
                d.rounded_rectangle((px, py, px + pw - 1, py + ph - 1), radius=L.LOGO_STICKER_RADIUS, fill=_rgb(place["plate"]))
            else:
                d.rectangle((px, py, px + pw - 1, py + ph - 1), fill=_rgb(place["plate"]))
        with Image.open(logo_file(logo, place)) as lg:
            lg = lg.convert("RGBA").resize((w, h), Image.LANCZOS)
            im.paste(lg, (x, y), lg)
    return im


def burn_watermark(im, text: str, font_file: str | None):
    """透かしを焼いた写しを返す（中央下の端・固定の薄いグレー・ADR 0010）。下の色は測らない。
    文字のベースラインを画面の下端から WATERMARK_BOTTOM px に置く（g・p などの下に伸びる字だけ、それより下へ出る）。"""
    from PIL import ImageDraw, ImageFont
    out = im.copy()
    if not text:
        return out
    font = ImageFont.truetype(font_file, L.WATERMARK_PX) if font_file else ImageFont.load_default()
    ImageDraw.Draw(out).text((L.W // 2, L.H - L.WATERMARK_BOTTOM), text, font=font, fill=_rgb(L.WATERMARK_COLOR), anchor="ms")
    return out
