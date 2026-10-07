#!/usr/bin/env python3
"""資料の台本（JSON）→ HTML。出す HTML は references/contract.md の決まりに沿う。

    python3 deck.py --script <json> --out <dir> [--theme <name|yaml>] [--mode talk|handout]

<dir>/deck.html と <dir>/assets/（台本の画像のコピー）を出す。台本の誤りは rc=2（DeckError）。
入口の html_slide.py からは build_html() を呼ぶ。台本の outline と role は読み飛ばす（ストーリーテンプレートはやめた・ADR 0004）。

- テーマ: --theme > 台本の theme > 設定の default_theme > default。名前は themes/<名前>.yaml、パスも書ける
- 字体: テーマの font の名前で、設定（places.py の置き場の config.yaml）の fonts を引く。
  無ければ游ゴシックの既定のパス（deck_types.DEFAULT_FONTS）。テーマの font_files で直に差し替えもできる
- 色: テーマの background・ink・primary（と任意の差し色 accent）から、型が使う色を全部作り、文字と地の組み合わせを検査する（読めなければ rc=2）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import sys
import urllib.parse

sys.dont_write_bytecode = True   # 置いた場所（配る物の中・ほかの環境からのリンク先）に __pycache__ を残さない
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cache as K  # noqa: E402
import deck_style as DS  # noqa: E402
import deck_types as T  # noqa: E402
import places as PL  # noqa: E402
from deck_types import DeckError  # noqa: E402,F401  （入口から deck.DeckError で引けるように）

SKILL = pathlib.Path(__file__).resolve().parent.parent
THEMES = SKILL / "themes"
TEMPLATE = SKILL / "templates" / "deck.html"
MODES = ("talk", "handout")
TRANSITIONS = ("morph", "fade", "none")
TOP_KEYS = {"title", "theme", "mode", "footer", "page_numbers", "outline", "slides"}
THEME_KEYS = {"background", "ink", "primary", "accent", "muted", "surface", "font", "font_files"}
CLICK = "［クリック］"

# ロゴ（--logo）: 右下の枠に、縦横比を保って収める（縮めるだけ・引き伸ばさない）。枠の寸法は quick-slide の
# placement.py の既定（1920×1080 で 329×44）と同じ。縦は下の文字の行の中心にそろえ、右は本文の余白（120px）にそろえる
LOGO_BOX_W, LOGO_BOX_H = 329, 44
LOGO_RIGHT = 120
LOGO_MID_Y = 1006 + 22 * 1.4 / 2      # .footer・.pageno の行の中心（templates/deck.html）
LOGO_GAP = 28                         # ロゴと下の文字の間
FOOTER_LEFT, FOOTER_END = 120, 1420   # 下の文字（.footer）の左端と右端
FOOTER_END_WITH_NO = 912              # ページ番号（中央下・900〜1020px）を出すときの下の文字の右端（番号の左 48px まで）
LOGO_MIN_SIDE = 30                    # 短辺がこれ未満なら小さいと警告する（quick-slide の small_logo と同じ 2.8%）
LOGO_EXT = {".png", ".jpg", ".jpeg"}
LOGO_TOP_MID_Y = 46                   # 右上に置くときの縦の中心（44px のロゴが 24〜68px。見出しの .head は 80px から）
# --logo-position: 指定した角を先に試し、ふさがっていれば次へ（ADR 0008）。上下をまたいで逃がすのは右上 → 右下だけ
# （右の列は変わらない）。左上は選べない
LOGO_POSITIONS = {
    "bottom-right": ("bottom-right", "bottom-left"),
    "bottom-left": ("bottom-left", "bottom-right"),
    "top-right": ("top-right", "bottom-right"),
}
LOGO_KEEP = {"bottom-right": "", "bottom-left": "-left", "top-right": "-top"}   # 角ごとに別の名前（同じ名前だとモーフで滑る）
# 透かし（--watermark）: 全ページの中央下の端に、固定の薄いグレーで（ADR 0009）。
# 数字は image-slide の layout.py と同じ（image-slide の tests が見比べる）。下の文字の行（1006〜1037px）とは重ならない
WATERMARK_PX = 16                     # 文字の大きさ（1080 の 1.5%）
WATERMARK_COLOR = "#9ca3af"           # Gray-400。下の色は測らない
WATERMARK_BOTTOM = 13                 # 文字のベースラインから画面の下端まで（1080 の 1.2%）
WATERMARK_TOP = 1054                  # .watermark の箱の上端（templates/deck.html）。line-height 1 の Yu Gothic でベースラインは上端から 13px → 1067px（Chrome で実測）


# --- 色 ----------------------------------------------------------------------------

def _hex(v, what) -> str:
    s = str(v).strip().strip("'\"").lower()
    if len(s) != 7 or s[0] != "#" or any(c not in "0123456789abcdef" for c in s[1:]):
        raise DeckError(f"テーマの {what} は #rrggbb で書く: {v!r}")
    return s


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _to_hex(c):
    return "#" + "".join(f"{round(max(0, min(1, v)) * 255):02x}" for v in c)


def mix(fg, alpha, bg) -> str:
    """fg を alpha で bg に重ねた色。"""
    return _to_hex(tuple(alpha * a + (1 - alpha) * b for a, b in zip(_rgb(fg), _rgb(bg))))


def ratio(a, b) -> float:
    def lum(h):
        def ch(v):
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b_ = (ch(v) for v in _rgb(h))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def palette(t: dict) -> dict:
    """テーマの色（地・文字・主役の色 primary・差し色 accent。任意で muted・surface）から型が使う色を作る。

    色は2つの系統。どちらも同じ作り方で4つの役を持つ（2026-10-05）:
      primary … 骨格（上の帯・小見出し・章扉・番号の丸・矢羽根・印・連絡先・主張の全面）。ブランドの主役の色
      accent  … 差し色（台本の *語*・accent: true の項目・強調する棒と線・表の強調する列）。書かなければ primary と同じ
    役（primary を例に。accent も同じ名前で accent-…）:
      primary         … 面（帯・棒・矢羽根・線・印）。テーマの色そのまま
      primary-text    … 文字（小見出し・番号・数字）。地の上で 4.5 を越えるまで、色相を保って暗くした色
      primary-strong  … 文字を載せる面（章扉・番号の丸・連絡先）。白か文字の色が 4.5 を越える色。届かなければ暗くする
      on-primary      … その面に載せる文字（白か文字の色の、読める方）
      primary-soft    … 淡い地（強めた目次の行）。accent-soft は強調する箱・表の列の地
      primary-on-dark … 写真の上（暗幕の地）の色
    """
    bg, ink = t["background"], t["ink"]
    primary, accent = t["primary"], t.get("accent") or t["primary"]
    white = "#ffffff"
    p = {
        "bg": bg, "ink": ink, "primary": primary, "accent": accent,
        "muted": t.get("muted") or mix(ink, 0.72, bg),           # 下で、淡い地の上でも読める濃さに寄せる（テーマで書いたときはそのまま）
        "surface": t.get("surface") or mix(ink, 0.04, bg),
        "primary-soft": mix(primary, 0.07, bg),   # 強めた目次の地・スタイルの look: surface primary の箱の地
        "accent-soft": mix(accent, 0.07, bg),     # 強めたカード・比較の列・表の列・吹き出しの地
        "tint": mix(primary, 0.13, bg),           # 矢羽根の地
        "line": mix(ink, 0.14, bg),               # 区切りの線・比較の札
        "bar": mix(ink, 0.24, bg),                # 強めない棒・年表の丸
    }
    grounds = [bg, p["surface"], p["primary-soft"], p["accent-soft"], p["tint"]]
    if not t.get("muted"):
        p["muted"] = _readable(p["muted"], grounds, 4.5)
    # 写真の上（暗幕の地）: 色を白に寄せて、ink の地で 4.5・一番薄い暗幕を白い写真に重ねた地で 3.0 を越える一番濃い色
    thin = mix(ink, min(T.OVERLAYS.values()), white)
    for name, color in (("primary", primary), ("accent", accent)):
        p[f"{name}-on-dark"] = next((c for c in (mix(color, 1 - k / 40, white) for k in range(41))
                                     if ratio(c, ink) >= 4.5 and ratio(c, thin) >= 3.0), white)
        p[f"{name}-text"] = _readable(color, grounds, 4.5)
        # 文字を載せる面は、なるべく白抜き（2026-10-05）。濃い色の背景やカードに文字を載せるなら白抜きにする。
        # 白が読めない色は、色相を保って白が読める濃さまで暗くする。ただし暗くする量が WHITE_DROP を越える明るい色
        # （黄・橙・黄緑など）は、色を変えすぎるので、そのままの色に文字の色（ink）を載せる
        if ratio(white, color) >= 4.5:
            strong = color
        else:
            dark = _readable(color, [white], 4.5)
            strong = dark if _drop(color, dark) <= WHITE_DROP or ratio(ink, color) < 4.5 else color
        p[f"{name}-strong"] = strong
        p[f"on-{name}"] = white if ratio(white, strong) >= 4.5 else ink
    r, g, b = (int(p["ink"][i:i + 2], 16) for i in (1, 3, 5))
    p["scrim"] = f"rgba({r}, {g}, {b}, {T.SCRIM_ALPHA})"      # 全面写真の上の文字の地
    p["scrim-0"] = f"rgba({r}, {g}, {b}, 0)"                  # 暗幕のグラデーションの透明な端
    p["scrim-quote"] = f"rgba({r}, {g}, {b}, {T.QUOTE_ALPHA})"  # 全面の写真に重ねる引用の暗幕
    vr, vg, vb = (int(bg[i:i + 2], 16) for i in (1, 3, 5))
    p["veil"] = f"rgba({vr}, {vg}, {vb}, {T.HERO_VEIL})"      # 全面の表紙の題の下の幕（背景の色）
    p["veil-0"] = f"rgba({vr}, {vg}, {vb}, 0)"
    p["shadow"] = f"rgba({r}, {g}, {b}, 0.12)"                 # 白い箱の影（スタイルの look: surface white）
    return p


WHITE_DROP = 0.32   # 白抜きのために暗くしてよい明るさの割合（シアン #0db4f2 は 0.30・青 #399dd1 は 0.21 で白抜き、黄 #f5a623 は 0.39 で黒の文字）


def _drop(color: str, dark: str) -> float:
    """color から dark へ、明るさ（HLS の L）が何割下がったか。"""
    import colorsys
    l0 = colorsys.rgb_to_hls(*_rgb(color))[1]
    return 0.0 if l0 == 0 else 1 - colorsys.rgb_to_hls(*_rgb(dark))[1] / l0


def _readable(color: str, grounds: list[str], need: float) -> str:
    """color の色相・彩度を保ち、どの地の上でも need を越えるまで暗くした色（初めから越えていればそのまま）。"""
    import colorsys
    r, g, b = _rgb(color)
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    for k in range(0, 101):
        c = _to_hex(colorsys.hls_to_rgb(h, max(0.0, l * (1 - k / 100)), sat))
        if all(ratio(c, gr) >= need for gr in grounds):
            return c
    return "#000000"


def contrast_problems(p: dict) -> list[str]:
    """実際に描く「文字 × 地」の組み合わせで、読めないものを返す（空なら合格）。"""
    need = []
    grounds = ("bg", "surface", "primary-soft", "accent-soft", "tint")
    for fg in ("ink", "muted", "primary-text", "accent-text"):
        for g in grounds:
            need.append((fg, g, 4.5))
    need += [("primary-text", "line", 3.0), ("accent-text", "line", 3.0), ("ink", "line", 4.5),
             ("on-primary", "primary-strong", 4.5), ("on-accent", "accent-strong", 4.5), ("ink", "bar", 3.0)]
    bad = []
    for fg, g, n in need:
        r = ratio(p[fg], p[g])
        if r < n - 1e-9:
            bad.append(f"{fg}（{p[fg]}）× {g}（{p[g]}）のコントラスト {r:.2f} が {n} 未満")
    return bad


def overlay_problems(p: dict, used: dict) -> list[str]:
    """写真の上の文字の幕のうち、台本で使ったもの（used: 名前 → 枚目）だけを検査する。

    暗い幕（ink の半透明）は一番明るい（白い）写真に、明るい幕（背景の色の半透明）は一番暗い（黒い）写真に重ねた色を地とする。
    濃さは型ごとに T.OVERLAYS・T.LIGHT_OVERLAYS（グラデーションなら文字がある範囲での最小）
    """
    bad = []
    for name, no in used.items():
        if name in T.OVERLAYS:
            alpha = T.OVERLAYS[name]
            ground = mix(p["ink"], alpha, "#ffffff")
            for fg, val, n in (("白の文字", "#ffffff", 4.5), ("主役の色（primary-on-dark）", p["primary-on-dark"], 3.0),
                               ("差し色（accent-on-dark）", p["accent-on-dark"], 3.0)):
                r = ratio(val, ground)
                if r < n - 1e-9:
                    bad.append(f"{no}枚目 {name}の{fg}（{val}）× 暗幕（ink を {alpha} で白い写真に重ねた {ground}）のコントラスト {r:.2f} が {n} 未満")
        if name in T.LIGHT_OVERLAYS:
            alpha = T.LIGHT_OVERLAYS[name]
            ground = mix(p["bg"], alpha, "#000000")
            for fg, n in (("ink", 4.5), ("primary-text", 3.0), ("accent-text", 3.0)):   # 幕の上では副題・日付も ink で書く（muted は使わない）。小見出しと強調の語は文字の色
                r = ratio(p[fg], ground)
                if r < n - 1e-9:
                    bad.append(f"{no}枚目 {name}の {fg}（{p[fg]}）× 幕（背景の色を {alpha} で黒い写真に重ねた {ground}）のコントラスト {r:.2f} が {n} 未満")
    return bad


# --- 設定とテーマ ------------------------------------------------------------------

def load_config() -> dict:
    return PL.load_config("html-slide")


def resolve_theme(spec, script_dir: pathlib.Path, cfg: dict) -> pathlib.Path:
    name = str(spec or cfg.get("default_theme") or "default")
    if "/" in name or name.endswith((".yaml", ".yml")):
        p = pathlib.Path(name).expanduser()
        path = p if p.is_absolute() else (script_dir / p)
    else:
        path = THEMES / f"{name}.yaml"
    if not path.is_file():
        have = ", ".join(sorted(x.stem for x in THEMES.glob("*.yaml")))
        raise DeckError(f"テーマが無い: {name}（あるのは {have}）")
    return path.resolve()


def load_theme(path: pathlib.Path) -> dict:
    import yaml
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise DeckError(f"{path}: テーマが辞書ではない")
    extra = sorted(set(raw) - THEME_KEYS)
    if extra:
        raise DeckError(f"{path}: テーマに知らないキー: {', '.join(extra)}（使えるのは {', '.join(sorted(THEME_KEYS))}）")
    t = {"name": path.stem, "font": str(raw.get("font") or "Yu Gothic"), "font_files": raw.get("font_files")}
    if any(c in t["font"] for c in '"\\<>;{}'):
        raise DeckError(f"{path}: font の名前に使えない文字がある（CSS にそのまま入るため）: {t['font']}")
    if "accent" in raw and "primary" not in raw:
        raise DeckError(f"{path}: テーマの主役の色は primary に名前が変わった（accent は差し色・任意）。accent: を primary: に書き換える")
    for k in ("background", "ink", "primary"):
        if k not in raw:
            raise DeckError(f"{path}: テーマに {k} が無い")
        t[k] = _hex(raw[k], k)
    for k in ("accent", "muted", "surface"):
        if raw.get(k):
            t[k] = _hex(raw[k], k)
    return t


def font_files(theme: dict, cfg: dict) -> dict:
    """測る字体ファイル（regular・bold）。テーマの font_files > 設定の fonts[字体名] > 游ゴシックの既定。"""
    ff = theme.get("font_files") or (cfg.get("fonts") or {}).get(theme["font"])
    if ff is None:
        if theme["font"] != "Yu Gothic":
            raise DeckError(f"字体 {theme['font']} のファイルが分からない（設定の fonts か テーマの font_files に書く）")
        ff = T.DEFAULT_FONTS
    if not isinstance(ff, dict) or not ff.get("regular"):
        raise DeckError(f"字体 {theme['font']} の指定は regular と bold のパスで書く")
    reg = str(pathlib.Path(ff["regular"]).expanduser())
    bold = str(pathlib.Path(ff.get("bold") or ff["regular"]).expanduser())
    return {"regular": reg, "bold": bold}


def font_faces(family: str, files: dict) -> str:
    def url(p):
        return "file://" + urllib.parse.quote(p)
    return (f'@font-face {{ font-family: "{family}"; font-weight: 400; src: url("{url(files["regular"])}"); }}\n'
            f'      @font-face {{ font-family: "{family}"; font-weight: 700; src: url("{url(files["bold"])}"); }}')


# --- 台本 --------------------------------------------------------------------------

def load_script(path: pathlib.Path) -> dict:
    try:
        sc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DeckError(f"台本が無い: {path}")
    except json.JSONDecodeError as e:
        raise DeckError(f"台本の JSON が読めない: {path}（{e}）")
    if not isinstance(sc, dict):
        raise DeckError("台本の一番外はオブジェクト（title・slides …）で書く")
    extra = sorted(set(sc) - TOP_KEYS)
    if extra:
        raise DeckError(f"台本の一番外に知らないキー: {', '.join(extra)}（使えるのは {', '.join(sorted(TOP_KEYS))}）")
    if not isinstance(sc.get("slides"), list) or not sc["slides"]:
        raise DeckError("slides が無い（1枚以上の配列）")
    mode = sc.get("mode", "talk")
    if mode not in MODES:
        raise DeckError(f"mode は talk か handout（{mode!r}）")
    return sc


def talk_lines(no, spec, clicks) -> list[str]:
    """talk を照合する: 数 − 1 = 段の数。省いたら段の数ぶんの空。"""
    talk = spec.get("talk")
    if talk is None:
        return [""] * (clicks + 1)
    if isinstance(talk, str):
        talk = [talk]
    if not isinstance(talk, list) or not all(isinstance(x, str) for x in talk):
        raise DeckError(f"{no}枚目（{spec.get('type')}）: talk は文字の配列で書く")
    if len(talk) != clicks + 1:
        raise DeckError(f"{no}枚目（{spec.get('type')}）: talk が {len(talk)} 個。この型の動きはクリック {clicks} 回なので "
                        f"{clicks + 1} 個（画面に入ったとき1つ＋クリックごとに1つ）にする"
                        + ("" if clicks else "（動きを付けたくなければ steps: false で全部を自動にできる）"))
    return talk


def logo_geometry(w: int, h: int) -> dict:
    """ロゴの原本の寸法 → 置く位置と大きさ {"x", "y", "w", "h", "scale", "warning"}。

    scale = min(1, 枠の幅/w, 枠の高さ/h)。枠より小さい原本は原寸のまま（にじむので引き伸ばさない）。
    """
    if w <= 0 or h <= 0:
        raise DeckError(f"ロゴの画像の大きさが読めない（{w}×{h}）")
    scale = min(1.0, LOGO_BOX_W / w, LOGO_BOX_H / h)
    lw, lh = max(1, round(w * scale)), max(1, round(h * scale))
    x, y = 1920 - LOGO_RIGHT - lw, round(LOGO_MID_Y - lh / 2)
    warning = None
    if min(lw, lh) < LOGO_MIN_SIDE:
        why = ("原本が小さい（引き伸ばすとにじむので原寸のまま）" if scale == 1.0 and (w < LOGO_BOX_W and h < LOGO_BOX_H)
               else f"枠 {LOGO_BOX_W}×{LOGO_BOX_H} に縦横比を保って収めたため")
        warning = f"ロゴが {lw}×{lh}px と小さい（{why}）。横長のロゴか、大きい原本があればそちらを使う"
    return {"x": x, "y": y, "w": lw, "h": lh, "scale": scale, "warning": warning}


def load_logo(logo) -> tuple[pathlib.Path, dict] | tuple[None, None]:
    """ロゴのパスと置き方。透明な余白は切ってから枠に収める（geo["trim"] に切る範囲。切らなければ None）。"""
    if not logo:
        return None, None
    p = pathlib.Path(logo).expanduser().resolve()
    if not p.is_file():
        raise DeckError(f"ロゴの画像が無い: {p}")
    if p.suffix.lower() not in LOGO_EXT:
        raise DeckError(f"ロゴは PNG か JPEG（{p.name}）")
    from PIL import Image
    with Image.open(p) as im:
        im.load()
        box = im.convert("RGBA").getchannel("A").getbbox() if "A" in im.getbands() or im.mode == "P" else None
        geo = logo_geometry(*(box[2] - box[0], box[3] - box[1]) if box else im.size)
        geo["trim"] = list(box) if box and box != (0, 0, *im.size) else None
        return p, geo


def logo_box(geo: dict, corner: str) -> tuple:
    """角ごとのロゴの枠 (x, y, w, h)。左下の x は -1（logo_ground.mjs が、そのページの左半分の文字の左端にそろえる）。"""
    w, h = geo["w"], geo["h"]
    if corner == "bottom-left":
        return (-1, geo["y"], w, h)
    if corner == "top-right":
        return (geo["x"], round(LOGO_TOP_MID_Y - h / 2), w, h)
    return (geo["x"], geo["y"], w, h)


def logo_parts(box: tuple, corner: str, name: str, plate: str) -> list[str]:
    """ロゴと、その後ろの地（ロゴと同じ大きさ・その場所の地の色）。PPTX では別々の部品になる。"""
    x, y, w, h = box
    css = f'left: {x}px; top: {y}px; width: {w}px; height: {h}px;'
    k = LOGO_KEEP[corner]
    return [f'<div class="logo-plate" data-keep="logo-plate{k}" data-name="ロゴの地" style="background-color: {plate}; {css}"></div>',
            f'<img class="logo" data-keep="logo{k}" data-name="ロゴ" src="assets/{name}" alt="ロゴ" style="{css}" />']


LOGO_RING = 16          # 地の色を測る、ロゴの枠の周りの幅（px）
LOGO_GROUND_SHARE = 0.5 # 一番多い色がこれ未満なら、地がまだらと警告する


def fit_logo_plates(html_path, info) -> None:
    """組んだ HTML を Chrome で開き、ページごとにロゴの置き場と地を決めて、ロゴを書き入れる（--logo のときだけ）。

    - 置き場は --logo-position の順（LOGO_POSITIONS）に試す。写真・絵（<img>）か文字が枠にかかる所には置かない
      （右半分の写真の表紙は左下に・全面の写真は置かない）。下の文字とページ番号は測るときに隠し、ロゴに合わせて動かす
    - 左下なら下の文字をロゴの右へ寄せる。左下の x は、そのページの左半分の文字の左端（ページ番号は中央下なので動かさない）
    - 地は、その枠の周り（LOGO_RING px）で一番多い色（そのページのその場所の地の色）
    - 角ごとにロゴの名前を変える（!!logo・!!logo-left・!!logo-top）。同じ名前だと、モーフで別の角へ滑って動くため
    結果は info["logo"]["pages"] に {"no", "corner"（bottom-right・bottom-left・top-right）, "x", "plate"（色）} か {"no", "off": True} で残す。
    """
    import collections
    import subprocess
    import tempfile
    lg = info.get("logo")
    if not lg:
        return
    html_path = pathlib.Path(html_path)
    node = shutil.which("node")
    if not node:
        raise DeckError("ロゴの置き場を調べるのに node が要る")
    order = LOGO_POSITIONS[lg["position"]]
    boxes = [logo_box(lg, c) for c in order]
    with tempfile.TemporaryDirectory(prefix="html-slide-logo-") as tmp:
        r = subprocess.run([node, str(pathlib.Path(__file__).with_name("logo_ground.mjs")), str(html_path), tmp,
                            str(LOGO_RING), *(",".join(map(str, b)) for b in boxes)],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise DeckError(f"ロゴの置き場を調べられなかった: {r.stderr.strip()[:300]}")
        hits = json.loads(r.stdout.strip().splitlines()[-1])["pages"]
        from PIL import Image
        choice = []
        for i, page in enumerate(hits):
            for k, (corner, h) in enumerate(zip(order, page)):
                if h["img"] or h["text"]:
                    continue
                with Image.open(pathlib.Path(tmp) / f"ground-{i + 1:02d}-{k}.png") as im:
                    px = list(im.convert("RGB").getdata())
                color, n = collections.Counter(px).most_common(1)[0]
                choice.append((corner, (h["x"], *boxes[k][1:]), "#%02x%02x%02x" % color, n / len(px)))
                break
            else:
                choice.append(None)
    html = html_path.read_text(encoding="utf-8")
    head, *secs = html.split("<section data-slide")
    name = lg["name"]
    pages = []
    for i, sec in enumerate(secs):
        no = i + 1
        if choice[i] is None:
            pages.append({"no": no, "off": True})
            continue
        corner, box, color, share = choice[i]
        x, _, w, _ = box
        if corner == "bottom-left":                                           # 下の文字はロゴの右
            left = x + w + LOGO_GAP
            end = FOOTER_END_WITH_NO if 'class="pageno"' in sec else FOOTER_END
            sec = re.sub(r'(data-name="下の文字")(?: style="[^"]*")?>', rf'\1 style="left: {left}px; width: {end - left}px;">', sec, count=1)
        sec = sec.replace('      <aside class="notes">', "".join(f"      {ln}\n" for ln in logo_parts(box, corner, name, color))
                          + '      <aside class="notes">', 1)
        pages.append({"no": no, "corner": corner, "x": x, "plate": color})
        if share < LOGO_GROUND_SHARE:
            info["warnings"].append(f"{no}枚目: ロゴの周りの地がまだら（一番多い色 {color} が {share:.0%}）。地はその色で塗った")
        secs[i] = sec
    html_path.write_text("<section data-slide".join([head, *secs]), encoding="utf-8")
    lg["pages"] = pages


def build_html(script_path, out_dir, theme=None, mode=None, placeholder=False, style=None, logo=None, logo_position=None,
               watermark=None, image_look=None):
    """台本 → <out_dir>/deck.html と assets/。(html のパス, build 情報の dict) を返す。誤りは DeckError。

    build 情報: {"title", "mode", "theme", "html", "warnings": [文…], "images": [枠…],
                 "slides": [{"no", "type", "clicks", "transition"}]}
    warnings は placeholder で仮の枠を描いたこと。rc を変えない
    images は画像の枠の一覧（image_slots と同じ形）
    placeholder=True なら、無い画像を灰色の仮の枠にする（下書き用。仮の枠の文字は検品の H-3 に掛かる）。
    "generated" なら prompt だけの枠（Codex で描く枠）だけを仮にする（描く前の検査用）
    image_look は生成の枠の画風（photo・illustration・handdrawn）。省けばスタイルの image_look（ADR 0011）
    logo はロゴの画像のパス。logo_position（bottom-right 既定・bottom-left・top-right）の角から順に、空いている所に置く
    （fit_logo_plates）
    watermark は透かしの文字。全ページ（表紙・写真も）の中央下の端に置く。PPTX では一番上の、選べない部品になる（ADR 0009）
    """
    script_path = pathlib.Path(script_path).expanduser().resolve()
    out_dir = pathlib.Path(out_dir).expanduser().resolve()
    sc = load_script(script_path)
    mode = mode or sc.get("mode", "talk")
    if mode not in MODES:
        raise DeckError(f"mode は talk か handout（{mode!r}）")
    cfg = load_config()
    gen_dir = K.area_path(K.root("html-slide", cfg), script_path.parent)   # 生成の絵の置き場（ADR 0014）
    tpath = resolve_theme(theme or sc.get("theme"), script_path.parent, cfg)
    sty = DS.load(style)              # スタイル（standard・visual・カスタム。ADR 0005）。台本に書いた値が勝つ
    import imagegen as G
    image_look = G.look_of(image_look or sty.get("image_look"))
    th = DS.look_theme(load_theme(tpath), sty["look"])
    pal = palette(th)
    bad = contrast_problems(pal)
    if bad:
        raise DeckError(f"テーマ {th['name']} の色で読めない文字がある:\n  " + "\n  ".join(bad))
    files = font_files(th, cfg)
    T.use_fonts(files["regular"], files["bold"])

    footer = sc.get("footer") or ""
    if not isinstance(footer, str):
        raise DeckError("footer は文字で書く")
    numbers = sc.get("page_numbers", False)          # 既定はオフ。出すときは中央下（2026-10-06・ADR 0008）
    logo_position = logo_position or "bottom-right"
    if logo_position not in LOGO_POSITIONS:
        raise DeckError(f"ロゴの位置（--logo-position）は {' / '.join(LOGO_POSITIONS)} のどれか（{logo_position!r}）")
    watermark = (watermark or "").strip()
    if "\n" in watermark:
        raise DeckError("透かし（--watermark）は1行で書く")
    logo_path, logo_geo = load_logo(logo)
    assets: list = []
    logo_name = None
    if logo_path:
        logo_name = "logo" + logo_path.suffix.lower()
        assets.append((logo_path, logo_name) if not logo_geo["trim"] else (None, logo_name))
    slots: list = []
    img_warns: list = []
    used_overlays: dict = {}
    sections, info_slides = [], []
    chapter = 0
    for no, spec in enumerate(sc["slides"], 1):
        if not isinstance(spec, dict):
            raise DeckError(f"{no}枚目: スライドはオブジェクトで書く")
        spec, styled = DS.apply(sty, dict(spec), no, mode)
        if spec.get("type") == "section":
            chapter += 1
            spec["_auto_number"] = chapter
        steps_on = spec.get("steps", True)
        if not isinstance(steps_on, bool):
            raise DeckError(f"{no}枚目（{spec.get('type')}）: steps は true か false")
        s = T.Slide(no, spec, pal, flat=not steps_on, script_dir=script_path.parent, assets=assets,
                    placeholder=placeholder, slots=slots, warnings=img_warns, overlays=used_overlays,
                    still=(mode == "handout"), image_look=image_look,   # 配る資料は動きなし（読む・直す・PDF にする用）
                    gen_dir=gen_dir)
        parts, chrome = T.build_slide(s)
        if s.chars and not s.chars_used:
            img_warns.append(f"{no}枚目（{spec['type']}）: _characters を描き込む枠が無い（prompt だけの画像の枠に描く。"
                             "持ち込みの絵と枠の無いページには描かない）")
        if spec["type"] in sty.get("expects_images", ()) and not spec.get("image"):
            img_warns.append(f"{no}枚目（{spec['type']}）: スタイル {sty['name']} では画像を置く型だが、image が無い"
                             "（prompt を書けば Codex で描いて埋める）")
        clicks = s.clicks if steps_on else 0
        talk = talk_lines(no, spec, clicks)
        tr = spec.get("transition") or ("fade" if no == 1 or mode == "handout" else "morph")
        if tr not in TRANSITIONS:
            raise DeckError(f"{no}枚目（{spec.get('type')}）: transition は {' / '.join(TRANSITIONS)} のどれか")
        if mode == "handout":
            tr = "none"                                         # 切り替えの効果も付けない（1枚ずつめくって読む）
        if chrome and not getattr(s, "no_band", False):          # 上に写真の帯を置く型は、上の帯を出さない
            parts.insert(0, '<div class="band" data-keep="band" data-name="上の帯"></div>')
        if chrome:
            if footer:
                fs = f' style="width: {FOOTER_END_WITH_NO - FOOTER_LEFT}px;"' if numbers else ""   # 中央下の番号の手前まで
                parts.append(f'<div class="footer" data-keep="footer" data-name="下の文字"{fs}>{T.esc(footer)}</div>')
            if numbers:
                parts.append(f'<div class="pageno" data-keep="pageno" data-name="ページ番号">{no}</div>')
        if watermark:                                            # 全ページ・一番上（ロゴは後から足すが、重ならない）
            parts.append(f'<div class="watermark" data-keep="watermark" data-name="透かし">{T.esc(watermark)}</div>')
        notes = (CLICK.join(talk) if mode == "talk" else "\n".join(x for x in talk if x))
        body = "\n      ".join(parts)
        sections.append(f'    <section data-slide data-type="{spec["type"]}" data-transition="{tr}">\n      {body}\n'
                        f'      <aside class="notes">{T.esc_plain(notes)}</aside>\n    </section>')
        info_slides.append({"no": no, "type": spec["type"], "styled": styled,
                            "clicks": clicks if mode == "talk" else 0, "transition": tr})

    bad = overlay_problems(pal, used_overlays)
    if bad:
        raise DeckError(f"テーマ {th['name']} の色で、写真の上の文字が読めない:\n  " + "\n  ".join(bad))
    if any(x["origin"] == "generated" and x["exists"] for x in slots):
        K.area("html-slide", cfg, script_path.parent)              # 使った時刻を書く（上限で消す順は、古く使った区画から）
    out_dir.mkdir(parents=True, exist_ok=True)
    if assets:
        (out_dir / "assets").mkdir(exist_ok=True)
        for src, name in assets:
            dest = out_dir / "assets" / name
            dest.unlink(missing_ok=True)                            # 前の回の張り（ハードリンク）に書き込まない
            if src is None:                                         # 透明な余白を切ったロゴ（load_logo）
                from PIL import Image
                with Image.open(logo_path) as im:
                    im.crop(tuple(logo_geo["trim"])).save(dest)
                continue
            if gen_dir in pathlib.Path(src).parents:                # 描いた絵は区画から張る（同じ絵で場所を二重に取らない）
                try:
                    os.link(src, dest)
                    continue
                except OSError:
                    pass                                            # 別のボリュームなど。写す
            shutil.copyfile(src, dest)
    theme_css = " ".join(f"--{k}: {v};" for k, v in pal.items() if not k.startswith("_"))
    title = str(sc.get("title") or script_path.stem)
    html = (TEMPLATE.read_text(encoding="utf-8")
            .replace("{{TITLE}}", T.esc_plain(title))
            .replace("{{FONT_FACES}}", font_faces(th["font"], files))
            .replace("{{THEME}}", theme_css)
            .replace("{{FONT_FAMILY}}", f'"{th["font"]}", sans-serif')
            .replace("{{LOOK}}", DS.look_css(sty["look"]))
            .replace("{{SLIDES}}", "\n".join(sections)))
    html_path = out_dir / "deck.html"
    html_path.write_text(html, encoding="utf-8")
    info = {"title": title, "mode": mode, "theme": str(tpath), "html": str(html_path),
            "style": sty["name"], "style_description": sty["description"], "image_look": image_look,
            "warnings": img_warns + ([logo_geo["warning"]] if logo_geo and logo_geo["warning"] else []),
            "images": slots, "cache": str(gen_dir),
            "logo": ({"path": str(logo_path), "name": logo_name, "position": logo_position,
                      **{k: logo_geo[k] for k in ("x", "y", "w", "h", "scale")}} if logo_path else None),
            "slides": info_slides}
    if watermark:
        info["watermark"] = watermark
    if logo_path:
        fit_logo_plates(html_path, info)          # ページごとにロゴの角と地の色を決めて書き入れる・写真のページは置かない
    return html_path, info


def image_slots(script_path, theme=None, style=None, image_look=None) -> list[dict]:
    """台本の全画像の枠。画像が無くても止めない（仮の枠で組んで数える）。

    1つの枠: {"slide", "type", "slot"（枠の名前）, "width", "height"（枠の CSS px）, "aspect", "size"（画像生成の
    大きさ 1024x1024 / 1536x1024 / 1024x1536 のうち縦横比が一番近いもの）, "crop"（切り抜き方）, "prompt",
    "prompt_full"（「文字・ロゴ・透かしを描かない」「文字を重ねる側を空ける」を足したもの）, "focus", "alt",
    "clear"（文字を重ねる側。無ければ None）, "origin"（path・placed・generated）, "path"（在るファイルか置き場）,
    "default_path", "exists"}。生成の枠には "key"・"gen_path"・"gen_text"（Codex に渡す描く指示）・"shape"・"image_look"・
    "characters" も入る
    """
    import tempfile
    with tempfile.TemporaryDirectory(prefix="html-slide-slots-") as tmp:
        _, info = build_html(script_path, tmp, theme=theme, placeholder=True, style=style, image_look=image_look)
    return info["images"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="資料の台本（JSON）→ HTML")
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", required=True, help="HTML の出力先")
    ap.add_argument("--theme")
    ap.add_argument("--mode", choices=MODES)
    ap.add_argument("--json", action="store_true", help="build 情報を JSON で出す")
    ap.add_argument("--placeholder", action="store_true", help="無い画像を灰色の仮の枠にする（下書き用）")
    ap.add_argument("--slots", action="store_true", help="画像の枠の一覧を JSON で出して終わる")
    a = ap.parse_args(argv)
    try:
        if a.slots:
            print(json.dumps(image_slots(a.script, a.theme), ensure_ascii=False, indent=2))
            return 0
        path, info = build_html(a.script, a.out, a.theme, a.mode, placeholder=a.placeholder)
    except DeckError as e:
        print(f"deck: {e}", file=sys.stderr)
        return 2
    for w in info["warnings"]:
        print(f"警告: {w}", file=sys.stderr)
    if a.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        clicks = sum(x["clicks"] for x in info["slides"])
        print(f"{path}（{len(info['slides'])} 枚・クリック {clicks} 回・{info['mode']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
