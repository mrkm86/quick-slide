#!/usr/bin/env python3
"""ブランドの色から DADS のキーカラーを作る（標準ライブラリだけ・同じ入力なら同じ出力）。

デジタル庁デザインシステム（DADS）の「ブランドカラーがコントラスト要件を満たさない場合」の方法に沿う:
ブランドの色と**同じ色相**のまま**明度だけ**を動かして、地に対するコントラスト比を満たす色を作る。
https://design.digital.go.jp/dads/foundations/color/ （カラー（アクセシビリティ））

    python3 skills/dads/scripts/keycolors.py '#0db4f2'                      # 白の地に対するキーカラー（JSON）
    python3 skills/dads/scripts/keycolors.py '#0db4f2' --ground '#1a1a1a'   # 地を替える
    python3 skills/dads/scripts/keycolors.py --ramp cyan                    # DADS のプリミティブの段そのままでキーカラーを作る
    python3 skills/dads/scripts/keycolors.py --contrast '#333333' '#ffffff' # コントラスト比だけ

作る色（brand_key_colors）:
- primary    … 地と background の上で 4.5:1 以上になる、ブランドの明度にいちばん近いもの（ブランド自身が満たせばそのまま）。文字にも使える
- secondary  … 同色相の「primary より地に近い側」。地に 3:1 以上のうち最も地に近いもの（非テキスト用。文字に使うなら 4.5:1 を測り直す）
- tertiary   … 同色相の「primary より地から遠い側」。7:1 以上、かつ primary より OKLCH の L で 0.08 以上遠いもの
- background … 同色相のごく淡い（暗い地なら、ごく暗い）地の色。文字の色（text）がその上で 4.5:1 以上になる
- on_primary … primary の上に置く文字の色（白が 4.5:1 以上なら白。足りなければ白か黒の高いほう）

色空間: 明度の操作は OKLCH（知覚に近い）で行う。色相と彩度は保ち、彩度は sRGB に収まる範囲で下げる。
コントラスト比は WCAG の相対輝度（sRGB の 8bit の hex に丸めたあとの値）で測る。
"""
from __future__ import annotations

import argparse
import json
import math
import sys

# ---------------------------------------------------------------------------
# (1) WCAG の相対輝度とコントラスト比
# ---------------------------------------------------------------------------


def parse_hex(s: str) -> tuple[int, int, int]:
    """'#rgb' / '#rrggbb'（# は省ける）→ (r, g, b) 0〜255。"""
    h = s.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"色の書き方が違う: {s!r}")
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError as e:
        raise ValueError(f"色の書き方が違う: {s!r}") from e


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def norm_hex(s: str) -> str:
    return to_hex(parse_hex(s))


def _lin(c8: int) -> float:
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _delin(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def luminance(hex_color: str) -> float:
    """WCAG 2.x の相対輝度（0〜1）。"""
    r, g, b = parse_hex(hex_color)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str) -> float:
    """WCAG 2.x のコントラスト比（1〜21）。順序は問わない。"""
    la, lb = luminance(a), luminance(b)
    hi, lo = (la, lb) if la >= lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
# OKLab / OKLCH（明度だけを動かすための色空間）
# ---------------------------------------------------------------------------


def _srgb_to_oklab(hex_color: str) -> tuple[float, float, float]:
    r, g, b = (_lin(v) for v in parse_hex(hex_color))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(x) ** (1 / 3), x) for x in (l, m, s))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _oklab_to_linear(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_**3, m_**3, s_**3
    return (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def to_oklch(hex_color: str) -> tuple[float, float, float]:
    """hex → (L 0〜1, C, h 度 0〜360)。無彩色の h は 0。"""
    L, a, b = _srgb_to_oklab(hex_color)
    c = math.hypot(a, b)
    h = math.degrees(math.atan2(b, a)) % 360 if c > 1e-4 else 0.0
    return L, c, h


def _in_gamut(rgb: tuple[float, float, float]) -> bool:
    return all(-1e-6 <= v <= 1 + 1e-6 for v in rgb)


def from_oklch(L: float, c: float, h: float) -> str:
    """OKLCH → hex。L と h は保ち、sRGB に収まらなければ彩度 c だけを下げる。"""
    hr = math.radians(h)
    lo, hi = 0.0, max(c, 0.0)
    rgb = _oklab_to_linear(L, hi * math.cos(hr), hi * math.sin(hr))
    if not _in_gamut(rgb):
        for _ in range(28):
            mid = (lo + hi) / 2
            if _in_gamut(_oklab_to_linear(L, mid * math.cos(hr), mid * math.sin(hr))):
                lo = mid
            else:
                hi = mid
        rgb = _oklab_to_linear(L, lo * math.cos(hr), lo * math.sin(hr))
    return to_hex(tuple(round(min(1.0, max(0.0, _delin(max(v, 0.0)))) * 255) for v in rgb))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# (2) ブランドの色 → キーカラー
# ---------------------------------------------------------------------------

_STEP = 0.002  # L を動かす刻み。粗すぎず、数百回で端に着く


def _walk(h: float, c: float, ground: str, start_L: float, direction: int, ok) -> tuple[str, float] | None:
    """start_L から direction（+1=明るく・-1=暗く）へ L を動かし、ok(hex) を最初に満たす (hex, L) を返す。端まで行って満たさなければ None。"""
    L = start_L
    while -1e-9 <= L <= 1 + 1e-9:
        hx = from_oklch(min(max(L, 0.0), 1.0), c, h)
        if ok(hx):
            return hx, L
        L += direction * _STEP
    return None


def _reach(brand: str, grounds: list[str], min_ratio: float) -> tuple[str, float]:
    """ブランドと同じ色相・彩度のまま、すべての地に min_ratio 以上になる、ブランドの明度に最も近い色（hex, L）。"""
    L0, c, h = to_oklch(brand)
    b = norm_hex(brand)
    ok = lambda hx: all(contrast(hx, g) >= min_ratio for g in grounds)  # noqa: E731
    if ok(b):
        return b, L0
    cands = [r for r in (_walk(h, c, grounds[0], L0, -1, ok), _walk(h, c, grounds[0], L0, +1, ok)) if r]
    if not cands:
        raise ValueError(f"{brand} と同じ色相では、{', '.join(grounds)} に対して {min_ratio}:1 を満たせない")
    # 明度の動きが小さいほうを選ぶ。同じなら暗いほう（暗い側が先に並ぶので min の最初が暗い側）
    return min(cands, key=lambda r: abs(r[1] - L0))


def _on_color(bg: str) -> str:
    """bg の上に置く文字の色。白が 4.5:1 以上なら白（DADS の塗りボタンのラベルは White）、でなければ高いほう（黒が高ければ黒）。"""
    white, black = "#ffffff", "#000000"
    if contrast(white, bg) >= 4.5:
        return white
    return white if contrast(white, bg) >= contrast(black, bg) else black


def _lighter_side(ground: str) -> int:
    """地に近づく向き（+1=明るく・-1=暗く）。地が明るければ +1。"""
    return +1 if luminance(ground) >= 0.18 else -1


def brand_key_colors(brand: str, ground: str = "#ffffff", text: str | None = None, on_background: bool = True) -> dict:
    """ブランドの色 hex と地の色 hex から、キーカラー一式を作る（モジュールの説明を見る）。

    text は地の上の本文の色（省けば、明るい地は #333333・暗い地は #ffffff）。background の上で 4.5:1 を保つ確認に使う。
    on_background が真（既定）なら、primary は地だけでなく background の上でも 4.5:1 以上にする
    （キーカラーの文字を淡い地の箱に載せても読めるように。DADS のカラー「バックグラウンドカラー」の注意）。
    """
    ground = norm_hex(ground)
    L0, c, h = to_oklch(brand)
    toward = _lighter_side(ground)  # 地に近づく向き
    text = norm_hex(text) if text else ("#333333" if toward > 0 else "#ffffff")

    # background: 地側に L を寄せた、ごく淡い（暗い地ならごく暗い）同色相。彩度は小さく抑える
    bg_L = 0.97 if toward > 0 else 0.22
    bg = from_oklch(bg_L, min(c, 0.03), h)
    while contrast(text, bg) < 4.5 and 0.0 < bg_L < 1.0:  # 通常は通る。通らない地なら地の側へ寄せる
        bg_L += toward * _STEP
        bg = from_oklch(min(max(bg_L, 0.0), 1.0), min(c, 0.03), h)

    primary, Lp = _reach(brand, [ground, bg] if on_background else [ground], 4.5)


    # secondary: primary から地へ近づく向きに進み、3:1 を割る直前（地にいちばん近い 3:1 以上）で止める
    sec, Ls = primary, Lp
    L = Lp
    while True:
        L += toward * _STEP
        if not -1e-9 <= L <= 1 + 1e-9:
            break
        hx = from_oklch(min(max(L, 0.0), 1.0), c, h)
        if contrast(hx, ground) < 3.0:
            break
        sec, Ls = hx, L

    # tertiary: primary から地と反対へ進み、7:1 以上かつ L が 0.08 以上離れるところで止める。端に着いたらそこまで
    ter, Lt = primary, Lp
    L = Lp
    while True:
        L -= toward * _STEP
        if not -1e-9 <= L <= 1 + 1e-9:
            break
        hx = from_oklch(min(max(L, 0.0), 1.0), c, h)
        ter, Lt = hx, L
        if contrast(hx, ground) >= 7.0 and abs(L - Lp) >= 0.08:
            break

    white, black = "#ffffff", "#000000"
    on_primary = _on_color(primary)

    def row(hx: str, need: float) -> dict:
        r = contrast(hx, ground)
        return {"hex": hx, "contrast_on_ground": round(r, 2), "ok": r >= need}

    return {
        "brand": norm_hex(brand),
        "ground": ground,
        "text": text,
        "oklch": {"L": round(L0, 4), "C": round(c, 4), "h": round(h, 1)},
        "primary": row(primary, 4.5),
        "secondary": row(sec, 3.0),
        "tertiary": row(ter, 3.0),
        "background": {
            "hex": bg,
            "text_on_background": round(contrast(text, bg), 2),
            "primary_on_background": round(contrast(primary, bg), 2),
            "ok": contrast(text, bg) >= 4.5,
        },
        "on_primary": {
            "hex": on_primary,
            "contrast_on_primary": round(contrast(on_primary, primary), 2),
            "ok": contrast(on_primary, primary) >= 4.5,
        },
        "brand_changed": primary != norm_hex(brand),
        "nearest_primitive": nearest_primitive(primary),
    }


# ---------------------------------------------------------------------------
# (3) DADS のプリミティブの色相表（公式トークン 2.0.1・tokens/dads.css と同じ。tests で突き合わせる）
# ---------------------------------------------------------------------------

STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200)

PRIMITIVE: dict[str, tuple[str, ...]] = {
    "blue": ("#e8f1fe", "#d9e6ff", "#c5d7fb", "#9db7f9", "#7096f8", "#4979f5", "#3460fb", "#264af4", "#0031d8", "#0017c1", "#00118f", "#000071", "#000060"),
    "light-blue": ("#f0f9ff", "#dcf0ff", "#c0e4ff", "#97d3ff", "#57b8ff", "#39abff", "#008bf2", "#0877d7", "#0066be", "#0055ad", "#00428c", "#00316a", "#00234b"),
    "cyan": ("#e9f7f9", "#c8f8ff", "#99f2ff", "#79e2f2", "#2bc8e4", "#01b7d6", "#00a3bf", "#008da6", "#008299", "#006f83", "#006173", "#004c59", "#003741"),
    "green": ("#e6f5ec", "#c2e5d1", "#9bd4b5", "#71c598", "#51b883", "#2cac6e", "#259d63", "#1d8b56", "#197a4b", "#115a36", "#0c472a", "#08351f", "#032213"),
    "lime": ("#ebfad9", "#d0f5a2", "#c0f354", "#ade830", "#9ddd15", "#8cc80c", "#7eb40d", "#6fa104", "#618e00", "#507500", "#3e5a00", "#2c4100", "#1e2d00"),
    "yellow": ("#fbf5e0", "#fff0b3", "#ffe380", "#ffd43d", "#ffc700", "#ebb700", "#d2a400", "#b78f00", "#a58000", "#927200", "#806300", "#6e5600", "#604b00"),
    "orange": ("#ffeee2", "#ffdfca", "#ffc199", "#ffa66d", "#ff8d44", "#ff7628", "#fb5b01", "#e25100", "#c74700", "#ac3e00", "#8b3200", "#6d2700", "#541e00"),
    "red": ("#fdeeee", "#ffdada", "#ffbbbb", "#ff9696", "#ff7171", "#ff5454", "#fe3939", "#fa0000", "#ec0000", "#ce0000", "#a90000", "#850000", "#620000"),
    "magenta": ("#f3e5f4", "#ffd0ff", "#ffaeff", "#ff8eff", "#f661f6", "#f137f1", "#db00db", "#c000c0", "#aa00aa", "#8b008b", "#6c006c", "#500050", "#3b003b"),
    "purple": ("#f1eafa", "#ecddff", "#ddc2ff", "#cda6ff", "#bb87ff", "#a565f8", "#8843e1", "#6f23d0", "#5c10be", "#5109ad", "#41048e", "#30016c", "#21004b"),
}

# ニュートラル（solid-gray）。段の数字は DADS のトークン名のとおり（420・536 は 3:1・4.5:1 の境）
SOLID_GRAY: dict[int, str] = {
    50: "#f2f2f2", 100: "#e6e6e6", 200: "#cccccc", 300: "#b3b3b3", 400: "#999999", 420: "#949494",
    500: "#7f7f7f", 536: "#767676", 600: "#666666", 700: "#4d4d4d", 800: "#333333", 900: "#1a1a1a",
}

HUES = tuple(PRIMITIVE)


def ramp(family: str) -> list[dict]:
    """色相名（'cyan' など。'solid-gray' も可）→ 段の一覧 [{step, hex}]。"""
    if family == "solid-gray":
        return [{"step": s, "hex": v} for s, v in SOLID_GRAY.items()]
    if family not in PRIMITIVE:
        raise ValueError(f"知らない色相: {family!r}（{', '.join(HUES)}, solid-gray）")
    return [{"step": s, "hex": v} for s, v in zip(STEPS, PRIMITIVE[family])]


def _all_primitives() -> list[tuple[str, int, str]]:
    out = [(f, s, v) for f, vs in PRIMITIVE.items() for s, v in zip(STEPS, vs)]
    out += [("solid-gray", s, v) for s, v in SOLID_GRAY.items()]
    return out


def _delta(a: str, b: str) -> float:
    la, aa, ba = _srgb_to_oklab(a)
    lb, ab, bb = _srgb_to_oklab(b)
    return math.sqrt((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2)


def nearest_primitive(hex_color: str) -> dict:
    """入力色にいちばん近い DADS のプリミティブ（OKLab の距離）。{family, step, hex, delta, name}。

    名前はトークン名（--color-primitive-<family>-<step>、solid-gray は --color-neutral-solid-gray-<step>）。
    同じ距離なら、一覧の先（色相の並び順・段の小さいほう）。
    """
    best = min(_all_primitives(), key=lambda t: _delta(hex_color, t[2]))
    fam, step, hx = best
    name = f"--color-neutral-solid-gray-{step}" if fam == "solid-gray" else f"--color-primitive-{fam}-{step}"
    return {"family": fam, "step": step, "hex": hx, "delta": round(_delta(hex_color, hx), 4), "name": name}


def nearest_hue(hex_color: str) -> dict:
    """入力色の色相にいちばん近い色相の 13 段。{family, hue_gap, ramp}。無彩色（彩度 0.02 未満）は solid-gray。

    nearest_primitive が「色そのもの」に近い 1 色なのに対し、こちらは「色相」で選んでその色相の段を返す。
    色相の代表は、その色相の 500〜900 の段の OKLCH の h の平均（円周上）。
    """
    _, c, h = to_oklch(hex_color)
    if c < 0.02:
        return {"family": "solid-gray", "hue_gap": None, "ramp": ramp("solid-gray")}
    best, gap = None, 360.0
    for fam in HUES:
        hs = [to_oklch(v)[2] for v in PRIMITIVE[fam][5:10]]
        sx = sum(math.cos(math.radians(x)) for x in hs)
        sy = sum(math.sin(math.radians(x)) for x in hs)
        rep = math.degrees(math.atan2(sy, sx)) % 360
        d = abs((h - rep + 180) % 360 - 180)
        if d < gap:
            best, gap = fam, d
    return {"family": best, "hue_gap": round(gap, 1), "ramp": ramp(best)}


def ramp_key_colors(family: str, ground: str = "#ffffff", on_background: bool = True) -> dict:
    """DADS のプリミティブの段をそのまま使ってキーカラーを選ぶ（既定のテーマ用。色は作らない）。

    - background … その色相の 50（地が暗ければ 1200）
    - primary    … 地（と、on_background が真なら background）に 4.5:1 以上になる、いちばん地に近い段
    - secondary  … 地に 3:1 以上になる、いちばん地に近い段（primary と同じ段になりうる）
    - tertiary   … primary より地から遠い側で、地に 7:1 以上になる最初の段（無ければ最も遠い段）
    - on_primary … 白が 4.5:1 以上なら白、でなければ白か黒の高いほう
    """
    ground = norm_hex(ground)
    steps = ramp(family)
    light = luminance(ground) >= 0.18
    near_first = steps if light else list(reversed(steps))  # 地に近い順（明るい地なら薄い順）
    ratio = lambda s: contrast(s["hex"], ground)  # noqa: E731
    background = near_first[0]
    grounds = [ground, background["hex"]] if on_background else [ground]

    ok45 = [s for s in near_first if all(contrast(s["hex"], g) >= 4.5 for g in grounds)]
    if not ok45:
        raise ValueError(f"{family} には {', '.join(grounds)} に対して 4.5:1 を満たす段が無い")
    primary = ok45[0]
    pi = near_first.index(primary)
    secondary = next(s for s in near_first if ratio(s) >= 3.0)
    far = near_first[pi + 1 :]
    tertiary = next((s for s in far if ratio(s) >= 7.0), far[-1] if far else primary)
    on = _on_color(primary["hex"])

    def row(s: dict) -> dict:
        return {"step": s["step"], "hex": s["hex"], "contrast_on_ground": round(ratio(s), 2)}

    return {
        "family": family,
        "ground": ground,
        "primary": {**row(primary), "contrast_on_background": round(contrast(primary["hex"], background["hex"]), 2)},
        "secondary": row(secondary),
        "tertiary": row(tertiary),
        "background": {"step": background["step"], "hex": background["hex"]},
        "on_primary": {"hex": on, "contrast_on_primary": round(contrast(on, primary["hex"]), 2)},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="ブランドの色（hex）から DADS のキーカラーを作り、JSON で出す。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("hex", nargs="?", help="ブランドの色（#rrggbb）")
    ap.add_argument("--ground", default="#ffffff", help="地の色（既定 #ffffff）")
    ap.add_argument("--text", help="本文の色（既定: 明るい地は #333333・暗い地は #ffffff）")
    ap.add_argument("--no-bg-check", action="store_true", help="primary を地だけで測る（既定は background の上でも 4.5:1）")
    ap.add_argument("--ramp", metavar="FAMILY", help="プリミティブの段そのままでキーカラーを作る（例 cyan）")
    ap.add_argument("--contrast", nargs=2, metavar=("A", "B"), help="2 色のコントラスト比だけ出す")
    a = ap.parse_args(argv)
    try:
        if a.contrast:
            out: dict = {"a": norm_hex(a.contrast[0]), "b": norm_hex(a.contrast[1]), "ratio": round(contrast(*a.contrast), 2)}
        elif a.ramp:
            out = ramp_key_colors(a.ramp, a.ground, not a.no_bg_check)
        elif a.hex:
            out = brand_key_colors(a.hex, a.ground, a.text, not a.no_bg_check)
            out["hue_family"] = nearest_hue(a.hex)["family"]
        else:
            ap.print_usage(sys.stderr)
            return 2
    except ValueError as e:
        print(f"NG: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
