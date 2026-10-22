"""テーマの色 → 型が使う色の役（DADS のキーカラーの作り方・ADR 0020）。標準ライブラリと keycolors だけ。

deck.py の palette() の土台（写真の上の幕に載せる色は deck.py が足す）。ほかの道具（ブランドの編集画面の見本など）が
「描く資料と同じ色」を出すために、このファイルだけを読み込めるように分けてある。台本や型のことは知らない。
読めない色の組み合わせは ValueError（deck.py が DeckError にする）。
keycolors（skills/dads/scripts）は呼ぶ側が読み込めるようにしておく（deck.py は sys.path に足してから読む）。
"""
from __future__ import annotations

import keycolors as KC


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


# DADS の値（skills/dads/tokens/slide.json の color と同じ。tests が見比べる）
INK, MUTED, GRAY_SURFACE = "#333333", "#666666", "#f2f2f2"          # Gray-800・Gray-600・Gray-50
RULE, RULE_WEAK = "#767676", "#949494"                              # Gray-536（どの地でも 3:1）・Gray-420（白の地だけ 3:1）
SEMANTIC = {                                                         # 意味の色。-1 は枠・図形（3:1）、-2 は文字・アイコン・塗り（4.5:1）。白い面の上だけ
    "success-1": "#259d63", "success-2": "#197a4b", "error-1": "#ec0000", "error-2": "#ce0000",
    "warning-yellow-1": "#b78f00", "warning-yellow-2": "#927200", "warning-orange-1": "#fb5b01", "warning-orange-2": "#c74700",
}


def _darken(color: str, grounds: list[str], need: float) -> str:
    """color の色相・彩度を保ち（OKLCH）、どの地の上でも need 以上になるまで明度を下げた色（初めから満たせばそのまま）。"""
    L, c, h = KC.to_oklch(color)
    x = color
    while not all(ratio(x, g) >= need - 1e-9 for g in grounds):
        L -= 0.004
        if L <= 0:
            return "#000000"
        x = KC.from_oklch(L, c, h)
    return x


def _key(brand: str, bg: str, surface: str, what: str) -> dict:
    """主役の色 1 つから、DADS のキーカラーの作り方で色の役を作る（primary・accent で同じ）。

    ブランドの色が DADS のプリミティブの段そのままなら、その色相の段（keycolors.ramp_key_colors）。そうでなければ、同じ色相のまま
    明度だけを動かして作る（keycolors.brand_key_colors）。
      key    … 4.5:1（地・surface・淡い地の全部の上）。文字にも使う
      tint   … 同じ色相のごく淡い地
      mark   … 意味のある図形・線・棒（地・surface・淡い地の上で 3:1）。key より明るい
      deco   … 飾りの面だけ（セカンダリー。情報を持たせない）
      strong … 白い文字を載せる面（key か、足りなければターシャリー）
    """
    try:
        kc = KC.brand_key_colors(brand, bg)
    except ValueError as e:
        raise ValueError(f"テーマの {what}（{brand}）から読める色を作れない: {e}") from e
    fam = KC.nearest_primitive(kc["primary"]["hex"])
    if fam["delta"] == 0 and fam["family"] != "solid-gray":
        r = KC.ramp_key_colors(fam["family"], bg)
        key, sec, ter, tint = (r[k]["hex"] for k in ("primary", "secondary", "tertiary", "background"))
        steps = [x["hex"] for x in KC.ramp(fam["family"])]
        grounds = [bg, surface, tint]
        key = _darken(key, grounds, 4.5)
        mark = next((x for x in steps if all(ratio(x, g) >= 3.0 for g in grounds)), key)
    else:
        key, sec, ter, tint = (kc[k]["hex"] for k in ("primary", "secondary", "tertiary", "background"))
        grounds = [bg, surface, tint]
        key = _darken(key, grounds, 4.5)
        mark = _darken(sec, grounds, 3.0)
    white = "#ffffff"
    strong = key if ratio(white, key) >= 4.5 else ter
    return {"key": key, "tint": tint, "mark": mark, "deco": sec, "strong": strong}


def base_palette(t: dict) -> dict:
    """テーマの色（background・ink・primary。任意で accent・muted・surface。どれも小文字の #rrggbb）→ 型が使う色の役。

    役の意味は deck.py の palette() の説明のとおり。写真の上の幕に載せる色（…-on-dark・…-on-light・scrim・veil）は含まない。
    """
    bg, ink = t["background"], t["ink"]
    white = "#ffffff"
    surface = t.get("surface") or (GRAY_SURFACE if bg == white else mix(ink, 0.04, bg))
    pk = _key(t["primary"], bg, surface, "primary")
    ak = _key(t["accent"], bg, surface, "accent") if t.get("accent") and t["accent"] != t["primary"] else pk
    grounds = [bg, surface, pk["tint"], ak["tint"]]
    p = {"bg": bg, "ink": ink, "surface": surface}
    p["muted"] = t.get("muted") or _darken(MUTED, grounds, 4.5)
    p["faint"] = _darken(RULE, [bg], 4.5)             # 透かし。地の上だけ
    p["rule"] = _darken(RULE, grounds, 3.0)
    p["rule-weak"] = RULE_WEAK if ratio(RULE_WEAK, bg) >= 3.0 else p["rule"]
    p["bar"] = p["rule-weak"]
    p["tint"] = pk["tint"]
    p["primary-soft"] = pk["tint"]
    p["accent-soft"] = ak["tint"]
    for name, k in (("primary", pk), ("accent", ak)):
        p[name] = k["key"]
        p[f"{name}-text"] = k["key"]
        p[f"{name}-mark"] = k["mark"]
        p[f"{name}-decor"] = k["deco"]
        p[f"{name}-strong"] = k["strong"]
        p[f"on-{name}"] = white if ratio(white, k["strong"]) >= 4.5 else ink
    p.update(SEMANTIC)
    p.update(_tone_roles(p))
    p.update(_bar_roles(p))
    return p


TONES = ("good", "warn", "bad")
TONE_BASE = {"good": "success", "warn": "warning-yellow", "bad": "error"}
GROUPS = ("a", "b", "c")
GROUP_C_LIGHT, GROUP_C_DARK = KC.PRIMITIVE["blue"][6], KC.PRIMITIVE["blue"][9]    # c は a（緑）・b（橙）と色相の離れた青。テーマの色に依らない固定


def _tone_roles(p: dict) -> dict:
    """増減・セルの評価の色（tone-…）と、カードの種類分けの色（group-…）。どちらも不透明な色で、実際の地に対して作る。

      tone-{good,warn,bad}-bg   … 意味の色（-1）を地に 0.08 で混ぜた淡い地。tone-…-text … 同じ色の濃い側（-2）。tone の地・地の上で 4.5:1
      tone-neutral-bg / -text   … surface と、その上で 4.5:1 にした muted
      group-{a,b,c}-bg          … a＝success・b＝warning-orange・c＝blue（DADS のプリミティブ・テーマに依らず固定）を地に混ぜた淡い地（ink・muted・accent-text が 4.5:1 になる濃さまで薄める）
      group-…-text              … 札の文字と枠。group の地・地の上で 4.5:1。group-…-rule … カードの枠。group の地・地の上で 3:1
    """
    bg, surface = p["bg"], p["surface"]
    out = {}
    for tone in TONES:
        base = TONE_BASE[tone]
        tbg = mix(SEMANTIC[f"{base}-1"], 0.08, bg)
        out[f"tone-{tone}-bg"] = tbg
        out[f"tone-{tone}-text"] = _darken(SEMANTIC[f"{base}-2"], [tbg, bg], 4.5)
    out["tone-neutral-bg"] = surface
    out["tone-neutral-text"] = _darken(p["muted"], [surface, bg], 4.5)
    for g, (light, dark) in zip(GROUPS, (("success-1", "success-2"), ("warning-orange-1", "warning-orange-2"),
                                         (GROUP_C_LIGHT, GROUP_C_DARK))):
        light, dark = SEMANTIC.get(light, light), SEMANTIC.get(dark, dark)
        alpha = 0.08
        while True:
            gbg = mix(light, alpha, bg)
            if alpha <= 0.01 or all(ratio(p[fg], gbg) >= 4.5 for fg in ("ink", "muted", "accent-text")):
                break
            alpha = round(alpha - 0.01, 2)
        out[f"group-{g}-bg"] = gbg
        out[f"group-{g}-text"] = _darken(dark, [gbg, bg], 4.5)
        out[f"group-{g}-rule"] = _darken(light, [gbg, bg], 3.0)
    return out


BAR_TONES = ("good-strong", "good", "good-soft", "warn", "bad")     # 棒の色の段。neutral（と省略）は灰（bar）
BAR_TONE_BASE = {"good-strong": "success", "good": "success", "good-soft": "success", "warn": "warning-yellow", "bad": "error"}
BAR_GOOD_NEED = {"good-soft": 3.0, "good": 4.5, "good-strong": 7.0}                # 良い側の 3 段が地に対して満たす下限。幅は色相を保った明度の違いで作る


def _lighten(color: str, grounds: list[str], need: float) -> str:
    """color の色相・彩度を保ち、どの地の上でも need 以上を保てる一番明るい色（初めから満たさなければ _darken で満たす）。"""
    x = _darken(color, grounds, need)
    L, c, h = KC.to_oklch(x)
    while L < 1:
        y = KC.from_oklch(L + 0.004, c, h)
        if not all(ratio(y, g) >= need - 1e-9 for g in grounds):
            break
        L, x = L + 0.004, y
    return x


def _bar_roles(p: dict) -> dict:
    """棒の色の段（bar-…）と、その値の文字色（bar-…-text）。棒は地（bg・surface）の上で 3:1 以上、値の文字は 4.5:1。

      bar-good-soft   … success-1 を、地の上で 3:1 を割らない所まで淡くした色。bar-good … 同じ色相で 4.5:1。bar-good-strong … 同じ色相で 7:1
      bar-warn・bar-bad … warning-yellow-1・error-1 を地の上で 3:1 に（足りなければ濃く）
      bar-…-text      … 意味の色の濃い側（-2）を地の上で 4.5:1 に。良い側の 3 段は同じ文字色
    """
    grounds = [p["bg"], p["surface"]]
    out = {}
    soft = _lighten(SEMANTIC["success-1"], grounds, 3.0)
    for tone in BAR_TONES:
        base = BAR_TONE_BASE[tone]
        if tone in BAR_GOOD_NEED:
            out[f"bar-{tone}"] = soft if tone == "good-soft" else _darken(soft, grounds, BAR_GOOD_NEED[tone])
        else:
            out[f"bar-{tone}"] = _darken(SEMANTIC[f"{base}-1"], grounds, 3.0)
        out[f"bar-{tone}-text"] = _darken(SEMANTIC[f"{base}-2"], grounds, 4.5)
    return out


# 検査する組み合わせ。文字は大きさによらず 4.5（DADS は大きな文字の緩和を使わない）。図形・罫・枠は 3
TEXT_GROUNDS = ("bg", "surface", "tint", "accent-soft")


def contrast_problems(p: dict) -> list[str]:
    """実際に描く「文字 × 地」「図形・罫 × 地」の組み合わせで、足りないものを返す（空なら合格）。"""
    need = []
    for fg in ("ink", "muted", "primary-text", "accent-text"):
        for g in TEXT_GROUNDS:
            need.append((fg, g, 4.5))
    need += [("faint", "bg", 4.5),
             ("on-primary", "primary-strong", 4.5), ("on-accent", "accent-strong", 4.5)]
    for g in TEXT_GROUNDS:                      # 罫・枠・軸
        need.append(("rule", g, 3.0))
    need += [("rule-weak", "bg", 3.0), ("bar", "bg", 3.0)]
    for g in ("bg", "surface", "tint"):         # 意味のある図形・線・棒
        need.append(("primary-mark", g, 3.0))
    for g in ("bg", "surface", "accent-soft"):
        need.append(("accent-mark", g, 3.0))
    need += [("primary-strong", "bg", 3.0), ("accent-strong", "bg", 3.0)]    # 面の境
    for tone in TONES:                          # 増減・セルの評価: 文字 × その地。同じ色は凡例の枠にも使うので、地の上で 3
        need += [(f"tone-{tone}-text", f"tone-{tone}-bg", 4.5), (f"tone-{tone}-text", "bg", 3.0)]
    need += [("tone-neutral-text", "tone-neutral-bg", 4.5), ("tone-neutral-text", "bg", 3.0)]
    for tone in (*TONES, "neutral"):            # 増減の札の輪郭: tone の色の枠が、札の置かれる面（通常・強調の淡い面・group）に対して 3。強い look の面（accent-strong）の上では枠が札の地と同じ色になるので、札の地が 3
        for g in ("bg", "surface", "accent-soft", *(f"group-{x}-bg" for x in GROUPS)):
            need.append((f"tone-{tone}-text", g, 3.0))
        need.append((f"tone-{tone}-bg", "accent-strong", 3.0))
    for g in GROUPS:                            # カードの種類分け: 地の上の本文・札の文字・枠
        for fg in ("ink", "muted", "accent-text", f"group-{g}-text"):
            need.append((fg, f"group-{g}-bg", 4.5))
        need += [(f"group-{g}-text", "bg", 4.5), (f"group-{g}-rule", f"group-{g}-bg", 3.0), (f"group-{g}-rule", "bg", 3.0)]
    for tone in BAR_TONES:                      # 棒の塗り × 棒の置かれる地は 3、値の文字 × 同じ地は 4.5
        for g in ("bg", "surface"):
            need += [(f"bar-{tone}", g, 3.0), (f"bar-{tone}-text", g, 4.5)]
    bad = []
    for fg, g, n in need:
        r = ratio(p[fg], p[g])
        if r < n - 1e-9:
            bad.append(f"{fg}（{p[fg]}）× {g}（{p[g]}）のコントラスト {r:.2f} が {n} 未満")
    return bad
